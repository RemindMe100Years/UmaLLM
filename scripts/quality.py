import re

FORBIDDEN_WORDS = {'horse'}


def has_forbidden_word(text):
    text_lower = text.lower()
    return any(word in text_lower for word in FORBIDDEN_WORDS)


def is_trivial(raw_in, raw_out):
    stripped = raw_out.strip()
    inp_chars = len(raw_in.replace(" ", ""))
    out_stripped = re.sub(r"[\.\-\!\?\,\:\;\x27\x60\~\u2014\u2013\(\)\[\]{}]", "", stripped)
    if inp_chars > 0 and len(stripped) == 0:
        return True
    inp_stripped = re.sub(r"[\.\-\!\?\,\:\;\x27\x60\~\u2014\u2013\(\)\[\]{}！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～。「」『』【】、・〜\u2018\u2019\u201c\u201d\u2010\u2011\u2012\u2015\u2016\u2017\u2026]", "", raw_in.replace(" ", ""))
    if inp_chars > 10 and len(out_stripped) == 0 and len(inp_stripped) > 2:
        return True
    if inp_chars > 15 and len(stripped) < 8 and len(inp_stripped) > 2:
        return True
    if inp_chars > 15 and len(out_stripped) < inp_chars * 0.35 and len(inp_stripped) > 2:
        return True
    cjk_pattern = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')
    if cjk_pattern.search(stripped):
        return True
    return False


def sanity_check(japanese_input, english_output, jamdict_local):
    """Check if the English output is a reasonable translation of the Japanese input."""
    if not jamdict_local:
        return True
    from config import _JAMDICT_AVAILABLE, Jamdict
    if english_output and re.match(r'^\$[\w]+', english_output.strip()):
        return False
    if not hasattr(jamdict_local, 'instance'):
        jamdict_local.instance = Jamdict()
    result = jamdict_local.instance.lookup(japanese_input)
    if not result.entries:
        return True
    all_meanings = []
    for entry in result.entries:
        for sense in entry.senses:
            for gloss in sense.gloss:
                all_meanings.append(gloss.text.lower())
    if not all_meanings:
        return True
    meaningful = sorted(set(m for m in all_meanings if len(m) > 3), key=len, reverse=True)
    if not meaningful:
        return True
    output_lower = english_output.lower()
    matched = [m for m in meaningful if m in output_lower]
    if not matched:
        return False
    longest = meaningful[0]
    if len(longest) <= 5:
        return True
    else:
        if len(matched) < 2:
            return False
        return any(len(m) >= len(longest) * 0.5 for m in matched)
