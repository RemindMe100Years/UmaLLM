import json
import os
import re
import queue
import sys
import signal

try:
    import botocore
except ImportError:
    import io, warnings
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import litellm
    sys.stderr = old_stderr
else:
    import litellm
import plugins
from concurrent.futures import ThreadPoolExecutor
import threading
from flask import Flask, request
from flask_cors import CORS, cross_origin
from waitress import serve
import time


def _force_shutdown(signum=None, frame=None):
    print("\n[SHUTDOWN] Signal received, forcing exit...")
    os._exit(0)


signal.signal(signal.SIGINT, _force_shutdown)
signal.signal(signal.SIGTERM, _force_shutdown)
try:
    signal.signal(signal.SIGHUP, _force_shutdown)
except AttributeError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
CHARACTER_FILE = os.path.join(BASE_DIR, "data", "character_memory.json")

with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
    settings = json.load(file)

port = settings["HTTP_port_number"]
host = "0.0.0.0"


class Main_Translator:
    def __init__(self):
        self.translator_ready_or_not = False
        self.can_change_language_or_not = True
        self.input_language = settings["input_language"]
        self.output_language = settings["output_language"]
        self.model_name = settings["model_name"]
        self.api_key = settings["api_key"]
        self.api_server = settings["api_server"]
        self.context_lines = settings["context_lines"]
        self.temperature = settings["temperature"]
        self.top_p = settings["top_p"]
        self.top_k = settings.get("top_k", 40)
        self.repetition_penalty = settings.get("repetition_penalty", 1.1)
        self.max_tokens = settings.get("max_tokens", 2048)
        self.min_p = settings.get("min_p", 0.05)

        self.parallel_workers = settings.get("parallel_workers", 1)
        self.chunk_size = settings.get("chunk_size", 10)
        self.max_retries = settings.get("max_retries", 3)
        self._lock = threading.Lock()

        self.messages = []
        self.stop_translation = False

        substitutions = {
            "input_language": self.input_language,
            "output_language": self.output_language,
        }

        self.base_instruction = settings["system_prompt"].format(**substitutions)

        with open(CHARACTER_FILE, "r", encoding="utf-8") as f:
            self.character_memory = json.load(f).get("characters", {})

    def _build_char_instructions(self, input_text):
        instructions = set()
        search_text = (
            " ".join(input_text) if isinstance(input_text, list) else input_text
        )

        for original_jp, raw_data in self.character_memory.items():
            char_data = self._substitute(raw_data)
            found_this_char = False

            if original_jp in search_text:
                instructions.add(f"- {original_jp} -> {char_data['name']}")
                found_this_char = True

            nicknames = char_data.get("nickname", [])
            if isinstance(nicknames, str):
                nicknames = [nicknames]

            for nick in nicknames:
                match = re.search(r"(.*?) \((.*?)\)", nick)
                if match:
                    eng_nick, jp_nick = match.group(1).strip(), match.group(2).strip()
                    if jp_nick in search_text:
                        instructions.add(f"- {jp_nick} -> {eng_nick}")
                        found_this_char = True
                elif nick in search_text:
                    instructions.add(f"- {nick} -> {nick}")
                    found_this_char = True

            if found_this_char:
                instructions.add(
                    f"Context: {char_data['name']} is {char_data.get('gender', 'unknown')}."
                )
                notes = char_data.get("notes")
                if notes and notes.strip():
                    instructions.add(notes)

        return instructions

    def _substitute(self, value):
        subs = {
            "input_language": self.input_language,
            "output_language": self.output_language,
        }
        if isinstance(value, str):
            return value.format(**subs)
        if isinstance(value, list):
            return [self._substitute(v) for v in value]
        if isinstance(value, dict):
            return {k: self._substitute(v) for k, v in value.items()}
        return value

    def apply_character_memory(self, input_text):
        instructions = self._build_char_instructions(input_text)
        if instructions:
            return "\n[CHARACTER GLOSSARY]:\n" + "\n".join(instructions)
        return ""

    def _looks_like_fragment(self, a, b):
        a_end = a.rstrip()
        b_start = b.lstrip()
        sentence_endings = set(".!?:;\"'")
        a_ends_sentence = bool(a_end and a_end[-1] in sentence_endings)
        if a_ends_sentence:
            return False
        lower_chars = set("abcdefghijklmnopqrstuvwxyz")
        b_starts_lowercase = bool(b_start and b_start[0] in lower_chars)
        both_short = len(a_end) < 80 and len(b_start) < 80
        if b_starts_lowercase and both_short:
            return True
        return False

    def _realign_translations(self, input_lines, translations):
        n = len(input_lines)
        m = len(translations)
        if n == m:
            return translations[:]
        if m == 0:
            return ["Error"] * n

        if m < n:
            missing = n - m
            print(f"[REALIGN] Dropped {missing} translation(s), filling with last available")
            out = list(translations) + [translations[-1]] * missing
            return out[:n]

        surplus = m - n
        print(f"[REALIGN] Got {m} translations for {n} inputs, merging {surplus} surplus")

        out = []
        i = 0
        while i < m and len(out) < n:
            remaining_trans = m - i
            remaining_out = n - len(out)

            if remaining_out == 1:
                merged = "\n".join(t.strip() for t in translations[i:])
                out.append(merged.strip())
                break

            if remaining_trans == remaining_out:
                out.extend(translations[i:i + remaining_out])
                break

            need_merge = (remaining_trans - 1) >= remaining_out and self._looks_like_fragment(
                translations[i], translations[i + 1] if i + 1 < m else ""
            )
            force_merge = (remaining_trans - 1) < remaining_out

            if need_merge or force_merge:
                a = translations[i].strip()
                b = translations[i + 1].strip() if i + 1 < m else ""
                if self._looks_like_fragment(a, b):
                    merged = (a + " " + b).strip()
                else:
                    merged = (a + "\n" + b).strip()
                out.append(merged)
                i += 2
            else:
                out.append(translations[i])
                i += 1

        while len(out) < n:
            out.append("Error")
        excess = len(out) - n
        if excess > 0:
            print(f"[REALIGN] Dropping {excess} excess translation(s)")
        return out[:n]

    def _build_batches(self, list_of_text):
        n = len(list_of_text)
        if n == 0:
            return []

        batches = []
        cs = max(1, self.chunk_size)
        overlap = max(0, self.context_lines)

        batch_start = 0
        while batch_start < n:
            batch_end = min(batch_start + cs, n)

            ctx_before_start = max(0, batch_start - overlap)
            ctx_after_end = min(n, batch_end + overlap)

            batches.append({
                'start': batch_start,
                'end': batch_end,
                'context_before': list_of_text[ctx_before_start:batch_start],
                'context_after': list_of_text[batch_end:ctx_after_end],
                'translate_lines': list_of_text[batch_start:batch_end],
            })

            batch_start = batch_end

        return batches

    def execute(self, messages):
        api_params = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repetition_penalty": self.repetition_penalty,
            "max_tokens": self.max_tokens,
        }

        if any(
            name in self.model_name for name in ["ollama", "lm_studio", "oobabooga"]
        ):
            return (
                litellm.completion(
                    **api_params, api_key=self.api_key, api_base=self.api_server
                )
                .choices[0]
                .message.content
            )
        return (
            litellm.completion(**api_params, api_key=self.api_key)
            .choices[0]
            .message.content
        )

    def _flatten(self, s):
        return " ".join(str(s).strip().split())

    def _is_numbered_output(self, text):
        """Detect if the LLM output numbered text instead of JSON."""
        t = text.strip().strip('"[]')
        return bool(re.match(r"^\s*\d+[.)\s]", t))

    def _extract_json_array(self, raw_response, context_label="", expected_count=None):
        text = raw_response.strip()

        for candidate in [re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE).strip(), text]:
            cleaned = re.sub(r"\s*```\s*$", "", candidate, flags=re.MULTILINE)
            try:
                obj = json.loads(cleaned)
                if isinstance(obj, list):
                    if self._is_numbered_output(cleaned):
                        return None
                    if expected_count is None or len(obj) == expected_count:
                        return [self._flatten(t) for t in obj]
            except Exception:
                pass

        bracket_start = raw_response.index("[") if "[" in raw_response else -1
        if bracket_start >= 0:
            all_arrays = []
            depth = 0
            start = -1
            for i, ch in enumerate(raw_response[bracket_start:], start=bracket_start):
                if ch == "[":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidate = raw_response[start:i + 1]
                        cleaned = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.MULTILINE)
                        cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
                        try:
                            obj = json.loads(cleaned)
                            if isinstance(obj, list):
                                if self._is_numbered_output(cleaned):
                                    return None
                                all_arrays.append([self._flatten(t) for t in obj])
                        except Exception:
                            continue
                        start = -1

            if expected_count is not None and len(all_arrays) > 1:
                for arr in all_arrays:
                    if len(arr) == expected_count:
                        return arr

            if all_arrays:
                return all_arrays[0]

        if raw_response.lstrip().startswith("["):
            try:
                obj = json.loads(raw_response + "]")
                if isinstance(obj, list):
                    return [self._flatten(t) for t in obj]
            except Exception:
                pass

        return []

    def _is_trivial(self, raw_in, raw_out):
        stripped = raw_out.strip()
        inp_chars = len(raw_in.replace(" ", ""))
        out_stripped = re.sub(r"[\.\-\!\?\,\:\;\x27\x60\~\u2014\u2013\(\)\[\]{}]", "", stripped)
        if inp_chars > 20 and (len(out_stripped) == 0 or len(stripped) < 5):
            return True
        # Check for untranslated CJK characters (Japanese/Chinese/Korean)
        cjk_pattern = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')
        if cjk_pattern.search(stripped):
            return True
        return False

    def translate(self, input_text):
        if self.stop_translation:
            return "Paused"

        if isinstance(input_text, list) and self.parallel_workers > 1:
            return self._process_parallel_chunked(input_text)

        if isinstance(input_text, list):
            return self._process_batch_llm(input_text)

        return self._process_single_line(input_text)

    def _process_single_line(self, input_text):
        input_text = plugins.process_input_text(input_text)
        char_map = self.apply_character_memory(input_text)

        current_turn_prompt = (
            f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
            f"{char_map}\n\n"
            f"### TEXT TO TRANSLATE ###\n{input_text}"
        )

        history = (
            self.messages[-(self.context_lines * 2) :] if self.context_lines > 0 else []
        )

        final_payload = history + [{"role": "user", "content": current_turn_prompt}]

        result = self.execute(messages=final_payload)

        self.messages.append({"role": "user", "content": input_text})
        self.messages.append({"role": "assistant", "content": result})

        return plugins.process_output_text(result)

    def _translate_chunk_with_context(self, batch):
        start_idx = batch['start']
        translate_lines = batch['translate_lines']
        ctx_before = batch['context_before']
        ctx_after = batch['context_after']

        with self._lock:
            history = (
                self.messages[-(self.context_lines * 2) :]
                if self.context_lines > 0
                else []
            )

        processed_translate = [plugins.process_input_text(t) for t in translate_lines]
        processed_ctx_before = [plugins.process_input_text(t) for t in ctx_before]
        processed_ctx_after = [plugins.process_input_text(t) for t in ctx_after]

        char_instructions = self._build_char_instructions(processed_translate)
        char_map = "\n[CHARACTER GLOSSARY]:\n" + "\n".join(char_instructions) if char_instructions else ""

        context_instructions = ""
        if processed_ctx_before:
            lines_str = "\n".join(
                f"Line {start_idx - len(processed_ctx_before) + i}: {t}"
                for i, t in enumerate(processed_ctx_before)
            )
            context_instructions += (
                "\n--- PREVIOUS DIALOGUE (for context only) ---\n"
                + lines_str + "--- END PREVIOUS ---\n"
            )
        if processed_ctx_after:
            ctx_start = start_idx + len(processed_translate)
            lines_str = "\n".join(
                f"Line {ctx_start + i}: {t}"
                for i, t in enumerate(processed_ctx_after)
            )
            context_instructions += (
                "\n--- FOLLOWING DIALOGUE (for context only) ---\n"
                + lines_str + "--- END FOLLOWING ---\n"
            )

        translate_lines_text = "\n".join(
            f"{i+1}. {t}" for i, t in enumerate(processed_translate)
        )

        expected_count = len(processed_translate)

        current_turn_prompt = (
            f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
            f"{char_map}{context_instructions}\n\n"
            f"Translate ONLY the following {expected_count} numbered lines. "
            f"The previous/following dialogue sections above are reference only.\n\n"
            f"### LINES TO TRANSLATE ###\n{translate_lines_text}"
            + f"\n\nOutput ONLY a valid JSON array of exactly {expected_count} strings.\n"
            f"DO NOT output numbered text — output ONLY the JSON array.\n"
            f'Format example: ["translation one", "translation two"]'
        )

        translations = []
        result = ""
        numbered_rejected = False
        for attempt in range(self.max_retries):
            final_payload = history + [{"role": "user", "content": current_turn_prompt}]
            result = self.execute(messages=final_payload)
            translations = self._extract_json_array(result, "", expected_count)

            if translations is None:
                numbered_rejected = True
                translations = []

            if len(translations) == expected_count:
                bad_indices = [i for i in range(expected_count)
                               if self._is_trivial(processed_translate[i], translations[i])]
                if not bad_indices:
                    break

            if attempt >= self.max_retries - 1:
                break

            numbered_warning = ""
            if numbered_rejected:
                numbered_warning = (
                    "\n\nCRITICAL: You previously output numbered text (e.g. \"1. translation\\n2. translation\") "
                    "wrapped in JSON brackets. THIS IS INVALID. Each translation must be a separate string in the array.\n"
                )
            if len(translations) != expected_count:
                current_turn_prompt = (
                    f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
                    f"{char_map}{context_instructions}\n\n"
                    f"Translate ONLY the following {expected_count} numbered lines.\n\n"
                    f"### LINES TO TRANSLATE ###\n{translate_lines_text}"
                    + f"\n\nYou previously produced an output with the wrong number of elements. "
                    f"The JSON array MUST contain exactly {expected_count} strings — no more, no fewer."
                    + numbered_warning
                    + f'\n\nCORRECT FORMAT EXAMPLE: ["translation one", "translation two"]'
                )
            else:
                bad_nums = ", ".join(str(x + 1) for x in bad_indices)
                current_turn_prompt = (
                    f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
                    f"{char_map}{context_instructions}\n\n"
                    f"Translate ONLY the following {expected_count} numbered lines.\n\n"
                    f"### LINES TO TRANSLATE ###\n{translate_lines_text}"
                    + f"\n\nYou previously produced incomplete translations for line(s): {bad_nums}. "
                    f"Every line must have a complete translation — do NOT output just \"...\", em-dashes, or single punctuation marks.\n"
                    f"The JSON array MUST contain exactly {expected_count} strings."
                    + numbered_warning
                )

        if not translations or len(translations) != expected_count:
            valid_trans = [t if t.strip() else "" for t in (translations or [])[:expected_count + 1]]
            translations = self._realign_translations(processed_translate, valid_trans)

        cleaned = [plugins.process_output_text(t) for t in translations[:expected_count]]
        print(f"[CHUNK {start_idx}] Extracted {len(translations)}, returned {len(cleaned)}")
        for i, (inp, raw, out) in enumerate(zip(processed_translate, translations, cleaned)):
            if inp != raw[:len(inp)] or "\n" in raw:
                print(f"  [{i+1}] IN: {inp[:60]}... | RAW: {raw[:80]}... | OUT: {out[:60]}...")
        return start_idx, cleaned, processed_translate, result

    def _process_batch_llm(self, list_of_text):
        processed_input = [plugins.process_input_text(t) for t in list_of_text]
        char_map = self.apply_character_memory(processed_input)

        expected_count = len(processed_input)
        lines_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(processed_input))
        batch_prompt = (
            f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
            f"{char_map}\n\n"
            f"Output ONLY a valid JSON array of exactly {expected_count} strings.\n"
            f"The output array MUST have exactly {expected_count} elements — no more, no fewer.\n"
            f"Do NOT split a single numbered line into multiple output strings.\n"
            f"DO NOT output numbered text (e.g. \"1. translation\") — output ONLY the JSON array.\n"
            f"Format example: [\"translation 1\", \"translation 2\"]\n\n"
            f"### LINES TO TRANSLATE ###\n{lines_text}"
        )

        history = (
            self.messages[-(self.context_lines * 2) :] if self.context_lines > 0 else []
        )

        translations = []
        result = ""
        numbered_rejected = False
        for attempt in range(self.max_retries):
            final_payload = history + [{"role": "user", "content": batch_prompt}]
            result = self.execute(messages=final_payload)
            translations = self._extract_json_array(result, "", expected_count)

            if translations is None:
                numbered_rejected = True
                translations = []

            if len(translations) == expected_count:
                bad_indices = [i for i in range(min(len(translations), len(processed_input)))
                               if self._is_trivial(processed_input[i], translations[i])]
                if not bad_indices:
                    break

            if attempt >= self.max_retries - 1:
                break

            numbered_warning = ""
            if numbered_rejected:
                numbered_warning = (
                    "\n\nCRITICAL: You previously output numbered text (e.g. \"1. translation\\n2. translation\") "
                    "wrapped in JSON brackets. THIS IS INVALID. Each translation must be a separate string in the array.\n"
                )
            if len(translations) != expected_count:
                batch_prompt = (
                    f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
                    f"{char_map}\n\n"
                    f"You previously produced the wrong number of elements. "
                    f"The JSON array MUST contain exactly {expected_count} strings.\n\n"
                    f"### LINES TO TRANSLATE ###\n"
                    + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(processed_input))
                    + numbered_warning
                    + f'\n\nCORRECT FORMAT EXAMPLE: ["translation one", "translation two"]'
                )
            else:
                bad_nums = ", ".join(str(x + 1) for x in bad_indices)
                batch_prompt = (
                    f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
                    f"{char_map}\n\n"
                    f"You previously produced incomplete translations for line(s): {bad_nums}. "
                    f"Every line must have a complete translation — do NOT output just \"...\", em-dashes, or single punctuation marks.\n"
                    f"The JSON array MUST contain exactly {expected_count} strings.\n\n"
                    f"### LINES TO TRANSLATE ###\n"
                    + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(processed_input))
                    + numbered_warning
                )

        if not translations or len(translations) != expected_count:
            valid_trans = [t if t.strip() else "" for t in (translations or [])[:expected_count + 1]]
            translations = self._realign_translations(processed_input, valid_trans)

        excess = len(translations) - len(processed_input)
        if excess > 0:
            print(f"[BATCH] Dropping {excess} excess translation(s)")
        cleaned = [plugins.process_output_text(t) for t in translations[:len(processed_input)]]
        print(f"[BATCH] Extracted {len(translations)}, returned {len(cleaned)}")
        for i, (inp, raw, out) in enumerate(zip(processed_input, translations, cleaned)):
            if "\n" in raw or len(raw) > len(inp) * 2:
                print(f"  [{i+1}] IN: {inp[:60]}... | RAW: {raw[:80]}... | OUT: {out[:60]}...")
        return cleaned

    def _worker_loop(
        self, work_q, completed_q, done_event, remaining_batches, batch_retry_counts, completed_batches
    ):
        while True:
            try:
                task = work_q.get(timeout=0.2)
            except queue.Empty:
                with self._lock:
                    if done_event.is_set() and not remaining_batches:
                        return
                continue

            batch = task['batch']
            start_idx = batch['start']
            my_round = task.get('retry_round', 0)

            with self._lock:
                if start_idx in completed_batches:
                    continue

            try:
                result = self._translate_chunk_with_context(batch)
                start, translations, processed_input, raw_output = result

                expected_count = len(processed_input)
                is_good = (
                    len(translations) == expected_count
                    and not any(
                        self._is_trivial(processed_input[i], translations[i])
                        for i in range(expected_count)
                    )
                )

                if is_good:
                    with self._lock:
                        if start not in completed_batches:
                            completed_q.put((start, translations, processed_input))
                            completed_batches.add(start)
                            remaining_batches.discard(start)
                    continue
            except Exception:
                pass

            action = None
            with self._lock:
                if start_idx in completed_batches:
                    continue
                elif my_round < batch_retry_counts.get(start_idx, 0):
                    continue

                current = batch_retry_counts.get(start_idx, 0)
                if current >= self.max_retries:
                    action = 'error'
                else:
                    batch_retry_counts[start_idx] = current + 1
                    action = ('broadcast', current + 1)

            if action == 'error':
                end_idx = batch['end']
                errors = ["Error"] * (end_idx - start_idx)
                processed_input = [plugins.process_input_text(t) for t in batch['translate_lines']]
                with self._lock:
                    completed_batches.add(start_idx)
                    remaining_batches.discard(start_idx)
                completed_q.put((start_idx, errors, processed_input))
            elif action == ('broadcast', new_round):
                for _ in range(self.parallel_workers):
                    work_q.put({'batch': batch, 'retry_round': new_round})

    def _process_parallel_chunked(self, list_of_text):
        if not list_of_text:
            return []

        batches = self._build_batches(list_of_text)

        n = len(list_of_text)
        results = [None] * n
        translated_pairs = []

        work_q = queue.Queue()
        completed_q = queue.Queue()
        done_event = threading.Event()

        remaining_batches = {b['start'] for b in batches}
        batch_retry_counts = {}
        completed_batches = set()

        for batch in batches:
            work_q.put({'batch': batch, 'retry_round': 0})

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = [
                executor.submit(
                    self._worker_loop,
                    work_q, completed_q, done_event,
                    remaining_batches, batch_retry_counts, completed_batches,
                )
                for _ in range(self.parallel_workers)
            ]

            done_event.set()

            for future in futures:
                future.result()

        while not completed_q.empty():
            start_idx, translations, processed_input = completed_q.get()
            for i, translation in enumerate(translations):
                if start_idx + i < n:
                    results[start_idx + i] = translation
            for inp, out in zip(processed_input, translations):
                translated_pairs.append((inp, out))

        gaps = sum(1 for r in results if r is None)
        if gaps:
            print(f"[CHUNK] {gaps} line(s) had no result, filling with Error")
        for i in range(n):
            if results[i] is None:
                results[i] = "Error"

        with self._lock:
            for input_text, output_text in translated_pairs:
                self.messages.append({"role": "user", "content": input_text})
                self.messages.append({"role": "assistant", "content": output_text})

        return results

    def pause(self):
        self.stop_translation = True

    def resume(self):
        self.stop_translation = False

    def activate(self):
        self.translator_ready_or_not = True
        return True


translator = Main_Translator()
translator.activate()

app = Flask(__name__)

cors = CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'

@app.route("/", methods=['POST', 'GET'])
@cross_origin()
def sendSugoi():
    tic = time.perf_counter()
    data = request.get_json(True)
    message = data.get("message")
    content = data.get("content")

    if message == "close server":
        print("Shutdown requested")
        return json.dumps({"status": "shutting down"})

    if message == "check if server is ready":
        result = translator.translator_ready_or_not
        return json.dumps(result)

    if message == "translate sentences":
        start = time.time()
        print("Translation request received")
        translation = translator.translate(content)
        print(translation)
        end = time.time()
        print(f"Translation completed in {end - start:.2f}s")
        return json.dumps(translation, ensure_ascii=False)

    if message == "translate batch":
        print("Batch translation request received")
        translation = translator.translate(content)
        return json.dumps(translation, ensure_ascii=False)

    if message == "pause":
        return json.dumps(translator.pause())

    if message == "resume":
        return json.dumps(translator.resume())


print(f"Starting Translation API Server on {host}:{port}")
serve(app, host=host, port=port)
