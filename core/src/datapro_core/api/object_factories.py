"""Object Factory CRUD. A factory says "this data source produces objects of
this object type." Unique on (data_source_id, object_type_id); deleted
automatically if either parent goes away (ON DELETE CASCADE in the schema)."""

import uuid

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from datapro_core.factory_validator import validate_object_factory
from datapro_core.models import DataSource, ObjectFactory, ObjectType
from datapro_core.schemas import (
    ObjectFactoryCreateRequest,
    ObjectFactoryUpdateRequest,
)
from datapro_core.trino_client import TrinoError

bp = Blueprint("object_factories", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


def _validate_column_spec_against_source(
    data_source: DataSource, column_spec: list[str]
):
    """Confirm every column_spec entry is a real column on the data
    source's underlying Trino table. Returns ``None`` on success or a
    ``(response, status)`` tuple to return verbatim from the endpoint.

    Skips work when ``column_spec`` is empty — there's nothing to check.
    """
    if not column_spec:
        return None
    try:
        live_columns = _trino().show_columns(
            data_source.catalog_name,
            data_source.schema_name,
            data_source.table_name,
        )
    except TrinoError as exc:
        # Without a column list we can't validate. Refuse rather than
        # silently let through potentially-invalid entries.
        return (
            jsonify(
                {
                    "error": "columns_unverifiable",
                    "details": (
                        f"Trino couldn't introspect the data source's columns: {exc}"
                    ),
                    "data_source_path": (
                        f"{data_source.catalog_name}."
                        f"{data_source.schema_name}."
                        f"{data_source.table_name}"
                    ),
                }
            ),
            502,
        )
    valid = {name for (name, _ty) in live_columns}
    invalid = [c for c in column_spec if c not in valid]
    if invalid:
        return (
            jsonify(
                {
                    "error": "invalid_columns",
                    "details": (
                        "column_spec contains entries that aren't columns of "
                        f"{data_source.catalog_name}.{data_source.schema_name}."
                        f"{data_source.table_name}."
                    ),
                    "invalid": invalid,
                    "valid_columns": sorted(valid),
                }
            ),
            400,
        )
    return None


def _parse_id(raw: str):
    try:
        return uuid.UUID(raw)
    except ValueError:
        return jsonify({"error": "invalid_id", "id": raw}), 400


@bp.get("/object-factories")
def list_object_factories():
    """List all object factories. Filters:
      ``?data_source_id=<uuid>``       — only this source's factories
      ``?catalog=<name>``              — all factories under this catalog
                                          (joined through data_sources)
      ``?object_type_id=<uuid>``       — only factories producing this type
    """
    catalog = (request.args.get("catalog") or "").strip()
    type_id_raw = (request.args.get("object_type_id") or "").strip()
    source_id_raw = (request.args.get("data_source_id") or "").strip()

    type_id_filter: uuid.UUID | None = None
    if type_id_raw:
        try:
            type_id_filter = uuid.UUID(type_id_raw)
        except ValueError:
            return jsonify({"error": "invalid_id", "id": type_id_raw}), 400

    source_id_filter: uuid.UUID | None = None
    if source_id_raw:
        try:
            source_id_filter = uuid.UUID(source_id_raw)
        except ValueError:
            return jsonify({"error": "invalid_id", "id": source_id_raw}), 400

    with _session() as session:
        q = session.query(ObjectFactory)
        if catalog:
            # Join through data_sources so the UI's "factories under this
            # catalog" view keeps working transparently.
            q = q.join(DataSource).where(DataSource.catalog_name == catalog)
        if source_id_filter is not None:
            q = q.where(ObjectFactory.data_source_id == source_id_filter)
        if type_id_filter is not None:
            q = q.where(ObjectFactory.object_type_id == type_id_filter)
        rows = q.order_by(ObjectFactory.created_at).all()
        return jsonify([r.to_dict() for r in rows])


@bp.get("/object-factories/<id_>")
def get_object_factory(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        row = session.get(ObjectFactory, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        return jsonify(row.to_dict())


@bp.post("/object-factories")
def create_object_factory():
    try:
        payload = ObjectFactoryCreateRequest.model_validate(
            request.get_json(force=True)
        )
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    try:
        object_type_id = uuid.UUID(payload.object_type_id)
    except ValueError:
        return (
            jsonify(
                {
                    "error": "invalid_id",
                    "id": payload.object_type_id,
                    "details": "object_type_id must be a UUID",
                }
            ),
            400,
        )
    try:
        data_source_id = uuid.UUID(payload.data_source_id)
    except ValueError:
        return (
            jsonify(
                {
                    "error": "invalid_id",
                    "id": payload.data_source_id,
                    "details": "data_source_id must be a UUID",
                }
            ),
            400,
        )

    with _session() as session:
        # Verify both parents exist — DB FK would catch it too but the error
        # there is opaque; explicit 404s tell the user which one is missing.
        data_source = session.get(DataSource, data_source_id)
        if data_source is None:
            return (
                jsonify(
                    {
                        "error": "data_source_not_found",
                        "data_source_id": str(data_source_id),
                    }
                ),
                404,
            )
        if session.get(ObjectType, object_type_id) is None:
            return (
                jsonify(
                    {
                        "error": "object_type_not_found",
                        "object_type_id": str(object_type_id),
                    }
                ),
                404,
            )

        # If the factory will use a specific column list, every entry must
        # be a real column of the data source's underlying Trino table.
        if not payload.use_all_columns and payload.column_spec:
            err = _validate_column_spec_against_source(
                data_source, payload.column_spec
            )
            if err is not None:
                return err

        row = ObjectFactory(
            data_source_id=data_source_id,
            object_type_id=object_type_id,
            description=payload.description,
            use_all_columns=payload.use_all_columns,
            column_spec=payload.column_spec,
            trait_config=payload.trait_config,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return (
                jsonify(
                    {
                        "error": "already_exists",
                        "details": (
                            f"Data source {payload.data_source_id!r} already has a "
                            f"factory for object type {payload.object_type_id!r}."
                        ),
                    }
                ),
                409,
            )
        # Refresh so the joined-load on data_source + object_type runs and
        # we can return the full denormalized row.
        session.refresh(row)
        # Live-validate the factory so its status + last_error reflect
        # current Trino reality immediately — particularly important for
        # trait_config (a bad identity column shouldn't read as 'ok'
        # until the heartbeat catches it).
        _validate_after_write(session, row)
        return jsonify(row.to_dict()), 201


@bp.patch("/object-factories/<id_>")
def update_object_factory(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    try:
        payload = ObjectFactoryUpdateRequest.model_validate(
            request.get_json(force=True)
        )
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    if (
        payload.description is None
        and payload.use_all_columns is None
        and payload.column_spec is None
        and payload.trait_config is None
    ):
        return (
            jsonify(
                {
                    "error": "empty_patch",
                    "details": "Provide at least one of `description`, `use_all_columns`, `column_spec`, `trait_config`.",
                }
            ),
            400,
        )

    with _session() as session:
        row = session.get(ObjectFactory, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404

        # Compute the effective post-patch state for column validation.
        # If the result will have use_all_columns=false and a non-empty
        # column_spec, validate every entry against the live Trino schema.
        effective_use_all = (
            payload.use_all_columns
            if payload.use_all_columns is not None
            else row.use_all_columns
        )
        effective_spec = (
            payload.column_spec
            if payload.column_spec is not None
            else list(row.column_spec or [])
        )
        if not effective_use_all and effective_spec:
            err = _validate_column_spec_against_source(row.data_source, effective_spec)
            if err is not None:
                return err

        if payload.description is not None:
            row.description = payload.description
        if payload.use_all_columns is not None:
            row.use_all_columns = payload.use_all_columns
        if payload.column_spec is not None:
            row.column_spec = payload.column_spec
        if payload.trait_config is not None:
            row.trait_config = payload.trait_config
        session.commit()
        session.refresh(row)
        _validate_after_write(session, row)
        return jsonify(row.to_dict())


def _validate_after_write(session, factory: ObjectFactory) -> None:
    """Run factory_validator so the persisted status/last_error reflect
    the new config against live Trino. Swallows TrinoError — that gets
    flagged the next time validation runs (heartbeat or query) without
    blocking the API write that just succeeded."""
    try:
        validate_object_factory(
            factory, session=session, trino=_trino(), live_catalogs=None
        )
        session.commit()
    except TrinoError:
        pass


@bp.delete("/object-factories/<id_>")
def delete_object_factory(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        row = session.get(ObjectFactory, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        session.delete(row)
        session.commit()
        return jsonify({"deleted": id_})


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
