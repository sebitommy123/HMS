"""Pure unit tests for the identity-join SQL shape.

The one that matters most here: the identity-key CTE must bound its keys
*deterministically* (ORDER BY before LIMIT). Every join branch filters on that
CTE, and Trino may re-evaluate it per reference — a non-deterministic key set
makes different branches pick different keys and the FULL OUTER JOIN fragments
(the multi-source merge silently loses sources, differently on every run). This
test guards that regression without needing Trino."""

import re

from datapro_core.query.models import FactoryPlan, Query, QueryPlan
from datapro_core.query.sql_builder import build_sql


def _identity_plan(paths: list[str], limit: int = 25) -> QueryPlan:
    factories = [
        FactoryPlan(
            factory_id=f"f{i}",
            data_source_id=f"d{i}",
            data_source_path=p,
            use_all_columns=False,
            column_spec=["optiver_id", "feedcode"],
            output_columns=[("optiver_id", "varchar"), ("feedcode", "varchar")],
            trait_config={"identity": {"column": "optiver_id"}},
        )
        for i, p in enumerate(paths)
    ]
    return QueryPlan(
        query=Query(from_type="Instrument", limit=limit, timeout_seconds=10),
        object_type_id="t",
        object_type_name="Instrument",
        factories=factories,
        object_type_traits=["identity"],
        sql="",
    )


def test_identity_key_cte_is_deterministic():
    """The keys CTE takes a deterministic global top-N (ORDER BY _identity before
    LIMIT), so the key set is stable and the merge can't fragment."""
    sql = build_sql(_identity_plan(["cat.sch.a", "cat.sch.b", "cat.sch.c"], limit=1))
    assert "WITH _identity_keys AS" in sql
    # The CTE's global selection orders before limiting (deterministic top-N).
    assert "ORDER BY _identity\n  LIMIT 1" in sql


def test_identity_key_cte_bounds_each_source_top_n():
    """Each source contributes only its OWN top-N (ORDER BY <id> LIMIT N pushed
    down), so the keys CTE never scans a full identity column. Global top-N of
    the union of per-source top-N is exact."""
    sql = build_sql(_identity_plan(["cat.sch.a", "cat.sch.b", "cat.sch.c"], limit=5))
    # One bounded per-source top-N per factory.
    assert sql.count('ORDER BY "optiver_id" LIMIT 5') == 3


def test_identity_uses_single_key_spine_with_left_joins():
    """The key set is referenced ONCE as a spine (not re-inlined per branch,
    which exploded the plan past Trino's stage limit at many sources); each
    branch LEFT JOINs onto it. No per-branch IN-filter, no FULL OUTER JOIN."""
    sql = build_sql(_identity_plan(["cat.sch.a", "cat.sch.b", "cat.sch.c"]))
    # Spine referenced exactly once (as the driving table).
    assert sql.count("_identity_keys AS _keys") == 1
    assert "IN (SELECT _identity FROM _identity_keys)" not in sql
    assert "FULL OUTER JOIN" not in sql
    # One LEFT JOIN per branch, each keyed onto the spine's identity.
    assert sql.count("LEFT JOIN") == 3
    assert sql.count("._identity = _keys._identity") == 3


def test_single_identity_factory_needs_no_key_cte():
    """A lone identity factory has no join to fragment, so it keeps the simple
    shape — no keys CTE, just the outer LIMIT the connector can push down."""
    sql = build_sql(_identity_plan(["cat.sch.a"]))
    assert "_identity_keys" not in sql
    assert "FULL OUTER JOIN" not in sql
    assert re.search(r"LIMIT \d+\s*$", sql)
