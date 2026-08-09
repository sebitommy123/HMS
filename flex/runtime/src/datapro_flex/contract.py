"""The flex user contract.

A flex module exposes two callables. The Java connector dispatches
exactly these two operations and nothing else. Keep the surface small;
new capabilities (writes, statistics, push-down hooks) earn their place
in the contract individually.

Splits and column projection are deliberately **not** in the contract.
Flex assumes there isn't much data — parallelism (splits) isn't worth
the API weight, so the framework always runs a single split internally.
Column projection is handled by the worker at egress: a module always
produces every declared column and the worker drops the unwanted ones
before they cross the Flight boundary. A module never sees a split or a
projection.

Phase A supports only this fixed type set:

  ============  ========================  ==============
  Trino type    Arrow type                 Python value
  ============  ========================  ==============
  BIGINT        int64                      int
  INTEGER       int32                      int
  DOUBLE        float64                    float
  BOOLEAN       bool                       bool
  VARCHAR       utf8                       str
  DATE          date32                     datetime.date
  TIMESTAMP_TZ  timestamp("ms", tz="UTC")  datetime.datetime (tz-aware; ms-truncated server-side)
  JSON          utf8                       str (caller serializes)
  ============  ========================  ==============

VARCHAR with explicit length, DECIMAL, ARRAY, ROW, MAP, and the rest of
Trino's type system are intentionally **not** in Phase A. They land
when there's a concrete flex module that needs them.
"""

from __future__ import annotations

from typing import Iterable, Literal, Protocol, TypedDict

import pyarrow as pa


TrinoType = Literal[
    "BIGINT",
    "INTEGER",
    "DOUBLE",
    "BOOLEAN",
    "VARCHAR",
    "DATE",
    "TIMESTAMP_TZ",
    "JSON",
]


class ColumnDescriptor(TypedDict):
    name: str
    type: TrinoType


class TableDescriptor(TypedDict):
    # Schema-qualified name. ``schema`` defaults to "default" on the Java
    # side if the module returns just ``name``.
    name: str
    schema: str  # use "default" if you only have one namespace
    columns: list[ColumnDescriptor]


class FlexModule(Protocol):
    """Structural type a flex module must satisfy. Modules don't
    have to inherit anything — module-level functions with the right
    signatures are enough."""

    def get_tables(self) -> list[TableDescriptor]:
        """Return every logical table this module exposes. Called
        once per worker lifetime (cached) — must be deterministic and
        cheap. Don't open network connections here; defer that to
        the first ``read_table`` call."""
        ...

    def read_table(self, table: str) -> Iterable[pa.RecordBatch]:
        """Yield Arrow record batches for the whole table. Always
        produce **every** declared column — the worker projects down to
        whatever columns the query needs before the batches cross the
        Flight boundary, so a module never has to think about
        projection.

        Each batch must conform to the declared schema for ``table``.
        Use ``datapro_flex.batch_from_rows`` if you prefer dict-of-row
        iteration over building Arrow vectors by hand. For a large
        table, yield it in chunks rather than one giant batch.
        """
        ...
