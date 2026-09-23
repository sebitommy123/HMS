"""HTTP surface over a conversation's staging changeset — for the UI.

The agent authors the changeset via tools; this blueprint lets the *user*
(through the UI) review it, edit it, test it, and promote it. Everything
delegates to ``ChangesetStore`` — the same service the agent tools use — so the
two surfaces can't diverge.
"""

from flask import Blueprint, current_app, jsonify, request

from datapro_ai.config import Config
from datapro_ai.models import Conversation
from datapro_ai.staging.changeset_store import ChangesetError, ChangesetStore
from datapro_ai.staging.core_client import CoreError, HttpCoreClient
from datapro_ai.staging.resolver import ResolverError

bp = Blueprint("changeset", __name__)


def _session():
    return current_app.extensions["db_session"]()


def _cfg() -> Config:
    return current_app.config["DATAPRO_AI"]


def _core() -> HttpCoreClient:
    return HttpCoreClient(_cfg().core_url)


def _load(session, conversation_id):
    c = session.get(Conversation, conversation_id)
    if c is None:
        return None, (jsonify({"error": "not_found", "id": str(conversation_id)}), 404)
    return ChangesetStore(session, c), None


@bp.get("/conversations/<uuid:conversation_id>/changeset")
def get_changeset(conversation_id):
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        return jsonify({"actions": store.raw(), "tests": store.tests()})


@bp.delete("/conversations/<uuid:conversation_id>/changeset/actions/<action_id>")
def remove_action(conversation_id, action_id):
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        try:
            store.remove(action_id)
        except ChangesetError as exc:
            return jsonify({"error": "bad_request", "details": str(exc)}), 400
        return jsonify({"actions": store.raw()})


@bp.post("/conversations/<uuid:conversation_id>/changeset/reorder")
def reorder(conversation_id):
    body = request.get_json(silent=True) or {}
    ids = body.get("action_ids")
    if not isinstance(ids, list):
        return jsonify({"error": "bad_request", "details": "action_ids must be a list"}), 400
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        try:
            store.reorder([str(i) for i in ids])
        except ChangesetError as exc:
            return jsonify({"error": "bad_request", "details": str(exc)}), 400
        return jsonify({"actions": store.raw()})


@bp.post("/conversations/<uuid:conversation_id>/changeset/clear")
def clear(conversation_id):
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        store.clear()
        return jsonify({"actions": store.raw()})


@bp.post("/conversations/<uuid:conversation_id>/changeset/query-stage")
def query_stage(conversation_id):
    """Build a fresh staging env from the changeset, run one semantic query, tear
    it down. Powers the UI's "try it" preview."""
    body = request.get_json(silent=True) or {}
    if not body.get("from"):
        return jsonify({"error": "bad_request", "details": "`from` is required"}), 400
    core = _core()
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        try:
            env = store.materialize(core)
        except (ResolverError, CoreError) as exc:
            return jsonify({"error": "stage_build_failed", "details": str(exc)}), 502
        try:
            result = core.query(env, {"from": body["from"], "limit": int(body.get("limit", 20))})
        except CoreError as exc:
            return jsonify({"error": "stage_query_failed", "details": str(exc)}), 502
        finally:
            try:
                core.drop_env(env)
            except CoreError:
                pass
    return jsonify(result)


@bp.post("/conversations/<uuid:conversation_id>/changeset/build-and-test")
def build_and_test(conversation_id):
    """Build a fresh staging env and run every saved acceptance test."""
    core = _core()
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        tests = store.tests()
        try:
            env = store.materialize(core)
        except (ResolverError, CoreError) as exc:
            return jsonify({"error": "stage_build_failed", "details": str(exc)}), 502
        results = []
        try:
            for t in tests:
                results.append(_run_test(core, env, t))
        finally:
            try:
                core.drop_env(env)
            except CoreError:
                pass
    return jsonify({
        "tests": results,
        "all_passed": all(r["passed"] for r in results) if results else None,
    })


@bp.post("/conversations/<uuid:conversation_id>/changeset/apply")
def apply(conversation_id):
    """Promote the changeset to production, atomically. Returns the promote
    result, or a 409 with a conflicts list if production drifted."""
    core = _core()
    with _session() as session:
        store, err = _load(session, conversation_id)
        if err:
            return err
        try:
            result = store.apply(core)
        except CoreError as exc:
            b = getattr(exc, "body", None)
            if isinstance(b, dict) and b.get("error") == "promote_conflict":
                return jsonify({"applied": False, "conflicts": b.get("conflicts", [])}), 409
            return jsonify({"error": "apply_failed", "details": str(exc)}), 502
        except ResolverError as exc:
            return jsonify({"error": "stage_build_failed", "details": str(exc)}), 502
        return jsonify({"applied": True, "result": result.get("result", {})})


def _run_test(core: HttpCoreClient, env: str, test: dict) -> dict:
    try:
        result = core.query(env, {"from": test["from"], "limit": 100})
        n = len(result.get("objects", []))
        return {"name": test.get("name"), "passed": n >= int(test.get("min_objects", 1)), "objects": n}
    except CoreError as exc:
        return {"name": test.get("name"), "passed": False, "error": str(exc)}
