/**
 * Renders the result of POST /flex-modules/preview as one section per
 * table the module declared. Each section shows the column list plus
 * up to N sample rows, or the per-table error if Trino choked on the
 * scan.
 */

import type { FlexPreviewResult, FlexPreviewTable } from "@/api/flexModules";

export function FlexPreviewPanel({ result }: { result: FlexPreviewResult }) {
  if (result.tables.length === 0) {
    return (
      <section
        className="rounded border border-zinc-200 bg-white p-4 text-sm text-zinc-600"
        data-testid="preview-empty"
      >
        The module didn't declare any tables (
        <code className="rounded bg-zinc-100 px-1 py-0.5">get_tables()</code>{" "}
        returned an empty list).
      </section>
    );
  }
  return (
    <section className="space-y-4" data-testid="preview-panel">
      <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
        Preview
      </h2>
      {result.tables.map((t) => (
        <TablePreview key={`${t.schema}.${t.name}`} table={t} />
      ))}
    </section>
  );
}

function TablePreview({ table }: { table: FlexPreviewTable }) {
  return (
    <div
      className="rounded border border-zinc-200 bg-white"
      data-testid={`preview-table-${table.schema}-${table.name}`}
    >
      <div className="border-b border-zinc-100 px-3 py-2">
        <code className="font-mono text-sm text-zinc-800">
          {table.schema}.{table.name}
        </code>
      </div>
      {table.error ? (
        <div className="px-3 py-3 text-sm text-red-800">
          <div className="mb-1 font-medium">Couldn't preview this table</div>
          <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs">
            {table.error}
          </pre>
        </div>
      ) : (
        <RowsView table={table} />
      )}
    </div>
  );
}

function RowsView({ table }: { table: FlexPreviewTable }) {
  const cols = table.sample_columns ?? table.columns?.map((c) => c.name) ?? [];
  const rows = table.sample_rows ?? [];
  if (cols.length === 0) {
    return (
      <p className="px-3 py-3 text-sm text-zinc-500">
        Table has no columns.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="bg-zinc-50">
            {cols.map((c) => (
              <th
                key={c}
                className="border-b border-zinc-200 px-3 py-1.5 text-left font-mono font-medium text-zinc-700"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className={ri % 2 === 0 ? "bg-white" : "bg-zinc-50/40"}>
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className="border-b border-zinc-100 px-3 py-1 font-mono text-zinc-900"
                >
                  {formatCell(cell)}
                </td>
              ))}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td
                colSpan={cols.length}
                className="px-3 py-3 text-zinc-500"
              >
                No rows returned.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
