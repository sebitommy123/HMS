"""Example flex module: exposes a single table backed by users.json.

This module sits next to ``users.json`` and reads it relative to its
own file. In a real catalog the JSON path would come from catalog
properties — Phase A's contract doesn't yet pass props through to the
module, so for now hardcoded-relative is fine. (Phase B adds an
``init_catalog(props)`` hook for runtime config.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pyarrow as pa

from datapro_flex import batch_from_rows


HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "users.json"


TABLE = {
    "schema": "default",
    "name": "users",
    "columns": [
        {"name": "id", "type": "BIGINT"},
        {"name": "name", "type": "VARCHAR"},
        {"name": "age", "type": "INTEGER"},
        {"name": "email", "type": "VARCHAR"},
    ],
}


def get_tables() -> list[dict]:
    return [TABLE]


def read_table(table: str) -> Iterable[pa.RecordBatch]:
    if table != "users":
        return
    rows = json.loads(DATA_FILE.read_text())
    # All rows in one batch — fine for 5 rows; chunk for real data.
    # Always produce every column; the worker projects for the query.
    yield batch_from_rows(rows, table=TABLE)
