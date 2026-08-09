import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listObjectTypes } from "@/api/objectTypes";
import {
  executeObjectsQuery,
  previewQueryPlan,
  type QueryPlanPreview,
  type QueryResult,
} from "@/api/query";
import { ApiError } from "@/api/client";

const DEFAULT_LIMIT = 25;
const DEFAULT_TIMEOUT = 10;
const MAX_LIMIT = 100;
const MAX_TIMEOUT = 30;

export function Query() {
  const [from, setFrom] = useState("");
  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  const [timeout, setTimeoutSec] = useState(DEFAULT_TIMEOUT);
  const [preview, setPreview] = useState<QueryPlanPreview | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const types = useQuery({
    queryKey: ["object-types", { search: "" }],
    queryFn: () => listObjectTypes(),
  });

  const previewMutation = useMutation({
    mutationFn: () =>
      previewQueryPlan({ from, limit, timeout_seconds: timeout }),
    onSuccess: (p) => {
      setPreview(p);
      setError(null);
    },
    onError: (e: unknown) => setError(formatError(e)),
  });

  const runMutation = useMutation({
    mutationFn: () =>
      executeObjectsQuery({ from, limit, timeout_seconds: timeout }),
    onSuccess: (r) => {
      setResult(r);
      setPreview(null); // result includes its own SQL
      setError(null);
    },
    onError: (e: unknown) => setError(formatError(e)),
  });

  const canRun =
    from.trim().length > 0 && !previewMutation.isPending && !runMutation.isPending;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Query</h1>
        <p className="mt-1 text-sm text-zinc-600">
          Ask Core for objects of a type. Core resolves the type to its
          registered factories, builds one Trino statement, and returns a
          unified table. Use{" "}
          <Link
            to="/raw-trino-query"
            className="underline underline-offset-2 hover:text-zinc-900"
          >
            Raw Trino
          </Link>{" "}
          if you need direct SQL.
        </p>
      </header>

      <section
        className="space-y-3 rounded border border-zinc-200 bg-white p-4"
        data-testid="query-builder"
      >
        <div>
          <label
            htmlFor="from"
            className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
          >
            Object type
          </label>
          <select
            id="from"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            disabled={types.isLoading}
            className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400 disabled:cursor-not-allowed"
            data-testid="from-select"
          >
            <option value="">
              {types.isLoading ? "Loading..." : "— pick one —"}
            </option>
            {(types.data ?? []).map((t) => (
              <option key={t.id} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
          {types.data && types.data.length === 0 && (
            <p className="mt-1 text-xs text-zinc-500" data-testid="no-types-hint">
              No object types registered yet.{" "}
              <Link to="/object-types" className="underline underline-offset-2">
                Create one
              </Link>{" "}
              to start querying.
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label
              htmlFor="limit"
              className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
            >
              Limit per factory (1–{MAX_LIMIT})
            </label>
            <input
              id="limit"
              type="number"
              min={1}
              max={MAX_LIMIT}
              value={limit}
              onChange={(e) => setLimit(clampInt(e.target.value, 1, MAX_LIMIT, DEFAULT_LIMIT))}
              className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
              data-testid="limit-input"
            />
          </div>
          <div>
            <label
              htmlFor="timeout"
              className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
            >
              Timeout in seconds (1–{MAX_TIMEOUT})
            </label>
            <input
              id="timeout"
              type="number"
              min={1}
              max={MAX_TIMEOUT}
              value={timeout}
              onChange={(e) => setTimeoutSec(clampInt(e.target.value, 1, MAX_TIMEOUT, DEFAULT_TIMEOUT))}
              className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
              data-testid="timeout-input"
            />
          </div>
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => previewMutation.mutate()}
            disabled={!canRun}
            className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="preview-button"
          >
            {previewMutation.isPending ? "Planning..." : "Preview SQL"}
          </button>
          <button
            type="button"
            onClick={() => runMutation.mutate()}
            disabled={!canRun}
            className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="run-button"
          >
            {runMutation.isPending ? "Running..." : "Run query"}
          </button>
        </div>
      </section>

      {error && (
        <div
          className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"
          data-testid="error-banner"
        >
          {error}
        </div>
      )}

      {preview && <PreviewPanel preview={preview} />}

      {result && <ResultPanel result={result} />}
    </div>
  );
}

function PreviewPanel({ preview }: { preview: QueryPlanPreview }) {
  return (
    <section
      className="space-y-3 rounded border border-zinc-200 bg-white p-4"
      data-testid="preview-panel"
    >
      <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
        Preview: Trino SQL Core would run
      </h2>
      <SqlBlock sql={preview.sql} testid="preview-sql" />
      <FactoryLists
        used={preview.factories_used.map((f) => ({
          factory_id: f.factory_id,
          data_source_path: f.data_source_path,
        }))}
        skipped={preview.factories_skipped}
      />
    </section>
  );
}

function ResultPanel({ result }: { result: QueryResult }) {
  const { columns, rows, result_status } = result;
  const tone = result_status.all_ok
    ? "bg-emerald-100 text-emerald-800"
    : "bg-amber-100 text-amber-800";
  return (
    <section
      className="space-y-3 rounded border border-zinc-200 bg-white p-4"
      data-testid="result-panel"
    >
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Result
        </h2>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tone}`}
          data-testid="result-status-pill"
        >
          {result_status.all_ok ? "OK" : "PARTIAL / ERROR"}
        </span>
        <span className="text-xs text-zinc-500">
          {rows.length} row{rows.length === 1 ? "" : "s"} ·{" "}
          {result_status.elapsed_seconds.toFixed(3)}s
          {result_status.trino_query_id && (
            <>
              {" "}
              · trino id{" "}
              <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono">
                {result_status.trino_query_id}
              </code>
            </>
          )}
        </span>
      </div>

      {result_status.errors.length > 0 && (
        <ul
          className="space-y-1 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"
          data-testid="result-errors"
        >
          {result_status.errors.map((e, i) => (
            <li key={i}>
              <span className="font-medium">{e.kind}:</span> {e.message}
            </li>
          ))}
        </ul>
      )}

      <FactoryLists
        used={result_status.factories_used}
        skipped={result_status.factories_skipped}
      />

      <ResultTable columns={columns} rows={rows} />

      <details className="text-xs text-zinc-600">
        <summary className="cursor-pointer hover:text-zinc-900">SQL that ran</summary>
        <SqlBlock sql={result_status.sql} testid="result-sql" />
      </details>
    </section>
  );
}

function FactoryLists({
  used,
  skipped,
}: {
  used: { factory_id: string; data_source_path: string }[];
  skipped: { factory_id: string; data_source_path: string; reason: string }[];
}) {
  return (
    <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
      <div>
        <h3 className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
          Factories used ({used.length})
        </h3>
        {used.length === 0 ? (
          <p className="text-zinc-500" data-testid="factories-used-empty">
            None.
          </p>
        ) : (
          <ul
            className="space-y-0.5 font-mono text-xs"
            data-testid="factories-used"
          >
            {used.map((f) => (
              <li key={f.factory_id}>
                <Link
                  to={`/object-factories/${f.factory_id}`}
                  className="text-zinc-700 hover:underline"
                >
                  {f.data_source_path}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h3 className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
          Factories skipped ({skipped.length})
        </h3>
        {skipped.length === 0 ? (
          <p className="text-zinc-500" data-testid="factories-skipped-empty">
            None.
          </p>
        ) : (
          <ul
            className="space-y-1 text-xs"
            data-testid="factories-skipped"
          >
            {skipped.map((f) => (
              <li key={f.factory_id} className="flex flex-col">
                <code className="font-mono text-zinc-700">{f.data_source_path}</code>
                <span className="text-zinc-500">{f.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ResultTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: unknown[][];
}) {
  if (rows.length === 0) {
    return (
      <p
        className="rounded border border-dashed border-zinc-200 p-4 text-sm text-zinc-500"
        data-testid="results-empty"
      >
        No rows returned.
      </p>
    );
  }
  return (
    <div
      className="max-h-[480px] overflow-auto rounded border border-zinc-200"
      data-testid="results-table-wrap"
    >
      <table className="min-w-full text-xs" data-testid="results-table">
        <thead className="sticky top-0 bg-zinc-50 text-left font-medium text-zinc-700">
          <tr>
            {columns.map((c) => (
              <th
                key={c}
                className="border-b border-zinc-200 px-2 py-1.5 font-mono"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className="border-t border-zinc-100 hover:bg-zinc-50"
              data-testid={`results-row-${i}`}
            >
              {row.map((cell, j) => (
                <td
                  key={j}
                  className="px-2 py-1 font-mono align-top text-zinc-800"
                >
                  {renderCell(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SqlBlock({ sql, testid }: { sql: string; testid: string }) {
  return (
    <pre
      className="overflow-x-auto rounded bg-zinc-50 p-3 font-mono text-xs text-zinc-800"
      data-testid={testid}
    >
      {sql}
    </pre>
  );
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function clampInt(raw: string, min: number, max: number, fallback: number): number {
  const n = Number.parseInt(raw, 10);
  if (Number.isNaN(n)) return fallback;
  if (n < min) return min;
  if (n > max) return max;
  return n;
}

function formatError(e: unknown): string {
  if (e instanceof ApiError) {
    const body = (e.body ?? {}) as { error?: string; details?: unknown };
    if (body.details) {
      return typeof body.details === "string"
        ? body.details
        : JSON.stringify(body.details);
    }
    return `${body.error ?? "Request failed"} (HTTP ${e.status})`;
  }
  return (e as Error).message ?? String(e);
}
