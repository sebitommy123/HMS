import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  createObjectType,
  listObjectTypes,
  type ObjectType,
} from "@/api/objectTypes";
import { ApiError } from "@/api/client";
import { relativeTime } from "@/lib/format";

const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;

export function ObjectTypes() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);

  const types = useQuery({
    queryKey: ["object-types", { search }],
    queryFn: () => listObjectTypes(search),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Object Types</h1>
        <button
          type="button"
          onClick={() => setCreating((c) => !c)}
          className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800"
          data-testid={creating ? "cancel-new-button" : "new-button"}
        >
          {creating ? "Cancel" : "+ New"}
        </button>
      </div>

      <p className="text-sm text-zinc-600">
        Object types are the kinds of things DataPro can talk about — e.g.{" "}
        <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs">Company</code> or{" "}
        <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs">Filing</code>. Fields and
        traits come in a later slice.
      </p>

      {creating && (
        <NewObjectTypeForm
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void qc.invalidateQueries({ queryKey: ["object-types"] });
          }}
        />
      )}

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name or description…"
        spellCheck={false}
        className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
        data-testid="search-input"
      />

      {types.isLoading ? (
        <Skeleton />
      ) : types.isError ? (
        <ErrorState message={(types.error as Error).message} />
      ) : types.data && types.data.length > 0 ? (
        <TypesList rows={types.data} />
      ) : (
        <EmptyState search={search} />
      )}
    </div>
  );
}

function TypesList({ rows }: { rows: ObjectType[] }) {
  return (
    <ul
      className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white"
      data-testid="object-types-list"
    >
      {rows.map((row) => (
        <li key={row.id}>
          <Link
            to={`/object-types/${row.id}`}
            className="block px-4 py-3 hover:bg-zinc-50"
            data-testid={`object-type-row-${row.name}`}
          >
            <div className="flex items-baseline justify-between gap-4">
              <span className="font-medium text-zinc-900">{row.name}</span>
              <span className="text-[11px] text-zinc-500" title={row.updated_at}>
                updated {relativeTime(row.updated_at)}
              </span>
            </div>
            {row.description && (
              <p className="mt-1 line-clamp-2 text-sm text-zinc-600">{row.description}</p>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function NewObjectTypeForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => createObjectType({ name: name.trim(), description }),
    onSuccess: onCreated,
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

  const nameValid = NAME_PATTERN.test(name.trim());

  return (
    <form
      className="space-y-2 rounded border border-zinc-200 bg-zinc-50 p-4"
      data-testid="new-object-type-form"
      onSubmit={(e) => {
        e.preventDefault();
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
          htmlFor="new-name"
          className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
        >
          Name
        </label>
        <input
          id="new-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          spellCheck={false}
          className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 font-mono text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          data-testid="new-name-input"
        />
      </div>
      <div>
        <label
          htmlFor="new-description"
          className="mb-1 block text-xs font-medium uppercase tracking-wide text-zinc-500"
        >
          Description
        </label>
        <textarea
          id="new-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="block w-full rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm focus:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-400"
          data-testid="new-description-input"
        />
      </div>

      {error && (
        <div
          className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
          data-testid="new-error"
        >
          {error}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          type="submit"
          disabled={mutation.isPending || !name.trim()}
          className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
          data-testid="create-button"
        >
          {mutation.isPending ? "Creating…" : "Create"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={mutation.isPending}
          className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function EmptyState({ search }: { search: string }) {
  return (
    <div
      className="rounded border border-dashed border-zinc-200 p-8 text-center text-sm text-zinc-500"
      data-testid="empty-state"
    >
      {search
        ? `No object types match "${search}".`
        : "No object types yet. Click + New to create your first one."}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-2" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800"
      data-testid="error-state"
    >
      Couldn't load object types: {message}
    </div>
  );
}
