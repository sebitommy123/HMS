import anthropic
from flask import Flask
from flask_cors import CORS

from datapro_ai.api import conversations as conversations_api
from datapro_ai.api import health as health_api
from datapro_ai.api import messages as messages_api
from datapro_ai.config import Config
from datapro_ai.db import make_engine, make_session_factory
from datapro_ai.turn_registry import TurnRegistry


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()
    app = Flask(__name__)
    app.config["DATAPRO_AI"] = cfg

    CORS(app, origins=list(cfg.cors_origins))

    engine = make_engine(cfg.database_url)
    app.extensions["db_engine"] = engine
    app.extensions["db_session"] = make_session_factory(engine)

    # The Anthropic client is constructed once and reused. If the key is
    # missing we still construct a client (it raises on first request); the
    # /health endpoint surfaces missing config without crashing the app.
    app.extensions["anthropic_client"] = anthropic.Anthropic(
        api_key=cfg.anthropic_api_key or None,
    )

    # Process-wide registry of in-flight agent turns. One per conversation.
    # Lives for the lifetime of the process; single-instance by design.
    app.extensions["turn_registry"] = TurnRegistry()

    app.register_blueprint(health_api.bp)
    app.register_blueprint(conversations_api.bp)
    app.register_blueprint(messages_api.bp)

    return app


app = create_app()
