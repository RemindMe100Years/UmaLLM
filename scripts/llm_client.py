import json
import os
import datetime

from config import CAPABILITIES_CACHE_FILE


def load_capabilities_cache(api_server, model_name):
    """Load cached API capabilities. Returns dict entry or None."""
    try:
        if not os.path.exists(CAPABILITIES_CACHE_FILE):
            return None
        with open(CAPABILITIES_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        key = f"{api_server}__{model_name}"
        return cache.get(key)
    except Exception:
        return None


def save_capabilities_cache(capabilities, api_server, model_name):
    """Save API capabilities to cache. Enforces 1KB size limit. Only caches successes."""
    try:
        cache = {}
        if os.path.exists(CAPABILITIES_CACHE_FILE):
            try:
                with open(CAPABILITIES_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
        cache_key = f"{api_server}__{model_name}"
        cache[cache_key] = capabilities
        serialized = json.dumps(cache).encode("utf-8")
        while len(serialized) > 1024 and len(cache) > 1:
            oldest_key = next(iter(cache))
            if oldest_key != cache_key:
                del cache[oldest_key]
                serialized = json.dumps(cache).encode("utf-8")
            else:
                break
        os.makedirs(os.path.dirname(CAPABILITIES_CACHE_FILE), exist_ok=True)
        with open(CAPABILITIES_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        from config import setup_logging
        logger, _ = setup_logging()
        logger.warning("Failed to save capabilities cache: %s", e)


def build_json_schema(count, ids=None):
    if ids:
        props = {k: {"type": "string"} for k in ids}
        req = list(ids)
    else:
        props = {"translation": {"type": "string"}}
        req = ["translation"]
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "translation_result",
            "schema": {
                "type": "object",
                "properties": props,
                "required": req,
                "additionalProperties": False,
            },
            "strict": True,
        },
    }
