"""Build the Trino SQL string for a planned query.

Two strategies, selected by the object type's traits:

  * **Default (no Identity)** — ``UNION ALL CORRESPONDING`` of per-factory
    branches, with each branch projecting all union columns and
    NULL-filling missing ones. Each row keeps its own identity (no
    cross-source merge); the synthesised ``_datasource`` column tells
    you which factory it came from.

  * **Identity** — ``FULL OUTER JOIN ... USING (_identity)`` across
    branches. Each branch aliases its identity column to ``_identity``;
    the join is the merge point. Non-identity columns are prefixed
    with ``__<data_source_path>`` so values from different factories
    don't collide. The ``_datasource`` column is also prefixed
    (``_datasource__<path>``) and serves as a "this factory contributed
    this row" indicator after the outer join.

The trait class doesn't own SQL generation directly — keeping that
here lets the planner reason about trait *combinations* (e.g. future
Identity + Versioned) in one place instead of composing tiny hooks.
"""

from datapro_core.query.models import FactoryPlan, QueryPlan


def build_sql(plan: QueryPlan) -> str:
    """Render a single Trino SQL statement for this plan. Returns ""
    when there are no surviving factories (the executor short-circuits
    on empty SQL)."""
    if not plan.factories:
        return ""
    if "identity" in plan.object_type_traits:
        return _build_identity_join(plan)
    return _build_union(plan)


def plan_branch_inner_sql(
    *,
    data_source_path: str,
    use_all_columns: bool,
    column_spec: list[str],
) -> str:
    """The inner SELECT (no LIMIT, no parens) for one factory, used by
    callers that just want the un-wrapped projection (e.g. introspection
    via DESCRIBE-via-LIMIT-0)."""
    select_list = _user_select_list(use_all_columns, column_spec)
    datasource_literal = _sql_string_literal(data_source_path)
    qualified_table = _quote_path(data_source_path)
    return (
        f"SELECT {datasource_literal} AS _datasource, {select_list} "
        f"FROM {qualified_table}"
    )


# ---- UNION strategy (default, no Identity) ----------------------------


def _build_union(plan: QueryPlan) -> str:
    union_columns = _union_columns(plan.factories)
    branches = [
        _union_branch(f, plan.query.limit, union_columns) for f in plan.factories
    ]
    return "\nUNION ALL CORRESPONDING\n".join(branches)


def _union_columns(factories: list[FactoryPlan]) -> list[tuple[str, str]]:
    """Union of (column_name, type) across factories, preserving the
    order they first appear. ``_datasource`` is always first."""
    seen: dict[str, str] = {"_datasource": "varchar"}
    for f in factories:
        for col, ty in f.output_columns:
            seen.setdefault(col, ty)
    return list(seen.items())


def _union_branch(
    factory: FactoryPlan,
    limit: int,
    union_columns: list[tuple[str, str]],
) -> str:
    """Emit one branch of the UNION. Every branch SELECTs all union
    columns in the same order; columns missing from this factory are
    NULL-filled with a cast to the union's type for that column."""
    factory_cols = {name for (name, _ty) in factory.output_columns}
    datasource_literal = _sql_string_literal(factory.data_source_path)
    qualified_table = _quote_path(factory.data_source_path)
    user_select = _user_select_list(factory.use_all_columns, factory.column_spec)

    inner = (
        f"SELECT {datasource_literal} AS _datasource, {user_select} "
        f"FROM {qualified_table}"
    )

    outer_projection: list[str] = []
    for col, ty in union_columns:
        if col == "_datasource":
            outer_projection.append('"_datasource"')
            continue
        if col in factory_cols:
            outer_projection.append(f'"{col}"')
        else:
            outer_projection.append(f'CAST(NULL AS {ty}) AS "{col}"')

    projection = ", ".join(outer_projection)
    return (
        f"(SELECT {projection}\n"
        f"  FROM ({inner}) AS _branch\n"
        f"  LIMIT {int(limit)})"
    )


# ---- Identity-JOIN strategy --------------------------------------------


def _build_identity_join(plan: QueryPlan) -> str:
    """Emit a FULL OUTER JOIN chain across factories, joining on the
    identity column each factory designates in ``trait_config.identity.column``.

    Per the prefix-with-source conflict policy, every non-identity
    column from each branch is renamed to ``<col>__<data_source_path>``
    so cross-factory same-named columns don't collide on the way out.
    """
    branches: list[tuple[FactoryPlan, str]] = []
    for f in plan.factories:
        identity_col = _identity_column(f)
        inner_select = _identity_branch_inner(f, identity_col)
        branches.append((f, inner_select))

    # First branch as base; subsequent branches FULL OUTER JOIN USING
    # (_identity). USING collapses the join keys into a single column,
    # so the join chain composes cleanly for N factories.
    first_factory, first_inner = branches[0]
    sql = f"({first_inner}) AS {_alias_for(first_factory, 0)}"
    for i, (factory, inner) in enumerate(branches[1:], start=1):
        sql += (
            f"\n  FULL OUTER JOIN ({inner}) AS {_alias_for(factory, i)} "
            f"USING (_identity)"
        )

    return (
        f"SELECT *\n  FROM {sql}\n  LIMIT {int(plan.query.limit)}"
    )


def _identity_column(factory: FactoryPlan) -> str:
    """Pull the identity column out of trait_config. The factory
    validator guarantees this is set + valid before the planner builds
    SQL, so a missing key here is a planner bug."""
    cfg = (factory.trait_config or {}).get("identity") or {}
    column = cfg.get("column")
    if not isinstance(column, str) or not column:
        raise ValueError(
            f"factory {factory.factory_id} is missing identity.column "
            "in trait_config (should have been caught by factory_validator)"
        )
    return column


def _identity_branch_inner(factory: FactoryPlan, identity_col: str) -> str:
    """Per-branch SELECT for Identity-join strategy. Aliases:
      - the identity column → ``_identity`` (join key)
      - the ``_datasource`` literal → ``_datasource__<path>`` (presence
        indicator after outer join)
      - every other column → ``<col>__<path>`` (no cross-factory collisions)
    """
    datasource_literal = _sql_string_literal(factory.data_source_path)
    qualified_table = _quote_path(factory.data_source_path)
    suffix = _path_suffix(factory.data_source_path)

    # Other columns = the factory's output_columns minus the identity
    # column. Use output_columns (not column_spec) so use_all_columns=true
    # factories work too.
    other_cols = [
        name for (name, _ty) in factory.output_columns if name != identity_col
    ]

    # Build the SELECT list inside the branch. Project from the
    # user_select scope so column_spec expressions stay intact.
    projections = [
        f'"{identity_col}" AS _identity',
        f'{datasource_literal} AS "_datasource{suffix}"',
    ]
    for col in other_cols:
        projections.append(f'"{col}" AS "{col}{suffix}"')

    user_select = _user_select_list(factory.use_all_columns, factory.column_spec)
    inner = f"SELECT {user_select} FROM {qualified_table}"
    return (
        f"SELECT {', '.join(projections)} "
        f"FROM ({inner}) AS _branch"
    )


def _alias_for(factory: FactoryPlan, index: int) -> str:
    """Per-branch table alias inside the join. Index keeps aliases
    unique even if two factories somehow share a path (shouldn't
    happen — uniqueness is enforced upstream — but defense in depth)."""
    return f"_b{index}"


def _path_suffix(data_source_path: str) -> str:
    """Suffix appended to columns from a particular branch under the
    prefix-with-source policy: ``__<catalog.schema.table>``. Trino
    handles dots inside quoted identifiers, so the suffix stays
    readable instead of being mangled to underscores."""
    return f"__{data_source_path}"


# ---- Shared helpers ---------------------------------------------------


def _user_select_list(use_all_columns: bool, column_spec: list[str]) -> str:
    """The user-visible SELECT items (excluding _datasource). For
    ``use_all_columns`` mode, that's ``*``; otherwise the column_spec
    entries joined verbatim."""
    if use_all_columns or not column_spec:
        return "*"
    return ", ".join(column_spec)


def _sql_string_literal(value: str) -> str:
    """Single-quote, doubling embedded quotes. Used for the synthesised
    ``_datasource`` literal — controlled input, but escape anyway."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _quote_path(path: str) -> str:
    """Quote a ``catalog.schema.table`` path part-by-part with Trino's
    double-quote identifier syntax. Doubles any embedded double-quote."""
    return ".".join(f'"{p.replace(chr(34), chr(34) * 2)}"' for p in path.split("."))
