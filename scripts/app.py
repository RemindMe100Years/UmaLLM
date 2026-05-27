import json
import time

from flask import Flask, request
from flask_cors import CORS, cross_origin


def create_app(translator, host, port):
    app = Flask(__name__)

    cors = CORS(app)
    app.config['CORS_HEADERS'] = 'Content-Type'

    from config import setup_logging
    logger, _ = setup_logging()

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
                    logger.tl("RAW %d: %s", i + 1, raw)
                    logger.tl("TRN %d: %s", i + 1, trn)
            for h in logger.handlers:
                h.flush()
            logger.info("Translation completed in %.2fs", end - start)
            return json.dumps(translation, ensure_ascii=False)

        if message == "translate batch":
            logger.info("Batch translation request received (%d lines)", len(content) if isinstance(content, list) else 1)
            translation = translator.translate(content)
            if isinstance(translation, list):
                for i, (raw, trn) in enumerate(zip(content, translation)):
                    logger.tl("RAW %d: %s", i + 1, raw)
                    logger.tl("TRN %d: %s", i + 1, trn)
            return json.dumps(translation, ensure_ascii=False)

        if message == "pause":
            return json.dumps(translator.pause())

        if message == "resume":
            return json.dumps(translator.resume())

    return app, host, port
