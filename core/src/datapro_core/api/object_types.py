"""Object Type CRUD + trait attachments.

Traits are a many-to-many: an object type can have any number from the
hardcoded registry in ``datapro_core.traits``. Adding or removing a
trait re-validates every factory that produces this type, because a
trait change can flip factories ok→broken (newly required trait_config
missing) or broken→ok (no longer required)."""

import uuid

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from datapro_core.factory_validator import validate_object_factory
from datapro_core.models import ObjectFactory, ObjectType, ObjectTypeTrait
from datapro_core.schemas import ObjectTypeCreateRequest, ObjectTypeUpdateRequest
from datapro_core.traits import known_trait_names
from datapro_core.trino_client import TrinoError

bp = Blueprint("object_types", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


def _parse_id(raw: str):
    """Return a UUID or a (response, status) tuple if invalid."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        return jsonify({"error": "invalid_id", "id": raw}), 400


@bp.get("/object-types")
def list_object_types():
    """List object types. Optional ``?search=foo`` filters case-insensitively on
    name + description."""
    search = (request.args.get("search") or "").strip()
    with _session() as session:
        q = session.query(ObjectType)
        if search:
            pattern = f"%{search.lower()}%"
            q = q.where(
                or_(
                    ObjectType.name.ilike(pattern),
                    ObjectType.description.ilike(pattern),
                )
            )
        rows = q.order_by(ObjectType.name).all()
        return jsonify([r.to_dict() for r in rows])


@bp.get("/object-types/<id_>")
def get_object_type(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        row = session.get(ObjectType, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        return jsonify(row.to_dict())


@bp.post("/object-types")
def create_object_type():
    try:
        payload = ObjectTypeCreateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    with _session() as session:
        row = ObjectType(name=payload.name, description=payload.description)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return (
                jsonify({"error": "already_exists", "name": payload.name}),
                409,
            )
        return jsonify(row.to_dict()), 201


@bp.patch("/object-types/<id_>")
def update_object_type(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    try:
        payload = ObjectTypeUpdateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_error_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    if payload.name is None and payload.description is None:
        return (
            jsonify(
                {
                    "error": "empty_patch",
                    "details": "Provide at least one of `name` or `description`.",
                }
            ),
            400,
        )

    with _session() as session:
        row = session.get(ObjectType, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        if payload.name is not None:
            row.name = payload.name
        if payload.description is not None:
            row.description = payload.description
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return (
                jsonify({"error": "already_exists", "name": payload.name}),
                409,
            )
        return jsonify(row.to_dict())


@bp.delete("/object-types/<id_>")
def delete_object_type(id_: str):
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        row = session.get(ObjectType, parsed)
        if row is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        session.delete(row)
        session.commit()
        return jsonify({"deleted": id_})


@bp.put("/object-types/<id_>/traits/<trait_name>")
def add_object_type_trait(id_: str, trait_name: str):
    """Idempotently attach a trait. PUT (not POST) because the operation
    has no body and re-running it is a no-op — easier to reason about
    from the AI tool layer than POST's "create exactly one" semantics.
    """
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    if trait_name not in known_trait_names():
        return (
            jsonify(
                {
                    "error": "unknown_trait",
                    "trait_name": trait_name,
                    "known": known_trait_names(),
                }
            ),
            400,
        )

    with _session() as session:
        otype = session.get(ObjectType, parsed)
        if otype is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        if trait_name in otype.trait_names:
            return jsonify(otype.to_dict())  # already present, no-op
        session.add(
            ObjectTypeTrait(object_type_id=otype.id, trait_name=trait_name)
        )
        try:
            session.commit()
        except IntegrityError:
            # Race against a concurrent add; the unique constraint caught it.
            session.rollback()
            session.refresh(otype)
            return jsonify(otype.to_dict())
        _revalidate_type_factories(session, otype)
        session.commit()
        session.refresh(otype)
        return jsonify(otype.to_dict())


@bp.delete("/object-types/<id_>/traits/<trait_name>")
def remove_object_type_trait(id_: str, trait_name: str):
    """Idempotent removal. 404 if the object type doesn't exist, but
    removing an absent trait is a 200 no-op for AI-tool friendliness."""
    parsed = _parse_id(id_)
    if isinstance(parsed, tuple):
        return parsed
    with _session() as session:
        otype = session.get(ObjectType, parsed)
        if otype is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        row = (
            session.query(ObjectTypeTrait)
            .where(
                ObjectTypeTrait.object_type_id == otype.id,
                ObjectTypeTrait.trait_name == trait_name,
            )
            .one_or_none()
        )
        if row is not None:
            session.delete(row)
            session.commit()
            _revalidate_type_factories(session, otype)
            session.commit()
            session.refresh(otype)
        return jsonify(otype.to_dict())


def _revalidate_type_factories(session, object_type: ObjectType) -> None:
    """Re-run factory_validator for every factory under this type so
    status badges reflect the trait change without waiting for the
    60s heartbeat. Trino unreachable → silently skip; the heartbeat
    will pick it up later. Validator handles its own status persistence.

    The session has ``expire_on_commit=False``, so after we just added or
    removed a trait row the cached ``trait_rows`` on the ObjectType
    instance is stale. Refresh it explicitly so the validator sees the
    new state (the validator reads ``factory.object_type.trait_names``
    via the identity-mapped same instance).
    """
    try:
        live = {s.name for s in _trino().list_catalogs()}
    except TrinoError:
        return
    session.refresh(object_type)
    factories = (
        session.query(ObjectFactory)
        .where(ObjectFactory.object_type_id == object_type.id)
        .all()
    )
    for f in factories:
        validate_object_factory(
            f, session=session, trino=_trino(), live_catalogs=live
        )


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
