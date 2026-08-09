"""Single source of truth for "is this object factory usable right now?".

Called from two places:

  1. The query planner (every time a query plans a type), so broken
     factories get excluded from the UNION and the user sees why.
  2. The periodic heartbeat (every 60s), so the UI's status badges
     stay accurate even when nobody's running queries.

A factory is OK if:

  - its data source's catalog is currently registered in Trino, and
  - Trino can introspect the data source's table (SHOW COLUMNS), and
  - either ``use_all_columns`` is true, or every entry in ``column_spec``
    is a real column on the live table.

Anything else → BROKEN, with ``last_error`` recording the reason. The
discovered (name, type) column list is returned alongside so the
planner can reuse it for SQL generation without a second Trino round
trip.

The validator only writes to the DB when the persisted status / error
actually change — periodic re-validation against a healthy factory is
free.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from datapro_core.catalog_validator import validate_catalog
from datapro_core.models import ObjectFactory, ObjectFactoryStatus
from datapro_core.traits import get_trait
from datapro_core.trino_client import TrinoClient, TrinoError


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a single validate_object_factory call."""

    valid: bool
    reason: str | None
    # Live (name, type) per column on the data source's table. Populated
    # whenever Trino successfully introspected the table — even if the
    # factory ends up invalid because of a column_spec mismatch.
    columns: list[tuple[str, str]]


def validate_object_factory(
    factory: ObjectFactory,
    *,
    session: Session,
    trino: TrinoClient,
    live_catalogs: set[str] | None = None,
) -> ValidationResult:
    """Check ``factory`` against the live Trino schema. Updates the
    factory's status + last_error in the DB iff they changed.

    ``live_catalogs`` may be passed in by the caller when validating many
    factories at once (avoids one ``SHOW CATALOGS`` round-trip per
    factory). If None, the validator fetches its own.
    """
    ds = factory.data_source
    if ds is None:
        # Shouldn't happen — FK is NOT NULL — but defensive.
        return _mark_and_return(
            session,
            factory,
            valid=False,
            reason="factory has no data source",
            columns=[],
        )

    # Step 0: a factory can't be any healthier than the catalog it
    # reads through. Delegate the (somewhat involved) catalog check —
    # live-Trino lookup, self-heal of stale broken/disabled, status
    # cascade — to the dedicated catalog_validator.
    cat = validate_catalog(
        ds.catalog_name,
        session=session,
        trino=trino,
        live_catalogs=live_catalogs,
    )
    if not cat.valid:
        # If Trino is just unreachable, defer judgment — don't flip the
        # factory to broken on a transient outage.
        if cat.catalog is None and "unreachable" in (cat.reason or ""):
            return ValidationResult(
                valid=False, reason=cat.reason, columns=[]
            )
        return _mark_and_return(
            session,
            factory,
            valid=False,
            reason=cat.reason or "catalog is not usable",
            columns=[],
        )

    # Step 1: can Trino read the table's columns?
    try:
        columns = trino.show_columns(ds.catalog_name, ds.schema_name, ds.table_name)
    except TrinoError as exc:
        return _mark_and_return(
            session,
            factory,
            valid=False,
            reason=(
                f"Trino can't read columns for "
                f"{ds.catalog_name}.{ds.schema_name}.{ds.table_name}: {exc}"
            ),
            columns=[],
        )

    # Step 2: column_spec entries must exist on the live table.
    if not factory.use_all_columns and factory.column_spec:
        valid_names = {name for (name, _ty) in columns}
        invalid = [c for c in factory.column_spec if c not in valid_names]
        if invalid:
            return _mark_and_return(
                session,
                factory,
                valid=False,
                reason=(
                    "column_spec references columns that don't exist on "
                    f"{ds.catalog_name}.{ds.schema_name}.{ds.table_name}: "
                    + ", ".join(invalid)
                ),
                columns=columns,
            )

    # Step 3: per-trait validation. Walk every trait the parent object
    # type has enabled; ask each trait to validate the factory's
    # ``trait_config[trait_name]`` against the live column list. First
    # failure marks the factory broken with a precise reason.
    type_traits = factory.object_type.trait_names if factory.object_type else []
    factory_config = factory.trait_config or {}
    for trait_name in type_traits:
        trait = get_trait(trait_name)
        if trait is None:
            # Object type has a trait the registry no longer knows about.
            # Could happen mid-deploy if a trait was removed; surface
            # plainly rather than crash.
            return _mark_and_return(
                session,
                factory,
                valid=False,
                reason=(
                    f"object type has unknown trait {trait_name!r}; "
                    "remove it from the type or upgrade Core"
                ),
                columns=columns,
            )
        cfg = factory_config.get(trait_name)
        if cfg is None:
            return _mark_and_return(
                session,
                factory,
                valid=False,
                reason=(
                    f"object type requires the {trait_name!r} trait but the "
                    f"factory has no trait_config[{trait_name!r}]"
                ),
                columns=columns,
            )
        err = trait.validate_factory_config(cfg, columns)
        if err is not None:
            return _mark_and_return(
                session,
                factory,
                valid=False,
                reason=f"trait {trait_name!r}: {err}",
                columns=columns,
            )

    return _mark_and_return(
        session, factory, valid=True, reason=None, columns=columns
    )


def validate_all(
    *,
    session: Session,
    trino: TrinoClient,
) -> dict[str, int]:
    """Validate every object factory in one pass. Used by the heartbeat.
    Returns a small summary so the caller can log progress."""
    # Fetch live catalogs once for the whole batch.
    try:
        live = {s.name for s in trino.list_catalogs()}
    except TrinoError:
        # Trino's down — leave statuses alone. Heartbeat will retry next tick.
        return {"checked": 0, "ok": 0, "broken": 0, "trino_unreachable": 1}

    rows = session.query(ObjectFactory).all()
    ok = broken = 0
    for r in rows:
        res = validate_object_factory(r, session=session, trino=trino, live_catalogs=live)
        if res.valid:
            ok += 1
        else:
            broken += 1
    session.commit()
    return {"checked": len(rows), "ok": ok, "broken": broken, "trino_unreachable": 0}


def _mark_and_return(
    session: Session,
    factory: ObjectFactory,
    *,
    valid: bool,
    reason: str | None,
    columns: list[tuple[str, str]],
) -> ValidationResult:
    """Persist status + last_error only when they changed, then return the
    structured result. updated_at would otherwise bump on every heartbeat
    tick — limiting writes keeps that field meaningful."""
    desired_status = ObjectFactoryStatus.OK if valid else ObjectFactoryStatus.BROKEN
    desired_error = None if valid else reason
    if factory.status != desired_status or factory.last_error != desired_error:
        factory.status = desired_status
        factory.last_error = desired_error
        session.flush()  # caller decides when to commit
    return ValidationResult(valid=valid, reason=reason, columns=columns)
