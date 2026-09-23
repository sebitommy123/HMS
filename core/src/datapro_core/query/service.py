"""Facade for the query engine: parse → plan → execute.

Endpoints call into here so they don't have to know about the internal
module split. Keep it skinny — it really is just glue.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from datapro_core.query.executor import execute
from datapro_core.query.models import QueryPlan, QueryResult
from datapro_core.query.parser import ParseError, parse_query
from datapro_core.query.planner import PlanError, build_plan
from datapro_core.trino_client import TrinoClient, TrinoError


def plan_only(
    raw: object,
    *,
    session: Session,
    trino: TrinoClient,
    env: uuid.UUID | None = None,
) -> QueryPlan:
    """Parse + plan + return without executing. Powers /preview-query-plan.

    Live-catalog set comes from Trino so the plan reflects what would
    actually run *right now* (vs. relying on a cached snapshot). ``env`` scopes
    resolution to a staging overlay."""
    query = parse_query(raw)
    live_catalogs = _safe_list_catalogs(trino)
    return build_plan(
        query, session=session, trino=trino, live_catalogs=live_catalogs, env=env
    )


def run_query(
    raw: object,
    *,
    session: Session,
    trino: TrinoClient,
    env: uuid.UUID | None = None,
) -> QueryResult:
    """Parse + plan + execute, return the final QueryResult."""
    plan = plan_only(raw, session=session, trino=trino, env=env)
    return execute(plan, trino)


def _safe_list_catalogs(trino: TrinoClient) -> set[str]:
    """Return the names of catalogs Trino currently advertises. On error
    (Trino unreachable), return the empty set — every factory then gets
    skipped, the query short-circuits to empty rows, and the user sees
    the error in factories_skipped reasons."""
    try:
        snapshots = trino.list_catalogs()
    except TrinoError:
        return set()
    return {s.name for s in snapshots}


# Re-export the error types so the endpoint can catch them by short name.
__all__ = [
    "plan_only",
    "run_query",
    "ParseError",
    "PlanError",
    "_to_response",
]


def _to_response(result: QueryResult) -> dict[str, Any]:
    return result.to_dict()
