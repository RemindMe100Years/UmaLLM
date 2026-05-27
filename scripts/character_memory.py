import re

HONORIFIC_MAP = {
    'さん': 'san', '様': 'sama', 'さま': 'sama',
    '君': 'kun', 'くん': 'kun', 'ちゃん': 'chan',
    'たん': 'tan', 'ち': 'chi',
    '先生': 'sensei', '先輩': 'senpai', '社長': 'shachou',
    '部長': 'buchou', 'どの': 'dono', '兄貴': 'aniki',
    '氏': 'shi', '殿': 'tono', '上': 'ue',
    '師匠': 'shishou', '隊長': 'taichou', '公': 'kimi',
    '殿下': 'denka', '陛下': 'heika', '閣下': 'kakkou',
    '坊': 'bou', '嬢': 'jou',
}

_HONORIFIC_PATTERN = r'(?:さん|様|さま|君|くん|ちゃん|たん|ち|先生|先輩|社長|部長|どの|兄貴|氏|殿|上|師匠|隊長|公|殿下|陛下|閣下|坊|嬢)'


def is_standalone(word, search_text):
    """Check if word appears in the search text. Short nicknames (<=3 chars) require an honorific suffix to avoid false positives."""
    if len(word) <= 3:
        pattern = re.escape(word) + _HONORIFIC_PATTERN
        return bool(re.search(pattern, search_text))
    return word in search_text


def match_nickname_with_honorific(jp_nick, search_text, preserve_honorifics):
    """Find jp_nick in search_text and return (full_match, transliterated_honorific) or (jp_nick, None)."""
    if not preserve_honorifics:
        return jp_nick, None
    pattern = re.escape(jp_nick) + r'(' + _HONORIFIC_PATTERN + r')(?=[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]|\Z)'
    m = re.search(pattern, search_text)
    if m:
        honorific = m.group(1)
        eng_hon = HONORIFIC_MAP.get(honorific, honorific)
        return jp_nick + honorific, eng_hon
    return jp_nick, None


def substitute(value, input_language, output_language):
    subs = {
        "input_language": input_language,
        "output_language": output_language,
    }
    if isinstance(value, str):
        return value.format(**subs)
    if isinstance(value, list):
        return [substitute(v, input_language, output_language) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, input_language, output_language) for k, v in value.items()}
    return value


def build_char_instructions(character_memory, search_text, input_language, output_language, preserve_honorifics):
    instructions = []
    matched_info = []

    matched_keys = set()
    for original_jp in character_memory:
        if original_jp in search_text:
            matched_keys.add(original_jp)

    for original_jp, raw_data in character_memory.items():
        char_data = substitute(raw_data, input_language, output_language)
        found_this_char = False
        nick_parts = []
        char_nicknames = []

        if original_jp in search_text:
            found_this_char = True

        nicknames = char_data.get("nickname", [])
        if isinstance(nicknames, str):
            nicknames = [nicknames]

        for nick in nicknames:
            match = re.search(r"(.*?) \((.*?)\)", nick)
            if match:
                eng_nick, jp_nick = match.group(1).strip(), match.group(2).strip()
                if is_standalone(jp_nick, search_text):
                    other_keys = [mk for mk in matched_keys if mk != original_jp]
                    is_substring = any(jp_nick in mk and len(jp_nick) < len(mk) for mk in other_keys)
                    if not is_substring:
                        full_jp, eng_hon = match_nickname_with_honorific(jp_nick, search_text, preserve_honorifics)
                        eng_target = f"{eng_nick}-{eng_hon}" if eng_hon else eng_nick
                        nick_parts.append(f"{full_jp}->{eng_target}")
                        char_nicknames.append(eng_nick)
                        found_this_char = True
            elif is_standalone(nick, search_text):
                other_keys = [mk for mk in matched_keys if mk != original_jp]
                is_substring = any(nick in mk and len(nick) < len(mk) for mk in other_keys)
                if not is_substring:
                    full_jp, eng_hon = match_nickname_with_honorific(nick, search_text, preserve_honorifics)
                    target = f"{nick}-{eng_hon}" if eng_hon else nick
                    nick_parts.append(f"{full_jp}->{target}")
                    char_nicknames.append(nick)
                    found_this_char = True

        if found_this_char:
            gender = char_data.get('gender', '')
            gender_tag = f" ({gender})" if gender else ""
            nick_tag = f" | Nicknames: {', '.join(nick_parts)}" if nick_parts else ""
            line = f"- {original_jp} -> {char_data['name']}{gender_tag}{nick_tag}"
            notes = char_data.get("notes")
            if notes and notes.strip():
                line += f" - {notes}"
            instructions.append(line)
            matched_info.append((char_data['name'], char_nicknames))

    return instructions, matched_info


def log_character_matches(matched_info):
    from config import setup_logging
    logger, _ = setup_logging()
    parts = []
    for name, nicknames in matched_info:
        if nicknames:
            parts.append(f"{name} ({', '.join(nicknames)})")
        else:
            parts.append(name)
    if parts:
        logger.info("[MAP] Character matches found: %s", ", ".join(parts))


def apply_character_memory(character_memory, input_text, input_language, output_language, preserve_honorifics):
    search_text = " ".join(input_text) if isinstance(input_text, list) else input_text
    instructions, matched_info = build_char_instructions(character_memory, search_text, input_language, output_language, preserve_honorifics)
    log_character_matches(matched_info)
    result = ""
    if instructions:
        result = "\n[GLOSSARY]:\n" + "\n\n".join(instructions)
    return result
