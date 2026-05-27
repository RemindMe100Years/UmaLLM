import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import plugins
from config import setup_logging
from character_memory import apply_character_memory, build_char_instructions, log_character_matches
from prompts import (
    build_uid_format, build_messages, build_prompt,
    build_batches
)
from parsers import parse_uid_response, parse_json_response, clean_translations
from quality import is_trivial, sanity_check, has_forbidden_word
from llm_client import build_json_schema


logger, _ = setup_logging()


def process_single_line(translator):
    input_text = plugins.process_input_text(translator['raw_input'])
    char_map = apply_character_memory(
        translator['character_memory'], input_text,
        translator['input_language'], translator['output_language'],
        translator['preserve_honorifics']
    )

    result = ""
    schema = build_json_schema(1)

    for attempt in range(translator['max_retries']):
        if attempt > 0:
            logger.info("Single-line retry %d/%d", attempt + 1, translator['max_retries'])

        if translator['prompt_cache_enabled']:
            system_content = translator['base_instruction']
            user_content = f"{char_map}\n\n{input_text}"
            final_payload = build_messages(system_content, [], user_content)
        else:
            current_turn_prompt = (
                f"### INSTRUCTIONS ###\n{translator['base_instruction']}\n"
                f"{char_map}\n\n"
                f"### TEXT TO TRANSLATE ###\n{input_text}"
            )
            final_payload = [{"role": "user", "content": current_turn_prompt}]

        result = translator['execute'](messages=final_payload, response_format=schema)
        parsed = parse_json_response(result)
        cleaned = plugins.process_output_text(parsed[0]) if parsed else ""
        if cleaned and cleaned.strip():
            break

    logger.tl("RAW: %s", input_text)
    logger.tl("TRN: %s", cleaned)

    return cleaned, result


def translate_chunk_with_context(batch, translator):
    start_idx = batch['start']
    translate_lines = batch['translate_lines']
    ctx_before = batch['context_before']
    ctx_after = batch['context_after']

    processed_translate = [plugins.process_input_text(t) for t in translate_lines]
    processed_ctx_before = [plugins.process_input_text(t) for t in ctx_before]
    processed_ctx_after = [plugins.process_input_text(t) for t in ctx_after]

    char_map = batch.get('char_map') or apply_character_memory(
        translator['character_memory'], processed_translate,
        translator['input_language'], translator['output_language'],
        translator['preserve_honorifics']
    )

    ctx_before_block = ""
    if processed_ctx_before:
        lines_str = "\n".join(processed_ctx_before)
        ctx_before_block = (
            f"> Reference (previous context, DO NOT translate):\n"
            f"> {lines_str.replace('\n', '\n> ')}\n\n"
        )

    expected_count = len(processed_translate)
    uid_format = build_uid_format(processed_translate, start_idx)
    expected_ids = [f"LINE_{start_idx + i + 1:03d}" for i in range(expected_count)]

    initial_instruction = (
        f'Example Output Format: {{"LINE_XXX": "<translation>", ...}}\n\n'
        f"Translate the following {expected_count} lines. "
        f"Each LINE_NNN ID maps to exactly one translation — do NOT split a single line into multiple translations."
    )
    system_content, current_turn_prompt = build_prompt(
        translator['base_instruction'], char_map, uid_format, expected_count,
        ctx_before_block, initial_instruction, translator['prompt_cache_enabled']
    )

    response_format = build_json_schema(expected_count, expected_ids) if translator['supports_json_schema'] else None

    translations = []
    result = ""
    for attempt in range(translator['max_retries']):
        if attempt > 0:
            logger.info("CHUNK %d retry %d/%d (got %d/%d translations)", start_idx, attempt + 1, translator['max_retries'], len(translations), expected_count)
        final_payload = build_messages(system_content, [], current_turn_prompt)
        result = translator['execute'](messages=final_payload, response_format=response_format)
        translations = parse_uid_response(result, expected_ids)

        if len(translations) == expected_count:
            break

        if attempt >= translator['max_retries'] - 1:
            break

        retry_instruction = (
            f'Example Output Format: {{"LINE_XXX": "<translation>", ...}}\n\n'
            f"You produced {len(translations)} translations for {expected_count} lines. Output EXACTLY {expected_count}. "
            f"Each LINE_NNN ID maps to exactly one translation — do NOT split a single line into multiple translations."
        )
        system_content, current_turn_prompt = build_prompt(
            translator['base_instruction'], char_map, uid_format, expected_count,
            ctx_before_block, retry_instruction, translator['prompt_cache_enabled']
        )

    single_line_pairs = []
    if not translations or len(translations) != expected_count:
        logger.warning("CHUNK %d: Translation failed after %d retries — falling back to line-by-line", start_idx, translator['max_retries'])
        translations = []
        for line in processed_translate:
            t, r = process_single_line({**translator, 'raw_input': line})
            translations.append(t)
            single_line_pairs.append((line, r))
    else:
        bad_indices = [i for i in range(expected_count)
                        if is_trivial(processed_translate[i], translations[i])]
        if translator.get('jamdict'):
            hallucinated = []
            for i in range(expected_count):
                ok = sanity_check(processed_translate[i], translations[i], translator['jamdict'])
                logger.info("CHUNK %d line %d: jamdict=%s | input=%s | output=%s", start_idx, i, ok, processed_translate[i], translations[i])
                if not ok:
                    hallucinated.append(i)
            new_bad = [i for i in hallucinated if i not in bad_indices]
            if new_bad:
                logger.info("CHUNK %d: Jamdict flagged %d hallucinated line(s): %s", start_idx, len(new_bad), new_bad)
            bad_indices.extend(new_bad)
        if bad_indices:
            logger.info("CHUNK %d: Retranslating %d bad line(s): %s", start_idx, len(bad_indices), bad_indices)
            for i in bad_indices:
                t, r = process_single_line({**translator, 'raw_input': processed_translate[i]})
                translations[i] = t
                single_line_pairs.append((processed_translate[i], r))

    cleaned = [plugins.process_output_text(t) for t in translations[:expected_count]]
    return start_idx, cleaned, processed_translate, result


def process_batch_llm(list_of_text, translator):
    processed_input = [plugins.process_input_text(t) for t in list_of_text]
    char_map = apply_character_memory(
        translator['character_memory'], processed_input,
        translator['input_language'], translator['output_language'],
        translator['preserve_honorifics']
    )

    expected_count = len(processed_input)
    uid_format = build_uid_format(processed_input, 0)
    expected_ids = [f"LINE_{i + 1:03d}" for i in range(expected_count)]

    initial_instruction = (
        f'Example Output Format: {{"LINE_XXX": "<translation>", ...}}\n\n'
        f"Translate ONLY the lines below. Produce EXACTLY {expected_count} translations. "
        f"Each LINE_NNN ID maps to exactly one translation — do NOT split a single line into multiple translations."
    )
    system_content, batch_prompt = build_prompt(
        translator['base_instruction'], char_map, uid_format, expected_count,
        "", initial_instruction, translator['prompt_cache_enabled']
    )

    response_format = build_json_schema(expected_count, expected_ids) if translator['supports_json_schema'] else None

    translations = []
    result = ""
    for attempt in range(translator['max_retries']):
        if attempt > 0:
            logger.info("BATCH_LLM retry %d/%d (got %d/%d translations)", attempt + 1, translator['max_retries'], len(translations), expected_count)
        final_payload = build_messages(system_content, [], batch_prompt)
        result = translator['execute'](messages=final_payload, response_format=response_format)
        translations = parse_uid_response(result, expected_ids)

        if len(translations) == expected_count:
            break

        if attempt >= translator['max_retries'] - 1:
            break

        retry_instruction = (
            f'Example Output Format: {{"LINE_XXX": "<translation>", ...}}\n\n'
            f"You produced {len(translations)} translations for {expected_count} lines. Output EXACTLY {expected_count}. "
            f"Each LINE_NNN ID maps to exactly one translation — do NOT split a single line into multiple translations."
        )
        system_content, batch_prompt = build_prompt(
            translator['base_instruction'], char_map, uid_format, expected_count,
            "", retry_instruction, translator['prompt_cache_enabled']
        )

    single_line_pairs = []
    if not translations or len(translations) != expected_count:
        logger.warning("BATCH_LLM: Translation failed after %d retries — falling back to line-by-line", translator['max_retries'])
        translations = []
        for line in processed_input:
            t, r = process_single_line({**translator, 'raw_input': line})
            translations.append(t)
            single_line_pairs.append((line, r))
    else:
        bad_indices = [i for i in range(expected_count)
                        if is_trivial(processed_input[i], translations[i])]
        if translator.get('jamdict'):
            hallucinated = []
            for i in range(expected_count):
                ok = sanity_check(processed_input[i], translations[i], translator['jamdict'])
                logger.info("BATCH line %d: jamdict=%s | input=%s | output=%s", i, ok, processed_input[i], translations[i])
                if not ok:
                    hallucinated.append(i)
            new_bad = [i for i in hallucinated if i not in bad_indices]
            if new_bad:
                logger.info("BATCH_LLM: Jamdict flagged %d hallucinated line(s): %s", len(new_bad), new_bad)
            bad_indices.extend(new_bad)
        if bad_indices:
            logger.info("BATCH_LLM: Retranslating %d bad line(s): %s", len(bad_indices), bad_indices)
            for i in bad_indices:
                t, r = process_single_line({**translator, 'raw_input': processed_input[i]})
                translations[i] = t
                single_line_pairs.append((processed_input[i], r))

    cleaned = [plugins.process_output_text(t) for t in translations[:len(processed_input)]]
    return cleaned


def worker_loop(work_q, completed_q, done_event, remaining_batches, batch_retry_counts, completed_batches, translator):
    while True:
        try:
            task = work_q.get(timeout=0.2)
        except queue.Empty:
            with translator['lock']:
                if done_event.is_set() and not remaining_batches:
                    logger.info("Worker exiting (done=True, remaining=%d)", len(remaining_batches))
                    return
            continue

        batch = task['batch']
        start_idx = batch['start']
        my_round = task.get('retry_round', 0)

        with translator['lock']:
            if start_idx in completed_batches:
                continue

        try:
            result = translate_chunk_with_context(batch, translator)
            start, translations, processed_input, raw_output = result

            logger.info("CHUNK %d: _translate_chunk_with_context returned (got %d/%d translations, round=%d)", start_idx, len(translations), len(processed_input), my_round)

            expected_count = len(processed_input)
            trivial_indices = [i for i in range(expected_count)
                                if is_trivial(processed_input[i], translations[i])]
            forbidden_indices = ([i for i in range(expected_count)
                                  if has_forbidden_word(translations[i])]
                                if not translator.get('do_horses_exist') else [])
            is_good = (len(translations) == expected_count and not trivial_indices and not forbidden_indices)

            logger.info("CHUNK %d: is_good=%s, trivial_indices=%s, forbidden_indices=%s", start_idx, is_good, trivial_indices, forbidden_indices)
            for ti in trivial_indices:
                logger.info("  TRIVIAL[%d]: input=%s, output=%s", ti, processed_input[ti], translations[ti])
            for fi in forbidden_indices:
                logger.info("  FORBIDDEN[%d]: input=%s, output=%s", fi, processed_input[fi], translations[fi])

            if is_good:
                with translator['lock']:
                    if start not in completed_batches:
                        completed_q.put((start, translations, processed_input))
                        completed_batches.add(start)
                        remaining_batches.discard(start)
                continue
        except Exception as e:
            logger.error("CHUNK %d: Exception: %s: %s", start_idx, type(e).__name__, e)

        logger.info("CHUNK %d: entering retry check (round=%d)", start_idx, my_round)
        action = None
        with translator['lock']:
            if start_idx in completed_batches:
                logger.info("CHUNK %d: already completed, skipping", start_idx)
                continue
            elif my_round < batch_retry_counts.get(start_idx, 0):
                logger.info("CHUNK %d: older round (%d < %d), skipping", start_idx, my_round, batch_retry_counts.get(start_idx, 0))
                continue

            current = batch_retry_counts.get(start_idx, 0)
            if current >= translator['max_retries']:
                action = 'error'
            else:
                batch_retry_counts[start_idx] = current + 1
                action = ('broadcast', current + 1)

        if action == 'error':
            logger.info("CHUNK %d: max retries reached, marking as error", start_idx)
            end_idx = batch['end']
            errors = ["Error"] * (end_idx - start_idx)
            processed_input = [plugins.process_input_text(t) for t in batch['translate_lines']]
            with translator['lock']:
                completed_batches.add(start_idx)
                remaining_batches.discard(start_idx)
            completed_q.put((start_idx, errors, processed_input))
        elif action == ('broadcast', new_round):
            logger.info("CHUNK %d: queuing retry round %d", start_idx, new_round)
            work_q.put({'batch': batch, 'retry_round': new_round})


def process_parallel_chunked(list_of_text, translator):
    if not list_of_text:
        return []

    batch_char_map = None
    if translator['append_all_characters']:
        search_text = " ".join(list_of_text)
        instructions, matched_info = build_char_instructions(
            translator['character_memory'], search_text,
            translator['input_language'], translator['output_language'],
            translator['preserve_honorifics']
        )
        log_character_matches(matched_info)
        if instructions:
            batch_char_map = "\n[GLOSSARY]:\n" + "\n\n".join(instructions)

    batches = build_batches(
        list_of_text, translator['chunk_size'], translator.get('auto_min_total_lines', 25), translator.get('auto_min_lines_per_worker', 0),
        translator['parallel_workers'], translator['context_lines'], batch_char_map
    )

    n = len(list_of_text)
    results = [None] * n

    work_q = queue.Queue()
    completed_q = queue.Queue()
    done_event = threading.Event()

    remaining_batches = {b['start'] for b in batches}
    batch_retry_counts = {}
    completed_batches = set()

    for batch in batches:
        work_q.put({'batch': batch, 'retry_round': 0})

    with ThreadPoolExecutor(max_workers=translator['parallel_workers']) as executor:
        futures = [
            executor.submit(
                worker_loop,
                work_q, completed_q, done_event,
                remaining_batches, batch_retry_counts, completed_batches,
                translator,
            )
            for _ in range(translator['parallel_workers'])
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

    gaps = sum(1 for r in results if r is None)
    if gaps:
        logger.warning("CHUNK: %d line(s) had no result, filling with Error", gaps)
    for i in range(n):
        if results[i] is None:
            results[i] = "Error"

    if translator.get('jamdict'):
        bad_final = []
        for i in range(n):
            raw = plugins.process_input_text(list_of_text[i])
            ok = sanity_check(raw, results[i], translator['jamdict'])
            if not ok:
                bad_final.append(i)
        if bad_final:
            logger.warning("FINAL CHECK: %d bad translation(s) at indices %s — retranslating", len(bad_final), bad_final)
            for i in bad_final:
                t, _ = process_single_line({**translator, 'raw_input': list_of_text[i]})
                results[i] = t

    return results
