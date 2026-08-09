import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listDataSources, type DataSource } from "@/api/dataSources";
import { listObjectFactories } from "@/api/objectFactories";

/**
 * Lists a catalog's data sources, grouped into a collapsible folder per
 * schema. Data sources are sync-owned (discovered from Trino by Core's
 * reconciler), so this panel is read-only — each table links to
 * /data-sources/<id>. Shown on CatalogDetail.
 */
export function CatalogDataSourcesPanel({ catalogName }: { catalogName: string }) {
  const sources = useQuery({
    queryKey: ["data-sources", { catalog: catalogName }],
    queryFn: () => listDataSources(catalogName),
  });
  // Pull factory counts for every source under this catalog in one go, so
  // each row can show "N object types" without N+1 fetches.
  const factories = useQuery({
    queryKey: ["object-factories", { catalog: catalogName }],
    queryFn: () => listObjectFactories({ catalog: catalogName }),
  });

  const factoryCounts = new Map<string, number>();
  for (const f of factories.data ?? []) {
    factoryCounts.set(f.data_source_id, (factoryCounts.get(f.data_source_id) ?? 0) + 1);
  }

  // Group by schema, preserving the API's (schema, table) ordering.
  const bySchema = new Map<string, DataSource[]>();
  for (const s of sources.data ?? []) {
    const arr = bySchema.get(s.schema_name);
    if (arr) arr.push(s);
    else bySchema.set(s.schema_name, [s]);
  }
  const schemas = [...bySchema.keys()].sort((a, b) => a.localeCompare(b));

  // Folders collapse by default, except when there's only one schema (no
  // point hiding a single group). Per-schema user toggles override that.
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const defaultOpen = schemas.length <= 1;
  const isOpen = (schema: string) => overrides[schema] ?? defaultOpen;
  const toggle = (schema: string) =>
    setOverrides((o) => ({ ...o, [schema]: !isOpen(schema) }));

  return (
    <section data-testid="data-sources-panel">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Data sources
        </h2>
        <span className="text-[10px] text-zinc-400">Auto-discovered from Trino</span>
      </div>

      {sources.isLoading ? (
        <p className="text-sm text-zinc-500">Loading...</p>
      ) : schemas.length === 0 ? (
        <p
          className="rounded border border-dashed border-zinc-200 p-4 text-sm text-zinc-500"
          data-testid="data-sources-empty"
        >
          No data sources discovered for this catalog yet. They're synced from
          the catalog's tables — if this stays empty, the catalog may be down or
          have no queryable tables.
        </p>
      ) : (
        <div
          className="divide-y divide-zinc-100 overflow-hidden rounded border border-zinc-200 bg-white"
          data-testid="data-sources-list"
        >
          {schemas.map((schema) => {
            const rows = bySchema.get(schema)!;
            const open = isOpen(schema);
            const deletedCount = rows.filter((r) => r.status === "deleted").length;
            return (
              <div key={schema} data-testid={`schema-folder-${schema}`}>
                <button
                  type="button"
                  onClick={() => toggle(schema)}
                  aria-expanded={open}
                  className="flex w-full items-center gap-2 bg-zinc-50 px-3 py-2 text-left hover:bg-zinc-100"
                  data-testid={`schema-toggle-${schema}`}
                >
                  <Chevron open={open} />
                  <span className="font-mono text-sm font-medium text-zinc-800">
                    {schema}
                  </span>
                  <span className="text-[11px] text-zinc-500">
                    {rows.length} table{rows.length === 1 ? "" : "s"}
                  </span>
                  {deletedCount > 0 && (
                    <span
                      className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-red-800"
                      title={`${deletedCount} deleted`}
                    >
                      {deletedCount} deleted
                    </span>
                  )}
                </button>

                {open && (
                  <ul className="divide-y divide-zinc-100">
                    {rows.map((s) => (
                      <li key={s.id}>
                        <Link
                          to={`/data-sources/${s.id}`}
                          className="block py-2.5 pl-9 pr-4 hover:bg-zinc-50"
                          data-testid={`data-source-row-${s.id}`}
                        >
                          <div className="flex items-baseline justify-between gap-4">
                            <span className="flex items-center gap-2">
                              <span className="font-mono text-sm text-zinc-900">
                                {s.table_name}
                              </span>
                              {s.status === "deleted" && (
                                <span
                                  className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-red-800"
                                  data-testid={`data-source-deleted-${s.id}`}
                                >
                                  deleted
                                </span>
                              )}
                            </span>
                            <FactoryCountBadge count={factoryCounts.get(s.id) ?? 0} />
                          </div>
                          {s.description && (
                            <p className="mt-0.5 line-clamp-1 text-sm text-zinc-600">
                              {s.description}
                            </p>
                          )}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="currentColor"
      aria-hidden="true"
      className={`h-3.5 w-3.5 shrink-0 text-zinc-400 transition-transform ${
        open ? "rotate-90" : ""
      }`}
    >
      <path d="M7 5l6 5-6 5V5z" />
    </svg>
  );
}

function FactoryCountBadge({ count }: { count: number }) {
  const tone =
    count === 0
      ? "bg-zinc-100 text-zinc-600"
      : "bg-emerald-100 text-emerald-800";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tone}`}
    >
      {count} factor{count === 1 ? "y" : "ies"}
    </span>
  );
}
