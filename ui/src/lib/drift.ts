import type { Catalog, TrinoCatalogRow } from "@/api/catalogs";

/**
 * Per-row drift verdict between Core's desired state (Postgres) and Trino's
 * actual state. Mirrors the partial-sync classes that
 * core/tests/integration/test_partial_sync.py covers.
 */
export type DriftVerdict =
  /** Postgres + Trino agree on both name and connector. */
  | "aligned"
  /** Postgres has the row enabled, Trino doesn't have it. Reconcile would CREATE. */
  | "missing-in-trino"
  /** Trino has it, Postgres doesn't. Reconcile would DROP. */
  | "extra-in-trino"
  /** Same name in both, different connector. Reconcile would DROP + CREATE. */
  | "connector-mismatch"
  /** Postgres has the row marked broken — reconciler skips it. */
  | "broken"
  /** Postgres has it disabled but Trino still has it. Reconcile would DROP. */
  | "disabled-but-present";

export interface DriftRow {
  name: string;
  desired: Catalog | null;
  actual: TrinoCatalogRow | null;
  verdict: DriftVerdict;
}

export function diff(catalogs: Catalog[], trinoState: TrinoCatalogRow[]): DriftRow[] {
  const byName = new Map<string, { desired: Catalog | null; actual: TrinoCatalogRow | null }>();

  for (const c of catalogs) {
    byName.set(c.name, { desired: c, actual: null });
  }
  for (const t of trinoState) {
    const existing = byName.get(t.name);
    if (existing) {
      existing.actual = t;
    } else {
      byName.set(t.name, { desired: null, actual: t });
    }
  }

  const rows: DriftRow[] = [];
  for (const [name, { desired, actual }] of byName) {
    rows.push({ name, desired, actual, verdict: verdictFor(desired, actual) });
  }
  rows.sort((a, b) => a.name.localeCompare(b.name));
  return rows;
}

function verdictFor(
  desired: Catalog | null,
  actual: TrinoCatalogRow | null,
): DriftVerdict {
  if (!desired && actual) return "extra-in-trino";
  if (desired && !actual) {
    if (desired.status === "broken") return "broken";
    if (desired.status === "disabled") return "aligned"; // not desired in Trino either
    return "missing-in-trino";
  }
  // Both present.
  if (desired && actual) {
    if (desired.status === "broken") return "broken";
    if (desired.status === "disabled") return "disabled-but-present";
    if (desired.connector !== actual.connector) return "connector-mismatch";
    return "aligned";
  }
  // Should never hit — diff() only inserts when at least one side is present.
  return "aligned";
}

export interface DriftSummary {
  total: number;
  aligned: number;
  drift: number;
  byVerdict: Record<DriftVerdict, number>;
}

export function summarize(rows: DriftRow[]): DriftSummary {
  const byVerdict: Record<DriftVerdict, number> = {
    aligned: 0,
    "missing-in-trino": 0,
    "extra-in-trino": 0,
    "connector-mismatch": 0,
    broken: 0,
    "disabled-but-present": 0,
  };
  for (const r of rows) byVerdict[r.verdict] += 1;
  return {
    total: rows.length,
    aligned: byVerdict.aligned,
    drift: rows.length - byVerdict.aligned,
    byVerdict,
  };
}
