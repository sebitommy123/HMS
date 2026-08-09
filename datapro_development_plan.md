DataPro — Development Plan

This document is the implementation-level plan for building DataPro. It assumes you've read `datapro.md` (architecture and design) and `development_plan.md` (Phase 1 vs Phase 2 framing for the broader HMS vision).

The single most important thing: **Core first, but not "all of Core" first.** Build a thin vertical slice that proves the idea end-to-end, then expand. The temptation will be to build out the full query language, the full AI harness, the full Dashboard, etc. Resist all of it. Get one query end-to-end through real catalogs before doing anything sophisticated.

---

## Guiding Principles

- **Build the planner before the intelligence.** DataPro's differentiation is semantic planning, object assembly, and link-based joins. Not UI, not "AI magic," not exotic catalogs. The first thing to get right is the deterministic execution model. AI gets bolted on once that works.
- **Hardcode aggressively before generalizing.** Phase 1 has hardcoded catalogs, hardcoded links, hardcoded query shapes. That's correct. Generalization comes after the bones work.
- **Each phase delivers something testable.** Don't roll multiple phases into a single "big bang" PR. The whole architecture only works because each layer can be validated on its own.
- **Avoid the obvious traps.** Listed at the end. Re-read them before starting any phase that feels like it's getting too ambitious.

---

## Implementation Discipline (Cross-Cutting Rules)

These rules apply across all phases. They exist because some pieces of the architecture, even if "later" in the spec, are easier and safer to design in from the start than to bolt on later.

### Trait Set is Minimal in Early Phases

The full trait set in `datapro.md` (Identified, Temporal, Linked, Measurable, Textual, Versioned, Owned, Located) is the design target — not the day-one implementation.

Implement only what real queries need:

- **Phases 1–4**: `Identified`, `Linked`, optionally `Temporal` if a chosen catalog benefits.
- **Phase 5+**: add traits as concrete data sources require them.
- Do not implement `Measurable`, `Textual`, `Versioned`, etc. speculatively.

The trait *system* (declaration, lookup, traversal) should be in place from Phase 1 — but with only two or three concrete trait types implemented.

### Query Language Input is Canonical JSON

Avoid GraphQL, Cypher, or any other surface syntax in the first slice. Core needs to reason about every query before execution; that's much easier when queries are already structured documents.

- Inputs to Core are canonical JSON queries. Phase 2's example shape is the starting point.
- Human-friendly syntax (text, GraphQL-style, etc.) compiles to canonical JSON, in a separate layer, much later.
- This decision keeps the planner simple and keeps the query surface validatable from day one.

### Partial Data Metadata is Designed in From Phase 2

Field-level state is **not** a Phase 9 concern. The response format must distinguish "this field is null" from "we couldn't fetch this field" from day one. It affects:

- Response shape (every property carries state, not just a value)
- Dashboard debugging (can't surface failures without it)
- User trust (silent partial responses are dangerous, see `datapro.md` Hard Problems §2)

Phase 2's first end-to-end query should already return field-level state metadata, even if the values are trivial in the happy path.

### Identity Resolution Lives in the Object Model From Phase 1

Even when identifiers are hardcoded, Core's internal object model should already distinguish:

- **Native source ID** — what the catalog pulled from its backend
- **Canonical object ID** — DataPro's normalized identifier for this object
- **Mapping rule** — how the canonical ID was derived (even if it's `identity` in Phase 1)
- **Confidence/source** — explicit, inferred, fuzzy (Phase 1 is always `explicit`)

Bolting this on later is painful. Carving the shape now costs almost nothing and lets identity resolution evolve naturally as we hit real data.

### Adversarial Harness Starts Handwritten

Phase 6 does **not** start with "AI generates pathological queries." That's a later evolution.

- Start with a hand-curated list of ~10 known bad query templates (unbounded scan, huge time range, deep fan-out, non-indexed filter, etc.)
- The validation harness runs every catalog config against these templates and checks the planner refuses them
- Once the deterministic harness is solid, an agent can be added that *expands* the template set by generating new pathological queries

Determinism first; AI as an extension on top of it.

---

## Phase 0 — Define the Minimum System That Works

Before writing any code, pick **one concrete story** and optimize everything around it. The first vertical slice exists to prove the architecture, not to be feature-complete.

The chosen story:

> Given an `AppInstance` ID, return:
> - `status` (from Kubernetes)
> - `owner` (from a Postgres DB)
> - `cpu` (from a time-series backend, optional at first)
> - `host` it runs on (linked from another catalog)

This is enough to exercise every part of the architecture that matters:

- Multi-source object assembly (Kubernetes + DB)
- Link traversal (`AppInstance → Host`)
- Trino integration (real federation)
- Time-series integration (when added in Phase 8)

If we can demo this end-to-end against real data, the architecture is validated. Everything after Phase 0 is expanding the surface area, not proving the idea.

---

## Phase 1 — Skeleton Core (no AI, no Dashboard)

Build a deterministic Core with everything hardcoded. No AI, no config generation, no Dashboard, no fancy query language. The goal is to prove the execution model.

### Components to Build

1. **Query parser** — accepts a very simple canonical JSON query format (see Phase 2 for the shape). No surface syntax yet.
2. **Object registry** — in-memory list of object types, their fields, and their declared traits (only `Identified` and `Linked` to start, optionally `Temporal`)
3. **Catalog registry** — catalogs are manually defined and registered in code
4. **Link store** — hardcoded link definitions in code
5. **Query planner** — basic: figure out which catalogs to hit, which fields each one provides, what to join on
6. **Trino executor** — issues SQL to Trino, gets results back
7. **Object assembler** — merges results from multiple catalogs into a single object, with full field-level state metadata (not just values)
8. **Identity model** — even with hardcoded IDs, the internal object representation already separates native source ID, canonical object ID, mapping rule, and confidence (see Implementation Discipline)

### Trait Scope (Phase 1)

The trait *system* is in place — types declare which traits they have, the planner can branch on traits — but only `Identified` and `Linked` are actually implemented. `Temporal` if a chosen catalog benefits. Everything else stays in the spec.

### What "Hardcoded" Means

Approximately:

```ts
const catalogs = [postgresCatalog, kubernetesCatalog];
const links = [
  { from: "Application.host_id", to: "Host.id" },
];
```

No config files. No AI. No dynamic registration. The whole thing lives in code that you can step through with a debugger.

### Goal

Prove the execution model works: a DataPro query in, multi-source SQL out via Trino, assembled object back. That's it.

---

## Phase 2 — Make One Real Query Work

Take Phase 0's story and make it actually run.

### Example query

```json
{
  "from": "AppInstance",
  "where": { "id": "livestreamer_412" },
  "select": ["status", "owner"],
  "include": {
    "host": {
      "select": ["id", "region"]
    }
  }
}
```

### What Core has to do

1. Find which catalogs advertise `AppInstance`
2. Determine which selected fields come from which catalogs
3. Use the link store to plan the join from `AppInstance → Host`
4. Emit SQL to Trino (one query joining the relevant tables across catalogs)
5. Merge the rows back into a single object with a nested `host` sub-object **and field-level state metadata**

### Response Shape (with Field State)

Every property in the response carries state, not just a value. Even a fully-successful query returns the metadata structure — it's part of the contract from day one.

Successful field:

```json
{
  "status": { "state": "ok", "value": "Running", "source": "k8s-prod" }
}
```

Field whose source is unavailable:

```json
{
  "owner": { "state": "unavailable", "reason": "catalog missing", "source": "postgres-meta" }
}
```

Designing this in from Phase 2 means partial-data semantics never have to be retrofitted (see Implementation Discipline > Partial Data Metadata and `datapro.md` Hard Problems §2).

### Validation

If this runs successfully against 2–3 real catalogs (e.g. Postgres + Kubernetes via Trino's k8s-style data source, even if it's a stub), the architecture is validated. We can now expand outward.

---

## Phase 2.5 — Debug Console (small, recommended early)

Once Phase 2 works, build a tiny developer-facing debug console *before* moving on. It is much cheaper than the full Dashboard and pays back constantly during the next phases.

### What It Is

A CLI command, a single web page, or both — that takes a DataPro query and shows the entire pipeline:

1. The parsed query (canonical JSON, normalized)
2. The query plan (which catalogs, which links, which traits, what the join looks like)
3. The generated SQL going to Trino (and any native time-series queries later)
4. Row-by-row results returned by Trino
5. The final assembled object with field-level state

### Why Now

Every later phase debugs problems by inspecting one of these layers. Without a console, debugging means inserting `print` statements all over Core. With it, debugging is "run query → look at the plan → look at the SQL → done."

### What It Is Not

Not the full Dashboard. No catalog management UI, no AI agent orchestration, no failure dashboards. Just a window into Core's internals.

The full Dashboard (Phase 7) builds on the same primitives but adds the operational surface (managing catalogs, reviewing AI proposals, etc.).

---

## Phase 3 — Link Store Becomes Real

Phase 1 had hardcoded links in code. Phase 3 turns links into structured, persisted definitions.

### What Changes

Move from this:

```ts
{ from: "Application.host_id", to: "Host.id" }
```

To this:

```yaml
- source_type:     Application
  source_field:    host_id
  target_type:     Host
  target_id_field: id
  relationship:    RUNS_ON
  cardinality:     many_to_one
  confidence:      explicit
```

### Add

- **Named relationships** (`RUNS_ON`, `OWNED_BY`, etc.) — required, not optional. The query language must traverse links by name (see `datapro.md` Hard Problems §4).
- **Cardinality** — `one_to_one`, `many_to_one`, `many_to_many`, etc. The planner uses this for fan-out estimation.
- **Validation** — refuse to register a link whose source/target types don't exist, or whose fields don't appear on those types.

### Storage

A simple JSON/YAML file or a tiny in-memory store is fine for now. Real persistence is a Phase 4+ concern.

### Goal

This is where the system stops being "SQL-ish" and starts being **semantic**. Joins are link traversals, not column matches.

---

## Phase 4 — Catalog Configuration Layer

Phase 1 had catalog logic baked into code. Phase 4 moves that into declarative configuration.

### Replace Code with Config

A catalog's behavior — what object types it advertises, how its columns map to object properties and trait fields — becomes a config document:

```json
{
  "catalog_id": "k8s-prod",
  "advertises": [
    {
      "object_type": "AppInstance",
      "traits": ["Identified", "Linked"],
      "id":     ["namespace", "name"],
      "fields": {
        "status":  "k8s.pods.status",
        "host_id": "k8s.pods.node_name"
      },
      "links": [
        {
          "source_field":    "host_id",
          "target_type":     "Host",
          "target_id_field": "id",
          "relationship":    "RUNS_ON",
          "cardinality":     "many_to_one"
        }
      ]
    }
  ]
}
```

### What Core Does Differently

The query planner now reads catalog configs and the link store at startup (or hot-reloads them). The same Core code that worked in Phases 1–3 keeps working — but now its behavior is data-driven.

### Why This Matters

This is the moment AI can plug in later. If a catalog is just a config document, then "AI generates a catalog" means "AI generates a config document" — a bounded, validateable artifact, not arbitrary code.

---

## Phase 5 — Introduce AI (Carefully)

Only now bring in AI. Start with the easiest task: **config generation**, not Trino connector code.

### First AI Task: Config Generation

The agent's job is narrow:

```
Input:  Postgres schema (table list, columns, types, basic stats)
Output: catalog configuration
        - object types this catalog advertises
        - identifier strategy per type (single column, composite, transform)
        - property mappings (column → property)
        - traits per type
        - proposed links
```

### What's NOT in Scope Yet

- AI does **not** write Trino connectors. We use existing Trino connectors only.
- AI does **not** write SQL. The planner does that, deterministically.
- AI does **not** make runtime decisions. Setup-time only.
- AI does **not** produce a safety contract yet (Phase 6).
- AI does **not** design or extend the query language. The query language stays canonical JSON throughout this phase (see Implementation Discipline > Query Language Input).

### Validation

Every AI-produced config must be validatable:

- Sample queries against the proposed config must succeed
- Sample resolved objects must be inspectable by a human
- Identifier mappings must be tested against real data

If the agent produces something Core can't use, the agent's wrong, not Core.

### Tooling

Use **LangGraph** for the agent (multi-step graph with validation gates). LlamaIndex is optional and only enters when context starts ballooning past what fits in the prompt.

---

## Phase 6 — Validation Harness

Before AI writes anything "real" or before any AI-produced config is registered into Core, build the validation tooling. This is what makes AI usable.

### Components

- **Config validator** — schema-checks a proposed catalog config: types referenced exist, link targets exist, identifier fields exist on the relevant tables, etc.
- **Sample query runner** — given a config, runs a battery of normal queries and checks they produce sensible objects
- **Diff tool** — given expected vs actual resolved objects, highlights mismatches (missing fields, wrong identifiers, incorrect link traversals)
- **Broadness/safety checker** — given a config, runs a hand-curated list of ~10 pathological query templates and checks that the planner refuses them.
- **Identity confidence + conflict detection** — surface inferred (vs explicit) identity mappings and flag conflicts when two catalogs disagree on a property's value. See `datapro.md` Hard Problems §1. Phase 4 introduced the canonical ID + mapping rules; Phase 6 is where the validation around them gets teeth.

### Start Handwritten, Not AI-Generated

The pathological query templates start as a **hand-written, deterministic list**. Examples:

- `select all` of any object type with no filters
- huge time range (e.g. one year on a sample-level type)
- multi-hop traversal without a parent limit
- filter on a non-indexed property only
- sort a huge dataset by timestamp without a time bound

Run every catalog config against these. If any get past the planner, the safety contract is wrong. This deterministic version is what we ship in Phase 6.

The adversarial agent that *generates* new pathological queries on top of this template list comes later, as an extension of the harness — not as the starting point. (See `datapro.md` AI Harness > Adversarial Performance Certification for the full vision.)

### Why Build This Now

This harness is what AI's outputs feed *into* before they reach Core. Without it, AI is producing config that nobody can verify. With it, AI is producing config that's automatically gated by deterministic checks.

---

## Phase 7 — Minimal Dashboard

Now add UI — but only for the operational/control plane. No fancy 2D canvas, no consumer-facing visualization. That's all Phase 2 of the broader plan.

### Scope

- **View incoming queries** — what's hitting Core, what's failing, latencies
- **See failures with context** — which catalog failed, why, suggested fix
- **Inspect catalogs** — list registered catalogs, view their configs and safety contracts, enable/disable
- **Review AI proposals** — when an agent run completes, surface the proposed config + sample objects + identifier strategy + link proposals for human approval

### What Dashboard is NOT

- Not DataViz (the consumer-facing 2D spatial dashboard)
- Not a query authoring tool for end users (that comes later, on top of Core's query API)
- Not a graph/diagram view of the link store (nice-to-have, way later)

### Why It Matters Here

Without Dashboard, configuring DataPro means editing config files by hand. That's painful, and incompatible with the "AI proposes, humans review" loop. Dashboard is what makes Phase 5–6 actually usable.

---

## Phase 8 — Time-Series Integration

Bring in the time-series side of the architecture (`datapro.md` Architecture > Time Series).

### Add

- **A direct VictoriaMetrics (or similar TSDB) catalog** — speaks PromQL straight to the backend, bypassing Trino.
- **A time-series query path in Core** — separate from the object query path. Routes time-series queries to the relevant TSDB catalog.
- **(Optional) The same backend exposed as `MetricSample` objects** — Trino-side, so individual samples are queryable as Objects with `Temporal + Measurable` traits.

### Why This is Phase 8 and Not Earlier

The object path is the hard part. Time series is structurally simpler — it's mostly "speak PromQL to a backend and return points." Once the object path is solid, adding time series is a more contained piece of work that doesn't risk derailing the architecture.

---

## Phase 8.5 — DataMCP (early wedge candidate)

Once Core's query API is stable and there are real catalogs registered, expose DataPro to AI agents through MCP (Model Context Protocol).

### Why This Phase Exists

AI agents are a first-class consumer of DataPro (see `datapro.md` "Consumers and Use Cases > AI Agents"). They consume DataPro by issuing structured queries and traversing object graphs — exactly what Core already does, just via a different transport. **DataMCP** itself is small; what it unlocks is large.

This phase is also a strong **demo wedge**. An AI debugging an incident through DataPro is a much more compelling early demo than a custom dashboard, and it lands cleanly on top of everything Phases 1–8 produced. Consider promoting this earlier in the schedule if early external demos are valuable.

### Components

- **MCP server** that exposes DataPro Core's query API as MCP tools
- **Tool definitions** for the core operations: query objects, traverse links, list catalog/trait/link metadata, retrieve field-level state
- **Result shaping** so object graphs returned to the agent are easy for an LLM to reason over (consistent envelope, field-level state included, link traversals nested rather than flat)
- **Documentation surface** for what types, traits, and links exist — agents need to discover the schema, not memorize it

### Out of Scope (Initially)

- Streaming/subscribe semantics (agents poll for now)
- Write operations (DataPro is read-only as far as MCP is concerned)
- Authn/authz beyond whatever Core already enforces

### Why This Isn't Phase 4 or 5

MCP only pays off if the underlying query API is reliable. Without identity resolution, safety contracts, field-level state, and link traversal, the agent gets unreliable answers — which is worse than no integration. Phase 8.5 puts MCP after the foundations exist.

---

## Phase 9 — Expand Scope

With everything above working, the rest is incremental:

- Add more catalogs (more Trino connectors, more TSDB backends, more bespoke sources via AI)
- Add more object types (more configs, more traits, more links)
- Improve the query language (richer projections, multi-hop traversal, aggregation primitives)
- Refine link inference (better AI proposals, fuzzy matching, identity resolution improvements per `datapro.md` Hard Problems §1)
- Optimize planning (batching, query fusion, caching, prefetch)

This is the long tail. The hard architectural decisions are all behind us by this point.

---

## What NOT to Do Early

Hard rules during Phases 0–4:

- ❌ Don't design the full query language first. A minimal canonical JSON shape is enough until Phase 9-ish.
- ❌ Don't build the AI system first. AI doesn't go in until Phase 5, after the deterministic core works.
- ❌ Don't pick GraphQL, Cypher, or any other surface syntax as Core's input. JSON-first; surface syntaxes compile to JSON in a separate (much later) layer.
- ❌ Don't implement the full trait set. Only `Identified`, `Linked`, optionally `Temporal` in Phases 1–4. Add others as real queries demand them.
- ❌ Don't build a full Dashboard UI. Phase 7's Dashboard is operational only — no fancy visualizations, no graph views.
- ❌ Don't skip the debug console (Phase 2.5). It pays back constantly during the next phases.
- ❌ Don't try to support "all data sources." One Postgres + one Kubernetes (or equivalents) is the entire Phase 0–4 universe.
- ❌ Don't try to solve identity resolution perfectly — but **do** carve the internal model (native ID, canonical ID, mapping rule, confidence) from Phase 1. The shape is cheap to design in and painful to bolt on later.
- ❌ Don't start the adversarial harness with AI-generated pathological queries. Begin with ~10 hand-written templates. AI extends the template set later.
- ❌ Don't return raw values from queries — return field-level state metadata from Phase 2 onward.

---

## Key Development Principle

> **Build the planner before the intelligence.**

The differentiation of DataPro is:

- Semantic planning (which catalogs to hit, which links to traverse, what to push down)
- Object assembly across multiple sources
- Link-based joins enforced through the link store

The differentiation is *not*:

- A pretty UI
- AI magic
- A long list of supported catalogs

If we get the planner right, the AI layer can be modest and still ship a great product. If we get the planner wrong, no amount of AI cleverness will save it.

---

## Suggested Tech Stack

Practical defaults — change if there's a strong reason:

- **Core**: TypeScript or Go. Both give us strong control over types, performance, and explicit error handling. Pick whichever the team is faster in. (Avoid Python for Core itself — fine for the agent harness.)
- **Trino**: run locally during development. Either via Docker or a managed instance. Don't try to set up production Trino early.
- **Config storage**: JSON or YAML files on disk for Phases 1–4. Move to a small database (SQLite or Postgres) when configs need to be edited live via Dashboard.
- **Link store**: in-memory at first, backed by a simple persistent store (SQLite or a JSON file) when Phase 3 lands.
- **AI agent harness**: LangGraph (Python). Plays nicely with LlamaIndex if/when retrieval is needed.
- **Dashboard frontend**: React + TypeScript, kept deliberately simple. No 3D, no canvas, no fancy animations until much later.

---

## First Milestone (Very Concrete)

If we can demo this:

```
Input:
  AppInstance:livestreamer_412

Output:
  {
    status: "Running",
    owner:  "video-team",
    host: {
      id:     "h1",
      region: "us-east"
    }
  }
```

…assembled from **two or more real sources**, then we've proven the core idea. Everything past that is scaling, polish, and AI integration.

That's the bar for "Phase 0–2 complete."

---

## Where to Go Next

The plan is now coherent enough to start implementing. The single most valuable next artifact is **the internal Core data model**:

- `Object` — type, canonical ID, traits, properties (with field-level state)
- `Trait` — declared capability, with the field references that back it
- `CatalogConfig` — what a catalog advertises, including its query safety contract
- `LinkDefinition` — the entries in the link store (derived and explicit)
- `Query` — the canonical JSON shape, parsed
- `QueryPlan` — the planner's output before SQL generation: catalogs involved, links traversed, fields per source, projected shape
- `FieldState` — per-property metadata: state, value, source catalog, confidence, reason if missing

Designing these data structures up front pays off for every phase that follows. The planner, the assembler, the validator, the debug console, and Dashboard all consume these types — getting them right early keeps Core clean instead of accumulating ad-hoc shapes.

After the data model, the next natural artifact is **the query planner algorithm**: given a parsed `Query`, the link store, and the catalog configs, how does the planner decide what to hit, in what order, and how to join? This is the heart of Core and deserves careful design once the data shapes are settled.
