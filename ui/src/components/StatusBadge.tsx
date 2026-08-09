import { cn } from "@/lib/utils";
import type { CatalogStatus } from "@/api/catalogs";

const TONES: Record<CatalogStatus, string> = {
  enabled: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  disabled: "bg-zinc-100 text-zinc-700 ring-zinc-200",
  broken: "bg-red-100 text-red-800 ring-red-200",
  // Registered in Trino but its backing store is unreachable — amber, a
  // warning distinct from broken (which means it never registered).
  down: "bg-amber-100 text-amber-800 ring-amber-200",
};

export function StatusBadge({ status }: { status: CatalogStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium uppercase tracking-wide ring-1 ring-inset",
        TONES[status],
      )}
      data-testid={`status-${status}`}
    >
      {status}
    </span>
  );
}
