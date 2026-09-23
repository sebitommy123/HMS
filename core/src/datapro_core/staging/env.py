"""Environment overlay resolution — the read side of the ``env`` axis.

An environment is an overlay over production. A read scoped to env ``E`` sees:

  * every env-``E`` row (its own creations + copy-on-write shadows of prod), minus
    tombstones (``deleted=True`` rows), plus
  * every prod row (``env_id IS NULL``) whose logical identity the env does not
    shadow or tombstone.

Prod scope (``env_id=None``) is just the prod rows.

The metadata graph is tiny, so the overlay is computed in Python (fetch the env
slice + the prod slice, merge by logical key) rather than as gnarly window-
function SQL. Correctness first; these tables are hundreds of rows, not millions.

Catalogs are special: an env catalog is registered in Trino's single global
namespace under a *mangled physical name* so it can't collide with prod or other
envs. ``Catalog.name`` holds that physical name (and is what Trino, data-source
discovery, and every SQL ``FROM`` clause use verbatim); ``Catalog.logical_name``
is the name the user sees and the overlay keys on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from datapro_core.models import (
    Catalog,
    DataSource,
    Environment,
    EnvStatus,
    ObjectFactory,
    ObjectType,
    ObjectTypeTrait,
)

EnvId = uuid.UUID | None


# --------------------------------------------------------------------------- #
# Physical catalog naming
# --------------------------------------------------------------------------- #


def physical_catalog_name(env_id: EnvId, logical_name: str) -> str:
    """The exact catalog name to register in Trino. Prod catalogs keep their
    bare logical name; env catalogs are namespaced so they never collide with
    prod or another env's same-named catalog."""
    if env_id is None:
        return logical_name
    return f"stg_{env_id.hex[:12]}_{logical_name}"


# --------------------------------------------------------------------------- #
# Environment lifecycle
# --------------------------------------------------------------------------- #


class EnvError(Exception):
    """Raised when an env id is supplied but doesn't resolve to an open env."""


def make_new_staging_env(session: Session, *, label: str = "") -> Environment:
    """Create a fresh, empty staging overlay. It sees all of prod immediately
    (overlay reads fall through to prod); it owns nothing yet."""
    env = Environment(label=label, status=EnvStatus.OPEN)
    session.add(env)
    session.flush()
    return env


def require_open_env(session: Session, env_id: EnvId) -> EnvId:
    """Validate a caller-supplied env id. ``None`` (prod) is always fine.
    A non-null id must name an OPEN environment, else EnvError."""
    if env_id is None:
        return None
    env = session.get(Environment, env_id)
    if env is None or env.status != EnvStatus.OPEN:
        raise EnvError(f"environment {env_id} is not open")
    return env_id


# --------------------------------------------------------------------------- #
# Overlay resolution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Overlaid:
    """Result of merging an env slice over the prod slice for one table."""

    rows: list  # effective rows visible in the env (no tombstones)
    shadowed_prod_ids: set  # prod row ids the env shadows or tombstones


def _overlay(env_rows: list, prod_rows: list, *, logical_key, prod_id_of) -> _Overlaid:
    """Generic merge: env rows win over prod rows sharing a logical key; env
    tombstones (deleted=True) hide the prod row and are themselves omitted.

    ``logical_key(row)`` returns the identity the overlay dedups on.
    ``prod_id_of(env_row)`` returns the prod row id an env row shadows, or None.
    """
    shadowed_keys = {logical_key(r) for r in env_rows}
    shadowed_ids = {pid for r in env_rows if (pid := prod_id_of(r)) is not None}
    effective = [r for r in env_rows if not getattr(r, "deleted", False)]
    for p in prod_rows:
        if logical_key(p) in shadowed_keys:
            continue
        effective.append(p)
    return _Overlaid(rows=effective, shadowed_prod_ids=shadowed_ids)


def catalogs_in(session: Session, env_id: EnvId) -> list[Catalog]:
    """Effective catalogs visible in ``env_id`` (prod ∪ env, env shadows prod
    by logical_name, tombstones hidden)."""
    if env_id is None:
        return session.query(Catalog).filter(Catalog.env_id.is_(None)).all()
    env_rows = session.query(Catalog).filter(Catalog.env_id == env_id).all()
    prod_rows = session.query(Catalog).filter(Catalog.env_id.is_(None)).all()
    return _overlay(
        env_rows, prod_rows,
        logical_key=lambda c: c.logical_name,
        prod_id_of=lambda c: None,  # catalogs shadow by logical_name, not id
    ).rows


def resolve_catalog(session: Session, env_id: EnvId, logical_name: str) -> Catalog | None:
    """The effective catalog row for a logical name in ``env_id`` scope."""
    for c in catalogs_in(session, env_id):
        if c.logical_name == logical_name:
            return c
    return None


def object_types_in(session: Session, env_id: EnvId) -> list[ObjectType]:
    if env_id is None:
        return session.query(ObjectType).filter(ObjectType.env_id.is_(None)).all()
    env_rows = session.query(ObjectType).filter(ObjectType.env_id == env_id).all()
    prod_rows = session.query(ObjectType).filter(ObjectType.env_id.is_(None)).all()
    return _overlay(
        env_rows, prod_rows,
        logical_key=lambda t: t.name,
        prod_id_of=lambda t: t.base_id,
    ).rows


def resolve_object_type(session: Session, env_id: EnvId, name: str) -> ObjectType | None:
    for t in object_types_in(session, env_id):
        if t.name == name:
            return t
    return None


def type_id_set(session: Session, env_id: EnvId, name: str) -> set:
    """Every object_type row id that means logical type ``name`` in ``env_id``
    scope — the prod row and/or the env shadow. The planner uses this to gather
    factories that attach to the logical type regardless of which physical type
    row they point at."""
    ids: set = set()
    q = session.query(ObjectType).filter(ObjectType.name == name)
    for t in q.all():
        if t.env_id is None or t.env_id == env_id:
            ids.add(t.id)
    return ids


def factories_in(session: Session, env_id: EnvId) -> list[ObjectFactory]:
    if env_id is None:
        return session.query(ObjectFactory).filter(ObjectFactory.env_id.is_(None)).all()
    env_rows = session.query(ObjectFactory).filter(ObjectFactory.env_id == env_id).all()
    prod_rows = session.query(ObjectFactory).filter(ObjectFactory.env_id.is_(None)).all()
    # Factories dedup by base_id (an env shadow overrides the prod factory it
    # points at). Env-native factories have base_id=None and never collide.
    return _overlay(
        env_rows, prod_rows,
        logical_key=lambda f: f.base_id or f.id,
        prod_id_of=lambda f: f.base_id,
    ).rows


def factories_for_type(
    session: Session, env_id: EnvId, type_name: str
) -> list[ObjectFactory]:
    """Effective factories for the logical object type ``type_name`` in env scope."""
    ids = type_id_set(session, env_id, type_name)
    return [f for f in factories_in(session, env_id) if f.object_type_id in ids]


def traits_for_type_row(session: Session, type_row: ObjectType) -> list[str]:
    """Trait names attached to a specific object_type row. Traits are self-
    contained per row (a shadow copies the prod traits it keeps), so this is
    just that row's directly-attached traits — no cross-env merge needed."""
    rows = (
        session.query(ObjectTypeTrait)
        .filter(ObjectTypeTrait.object_type_id == type_row.id)
        .all()
    )
    return sorted({r.trait_name for r in rows})


def data_sources_in(session: Session, env_id: EnvId) -> list[DataSource]:
    """Data sources visible in ``env_id``: those belonging to a catalog that is
    effective in this env (by physical name). Data sources have no env_id of
    their own — they inherit it from their (physical-named) catalog."""
    physical_names = {c.name for c in catalogs_in(session, env_id)}
    return [
        ds
        for ds in session.query(DataSource).all()
        if ds.catalog_name in physical_names
    ]


def resolve_data_source(
    session: Session, env_id: EnvId, catalog_logical: str, schema: str, table: str
) -> DataSource | None:
    """Find the data source at (catalog_logical, schema, table) in env scope,
    resolving the catalog's logical name to its physical name first."""
    catalog = resolve_catalog(session, env_id, catalog_logical)
    if catalog is None:
        return None
    return (
        session.query(DataSource)
        .filter(
            DataSource.catalog_name == catalog.name,
            DataSource.schema_name == schema,
            DataSource.table_name == table,
        )
        .one_or_none()
    )
