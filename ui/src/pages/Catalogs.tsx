import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listCatalogs } from "@/api/catalogs";
import { StatusBadge } from "@/components/StatusBadge";
import { relativeTime, summarizeProperties } from "@/lib/format";

export function Catalogs() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["catalogs"],
    queryFn: listCatalogs,
  });

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Catalogs</h1>
          <p className="text-sm text-zinc-600">
            All catalogs Core knows about — the source of truth for what Trino should have.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isFetching ? "Refreshing…" : "Refresh"}
          </button>
          <Link
            to="/catalogs/new-flex"
            className="rounded border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-50"
            data-testid="new-flex-catalog-button"
          >
            New flex catalog
          </Link>
          <Link
            to="/catalogs/new"
            className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800"
            data-testid="new-catalog-button"
          >
            New catalog
          </Link>
        </div>
      </header>

      <div className="rounded-lg border border-zinc-200 bg-white" data-testid="catalogs-panel">
        {isLoading ? (
          <Skeleton />
        ) : isError ? (
          <ErrorState message={(error as Error)?.message ?? "Unknown error"} />
        ) : !data || data.length === 0 ? (
          <EmptyState />
        ) : (
          <CatalogsTable rows={data} />
        )}
      </div>
    </div>
  );
}

function CatalogsTable({ rows }: { rows: Awaited<ReturnType<typeof listCatalogs>> }) {
  return (
    <div className="overflow-hidden">
      <table className="w-full text-sm" data-testid="catalogs-table">
        <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <Th>Name</Th>
            <Th>Connector</Th>
            <Th>Status</Th>
            <Th>Properties</Th>
            <Th>Factories</Th>
            <Th>Updated</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {rows.map((row) => (
            <tr key={row.name} className="hover:bg-zinc-50">
              <Td>
                <Link
                  to={`/catalogs/${encodeURIComponent(row.name)}`}
                  className="font-medium text-zinc-900 hover:underline"
                  data-testid={`catalog-link-${row.name}`}
                >
                  {row.name}
                </Link>
              </Td>
              <Td>
                <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{row.connector}</code>
              </Td>
              <Td>
                <div className="flex flex-col gap-1">
                  <StatusBadge status={row.status} />
                  {row.last_error && (
                    <span
                      className="text-xs text-red-700"
                      title={row.last_error}
                      data-testid={`last-error-${row.name}`}
                    >
                      {truncate(row.last_error, 80)}
                    </span>
                  )}
                </div>
              </Td>
              <Td>
                <span className="text-zinc-600">{summarizeProperties(row.properties)}</span>
              </Td>
              <Td>
                <span className="text-zinc-600" data-testid={`factory-count-${row.name}`}>
                  {row.factory_count}
                </span>
              </Td>
              <Td>
                <span className="text-zinc-600" title={row.updated_at}>
                  {relativeTime(row.updated_at)}
                </span>
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-2.5 font-medium">{children}</th>;
}

function Td({ children }: { children: React.ReactNode }) {
  return <td className="px-4 py-3 align-top">{children}</td>;
}

function EmptyState() {
  return (
    <div className="p-12 text-center" data-testid="empty-state">
      <h2 className="text-lg font-medium text-zinc-900">No catalogs registered</h2>
      <p className="mt-2 text-sm text-zinc-600">
        Register one with the form, or via{" "}
        <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">POST /catalogs</code>.
      </p>
      <Link
        to="/catalogs/new"
        className="mt-4 inline-block rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800"
      >
        Register a catalog
      </Link>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-3 p-6" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-9 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="p-8 text-center" data-testid="error-state">
      <h2 className="text-base font-medium text-red-700">Couldn't load catalogs</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}
