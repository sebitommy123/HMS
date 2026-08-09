/**
 * Object type → traits toggle panel.
 *
 * Lists every trait Core's registry knows about as a checkbox; flipping
 * a checkbox PUTs or DELETEs the per-trait endpoint. Optimistic UI
 * isn't worth it here — the operations are fast and the response is
 * the authoritative ObjectType row.
 *
 * Caveat surfaced in the help text: adding a trait can flip factories
 * under this type to broken (missing trait_config). The factory cards
 * elsewhere on the page already render the broken state, so we don't
 * try to lift that warning up here.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  addTraitToObjectType,
  listTraits,
  removeTraitFromObjectType,
  type TraitDescriptor,
} from "@/api/traits";
import type { ObjectType } from "@/api/objectTypes";

export function TraitsPanel({ row }: { row: ObjectType }) {
  const qc = useQueryClient();
  const known = useQuery({
    queryKey: ["traits"],
    queryFn: listTraits,
    staleTime: 5 * 60_000, // registry is hardcoded; effectively static.
  });

  const mutation = useMutation({
    mutationFn: async ({
      trait,
      checked,
    }: {
      trait: string;
      checked: boolean;
    }) => {
      return checked
        ? addTraitToObjectType(row.id, trait)
        : removeTraitFromObjectType(row.id, trait);
    },
    onSuccess: async () => {
      // Re-fetch the type (carries the new traits[] array) and any
      // factories under it (their status badges may have flipped).
      await qc.invalidateQueries({ queryKey: ["object-types", row.id] });
      await qc.invalidateQueries({ queryKey: ["object-factories"] });
    },
  });

  if (known.isLoading) {
    return (
      <Section>
        <p className="text-sm text-zinc-500">Loading traits...</p>
      </Section>
    );
  }
  if (known.isError) {
    return (
      <Section>
        <p className="text-sm text-red-700">
          Couldn't load trait registry: {(known.error as Error).message}
        </p>
      </Section>
    );
  }

  const enabled = new Set(row.traits);
  const errorMsg =
    mutation.isError && mutation.error instanceof ApiError
      ? `${(mutation.error.body as { error?: string })?.error ?? "Request failed"} (HTTP ${mutation.error.status})`
      : mutation.isError
        ? (mutation.error as Error).message
        : null;

  return (
    <Section>
      <ul className="divide-y divide-zinc-100 rounded border border-zinc-200 bg-white">
        {known.data!.map((t) => (
          <TraitRow
            key={t.name}
            trait={t}
            checked={enabled.has(t.name)}
            disabled={mutation.isPending}
            onToggle={(checked) =>
              mutation.mutate({ trait: t.name, checked })
            }
          />
        ))}
      </ul>
      <p className="mt-2 text-xs text-zinc-500">
        Toggling a trait re-validates every factory under this type. Factories
        that don't yet have matching trait config will show as broken — set
        their trait config on the factory page.
      </p>
      {errorMsg && (
        <div
          className="mt-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
          data-testid="trait-toggle-error"
        >
          {errorMsg}
        </div>
      )}
    </Section>
  );
}

function TraitRow({
  trait,
  checked,
  disabled,
  onToggle,
}: {
  trait: TraitDescriptor;
  checked: boolean;
  disabled: boolean;
  onToggle: (checked: boolean) => void;
}) {
  return (
    <li className="flex items-start gap-3 px-3 py-2.5">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-1"
        data-testid={`trait-toggle-${trait.name}`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-sm font-medium text-zinc-900">
            {trait.name}
          </span>
          {trait.required_config_keys.length > 0 && (
            <span className="text-[10px] uppercase tracking-wide text-zinc-400">
              needs: {trait.required_config_keys.join(", ")}
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-zinc-600">{trait.description}</p>
      </div>
    </li>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">
        Traits
      </h2>
      {children}
    </section>
  );
}
