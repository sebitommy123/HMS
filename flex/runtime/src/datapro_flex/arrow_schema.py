"""Type mapping between the flex contract's Trino-type strings and
Arrow types, plus a convenience for building record batches from
plain Python dicts.

The mapping table is the single source of truth for Phase A's type
set. New types added here must also be added on the Java side's
TrinoTypes helper (A4) so both sides agree.
"""

from __future__ import annotations

from typing import Any, Iterable

import pyarrow as pa

from datapro_flex.contract import ColumnDescriptor, TableDescriptor, TrinoType


# Phase A type mapping. Trino-type string -> pyarrow type.
# Kept as a function (not a dict) so e.g. TIMESTAMP_TZ can re-use the
# tz-aware constructor without sharing mutable state.
def _arrow_type_for(trino_type: TrinoType) -> pa.DataType:
    match trino_type:
        case "BIGINT":
            return pa.int64()
        case "INTEGER":
            return pa.int32()
        case "DOUBLE":
            return pa.float64()
        case "BOOLEAN":
            return pa.bool_()
        case "VARCHAR" | "JSON":
            # JSON travels as UTF-8; modules are responsible for
            # producing valid JSON text. Trino's JSON type tolerates
            # arbitrary text but downstream consumers won't.
            return pa.utf8()
        case "DATE":
            return pa.date32()
        case "TIMESTAMP_TZ":
            # Millisecond precision matches Trino's TIMESTAMP(3).
            # The Java side enforces UTC normalization on receipt.
            return pa.timestamp("ms", tz="UTC")
        case _:
            raise ValueError(
                f"Unsupported flex Trino type {trino_type!r}. "
                "Phase A supports BIGINT, INTEGER, DOUBLE, BOOLEAN, "
                "VARCHAR, DATE, TIMESTAMP_TZ, JSON."
            )


def arrow_schema_for_table(table: TableDescriptor) -> pa.Schema:
    """Build the pyarrow Schema corresponding to a table descriptor.
    Used by the worker for outbound batch validation and by
    ``batch_from_rows`` for inferring field order."""
    fields = [
        pa.field(c["name"], _arrow_type_for(c["type"]))
        for c in table["columns"]
    ]
    return pa.schema(fields)


def batch_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    table: TableDescriptor,
    columns: list[str] | None = None,
) -> pa.RecordBatch:
    """Build one record batch from an iterable of row dicts.

    The dominant ergonomic for hand-written flex modules: write
    iteration as `yield {"col": ..., ...}` and call this once per
    batch. ``columns`` defaults to the table's full column list, which
    is what you want — ``read_table`` always produces every declared
    column and the worker projects down to the query's columns for you.

    For a large table, batch in chunks — one batch of a million rows
    is fine; one batch of fifty million isn't. The worker streams
    batches across the Flight DoGet boundary as you yield them.
    """
    descriptor_by_name = {c["name"]: c for c in table["columns"]}
    selected: list[ColumnDescriptor] = (
        [descriptor_by_name[name] for name in columns]
        if columns is not None
        else list(table["columns"])
    )
    if not selected:
        # Trino can ask for zero columns (COUNT(*)). Build a batch with
        # the right row count but no payload columns. PyArrow needs at
        # least one array to know the length, so use a throwaway and
        # then project it away.
        row_count = sum(1 for _ in rows)
        placeholder = pa.array([True] * row_count, type=pa.bool_())
        return pa.RecordBatch.from_arrays([placeholder], names=["_"]).select([])
    arrays_by_name: dict[str, list[Any]] = {c["name"]: [] for c in selected}
    for row in rows:
        for c in selected:
            arrays_by_name[c["name"]].append(row.get(c["name"]))
    fields = [pa.field(c["name"], _arrow_type_for(c["type"])) for c in selected]
    arrays = [
        pa.array(arrays_by_name[c["name"]], type=_arrow_type_for(c["type"]))
        for c in selected
    ]
    return pa.RecordBatch.from_arrays(arrays, schema=pa.schema(fields))
