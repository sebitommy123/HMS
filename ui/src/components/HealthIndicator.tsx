import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/api/health";
import { fetchAiHealth } from "@/api/conversations";
import { cn } from "@/lib/utils";

export function HealthIndicator() {
  const core = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  });
  const ai = useQuery({
    queryKey: ["ai-health"],
    queryFn: fetchAiHealth,
    refetchInterval: 10_000,
  });

  if (core.isPending) {
    return (
      <div className="flex items-center gap-3 text-sm" data-testid="health-indicator">
        <Dot label="checking…" tone="muted" />
      </div>
    );
  }

  if (core.isError || !core.data) {
    return (
      <div className="flex items-center gap-3 text-sm" data-testid="health-indicator">
        <Dot label="core" tone="bad" />
        <AiDot ai={ai} />
        <Pill tone="bad">offline</Pill>
      </div>
    );
  }

  const overall: Tone = core.data.status === "ok" ? "ok" : "warn";

  return (
    <div className="flex items-center gap-3 text-sm" data-testid="health-indicator">
      <Dot
        label="postgres"
        tone={core.data.postgres === "reachable" ? "ok" : "bad"}
      />
      <Dot
        label="trino"
        tone={core.data.trino === "reachable" ? "ok" : "bad"}
      />
      <AiDot ai={ai} />
      <Pill tone={overall}>{core.data.status}</Pill>
    </div>
  );
}

// Single dot for the AI service. Three states: pending (muted), reachable
// (ok unless anthropic key is missing — then warn), or unreachable (bad).
// The dot color carries the up/down signal; only the "no key" case gets a
// label suffix because that's a distinct state the color alone can't show.
function AiDot({
  ai,
}: {
  ai: ReturnType<typeof useQuery<Awaited<ReturnType<typeof fetchAiHealth>>>>;
}) {
  if (ai.isPending) {
    return <Dot label="ai" tone="muted" />;
  }
  if (ai.isError || !ai.data) {
    return <Dot label="ai" tone="bad" />;
  }
  if (ai.data.anthropic === "missing") {
    return <Dot label="ai: no key" tone="warn" />;
  }
  return <Dot label="ai" tone="ok" />;
}

type Tone = "ok" | "warn" | "bad" | "muted";

function Dot({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-zinc-600">
      <span
        className={cn(
          "size-2 rounded-full",
          tone === "ok" && "bg-emerald-500",
          tone === "warn" && "bg-amber-500",
          tone === "bad" && "bg-red-500",
          tone === "muted" && "bg-zinc-300",
        )}
      />
      {label}
    </span>
  );
}

function Pill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded px-2 py-0.5 text-xs font-medium uppercase tracking-wide",
        tone === "ok" && "bg-emerald-100 text-emerald-800",
        tone === "warn" && "bg-amber-100 text-amber-800",
        tone === "bad" && "bg-red-100 text-red-800",
        tone === "muted" && "bg-zinc-100 text-zinc-700",
      )}
    >
      {children}
    </span>
  );
}
