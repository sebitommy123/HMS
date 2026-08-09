import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  fetchTrinoState,
  listCatalogs,
  triggerReconcile,
  type ReconcileResult,
} from "@/api/catalogs";
import { DriftBadge } from "@/components/DriftBadge";
import { diff, summarize, type DriftRow } from "@/lib/drift";

export function State() {
  const qc = useQueryClient();

  const catalogs = useQuery({ queryKey: ["catalogs"], queryFn: listCatalogs });
  const trinoState = useQuery({ queryKey: ["trino-state"], queryFn: fetchTrinoState });
  const [onlyDrift, setOnlyDrift] = useState(false);
  const [lastReconcile, setLastReconcile] = useState<ReconcileResult | null>(null);

  const reconcile = useMutation({
    mutationFn: triggerReconcile,
    onSuccess: async (result) => {
      setLastReconcile(result);
      await qc.invalidateQueries({ queryKey: ["catalogs"] });
      await qc.invalidateQueries({ queryKey: ["trino-state"] });
    },
  });

  const rows = useMemo(
    () => diff(catalogs.data ?? [], trinoState.data ?? []),
    [catalogs.data, trinoState.data],
  );
  const summary = useMemo(() => summarize(rows), [rows]);
  const visible = onlyDrift ? rows.filter((r) => r.verdict !== "aligned") : rows;

  const ready = catalogs.isSuccess && trinoState.isSuccess;
  const failed = catalogs.isError || trinoState.isError;

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between gap-6">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">State</h1>
          <p className="text-sm text-zinc-600">
            Postgres (desired) vs Trino (actual). Catalogs that drift would be brought back into
            alignment by the next reconcile — manual or automatic.
          </p>
        </div>
        <button
          type="button"
          onClick={() => reconcile.mutate()}
          disabled={reconcile.isPending || !ready}
          className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="reconcile-button"
        >
          {reconcile.isPending ? "Reconciling…" : "Reconcile now"}
        </button>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Total" value={summary.total} />
        <Stat label="Aligned" value={summary.aligned} tone="ok" />
        <Stat label="Drift" value={summary.drift} tone={summary.drift > 0 ? "warn" : "muted"} />
        <Stat
          label="Connector mismatch"
          value={summary.byVerdict["connector-mismatch"]}
          tone={summary.byVerdict["connector-mismatch"] > 0 ? "bad" : "muted"}
        />
      </div>

      {failed && (
        <div
          className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"
          data-testid="state-error"
        >
          Couldn't load both sides — Core or Trino may be unreachable.
        </div>
      )}

      <div className="flex items-center gap-3">
        <label className="inline-flex items-center gap-2 text-sm text-zinc-600">
          <input
            type="checkbox"
            checked={onlyDrift}
            onChange={(e) => setOnlyDrift(e.target.checked)}
            data-testid="only-drift-toggle"
          />
          Only show drift
        </label>
      </div>

      {lastReconcile && (
        <ReconcileBanner result={lastReconcile} onDismiss={() => setLastReconcile(null)} />
      )}

      <div className="rounded-lg border border-zinc-200 bg-white" data-testid="state-panel">
        {ready && visible.length === 0 ? (
          <EmptyState onlyDrift={onlyDrift} />
        ) : ready ? (
          <DriftTable rows={visible} />
        ) : (
          <LoadingState />
        )}
      </div>
    </div>
  );
}

function DriftTable({ rows }: { rows: DriftRow[] }) {
  return (
    <div className="overflow-hidden">
      <table className="w-full text-sm" data-testid="drift-table">
        <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <Th>Name</Th>
            <Th>In Core</Th>
            <Th>In Trino</Th>
            <Th>Verdict</Th>
            <Th>Next action</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {rows.map((row) => (
            <tr key={row.name} className="hover:bg-zinc-50">
              <Td>
                {row.desired ? (
                  <Link
                    to={`/catalogs/${encodeURIComponent(row.name)}`}
                    className="font-medium text-zinc-900 hover:underline"
                  >
                    {row.name}
                  </Link>
                ) : (
                  <span className="font-medium text-zinc-900">{row.name}</span>
                )}
              </Td>
              <Td>
                <CellConnector row={row.desired?.connector} status={row.desired?.status} />
              </Td>
              <Td>
                <CellConnector row={row.actual?.connector} />
              </Td>
              <Td>
                <DriftBadge verdict={row.verdict} />
              </Td>
              <Td>
                <NextAction row={row} />
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CellConnector({ row, status }: { row: string | undefined; status?: string }) {
  if (!row) return <span className="text-zinc-400">—</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{row}</code>
      {status && status !== "enabled" && (
        <span className="text-xs uppercase tracking-wide text-zinc-500">({status})</span>
      )}
    </span>
  );
}

function NextAction({ row }: { row: DriftRow }) {
  switch (row.verdict) {
    case "aligned":
      return <span className="text-xs text-zinc-500">—</span>;
    case "missing-in-trino":
      return <span className="text-xs text-amber-700">CREATE in Trino</span>;
    case "extra-in-trino":
      return <span className="text-xs text-amber-700">DROP from Trino</span>;
    case "connector-mismatch":
      return <span className="text-xs text-red-700">DROP + CREATE</span>;
    case "broken":
      return <span className="text-xs text-red-700">Skipped (broken)</span>;
    case "disabled-but-present":
      return <span className="text-xs text-amber-700">DROP from Trino</span>;
  }
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-2.5 font-medium">{children}</th>;
}
function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-4 py-3 align-top">{children}</td>;
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "ok" | "warn" | "bad" | "muted";
}) {
  const toneClass =
    tone === "ok"
      ? "text-emerald-700"
      : tone === "warn"
        ? "text-amber-700"
        : tone === "bad"
          ? "text-red-700"
          : "text-zinc-900";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4" data-testid={`stat-${label.toLowerCase().replace(/\s+/g, "-")}`}>
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}

function EmptyState({ onlyDrift }: { onlyDrift: boolean }) {
  return (
    <div className="p-12 text-center" data-testid="empty-state">
      {onlyDrift ? (
        <>
          <h2 className="text-lg font-medium text-zinc-900">No drift</h2>
          <p className="mt-2 text-sm text-zinc-600">
            Every catalog Core knows about matches what Trino has.
          </p>
        </>
      ) : (
        <>
          <h2 className="text-lg font-medium text-zinc-900">Nothing here</h2>
          <p className="mt-2 text-sm text-zinc-600">
            Neither Core nor Trino has any catalogs.{" "}
            <Link to="/catalogs/new" className="underline underline-offset-2">Register one</Link>.
          </p>
        </>
      )}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-3 p-6" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function ReconcileBanner({
  result,
  onDismiss,
}: {
  result: ReconcileResult;
  onDismiss: () => void;
}) {
  const ok = result.all_ok;
  return (
    <div
      className={`rounded border p-3 text-sm ${
        ok
          ? "border-emerald-200 bg-emerald-50 text-emerald-900"
          : "border-red-200 bg-red-50 text-red-900"
      }`}
      data-testid="reconcile-banner"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <strong>Reconcile {ok ? "succeeded" : "completed with failures"}.</strong>{" "}
          {result.actions.length === 0
            ? "No changes were needed."
            : `${result.actions.length} action${result.actions.length === 1 ? "" : "s"}.`}
          {result.actions.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {result.actions.map((a, i) => (
                <li key={i}>
                  <code className="rounded bg-white/60 px-1.5 py-0.5">{a.kind}</code>{" "}
                  <span className="font-medium">{a.name}</span>{" "}
                  {a.ok ? "✓" : `✗ ${a.error}`}
                </li>
              ))}
            </ul>
          )}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs uppercase tracking-wide text-zinc-500 hover:text-zinc-700"
          aria-label="Dismiss reconcile banner"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
