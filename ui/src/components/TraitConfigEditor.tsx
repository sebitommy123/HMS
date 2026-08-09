/**
 * Per-trait config editor for an object factory.
 *
 * Knows about each trait's input shape — Identity + Temporal both
 * currently want a single `column` field, so the layout is one
 * grouped section per enabled trait, each picking from the data
 * source's live columns. If a new trait with non-column config
 * shows up later, branch in `TraitFields` rather than try to
 * generalize prematurely.
 *
 * The trait_config dict is the value of this control; the editor
 * mutates a local copy and bubbles changes up via `onChange`. Saving
 * is the parent's responsibility — this component never PATCHes.
 */

import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { getDataSourceColumns } from "@/api/dataSources";
import { listTraits, type TraitDescriptor } from "@/api/traits";

export type FactoryTraitConfig = Record<string, Record<string, unknown>>;

export function TraitConfigEditor({
  enabledTraitNames,
  dataSourceId,
  value,
  onChange,
}: {
  enabledTraitNames: string[];
  dataSourceId: string;
  value: FactoryTraitConfig;
  onChange: (next: FactoryTraitConfig) => void;
}) {
  const cols = useQuery({
    queryKey: ["data-source-columns", dataSourceId],
    queryFn: () => getDataSourceColumns(dataSourceId),
    staleTime: 30_000,
    retry: false,
  });
  const known = useQuery({
    queryKey: ["traits"],
    queryFn: listTraits,
    staleTime: 5 * 60_000,
  });

  // The parent type may have traits the registry no longer knows
  // about (mid-deploy). Render those as a plain warning rather than
  // crashing — matches the way factory_validator surfaces it.
  if (enabledTraitNames.length === 0) {
    return null;
  }

  return (
    <div>
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500">
        Trait configuration
      </span>
      <div className="space-y-2">
        {enabledTraitNames.map((name) => {
          const descriptor = known.data?.find((t) => t.name === name);
          return (
            <TraitSection
              key={name}
              traitName={name}
              descriptor={descriptor}
              available={cols.data?.columns ?? []}
              loadingColumns={cols.isLoading}
              columnError={cols.isError ? cols.error : null}
              value={value[name] ?? {}}
              onChange={(traitSlot) => {
                onChange({ ...value, [name]: traitSlot });
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

function TraitSection({
  traitName,
  descriptor,
  available,
  loadingColumns,
  columnError,
  value,
  onChange,
}: {
  traitName: string;
  descriptor: TraitDescriptor | undefined;
  available: { name: string; type: string }[];
  loadingColumns: boolean;
  columnError: unknown;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  return (
    <fieldset
      className="rounded border border-zinc-200 bg-white px-3 py-2.5"
      data-testid={`trait-config-${traitName}`}
    >
      <legend className="px-1 font-mono text-xs font-medium text-zinc-700">
        {traitName}
      </legend>
      {descriptor?.description && (
        <p className="mb-2 text-xs text-zinc-500">{descriptor.description}</p>
      )}

      {/* Both shipped traits (identity + temporal) want a single
          `column` field. If a new trait ever wants a different shape,
          branch on traitName here instead of generalizing. */}
      <TraitColumnField
        traitName={traitName}
        available={available}
        loading={loadingColumns}
        error={columnError}
        value={typeof value.column === "string" ? value.column : ""}
        onChange={(col) =>
          onChange(col === "" ? { ...value, column: "" } : { ...value, column: col })
        }
      />
    </fieldset>
  );
}

function TraitColumnField({
  traitName,
  available,
  loading,
  error,
  value,
  onChange,
}: {
  traitName: string;
  available: { name: string; type: string }[];
  loading: boolean;
  error: unknown;
  value: string;
  onChange: (next: string) => void;
}) {
  if (error) {
    const message =
      error instanceof ApiError
        ? (() => {
            const body = (error.body ?? {}) as {
              error?: string;
              details?: unknown;
            };
            const detail =
              typeof body.details === "string"
                ? body.details
                : JSON.stringify(body.details ?? "");
            return `${body.error ?? "Request failed"}: ${detail}`;
          })()
        : (error as Error).message ?? String(error);
    return (
      <p
        className="rounded border border-red-200 bg-red-50 p-2 font-mono text-xs text-red-800"
        data-testid={`trait-column-error-${traitName}`}
      >
        Couldn't load columns: {message}
      </p>
    );
  }

  // Stale-entry guard: the saved column might no longer exist on the
  // source (someone dropped it upstream). Surface it so the user can
  // see why their factory is broken.
  const stale = value !== "" && !available.some((c) => c.name === value);

  return (
    <label className="flex flex-col gap-1 text-xs text-zinc-700">
      <span className="font-medium uppercase tracking-wide text-zinc-500">
        Column
      </span>
      <select
        value={value}
        disabled={loading}
        onChange={(e) => onChange(e.target.value)}
        className={`block w-full rounded border bg-white px-2 py-1 font-mono text-xs focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400 disabled:cursor-not-allowed disabled:bg-zinc-100 ${
          stale ? "border-red-300" : "border-zinc-200"
        }`}
        data-testid={`trait-column-${traitName}`}
      >
        <option value="">— pick a column —</option>
        {stale && (
          <option value={value} disabled>
            {value} (not in source)
          </option>
        )}
        {available.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name} · {c.type}
          </option>
        ))}
      </select>
    </label>
  );
}
