"""Data source reads. A data source is a specific (catalog, schema, table)
handle that one or more object factories read from.

Data sources are **sync-owned**: the catalog reconciler discovers them from
Trino (via ``information_schema.tables``) and inserts/deletes/marks-deleted
rows to match reality. There is no create/update/delete API — this module is
read-only. See ``reconciler.sync_data_sources``.
"""

import uuid

from flask import Blueprint, current_app, jsonify, request

from datapro_core.api._env import BadEnv, env_from_request
from datapro_core.models import DataSource
from datapro_core.staging.env import data_sources_in, resolve_catalog
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
    """List data sources visible in the (optional) env overlay. ``?catalog=``
    filters by *logical* catalog name (resolved to physical within the env)."""
    catalog = (request.args.get("catalog") or "").strip()
    with _session() as session:
        try:
            env = env_from_request(session)
        except BadEnv as exc:
            return jsonify({"error": "bad_env", "details": exc.message}), 400
        rows = data_sources_in(session, env)
        if catalog:
            resolved = resolve_catalog(session, env, catalog)
            physical = resolved.name if resolved is not None else catalog
            rows = [r for r in rows if r.catalog_name == physical]
        rows = sorted(
            rows,
            key=lambda r: (r.catalog_name, r.schema_name, r.table_name),
        )
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
