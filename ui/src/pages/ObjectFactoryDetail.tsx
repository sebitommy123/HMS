import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  deleteObjectFactory,
  getObjectFactory,
  updateObjectFactory,
  type ObjectFactory,
} from "@/api/objectFactories";
import { getDataSourceColumns } from "@/api/dataSources";
import { useViewIdentity } from "@/lib/viewContext";
import { ApiError } from "@/api/client";
import {
  TraitConfigEditor,
  type FactoryTraitConfig,
} from "@/components/TraitConfigEditor";
import { relativeTime } from "@/lib/format";

export function ObjectFactoryDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const factory = useQuery({
    queryKey: ["object-factories", id],
    queryFn: () => getObjectFactory(id),
    enabled: Boolean(id),
  });

  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => deleteObjectFactory(id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["object-factories"] });
      navigate(-1);
    },
  });

  useViewIdentity(
    factory.data ? "Object factory" : undefined,
    factory.data ? { type: "object_factory", id: factory.data.id } : undefined,
  );

  if (factory.isLoading) return <Skeleton />;
  if (factory.isError) {
    const status = factory.error instanceof ApiError ? factory.error.status : undefined;
    if (status === 404 || status === 400) return <NotFound id={id} />;
    return <ErrorState message={(factory.error as Error).message} />;
  }
  if (!factory.data) return <NotFound id={id} />;

  const row = factory.data;

  return (
    <div className="space-y-6">
      <Breadcrumb row={row} />

      <header className="flex items-start justify-between gap-6">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">
            <Link
              to={`/data-sources/${row.data_source_id}`}
              className="font-mono hover:underline"
              data-testid="parent-source-link"
            >
              {row.data_source_path ?? row.data_source_id}
            </Link>{" "}
            produces{" "}
            <Link
              to={`/object-types/${row.object_type_id}`}
              className="hover:underline"
              data-testid="parent-type-link"
            >
              {row.object_type_name ?? "Object"}
            </Link>{" "}
            objects
          </h1>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <span>Object factory</span>
            <FactoryStatusBadge status={row.status} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          {!editing && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50"
              data-testid="edit-button"
            >
              Edit
            </button>
          )}
          <DeleteButton
            confirming={confirmingDelete}
            pending={deleteMutation.isPending}
            onAsk={() => setConfirmingDelete(true)}
            onCancel={() => setConfirmingDelete(false)}
            onConfirm={() => deleteMutation.mutate()}
          />
        </div>
      </header>

      {editing ? (
        <Editor
          row={row}
          onCancel={() => setEditing(false)}
          onSaved={() => setEditing(false)}
        />
      ) : (
        <ReadOnly row={row} />
      )}
    </div>
  );
}

function ReadOnly({ row }: { row: ObjectFactory }) {
  return (
    <div className="space-y-6">
      {row.status === "broken" && row.last_error && (
        <section data-testid="factory-last-error">
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-red-700">
            Validation error
          </h2>
          <pre className="overflow-x-auto whitespace-pre-wrap rounded border border-red-200 bg-red-50 p-3 font-mono text-xs text-red-800">
            {row.last_error}
          </pre>
          <p className="mt-1 text-xs text-zinc-500">
            The query planner will exclude this factory until the underlying
            issue is fixed. Edit the factory or its data source to resolve.
          </p>
        </section>
      )}

      <Section title="Columns">
        {row.use_all_columns ? (
          <div
            className="rounded border border-zinc-200 bg-white p-3 text-sm text-zinc-800"
            data-testid="columns-all"
          >
            <span className="font-medium">All columns</span> — the factory
            inherits every column from the source automatically.
          </div>
        ) : row.column_spec.length === 0 ? (
          <div
            className="rounded border border-zinc-200 bg-white p-3 text-sm"
            data-testid="columns-specific"
          >
            <p className="text-zinc-500" data-testid="column-spec-empty">
              No columns specified yet — the factory will return nothing
              until you add some.
            </p>
          </div>
        ) : (
          <ol
            className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white text-sm"
            data-testid="columns-specific"
          >
            {row.column_spec.map((col, i) => (
              <li
                key={i}
                className="flex items-baseline gap-3 px-3 py-2 font-mono text-xs text-zinc-800"
              >
                <span className="w-6 text-right text-zinc-400">{i + 1}</span>
                <span className="break-all">{col}</span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <TraitConfigReadOnly row={row} />

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
            No description. Edit to add one.
          </p>
        )}
      </Section>

      <Section title="Timestamps">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-zinc-500">Created</dt>
          <dd title={row.created_at}>{relativeTime(row.created_at)}</dd>
          <dt className="text-zinc-500">Updated</dt>
          <dd title={row.updated_at}>{relativeTime(row.updated_at)}</dd>
        </dl>
      </Section>
    </div>
  );
}

function Editor({
  row,
  onCancel,
  onSaved,
}: {
  row: ObjectFactory;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const [description, setDescription] = useState(row.description);
  const [useAllColumns, setUseAllColumns] = useState(row.use_all_columns);
  const [columnSpec, setColumnSpec] = useState<string[]>(row.column_spec);
  const [traitConfig, setTraitConfig] = useState<FactoryTraitConfig>(
    row.trait_config as FactoryTraitConfig,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDescription(row.description);
    setUseAllColumns(row.use_all_columns);
    setColumnSpec(row.column_spec);
    setTraitConfig(row.trait_config as FactoryTraitConfig);
  }, [row]);

  const mutation = useMutation({
    mutationFn: () => {
      const patch: {
        description?: string;
        use_all_columns?: boolean;
        column_spec?: string[];
        trait_config?: FactoryTraitConfig;
      } = {};
      if (description !== row.description) patch.description = description;
      if (useAllColumns !== row.use_all_columns) patch.use_all_columns = useAllColumns;
      // Strip empty entries on save so users can't accidentally persist blanks.
      const cleanedSpec = columnSpec.map((c) => c.trim()).filter((c) => c.length > 0);
      if (!arraysEqual(cleanedSpec, row.column_spec)) patch.column_spec = cleanedSpec;
      if (
        JSON.stringify(traitConfig) !== JSON.stringify(row.trait_config)
      ) {
        patch.trait_config = traitConfig;
      }
      if (Object.keys(patch).length === 0) return Promise.resolve(row);
      return updateObjectFactory(row.id, patch);
    },
    onSuccess: async () => {
      setError(null);
      await qc.invalidateQueries({ queryKey: ["object-factories"] });
      onSaved();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as { error?: string; details?: unknown };
        if (body.details) {
          setError(
            typeof body.details === "string"
              ? body.details
              : JSON.stringify(body.details),
          );
          return;
        }
        setError(`${body.error ?? "Request failed"} (HTTP ${err.status})`);
        return;
      }
      setError((err as Error).message);
    },
  });

  return (
    <form
      className="space-y-3 rounded border border-zinc-200 bg-zinc-50 p-4"
      data-testid="editor"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        mutation.mutate();
      }}
    >
      <div>
        <label className="flex items-start gap-2 text-sm text-zinc-800">
          <input
            type="checkbox"
            checked={useAllColumns}
            onChange={(e) => setUseAllColumns(e.target.checked)}
            className="mt-0.5"
            data-testid="use-all-columns-toggle"
          />
          <span>
            <span className="font-medium">Use all columns automatically</span>
            <span className="block text-xs text-zinc-500">
              When on, the factory inherits every column from the source. Turn
              off to specify columns explicitly below.
            </span>
          </span>
        </label>
      </div>

      <ColumnsField
        dataSourceId={row.data_source_id}
        columnSpec={columnSpec}
        onChange={setColumnSpec}
        disabled={useAllColumns}
      />

      {row.object_type_traits.length > 0 && (
        <TraitConfigEditor
          enabledTraitNames={row.object_type_traits}
          dataSourceId={row.data_source_id}
          value={traitConfig}
          onChange={setTraitConfig}
        />
      )}

      <div>
        <label
          htmlFor="edit-description"
          className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
        >
          Description
        </label>
        <textarea
          id="edit-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={4}
          className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          data-testid="edit-description-input"
        />
      </div>

      {error && (
        <div
          className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
          data-testid="edit-error"
        >
          {error}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="save-button"
        >
          {mutation.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          onClick={() => {
            setError(null);
            onCancel();
          }}
          disabled={mutation.isPending}
          className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="cancel-edit-button"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function ColumnsField({
  dataSourceId,
  columnSpec,
  onChange,
  disabled,
}: {
  dataSourceId: string;
  columnSpec: string[];
  onChange: (next: string[]) => void;
  disabled: boolean;
}) {
  // Fetch the data source's live column list so the dropdown matches
  // what Core will accept on save. Cached for 30s so repeat opens of the
  // editor are instant.
  const cols = useQuery({
    queryKey: ["data-source-columns", dataSourceId],
    queryFn: () => getDataSourceColumns(dataSourceId),
    staleTime: 30_000,
    retry: false,
  });

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-zinc-500">
          Columns
        </span>
        <button
          type="button"
          onClick={() => onChange([...columnSpec, ""])}
          disabled={disabled || cols.isLoading || cols.isError}
          className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="add-column-button"
        >
          + Add column
        </button>
      </div>

      {cols.isError ? (
        <ColumnsFetchError error={cols.error} />
      ) : cols.isLoading ? (
        <p className="rounded border border-dashed border-zinc-200 bg-white p-3 text-sm text-zinc-500">
          Loading columns from Trino...
        </p>
      ) : (
        <ColumnsPickerList
          columnSpec={columnSpec}
          available={cols.data!.columns.map((c) => ({ name: c.name, type: c.type }))}
          disabled={disabled}
          onChange={onChange}
        />
      )}

      <p className="mt-1 text-xs text-zinc-500">
        Pick from columns the data source actually exposes. Order matters in
        the result table. Disabled while &ldquo;use all columns&rdquo; is on.
      </p>
    </div>
  );
}

function ColumnsPickerList({
  columnSpec,
  available,
  disabled,
  onChange,
}: {
  columnSpec: string[];
  available: { name: string; type: string }[];
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  if (columnSpec.length === 0) {
    return (
      <p
        className="rounded border border-dashed border-zinc-200 bg-white p-3 text-sm text-zinc-500"
        data-testid="columns-editor-empty"
      >
        No columns yet. Click <strong>+ Add column</strong> to add one.
      </p>
    );
  }
  return (
    <ol className="space-y-1.5" data-testid="columns-editor">
      {columnSpec.map((v, i) => {
        // Stale entry guard: if a stored column no longer exists on the
        // source (someone dropped it upstream), flag the row so the user
        // knows the save will fail until they pick a real column.
        const stale =
          v !== "" && !available.some((c) => c.name === v);
        return (
          <li key={i} className="flex items-center gap-2">
            <span className="w-6 text-right text-xs text-zinc-400">{i + 1}</span>
            <select
              value={v}
              disabled={disabled}
              onChange={(e) =>
                onChange(columnSpec.map((c, j) => (j === i ? e.target.value : c)))
              }
              className={`block flex-1 rounded border bg-white px-2 py-1 font-mono text-xs focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400 disabled:cursor-not-allowed disabled:bg-zinc-100 disabled:text-zinc-400 ${
                stale ? "border-red-300" : "border-zinc-200"
              }`}
              data-testid={`column-input-${i}`}
            >
              <option value="">— pick a column —</option>
              {/* If the current value is stale (no longer in available),
                  show it anyway so the user can see what's wrong. */}
              {stale && (
                <option value={v} disabled>
                  {v} (not in source)
                </option>
              )}
              {available.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} · {c.type}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => onChange(columnSpec.filter((_, j) => j !== i))}
              disabled={disabled}
              aria-label={`Remove column ${i + 1}`}
              className="rounded px-2 py-1 text-xs text-zinc-500 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50"
              data-testid={`remove-column-${i}`}
            >
              ✕
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function ColumnsFetchError({ error }: { error: unknown }) {
  // If we can't read columns, the user can't safely pick them. Surface
  // the Trino error and disable adding rows (handled by the parent).
  const message =
    error instanceof ApiError
      ? (() => {
          const body = (error.body ?? {}) as { error?: string; details?: unknown };
          const detail =
            typeof body.details === "string"
              ? body.details
              : JSON.stringify(body.details ?? "");
          return `${body.error ?? "Request failed"}: ${detail}`;
        })()
      : (error as Error).message ?? String(error);

  return (
    <div
      className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"
      data-testid="columns-fetch-error"
    >
      <div className="font-medium">Couldn't read columns from Trino</div>
      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-xs">
        {message}
      </pre>
    </div>
  );
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function TraitConfigReadOnly({ row }: { row: ObjectFactory }) {
  // Walk the parent type's declared traits (denormalized onto the
  // factory) so the section shows every required slot — including ones
  // the factory hasn't configured yet (highlighted as missing) — not
  // only the keys present in the dict.
  if (row.object_type_traits.length === 0) {
    return null;
  }
  return (
    <Section title="Trait configuration">
      <ul
        className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white text-sm"
        data-testid="trait-config-readonly"
      >
        {row.object_type_traits.map((traitName) => {
          const slot = row.trait_config[traitName];
          const column =
            slot && typeof slot.column === "string" ? slot.column : "";
          const missing = !column;
          return (
            <li
              key={traitName}
              className="flex items-baseline gap-3 px-3 py-2"
              data-testid={`trait-config-row-${traitName}`}
            >
              <span className="font-mono text-xs font-medium text-zinc-700">
                {traitName}
              </span>
              {missing ? (
                <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-red-800">
                  not configured
                </span>
              ) : (
                <span className="text-xs text-zinc-600">
                  column ={" "}
                  <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono">
                    {column}
                  </code>
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}


function FactoryStatusBadge({ status }: { status: ObjectFactory["status"] }) {
  const tone =
    status === "ok"
      ? "bg-emerald-100 text-emerald-800"
      : "bg-red-100 text-red-800";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${tone}`}
      data-testid="factory-status-badge"
    >
      {status}
    </span>
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

function DeleteButton({
  confirming,
  pending,
  onAsk,
  onCancel,
  onConfirm,
}: {
  confirming: boolean;
  pending: boolean;
  onAsk: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!confirming) {
    return (
      <button
        type="button"
        onClick={onAsk}
        className="rounded border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50"
        data-testid="delete-button"
      >
        Delete
      </button>
    );
  }
  return (
    <div
      className="flex items-center gap-2 rounded border border-red-200 bg-red-50 px-2 py-1"
      data-testid="delete-confirm"
    >
      <span className="text-sm text-red-800">Delete this factory?</span>
      <button
        type="button"
        onClick={onCancel}
        className="rounded px-2 py-1 text-sm font-medium text-zinc-700 hover:bg-white"
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={onConfirm}
        disabled={pending}
        className="rounded bg-red-600 px-2 py-1 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
        data-testid="delete-confirm-button"
      >
        {pending ? "Deleting..." : "Yes, delete"}
      </button>
    </div>
  );
}

function Breadcrumb({ row }: { row: ObjectFactory }) {
  return (
    <nav className="text-sm text-zinc-500">
      <Link to={`/catalogs/${row.catalog_name}`} className="hover:text-zinc-900">
        Catalogs / {row.catalog_name}
      </Link>
      <span className="mx-2">/</span>
      <Link
        to={`/data-sources/${row.data_source_id}`}
        className="font-mono hover:text-zinc-900"
      >
        {row.schema_name}.{row.table_name}
      </Link>
      <span className="mx-2">/</span>
      <span className="text-zinc-700">
        {row.object_type_name ?? row.id.slice(0, 8)} Object Factory
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
      <h1 className="text-lg font-medium text-zinc-900">Object factory not found</h1>
      <p className="mt-2 text-sm text-zinc-600">
        No factory with id{" "}
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
      <h2 className="text-base font-medium text-red-700">Couldn't load factory</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
    </div>
  );
}
