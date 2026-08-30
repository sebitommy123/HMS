import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  deleteCatalog,
  fetchTrinoState,
  getCatalog,
  triggerReconcile,
  updateCatalog,
  type Catalog,
  type ReconcileResult,
} from "@/api/catalogs";
import { ApiError } from "@/api/client";
import { useViewIdentity } from "@/lib/viewContext";
import { CatalogDataSourcesPanel } from "@/components/CatalogDataSourcesPanel";
import { FlexModuleEditor } from "@/components/FlexModuleEditor";
import { StatusBadge } from "@/components/StatusBadge";
import { relativeTime } from "@/lib/format";

export function CatalogDetail() {
  const { name = "" } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const catalog = useQuery({
    queryKey: ["catalogs", name],
    queryFn: () => getCatalog(name),
    enabled: Boolean(name),
  });

  const trinoState = useQuery({
    queryKey: ["trino-state"],
    queryFn: fetchTrinoState,
  });

  const trinoRow = trinoState.data?.find((r) => r.name === name) ?? null;

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [lastReconcile, setLastReconcile] = useState<ReconcileResult | null>(null);

  const deleteMutation = useMutation({
    mutationFn: () => deleteCatalog(name),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["catalogs"] });
      await qc.invalidateQueries({ queryKey: ["trino-state"] });
      navigate("/catalogs");
    },
  });

  const reconcileMutation = useMutation({
    mutationFn: triggerReconcile,
    onSuccess: async (result) => {
      setLastReconcile(result);
      await qc.invalidateQueries({ queryKey: ["catalogs", name] });
      await qc.invalidateQueries({ queryKey: ["trino-state"] });
    },
  });

  useViewIdentity(
    catalog.data?.name,
    catalog.data
      ? {
          type: "catalog",
          name: catalog.data.name,
          connector: catalog.data.connector,
          status: catalog.data.status,
        }
      : undefined,
  );

  if (catalog.isLoading) {
    return <Skeleton />;
  }

  if (catalog.isError) {
    const status =
      catalog.error instanceof ApiError ? catalog.error.status : undefined;
    if (status === 404) {
      return <NotFound name={name} />;
    }
    return <ErrorState message={(catalog.error as Error).message} />;
  }

  if (!catalog.data) {
    return <NotFound name={name} />;
  }

  const row = catalog.data;
  const propertyEntries = Object.entries(row.properties);

  return (
    <div className="space-y-6">
      <Breadcrumb name={row.name} />

      <header className="flex items-start justify-between gap-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{row.name}</h1>
          <div className="flex items-center gap-3">
            <StatusBadge status={row.status} />
            <span className="text-sm text-zinc-600">
              uses connector <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{row.connector}</code>
            </span>
            <span className="text-sm text-zinc-500" data-testid="detail-factory-count">
              {row.factory_count} {row.factory_count === 1 ? "factory" : "factories"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => reconcileMutation.mutate()}
            disabled={reconcileMutation.isPending}
            className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="reconcile-button"
          >
            {reconcileMutation.isPending ? "Reconciling…" : "Reconcile"}
          </button>
          <DeleteButton
            confirming={confirmingDelete}
            pending={deleteMutation.isPending}
            onAsk={() => setConfirmingDelete(true)}
            onCancel={() => setConfirmingDelete(false)}
            onConfirm={() => deleteMutation.mutate()}
          />
        </div>
      </header>

      {row.last_error && (
        <Section title="Last error">
          <pre className="overflow-x-auto rounded bg-red-50 p-3 text-xs text-red-800" data-testid="detail-last-error">
            {row.last_error}
          </pre>
        </Section>
      )}

      {/* Flex catalogs have no client-facing properties — their only property
          (flex.module_path) is Core-managed and hidden. They get the module
          editor below instead. */}
      {row.connector !== "flex" && (
        <Section
          title="Properties"
          action={
            editing ? null : (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="rounded border border-zinc-200 bg-white px-2.5 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
                data-testid="edit-button"
              >
                Edit
              </button>
            )
          }
        >
          {editing ? (
            <CatalogEditor
              catalog={row}
              onCancel={() => setEditing(false)}
              onSaved={(result) => {
                setEditing(false);
                if (result.reconcile) setLastReconcile(result.reconcile);
              }}
            />
          ) : propertyEntries.length === 0 ? (
            <p className="text-sm text-zinc-500">No properties set on this catalog.</p>
          ) : (
            <dl
              className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white text-sm"
              data-testid="properties-list"
            >
              {propertyEntries.map(([k, v]) => (
                <div key={k} className="grid grid-cols-1 gap-1 px-4 py-2 sm:grid-cols-3 sm:gap-3">
                  <dt className="font-medium text-zinc-700">{k}</dt>
                  <dd className="font-mono text-xs text-zinc-700 sm:col-span-2 sm:break-all">
                    {maskSecret(k, v)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Section>
      )}

      {row.connector === "flex" && <FlexModuleEditor catalogName={row.name} />}

      <CatalogDataSourcesPanel catalogName={row.name} />

      {/* Trino-side state is only worth surfacing when it DIVERGES from the
          desired (Postgres) state. A healthy, in-sync catalog needs no
          restatement of what the header already shows (status + connector) —
          drift is the only signal that earns space here. */}
      {!trinoState.isLoading && !trinoRow && (
        <Section title="Trino-side state">
          <div className="rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900" data-testid="trino-missing">
            Not currently registered in Trino. Reconcile to create it.
          </div>
        </Section>
      )}
      {trinoRow && trinoRow.connector !== row.connector && (
        <Section title="Trino-side state">
          <div className="rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700" data-testid="connector-mismatch">
            ⚠ Connector mismatch — Postgres expects <strong>{row.connector}</strong> but Trino has <strong>{trinoRow.connector}</strong>. Reconcile will replace it.
          </div>
        </Section>
      )}

      <Section title="Timestamps">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
          <dt className="text-zinc-500">Created</dt>
          <dd title={row.created_at}>{relativeTime(row.created_at)}</dd>
          <dt className="text-zinc-500">Updated</dt>
          <dd title={row.updated_at}>{relativeTime(row.updated_at)}</dd>
        </dl>
      </Section>

      {lastReconcile && (
        <Section title="Latest reconcile result">
          <ReconcileSummary name={row.name} result={lastReconcile} />
        </Section>
      )}
    </div>
  );
}

function Breadcrumb({ name }: { name: string }) {
  return (
    <nav className="text-sm text-zinc-500">
      <Link to="/catalogs" className="hover:text-zinc-900">Catalogs</Link>
      <span className="mx-2">/</span>
      <span className="text-zinc-700">{name}</span>
    </nav>
  );
}

function Section({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-medium uppercase tracking-wide text-zinc-500">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function CatalogEditor({
  catalog,
  onCancel,
  onSaved,
}: {
  catalog: Catalog;
  onCancel: () => void;
  onSaved: (result: Awaited<ReturnType<typeof updateCatalog>>) => void;
}) {
  const qc = useQueryClient();
  const [connector, setConnector] = useState(catalog.connector);
  const [rows, setRows] = useState<Array<{ key: string; value: string }>>(() =>
    Object.entries(catalog.properties).map(([key, value]) => ({ key, value })),
  );
  // Reset local state whenever the underlying catalog reference changes —
  // happens after a successful save when the cache refreshes.
  useEffect(() => {
    setConnector(catalog.connector);
    setRows(Object.entries(catalog.properties).map(([key, value]) => ({ key, value })));
  }, [catalog]);

  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      const properties: Record<string, string> = {};
      for (const { key, value } of rows) {
        const trimmedKey = key.trim();
        if (!trimmedKey) continue;
        if (Object.prototype.hasOwnProperty.call(properties, trimmedKey)) {
          throw new Error(`Duplicate property key: ${trimmedKey}`);
        }
        properties[trimmedKey] = value;
      }
      return updateCatalog(catalog.name, {
        connector: connector.trim(),
        properties,
      });
    },
    onSuccess: async (result) => {
      setError(null);
      await qc.invalidateQueries({ queryKey: ["catalogs", catalog.name] });
      await qc.invalidateQueries({ queryKey: ["catalogs"] });
      await qc.invalidateQueries({ queryKey: ["trino-state"] });
      onSaved(result);
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    },
  });

  return (
    <div className="space-y-3" data-testid="catalog-editor">
      <div>
        <label
          htmlFor="edit-connector"
          className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
        >
          Connector
        </label>
        <input
          id="edit-connector"
          type="text"
          value={connector}
          onChange={(e) => setConnector(e.target.value)}
          spellCheck={false}
          className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-mono focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          data-testid="edit-connector-input"
        />
        <p className="mt-1 text-xs text-zinc-500">
          Changing the connector forces a DROP+CREATE in Trino.
        </p>
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-zinc-500">
            Properties
          </span>
          <button
            type="button"
            onClick={() => setRows((r) => [...r, { key: "", value: "" }])}
            className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 hover:bg-zinc-50"
            data-testid="add-property-button"
          >
            + Add property
          </button>
        </div>
        <div className="space-y-1.5" data-testid="property-rows">
          {rows.length === 0 ? (
            <p className="text-sm text-zinc-500">No properties.</p>
          ) : (
            rows.map((row, i) => (
              <div key={i} className="flex items-center gap-2">
                <input
                  type="text"
                  placeholder="key"
                  value={row.key}
                  onChange={(e) =>
                    setRows((rs) =>
                      rs.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)),
                    )
                  }
                  spellCheck={false}
                  className="block w-1/3 rounded border border-zinc-200 bg-white px-2 py-1 font-mono text-xs focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
                  data-testid={`property-key-${i}`}
                />
                <input
                  type="text"
                  placeholder="value"
                  value={row.value}
                  onChange={(e) =>
                    setRows((rs) =>
                      rs.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)),
                    )
                  }
                  spellCheck={false}
                  className="block flex-1 rounded border border-zinc-200 bg-white px-2 py-1 font-mono text-xs focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
                  data-testid={`property-value-${i}`}
                />
                <button
                  type="button"
                  onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
                  className="rounded px-2 py-1 text-xs text-zinc-500 hover:bg-zinc-100"
                  aria-label={`Remove property ${row.key || i + 1}`}
                  data-testid={`remove-property-${i}`}
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>
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
          type="button"
          onClick={() => {
            setError(null);
            mutation.mutate();
          }}
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
    </div>
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
    <div className="flex items-center gap-2 rounded border border-red-200 bg-red-50 px-2 py-1" data-testid="delete-confirm">
      <span className="text-sm text-red-800">Delete this catalog?</span>
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
        {pending ? "Deleting…" : "Yes, delete"}
      </button>
    </div>
  );
}

function ReconcileSummary({ name, result }: { name: string; result: ReconcileResult }) {
  const mine = result.actions.filter((a) => a.name === name);
  if (mine.length === 0) {
    return (
      <p className="text-sm text-zinc-600">
        Reconcile ran with no changes needed for this catalog. ({result.actions.length} total
        action{result.actions.length === 1 ? "" : "s"} across all catalogs.)
      </p>
    );
  }
  return (
    <ul className="space-y-1 text-sm" data-testid="reconcile-actions">
      {mine.map((a, i) => (
        <li key={i} className="flex items-center gap-2">
          <span
            className={
              a.ok
                ? "rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-medium uppercase text-emerald-800"
                : "rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium uppercase text-red-800"
            }
          >
            {a.kind}
          </span>
          <span className="text-zinc-700">{a.ok ? "succeeded" : `failed: ${a.error}`}</span>
        </li>
      ))}
    </ul>
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

function NotFound({ name }: { name: string }) {
  return (
    <div className="rounded border border-zinc-200 bg-white p-12 text-center" data-testid="not-found">
      <h1 className="text-lg font-medium text-zinc-900">Catalog not found</h1>
      <p className="mt-2 text-sm text-zinc-600">
        Core has no catalog named <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{name}</code>.
      </p>
      <Link
        to="/catalogs"
        className="mt-4 inline-block text-sm font-medium text-zinc-900 underline underline-offset-2"
      >
        Back to catalogs
      </Link>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded border border-zinc-200 bg-white p-8 text-center" data-testid="error-state">
      <h2 className="text-base font-medium text-red-700">Couldn't load catalog</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
    </div>
  );
}

// Don't display password / secret values verbatim in the detail view. Conservative
// allowlist of substrings — operators can always inspect properties from a shell if
// they truly need the value.
function maskSecret(key: string, value: string): string {
  const sensitive = ["password", "secret", "token", "key"];
  if (sensitive.some((s) => key.toLowerCase().includes(s))) {
    return value ? "••••••••" : "";
  }
  return value;
}
