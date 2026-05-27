import os
import signal
import sys

from config import setup_logging, load_settings
from translator import Main_Translator
from app import create_app


def _force_shutdown(signum=None, frame=None):
    logger.info("Signal received, forcing exit...")
    os._exit(0)


signal.signal(signal.SIGINT, _force_shutdown)
signal.signal(signal.SIGTERM, _force_shutdown)
try:
    signal.signal(signal.SIGHUP, _force_shutdown)
except AttributeError:
    pass


logger, settings = setup_logging()
port = settings["HTTP_port_number"]
host = "0.0.0.0"

translator = Main_Translator()
translator.activate()

logger.info("Model: %s", translator.model_name)
logger.info("API Server: %s", translator.api_server)
logger.info("Parallel workers: %d | Chunk size: %s | Max retries: %d", translator.parallel_workers, translator.chunk_size, translator.max_retries)
logger.info("Temperature: %.2f | Top P: %.2f | Repetition penalty: %.2f", translator.temperature, translator.top_p, translator.repetition_penalty)
logger.info("Structured output supported: %s", translator._supports_json_schema)

app, host, port = create_app(translator, host, port)

logger.info("Starting Translation API Server on %s:%d", host, port)
logger.info("Server is ready")

from waitress import serve
serve(app, host=host, port=port)
