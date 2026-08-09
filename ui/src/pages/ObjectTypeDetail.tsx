import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  deleteObjectType,
  getObjectType,
  updateObjectType,
  type ObjectType,
} from "@/api/objectTypes";
import { ApiError } from "@/api/client";
import { ObjectTypeFactoriesPanel } from "@/components/ObjectFactoriesPanel";
import { TraitsPanel } from "@/components/TraitsPanel";
import { relativeTime } from "@/lib/format";

const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;

export function ObjectTypeDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const type = useQuery({
    queryKey: ["object-types", id],
    queryFn: () => getObjectType(id),
    enabled: Boolean(id),
  });

  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => deleteObjectType(id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["object-types"] });
      navigate("/object-types");
    },
  });

  if (type.isLoading) return <Skeleton />;
  if (type.isError) {
    const status = type.error instanceof ApiError ? type.error.status : undefined;
    if (status === 404 || status === 400) return <NotFound id={id} />;
    return <ErrorState message={(type.error as Error).message} />;
  }
  if (!type.data) return <NotFound id={id} />;

  const row = type.data;

  return (
    <div className="space-y-6">
      <Breadcrumb name={row.name} />

      <header className="flex items-start justify-between gap-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{row.name}</h1>
          <div className="flex items-center gap-3 text-xs text-zinc-500">
            <span>id</span>
            <code
              className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono"
              data-testid="object-type-id"
            >
              {row.id}
            </code>
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
        <ReadOnlyView row={row} />
      )}
    </div>
  );
}

function ReadOnlyView({ row }: { row: ObjectType }) {
  return (
    <div className="space-y-6">
      <ObjectTypeFactoriesPanel typeId={row.id} />

      <TraitsPanel row={row} />

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
            No description.
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
  row: ObjectType;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(row.name);
  const [description, setDescription] = useState(row.description);
  const [error, setError] = useState<string | null>(null);

  // Reset local form state whenever the underlying row reference changes
  // (e.g. after a successful save when the query cache refreshes).
  useEffect(() => {
    setName(row.name);
    setDescription(row.description);
  }, [row]);

  const mutation = useMutation({
    mutationFn: () => {
      const body: { name?: string; description?: string } = {};
      const trimmedName = name.trim();
      if (trimmedName !== row.name) body.name = trimmedName;
      if (description !== row.description) body.description = description;
      if (Object.keys(body).length === 0) {
        // No change — short-circuit. Treat as success.
        return Promise.resolve(row);
      }
      return updateObjectType(row.id, body);
    },
    onSuccess: async () => {
      setError(null);
      await qc.invalidateQueries({ queryKey: ["object-types", row.id] });
      await qc.invalidateQueries({ queryKey: ["object-types"] });
      onSaved();
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        const body = (err.body ?? {}) as { error?: string; details?: unknown };
        if (body.error === "already_exists") {
          setError(`A type named "${name.trim()}" already exists.`);
          return;
        }
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

  const trimmedName = name.trim();
  const nameValid = NAME_PATTERN.test(trimmedName);

  return (
    <form
      className="space-y-3 rounded border border-zinc-200 bg-zinc-50 p-4"
      data-testid="editor"
      onSubmit={(e) => {
        e.preventDefault();
        if (!trimmedName) {
          setError("Name is required.");
          return;
        }
        if (!nameValid) {
          setError("Name must be alphanumerics, underscores, or hyphens.");
          return;
        }
        setError(null);
        mutation.mutate();
      }}
    >
      <div>
        <label
          htmlFor="edit-name"
          className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
        >
          Name
        </label>
        <input
          id="edit-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          spellCheck={false}
          className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 font-mono text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          data-testid="edit-name-input"
        />
        <p className="mt-1 text-xs text-zinc-500">
          The id stays the same when you rename — any tool or chat referencing this type
          by id keeps working.
        </p>
      </div>
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
      <span className="text-sm text-red-800">Delete this type?</span>
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

function Breadcrumb({ name }: { name: string }) {
  return (
    <nav className="text-sm text-zinc-500">
      <Link to="/object-types" className="hover:text-zinc-900">
        Object Types
      </Link>
      <span className="mx-2">/</span>
      <span className="text-zinc-700">{name}</span>
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
      <h1 className="text-lg font-medium text-zinc-900">Object type not found</h1>
      <p className="mt-2 text-sm text-zinc-600">
        No object type with id{" "}
        <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-xs">{id}</code>.
      </p>
      <Link
        to="/object-types"
        className="mt-4 inline-block text-sm font-medium text-zinc-900 underline underline-offset-2"
      >
        Back to object types
      </Link>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      className="rounded border border-zinc-200 bg-white p-8 text-center"
      data-testid="error-state"
    >
      <h2 className="text-base font-medium text-red-700">Couldn't load type</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
    </div>
  );
}
