"""Staging-environment lifecycle: create, inspect, promote, drop.

An environment is the overlay the AI builds its changeset into. Core is
stateless about *actions* — the AI owns those and replays them as ``?env=<id>``
calls against the other endpoints. This blueprint just manages the overlay
itself and the atomic promote to prod.
"""

import uuid

from flask import Blueprint, current_app, jsonify, request

from datapro_core.models import Environment
from datapro_core.reconciler import reconcile
from datapro_core.staging.env import make_new_staging_env
from datapro_core.staging.promote import (
    PromoteConflict,
    check_conflicts,
    drop_env,
    promote_env,
)

bp = Blueprint("environments", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _trino():
    return current_app.extensions["trino"]


def _parse_id(raw: str):
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


@bp.post("/environments")
def create_environment():
    body = request.get_json(silent=True) or {}
    label = str(body.get("label", ""))
    with _session() as session:
        env = make_new_staging_env(session, label=label)
        session.commit()
        return jsonify(env.to_dict()), 201


@bp.get("/environments")
def list_environments():
    with _session() as session:
        rows = session.query(Environment).order_by(Environment.created_at).all()
        return jsonify([e.to_dict() for e in rows])


@bp.get("/environments/<id_>")
def get_environment(id_: str):
    parsed = _parse_id(id_)
    if parsed is None:
        return jsonify({"error": "invalid_id", "id": id_}), 400
    with _session() as session:
        env = session.get(Environment, parsed)
        if env is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        return jsonify(env.to_dict())


@bp.get("/environments/<id_>/conflicts")
def environment_conflicts(id_: str):
    """Dry-run the promote precondition so the AI/UI can warn the user that prod
    drifted before they commit to promoting."""
    parsed = _parse_id(id_)
    if parsed is None:
        return jsonify({"error": "invalid_id", "id": id_}), 400
    with _session() as session:
        env = session.get(Environment, parsed)
        if env is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        return jsonify({"conflicts": check_conflicts(session, parsed)})


@bp.post("/environments/<id_>/promote")
def promote_environment(id_: str):
    parsed = _parse_id(id_)
    if parsed is None:
        return jsonify({"error": "invalid_id", "id": id_}), 400
    with _session() as session:
        env = session.get(Environment, parsed)
        if env is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        try:
            result = promote_env(session, parsed)
        except PromoteConflict as exc:
            session.rollback()
            return jsonify({"error": "promote_conflict", "conflicts": exc.conflicts}), 409
        session.commit()
        # Converge Trino: register promoted catalogs (already registered while
        # the env was open) and drop any the promote removed.
        reconcile(session, _trino())
        return jsonify(
            {
                "promoted": id_,
                "result": {
                    "catalogs_promoted": result.catalogs_promoted,
                    "catalogs_deleted": result.catalogs_deleted,
                    "object_types_promoted": result.object_types_promoted,
                    "object_types_deleted": result.object_types_deleted,
                    "factories_promoted": result.factories_promoted,
                    "factories_deleted": result.factories_deleted,
                },
            }
        )


@bp.delete("/environments/<id_>")
def delete_environment(id_: str):
    parsed = _parse_id(id_)
    if parsed is None:
        return jsonify({"error": "invalid_id", "id": id_}), 400
    with _session() as session:
        env = session.get(Environment, parsed)
        if env is None:
            return jsonify({"error": "not_found", "id": id_}), 404
        drop_env(session, parsed)
        session.commit()
        # Drop the env's now-orphaned catalogs from Trino.
        reconcile(session, _trino())
        return jsonify({"deleted": id_})
