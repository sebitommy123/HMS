"""Run a built QueryPlan through Trino, package the response.

Keeps the response shape minimal — columns + rows + a result_status block —
because the underlying contract is "Core mostly gets out of Trino's way".
Heavy lifting stays in Trino; assembly stays cheap.
"""

import time
from typing import Any

from datapro_core.query.models import (
    QueryPlan,
    QueryResult,
    ResultStatus,
)
from datapro_core.trino_client import (
    QueryTimeoutError,
    TrinoClient,
    TrinoError,
)

# When the planner has at least one surviving factory we expect Trino to
# return at least the synthesized ``_datasource`` column. When all
# factories were skipped we don't even call Trino — just hand back an
# empty table with this single-column header so the response shape stays
# uniform.
_EMPTY_COLUMNS: list[str] = ["_datasource"]
# Hard cap on the LIMIT we forward per branch. Independently of the
# per-query timeout, this is a defense against "0 factories survive but
# we somehow ran" pathology.
_MAX_ROW_RETURN = 100_000


def execute(plan: QueryPlan, trino: TrinoClient) -> QueryResult:
    """Execute the planned SQL, capture errors, return a QueryResult.
    Never raises for query-level failures — the result_status block
    surfaces them. Only raises for true exceptional cases (e.g. the
    function being called with a malformed plan, which shouldn't happen
    in practice)."""

    factories_used = [
        {
            "factory_id": f.factory_id,
            "data_source_path": f.data_source_path,
        }
        for f in plan.factories
    ]
    factories_skipped = [
        {
            "factory_id": s.factory_id,
            "data_source_path": s.data_source_path,
            "reason": s.reason,
        }
        for s in plan.skipped
    ]

    # Nothing to run if every factory was dropped pre-flight. Return an
    # empty table; still report all the skip reasons so the user knows.
    if not plan.factories or not plan.sql:
        return QueryResult(
            columns=_EMPTY_COLUMNS,
            rows=[],
            result_status=ResultStatus(
                all_ok=len(plan.skipped) == 0,
                factories_used=factories_used,
                factories_skipped=factories_skipped,
                errors=[],
                sql=plan.sql,
                trino_query_id=None,
                elapsed_seconds=0.0,
            ),
        )

    started = time.monotonic()
    errors: list[dict[str, Any]] = []
    try:
        result = trino.execute_query(
            plan.sql,
            timeout_seconds=plan.query.timeout_seconds,
            max_rows=_MAX_ROW_RETURN,
        )
        return QueryResult(
            columns=result.columns,
            rows=result.rows,
            result_status=ResultStatus(
                all_ok=len(plan.skipped) == 0,
                factories_used=factories_used,
                factories_skipped=factories_skipped,
                errors=errors,
                sql=plan.sql,
                trino_query_id=result.query_id,
                elapsed_seconds=result.elapsed_seconds,
            ),
        )
    except QueryTimeoutError as exc:
        errors.append({"kind": "timeout", "message": str(exc)})
    except TrinoError as exc:
        errors.append({"kind": "trino_error", "message": str(exc)})

    elapsed = time.monotonic() - started
    return QueryResult(
        columns=_EMPTY_COLUMNS,
        rows=[],
        result_status=ResultStatus(
            all_ok=False,
            factories_used=factories_used,
            factories_skipped=factories_skipped,
            errors=errors,
            sql=plan.sql,
            trino_query_id=None,
            elapsed_seconds=elapsed,
        ),
    )
