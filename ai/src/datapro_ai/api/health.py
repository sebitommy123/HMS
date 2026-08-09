import requests
from flask import Blueprint, current_app, jsonify

from datapro_ai.config import Config
from datapro_ai.db import ping as db_ping

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    cfg: Config = current_app.config["DATAPRO_AI"]
    engine = current_app.extensions["db_engine"]

    postgres_ok = db_ping(engine)
    core_ok = _core_reachable(cfg.core_url)
    anthropic_configured = bool(cfg.anthropic_api_key)

    status = {
        "postgres": "reachable" if postgres_ok else "unreachable",
        "core": "reachable" if core_ok else "unreachable",
        "anthropic": "configured" if anthropic_configured else "missing",
    }

    overall_ok = postgres_ok and core_ok and anthropic_configured
    overall = "ok" if overall_ok else "degraded"
    return jsonify({"status": overall, **status}), (200 if overall_ok else 503)


def _core_reachable(core_url: str) -> bool:
    try:
        r = requests.get(f"{core_url}/health", timeout=2)
        return r.ok
    except requests.RequestException:
        return False
