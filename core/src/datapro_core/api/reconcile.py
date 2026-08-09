from flask import Blueprint, current_app, jsonify

from datapro_core.reconciler import reconcile


bp = Blueprint("reconcile", __name__)


@bp.post("/reconcile")
def trigger_reconcile():
    session = current_app.extensions["db_session"]()
    trino = current_app.extensions["trino"]
    with session:
        result = reconcile(session, trino)
        return jsonify(
            {
                "all_ok": result.all_ok,
                "actions": [
                    {
                        "kind": r.action.kind,
                        "name": r.action.name,
                        "ok": r.ok,
                        "error": r.error,
                    }
                    for r in result.actions
                ],
            }
        ), 200 if result.all_ok else 502
