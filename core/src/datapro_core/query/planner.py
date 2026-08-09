"""Turn a parsed Query + DB session + Trino state into a fully-built QueryPlan.

Pre-flight: any factory whose catalog isn't currently registered with Trino
is dropped from the plan (and recorded in plan.skipped with a reason). The
SQL string is built last, so /preview-query-plan can show the user exactly
what would run.
"""

from sqlalchemy.orm import Session

from datapro_core.factory_validator import validate_object_factory
from datapro_core.models import ObjectFactory, ObjectType
from datapro_core.query.models import (
    FactoryPlan,
    Query,
    QueryPlan,
    SkippedFactory,
)
from datapro_core.query.sql_builder import build_sql
from datapro_core.trino_client import TrinoClient


class PlanError(Exception):
    """Raised by build_plan when the query can't be planned at all
    (object type doesn't exist). The endpoint turns this into a 404."""

    def __init__(self, error: str, details: str | None = None):
        self.error = error
        self.details = details
        super().__init__(error)


def build_plan(
    query: Query,
    *,
    session: Session,
    trino: TrinoClient,
    live_catalogs: set[str],
) -> QueryPlan:
    """Resolve the ObjectType, find its factories, drop unreachable ones,
    emit the SQL. ``live_catalogs`` is the set of catalog names Trino
    currently knows about (passed in so this function stays
    pure-of-network-IO and easy to test)."""
    object_type = (
        session.query(ObjectType)
        .where(ObjectType.name == query.from_type)
        .one_or_none()
    )
    if object_type is None:
        raise PlanError(
            "object_type_not_found",
            f"No object type named {query.from_type!r}.",
        )

    factory_rows = (
        session.query(ObjectFactory)
        .where(ObjectFactory.object_type_id == object_type.id)
        .order_by(ObjectFactory.created_at)
        .all()
    )

    factories: list[FactoryPlan] = []
    skipped: list[SkippedFactory] = []
    for row in factory_rows:
        ds = row.data_source
        path = (
            f"{ds.catalog_name}.{ds.schema_name}.{ds.table_name}"
            if ds is not None
            else "(unknown)"
        )

        # Single source of truth for "is this usable?" — also persists
        # status / last_error on the factory row so the UI's heartbeat
        # view stays current without anyone running a query.
        result = validate_object_factory(
            row, session=session, trino=trino, live_catalogs=live_catalogs
        )
        if not result.valid:
            skipped.append(
                SkippedFactory(
                    factory_id=str(row.id),
                    data_source_path=path,
                    reason=result.reason or "unknown validation failure",
                )
            )
            continue

        # Validator already introspected the table — reuse its columns
        # rather than re-paying for a DESCRIBE round trip. When
        # use_all_columns is true the branch projects everything; when
        # false it projects only the column_spec subset.
        if row.use_all_columns:
            output_columns = list(result.columns)
        else:
            wanted = set(row.column_spec or [])
            output_columns = [(n, t) for (n, t) in result.columns if n in wanted]

        factories.append(
            FactoryPlan(
                factory_id=str(row.id),
                data_source_id=str(ds.id) if ds else "",
                data_source_path=path,
                use_all_columns=row.use_all_columns,
                column_spec=list(row.column_spec or []),
                output_columns=output_columns,
                trait_config=dict(row.trait_config or {}),
            )
        )

    # Persist any status / last_error changes the validator made before
    # the planner exits. (Heartbeat commits independently; planner needs
    # to commit here for query-time validation results to stick.)
    session.commit()

    # Build the SQL last so the preview endpoint and /query response both
    # see the same string. The SQL builder branches on trait names —
    # e.g. Identity rewrites UNION into FULL OUTER JOIN.
    object_type_traits = object_type.trait_names
    plan = QueryPlan(
        query=query,
        object_type_id=str(object_type.id),
        object_type_name=object_type.name,
        factories=factories,
        object_type_traits=object_type_traits,
        skipped=skipped,
        sql="",
    )
    sql = build_sql(plan)
    # QueryPlan is frozen; rebuild with the SQL filled in.
    return QueryPlan(
        query=plan.query,
        object_type_id=plan.object_type_id,
        object_type_name=plan.object_type_name,
        factories=plan.factories,
        object_type_traits=plan.object_type_traits,
        skipped=plan.skipped,
        sql=sql,
    )
