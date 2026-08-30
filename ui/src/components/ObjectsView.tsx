/**
 * Primary render for a semantic query result: the interpreted HMS objects,
 * compact.
 *
 * Every distinct data source in the result gets a stable letter (A, B, C…) and
 * a color, shown once in each card's legend. Below that, fields are little
 * flowing badges — never full rows. Single-source fields sit in a section per
 * source (tinted that source's color); multi-source fields sit in a "Shared"
 * section where each badge references its sources by colored letter chip:
 *   - all sources agree  → the value once + the letters that agree
 *   - sources disagree   → each distinct value with the letters claiming it
 * Hovering any key-value badge pops a small ungrouped table of every source →
 * its value for that field (colorblind-safe: letters always accompany color).
 */

import type { HmsObject } from "@/api/query";

// Static Tailwind class strings (v4 scans source literally, so no interpolation).
const PALETTE = [
  { dot: "bg-indigo-500", chip: "bg-indigo-100 text-indigo-800", soft: "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-200" },
  { dot: "bg-emerald-500", chip: "bg-emerald-100 text-emerald-800", soft: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200" },
  { dot: "bg-amber-500", chip: "bg-amber-100 text-amber-800", soft: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200" },
  { dot: "bg-rose-500", chip: "bg-rose-100 text-rose-800", soft: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200" },
  { dot: "bg-sky-500", chip: "bg-sky-100 text-sky-800", soft: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200" },
  { dot: "bg-violet-500", chip: "bg-violet-100 text-violet-800", soft: "bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-200" },
  { dot: "bg-teal-500", chip: "bg-teal-100 text-teal-800", soft: "bg-teal-50 text-teal-700 ring-1 ring-inset ring-teal-200" },
  { dot: "bg-orange-500", chip: "bg-orange-100 text-orange-800", soft: "bg-orange-50 text-orange-800 ring-1 ring-inset ring-orange-200" },
] as const;

export type SourceStyle = (typeof PALETTE)[number] & { letter: string };
export type Legend = Map<string, SourceStyle>;

function letterFor(i: number): string {
  return i < 26 ? String.fromCharCode(65 + i) : `S${i}`;
}

/** Distinct data source paths across objects, in first-appearance order. */
export function collectPaths(objects: HmsObject[]): string[] {
  const order: string[] = [];
  for (const o of objects) {
    for (const ds of o.data_sources) if (!order.includes(ds)) order.push(ds);
  }
  return order;
}

/** Assign each path (in the given order) a stable letter + color, so "A" means
 * the same source everywhere it's referenced. Build ONCE per result from the
 * canonical source order (factory order) and share it across every consumer. */
export function buildLegend(paths: string[]): Legend {
  const legend: Legend = new Map();
  paths.forEach((path, i) => {
    legend.set(path, { ...PALETTE[i % PALETTE.length], letter: letterFor(i) });
  });
  return legend;
}

/** A source's colored letter chip — reused by the factories-used list so it
 * matches the object cards' legend exactly. */
export function SourceLetter({ path, legend }: { path: string; legend: Legend }) {
  const s = legend.get(path);
  if (!s) return null;
  return (
    <span
      data-testid={`source-letter-${path}`}
      className={`rounded px-1 text-[10px] font-bold ${s.chip}`}
    >
      {s.letter}
    </span>
  );
}

export function ObjectsView({
  objects,
  legend,
}: {
  objects: HmsObject[];
  legend?: Legend;
}) {
  if (objects.length === 0) {
    return (
      <p
        className="rounded border border-dashed border-zinc-200 p-4 text-sm text-zinc-500"
        data-testid="objects-empty"
      >
        No objects returned.
      </p>
    );
  }
  // Fall back to a legend built from the objects themselves when the parent
  // doesn't supply the shared one (e.g. standalone use / tests).
  const lg = legend ?? buildLegend(collectPaths(objects));
  return (
    <div className="space-y-2" data-testid="objects-view">
      {objects.map((obj, i) => (
        <ObjectCard key={i} object={obj} index={i} legend={lg} />
      ))}
    </div>
  );
}

function ObjectCard({
  object,
  index,
  legend,
}: {
  object: HmsObject;
  index: number;
  legend: Legend;
}) {
  const soloBySource = new Map<string, [string, unknown][]>();
  const shared: { name: string; values: Record<string, unknown> }[] = [];
  for (const [name, sources] of Object.entries(object.fields)) {
    const keys = Object.keys(sources);
    if (keys.length === 1) {
      const list = soloBySource.get(keys[0]) ?? [];
      list.push([name, sources[keys[0]]]);
      soloBySource.set(keys[0], list);
    } else if (keys.length > 1) {
      shared.push({ name, values: sources });
    }
  }

  return (
    <article
      className="space-y-2 rounded-lg border border-zinc-200 bg-white p-3"
      data-testid={`object-card-${index}`}
    >
      {/* id + source legend */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {object.id !== undefined && object.id !== null && (
          <span
            className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono text-xs font-semibold text-white"
            data-testid="object-id"
          >
            {stringify(object.id)}
          </span>
        )}
        <div className="flex flex-wrap items-center gap-1">
          {object.data_sources.map((path) => {
            const s = legend.get(path);
            if (!s) return null;
            return (
              <span
                key={path}
                data-testid={`legend-${path}`}
                className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] ${s.soft}`}
              >
                <span className="font-bold">{s.letter}</span>
                <span className="font-mono opacity-80">{path}</span>
              </span>
            );
          })}
        </div>
      </header>

      {/* single-source fields, grouped by their source (in the object's order) */}
      {object.data_sources.map((path) => {
        const solo = soloBySource.get(path);
        const s = legend.get(path);
        if (!solo || solo.length === 0 || !s) return null;
        return (
          <section key={path} data-testid={`object-source-group-${path}`}>
            <div className="mb-0.5 flex items-center gap-1">
              <span className={`h-2 w-2 rounded-full ${s.dot}`} />
              <span className="text-[10px] font-bold text-zinc-600">{s.letter}</span>
              <span className="font-mono text-[10px] text-zinc-400">{path}</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {solo.map(([name, value]) => (
                <FieldBadge
                  key={name}
                  name={name}
                  values={{ [path]: value }}
                  legend={legend}
                />
              ))}
            </div>
          </section>
        );
      })}

      {/* multi-source fields */}
      {shared.length > 0 && (
        <section data-testid="object-shared">
          <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
            Shared
          </div>
          <div className="flex flex-wrap gap-1">
            {shared.map(({ name, values }) => (
              <FieldBadge key={name} name={name} values={values} legend={legend} />
            ))}
          </div>
        </section>
      )}
    </article>
  );
}

/** One key-value badge. Single-source → value + source tint. Multi-source →
 * value(s) with colored letter chips (grouped by value: agree shows once,
 * disagree shows each). Hover pops the full ungrouped source→value table. */
function FieldBadge({
  name,
  values,
  legend,
}: {
  name: string;
  values: Record<string, unknown>;
  legend: Legend;
}) {
  const entries = Object.entries(values);
  const single = entries.length === 1;
  const groups = groupByValue(values);
  const agreed = groups.length === 1;

  return (
    <HoverCard popup={<ExpansionTable name={name} values={values} legend={legend} />}>
      {/* Badge background is always gray — the source's color lives in the
          section header + the letter chips, so cards don't get overwhelming. */}
      <span
        data-testid={`field-badge-${name}`}
        className="inline-flex cursor-default items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-700"
      >
        <span className="opacity-60">{name}</span>
        {single ? (
          <span className="font-mono font-medium">{renderValue(entries[0][1])}</span>
        ) : agreed ? (
          <span data-testid={`field-agree-${name}`} className="inline-flex items-center gap-1">
            <span className="font-mono font-medium">{renderValue(groups[0].value)}</span>
            <Letters sources={groups[0].sources} legend={legend} />
          </span>
        ) : (
          <span data-testid={`field-disagree-${name}`} className="inline-flex items-center gap-1">
            {groups.map((g, i) => (
              <span key={i} className="inline-flex items-center gap-0.5">
                {i > 0 && <span className="text-zinc-300">·</span>}
                <span className="font-mono font-medium">{renderValue(g.value)}</span>
                <Letters sources={g.sources} legend={legend} />
              </span>
            ))}
          </span>
        )}
      </span>
    </HoverCard>
  );
}

function Letters({ sources, legend }: { sources: string[]; legend: Legend }) {
  return (
    <>
      {sources.map((p) => {
        const s = legend.get(p);
        if (!s) return null;
        return (
          <span key={p} className={`rounded px-1 text-[10px] font-bold ${s.chip}`}>
            {s.letter}
          </span>
        );
      })}
    </>
  );
}

/** The hover popup: every source → its value for this field, one row each
 * (deliberately NOT grouped, so you see all sources even when some agree). */
function ExpansionTable({
  name,
  values,
  legend,
}: {
  name: string;
  values: Record<string, unknown>;
  legend: Legend;
}) {
  return (
    <div data-testid={`field-popup-${name}`}>
      <div className="mb-1 font-semibold text-zinc-700">{name}</div>
      <table className="text-[11px]">
        <tbody>
          {Object.entries(values).map(([path, value]) => {
            const s = legend.get(path);
            return (
              <tr key={path}>
                <td className="pr-1.5 align-top">
                  {s && (
                    <span className={`rounded px-1 text-[10px] font-bold ${s.chip}`}>
                      {s.letter}
                    </span>
                  )}
                </td>
                <td className="pr-2 align-top font-mono text-zinc-400">{path}</td>
                <td className="align-top font-mono text-zinc-800">{renderValue(value)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Lightweight CSS-only hover popover (named group so nested cards don't
 * cross-trigger). The popup stays in the DOM (toggled via `hidden`), so it's
 * queryable in tests. */
function HoverCard({
  popup,
  children,
}: {
  popup: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <span className="group/hc relative inline-block">
      {children}
      <span className="pointer-events-none absolute left-0 top-full z-20 mt-1 hidden w-max max-w-md rounded-lg border border-zinc-200 bg-white p-2 shadow-lg group-hover/hc:block">
        {popup}
      </span>
    </span>
  );
}

/** Group a field's sources by value equality (stable JSON), preserving first
 * appearance. One group → all agree; many → disagreement. */
function groupByValue(
  values: Record<string, unknown>,
): { value: unknown; sources: string[] }[] {
  const groups = new Map<string, { value: unknown; sources: string[] }>();
  for (const [path, v] of Object.entries(values)) {
    const key = stringify(v);
    const g = groups.get(key) ?? { value: v, sources: [] };
    g.sources.push(path);
    groups.set(key, g);
  }
  return [...groups.values()];
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return stringify(value);
}

function stringify(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
