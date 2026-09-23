"""Replay a changeset's action list into a staging env — the AI-owned resolver.

Core knows nothing about actions. The AI holds the ordered action list and, to
materialize a stage, creates a fresh env and *replays* each action as an
env-scoped Core call. Create-actions mint real ids that later actions reference
by handle — the resolution is just an in-memory ``handle -> entity`` map built
from the real returned ids, so re-materializing onto a moved-on prod rebinds
cleanly (no staging id is ever baked into the changeset).

``materialize`` builds and returns a live env id (the agent tests against it).
``apply`` builds a fresh env off *current* prod, then promotes it — so the
resolver never has a prod-scoped code path; prod only ever changes via the
atomic promote of a green env.
"""

from __future__ import annotations

from datapro_ai.staging.actions import Changeset
from datapro_ai.staging.core_client import CoreClient, CoreError


class ResolverError(Exception):
    """A changeset that can't be replayed — e.g. an action references a handle
    no earlier create-action declared (a reorder/delete left a dangling ref)."""


class _Handle:
    __slots__ = ("kind", "id", "logical")

    def __init__(self, kind: str, id: str, logical: str | None = None):
        self.kind = kind
        self.id = id
        self.logical = logical


class Resolver:
    def __init__(self, core: CoreClient):
        self.core = core

    # -- public ------------------------------------------------------------

    def materialize(self, changeset: Changeset, *, label: str = "") -> str:
        """Create a fresh env and replay every action into it. Returns the env
        id. Raises ResolverError on a structurally-broken changeset (the env is
        dropped so we don't leak a half-built overlay)."""
        env = self.core.make_env(label=label)
        handles: dict[str, _Handle] = {}
        try:
            for action in changeset.actions:
                self._replay(env, action, handles)
        except (ResolverError, CoreError):
            # Structural/Core failure — tear the half-built env down and re-raise
            # so the caller (agent) sees exactly what broke.
            try:
                self.core.drop_env(env)
            except CoreError:
                pass
            raise
        return env

    def apply(self, changeset: Changeset, *, label: str = "") -> dict:
        """Rebuild the changeset into a *fresh* env off current prod, then
        promote it atomically. Returns the promote result. The env is dropped on
        a promote conflict so nothing is left dangling."""
        env = self.materialize(changeset, label=label)
        try:
            return self.core.promote_env(env)
        except CoreError:
            try:
                self.core.drop_env(env)
            except CoreError:
                pass
            raise

    # -- ref resolution ----------------------------------------------------

    def _resolve(self, ref, handles: dict[str, _Handle], expected_kind: str) -> str:
        """A Ref → the concrete Core id it names. ProdRef carries the id
        directly; ActionRef looks it up in the handle map built so far."""
        if ref.source == "prod":
            return ref.id
        h = handles.get(ref.handle)
        if h is None:
            raise ResolverError(
                f"action references handle {ref.handle!r} that no earlier "
                f"create-action produced (reordered or deleted?)"
            )
        return h.id

    def _catalog_logical(self, ref, handles: dict[str, _Handle]) -> str:
        """The *logical* catalog name a DataSourceRef points at (needed to
        resolve a data source in env scope). ProdRef.id is the logical name for
        a prod catalog; an ActionRef resolves to the create-action's logical."""
        if ref.source == "prod":
            return ref.id
        h = handles.get(ref.handle)
        if h is None or h.kind != "catalog":
            raise ResolverError(
                f"data source references catalog handle {getattr(ref, 'handle', ref)!r} "
                f"that no earlier create_catalog produced"
            )
        return h.logical or h.id

    # -- per-action replay -------------------------------------------------

    def _replay(self, env: str, action, handles: dict[str, _Handle]) -> None:
        op = action.op
        if op == "create_catalog":
            res = self.core.create_catalog(
                env, action.name, action.connector, action.properties, action.source
            )
            # Store the physical name (Core's id for the catalog) + logical name.
            handles[action.handle] = _Handle(
                "catalog", res.get("physical_name", action.name), logical=action.name
            )
        elif op == "update_catalog":
            name = self._resolve_catalog_name(action.target, handles)
            fields = _present(action, ("connector", "properties"))
            self.core.update_catalog(env, name, **fields)
        elif op == "delete_catalog":
            name = self._resolve_catalog_name(action.target, handles)
            self.core.delete_catalog(env, name)
        elif op == "set_flex_module":
            name = self._resolve_catalog_name(action.target, handles)
            self.core.set_flex_module(env, name, action.source)
        elif op == "create_object_type":
            res = self.core.create_object_type(env, action.name, action.description)
            handles[action.handle] = _Handle("object_type", res["id"])
        elif op == "update_object_type":
            tid = self._resolve(action.target, handles, "object_type")
            self.core.update_object_type(env, tid, **_present(action, ("name", "description")))
        elif op == "delete_object_type":
            tid = self._resolve(action.target, handles, "object_type")
            self.core.delete_object_type(env, tid)
        elif op == "add_trait":
            tid = self._resolve(action.target, handles, "object_type")
            self.core.add_trait(env, tid, action.trait)
        elif op == "remove_trait":
            tid = self._resolve(action.target, handles, "object_type")
            self.core.remove_trait(env, tid, action.trait)
        elif op == "create_object_factory":
            ds = action.data_source
            catalog_logical = self._catalog_logical(ds.catalog, handles)
            ds_id = self.core.resolve_data_source(
                env, catalog_logical, ds.schema_name, ds.table
            )
            type_id = self._resolve(action.object_type, handles, "object_type")
            res = self.core.create_object_factory(
                env,
                data_source_id=ds_id,
                object_type_id=type_id,
                description=action.description,
                use_all_columns=action.use_all_columns,
                column_spec=action.column_spec,
                trait_config=action.trait_config,
            )
            handles[action.handle] = _Handle("object_factory", res["id"])
        elif op == "update_object_factory":
            fid = self._resolve(action.target, handles, "object_factory")
            fields = _present(
                action, ("description", "use_all_columns", "column_spec", "trait_config")
            )
            self.core.update_object_factory(env, fid, **fields)
        elif op == "delete_object_factory":
            fid = self._resolve(action.target, handles, "object_factory")
            self.core.delete_object_factory(env, fid)
        else:  # pragma: no cover - Changeset validation forbids unknown ops
            raise ResolverError(f"unknown action op {op!r}")

    def _resolve_catalog_name(self, ref, handles: dict[str, _Handle]) -> str:
        """Catalogs are addressed by *logical* name in the API, so resolve a ref
        to that logical name (not an internal id)."""
        return self._catalog_logical(ref, handles)


def _present(action, keys) -> dict:
    """Fields that are not None on an update action → the PATCH body. A
    whole-value replace, matching Core's PATCH semantics."""
    out = {}
    for k in keys:
        v = getattr(action, k, None)
        if v is not None:
            out[k] = v
    return out
