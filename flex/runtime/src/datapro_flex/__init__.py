"""DataPro Flex — author Trino-queryable tables in Python.

The user-facing surface is intentionally tiny: two callbacks and a
handful of type aliases. Read ``contract`` for the full story. Helpers
for building Arrow batches without learning pyarrow are in
``arrow_schema``.

A flex *module* is any Python file that, when imported, exposes those
two callbacks at module scope:

    def get_tables(): ...
    def read_table(table): ...

The DataPro Flex Java connector spawns ``python -m datapro_flex.worker
--module-path <your-module.py> --port 0`` and serves every Trino RPC
through it. Splits (parallelism) and column projection are handled by
the framework, not the module — see ``contract``.
"""

from datapro_flex.contract import (
    ColumnDescriptor,
    FlexModule,
    TableDescriptor,
    TrinoType,
)
from datapro_flex.arrow_schema import (
    arrow_schema_for_table,
    batch_from_rows,
)

__all__ = [
    "ColumnDescriptor",
    "FlexModule",
    "TableDescriptor",
    "TrinoType",
    "arrow_schema_for_table",
    "batch_from_rows",
]
