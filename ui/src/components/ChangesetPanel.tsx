import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import {
  applyChangeset,
  buildAndTestStage,
  clearChangeset,
  removeAction,
  type ApplyResult,
  type BuildTestResult,
  type StagedAction,
  type StageTest,
} from "@/api/changeset";

// ---- op → tone / verb / entity ---------------------------------------------

type OpFamily = "create" | "update" | "delete";

function family(op: string): OpFamily {
  if (op.startsWith("create_")) return "create";
  if (op.startsWith("delete_") || op === "remove_trait") return "delete";
  return "update";
}

const FAMILY_TONE: Record<OpFamily, string> = {
  create: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  update: "bg-amber-100 text-amber-800 ring-amber-200",
  delete: "bg-red-100 text-red-800 ring-red-200",
};

const FAMILY_BAR: Record<OpFamily, string> = {
  create: "border-l-emerald-300",
  update: "border-l-amber-300",
  delete: "border-l-red-300",
};

// ---- ref + summary rendering ------------------------------------------------

function refLabel(ref: unknown): string {
  if (ref && typeof ref === "object") {
    const r = ref as Record<string, unknown>;
    if (r.source === "action" && typeof r.handle === "string") return `@${r.handle}`;
    if (r.source === "prod" && typeof r.id === "string") return `${r.id} (prod)`;
  }
  return "?";
}

function str(a: StagedAction, key: string): string | undefined {
  const v = (a as Record<string, unknown>)[key];
  return typeof v === "string" ? v : undefined;
}

/** A short, human-readable summary of an action for the review list. */
function summarize(a: StagedAction): { verb: string; detail: React.ReactNode } {
  const op = a.op;
  const handle = str(a, "handle");
  const handleChip = handle ? <Chip>@{handle}</Chip> : null;
  switch (op) {
    case "create_catalog":
      return {
        verb: "Create catalog",
        detail: <>
          <strong>{str(a, "name")}</strong>
          <span className="text-zinc-400"> · {str(a, "connector")}</span> {handleChip}
        </>,
      };
    case "update_catalog":
      return { verb: "Update catalog", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    case "delete_catalog":
      return { verb: "Delete catalog", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    case "set_flex_module":
      return { verb: "Set flex module", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    case "create_object_type":
      return { verb: "Create object type", detail: <><strong>{str(a, "name")}</strong> {handleChip}</> };
    case "update_object_type":
      return { verb: "Update object type", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    case "delete_object_type":
      return { verb: "Delete object type", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    case "add_trait":
      return { verb: "Add trait", detail: <><strong>{str(a, "trait")}</strong> to <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip></> };
    case "remove_trait":
      return { verb: "Remove trait", detail: <><strong>{str(a, "trait")}</strong> from <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip></> };
    case "create_object_factory": {
      const ds = (a as Record<string, unknown>).data_source as Record<string, unknown> | undefined;
      const cat = ds ? refLabel(ds.catalog) : "?";
      const path = ds ? `${cat} · ${ds.schema_name}.${ds.table}` : "?";
      return {
        verb: "Create factory",
        detail: <><Chip>{path}</Chip> → <Chip>{refLabel((a as Record<string, unknown>).object_type)}</Chip> {handleChip}</>,
      };
    }
    case "update_object_factory":
      return { verb: "Update factory", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    case "delete_object_factory":
      return { verb: "Delete factory", detail: <Chip>{refLabel((a as Record<string, unknown>).target)}</Chip> };
    default:
      return { verb: op, detail: null };
  }
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[11px] text-zinc-700">
      {children}
    </span>
  );
}

// ---- action row -------------------------------------------------------------

function ActionRow({
  action,
  index,
  onRemove,
  removing,
  disabled,
}: {
  action: StagedAction;
  index: number;
  onRemove: () => void;
  removing: boolean;
  disabled: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const fam = family(action.op);
  const { verb, detail } = summarize(action);
  return (
    <li
      className={cn("border-l-2", FAMILY_BAR[fam])}
      data-testid={`staged-action-${action.op}`}
    >
      <div className="group flex items-start gap-2 py-1.5 pl-2 pr-1 text-sm">
        <span className="mt-0.5 w-5 shrink-0 text-right text-[11px] tabular-nums text-zinc-400">{index + 1}</span>
        <span
          className={cn(
            "mt-0.5 inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset",
            FAMILY_TONE[fam],
          )}
        >
          {fam}
        </span>
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          className="min-w-0 flex-1 text-left"
          data-testid="toggle-action"
        >
          <span className={cn("block", !expanded && "truncate")}>
            <span className={cn("text-zinc-400", !expanded && "mr-1")}>{expanded ? "▾" : "▸"}</span>
            {/* When expanded, the full summary shows on its own line below —
                don't repeat it here. */}
            {!expanded && (
              <>
                <span className="text-zinc-700">{verb}</span> {detail}
              </>
            )}
          </span>
        </button>
        <button
          type="button"
          onClick={onRemove}
          disabled={disabled || removing}
          title="Remove this action"
          aria-label="Remove action"
          className="mt-0.5 shrink-0 rounded px-1 text-zinc-300 hover:bg-red-50 hover:text-red-600 disabled:opacity-40 group-hover:text-zinc-400"
          data-testid="remove-action"
        >
          ✕
        </button>
      </div>
      {expanded && <ActionDetail action={action} />}
    </li>
  );
}

// ---- expanded detail: the whole action, materialized as a UI form -----------

const HIDDEN_FIELDS = new Set(["id", "op", "note"]);

function ActionDetail({ action }: { action: StagedAction }) {
  const note = typeof action.note === "string" ? action.note.trim() : "";
  const fields = Object.entries(action).filter(([k]) => !HIDDEN_FIELDS.has(k));
  return (
    <div className="ml-7 mb-2 mr-2 space-y-2" data-testid="action-detail">
      {note && <p className="text-sm leading-relaxed text-zinc-700">{note}</p>}
      {fields.length > 0 && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[13px]">
          {fields.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="whitespace-nowrap text-zinc-400">{humanize(k)}</dt>
              <dd className="min-w-0 break-words text-zinc-700">{renderValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function renderValue(key: string, value: unknown): React.ReactNode {
  // Flex module source is real code — show it as a scrollable code block.
  if (key === "source" && typeof value === "string") {
    return (
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-50 p-2 font-mono text-[11px] leading-relaxed text-zinc-600">
        {value}
      </pre>
    );
  }
  return <FieldValue value={value} fieldKey={key} />;
}

function FieldValue({ value, fieldKey }: { value: unknown; fieldKey?: string }): React.ReactNode {
  if (value === null || value === undefined || value === "") {
    return <span className="text-zinc-400">—</span>;
  }
  if (typeof value === "boolean") return <span>{value ? "yes" : "no"}</span>;
  if (typeof value === "number") return <span>{value}</span>;
  if (typeof value === "string") {
    if (fieldKey && /pass|secret|token|key$/i.test(fieldKey)) {
      return <span className="tracking-widest text-zinc-500">••••••••</span>;
    }
    return <span>{value}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-zinc-400">none</span>;
    return (
      <span className="flex flex-wrap gap-1">
        {value.map((v, i) => (
          <Chip key={i}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</Chip>
        ))}
      </span>
    );
  }
  const o = value as Record<string, unknown>;
  // A reference (ProdRef / ActionRef).
  if (o.source === "action" && typeof o.handle === "string") return <Chip>@{o.handle}</Chip>;
  if (o.source === "prod" && typeof o.id === "string") {
    return <Chip>{String(o.id)} <span className="ml-1 text-zinc-400">prod</span></Chip>;
  }
  // A structural data-source reference (catalog + schema + table).
  if ("catalog" in o && "table" in o) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1">
        <FieldValue value={o.catalog} />
        <span className="text-zinc-400">·</span>
        <span className="font-mono text-[12px]">{String(o.schema_name)}.{String(o.table)}</span>
      </span>
    );
  }
  // Any other object (properties, trait_config, …) — nested key/value.
  const entries = Object.entries(o);
  if (entries.length === 0) return <span className="text-zinc-400">none</span>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-2 gap-y-0.5">
      {entries.map(([kk, vv]) => (
        <div key={kk} className="contents">
          <dt className="whitespace-nowrap font-mono text-[11px] text-zinc-400">{kk}</dt>
          <dd className="min-w-0 break-words"><FieldValue value={vv} fieldKey={kk} /></dd>
        </div>
      ))}
    </dl>
  );
}

/** snake_case / camelCase field name → human "Title Case" label. */
function humanize(key: string): string {
  const spaced = key.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// ---- the panel --------------------------------------------------------------

export function ChangesetPanel({
  conversationId,
  changeset,
  tests,
  coreDown,
}: {
  conversationId: string;
  changeset: StagedAction[];
  tests: StageTest[];
  coreDown: boolean;
}) {
  const qc = useQueryClient();
  const [apply, setApply] = useState<ApplyResult | null>(null);
  const [testRun, setTestRun] = useState<BuildTestResult | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["conversations", conversationId] });

  const removeMut = useMutation({
    mutationFn: (actionId: string) => removeAction(conversationId, actionId),
    onSuccess: () => { setApply(null); invalidate(); },
  });
  const clearMut = useMutation({
    mutationFn: () => clearChangeset(conversationId),
    onSuccess: () => { setApply(null); setTestRun(null); invalidate(); },
  });
  const testMut = useMutation({
    mutationFn: () => buildAndTestStage(conversationId),
    onSuccess: (r) => setTestRun(r),
  });
  const applyMut = useMutation({
    mutationFn: () => applyChangeset(conversationId),
    onSuccess: (r) => { setApply(r); if (r.applied) invalidate(); },
  });

  if (changeset.length === 0) return null;

  const busy = removeMut.isPending || clearMut.isPending || testMut.isPending || applyMut.isPending;
  const applied = apply?.applied === true;
  const counts = countFamilies(changeset);

  return (
    <section
      className="mx-1 mb-2 rounded border border-zinc-200 bg-white text-sm shadow-sm"
      data-testid="changeset-panel"
    >
      <div
        className="flex items-center gap-2 px-3 py-2"
        data-testid="changeset-header"
      >
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-500">Staged changes</span>
        <span
          className="inline-flex items-center rounded-full bg-zinc-900 px-2 py-0.5 text-[11px] font-medium text-white"
          data-testid="changeset-count"
        >
          {changeset.length}
        </span>
        <span className="hidden text-[11px] text-zinc-400 sm:inline">{counts}</span>
      </div>

      <ol className="border-t border-zinc-100 px-2 py-1">
            {changeset.map((a, i) => (
              <ActionRow
                key={a.id}
                action={a}
                index={i}
                removing={removeMut.isPending && removeMut.variables === a.id}
                disabled={busy || applied}
                onRemove={() => removeMut.mutate(a.id)}
              />
            ))}
          </ol>

          {/* result banners */}
          {apply && <ApplyBanner apply={apply} />}
          {testRun && <TestBanner run={testRun} />}
          {(removeMut.isError || clearMut.isError || testMut.isError || applyMut.isError) && (
            <div className="mx-3 my-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
              {errText(removeMut.error || clearMut.error || testMut.error || applyMut.error)}
            </div>
          )}

          {/* actions */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 px-3 py-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => testMut.mutate()}
                disabled={busy || coreDown}
                className="rounded border border-zinc-200 bg-white px-2.5 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
                data-testid="test-stage"
              >
                {testMut.isPending ? "Testing…" : tests.length ? `Test (${tests.length})` : "Test"}
              </button>
              <button
                type="button"
                onClick={() => clearMut.mutate()}
                disabled={busy || applied}
                className="rounded px-2 py-1 text-xs text-zinc-400 hover:text-red-600 disabled:opacity-40"
                data-testid="clear-changeset"
              >
                Clear
              </button>
            </div>
            <button
              type="button"
              onClick={() => applyMut.mutate()}
              disabled={busy || coreDown || applied}
              className="rounded bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid="apply-changeset"
            >
              {applyMut.isPending ? "Applying…" : applied ? "Applied ✓" : "Apply to production"}
            </button>
          </div>
          {coreDown && (
            <p className="px-3 pb-2 text-[11px] text-amber-700">Core is unreachable — testing and applying are disabled.</p>
          )}
    </section>
  );
}

function ApplyBanner({ apply }: { apply: ApplyResult }) {
  if (apply.applied) {
    const r = apply.result ?? {};
    const bits = Object.entries(r)
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`);
    return (
      <div
        className="mx-3 my-2 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800"
        data-testid="apply-success"
      >
        Applied to production{bits.length ? `: ${bits.join(", ")}` : "."}
      </div>
    );
  }
  return (
    <div
      className="mx-3 my-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
      data-testid="apply-conflict"
    >
      <p className="font-medium">Production drifted — not applied.</p>
      <ul className="mt-1 list-disc pl-4">
        {(apply.conflicts ?? []).map((c, i) => (
          <li key={i}>
            {c.kind}
            {typeof (c as Record<string, unknown>).entity === "string" ? ` · ${(c as Record<string, unknown>).entity}` : ""}
            {typeof (c as Record<string, unknown>).name === "string" ? ` "${(c as Record<string, unknown>).name}"` : ""}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-amber-700">Ask the agent to re-check and re-apply.</p>
    </div>
  );
}

function TestBanner({ run }: { run: BuildTestResult }) {
  if (run.tests.length === 0) {
    return (
      <div className="mx-3 my-2 rounded border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600" data-testid="test-empty">
        No saved acceptance tests yet — ask the agent to add some, or apply directly.
      </div>
    );
  }
  return (
    <div
      className={cn(
        "mx-3 my-2 rounded border px-3 py-2 text-xs",
        run.all_passed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800",
      )}
      data-testid="test-results"
    >
      <p className="font-medium">{run.all_passed ? "All tests passed" : "Some tests failed"}</p>
      <ul className="mt-1 space-y-0.5">
        {run.tests.map((t, i) => (
          <li key={i} className="flex items-center gap-1.5">
            <span>{t.passed ? "✓" : "✕"}</span>
            <span className="font-medium">{t.name ?? "test"}</span>
            {typeof t.objects === "number" && <span className="text-zinc-500">— {t.objects} objects</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function countFamilies(actions: StagedAction[]): string {
  const c = { create: 0, update: 0, delete: 0 };
  for (const a of actions) c[family(a.op)]++;
  return (["create", "update", "delete"] as const)
    .filter((k) => c[k] > 0)
    .map((k) => `${c[k]} ${k}`)
    .join(" · ");
}

function errText(err: unknown): string {
  if (err && typeof err === "object" && "message" in err) return String((err as { message: unknown }).message);
  return "Something went wrong.";
}
