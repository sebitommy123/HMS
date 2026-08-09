HMS — Development Plan

## The Three Products

HMS is delivered as three products on one shared semantic foundation:

- **DataPro** — the semantic data layer (Phase 1). See `datapro.md` and `datapro_development_plan.md`.
- **DataMCP** — the AI agent surface (Phase 2). Exposes DataPro through the Model Context Protocol.
- **DataViz** — the 2D spatial dashboard for humans (Phase 3). See `specifications.md`.

This document covers the high-level shipping sequence across all three. Each product has (or will have) its own detailed implementation plan.

## Philosophy

Building the whole thing at once is not realistic. The project needs to be sliced into individually value-adding components that each stand on their own while also composing into the full platform. The goal is to keep a tight feedback loop with reality — ship something real, useful, and testable as early as possible, then build outward from there.

## The Two Options

When deciding where to start, there were two natural sequences:

### Option A — UI-first

Build the dashboard first: the 2D spatial canvas, the pre-made components, the AI-driven configuration layer. You could speak a beautiful dashboard into existence, pan around, customize it, and see your system.

The tradeoff: without the data processing layer, the UI would have to deal with raw data directly. Huge datasets would make it laggy, graphs would be slow to load, and interactions would feel sluggish. The UI would look great but feel bad.

### Option B — Data-processing-first

Build the injector layer first: standardization, filtering, aggregation, modules, and links. The output is a polished, normalized, cached, sampled data access layer — fast, consistent, and uniform across data sources.

The tradeoff: no fancy UI initially. Anyone wanting to visualize data would need to build their own frontend (HTML/JS, CLI, whatever) against the normalized API. But the API would be clean and easy to work with, and whatever they build would be fast and responsive because the data layer has already done the hard work of reducing data to a consumable size.

## Decision: Option B (Data-processing-first)

**We are going with the data-processing side first.**

Reasons:
- The data layer is the foundation. A great UI on top of a bad data layer is still a bad product. A usable API on top of a great data layer is immediately useful to anyone, even without a polished UI.
- The API becomes a clean integration point. Other tools — custom dashboards, CLIs, scripts, notebooks — can all hit the same normalized API. This multiplies the value of the work before we've even touched the visual layer.
- Performance and correctness are set from the start. If we get the injector/module layer right, everything built on top of it inherits those properties.
- It avoids the trap of building a beautiful but slow UI that has to be rebuilt once the data layer lands.

## What "Done" Looks Like for Phase 1

Phase 1 is **DataPro** (see `datapro.md`). Architecturally, Trino is the only core infrastructure piece — it powers the object abstraction. Time series uses direct, backend-specific catalogs that bypass SQL.

DataPro itself has two components:

- **DataPro Core** — the headless deterministic runtime that executes queries against Trino and time-series backends.
- **DataPro Dashboard** — the management UI for inspecting queries, debugging failures, managing catalogs, and launching/reviewing AI agents.

Note: DataPro Dashboard is the operator UI for setting up and debugging DataPro itself. It is **not** DataViz — DataViz is the consumer-facing 2D spatial dashboard delivered in Phase 3 (see `specifications.md`).

### Phase 1a: DataPro Core

The first milestone is a working normalized data access layer:
- Trino set up as the federation/execution engine for objects
- A catalog configuration for at least one data source (e.g. Postgres), AI-generated, declaring object types, traits, field/property mappings, and proposed links
- A direct time-series catalog for at least one TSDB backend (e.g. VictoriaMetrics via PromQL), exposing the time-series abstraction
- The same time-series backend also exposed as objects via Trino, demonstrating dual exposure
- A deterministic query engine in Core that accepts DataPro queries, plans them, emits SQL, and assembles results
- The link store with at least basic derived links across two data sources, traversable via the API
- Full test coverage per `testing.md` — unit tests, scale tests, correctness tests, and automated AI tests

This alone is useful: anyone can build on top of Core's API.

### Phase 1b: DataPro Dashboard

DataPro Dashboard makes Core operationally viable. Without it, configuring catalogs means editing config by hand — painful, and incompatible with the AI-assisted onboarding story. Dashboard delivers:

- Query inspection (what's coming in, what's failing, why)
- Catalog management (list, configure, enable/disable)
- AI agent orchestration (kick off the LangGraph catalog-building agent against a new data source)
- AI proposal review (sample objects, identifier strategies, traits, links — humans approve/reject before the configuration enters Core)

Phase 1a and 1b are tightly coupled — Core is the engine, Dashboard is the way humans actually use it — but Core can ship and run before Dashboard is fully built out.

## Phase 2 — DataMCP

Once DataPro is shipping reliable, bounded, provenance-tracked answers, the cleanest next product is **DataMCP** — a Model Context Protocol server that exposes DataPro's query surface to AI agents.

DataMCP delivers:
- An MCP server endpoint that AI agents can connect to
- Tool definitions that map to DataPro's query primitives (object lookup, link traversal, time-series fetch, schema introspection)
- Bounded behavior — every agent call goes through the same safety contracts as direct DataPro queries
- Discovery — agents can ask "what object types exist?" and "what links exist?" and get back a usable system map

Why DataMCP before DataViz: AI agents are an immediate, validated demand for exactly the shape of API DataPro provides. DataMCP is small, mostly an interface adapter, and gives HMS its cleanest early demo — an AI copilot debugging an incident through a single API call instead of stitching six tools together.

## Phase 3 — DataViz

After DataPro and DataMCP are solid, **DataViz** is the consumer-facing 2D spatial dashboard for humans (see `specifications.md`):
- The 2D pan/zoom canvas, pre-made components, AI-driven layout
- Expansion features: alerts, conditional rendering, collaboration, temporal exploration (see `specifications.md` §3.2)

DataViz is intentionally last because it's the product with the most surface area, the most visual polish required, and the one that benefits most from a battle-tested DataPro underneath.

## Why This Slicing Works

Each phase produces something valuable on its own:
- **DataPro alone** is useful: a normalized data access layer with AI-assisted setup is a real product, even without DataMCP or DataViz. People can query it from scripts, CLIs, or their own frontends.
- **DataPro + DataMCP** is a clean wedge into the AI-agent ecosystem — easier to demo, immediately useful, aligned with where the AI ecosystem is going.
- **DataPro + DataMCP + DataViz** is the full vision: the consumer-facing dashboard finally has the fast, clean backend it needs to feel responsive and great, and humans and AI agents share the same source of truth.

At no point are we building something that's useless until the next piece lands.
