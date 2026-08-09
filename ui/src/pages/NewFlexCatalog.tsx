/**
 * "New flex catalog" page.
 *
 * Flex-specific spin on the generic NewCatalog page: instead of a
 * properties grid, the user authors a Python module in a code editor
 * (Monaco) pre-loaded with a starter template. Preview runs the module
 * server-side in a transient catalog so the operator can see sample
 * rows before committing. Create persists the catalog row + the
 * FlexModule + materializes the file in one POST.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { createCatalog } from "@/api/catalogs";
import { useObservation, useViewIdentity } from "@/lib/viewContext";
import { ApiError } from "@/api/client";
import { previewFlexModule, type FlexPreviewResult } from "@/api/flexModules";
import { CodeEditor } from "@/components/CodeEditor";
import { FlexPreviewPanel } from "@/components/FlexPreviewPanel";

const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;

const STARTER_TEMPLATE = `"""Your flex module — exposes one Trino table from a Python iterable.

Two callables are required:
  - get_tables(): list of table descriptors
  - read_table(table): iterable of pyarrow.RecordBatch

Flex assumes small data: there are no splits (the framework runs one
internally) and read_table always returns every declared column — the
worker projects down to whatever columns the query selects.

Supported column types (Phase A): BIGINT, INTEGER, DOUBLE, BOOLEAN,
VARCHAR, DATE, TIMESTAMP_TZ, JSON.
"""

from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "items",
    "columns": [
        {"name": "id", "type": "BIGINT"},
        {"name": "label", "type": "VARCHAR"},
    ],
}


def get_tables():
    return [TABLE]


def read_table(table):
    rows = [
        {"id": 1, "label": "hello"},
        {"id": 2, "label": "world"},
    ]
    yield batch_from_rows(rows, table=TABLE)
`;

export function NewFlexCatalog() {
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [source, setSource] = useState(STARTER_TEMPLATE);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<FlexPreviewResult | null>(null);

  // The flex module the user is drafting is client-only — the agent can't get
  // it any other way. Publish the source + any preview so it can help write it.
  useViewIdentity("New flex catalog", { type: "new_flex_catalog", name: name || null });
  useObservation(
    "flex_module_draft",
    { description: "the flex module Python source the user is writing", kind: "text" },
    { name, source },
  );
  useObservation(
    "flex_module_preview",
    {
      description: preview
        ? "preview result of the draft flex module"
        : error
          ? "the draft flex module failed to preview"
          : "no preview run yet",
      kind: "json",
    },
    preview ?? (error ? { error } : undefined),
  );

  const previewMutation = useMutation({
    mutationFn: () => previewFlexModule(source),
    onSuccess: (result) => {
      setError(null);
      setPreview(result);
    },
    onError: (err: unknown) => {
      setPreview(null);
      setError(formatError(err));
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createCatalog({
        name: name.trim(),
        connector: "flex",
        properties: {},
        source,
      }),
    onSuccess: (result) => {
      navigate(`/catalogs/${encodeURIComponent(result.catalog.name)}`);
    },
    onError: (err: unknown) => {
      // 502 means the catalog was persisted but Trino rejected CREATE —
      // still navigate to the detail page so the operator sees the
      // error and can edit/delete from there.
      if (err instanceof ApiError && err.status === 502 && err.body && typeof err.body === "object" && "catalog" in err.body) {
        const persisted = (err.body as { catalog: { name: string } }).catalog;
        navigate(`/catalogs/${encodeURIComponent(persisted.name)}`);
        return;
      }
      setError(formatError(err));
    },
  });

  const nameValid = NAME_PATTERN.test(name.trim());

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!nameValid) {
      setError("Name must be alphanumerics, underscores, or hyphens.");
      return;
    }
    if (!source.trim()) {
      setError("Module source can't be empty.");
      return;
    }
    setError(null);
    createMutation.mutate();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <nav className="text-sm text-zinc-500">
        <Link to="/catalogs" className="hover:text-zinc-900">
          Catalogs
        </Link>
        <span className="mx-2">/</span>
        <span className="text-zinc-700">New flex catalog</span>
      </nav>

      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          New flex catalog
        </h1>
        <p className="text-sm text-zinc-600">
          Author the Python module Trino will query through. Preview to
          run a transient catalog and see sample rows before committing.
        </p>
      </header>

      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-lg border border-zinc-200 bg-white p-6"
        data-testid="new-flex-form"
      >
        <div>
          <label
            htmlFor="flex-name"
            className="mb-1 block text-sm font-medium text-zinc-900"
          >
            Catalog name
          </label>
          <input
            id="flex-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            placeholder="my_flex_catalog"
            className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 font-mono text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
            data-testid="flex-name-input"
          />
          <p className="mt-1 text-xs text-zinc-500">
            Alphanumerics, underscores, and hyphens. The catalog will
            be queryable as <code className="rounded bg-zinc-100 px-1 py-0.5">{name.trim() || "<name>"}.&lt;schema&gt;.&lt;table&gt;</code>.
          </p>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-zinc-900">
            Module source
          </label>
          <CodeEditor
            value={source}
            onChange={setSource}
            height="500px"
            testId="flex-source-editor"
          />
        </div>

        {error && (
          <div
            className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
            data-testid="form-error"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-zinc-100 pt-4">
          <Link
            to="/catalogs"
            className="rounded px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
          >
            Cancel
          </Link>
          <button
            type="button"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending || !source.trim()}
            className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="preview-button"
          >
            {previewMutation.isPending ? "Previewing…" : "Preview"}
          </button>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="create-button"
          >
            {createMutation.isPending ? "Creating…" : "Create flex catalog"}
          </button>
        </div>
      </form>

      {preview && <FlexPreviewPanel result={preview} />}
    </div>
  );
}

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    const body = (err.body ?? {}) as { error?: string; details?: unknown };
    const detail =
      typeof body.details === "string"
        ? body.details
        : body.details
          ? JSON.stringify(body.details)
          : null;
    return detail
      ? `${body.error ?? "Request failed"}: ${detail}`
      : `${body.error ?? "Request failed"} (HTTP ${err.status})`;
  }
  return (err as Error).message ?? String(err);
}
