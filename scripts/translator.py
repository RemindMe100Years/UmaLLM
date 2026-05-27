import threading

try:
    import botocore
except ImportError:
    import io, warnings
    old_stderr = __import__('sys').stderr
    __import__('sys').stderr = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import litellm
    __import__('sys').stderr = old_stderr
else:
    import litellm

try:
    from jamdict import Jamdict
    _JAMDICT_AVAILABLE = True
except ImportError:
    _JAMDICT_AVAILABLE = False
    Jamdict = None

from config import load_settings, load_character_memory
from llm_client import load_capabilities_cache, save_capabilities_cache
from pipeline import process_single_line, process_batch_llm, process_parallel_chunked


class Main_Translator:
    def __init__(self):
        self.translator_ready_or_not = False
        self.can_change_language_or_not = True
        self.input_language = "Japanese"
        self.output_language = "English"
        self.model_name = None
        self.api_key = None
        self.api_server = None
        self.context_lines = 0
        self.temperature = 0.6
        self.top_p = 0.95
        self.top_k = 64
        self.repetition_penalty = 1.1
        self.frequency_penalty = 0.5
        self.presence_penalty = 0.5
        self.max_tokens = 4096
        self.min_p = 0.05

        self.parallel_workers = 3
        self.chunk_size = "auto"
        self.auto_min_total_lines = 30
        self.auto_min_lines_per_worker = 15
        self.max_retries = 3
        self.append_all_characters = False
        self.jamdict_sanity_check = False
        self.preserve_honorifics = True
        self.prompt_cache_enabled = True
        self.do_horses_exist = True
        self._lock = threading.Lock()

        self._jamdict = None
        self.stop_translation = False
        self.base_instruction = ""
        self.character_memory = {}

        self._supported_params = []
        self._supports_json_schema = False

        self._load_config()

    def _load_config(self):
        settings = load_settings()
        self.output_language = settings["output_language"]
        self.model_name = settings["model_name"]
        self.api_key = settings["api_key"]
        self.api_server = settings["api_server"]
        self.context_lines = settings["context_lines"]
        self.temperature = settings["temperature"]
        self.top_p = settings["top_p"]
        self.top_k = settings.get("top_k", 64)
        self.repetition_penalty = settings.get("repetition_penalty", 1.1)
        self.frequency_penalty = settings.get("frequency_penalty", 0.5)
        self.presence_penalty = settings.get("presence_penalty", 0.5)
        self.max_tokens = settings.get("max_tokens", 4096)
        self.min_p = settings.get("min_p", 0.05)

        self.parallel_workers = settings.get("parallel_workers", 3)
        self.chunk_size = settings.get("chunk_size", "auto")
        self.auto_min_total_lines = settings.get("auto_min_total_lines", 30)
        self.auto_min_lines_per_worker = settings.get("auto_min_lines_per_worker", 15)
        self.max_retries = settings.get("max_retries", 3)
        self.append_all_characters = settings.get("append_all_characters", False)
        self.jamdict_sanity_check = settings.get("jamdict_sanity_check", False)
        self.preserve_honorifics = settings.get("preserve_honorifics", True)
        self.prompt_cache_enabled = settings.get("prompt_cache_enabled", True)
        self.do_horses_exist = settings.get("do_horses_exist", True)

        if self.jamdict_sanity_check and _JAMDICT_AVAILABLE:
            try:
                self._jamdict = threading.local()
                self._jamdict.instance = Jamdict()
                from config import setup_logging
                logger, _ = setup_logging()
                logger.info("Jamdict loaded for semantic sanity checking")
            except Exception as e:
                from config import setup_logging
                logger, _ = setup_logging()
                logger.warning("Jamdict enabled but failed to load: %s — sanity check disabled", e)

        substitutions = {
            "input_language": self.input_language,
            "output_language": self.output_language,
        }
        self.base_instruction = settings["system_prompt"].format(**substitutions)

        self.character_memory = load_character_memory()

        self._supported_params, self._supports_json_schema = self._probe_capabilities()
        from config import setup_logging
        logger, _ = setup_logging()
        if self.api_server:
            logger.info("API capabilities — structured output: %s, params: %s",
                        self._supports_json_schema, self._supported_params)

    def _probe_capabilities(self):
        from config import setup_logging
        logger, _ = setup_logging()
        default_params = []
        default_schema = False

        if not self.api_server:
            logger.info("No custom api_server — using litellm defaults")
            return default_params, default_schema

        logger.info("Checking API capabilities... (max 15s, cached for future launches)")

        cached = load_capabilities_cache(self.api_server, self.model_name)
        if cached and isinstance(cached, dict):
            logger.info("Using cached API capabilities for %s", self.model_name)
            return cached.get("supported_params", []), cached.get("structured_output", False)

        supported_params = []
        try:
            supported_params = litellm.get_supported_openai_params(
                model=self.model_name, custom_llm_provider="openai"
            )
        except Exception as e:
            logger.warning("Could not query supported params: %s", e)

        supports_schema = False
        test_props = {f"item_{i}": {"type": "string"} for i in range(10)}
        test_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "test",
                "schema": {
                    "type": "object",
                    "properties": test_props,
                    "required": list(test_props.keys()),
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
        try:
            response = litellm.completion(
                model=self.model_name,
                api_key=self.api_key,
                api_base=self.api_server,
                messages=[{"role": "user", "content": "Output 10 short words as item_0 through item_9."}],
                max_tokens=128,
                response_format=test_schema,
                timeout=15,
            )
            if response and hasattr(response, "choices") and len(response.choices) > 0:
                content = response.choices[0].message.content or ""
                import json
                parsed = json.loads(content)
                if isinstance(parsed, dict) and all(k in parsed for k in test_props):
                    supports_schema = True
                    if "response_format" not in supported_params:
                        supported_params.append("response_format")
                else:
                    logger.info("Structured output probe: schema accepted but response incomplete (%d/10 keys)", len([k for k in test_props if k in parsed]) if isinstance(parsed, dict) else 0)
        except Exception as e:
            error_msg = str(e).lower()
            if "structured" in error_msg or "json_schema" in error_msg or "response_format" in error_msg:
                logger.info("API does not support structured output: %s", e)
            else:
                logger.warning("Structured output probe failed: %s", e)

        if supports_schema or supported_params:
            import datetime
            capabilities = {
                "structured_output": supports_schema,
                "supported_params": supported_params,
                "probed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            save_capabilities_cache(capabilities, self.api_server, self.model_name)

        return supported_params, supports_schema

    def execute(self, messages, response_format=None):
        api_params = {"model": self.model_name, "messages": messages}
        if "reasoning_effort" in self._supported_params:
            api_params["reasoning_effort"] = None
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

        if self.api_server:
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

    def translate(self, input_text):
        if self.stop_translation:
            return "Paused"

        if input_text == "test" or input_text == ["test"]:
            return input_text if isinstance(input_text, list) else "test"

        if isinstance(input_text, list) and self.parallel_workers > 1:
            return process_parallel_chunked(input_text, self._to_dict())

        if isinstance(input_text, list):
            return process_batch_llm(input_text, self._to_dict())

        cleaned, result = process_single_line({**self._to_dict(), 'raw_input': input_text})
        return cleaned

    def _to_dict(self):
        return {
            'input_language': self.input_language,
            'output_language': self.output_language,
            'model_name': self.model_name,
            'api_key': self.api_key,
            'api_server': self.api_server,
            'context_lines': self.context_lines,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'top_k': self.top_k,
            'repetition_penalty': self.repetition_penalty,
            'frequency_penalty': self.frequency_penalty,
            'presence_penalty': self.presence_penalty,
            'max_tokens': self.max_tokens,
            'min_p': self.min_p,
            'parallel_workers': self.parallel_workers,
            'chunk_size': self.chunk_size,
            'auto_min_total_lines': self.auto_min_total_lines,
            'auto_min_lines_per_worker': self.auto_min_lines_per_worker,
            'max_retries': self.max_retries,
            'append_all_characters': self.append_all_characters,
            'jamdict_sanity_check': self.jamdict_sanity_check,
            'preserve_honorifics': self.preserve_honorifics,
            'prompt_cache_enabled': self.prompt_cache_enabled,
            'do_horses_exist': self.do_horses_exist,
            'lock': self._lock,
            'jamdict': self._jamdict,
            'base_instruction': self.base_instruction,
            'character_memory': self.character_memory,
            'supports_json_schema': self._supports_json_schema,
            'execute': self.execute,
        }

    def pause(self):
        self.stop_translation = True

    def resume(self):
        self.stop_translation = False

    def activate(self):
        self.translator_ready_or_not = True
        return True
