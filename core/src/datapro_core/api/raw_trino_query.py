"""Raw-Trino-SQL passthrough endpoint, for debugging only.

This is the "type some SQL, see what Trino returns" surface. It is NOT the
semantic Core query layer — /query is being reserved for that. Use this
endpoint when you want to bypass any semantic layer and talk directly to
Trino: catalog drift investigations, ad-hoc joins, schema introspection,
that sort of thing.

Wall-clock and row-count limits are enforced server-side because the browser
can crash trying to render hundreds of MB of rows.

Not safety-critical: anyone with the API can run arbitrary SQL against Trino
(including DROP CATALOG, which the reconciler would then promptly recreate
from Postgres). We trust the operator.
"""

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, Field, ValidationError

from datapro_core.trino_client import (
    QueryTimeoutError,
    TrinoError,
)


bp = Blueprint("raw_trino_query", __name__)


# Hard upper bounds even if the caller asks for more. Tighter than the trino
# client's bounds so the API surface is predictable.
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_ROWS_CAP = 100_000
DEFAULT_MAX_ROWS = 10_000


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    max_rows: int = Field(default=DEFAULT_MAX_ROWS, gt=0)


@bp.post("/raw-trino-query")
def execute_raw_trino_query():
    try:
        payload = QueryRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return (
            jsonify(
                {
                    "error": "invalid_request",
                    "details": [
                        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
                    ],
                }
            ),
            400,
        )
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    timeout = min(payload.timeout_seconds, MAX_TIMEOUT_SECONDS)
    max_rows = min(payload.max_rows, MAX_ROWS_CAP)

    trino = current_app.extensions["trino"]
    try:
        result = trino.execute_query(
            payload.sql, timeout_seconds=timeout, max_rows=max_rows
        )
    except QueryTimeoutError as exc:
        return (
            jsonify(
                {
                    "error": "timeout",
                    "details": str(exc),
                    "timeout_seconds": timeout,
                }
            ),
            504,
        )
    except TrinoError as exc:
        return (
            jsonify(
                {
                    "error": "trino_error",
                    "details": str(exc),
                }
            ),
            400,
        )

    return jsonify(
        {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "elapsed_seconds": result.elapsed_seconds,
            "query_id": result.query_id,
            "applied_limits": {
                "timeout_seconds": timeout,
                "max_rows": max_rows,
            },
        }
    )
