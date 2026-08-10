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

# Name of the CTE that bounds the identity key set before the join.
_KEYS_CTE = "_identity_keys"


def _build_identity_join(plan: QueryPlan) -> str:
    """Emit a FULL OUTER JOIN across factories, joining on the identity column
    each factory designates in ``trait_config.identity.column``.

    Per the prefix-with-source conflict policy, every non-identity column from
    each branch is renamed to ``<col>__<data_source_path>`` so cross-factory
    same-named columns don't collide on the way out.

    **Limit is bounded before the join.** ``LIMIT N`` on the outer SELECT means
    "at most N distinct identities". A FULL OUTER JOIN is a limit-blocking
    operator — Trino can't push the outer LIMIT below it, so a naive
    ``... JOIN ... LIMIT N`` scans *both entire tables* before the cap applies
    (the pathology the perf suite's identity scenario captures). Instead we
    first pick ≤N identity keys via a cheap single-column CTE (Trino pushes the
    LIMIT into each Postgres scan), then fetch only those keys' rows per branch
    (indexed / dynamic-filtered) and join *those*. Since the outer LIMIT is
    unordered, "any N identities" is semantically equivalent to join-then-limit,
    but the heavy full scan + full join is eliminated.

    Rows with a NULL identity are dropped: they can't participate in a merge
    (NULL never matches a join key), so they were never joinable objects.
    """
    branches_meta = [(f, _identity_column(f)) for f in plan.factories]
    limit = int(plan.query.limit)

    # Single factory: no join to block pushdown. The outer LIMIT pushes cleanly
    # through the one subquery to the table scan, so leave the simple shape.
    if len(branches_meta) == 1:
        factory, identity_col = branches_meta[0]
        inner = _identity_branch_inner(factory, identity_col)
        return (
            f"SELECT *\n  FROM ({inner}) AS {_alias_for(factory, 0)}\n"
            f"  LIMIT {limit}"
        )

    key_filter = f"IN (SELECT _identity FROM {_KEYS_CTE})"
    joined = _join_branches(branches_meta, key_filter=key_filter)
    return (
        f"WITH {_KEYS_CTE} AS (\n{_identity_keys_cte(branches_meta, limit)}\n)\n"
        f"SELECT *\n  FROM {joined}\n  LIMIT {limit}"
    )


def _identity_keys_cte(
    branches_meta: list[tuple[FactoryPlan, str]], limit: int
) -> str:
    """Bounded set of identity keys: ``UNION`` each factory's identity column
    (a single narrow column Trino can read with the LIMIT pushed into Postgres),
    then cap at ``limit``. UNION (not UNION ALL) de-dupes so the cap counts
    distinct identities."""
    selects = [
        f'    SELECT "{identity_col}" AS _identity FROM {_quote_path(f.data_source_path)}'
        for (f, identity_col) in branches_meta
    ]
    union = "\n    UNION\n".join(selects)
    return (
        f"  SELECT _identity FROM (\n{union}\n  ) AS _all_keys\n"
        f"  WHERE _identity IS NOT NULL\n"
        f"  LIMIT {limit}"
    )


def _join_branches(
    branches_meta: list[tuple[FactoryPlan, str]], *, key_filter: str | None
) -> str:
    """FULL OUTER JOIN the branches USING (_identity). USING collapses the join
    keys into a single column, so the chain composes cleanly for N factories.
    ``key_filter`` (e.g. ``IN (SELECT _identity FROM _identity_keys)``), when
    given, restricts each branch's scan to the bounded key set."""
    rendered = [
        (f, _identity_branch_inner(f, identity_col, key_filter=key_filter))
        for (f, identity_col) in branches_meta
    ]
    first_factory, first_inner = rendered[0]
    sql = f"({first_inner}) AS {_alias_for(first_factory, 0)}"
    for i, (factory, inner) in enumerate(rendered[1:], start=1):
        sql += (
            f"\n  FULL OUTER JOIN ({inner}) AS {_alias_for(factory, i)} "
            f"USING (_identity)"
        )
    return sql


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


def _identity_branch_inner(
    factory: FactoryPlan, identity_col: str, *, key_filter: str | None = None
) -> str:
    """Per-branch SELECT for Identity-join strategy. Aliases:
      - the identity column → ``_identity`` (join key)
      - the ``_datasource`` literal → ``_datasource__<path>`` (presence
        indicator after outer join)
      - every other column → ``<col>__<path>`` (no cross-factory collisions)

    ``key_filter`` (e.g. ``IN (SELECT _identity FROM _identity_keys)``), when
    given, is applied as ``WHERE "<identity_col>" <key_filter>`` on the raw
    table scan so the restriction pushes down (dynamic filtering) instead of
    scanning the whole table.
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

    where = f' WHERE "{identity_col}" {key_filter}' if key_filter else ""
    user_select = _user_select_list(factory.use_all_columns, factory.column_spec)
    inner = f"SELECT {user_select} FROM {qualified_table}{where}"
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
