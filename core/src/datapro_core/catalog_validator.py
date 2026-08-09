"""Single source of truth for "is this catalog currently usable?".

The factory validator delegates here for its prerequisite check, the
heartbeat can use it to refresh status outside of reconciles, and any
future surface that needs the same answer (debug console, MCP, ...)
gets it without re-implementing the rules.

A catalog is valid if:

  - the row exists in Postgres, and
  - the row's status is ENABLED (or self-healable to ENABLED from a
    stale broken/disabled value), and
  - Trino currently has the catalog registered.

A DOWN catalog (registered in Trino but its backing store failed
introspection) is reported invalid with a "backing store is down"
reason — it's registered, but nothing can be read through it until the
reconciler's data-source sync sees the store recover.

When Trino is unreachable entirely, the validator returns a soft fail
(valid=False, but ``catalog`` is None) so callers can distinguish "this
catalog is broken" from "we just can't tell right now" and avoid
flipping anything to broken on the basis of a transient outage.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from datapro_core.models import Catalog, CatalogStatus
from datapro_core.trino_client import TrinoClient, TrinoError


@dataclass(frozen=True)
class CatalogValidationResult:
    valid: bool
    reason: str | None
    # The (potentially refreshed) Catalog row. None when validation
    # couldn't even look it up (catalog row missing, or Trino
    # unreachable so we deferred judgment).
    catalog: Catalog | None


def validate_catalog(
    catalog_name: str,
    *,
    session: Session,
    trino: TrinoClient,
    live_catalogs: set[str] | None = None,
) -> CatalogValidationResult:
    """Check + self-heal a catalog's sync state. Writes to the catalog
    row only when the persisted status disagrees with reality.

    ``live_catalogs`` may be passed in by callers that already have it
    (e.g. validating many factories under different catalogs in one
    pass) so we don't re-query Trino for every call.
    """
    if live_catalogs is None:
        try:
            live_catalogs = {s.name for s in trino.list_catalogs()}
        except TrinoError as exc:
            # Trino itself is unreachable. Don't flip statuses on a
            # transient outage — defer judgment so callers can soft-skip
            # for this attempt without persisting anything.
            return CatalogValidationResult(
                valid=False,
                reason=f"Trino unreachable: {exc}",
                catalog=None,
            )

    catalog = session.get(Catalog, catalog_name)
    if catalog is None:
        return CatalogValidationResult(
            valid=False,
            reason=f"catalog {catalog_name!r} does not exist",
            catalog=None,
        )

    catalog_in_trino = catalog_name in live_catalogs

    # Self-heal stale broken/disabled. If Trino currently has the
    # catalog but our row says otherwise (left over from an old failed
    # reconcile, manual out-of-band fix, etc.), reality wins.
    #
    # DOWN is deliberately excluded: it reflects backing-store health
    # (introspection failing), not Trino registration, so the mere fact
    # that Trino has the catalog registered doesn't clear it. Only the
    # reconciler's data-source sync flips DOWN back to ENABLED once it can
    # actually enumerate the catalog's tables again.
    if catalog_in_trino and catalog.status in (
        CatalogStatus.BROKEN,
        CatalogStatus.DISABLED,
    ):
        catalog.status = CatalogStatus.ENABLED
        catalog.last_error = None
        session.flush()

    if catalog.status == CatalogStatus.BROKEN:
        detail = (
            f": {catalog.last_error}"
            if catalog.last_error
            else " (no error recorded)"
        )
        return CatalogValidationResult(
            valid=False,
            reason=f"catalog {catalog_name!r} is in broken state{detail}",
            catalog=catalog,
        )
    if catalog.status == CatalogStatus.DISABLED:
        return CatalogValidationResult(
            valid=False,
            reason=f"catalog {catalog_name!r} is disabled",
            catalog=catalog,
        )
    if catalog.status == CatalogStatus.DOWN:
        detail = f": {catalog.last_error}" if catalog.last_error else ""
        return CatalogValidationResult(
            valid=False,
            reason=f"catalog {catalog_name!r} backing store is down{detail}",
            catalog=catalog,
        )

    # Status says enabled — last check is whether Trino actually has it.
    # (Inverse staleness: row says enabled but Trino dropped it.)
    if not catalog_in_trino:
        return CatalogValidationResult(
            valid=False,
            reason=(
                f"catalog {catalog_name!r} is not currently registered in Trino"
            ),
            catalog=catalog,
        )

    return CatalogValidationResult(valid=True, reason=None, catalog=catalog)
