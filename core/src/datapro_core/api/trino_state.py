from flask import Blueprint, current_app, jsonify

from datapro_core.trino_client import TrinoError


bp = Blueprint("trino_state", __name__)


@bp.get("/trino/state")
def trino_state():
    trino = current_app.extensions["trino"]
    try:
        snapshots = trino.list_catalogs()
    except TrinoError as exc:
        return jsonify({"error": "trino_unreachable", "details": str(exc)}), 502
    return jsonify(
        [{"name": s.name, "connector": s.connector} for s in snapshots]
    )
