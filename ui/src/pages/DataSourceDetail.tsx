import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  getDataSource,
  getDataSourceColumns,
  type DataSource,
} from "@/api/dataSources";
import { ApiError } from "@/api/client";
import { DataSourceFactoriesPanel } from "@/components/ObjectFactoriesPanel";
import { relativeTime } from "@/lib/format";

export function DataSourceDetail() {
  const { id = "" } = useParams<{ id: string }>();

  const source = useQuery({
    queryKey: ["data-sources", id],
    queryFn: () => getDataSource(id),
    enabled: Boolean(id),
  });

  if (source.isLoading) return <Skeleton />;
  if (source.isError) {
    const status = source.error instanceof ApiError ? source.error.status : undefined;
    if (status === 404 || status === 400) return <NotFound id={id} />;
    return <ErrorState message={(source.error as Error).message} />;
  }
  if (!source.data) return <NotFound id={id} />;

  const row = source.data;

  return (
    <div className="space-y-6">
      <Breadcrumb row={row} />

      <header className="space-y-1">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight font-mono">
            <Link to={`/catalogs/${row.catalog_name}`} className="hover:underline">
              {row.catalog_name}
            </Link>
            .{row.schema_name}.{row.table_name}
          </h1>
          {row.status === "deleted" && <DeletedBadge />}
        </div>
        <p className="text-xs text-zinc-500">
          Data source · discovered from Trino
        </p>
      </header>

      {row.status === "deleted" && <DeletedBanner />}

      <ReadOnly row={row} />

      <ColumnsSection dataSourceId={row.id} />

      <DataSourceFactoriesPanel dataSourceId={row.id} />
    </div>
  );
}

function DeletedBadge() {
  return (
    <span
      className="inline-flex items-center rounded bg-red-100 px-2 py-0.5 text-xs font-medium uppercase tracking-wide text-red-800 ring-1 ring-inset ring-red-200"
      data-testid="status-deleted"
    >
      deleted
    </span>
  );
}

function DeletedBanner() {
  return (
    <div
      className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
      data-testid="deleted-banner"
    >
      This table no longer exists in Trino, but object factories below still
      reference it, so it's kept as <span className="font-medium">deleted</span>{" "}
      rather than removed. Delete or repoint those factories and the next sync
      will drop this data source automatically.
    </div>
  );
}

function ColumnsSection({ dataSourceId }: { dataSourceId: string }) {
  // Live-read each visit — upstream schemas can change. Cached for 30s
  // within the page so jumping back doesn't refetch immediately.
  const cols = useQuery({
    queryKey: ["data-source-columns", dataSourceId],
    queryFn: () => getDataSourceColumns(dataSourceId),
    staleTime: 30_000,
    retry: false,
  });

  return (
    <section data-testid="columns-section">
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
        Columns
      </h2>
      {cols.isLoading ? (
        <p className="text-sm text-zinc-500">Loading columns from Trino...</p>
      ) : cols.isError ? (
        <ColumnsError error={cols.error} />
      ) : cols.data!.columns.length === 0 ? (
        <p
          className="rounded border border-dashed border-zinc-200 p-3 text-sm text-zinc-500"
          data-testid="columns-empty"
        >
          The table exists but Trino reports no columns.
        </p>
      ) : (
        <div className="overflow-x-auto rounded border border-zinc-200 bg-white">
          <table
            className="min-w-full text-xs"
            data-testid="columns-table"
          >
            <thead className="bg-zinc-50 text-left font-medium text-zinc-700">
              <tr>
                <th className="border-b border-zinc-200 px-3 py-1.5 font-mono">
                  Name
                </th>
                <th className="border-b border-zinc-200 px-3 py-1.5 font-mono">
                  Type
                </th>
              </tr>
            </thead>
            <tbody>
              {cols.data!.columns.map((c) => (
                <tr
                  key={c.name}
                  className="border-t border-zinc-100"
                  data-testid={`column-row-${c.name}`}
                >
                  <td className="px-3 py-1 font-mono text-zinc-800">{c.name}</td>
                  <td className="px-3 py-1 font-mono text-zinc-600">{c.type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-1 text-[10px] text-zinc-400">
        Live introspection via Trino's SHOW COLUMNS.
      </p>
    </section>
  );
}

function ColumnsError({ error }: { error: unknown }) {
  // Trino errors come back as 502 with structured details. Surface what we
  // can without making the user open the network tab.
  if (error instanceof ApiError) {
    const body = (error.body ?? {}) as { error?: string; details?: unknown };
    const detail =
      typeof body.details === "string"
        ? body.details
        : JSON.stringify(body.details ?? "");
    return (
      <div
        className="space-y-1 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        data-testid="columns-error"
      >
        <div className="font-medium">
          Couldn't read columns from Trino ({body.error ?? `HTTP ${error.status}`})
        </div>
        {detail && (
          <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs">
            {detail}
          </pre>
        )}
      </div>
    );
  }
  return (
    <div
      className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"
      data-testid="columns-error"
    >
      {(error as Error).message ?? String(error)}
    </div>
  );
}

function ReadOnly({ row }: { row: DataSource }) {
  return (
    <div className="space-y-6">
      <Section title="Description">
        {row.description ? (
          <p
            className="rounded border border-zinc-200 bg-white p-3 text-sm whitespace-pre-wrap text-zinc-800"
            data-testid="description-text"
          >
            {row.description}
          </p>
        ) : (
          <p className="text-sm text-zinc-500" data-testid="description-empty">
            No description.
          </p>
        )}
      </Section>

      <Section title="Timestamps">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-zinc-500">Discovered</dt>
          <dd title={row.created_at}>{relativeTime(row.created_at)}</dd>
          <dt className="text-zinc-500">Updated</dt>
          <dd title={row.updated_at}>{relativeTime(row.updated_at)}</dd>
        </dl>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Breadcrumb({ row }: { row: DataSource }) {
  return (
    <nav className="text-sm text-zinc-500">
      <Link to={`/catalogs/${row.catalog_name}`} className="hover:text-zinc-900">
        Catalogs / {row.catalog_name}
      </Link>
      <span className="mx-2">/</span>
      <span className="text-zinc-700 font-mono">
        {row.schema_name}.{row.table_name}
      </span>
    </nav>
  );
}

function Skeleton() {
  return (
    <div className="space-y-3" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function NotFound({ id }: { id: string }) {
  return (
    <div
      className="rounded border border-zinc-200 bg-white p-12 text-center"
      data-testid="not-found"
    >
      <h1 className="text-lg font-medium text-zinc-900">Data source not found</h1>
      <p className="mt-2 text-sm text-zinc-600">
        No data source with id{" "}
        <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{id}</code>.
      </p>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      className="rounded border border-zinc-200 bg-white p-8 text-center"
      data-testid="error-state"
    >
      <h2 className="text-base font-medium text-red-700">Couldn't load data source</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
    </div>
  );
}
