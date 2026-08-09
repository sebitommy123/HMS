"""Diff desired (Postgres) vs actual (Trino) catalog state and apply convergence actions.

Two things converge here on every reconcile pass:

1. **Catalogs** — CREATE/DROP so Trino's registered catalogs match Postgres.
2. **Data sources** — for each registered catalog, discover its tables via
   Trino introspection and sync the ``data_sources`` table to match. Data
   sources are sync-owned: users never create them by hand.

What we DO detect (catalogs):
- Catalog in Postgres but not in Trino → CREATE
- Catalog in Trino but not in Postgres → DROP
- Same name in both but different connector → DROP + CREATE (in that order)

What we do NOT detect (catalogs):
- Same name + same connector + different WITH-clause properties. Trino's
  `system.metadata.catalogs` doesn't expose properties, so we can't see property
  drift. A future PATCH /catalogs API will need to force a DROP+CREATE on
  property changes explicitly. Documented in README as a known Phase-0 limit.

Data-source sync rules (per registered catalog):
- Table in Trino, not in DB → INSERT (status=active)
- Table in Trino that was marked deleted → revive (status=active)
- Table gone from Trino, not referenced by any factory → hard DELETE
- Table gone from Trino, still referenced by a factory → mark status=deleted
  (keep the row so the operator can resolve their factories)
- ``information_schema.tables`` itself fails → the catalog's backing store is
  down; mark the catalog DOWN and leave its data sources untouched.
"""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from datapro_core.models import (
    Catalog,
    CatalogStatus,
    DataSource,
    DataSourceStatus,
    ObjectFactory,
)
from datapro_core.trino_client import TrinoCatalogSnapshot, TrinoClient, TrinoError


class ActionKind(StrEnum):
    CREATE = "create"
    DROP = "drop"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    name: str
    connector: str | None = None
    properties: dict[str, str] | None = None


@dataclass(frozen=True)
class ActionResult:
    action: Action
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class ReconcileResult:
    actions: list[ActionResult]

    @property
    def all_ok(self) -> bool:
        return all(r.ok for r in self.actions)


def plan(
    desired: list[Catalog], actual: list[TrinoCatalogSnapshot]
) -> list[Action]:
    """Pure diff: what CREATE/DROP actions converge actual to desired.

    ENABLED and DOWN rows are both considered desired-in-Trino: a DOWN catalog
    is registered fine (only its backing store is unhealthy), so we keep it
    registered and re-CREATE it if it's somehow missing. Disabled/broken rows
    aren't desired; disabled rows aren't dropped from Trino until the API layer
    explicitly deletes them.
    """
    actions: list[Action] = []

    desired_enabled = {
        c.name: c
        for c in desired
        if c.status in (CatalogStatus.ENABLED, CatalogStatus.DOWN)
    }
    actual_by_name = {s.name: s for s in actual}

    for name, cat in desired_enabled.items():
        snap = actual_by_name.get(name)
        if snap is None:
            actions.append(_create_action(cat))
        elif snap.connector != cat.connector:
            # Name match but wrong connector. Drop-then-create to converge.
            actions.append(Action(kind=ActionKind.DROP, name=name))
            actions.append(_create_action(cat))
        # else: same name + same connector → assume in sync (property drift is
        # undetectable from Trino's introspection; see module docstring).

    for name in actual_by_name:
        if name not in desired_enabled:
            actions.append(Action(kind=ActionKind.DROP, name=name))

    return actions


def _create_action(cat: Catalog) -> "Action":
    return Action(
        kind=ActionKind.CREATE,
        name=cat.name,
        connector=cat.connector,
        properties=dict(cat.properties or {}),
    )


def apply(
    actions: list[Action], trino: TrinoClient, session: Session
) -> list[ActionResult]:
    results: list[ActionResult] = []
    for action in actions:
        try:
            if action.kind == ActionKind.CREATE:
                assert action.connector is not None, "CREATE action requires connector"
                trino.create_catalog(
                    action.name, action.connector, action.properties or {}
                )
                _mark_status(session, action.name, CatalogStatus.ENABLED, last_error=None)
            elif action.kind == ActionKind.DROP:
                trino.drop_catalog(action.name)
            results.append(ActionResult(action=action, ok=True))
        except TrinoError as exc:
            if action.kind == ActionKind.CREATE:
                _mark_status(session, action.name, CatalogStatus.BROKEN, last_error=str(exc))
            results.append(ActionResult(action=action, ok=False, error=str(exc)))
    session.commit()
    return results


def reconcile(session: Session, trino: TrinoClient) -> ReconcileResult:
    desired = session.query(Catalog).all()
    actual = trino.list_catalogs()
    actions = plan(desired, actual)
    results = apply(actions, trino, session)

    # Now that catalogs are converged, sync each registered catalog's data
    # sources against the tables Trino actually exposes. Only catalogs that
    # are both desired (enabled/down) AND currently registered in Trino are
    # worth introspecting — a BROKEN catalog (CREATE failed) has no tables.
    desired_registered = {
        c.name for c in desired
        if c.status in (CatalogStatus.ENABLED, CatalogStatus.DOWN)
    }
    registered = {s.name for s in actual}
    for r in results:
        if not r.ok:
            continue
        if r.action.kind == ActionKind.CREATE:
            registered.add(r.action.name)
        elif r.action.kind == ActionKind.DROP:
            registered.discard(r.action.name)
    for name in sorted(desired_registered & registered):
        sync_data_sources(session, trino, name)
    session.commit()

    return ReconcileResult(actions=results)


def sync_data_sources(session: Session, trino: TrinoClient, catalog_name: str) -> None:
    """Converge the ``data_sources`` rows for one catalog against the tables
    Trino currently exposes. Mutates the session but does NOT commit — the
    caller commits once after syncing every catalog.

    If Trino can't enumerate the catalog's tables, the catalog's backing
    store is down: mark the catalog DOWN and leave its data sources alone
    (we don't know what's really there, so we don't delete anything).

    Deletions are guarded hard, because the object_factories -> data_sources
    FK is ON DELETE CASCADE — a wrong hard-delete here silently destroys the
    operator's factories. Two safeguards:
      1. A referenced source is NEVER hard-deleted; it's marked ``deleted``
         and kept, so the factory survives for the operator to resolve.
      2. An **empty** enumeration never deletes anything when the catalog
         already has data sources. A flaky/erroring connector can report
         zero tables instead of raising; going from "N tables" to "zero" in
         one pass is treated as an untrustworthy read, not a real drop-all.
         (Partial under-reporting is not fully defended against, but the
         worst case there is limited to hard-deleting *unreferenced* sources;
         referenced ones are still only soft-deleted per safeguard 1.)
    """
    try:
        discovered = set(trino.list_tables(catalog_name))
    except TrinoError as exc:
        _mark_status(session, catalog_name, CatalogStatus.DOWN, last_error=str(exc))
        return

    # Enumeration succeeded → the store is reachable. If the catalog was
    # flagged DOWN by a previous pass, it has recovered.
    catalog = session.get(Catalog, catalog_name)
    if catalog is not None and catalog.status == CatalogStatus.DOWN:
        catalog.status = CatalogStatus.ENABLED
        catalog.last_error = None

    existing = (
        session.query(DataSource)
        .filter(DataSource.catalog_name == catalog_name)
        .all()
    )
    existing_by_key = {(ds.schema_name, ds.table_name): ds for ds in existing}

    # Additions + revivals.
    for schema, table in discovered:
        ds = existing_by_key.get((schema, table))
        if ds is None:
            session.add(
                DataSource(
                    catalog_name=catalog_name,
                    schema_name=schema,
                    table_name=table,
                    status=DataSourceStatus.ACTIVE,
                )
            )
        elif ds.status != DataSourceStatus.ACTIVE:
            # Table reappeared — revive it.
            ds.status = DataSourceStatus.ACTIVE

    # Safeguard 2: never delete on the strength of an empty read when we
    # already have sources. Additions above are a no-op in this case anyway.
    if not discovered and existing:
        return

    # Disappearances: hard-delete if unreferenced, else mark deleted.
    for (schema, table), ds in existing_by_key.items():
        if (schema, table) in discovered:
            continue
        referenced = (
            session.query(ObjectFactory.id)
            .filter(ObjectFactory.data_source_id == ds.id)
            .first()
            is not None
        )
        if referenced:
            if ds.status != DataSourceStatus.DELETED:
                ds.status = DataSourceStatus.DELETED
        else:
            session.delete(ds)


def reconcile_one(
    catalog_name: str, *, session: Session, trino: TrinoClient
) -> ReconcileResult:
    """Refresh a single catalog's sync state with Trino. Cheaper than
    ``reconcile`` when the caller only cares about one row (e.g. the
    factory validator wants to be sure ``catalog.status`` is fresh
    before reading it).

    Runs the same plan/apply pipeline but scoped to actions that name
    this catalog — both the desired side (the catalog row, if it
    exists) and the actual side (any matching Trino snapshot)."""
    desired_row = session.get(Catalog, catalog_name)
    desired = [desired_row] if desired_row is not None else []
    actual_all = trino.list_catalogs()
    actual = [s for s in actual_all if s.name == catalog_name]
    actions = plan(desired, actual)
    results = apply(actions, trino, session)
    return ReconcileResult(actions=results)


def _mark_status(session: Session, name: str, status: CatalogStatus, last_error: str | None):
    row = session.get(Catalog, name)
    if row is None:
        return
    row.status = status
    row.last_error = last_error
