"""Workaround for a bug in trino-python-client 0.337.0.

``ColumnDescription.from_column`` (trino/dbapi.py) computes ``cursor.description``
by reading ``typeSignature.arguments[0]["value"]`` *unconditionally* for any
length/precision/scale type::

    arguments[0]["value"] if raw_type in LENGTH_TYPES else None,   # internal_size
    arguments[0]["value"] if raw_type in PRECISION_TYPES else None,  # precision
    arguments[1]["value"] if raw_type in SCALE_TYPES else None,      # scale

Trino's Postgres connector emits unbounded ``text`` and numeric-without-precision
columns as a *bare* ``varchar`` / ``decimal`` — ``rawType`` is a length/precision
type but ``arguments`` is EMPTY. So building the description for such a column
raises ``IndexError: list index out of range`` before a single row is returned.
Any query touching those columns crashes inside the client — including the UI's
Preview button (``POST /raw-trino-query``).

We monkey-patch ``from_column`` to fall back to ``None`` when the argument isn't
present, instead of indexing an empty list. Behaviour is otherwise identical:
bounded types (``varchar(50)``, ``decimal(10,2)``, ``timestamp(6)``) still report
their size/precision/scale.

REMOVE THIS the moment upstream ships a fix and ``core/pyproject.toml``'s
``trino`` pin is bumped past the broken release. It's applied once at import of
``datapro_core.trino_client`` (the only module that talks to the Trino DBAPI).
"""

from typing import Any, Dict

import trino.dbapi as _dbapi
from trino.constants import LENGTH_TYPES, PRECISION_TYPES, SCALE_TYPES


def _from_column(cls, column: Dict[str, Any]):
    type_signature = column["typeSignature"]
    raw_type = type_signature["rawType"]
    arguments = type_signature["arguments"]

    def arg(i: int):
        # The whole fix: only index when the argument is actually present.
        return arguments[i]["value"] if len(arguments) > i else None

    return cls(
        column["name"],  # name
        column["type"],  # type_code
        None,  # display_size
        arg(0) if raw_type in LENGTH_TYPES else None,  # internal_size
        arg(0) if raw_type in PRECISION_TYPES else None,  # precision
        arg(1) if raw_type in SCALE_TYPES else None,  # scale
        None,  # null_ok
    )


def apply() -> None:
    """Install the patched ``from_column``. Idempotent — safe to call repeatedly."""
    _dbapi.ColumnDescription.from_column = classmethod(_from_column)


# Apply on import so merely importing this module (as trino_client does) fixes
# the client before any query runs.
apply()
