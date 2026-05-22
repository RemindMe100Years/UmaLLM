import json
import logging
import logging.handlers
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
    logger.info("Signal received, forcing exit...")
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

# Setup logging — keeps 5 most recent logs in logs/ folder
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
log_file = os.path.join(LOGS_DIR, "server.log")


class _SensitiveFilter(logging.Filter):
    """Redact API keys and other sensitive data from log messages."""
    def __init__(self):
        super().__init__()
        self._patterns = []
        self._path_pattern = re.compile(r'[A-Z]:\\[^:\s]*', re.IGNORECASE)

    def load_sensitive_values(self, settings):
        sensitive = settings.get("api_key")
        if sensitive and isinstance(sensitive, str):
            self._patterns.append(sensitive)

    def filter(self, record):
        msg = record.getMessage()
        for pattern in self._patterns:
            msg = msg.replace(pattern, "***REDACTED***")
        msg = self._path_pattern.sub("***REDACTED***", msg)
        record.msg = msg
        record.args = None
        return True


with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
    settings = json.load(file)

port = settings["HTTP_port_number"]
host = "0.0.0.0"

# Setup logging — timestamped files, keeps 5 most recent
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
timestamp = time.strftime("%Y%m%d_%H%M%S")
log_file = os.path.join(LOGS_DIR, f"server_{timestamp}.log")

# Clean up old logs — keep only 5 most recent
log_files = sorted([f for f in os.listdir(LOGS_DIR) if f.startswith("server_") and f.endswith(".log")])
for old_log in log_files[:-5]:
    os.remove(os.path.join(LOGS_DIR, old_log))

sensitive_filter = _SensitiveFilter()
sensitive_filter.load_sensitive_values(settings)

logger = logging.getLogger("TranslationServer")
logger.setLevel(logging.INFO)
logger.addFilter(sensitive_filter)

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# Prevent propagation to root logger (causes duplicate output)
logger.propagate = False

# Suppress litellm's own logging to prevent duplicate output
logging.getLogger("litellm").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)


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
        self.frequency_penalty = settings.get("frequency_penalty", 0.5)
        self.presence_penalty = settings.get("presence_penalty", 0.5)
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

        # Check if model supports structured output
        self._supported_params = self._get_supported_params()
        self._supports_json_schema = self._check_json_schema_support()

    def _check_json_schema_support(self):
        try:
            from litellm import supports_response_schema
            has_response_format = "response_format" in self._supported_params
            has_schema = supports_response_schema(
                model=self.model_name, custom_llm_provider=self._get_provider()
            )
            return has_response_format and has_schema
        except Exception:
            return False

    def _get_supported_params(self):
        try:
            return litellm.get_supported_openai_params(
                model=self.model_name, custom_llm_provider=self._get_provider()
            )
        except Exception:
            return []

    def _get_provider(self):
        if "ollama" in self.model_name:
            return "ollama"
        if "lm_studio" in self.model_name:
            return "lm_studio"
        if "oobabooga" in self.model_name:
            return "openai"
        return None

    def _build_json_schema(self, count):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "translation_result",
                "schema": {
                    "type": "object",
                    "properties": {
                        "translations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": count,
                            "maxItems": count,
                        }
                    },
                    "required": ["translations"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }

    def _build_char_instructions(self, input_text):
        instructions = set()
        search_text = (
            " ".join(input_text) if isinstance(input_text, list) else input_text
        )

        matched_names = []
        for original_jp in self.character_memory:
            if original_jp in search_text:
                matched_names.append(original_jp)

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
                        is_substring = any(jp_nick in mn and len(jp_nick) < len(mn) for mn in matched_names)
                        if not is_substring:
                            instructions.add(f"- {jp_nick} -> {eng_nick}")
                            found_this_char = True
                elif nick in search_text:
                    is_substring = any(nick in mn and len(nick) < len(mn) for mn in matched_names)
                    if not is_substring:
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
            logger.warning("REALIGN: Dropped %d translation(s), filling with last available", missing)
            out = list(translations) + [translations[-1]] * missing
            return out[:n]

        surplus = m - n
        logger.info("REALIGN: Got %d translations for %d inputs, merging %d surplus", m, n, surplus)

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
            logger.warning("REALIGN: Dropping %d excess translation(s)", excess)
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

    def execute(self, messages, response_format=None):
        api_params = {"model": self.model_name, "messages": messages}
        param_map = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repetition_penalty": self.repetition_penalty,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "max_tokens": self.max_tokens,
        }
        for key, value in param_map.items():
            if key in self._supported_params:
                api_params[key] = value

        if response_format is not None:
            api_params["response_format"] = response_format

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

    def _parse_json_response(self, raw_response):
        """Parse JSON response from structured output. Extracts the translations array."""
        text = raw_response.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        # Fix missing commas between adjacent strings: "str1" "str2" → "str1", "str2"
        text = re.sub(r'"\s+"', '", "', text)

        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "translations" in obj:
                return self._clean_translations(obj["translations"])
            if isinstance(obj, list):
                return self._clean_translations(obj)
        except Exception:
            pass

        # Fallback: try to find a JSON object/array in the text
        for pattern in [r"\{.*\}", r"\[.*\]"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    obj = json.loads(match.group(0))
                    if isinstance(obj, dict) and "translations" in obj:
                        return self._clean_translations(obj["translations"])
                    if isinstance(obj, list):
                        return self._clean_translations(obj)
                except Exception:
                    pass

        return []

    _CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')

    def _clean_translations(self, translations):
        """Strip newlines, escape artifacts, CJK chars, and clean up translations."""
        cleaned = []
        for t in translations:
            t = t.replace("\n", " ").replace("\r", " ")
            t = re.sub(r"\\+", "", t)  # remove stray backslashes
            t = re.sub(r"\}}+", "}", t)  # fix doubled braces
            t = self._CJK_PATTERN.sub("", t)  # remove CJK chars model copies from source
            t = re.sub(r"  +", " ", t).strip()
            cleaned.append(t)
        return cleaned

    def _is_trivial(self, raw_in, raw_out):
        stripped = raw_out.strip()
        inp_chars = len(raw_in.replace(" ", ""))
        out_stripped = re.sub(r"[\.\-\!\?\,\:\;\x27\x60\~\u2014\u2013\(\)\[\]{}]", "", stripped)
        if inp_chars > 20 and (len(out_stripped) == 0 or len(stripped) < 5):
            return True
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

        cleaned, result = self._process_single_line(input_text)
        with self._lock:
            self.messages.append({"role": "user", "content": input_text})
            self.messages.append({"role": "assistant", "content": result})
        return cleaned

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
        result = ""
        schema = self._build_json_schema(1)

        for attempt in range(self.max_retries):
            if attempt > 0:
                logger.info("Single-line retry %d/%d", attempt + 1, self.max_retries)
            final_payload = history + [{"role": "user", "content": current_turn_prompt}]
            result = self.execute(messages=final_payload, response_format=schema)
            parsed = self._parse_json_response(result)
            cleaned = plugins.process_output_text(parsed[0]) if parsed else ""
            if cleaned and cleaned.strip():
                break

        logger.info("RAW: %s", input_text)
        logger.info("TRN: %s", cleaned)

        return cleaned, result

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

        ctx_before_block = ""
        if processed_ctx_before:
            lines_str = "\n".join(f"> {t}" for t in processed_ctx_before)
            ctx_before_block = (f"### CONTEXT BEFORE (preceding dialogue, for reference only) ###\n"
                                f"{lines_str}\n\n")

        ctx_after_block = ""
        if processed_ctx_after:
            lines_str = "\n".join(f"> {t}" for t in processed_ctx_after)
            ctx_after_block = (f"\n\n### CONTEXT AFTER (succeeding dialogue, for reference only) ###\n"
                               f"{lines_str}\n")

        expected_count = len(processed_translate)
        translate_lines_text = "\n".join(
            f"--- {i+1}/{expected_count} ---\n{t}" for i, t in enumerate(processed_translate)
        )

        current_turn_prompt = (
            f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
            f"{char_map}\n\n"
            f"### FORMAT EXAMPLE (showing 2 of {expected_count} lines) ###\n"
            f"Input:\n--- 1/{expected_count} ---\nHello\n--- 2/{expected_count} ---\nWorld\n[... remaining {expected_count - 2} lines ...]\n\n"
            f"Example Output Format: {{\"translations\": [\"<line 1 translation>\", \"<line 2 translation>\", ...]}}\n\n"
            f"Translate ONLY the following {expected_count} lines in the ### LINES TO TRANSLATE ### section below.\n\n"
            f"{ctx_before_block}"
            f"### LINES TO TRANSLATE ###\n{translate_lines_text}"
            f"{ctx_after_block}"
        )

        response_format = self._build_json_schema(expected_count) if self._supports_json_schema else None

        translations = []
        result = ""
        for attempt in range(self.max_retries):
            if attempt > 0:
                logger.info("CHUNK %d retry %d/%d (got %d/%d translations)", start_idx, attempt + 1, self.max_retries, len(translations), expected_count)
            final_payload = history + [{"role": "user", "content": current_turn_prompt}]
            result = self.execute(messages=final_payload, response_format=response_format)
            translations = self._parse_json_response(result)

            if len(translations) == expected_count:
                break

            if attempt >= self.max_retries - 1:
                break

            current_turn_prompt = (
                f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
                f"{char_map}\n\n"
                f"You produced {len(translations)} translations for {expected_count} lines. "
                f"Output exactly {expected_count} translations.\n\n"
                f"{ctx_before_block}"
                f"### LINES TO TRANSLATE ###\n{translate_lines_text}"
                f"{ctx_after_block}"
            )

        single_line_pairs = []
        if not translations or len(translations) != expected_count:
            logger.warning("CHUNK %d: Translation failed after %d retries — falling back to line-by-line", start_idx, self.max_retries)
            translations = []
            for line in processed_translate:
                t, r = self._process_single_line(line)
                translations.append(t)
                single_line_pairs.append((line, r))
        else:
            # Fix trivial lines individually — no need to re-translate the whole chunk
            bad_indices = [i for i in range(expected_count)
                           if self._is_trivial(processed_translate[i], translations[i])]
            if bad_indices:
                logger.info("CHUNK %d: Retranslating %d trivial line(s): %s", start_idx, len(bad_indices), bad_indices)
                for i in bad_indices:
                    t, r = self._process_single_line(processed_translate[i])
                    translations[i] = t
                    single_line_pairs.append((processed_translate[i], r))

        with self._lock:
            for inp, out in single_line_pairs:
                self.messages.append({"role": "user", "content": inp})
                self.messages.append({"role": "assistant", "content": out})

        cleaned = [plugins.process_output_text(t) for t in translations[:expected_count]]
        return start_idx, cleaned, processed_translate, result

    def _process_batch_llm(self, list_of_text):
        processed_input = [plugins.process_input_text(t) for t in list_of_text]
        char_map = self.apply_character_memory(processed_input)

        expected_count = len(processed_input)
        lines_text = "\n".join(f"--- {i + 1}/{expected_count} ---\n{t}" for i, t in enumerate(processed_input))

        batch_prompt = (
            f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
            f"{char_map}\n\n"
            f"### FORMAT EXAMPLE (showing 2 of {expected_count} lines) ###\n"
            f"Input:\n--- 1/{expected_count} ---\nHello\n--- 2/{expected_count} ---\nWorld\n[... remaining {expected_count - 2} lines ...]\n\n"
            f"Example Output Format: {{\"translations\": [\"<line 1 translation>\", \"<line 2 translation>\", ...]}}\n\n"
            f"Translate ONLY the following {expected_count} lines in the ### LINES TO TRANSLATE ### section below.\n\n"
            f"### LINES TO TRANSLATE ###\n{lines_text}"
        )

        history = (
            self.messages[-(self.context_lines * 2) :] if self.context_lines > 0 else []
        )

        response_format = self._build_json_schema(expected_count) if self._supports_json_schema else None

        translations = []
        result = ""
        for attempt in range(self.max_retries):
            if attempt > 0:
                logger.info("BATCH_LLM retry %d/%d (got %d/%d translations)", attempt + 1, self.max_retries, len(translations), expected_count)
            final_payload = history + [{"role": "user", "content": batch_prompt}]
            result = self.execute(messages=final_payload, response_format=response_format)
            translations = self._parse_json_response(result)

            if len(translations) == expected_count:
                break

            if attempt >= self.max_retries - 1:
                break

            batch_prompt = (
                f"### INSTRUCTIONS ###\n{self.base_instruction}\n"
                f"{char_map}\n\n"
                f"You produced {len(translations)} translations for {expected_count} lines. "
                f"Output exactly {expected_count} translations.\n\n"
                f"### LINES TO TRANSLATE ###\n{lines_text}"
            )

        single_line_pairs = []
        if not translations or len(translations) != expected_count:
            logger.warning("BATCH_LLM: Translation failed after %d retries — falling back to line-by-line", self.max_retries)
            translations = []
            for line in processed_input:
                t, r = self._process_single_line(line)
                translations.append(t)
                single_line_pairs.append((line, r))
        else:
            # Fix trivial lines individually — no need to re-translate the whole batch
            bad_indices = [i for i in range(expected_count)
                           if self._is_trivial(processed_input[i], translations[i])]
            if bad_indices:
                logger.info("BATCH_LLM: Retranslating %d trivial line(s): %s", len(bad_indices), bad_indices)
                for i in bad_indices:
                    t, r = self._process_single_line(processed_input[i])
                    translations[i] = t
                    single_line_pairs.append((processed_input[i], r))

        with self._lock:
            for inp, out in single_line_pairs:
                self.messages.append({"role": "user", "content": inp})
                self.messages.append({"role": "assistant", "content": out})

        cleaned = [plugins.process_output_text(t) for t in translations[:len(processed_input)]]
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
                        logger.info("Worker exiting (done=True, remaining=%d)", len(remaining_batches))
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

                logger.info("CHUNK %d: _translate_chunk_with_context returned (got %d/%d translations, round=%d)", start_idx, len(translations), len(processed_input), my_round)

                expected_count = len(processed_input)
                trivial_indices = [i for i in range(expected_count)
                                   if self._is_trivial(processed_input[i], translations[i])]
                is_good = (len(translations) == expected_count and not trivial_indices)

                logger.info("CHUNK %d: is_good=%s, trivial_indices=%s", start_idx, is_good, trivial_indices)
                for ti in trivial_indices:
                    logger.info("  TRIVIAL[%d]: input=%s, output=%s", ti, processed_input[ti], translations[ti])

                if is_good:
                    with self._lock:
                        if start not in completed_batches:
                            completed_q.put((start, translations, processed_input))
                            completed_batches.add(start)
                            remaining_batches.discard(start)
                    continue
            except Exception as e:
                logger.error("CHUNK %d: Exception: %s: %s", start_idx, type(e).__name__, e)

            logger.info("CHUNK %d: entering retry check (round=%d)", start_idx, my_round)
            action = None
            with self._lock:
                if start_idx in completed_batches:
                    logger.info("CHUNK %d: already completed, skipping", start_idx)
                    continue
                elif my_round < batch_retry_counts.get(start_idx, 0):
                    logger.info("CHUNK %d: older round (%d < %d), skipping", start_idx, my_round, batch_retry_counts.get(start_idx, 0))
                    continue

                current = batch_retry_counts.get(start_idx, 0)
                if current >= self.max_retries:
                    action = 'error'
                else:
                    batch_retry_counts[start_idx] = current + 1
                    action = ('broadcast', current + 1)

            if action == 'error':
                logger.info("CHUNK %d: max retries reached, marking as error", start_idx)
                end_idx = batch['end']
                errors = ["Error"] * (end_idx - start_idx)
                processed_input = [plugins.process_input_text(t) for t in batch['translate_lines']]
                with self._lock:
                    completed_batches.add(start_idx)
                    remaining_batches.discard(start_idx)
                completed_q.put((start_idx, errors, processed_input))
            elif action == ('broadcast', new_round):
                logger.info("CHUNK %d: broadcasting retry round %d", start_idx, new_round)
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
            logger.info("done_event set, waiting for %d workers", len(futures))

            for i, future in enumerate(futures):
                logger.info("Waiting for worker %d to finish...", i)
                future.result()
                logger.info("Worker %d finished", i)

        while not completed_q.empty():
            start_idx, translations, processed_input = completed_q.get()
            for i, translation in enumerate(translations):
                if start_idx + i < n:
                    results[start_idx + i] = translation
            for inp, out in zip(processed_input, translations):
                translated_pairs.append((inp, out))

        gaps = sum(1 for r in results if r is None)
        if gaps:
            logger.warning("CHUNK: %d line(s) had no result, filling with Error", gaps)
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

logger.info("Model: %s", translator.model_name)
logger.info("API Server: %s", translator.api_server)
logger.info("Parallel workers: %d | Chunk size: %d | Max retries: %d", translator.parallel_workers, translator.chunk_size, translator.max_retries)
logger.info("Temperature: %.2f | Top P: %.2f | Repetition penalty: %.2f", translator.temperature, translator.top_p, translator.repetition_penalty)
logger.info("Structured output supported: %s", translator._supports_json_schema)

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
        logger.info("Shutdown requested")
        return json.dumps({"status": "shutting down"})

    if message == "check if server is ready":
        result = translator.translator_ready_or_not
        return json.dumps(result)

    if message == "translate sentences":
        start = time.time()
        logger.info("Translation request received (%d lines)", len(content) if isinstance(content, list) else 1)
        translation = translator.translate(content)
        end = time.time()
        if isinstance(translation, list):
            for i, (raw, trn) in enumerate(zip(content, translation)):
                logger.info("RAW %d: %s", i + 1, raw)
                logger.info("TRN %d: %s", i + 1, trn)
        for h in logger.handlers:
            h.flush()
        logger.info("Translation completed in %.2fs", end - start)
        return json.dumps(translation, ensure_ascii=False)

    if message == "translate batch":
        logger.info("Batch translation request received (%d lines)", len(content) if isinstance(content, list) else 1)
        translation = translator.translate(content)
        if isinstance(translation, list):
            for i, (raw, trn) in enumerate(zip(content, translation)):
                logger.info("RAW %d: %s", i + 1, raw)
                logger.info("TRN %d: %s", i + 1, trn)
        return json.dumps(translation, ensure_ascii=False)

    if message == "pause":
        return json.dumps(translator.pause())

    if message == "resume":
        return json.dumps(translator.resume())


logger.info("Starting Translation API Server on %s:%d", host, port)
logger.info("Server is ready")
serve(app, host=host, port=port)
