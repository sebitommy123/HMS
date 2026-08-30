"""The trino 0.337.0 empty-arguments workaround (datapro_core.trino_patch).

Importing datapro_core.trino_client applies the patch; we import the patch
module directly so the test is self-contained. Pure unit — no Trino needed."""

from trino.dbapi import ColumnDescription

import datapro_core.trino_patch  # noqa: F401  (import applies the patch)


def _col(raw_type: str, arguments: list, name: str = "c"):
    return {
        "name": name,
        "type": raw_type,
        "typeSignature": {"rawType": raw_type, "arguments": arguments},
    }


def _len_arg(value: int):
    return {"kind": "LONG_LITERAL", "value": value}


# ---- the bug: bare types with EMPTY arguments must not crash ----


def test_bare_varchar_does_not_crash():
    """An unbounded varchar (Postgres text) has rawType=varchar but no length —
    the unpatched client did arguments[0]['value'] and raised IndexError."""
    d = ColumnDescription.from_column(_col("varchar", []))
    assert d.name == "c"
    assert d.internal_size is None  # no length available → None, not a crash


def test_bare_char_and_decimal_and_timestamp_do_not_crash():
    for raw in ("char", "decimal", "timestamp", "time"):
        d = ColumnDescription.from_column(_col(raw, []))
        assert d.internal_size is None
        assert d.precision is None
        assert d.scale is None


# ---- regression: bounded types still report their size/precision/scale ----


def test_bounded_varchar_reports_length():
    d = ColumnDescription.from_column(_col("varchar", [_len_arg(50)]))
    assert d.internal_size == 50


def test_decimal_reports_precision_and_scale():
    d = ColumnDescription.from_column(_col("decimal", [_len_arg(10), _len_arg(2)]))
    assert d.precision == 10
    assert d.scale == 2


def test_non_parameterized_type_is_unaffected():
    d = ColumnDescription.from_column(_col("bigint", []))
    assert d.internal_size is None
    assert d.precision is None
    assert d.scale is None
