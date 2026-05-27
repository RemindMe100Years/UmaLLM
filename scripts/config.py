import json
import logging
import logging.handlers
import os
import re
import sys
import time


class SensitiveFilter(logging.Filter):
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


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")
CHARACTER_FILE = os.path.join(ROOT_DIR, "data", "character_memory.json")
CAPABILITIES_CACHE_FILE = os.path.join(ROOT_DIR, "cache", "api_capabilities.json")

LOGS_DIR = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


_initialized_logging = False

def setup_logging():
    global _initialized_logging
    if _initialized_logging:
        logger = logging.getLogger("TranslationServer")
        with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
            settings = json.load(file)
        return logger, settings

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOGS_DIR, f"server_{timestamp}.log")

    log_files = sorted([f for f in os.listdir(LOGS_DIR) if f.startswith("server_") and f.endswith(".log")])
    for old_log in log_files[:-5]:
        os.remove(os.path.join(LOGS_DIR, old_log))

    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        settings = json.load(file)

    sensitive_filter = SensitiveFilter()
    sensitive_filter.load_sensitive_values(settings)

    logger = logging.getLogger("TranslationServer")
    logger.setLevel(logging.INFO)
    logger.addFilter(sensitive_filter)

    TL_LEVEL = 25
    logging.addLevelName(TL_LEVEL, "TL")
    def tl_log(self, message, *args, **kwargs):
        if self.isEnabledFor(TL_LEVEL):
            self._log(TL_LEVEL, message, args, **kwargs)
    logger.tl = lambda msg, *args, **kwargs: tl_log(logger, msg, *args, **kwargs)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False

    logging.getLogger("litellm").setLevel(logging.CRITICAL)
    logging.getLogger("httpx").setLevel(logging.CRITICAL)

    _initialized_logging = True
    return logger, settings


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def load_character_memory():
    with open(CHARACTER_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("characters", {})
