"""Promote a staging environment's delta into production — atomically.

Promotion is the ONLY way the AI's work reaches prod. It is not a diff between
two object graphs: the env already *is* the delta (every env row is something the
changeset created, shadowed, or tombstoned). Promotion re-parents that delta onto
prod in one transaction, guarded by a drift/collision precondition scoped to just
the prod entities the env touched.

Preconditions (abort the whole promote if any fail):
  * **collision** — an env-native new entity's logical name is now taken in prod
    (prod moved under us since the env was built).
  * **missing base** — a shadow/tombstone targets a prod row that no longer
    exists (someone else deleted it).

Effect (all-or-nothing; the caller's session commits once at the end):
  * new env entity  → flip env_id → NULL (becomes prod). Physical catalog names
    stay mangled; that's cosmetic, prod resolves them fine.
  * shadow          → copy its fields onto the prod row it shadows (base_id),
    keeping the prod row's id stable so references survive; drop the shadow.
  * tombstone       → delete the prod row it shadows.
Factories that referenced a promoted *shadow type* are repointed to the surviving
prod type id.

Trino convergence (register promoted catalogs / drop tombstoned ones) is left to a
``reconcile`` the caller runs after — the metadata flip is the atomic part.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from datapro_core.models import (
    Catalog,
    Environment,
    EnvStatus,
    ObjectFactory,
    ObjectType,
    ObjectTypeTrait,
)


class PromoteConflict(Exception):
    """Drift/collision precondition failed — promote aborted, nothing applied."""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)} promote conflict(s)")


@dataclass
class PromoteResult:
    catalogs_promoted: int = 0
    catalogs_deleted: int = 0
    object_types_promoted: int = 0
    object_types_deleted: int = 0
    factories_promoted: int = 0
    factories_deleted: int = 0
    touched_prod_ids: list[str] = field(default_factory=list)


def check_conflicts(session: Session, env_id: uuid.UUID) -> list[dict]:
    """The drift/collision precondition, side-effect free. Returns a list of
    conflict dicts (empty = clean). The AI shows these to the user; a clean
    result means the env's tests ran against a prod that hasn't drifted under
    the touched set."""
    conflicts: list[dict] = []

    # --- catalogs ---
    for c in session.query(Catalog).filter(Catalog.env_id == env_id).all():
        prod = (
            session.query(Catalog)
            .filter(Catalog.env_id.is_(None), Catalog.logical_name == c.logical_name)
            .one_or_none()
        )
        if c.deleted:
            if prod is None:
                conflicts.append(
                    {"kind": "missing_base", "entity": "catalog", "name": c.logical_name}
                )
        elif prod is not None:
            conflicts.append(
                {"kind": "collision", "entity": "catalog", "name": c.logical_name}
            )

    # --- object types ---
    for t in session.query(ObjectType).filter(ObjectType.env_id == env_id).all():
        if t.base_id is not None:
            if session.get(ObjectType, t.base_id) is None:
                conflicts.append(
                    {"kind": "missing_base", "entity": "object_type", "name": t.name}
                )
        else:
            prod = (
                session.query(ObjectType)
                .filter(ObjectType.env_id.is_(None), ObjectType.name == t.name)
                .one_or_none()
            )
            if prod is not None:
                conflicts.append(
                    {"kind": "collision", "entity": "object_type", "name": t.name}
                )

    # --- factories (shadow/tombstone base must still exist) ---
    for f in session.query(ObjectFactory).filter(ObjectFactory.env_id == env_id).all():
        if f.base_id is not None and session.get(ObjectFactory, f.base_id) is None:
            conflicts.append(
                {"kind": "missing_base", "entity": "object_factory", "id": str(f.id)}
            )

    return conflicts


def promote_env(session: Session, env_id: uuid.UUID) -> PromoteResult:
    """Apply the env's delta to prod. Raises PromoteConflict (nothing applied)
    if the precondition fails. Does NOT commit — the caller commits (then
    reconciles Trino)."""
    env = session.get(Environment, env_id)
    if env is None or env.status != EnvStatus.OPEN:
        raise PromoteConflict([{"kind": "env_not_open", "env_id": str(env_id)}])

    conflicts = check_conflicts(session, env_id)
    if conflicts:
        raise PromoteConflict(conflicts)

    result = PromoteResult()

    # --- object types first (factories depend on the shadow→prod id map) ---
    shadow_to_prod: dict[uuid.UUID, uuid.UUID] = {}
    for t in session.query(ObjectType).filter(ObjectType.env_id == env_id).all():
        if t.deleted:
            if t.base_id is not None:
                prod = session.get(ObjectType, t.base_id)
                if prod is not None:
                    session.delete(prod)
                    result.object_types_deleted += 1
                    result.touched_prod_ids.append(str(t.base_id))
            _delete_type_traits(session, t.id)
            session.delete(t)
        elif t.base_id is not None:
            # Shadow-merge onto the stable prod row.
            prod = session.get(ObjectType, t.base_id)
            prod.name = t.name
            prod.description = t.description
            _replace_type_traits(session, prod.id, from_type_id=t.id, env_id=env_id)
            shadow_to_prod[t.id] = prod.id
            session.delete(t)
            result.object_types_promoted += 1
            result.touched_prod_ids.append(str(prod.id))
        else:
            # Env-native new type → becomes prod.
            _reparent_type_traits(session, t.id)
            t.env_id = None
            result.object_types_promoted += 1

    # --- factories ---
    for f in session.query(ObjectFactory).filter(ObjectFactory.env_id == env_id).all():
        if f.deleted:
            if f.base_id is not None:
                prod = session.get(ObjectFactory, f.base_id)
                if prod is not None:
                    session.delete(prod)
                    result.factories_deleted += 1
            session.delete(f)
        elif f.base_id is not None:
            prod = session.get(ObjectFactory, f.base_id)
            prod.data_source_id = f.data_source_id
            prod.object_type_id = shadow_to_prod.get(f.object_type_id, f.object_type_id)
            prod.description = f.description
            prod.use_all_columns = f.use_all_columns
            prod.column_spec = list(f.column_spec or [])
            prod.trait_config = dict(f.trait_config or {})
            session.delete(f)
            result.factories_promoted += 1
        else:
            # Repoint at the surviving prod type if it pointed at a shadow.
            f.object_type_id = shadow_to_prod.get(f.object_type_id, f.object_type_id)
            f.env_id = None
            result.factories_promoted += 1

    # --- catalogs ---
    for c in session.query(Catalog).filter(Catalog.env_id == env_id).all():
        if c.deleted:
            prod = (
                session.query(Catalog)
                .filter(Catalog.env_id.is_(None), Catalog.logical_name == c.logical_name)
                .one_or_none()
            )
            if prod is not None:
                session.delete(prod)
                result.catalogs_deleted += 1
            session.delete(c)  # drop the tombstone itself
        else:
            c.env_id = None
            result.catalogs_promoted += 1

    env.status = EnvStatus.PROMOTED
    session.flush()
    return result


def drop_env(session: Session, env_id: uuid.UUID) -> None:
    """Tear down an env: delete all its rows (FK cascade from the environments
    row handles catalogs/types/factories/traits) and mark it discarded. The
    caller reconciles Trino afterwards to drop the env's now-orphaned catalogs."""
    env = session.get(Environment, env_id)
    if env is None:
        return
    # Deleting the Environment row cascades to every env-scoped row via
    # ON DELETE CASCADE on their env_id FKs.
    session.delete(env)
    session.flush()


def _delete_type_traits(session: Session, type_id: uuid.UUID) -> None:
    for tr in (
        session.query(ObjectTypeTrait)
        .filter(ObjectTypeTrait.object_type_id == type_id)
        .all()
    ):
        session.delete(tr)


def _reparent_type_traits(session: Session, type_id: uuid.UUID) -> None:
    """Flip an env-native type's own trait rows to prod alongside it."""
    for tr in (
        session.query(ObjectTypeTrait)
        .filter(ObjectTypeTrait.object_type_id == type_id)
        .all()
    ):
        tr.env_id = None


def _replace_type_traits(
    session: Session, prod_type_id: uuid.UUID, *, from_type_id: uuid.UUID, env_id: uuid.UUID
) -> None:
    """Make the prod type's traits exactly match the shadow's. We create *fresh*
    prod trait rows from the shadow's trait names rather than re-parenting the
    shadow's own rows — the shadow ObjectType's ``trait_rows`` relationship has
    ``delete-orphan`` cascade, so re-parented rows would be cascade-deleted when
    the shadow type is dropped."""
    shadow_traits = [
        tr.trait_name
        for tr in session.query(ObjectTypeTrait)
        .filter(ObjectTypeTrait.object_type_id == from_type_id)
        .all()
    ]
    for tr in (
        session.query(ObjectTypeTrait)
        .filter(ObjectTypeTrait.object_type_id == prod_type_id)
        .all()
    ):
        session.delete(tr)
    session.flush()
    for name in shadow_traits:
        session.add(
            ObjectTypeTrait(env_id=None, object_type_id=prod_type_id, trait_name=name)
        )
    session.flush()
