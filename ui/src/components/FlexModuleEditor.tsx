/**
 * View + edit the Python source backing a flex catalog.
 *
 * Read-only by default — the source displays in Monaco so syntax
 * highlighting + line numbers help operators scan it. Click "Edit"
 * to unlock the editor; "Preview" runs the current draft in a
 * transient catalog; "Save" PUTs and hot-swaps the live catalog
 * (Trino's flex worker reloads on its own via mtime).
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  getFlexModule,
  previewFlexModule,
  replaceFlexModule,
  type FlexPreviewResult,
} from "@/api/flexModules";
import { CodeEditor } from "@/components/CodeEditor";
import { FlexPreviewPanel } from "@/components/FlexPreviewPanel";

export function FlexModuleEditor({ catalogName }: { catalogName: string }) {
  const qc = useQueryClient();
  const module_ = useQuery({
    queryKey: ["flex-modules", catalogName],
    queryFn: () => getFlexModule(catalogName),
  });

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>("");
  const [preview, setPreview] = useState<FlexPreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Sync draft to server source whenever we enter edit mode or the
  // backing source changes (e.g. after a successful save).
  useEffect(() => {
    if (module_.data) {
      setDraft(module_.data.source_text);
    }
  }, [module_.data]);

  const saveMutation = useMutation({
    mutationFn: () => replaceFlexModule(catalogName, draft),
    onSuccess: async () => {
      setError(null);
      setPreview(null);
      setEditing(false);
      await qc.invalidateQueries({ queryKey: ["flex-modules", catalogName] });
    },
    onError: (err: unknown) => setError(formatError(err)),
  });

  const previewMutation = useMutation({
    mutationFn: () => previewFlexModule(draft),
    onSuccess: (result) => {
      setError(null);
      setPreview(result);
    },
    onError: (err: unknown) => {
      setPreview(null);
      setError(formatError(err));
    },
  });

  if (module_.isLoading) {
    return (
      <Section title="Flex module source">
        <p className="text-sm text-zinc-500">Loading…</p>
      </Section>
    );
  }
  if (module_.isError) {
    const status = module_.error instanceof ApiError ? module_.error.status : undefined;
    if (status === 404) {
      // Flex catalog without a Core-managed module — pre-Phase-B
      // catalogs that point at a manually-mounted file. Skip the
      // editor; properties section already shows the path.
      return null;
    }
    return (
      <Section title="Flex module source">
        <p className="text-sm text-red-700">
          Couldn't load module: {(module_.error as Error).message}
        </p>
      </Section>
    );
  }
  if (!module_.data) return null;

  const row = module_.data;
  const dirty = editing && draft !== row.source_text;

  return (
    <>
      <Section
        title="Flex module source"
        meta={`v${row.version}`}
        action={
          editing ? null : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded border border-zinc-200 bg-white px-2.5 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
              data-testid="flex-edit-button"
            >
              Edit
            </button>
          )
        }
      >
        <CodeEditor
          value={editing ? draft : row.source_text}
          onChange={editing ? setDraft : undefined}
          height="450px"
          readOnly={!editing}
          testId="flex-source-editor"
        />

        {error && (
          <div
            className="mt-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
            data-testid="flex-editor-error"
          >
            {error}
          </div>
        )}

        {editing && (
          <div className="mt-3 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setDraft(row.source_text);
                setError(null);
                setPreview(null);
              }}
              disabled={saveMutation.isPending}
              className="rounded px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="flex-cancel-button"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => previewMutation.mutate()}
              disabled={previewMutation.isPending || !draft.trim()}
              className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="flex-preview-button"
            >
              {previewMutation.isPending ? "Previewing…" : "Preview"}
            </button>
            <button
              type="button"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !dirty}
              className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="flex-save-button"
            >
              {saveMutation.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </Section>

      {preview && <FlexPreviewPanel result={preview} />}
    </>
  );
}

function Section({
  title,
  meta,
  action,
  children,
}: {
  title: string;
  meta?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        <div className="flex items-baseline gap-2">
          <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500">
            {title}
          </h2>
          {meta && <span className="text-xs text-zinc-400">{meta}</span>}
        </div>
        {action}
      </div>
      {children}
    </section>
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
