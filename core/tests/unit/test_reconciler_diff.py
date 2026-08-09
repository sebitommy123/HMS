from datapro_core.models import Catalog, CatalogStatus
from datapro_core.reconciler import ActionKind, plan
from datapro_core.trino_client import TrinoCatalogSnapshot


def _cat(name, connector="postgresql", properties=None, status=CatalogStatus.ENABLED):
    return Catalog(
        name=name,
        connector=connector,
        properties=properties or {},
        status=status,
    )


def _snap(name, connector="postgresql"):
    return TrinoCatalogSnapshot(name=name, connector=connector)


def test_no_op_when_aligned():
    desired = [_cat("a"), _cat("b")]
    actions = plan(desired, [_snap("a"), _snap("b")])
    assert actions == []


def test_creates_missing_catalog():
    desired = [_cat("a", connector="tpch")]
    actions = plan(desired, [])
    assert len(actions) == 1
    assert actions[0].kind == ActionKind.CREATE
    assert actions[0].name == "a"
    assert actions[0].connector == "tpch"


def test_drops_extra_catalog():
    actions = plan([], [_snap("stale")])
    assert len(actions) == 1
    assert actions[0].kind == ActionKind.DROP
    assert actions[0].name == "stale"


def test_mixed_create_and_drop():
    desired = [_cat("keep"), _cat("new")]
    actual = [_snap("keep"), _snap("old")]
    actions = plan(desired, actual)
    kinds = {(a.kind, a.name) for a in actions}
    assert (ActionKind.CREATE, "new") in kinds
    assert (ActionKind.DROP, "old") in kinds
    assert (ActionKind.CREATE, "keep") not in kinds


def test_disabled_rows_not_created():
    desired = [_cat("paused", status=CatalogStatus.DISABLED)]
    actions = plan(desired, [])
    assert actions == []


def test_disabled_row_still_in_trino_gets_dropped():
    desired = [_cat("paused", status=CatalogStatus.DISABLED)]
    actions = plan(desired, [_snap("paused")])
    assert len(actions) == 1
    assert actions[0].kind == ActionKind.DROP


def test_broken_rows_not_recreated_until_fixed():
    desired = [_cat("oops", status=CatalogStatus.BROKEN)]
    actions = plan(desired, [])
    assert actions == []


def test_properties_passed_through():
    desired = [_cat("pg", connector="postgresql", properties={"connection-url": "jdbc:..."})]
    actions = plan(desired, [])
    assert actions[0].properties == {"connection-url": "jdbc:..."}


# -- connector-mismatch detection ----------------------------------------------


def test_connector_mismatch_emits_drop_then_create():
    """Same name but different connector → DROP then CREATE, in that order."""
    desired = [_cat("svc", connector="tpch")]
    actual = [_snap("svc", connector="memory")]
    actions = plan(desired, actual)
    assert len(actions) == 2
    assert actions[0].kind == ActionKind.DROP
    assert actions[0].name == "svc"
    assert actions[1].kind == ActionKind.CREATE
    assert actions[1].name == "svc"
    assert actions[1].connector == "tpch"


def test_same_name_same_connector_is_in_sync():
    """Property drift can't be detected; same name + same connector is treated as ok."""
    desired = [_cat("svc", connector="postgresql", properties={"k": "new"})]
    actual = [_snap("svc", connector="postgresql")]
    assert plan(desired, actual) == []


def test_connector_mismatch_for_one_doesnt_affect_others():
    desired = [_cat("a", connector="tpch"), _cat("b", connector="postgresql")]
    actual = [_snap("a", connector="memory"), _snap("b", connector="postgresql")]
    actions = plan(desired, actual)
    # Only "a" should change; "b" should be left alone.
    affected = {a.name for a in actions}
    assert affected == {"a"}
    assert len(actions) == 2  # DROP a, CREATE a
