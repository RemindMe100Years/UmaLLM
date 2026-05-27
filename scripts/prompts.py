import re


def build_uid_format(lines, start_idx=0):
    """Build UID-keyed input format with spacing between lines."""
    entries = []
    for i, line in enumerate(lines):
        uid = f"LINE_{start_idx + i + 1:03d}"
        escaped = line.replace("\\", "\\\\").replace('"', '\\"')
        entries.append(f'"{uid}": "{escaped}"')
    return "\n\n".join(entries)


def build_messages(system_content, history, user_content):
    """Build message list for LLM request.

    When system_content is not None (caching on): [system, history..., user].
    When system_content is None (caching off): [history..., user].
    """
    if system_content:
        return [{"role": "system", "content": system_content}] + history + [{"role": "user", "content": user_content}]
    else:
        return history + [{"role": "user", "content": user_content}]


def build_prompt(base_instruction, char_map, uid_format, expected_count, ctx_before_block, instruction_text, prompt_cache_enabled):
    """Build translation prompt. Returns (system_content, user_content) tuple.

    When prompt_cache_enabled: system_content = base_instruction (stable prefix for caching).
    When off: system_content = None, user_content = full prompt with markers (old behavior).
    """
    ctx_hint = (
        f"Lines prefixed with '>' are reference only — do not produce translations for them. "
        if ctx_before_block else ""
    )

    if prompt_cache_enabled:
        system_content = base_instruction
        user_content = (
            f"{instruction_text}\n\n"
            f"{ctx_hint}"
            f"{char_map}\n\n"
            f"{ctx_before_block}"
            f"{uid_format}"
        )
        return (system_content, user_content)
    else:
        full_prompt = (
            f"### INSTRUCTIONS ###\n{base_instruction}\n"
            f"{char_map}\n\n"
            f"{instruction_text}\n\n"
            f"{ctx_hint}\n\n"
            f"{ctx_before_block}"
            f"--- TRANSLATE THESE ---\n"
            f"{uid_format}\n"
            f"--- END TRANSLATION ---"
        )
        return (None, full_prompt)


def looks_like_fragment(a, b):
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


def realign_translations(input_lines, translations):
    n = len(input_lines)
    m = len(translations)
    if n == m:
        return translations[:]
    if m == 0:
        return ["Error"] * n

    if m < n:
        from config import setup_logging
        logger, _ = setup_logging()
        missing = n - m
        logger.warning("REALIGN: Dropped %d translation(s), filling with last available", missing)
        out = list(translations) + [translations[-1]] * missing
        return out[:n]

    surplus = m - n
    from config import setup_logging
    logger, _ = setup_logging()
    logger.info("REALIGN: Got %d translations for %d inputs, merging %d surplus", m, n, surplus)

    sub_line_counts = [max(1, input_lines[i].count('\n') + 1) for i in range(n)]

    out = []
    ti = 0
    remaining_surplus = surplus
    for i in range(n):
        is_multiline = '\n' in input_lines[i]
        if is_multiline and remaining_surplus > 0:
            absorb = min(sub_line_counts[i] - 1, remaining_surplus)
            needed = 1 + absorb
            chunk = translations[ti:ti + needed]
            if len(chunk) < needed:
                chunk = translations[ti:]
            merged = "\n".join(t.strip() for t in chunk)
            out.append(merged.strip())
            ti += len(chunk)
            remaining_surplus -= (len(chunk) - 1)
        else:
            out.append(translations[ti])
            ti += 1

    while len(out) < n:
        out.append("Error")
    excess = len(out) - n
    if excess > 0:
        logger.warning("REALIGN: Dropping %d excess translation(s)", excess)
    return out[:n]


def build_batches(list_of_text, chunk_size, auto_min_total_lines, auto_min_lines_per_worker, parallel_workers, context_lines, batch_char_map=None):
    n = len(list_of_text)
    if n == 0:
        return []
    batches = []
    if chunk_size == "auto":
        if (auto_min_total_lines < 1 or n >= auto_min_total_lines) and (auto_min_lines_per_worker < 1 or (n // parallel_workers) >= auto_min_lines_per_worker):
            num_batches = min(n, parallel_workers)
        else:
            num_batches = 1
        if num_batches < 1:
            num_batches = 1
        base_size = n // num_batches
        remainder = n % num_batches
        cs = None
    else:
        num_batches = None
        base_size = None
        remainder = None
        cs = max(1, chunk_size)
    overlap = max(0, context_lines)

    batch_start = 0
    batch_idx = 0
    while batch_start < n:
        if cs is None:
            this_cs = base_size + (1 if batch_idx < remainder else 0)
        else:
            this_cs = cs
        batch_end = min(batch_start + this_cs, n)

        ctx_before_start = max(0, batch_start - overlap)
        ctx_after_end = min(n, batch_end + overlap)

        batches.append({
            'start': batch_start,
            'end': batch_end,
            'context_before': list_of_text[ctx_before_start:batch_start],
            'context_after': list_of_text[batch_end:ctx_after_end],
            'translate_lines': list_of_text[batch_start:batch_end],
            'char_map': batch_char_map,
        })

        batch_start = batch_end
        batch_idx += 1

    return batches
