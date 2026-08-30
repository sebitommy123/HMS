"""Unit tests for assemble_objects — the tabular-result → HMS-objects mapping.

Pure: no Trino, no containers. The assembler only reads plan.object_type_traits
and each factory's data_source_path, so plans here are minimal."""

from datapro_core.query.models import FactoryPlan, Query, QueryPlan
from datapro_core.query.objects import assemble_objects


def _plan(paths: list[str], *, identity: bool) -> QueryPlan:
    factories = [
        FactoryPlan(
            factory_id=f"f{i}",
            data_source_id=f"d{i}",
            data_source_path=p,
            use_all_columns=True,
            column_spec=[],
        )
        for i, p in enumerate(paths)
    ]
    return QueryPlan(
        query=Query(from_type="X", limit=25, timeout_seconds=10),
        object_type_id="t",
        object_type_name="X",
        factories=factories,
        object_type_traits=["identity"] if identity else [],
    )


A = "cat.sch.a"
B = "cat.sch.b"


# ---- identity path ----------------------------------------------------


def test_identity_multi_source_agree():
    """Both sources contribute the same identity; a shared field agrees."""
    plan = _plan([A, B], identity=True)
    columns = [
        "_identity",
        f"_datasource__{A}",
        f"feedcode__{A}",
        f"price__{A}",
        f"_datasource__{B}",
        f"price__{B}",
    ]
    rows = [["id1", A, "FEED1", 42.5, B, 42.5]]
    (obj,) = assemble_objects(plan, columns, rows)

    assert obj["data_sources"] == [A, B]
    assert obj["id"] == "id1"
    assert obj["fields"] == {
        "feedcode": {A: "FEED1"},
        "price": {A: 42.5, B: 42.5},  # both sources present; caller sees they agree
    }


def test_identity_multi_source_disagree():
    plan = _plan([A, B], identity=True)
    columns = ["_identity", f"_datasource__{A}", f"price__{A}", f"_datasource__{B}", f"price__{B}"]
    rows = [["id1", A, 42.5, B, 43.0]]
    (obj,) = assemble_objects(plan, columns, rows)
    assert obj["fields"]["price"] == {A: 42.5, B: 43.0}


def test_identity_non_contributing_source_omitted():
    """Row where only source A matched the identity: B's presence is NULL, so B
    is neither in data_sources nor in any field."""
    plan = _plan([A, B], identity=True)
    columns = ["_identity", f"_datasource__{A}", f"price__{A}", f"_datasource__{B}", f"price__{B}"]
    rows = [["id1", A, 42.5, None, None]]
    (obj,) = assemble_objects(plan, columns, rows)
    assert obj["data_sources"] == [A]
    assert obj["fields"] == {"price": {A: 42.5}}


def test_identity_contributing_source_with_null_field_is_kept():
    """A source that DID contribute (presence non-null) but whose field value is
    a genuine NULL keeps the null — that's real data, not absence."""
    plan = _plan([A], identity=True)
    columns = ["_identity", f"_datasource__{A}", f"price__{A}"]
    rows = [["id1", A, None]]
    (obj,) = assemble_objects(plan, columns, rows)
    assert obj["data_sources"] == [A]
    assert obj["fields"] == {"price": {A: None}}


# ---- union path -------------------------------------------------------


def test_union_single_source_no_id():
    plan = _plan([A], identity=False)
    columns = ["_datasource", "name", "sic"]
    rows = [[A, "Apple", "3571"]]
    (obj,) = assemble_objects(plan, columns, rows)
    assert obj["data_sources"] == [A]
    assert "id" not in obj
    assert obj["fields"] == {"name": {A: "Apple"}, "sic": {A: "3571"}}


def test_union_null_filled_columns_omitted():
    """Union rows NULL-fill columns a source lacks; with no presence sentinel we
    can't tell absent from genuine-null, so nulls are omitted."""
    plan = _plan([A, B], identity=False)
    columns = ["_datasource", "name", "extra_from_b"]
    rows = [[A, "Apple", None]]  # this row came from A, which lacks extra_from_b
    (obj,) = assemble_objects(plan, columns, rows)
    assert obj["fields"] == {"name": {A: "Apple"}}


# ---- invariants -------------------------------------------------------


def test_objects_are_one_to_one_with_rows():
    plan = _plan([A, B], identity=True)
    columns = ["_identity", f"_datasource__{A}", f"price__{A}", f"_datasource__{B}", f"price__{B}"]
    rows = [
        ["id1", A, 1.0, B, 1.0],
        ["id2", A, 2.0, None, None],
        ["id3", None, None, B, 3.0],
    ]
    objs = assemble_objects(plan, columns, rows)
    assert len(objs) == len(rows)
    assert [o["id"] for o in objs] == ["id1", "id2", "id3"]
    assert objs[2]["data_sources"] == [B]  # only B matched id3


def test_empty_rows_yield_no_objects():
    plan = _plan([A], identity=True)
    assert assemble_objects(plan, ["_identity", f"_datasource__{A}"], []) == []
    assert assemble_objects(plan, [], []) == []
