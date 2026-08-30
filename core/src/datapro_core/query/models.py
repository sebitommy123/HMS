"""Q1 query engine data model.

Intentionally small — most of the eventual "Phase 2 contract" complexity
(per-field state, identity, traits, link traversal) is NOT here yet. The
shape below is just enough to run a single ``{from, limit}`` query against
N factories via one Trino UNION ALL CORRESPONDING statement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Query:
    """Parsed canonical-JSON query."""

    from_type: str
    """Display name of the ObjectType to read."""

    limit: int
    """Per-factory row cap. Each branch in the UNION applies this LIMIT, so
    the total rows returned can be up to ``limit * num_factories``."""

    timeout_seconds: int


@dataclass(frozen=True)
class FactoryPlan:
    """One branch of the eventual SQL output (UNION or JOIN, depending
    on which traits the object type has). Captured at plan time so the
    executor + assembler can describe what each branch contributed."""

    factory_id: str
    data_source_id: str
    data_source_path: str  # "catalog.schema.table"
    use_all_columns: bool
    column_spec: list[str]
    # Discovered at plan time via Trino DESCRIBE-via-LIMIT-0. Each entry
    # is ``(column_name, trino_type)`` — the SQL builder uses both:
    # names to project, types to CAST a NULL placeholder for columns
    # this factory doesn't have but others do. Excludes the synthesised
    # ``_datasource`` column.
    output_columns: list[tuple[str, str]] = field(default_factory=list)
    # Per-trait config for this factory, as persisted on the row.
    # Shape: ``{trait_name: {trait-specific keys}}``. The SQL builder
    # reads this for traits that affect SQL (e.g. Identity needs the
    # ``column`` key to know which column to join on).
    trait_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SkippedFactory:
    """A factory the planner dropped, with the human-readable reason."""

    factory_id: str
    data_source_path: str
    reason: str


@dataclass(frozen=True)
class QueryPlan:
    """A fully-built plan ready for execution or preview."""

    query: Query
    object_type_id: str
    object_type_name: str
    factories: list[FactoryPlan]
    # Names of traits enabled on the object type, in stable order.
    # The SQL builder branches on this — e.g. ``identity`` causes
    # FULL OUTER JOIN instead of UNION ALL.
    object_type_traits: list[str] = field(default_factory=list)
    skipped: list[SkippedFactory] = field(default_factory=list)
    sql: str = ""
    """The literal Trino SQL the executor will send. Set by the planner;
    surfaced verbatim in /preview-query-plan and (per design) in every
    /query response's result_status."""


@dataclass(frozen=True)
class ResultStatus:
    all_ok: bool
    factories_used: list[dict[str, Any]]
    factories_skipped: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    sql: str
    trino_query_id: str | None
    elapsed_seconds: float


@dataclass(frozen=True)
class QueryResult:
    """The shape /query returns. ``objects`` is the interpreted layer — one HMS
    object per row (see ``query.objects.assemble_objects``), invertible 1:1 with
    the row — and is what clients should read. The raw ``columns``/``rows`` (which
    mirror Trino's response) are relegated into ``result_status`` for debugging,
    alongside the rest of the run metadata."""

    columns: list[str]
    rows: list[list[Any]]
    objects: list[dict[str, Any]]
    result_status: ResultStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "objects": self.objects,
            "result_status": {
                "all_ok": self.result_status.all_ok,
                "columns": self.columns,
                "rows": self.rows,
                "factories_used": self.result_status.factories_used,
                "factories_skipped": self.result_status.factories_skipped,
                "errors": self.result_status.errors,
                "sql": self.result_status.sql,
                "trino_query_id": self.result_status.trino_query_id,
                "elapsed_seconds": self.result_status.elapsed_seconds,
            },
        }
