"""Interpret a tabular query result into HMS objects.

The executor gets ``columns`` + ``rows`` back from Trino, where the column
*names* encode everything (which source a value came from, whether a source
contributed a given identity, etc. — see ``sql_builder``). Clients shouldn't
have to decode that. ``assemble_objects`` inverts the encoding into one object
per row, invertible 1:1 with the row.

Object shape::

    {
      "data_sources": ["cat.sch.a", "cat.sch.b"],   # every source that fed this object
      "id": <value>,                                 # only when the object type has `identity`
      "fields": { "<name>": { "<data_source>": <value> } }
    }

Access is always ``object["fields"][name][data_source]`` — never flattened, no
default even for single-source fields. Provenance is explicit by design.
"""

from __future__ import annotations

from typing import Any

from datapro_core.query.models import QueryPlan

# Synthesised column names the encoding reserves. These become `id` /
# `data_sources`, never fields.
_IDENTITY_COL = "_identity"
_DATASOURCE_COL = "_datasource"


def assemble_objects(
    plan: QueryPlan, columns: list[str], rows: list[list[Any]]
) -> list[dict[str, Any]]:
    """One object per row (same order). Dispatches on the identity trait, which
    is exactly what determined the SQL shape in ``sql_builder.build_sql``."""
    if not columns or not rows:
        return []
    if "identity" in plan.object_type_traits:
        return _assemble_identity(plan, columns, rows)
    return _assemble_union(columns, rows)


# ---- identity path -----------------------------------------------------
#
# Columns: `_identity`, plus per source path P: `_datasource__P` (presence
# sentinel — non-null iff P contributed this identity) and `<field>__P`.


def _assemble_identity(
    plan: QueryPlan, columns: list[str], rows: list[list[Any]]
) -> list[dict[str, Any]]:
    paths = [f.data_source_path for f in plan.factories]
    id_idx = columns.index(_IDENTITY_COL) if _IDENTITY_COL in columns else None

    # Classify every column once: presence sentinel for path P, or (field, P).
    presence_idx: dict[str, int] = {}
    field_cols: list[tuple[str, str, int]] = []  # (field_name, path, col_index)
    for i, name in enumerate(columns):
        if name == _IDENTITY_COL:
            continue
        path = _match_path(name, paths)
        if path is None:
            continue  # defensive: unrecognised column, skip
        base = name[: -len(f"__{path}")]
        if base == _DATASOURCE_COL:
            presence_idx[path] = i
        else:
            field_cols.append((base, path, i))

    objects: list[dict[str, Any]] = []
    for row in rows:
        # Which sources actually contributed this identity (presence non-null).
        contributing = [
            p for p in paths if p in presence_idx and row[presence_idx[p]] is not None
        ]
        obj: dict[str, Any] = {"data_sources": contributing}
        if id_idx is not None:
            obj["id"] = row[id_idx]

        fields: dict[str, dict[str, Any]] = {}
        for name, path, i in field_cols:
            if path in contributing:  # keep genuine NULLs from contributors; drop absentees
                fields.setdefault(name, {})[path] = row[i]
        obj["fields"] = fields
        objects.append(obj)
    return objects


# ---- union path --------------------------------------------------------
#
# Columns: a single `_datasource` (which source this row came from) + the union
# of all factories' columns (NULL-filled where a source lacks one). One row =
# one source, so no merge and no id.


def _assemble_union(
    columns: list[str], rows: list[list[Any]]
) -> list[dict[str, Any]]:
    ds_idx = columns.index(_DATASOURCE_COL) if _DATASOURCE_COL in columns else None
    field_cols = [
        (name, i) for i, name in enumerate(columns) if name != _DATASOURCE_COL
    ]

    objects: list[dict[str, Any]] = []
    for row in rows:
        source = row[ds_idx] if ds_idx is not None else None
        obj: dict[str, Any] = {"data_sources": [source] if source is not None else []}
        fields: dict[str, dict[str, Any]] = {}
        if source is not None:
            for name, i in field_cols:
                value = row[i]
                # No presence sentinel on the union path, so a genuine NULL is
                # indistinguishable from "this source lacks the column" — omit it.
                if value is not None:
                    fields[name] = {source: value}
        obj["fields"] = fields
        objects.append(obj)
    return objects


def _match_path(column: str, paths: list[str]) -> str | None:
    """Return the source path P such that ``column`` ends with ``__P``, or None.
    Longest match wins so a path that is a suffix of another can't shadow it
    (the `__` delimiter over dot-paths makes real collisions near-impossible,
    but be defensive)."""
    best: str | None = None
    for p in paths:
        if column.endswith(f"__{p}") and (best is None or len(p) > len(best)):
            best = p
    return best
