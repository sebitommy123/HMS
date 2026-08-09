"""Data source reads. A data source is a specific (catalog, schema, table)
handle that one or more object factories read from.

Data sources are **sync-owned**: the catalog reconciler discovers them from
Trino (via ``information_schema.tables``) and inserts/deletes/marks-deleted
rows to match reality. There is no create/update/delete API — this module is
read-only. See ``reconciler.sync_data_sources``.
"""

import uuid

from flask import Blueprint, current_app, jsonify, request

from datapro_core.models import DataSource
from datapro_core.trino_client import TrinoError

bp = Blueprint("data_sources", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


def _parse_id(raw: str):
    try:
        return uuid.UUID(raw)
    except ValueError:
        return jsonify({"error": "invalid_id", "id": raw}), 400


@bp.get("/data-sources")
def list_data_sources():
    """List data sources. Optional ``?catalog=<name>`` filter."""
    catalog = (request.args.get("catalog") or "").strip()
    with _session() as session:
        q = session.query(DataSource)
        if catalog:
            q = q.where(DataSource.catalog_name == catalog)
        rows = q.order_by(
            DataSource.catalog_name, DataSource.schema_name, DataSource.table_name
        ).all()
        return jsonify([r.to_dict() for r in rows])


@bp.get("/data-sources/<id_>")
def get_data_source(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        row = session.get(DataSource, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        return jsonify(row.to_dict())


@bp.get("/data-sources/<id_>/columns")
def get_data_source_columns(id_: str):
    """Introspect the data source's columns by running SHOW COLUMNS against
    Trino. Returns ``{columns: [{name, type}, ...]}`` for the table the
    data source points to.

    Live read every call — schemas can change upstream. Trino errors
    (table missing, catalog unreachable) surface as 502 with the raw
    Trino message so the UI / agent can show the operator what's broken.
    """
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        row = session.get(DataSource, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        catalog = row.catalog_name
        schema = row.schema_name
        table = row.table_name
        path = f"{catalog}.{schema}.{table}"

    try:
        cols = _trino().show_columns(catalog, schema, table)
    except TrinoError as exc:
        return (
            jsonify(
                {
                    "error": "trino_error",
                    "details": str(exc),
                    "path": path,
                }
            ),
            502,
        )

    return jsonify(
        {
            "data_source_id": id_,
            "path": path,
            "columns": [{"name": n, "type": t} for (n, t) in cols],
        }
    )
