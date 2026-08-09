import { useCallback, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { executeRawTrinoQuery, type QueryResult } from "@/api/rawTrinoQuery";
import { ApiError } from "@/api/client";
import { useObservation, useViewIdentity } from "@/lib/viewContext";

const SUGGESTIONS = [
  { label: "List catalogs", sql: "SHOW CATALOGS" },
  {
    label: "List schemas in a catalog",
    sql: "SHOW SCHEMAS FROM tpch",
  },
  {
    label: "Sample TPC-H nations",
    sql: "SELECT * FROM tpch.tiny.nation ORDER BY n_nationkey LIMIT 25",
  },
  {
    label: "Trino system metadata",
    sql: "SELECT catalog_name, connector_name FROM system.metadata.catalogs ORDER BY catalog_name",
  },
];

const TIMEOUT_OPTIONS = [5, 10, 30, 60];
const ROW_OPTIONS = [100, 1_000, 10_000];

interface QueryError {
  kind: "timeout" | "trino" | "validation" | "network";
  message: string;
  details?: string;
  status?: number;
}

export function RawTrinoQuery() {
  const [sql, setSql] = useState("SHOW CATALOGS");
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [maxRows, setMaxRows] = useState(10_000);
  const [error, setError] = useState<QueryError | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);

  // Let the agent see the query the user is composing / ran here and its result.
  useViewIdentity("Raw Trino query", { type: "raw_trino_query" });
  useObservation(
    "raw_trino_query",
    {
      description: result
        ? `Raw Trino query result — ${result.row_count} row(s)`
        : error
          ? `Raw Trino query — ${error.kind} error`
          : "the raw Trino query the user is composing",
      kind: "table",
    },
    result
      ? {
          sql,
          columns: result.columns,
          rows: result.rows,
          row_count: result.row_count,
          truncated: result.truncated,
        }
      : error
        ? { sql, error: error.message, details: error.details }
        : { sql },
  );

  const mutation = useMutation({
    mutationFn: () =>
      executeRawTrinoQuery({
        sql,
        timeout_seconds: timeoutSeconds,
        max_rows: maxRows,
      }),
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (err: unknown) => {
      setResult(null);
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as {
          error?: string;
          details?: string;
        };
        if (err.status === 504) {
          setError({
            kind: "timeout",
            message: "Query exceeded the wall-clock budget",
            details: body.details,
            status: err.status,
          });
          return;
        }
        if (err.status === 400 && body.error === "trino_error") {
          setError({
            kind: "trino",
            message: "Trino rejected the query",
            details: body.details,
            status: err.status,
          });
          return;
        }
        if (err.status === 400) {
          setError({
            kind: "validation",
            message: "Invalid request",
            details: body.details ?? err.message,
            status: err.status,
          });
          return;
        }
      }
      setError({
        kind: "network",
        message: "Couldn't reach Core",
        details: (err as Error).message,
      });
    },
  });

  const handleKey = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (sql.trim()) mutation.mutate();
      }
    },
    [sql, mutation],
  );

  const isRunning = mutation.isPending;

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Raw Trino Query</h1>
        <p className="text-sm text-zinc-600">
          Send raw SQL to Trino through Core. For testing and validation — Core enforces a
          wall-clock and row cap, but otherwise passes everything through.
        </p>
      </header>

      <div className="space-y-2 rounded-lg border border-zinc-200 bg-white p-4">
        <textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={handleKey}
          spellCheck={false}
          className="block w-full resize-y rounded border border-zinc-200 bg-zinc-50 p-3 font-mono text-sm shadow-inner focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          rows={8}
          placeholder="SHOW CATALOGS"
          data-testid="sql-input"
        />

        <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-700">
            <SelectField
              label="Timeout"
              value={timeoutSeconds}
              onChange={setTimeoutSeconds}
              options={TIMEOUT_OPTIONS}
              suffix="s"
              testid="timeout-select"
            />
            <SelectField
              label="Max rows"
              value={maxRows}
              onChange={setMaxRows}
              options={ROW_OPTIONS}
              testid="max-rows-select"
            />
            <SuggestionsMenu onPick={setSql} />
          </div>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <span className="hidden sm:inline">⌘/Ctrl + Enter to run</span>
            <button
              type="button"
              onClick={() => mutation.mutate()}
              disabled={isRunning || !sql.trim()}
              className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="run-button"
            >
              {isRunning ? "Running…" : "Run query"}
            </button>
          </div>
        </div>
      </div>

      {error && <ErrorPanel error={error} />}
      {result && !error && <ResultPanel result={result} />}
      {!result && !error && !isRunning && <EmptyHint />}
      {isRunning && <LoadingHint />}
    </div>
  );
}

function SelectField<T extends number>({
  label,
  value,
  onChange,
  options,
  suffix,
  testid,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: readonly T[];
  suffix?: string;
  testid?: string;
}) {
  return (
    <label className="inline-flex items-center gap-1.5">
      <span className="text-zinc-500">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(Number(e.target.value) as T)}
        className="rounded border border-zinc-200 bg-white px-2 py-1 text-sm shadow-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
        data-testid={testid}
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {o.toLocaleString()}
            {suffix}
          </option>
        ))}
      </select>
    </label>
  );
}

function SuggestionsMenu({ onPick }: { onPick: (sql: string) => void }) {
  return (
    <label className="inline-flex items-center gap-1.5">
      <span className="text-zinc-500">Examples</span>
      <select
        defaultValue=""
        onChange={(e) => {
          const choice = SUGGESTIONS.find((s) => s.label === e.target.value);
          if (choice) onPick(choice.sql);
          // Reset the select so picking the same option twice still fires.
          e.target.value = "";
        }}
        className="rounded border border-zinc-200 bg-white px-2 py-1 text-sm shadow-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
        data-testid="examples-select"
      >
        <option value="" disabled>
          Pick one…
        </option>
        {SUGGESTIONS.map((s) => (
          <option key={s.label} value={s.label}>
            {s.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyHint() {
  return (
    <div className="rounded border border-dashed border-zinc-200 p-8 text-center text-sm text-zinc-500">
      Run a query to see results.
    </div>
  );
}

function LoadingHint() {
  return (
    <div className="rounded border border-zinc-200 bg-white p-4" data-testid="loading-state">
      <div className="space-y-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-6 animate-pulse rounded bg-zinc-100" />
        ))}
      </div>
    </div>
  );
}

function ErrorPanel({ error }: { error: QueryError }) {
  const tone =
    error.kind === "timeout"
      ? "amber"
      : error.kind === "network"
        ? "red"
        : "red";
  const headline =
    error.kind === "timeout"
      ? "Query timed out"
      : error.kind === "trino"
        ? "Trino error"
        : error.kind === "validation"
          ? "Validation error"
          : "Couldn't reach Core";
  const cls =
    tone === "red"
      ? "border-red-200 bg-red-50 text-red-900"
      : "border-amber-200 bg-amber-50 text-amber-900";
  return (
    <div className={`rounded border p-4 text-sm ${cls}`} data-testid="error-panel">
      <div className="flex items-baseline justify-between gap-3">
        <strong>{headline}</strong>
        {error.status && (
          <span className="font-mono text-xs uppercase tracking-wide">HTTP {error.status}</span>
        )}
      </div>
      <p className="mt-1">{error.message}</p>
      {error.details && (
        <pre className="mt-2 whitespace-pre-wrap rounded bg-white/60 p-2 text-xs">
          {typeof error.details === "string"
            ? error.details
            : JSON.stringify(error.details, null, 2)}
        </pre>
      )}
    </div>
  );
}

function ResultPanel({ result }: { result: QueryResult }) {
  return (
    <div className="space-y-3" data-testid="result-panel">
      <Meta result={result} />
      {result.truncated && (
        <div
          className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          data-testid="truncation-banner"
        >
          Result truncated at {result.applied_limits.max_rows.toLocaleString()} rows. Increase
          the row cap or add a LIMIT to your query if you need more.
        </div>
      )}
      {result.columns.length === 0 ? (
        <div className="rounded border border-zinc-200 bg-white p-6 text-center text-sm text-zinc-500">
          Statement ran successfully — no rows returned.
        </div>
      ) : (
        <ResultsTable result={result} />
      )}
    </div>
  );
}

function Meta({ result }: { result: QueryResult }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
      <span>
        <strong className="text-zinc-700">{result.row_count.toLocaleString()}</strong> row
        {result.row_count === 1 ? "" : "s"}
      </span>
      <span>
        <strong className="text-zinc-700">{result.elapsed_seconds.toFixed(2)}s</strong> elapsed
      </span>
      {result.query_id && (
        <span>
          query_id <code className="rounded bg-zinc-100 px-1 py-0.5">{result.query_id}</code>
        </span>
      )}
    </div>
  );
}

function ResultsTable({ result }: { result: QueryResult }) {
  const display = useMemo(() => result.rows.map((r) => r.map(renderCell)), [result.rows]);
  return (
    <div
      className="max-h-[60vh] overflow-auto rounded border border-zinc-200 bg-white"
      data-testid="results-table-wrapper"
    >
      <table className="w-full text-sm" data-testid="results-table">
        <thead className="sticky top-0 z-10 border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            {result.columns.map((c) => (
              <th key={c} className="px-3 py-2 font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100">
          {display.map((row, i) => (
            <tr key={i} className="hover:bg-zinc-50">
              {row.map((cell, j) => (
                <td key={j} className="whitespace-pre px-3 py-1.5 font-mono text-xs text-zinc-800">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderCell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
