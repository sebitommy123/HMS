import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { AI_URL } from "@/api/ai-client";
import { CORE_URL } from "@/api/client";
import { fetchTrinoState, listCatalogs } from "@/api/catalogs";

export function Home() {
  const catalogs = useQuery({ queryKey: ["catalogs"], queryFn: listCatalogs });
  const trino = useQuery({ queryKey: ["trino-state"], queryFn: fetchTrinoState });

  const rows = catalogs.data ?? [];
  const enabled = rows.filter((c) => c.status === "enabled").length;
  const disabled = rows.filter((c) => c.status === "disabled").length;
  const broken = rows.filter((c) => c.status === "broken").length;

  const trinoCount = trino.data?.length ?? 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="text-sm text-zinc-600">
          Operator dashboard for DataPro. Manage catalogs, inspect Trino state, run ad-hoc queries.
          Header indicators reflect Core's live connection to its backends.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <Stat label="Total catalogs" value={rows.length} />
        <Stat label="Enabled" value={enabled} tone="ok" />
        <Stat label="Broken" value={broken} tone={broken > 0 ? "bad" : "muted"} />
        <Stat label="Live in Trino" value={trinoCount} />
      </div>

      {disabled > 0 && (
        <p className="text-sm text-zinc-600">{disabled} disabled (won't be reconciled into Trino).</p>
      )}

      <section className="rounded-lg border border-zinc-200 bg-white p-5">
        <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-500">Backends</h2>
        <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto,1fr]">
          <dt className="font-medium text-zinc-700">Core</dt>
          <dd><code className="text-zinc-600">{CORE_URL}</code></dd>
          <dt className="font-medium text-zinc-700">AI</dt>
          <dd><code className="text-zinc-600">{AI_URL}</code></dd>
        </dl>
        <p className="mt-3 text-sm text-zinc-600">
          Manage catalogs via{" "}
          <Link to="/catalogs" className="font-medium text-zinc-900 underline underline-offset-2">
            the Catalogs page
          </Link>
          , or talk to Claude on{" "}
          <Link to="/chats" className="font-medium text-zinc-900 underline underline-offset-2">
            the Chat page
          </Link>
          .
        </p>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "ok" | "bad" | "muted";
}) {
  const toneClass =
    tone === "ok"
      ? "text-emerald-700"
      : tone === "bad"
        ? "text-red-700"
        : "text-zinc-900";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}
