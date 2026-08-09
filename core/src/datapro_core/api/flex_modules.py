"""Flex module CRUD + preview + AI-friendly edit ops + contract discovery.

The catalog row owns the lifecycle (CREATE / DELETE come through
``/catalogs``); this blueprint is for *source* operations on an
already-registered flex catalog:

  GET  /flex-modules/{name}                     read current source + version
  PUT  /flex-modules/{name}                     replace source (hot-swap)
  POST /flex-modules/{name}/replace             substring replacement (AI surface)
  POST /flex-modules/{name}/replace-lines       line-range replacement (AI surface)
  POST /flex-modules/preview                    materialize-and-sample without committing

Hot-swap path: PUT writes to the same on-disk path the catalog already
points at. The Java connector watches the file's mtime and triggers a
Python worker reload on next query. No catalog DROP+CREATE.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from datapro_core import flex_module_materializer as materializer
from datapro_core.models import FlexModule
from datapro_core.schemas import (
    FlexModulePreviewRequest,
    FlexModuleReplaceLinesRequest,
    FlexModuleReplaceRequest,
    FlexModuleUpdateRequest,
)
from datapro_core.trino_client import TrinoError


bp = Blueprint("flex_modules", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


def _config():
    return current_app.config["DATAPRO"]


def _validate_python(source: str) -> tuple[bool, str | None]:
    """Cheap syntax check before we write to disk. Catches the obvious
    "typed a `,` for a `;`" mistakes; doesn't run the module."""
    try:
        compile(source, "<flex-module>", "exec")
        return True, None
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"


def _validation_error_response(exc: ValidationError):
    return (
        jsonify(
            {
                "error": "invalid_request",
                "details": [
                    {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ],
            }
        ),
        400,
    )


# ---- read ------------------------------------------------------------


@bp.get("/flex-modules/<name>")
def get_module(name: str):
    with _session() as session:
        row = session.query(FlexModule).where(FlexModule.catalog_name == name).one_or_none()
        if row is None:
            return jsonify({"error": "not_found", "catalog_name": name}), 404
        return jsonify(row.to_dict())


# ---- replace (hot-swap) ----------------------------------------------


@bp.put("/flex-modules/<name>")
def replace_module(name: str):
    try:
        payload = FlexModuleUpdateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400
    return _persist_source(name, payload.source)


@bp.post("/flex-modules/<name>/replace")
def replace_substring(name: str):
    try:
        payload = FlexModuleReplaceRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    with _session() as session:
        row = session.query(FlexModule).where(FlexModule.catalog_name == name).one_or_none()
        if row is None:
            return jsonify({"error": "not_found", "catalog_name": name}), 404
        source = row.source_text
        occurrences = source.count(payload.old_text)
        if occurrences == 0:
            return (
                jsonify(
                    {
                        "error": "old_text_not_found",
                        "details": (
                            "old_text does not appear in the current module source. "
                            "Use GET /flex-modules/<name> to see the current source "
                            "and choose a substring that exists verbatim."
                        ),
                    }
                ),
                400,
            )
        if occurrences > 1:
            return (
                jsonify(
                    {
                        "error": "old_text_ambiguous",
                        "details": (
                            f"old_text appears {occurrences} times in the module. "
                            "Provide a longer substring that uniquely identifies the "
                            "location, or use POST /flex-modules/<name>/replace-lines "
                            "with the exact line range."
                        ),
                        "occurrences": occurrences,
                    }
                ),
                400,
            )
    new_source = source.replace(payload.old_text, payload.new_text, 1)
    return _persist_source(name, new_source)


@bp.post("/flex-modules/<name>/replace-lines")
def replace_lines(name: str):
    try:
        payload = FlexModuleReplaceLinesRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    if payload.end_line < payload.start_line:
        return (
            jsonify(
                {
                    "error": "invalid_range",
                    "details": "end_line must be >= start_line",
                }
            ),
            400,
        )

    with _session() as session:
        row = session.query(FlexModule).where(FlexModule.catalog_name == name).one_or_none()
        if row is None:
            return jsonify({"error": "not_found", "catalog_name": name}), 404
        source = row.source_text
        # splitlines(keepends=True) keeps line terminators intact so a
        # round-trip without edits produces a byte-identical file.
        lines = source.splitlines(keepends=True)
        if payload.end_line > len(lines):
            return (
                jsonify(
                    {
                        "error": "end_line_out_of_range",
                        "details": (
                            f"end_line={payload.end_line} exceeds the module's "
                            f"line count ({len(lines)})."
                        ),
                    }
                ),
                400,
            )
    # The new text shouldn't accidentally swallow the trailing newline
    # of the line before start_line — splitlines already put that on
    # the previous line. Normalize: ensure new_text ends with \n unless
    # the range covered the file's final line and that line had no
    # trailing newline.
    new_text = payload.new_text
    keeps_trailing_newline = lines[payload.end_line - 1].endswith(("\n", "\r"))
    if new_text and not new_text.endswith("\n") and keeps_trailing_newline:
        new_text = new_text + "\n"
    new_lines = (
        lines[: payload.start_line - 1]
        + [new_text]
        + lines[payload.end_line :]
    )
    return _persist_source(name, "".join(new_lines))


def _persist_source(catalog_name: str, source: str):
    """Shared tail end of every "this is the new source" path: syntax
    check, atomic write, persist row, bump version."""
    ok, err = _validate_python(source)
    if not ok:
        return (
            jsonify({"error": "invalid_python", "details": err}),
            400,
        )
    with _session() as session:
        row = session.query(FlexModule).where(FlexModule.catalog_name == catalog_name).one_or_none()
        if row is None:
            return jsonify({"error": "not_found", "catalog_name": catalog_name}), 404
        try:
            materializer.write(_config(), catalog_name, source)
        except Exception as exc:
            return (
                jsonify(
                    {
                        "error": "materialize_failed",
                        "details": str(exc),
                    }
                ),
                500,
            )
        row.source_text = source
        row.version = (row.version or 0) + 1
        session.commit()
        return jsonify(row.to_dict()), 200


# ---- preview ---------------------------------------------------------


@bp.post("/flex-modules/preview")
def preview():
    try:
        payload = FlexModulePreviewRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    ok, err = _validate_python(payload.source)
    if not ok:
        return jsonify({"error": "invalid_python", "details": err}), 400

    trino = _trino()
    cfg = _config()
    # Unique per-request name so concurrent previews don't collide.
    # Hyphens aren't safe in Trino catalog identifiers, only [_alnum].
    catalog_name = f"_flex_preview_{uuid.uuid4().hex[:12]}"
    try:
        materializer.write(cfg, catalog_name, payload.source)
        module_path = materializer.container_path_for(cfg, catalog_name)
        trino.create_catalog(
            catalog_name, connector="flex", properties={"flex.module_path": module_path}
        )
    except Exception as exc:
        # Materialization or catalog creation failed — clean up either
        # the file or the catalog (whichever made it that far) and return.
        try:
            trino.drop_catalog(catalog_name)
        except Exception:
            pass
        materializer.delete(cfg, catalog_name)
        return (
            jsonify({"error": "preview_setup_failed", "details": str(exc)}),
            500,
        )

    try:
        tables_preview: list[dict] = []
        # Walk schemas → tables → sample. Mirrors what the UI's
        # tabular-preview pane wants to render.
        listing = trino.execute_query(
            f'SELECT table_schema, table_name FROM "{catalog_name}".information_schema.tables '
            f"WHERE table_schema NOT IN ('information_schema')",
            timeout_seconds=15.0,
            max_rows=1000,
        )
        for schema, name in listing.rows:
            try:
                desc = trino.execute_query(
                    f'DESCRIBE "{catalog_name}"."{schema}"."{name}"',
                    timeout_seconds=15.0,
                    max_rows=1000,
                )
                columns = [{"name": d[0], "type": d[1]} for d in desc.rows]
                sample = trino.execute_query(
                    f'SELECT * FROM "{catalog_name}"."{schema}"."{name}" '
                    f"LIMIT {int(payload.sample_limit)}",
                    timeout_seconds=30.0,
                    max_rows=int(payload.sample_limit),
                )
                tables_preview.append(
                    {
                        "schema": schema,
                        "name": name,
                        "columns": columns,
                        "sample_columns": sample.columns,
                        "sample_rows": [list(r) for r in sample.rows],
                    }
                )
            except TrinoError as exc:
                tables_preview.append(
                    {
                        "schema": schema,
                        "name": name,
                        "error": str(exc),
                    }
                )
        return jsonify({"tables": tables_preview})
    except TrinoError as exc:
        return (
            jsonify({"error": "preview_query_failed", "details": str(exc)}),
            502,
        )
    finally:
        try:
            trino.drop_catalog(catalog_name)
        except Exception:
            pass
        materializer.delete(cfg, catalog_name)


# ---- contract discovery ----------------------------------------------


# Path to the flex runtime sources, anchored on the repo root that Core
# lives inside. In dev: <repo>/flex/runtime/src/datapro_flex/{contract,arrow_schema}.py.
# In production: same layout shipped in the deploy artifact.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_FLEX_RUNTIME = _REPO_ROOT / "flex" / "runtime" / "src" / "datapro_flex"
_FLEX_EXAMPLE = _REPO_ROOT / "flex" / "examples" / "users_json" / "module.py"

_SUPPORTED_TYPES = [
    "BIGINT", "INTEGER", "DOUBLE", "BOOLEAN",
    "VARCHAR", "DATE", "TIMESTAMP_TZ", "JSON",
]


@bp.get("/flex-contract")
def get_flex_contract():
    """Returns everything an author (human or AI) needs to write a
    flex module against Phase A's contract: the canonical Python
    Protocol definition, the type-mapping helpers, the eight supported
    Trino types, and a fully worked example module.

    Designed so the AI's get_flex_contract tool can return all of this
    in a single call — no need for the agent to grep the repo or guess
    at function signatures from preview errors.
    """
    try:
        contract_source = (_FLEX_RUNTIME / "contract.py").read_text()
    except FileNotFoundError:
        contract_source = ""
    try:
        arrow_helpers = (_FLEX_RUNTIME / "arrow_schema.py").read_text()
    except FileNotFoundError:
        arrow_helpers = ""
    try:
        example_source = _FLEX_EXAMPLE.read_text()
    except FileNotFoundError:
        example_source = ""

    return jsonify({
        "summary": (
            "A flex module is a Python file with two module-level callables: "
            "get_tables() and read_table(table). The runtime imports it once "
            "per catalog and dispatches Trino's metadata + scan calls through "
            "Arrow Flight. Flex assumes small data: there are no splits "
            "(the framework runs one internally) and read_table always "
            "returns every declared column (the worker projects for the query)."
        ),
        "supported_types": _SUPPORTED_TYPES,
        "function_signatures": {
            "get_tables": "def get_tables() -> list[TableDescriptor]",
            "read_table": "def read_table(table: str) -> Iterable[pyarrow.RecordBatch]",
        },
        "contract_source": contract_source,
        "arrow_helpers_source": arrow_helpers,
        "example_module_source": example_source,
    })
