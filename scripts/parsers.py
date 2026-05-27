import json
import re


_CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]')
_TOKEN_PATTERN = re.compile(r'<\|[^>]*>')
_THINKING_BLOCK = re.compile(r'<\|be_thought_\|>.*?<\|ee_thought_\|>', re.DOTALL)
_UID_PATTERN = re.compile(r'"?(LINE_\d+)"?\s*:\s*"?(.+?)"?(?:\s*,?\s*$)')


def strip_uid(text):
    """Strip LINE_NNN prefix from translation if LLM included it."""
    return re.sub(r'^LINE_\d+[:\-=–—]*\s*', '', text.strip())


def clean_translations(translations):
    """Strip newlines, escape artifacts, CJK chars, thinking blocks, and clean up translations."""
    cleaned = []
    for t in translations:
        t = strip_uid(t)
        t = _THINKING_BLOCK.sub("", t)
        t = t.replace("\n", " ").replace("\r", " ")
        t = re.sub(r"\\+", "", t)
        t = re.sub(r"\}}+", "}", t)
        t = _CJK_PATTERN.sub("", t)
        t = _TOKEN_PATTERN.sub("", t)
        t = re.sub(r"'\s+\w+'", "'", t)
        t = re.sub(r"'/(\\w)", r"'\1", t)
        t = re.sub(r"  +", " ", t).strip()
        cleaned.append(t)
    return [t for t in cleaned if t]


def parse_uid_response(raw_response, expected_ids):
    """Parse UID-keyed JSON response. Extracts translations ordered by expected IDs."""
    text = raw_response.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    text = re.sub(r',(\s*[}\]])', r'\1', text)

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            result = []
            for uid in expected_ids:
                val = obj.get(uid, "")
                if val:
                    result.append(strip_uid(str(val)))
                else:
                    result.append("")
            return clean_translations(result)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                result = []
                for uid in expected_ids:
                    val = obj.get(uid, "")
                    if val:
                        result.append(strip_uid(str(val)))
                    else:
                        result.append("")
                return clean_translations(result)
        except Exception:
            pass

    found = _UID_PATTERN.findall(text)
    if found:
        uid_map = {uid: strip_uid(val.strip()) for uid, val in found}
        result = []
        for uid in expected_ids:
            val = uid_map.get(uid, "")
            result.append(val if val else "")
        return clean_translations(result)

    return parse_json_response(raw_response)


def parse_json_response(raw_response):
    """Parse JSON response from structured output. Extracts the translations array."""
    text = raw_response.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    text = re.sub(r'"\s+"', '", "', text)
    text = re.sub(r',(\s*[}\]])', r'\1', text)

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            if "translations" in obj:
                return clean_translations(obj["translations"])
            if "translation" in obj:
                return clean_translations([obj["translation"]])
        if isinstance(obj, list):
            return clean_translations(obj)
    except Exception:
        pass

    for pattern in [r"\{.*\}", r"\[.*\]"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                if isinstance(obj, dict):
                    if "translations" in obj:
                        return clean_translations(obj["translations"])
                    if "translation" in obj:
                        return clean_translations([obj["translation"]])
                if isinstance(obj, list):
                    return clean_translations(obj)
            except Exception:
                pass

    numbered = re.findall(r"^\d+\.\s+(.+)$", text, re.MULTILINE)
    if numbered:
        return clean_translations(numbered)

    return []
