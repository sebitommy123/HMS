"""POST /query — the semantic Core query endpoint.

Returns a thin tabular response: columns + rows + a result_status block.
See datapro_core.query.service for the pipeline (parse → plan → execute).

Sister endpoint POST /preview-query-plan returns the SQL that would run
without executing it.
"""

from flask import Blueprint, current_app, jsonify, request

from datapro_core.query.service import (
    ParseError,
    PlanError,
    plan_only,
    run_query,
)

bp = Blueprint("query", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


@bp.post("/query")
def execute_query():
    raw = _read_json()
    if isinstance(raw, tuple):
        return raw  # already an error response

    with _session() as session:
        try:
            result = run_query(raw, session=session, trino=_trino())
        except ParseError as exc:
            return jsonify({"error": exc.error, "details": exc.details}), 400
        except PlanError as exc:
            return jsonify({"error": exc.error, "details": exc.details}), 404

    return jsonify(result.to_dict())


@bp.post("/preview-query-plan")
def preview_query_plan():
    raw = _read_json()
    if isinstance(raw, tuple):
        return raw

    with _session() as session:
        try:
            plan = plan_only(raw, session=session, trino=_trino())
        except ParseError as exc:
            return jsonify({"error": exc.error, "details": exc.details}), 400
        except PlanError as exc:
            return jsonify({"error": exc.error, "details": exc.details}), 404

    return jsonify(
        {
            "from": plan.query.from_type,
            "object_type_id": plan.object_type_id,
            "limit": plan.query.limit,
            "timeout_seconds": plan.query.timeout_seconds,
            "sql": plan.sql,
            "factories_used": [
                {
                    "factory_id": f.factory_id,
                    "data_source_id": f.data_source_id,
                    "data_source_path": f.data_source_path,
                    "use_all_columns": f.use_all_columns,
                    "column_spec": f.column_spec,
                }
                for f in plan.factories
            ],
            "factories_skipped": [
                {
                    "factory_id": s.factory_id,
                    "data_source_path": s.data_source_path,
                    "reason": s.reason,
                }
                for s in plan.skipped
            ],
        }
    )


def _read_json():
    """Read + parse the request body. Returns the parsed dict on success
    or a (response, status) tuple on failure."""
    try:
        return request.get_json(force=True)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400
