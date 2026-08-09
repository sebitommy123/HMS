import { cn } from "@/lib/utils";
import type { DriftVerdict } from "@/lib/drift";

const TONE: Record<DriftVerdict, string> = {
  aligned: "bg-emerald-100 text-emerald-800 ring-emerald-200",
  "missing-in-trino": "bg-amber-100 text-amber-900 ring-amber-200",
  "extra-in-trino": "bg-amber-100 text-amber-900 ring-amber-200",
  "connector-mismatch": "bg-red-100 text-red-800 ring-red-200",
  broken: "bg-red-100 text-red-800 ring-red-200",
  "disabled-but-present": "bg-amber-100 text-amber-900 ring-amber-200",
};

const LABEL: Record<DriftVerdict, string> = {
  aligned: "aligned",
  "missing-in-trino": "missing in trino",
  "extra-in-trino": "extra in trino",
  "connector-mismatch": "connector mismatch",
  broken: "broken",
  "disabled-but-present": "disabled but present",
};

export function DriftBadge({ verdict }: { verdict: DriftVerdict }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium uppercase tracking-wide ring-1 ring-inset",
        TONE[verdict],
      )}
      data-testid={`drift-${verdict}`}
    >
      {LABEL[verdict]}
    </span>
  );
}
