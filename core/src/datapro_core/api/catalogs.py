from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError

from datapro_core import flex_module_materializer as flex_materializer
from datapro_core.models import Catalog, CatalogStatus, FlexModule
from datapro_core.reconciler import reconcile
from datapro_core.schemas import CatalogCreateRequest, CatalogUpdateRequest
from datapro_core.trino_client import TrinoError


bp = Blueprint("catalogs", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


def _public_catalog(row: Catalog) -> dict:
    """Serialize a catalog for the client. A flex catalog's only property is
    ``flex.module_path`` — a Core-managed implementation detail (where the
    materialized module lives inside the Trino container). It's neither useful
    nor safe for clients to see or edit (removing it breaks the catalog), so we
    redact flex properties on the way out. Editing is blocked in the PATCH
    handler; hiding them here keeps the UI/agent from surfacing them at all."""
    d = row.to_dict()
    if row.connector == "flex":
        d["properties"] = {}
    return d


@bp.get("/catalogs")
def list_catalogs():
    with _session() as session:
        rows = session.query(Catalog).order_by(Catalog.name).all()
        return jsonify([_public_catalog(r) for r in rows])


@bp.get("/catalogs/<name>")
def get_catalog(name: str):
    with _session() as session:
        row = session.get(Catalog, name)
        if row is None:
            return jsonify({"error": "not_found", "name": name}), 404
        return jsonify(_public_catalog(row))


@bp.post("/catalogs")
def create_catalog():
    try:
        payload = CatalogCreateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
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
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    with _session() as session:
        existing = session.get(Catalog, payload.name)
        if existing is not None:
            return jsonify({"error": "already_exists", "name": payload.name}), 409

        # Flex catalogs get an extra materialization step: write the
        # source to the shared volume + auto-populate flex.module_path
        # so the operator never has to touch a filesystem path by hand.
        properties = dict(payload.properties)
        flex_source: str | None = None
        if payload.connector == "flex":
            if payload.source is not None and "flex.module_path" in properties:
                return (
                    jsonify(
                        {
                            "error": "conflicting_flex_inputs",
                            "details": (
                                "Provide either `source` (Core materializes the "
                                "module + sets flex.module_path automatically) "
                                "or a manual `flex.module_path` property — not both."
                            ),
                        }
                    ),
                    400,
                )
            if payload.source is not None:
                ok, err = _validate_flex_source(payload.source)
                if not ok:
                    return jsonify({"error": "invalid_python", "details": err}), 400
                flex_source = payload.source
                cfg = current_app.config["DATAPRO"]
                try:
                    flex_materializer.write(cfg, payload.name, flex_source)
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
                properties["flex.module_path"] = flex_materializer.container_path_for(
                    cfg, payload.name
                )
            elif "flex.module_path" not in properties:
                return (
                    jsonify(
                        {
                            "error": "flex_module_required",
                            "details": (
                                "For connector=flex, supply either `source` or "
                                "a `flex.module_path` property pointing at an "
                                "already-mounted module on the Trino container."
                            ),
                        }
                    ),
                    400,
                )

        row = Catalog(
            name=payload.name,
            connector=payload.connector,
            properties=properties,
            status=CatalogStatus.ENABLED,
        )
        session.add(row)
        if flex_source is not None:
            session.add(FlexModule(catalog_name=payload.name, source_text=flex_source, version=1))
        session.commit()

        # Synchronous reconcile so the operator sees the outcome.
        result = reconcile(session, _trino())
        # Re-fetch to pick up status updates from reconcile.
        row = session.get(Catalog, payload.name)
        body = {
            "catalog": _public_catalog(row),
            "reconcile": {
                "all_ok": result.all_ok,
                "actions": [
                    {
                        "kind": r.action.kind,
                        "name": r.action.name,
                        "ok": r.ok,
                        "error": r.error,
                    }
                    for r in result.actions
                ],
            },
        }
        return jsonify(body), 201 if result.all_ok else 502


@bp.patch("/catalogs/<name>")
def update_catalog(name: str):
    """Modify an existing catalog. Either field optional; ``properties`` is a
    full replacement of the dict. Because Trino doesn't expose catalog
    properties through its introspection, the reconciler can't detect property
    drift on its own — so we force a DROP+CREATE on the Trino side here
    whenever anything actually changed."""
    try:
        payload = CatalogUpdateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
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
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    if payload.connector is None and payload.properties is None:
        return (
            jsonify(
                {
                    "error": "empty_patch",
                    "details": "Provide at least one of `connector` or `properties`.",
                }
            ),
            400,
        )

    with _session() as session:
        row = session.get(Catalog, name)
        if row is None:
            return jsonify({"error": "not_found", "name": name}), 404

        # Flex catalog properties (flex.module_path) are Core-managed and not
        # client-editable — removing/changing the path just breaks the catalog.
        # Reject property edits on flex catalogs outright; the module source is
        # edited via the /flex-modules endpoints instead.
        if row.connector == "flex" and payload.properties is not None:
            return (
                jsonify(
                    {
                        "error": "flex_properties_immutable",
                        "details": (
                            "Flex catalog properties are managed automatically by "
                            "Core and can't be modified. Edit the module source via "
                            "the flex-module endpoints instead."
                        ),
                    }
                ),
                400,
            )

        changed = False
        if payload.connector is not None and payload.connector != row.connector:
            row.connector = payload.connector
            changed = True
        if payload.properties is not None and payload.properties != (
            row.properties or {}
        ):
            row.properties = payload.properties
            changed = True

        if not changed:
            # No-op patch. Don't touch Trino, don't reconcile — return current
            # state so the operator can see what's on file.
            return jsonify({"catalog": _public_catalog(row), "reconcile": None}), 200

        # Reset to ENABLED so reconcile recreates the catalog in Trino. If the
        # previous state was BROKEN we want this PATCH to give it another shot
        # with the new config.
        row.status = CatalogStatus.ENABLED
        row.last_error = None
        session.commit()

        trino = _trino()
        # Force-drop on the Trino side so reconcile picks up the new
        # properties when it recreates. The diff in reconciler.plan() only
        # compares connector — without dropping first, a properties-only
        # change would be invisible to it and Trino would keep stale props.
        try:
            existing_in_trino = {s.name for s in trino.list_catalogs()}
            if name in existing_in_trino:
                trino.drop_catalog(name)
        except TrinoError as exc:
            # If we can't reach Trino to drop, fail loudly — the operator
            # needs to know Trino is unreachable rather than silently leaving
            # the catalog with stale properties.
            row.status = CatalogStatus.BROKEN
            row.last_error = f"pre-reconcile drop failed: {exc}"
            session.commit()
            return (
                jsonify(
                    {
                        "catalog": _public_catalog(row),
                        "reconcile": {
                            "all_ok": False,
                            "actions": [],
                            "error": str(exc),
                        },
                    }
                ),
                502,
            )

        result = reconcile(session, trino)
        row = session.get(Catalog, name)
        body = {
            "catalog": _public_catalog(row),
            "reconcile": {
                "all_ok": result.all_ok,
                "actions": [
                    {
                        "kind": r.action.kind,
                        "name": r.action.name,
                        "ok": r.ok,
                        "error": r.error,
                    }
                    for r in result.actions
                ],
            },
        }
        return jsonify(body), 200 if result.all_ok else 502


@bp.delete("/catalogs/<name>")
def delete_catalog(name: str):
    with _session() as session:
        row = session.get(Catalog, name)
        if row is None:
            return jsonify({"error": "not_found", "name": name}), 404
        is_flex = row.connector == "flex"
        session.delete(row)  # cascades to flex_modules row via FK
        session.commit()
        if is_flex:
            # Garbage-collect the materialized file. Best-effort; failures
            # here don't roll back the catalog delete (DB is the source
            # of truth, the file is just a cache).
            flex_materializer.delete(current_app.config["DATAPRO"], name)
        result = reconcile(session, _trino())
        return jsonify(
            {
                "deleted": name,
                "reconcile": {
                    "all_ok": result.all_ok,
                    "actions": [
                        {
                            "kind": r.action.kind,
                            "name": r.action.name,
                            "ok": r.ok,
                            "error": r.error,
                        }
                        for r in result.actions
                    ],
                },
            }
        )


def _validate_flex_source(source: str) -> tuple[bool, str | None]:
    """Mirror of the syntax check in api/flex_modules.py — kept here too
    so create_catalog doesn't have to reach into another blueprint
    module just to spell-check Python on the way in."""
    try:
        compile(source, "<flex-module>", "exec")
        return True, None
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: {exc.msg}"
