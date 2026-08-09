DataPro — The Data Processing Layer

> **Where DataPro fits in HMS.** HMS is three products on one shared semantic foundation: **DataPro** (the data layer, this document), **DataMCP** (the AI agent surface, exposing DataPro over the Model Context Protocol — Phase 2), and **DataViz** (the 2D spatial dashboard for humans — Phase 3, see `specifications.md`). DataPro is the foundation; DataMCP and DataViz are interface products that consume DataPro's API. Throughout this document, "HMS" refers to the platform as a whole; "DataPro," "DataMCP," and "DataViz" refer to specific products.

> **Consolidated architecture (v2):** For the current object-source model (**native / stock / flex / bespoke**), the flex Trino plugin, query surface, and Core / AI / UI split, use **`datapro_revised_v2.md`** as the working spec. This file is broader narrative and historical context; details may lag v2 where they conflict.

## What Is It?

DataPro is the first deliverable of HMS (see `development_plan.md`). It is the data processing layer — the backend that sits between raw, scattered data sources and whatever wants to consume that data (DataMCP, DataViz, custom tools, scripts, notebooks).

DataPro itself has two components: a headless **DataPro Core** (the deterministic runtime that executes queries) and a **DataPro Dashboard** (a management UI for setting up catalogs, debugging failures, and reviewing AI proposals). DataPro Dashboard is *for operating DataPro* — it is **not** DataViz, which is the consumer-facing visualization product delivered in Phase 3.

## The Problem It Solves

Organizations have data scattered across many different kinds of systems:

- SQL and NoSQL databases (Postgres, MySQL, MongoDB, etc.)
- Time-series databases (VictoriaMetrics, Prometheus, InfluxDB)
- Structured files (JSON, XML, YAML)
- Unstructured files (log files, emails, incident reports, Slack conversations, source code)
- APIs (Kubernetes, AWS, GitHub, Confluence, internal services)

All of this data is related. A running instance in Kubernetes is the same "thing" as a configuration entry in a repo, which is the same "thing" as a row in an inventory spreadsheet. But pulling it together is painful — each source has its own query language, schema, performance characteristics, and quirks.

DataPro exposes all of this data over a **normalized API**. You query through DataPro using a uniform language, and DataPro figures out where the data actually lives, how to fetch it efficiently, and how to represent it consistently.

## The Mission

Most data that lives in an organization can be conceptualized as one of a small number of abstractions. If we get those abstractions right, we can make the normalized API feel natural — queries "just work."

The hope: downstream consumers (dashboards, CLIs, scripts, notebooks, other services) can hit this API and get clean, fast, normalized data without having to know or care about where it actually came from.

## Abstractions

DataPro is built around two abstractions: **Objects** and **Time Series**. Most of the conceptual weight is on Objects, augmented by optional **traits**. Time Series is a complementary, narrowly-scoped abstraction for aggregated plottable data.

### Objects

An **object** is a typed, identified semantic record. Every object has:
- A **type** (e.g. `Application`, `Host`, `Deployment`)
- A set of **properties** (key-value pairs)

The combination of type and identifier forms the **namespace** — i.e. how you uniquely refer to an object globally.

**Objects can be composed across data sources.** A single `Application` object might have:
- Its `status` (running / down) from a Postgres table
- Its `configuration` from an XML file in a repo
- Its `last_modified_by` and `last_modified_at` from Git metadata
- Its `deployment_target` from the Kubernetes API

From the consumer's perspective, this is one object. You ask for the application, you get all its properties — the fact that four different backends were queried to assemble it is mostly transparent. Consumers can be opinionated about which sources they want included, but by default they just ask for the object.

**Objects are query-time records, never stored.** They exist only when something asks for them. This is critical because some object types have astronomical cardinality — every bid and ask on every exchange in the world, every metric sample, every log line. We can't materialize these. DataPro figures out which objects you want, which catalogs to query, pulls the data, and assembles the objects on demand.

If you ask for too many objects (e.g. "give me every order this year"), DataPro refuses and asks you to narrow the scope.

### Traits

The bare "type + id + properties" definition is enough for static things like a `Team` or a `Host`. But many objects need richer behavior — a timestamp, relationships, a measurable value, a duration. Rather than introduce a separate primitive for each (Event, Log, Alert, MetricSample, Relationship), DataPro gives objects **optional traits**.

A trait is a declared capability that unlocks specific operations on the object. Each catalog configuration declares which traits each of its object types supports.

The starting set of traits:

- **Identified** — every object has this; declares the canonical ID strategy (see Known Hard Problems §1)
- **Temporal** — has a timestamp or time range; can be filtered, windowed, and ordered by time
- **Linked** — has named relationships to other objects; can be traversed (see "Links: The Link Store")
- **Measurable** — has a numeric value with a unit; can be aggregated, plotted, summed
- **Textual** — has free-form text content; can be searched and summarized
- **Versioned** — has a version, commit, or hash; can be compared across versions
- **Owned** — has an owner (user, team); can be grouped by ownership
- **Located** — has a place (region, datacenter, namespace); can be grouped by location

Traits compose. A `Deployment` is `Identified + Temporal + Linked + Versioned + Owned`. A `MetricSample` is `Identified + Temporal + Measurable + Linked`. A `LogLine` is `Identified + Temporal + Textual + Linked`. A plain `Team` may only be `Identified + Owned`.

#### Examples

A point-in-time event:

```yaml
type: Deployment
id: deploy_9182
traits:
  temporal:
    timestamp: 2026-04-28T14:03:12Z
  linked:
    AFFECTED:    [Application:livestreamer]
    AUTHORED_BY: [User:alice]
    DEPLOYED_TO: [Host:h1]
  versioned:
    commit: abc123
```

An object with duration:

```yaml
type: Incident
id: inc_442
traits:
  temporal:
    start: 2026-04-28T14:00:00Z
    end:   2026-04-28T14:37:00Z
  linked:
    AFFECTED: [Application:livestreamer]
```

A measurement:

```yaml
type: MetricSample
id: metric_h1_cpu_1714312992
traits:
  temporal:
    timestamp: 2026-04-28T14:03:00Z
  measurable:
    value: 0.82
    unit:  cpu_percent
  linked:
    MEASURED: [Host:h1]
```

A static entity:

```yaml
type: Host
id: h1
traits:
  located:
    region: us-east-1
  linked:
    RUNS: [AppInstance:livestreamer_412]
```

#### Why Traits Matter

Traits are how DataPro's query engine reasons generically about what operations are valid for an object:

- A query like "give me all `Deployment` objects within this time range" works because `Deployment` has the **Temporal** trait.
- "Plot this metric over a week" works because `MetricSample` has the **Measurable** trait.
- "Find all `LogLine` objects mentioning `OutOfMemoryError`" works because `LogLine` has the **Textual** trait.
- "Show all incidents owned by the payments team" works because `Incident` has the **Owned** trait.

Without traits, the query language would have to special-case every object type, or expose only the lowest common denominator. With traits, the language stays object-centered and gains extra powers exactly where the data supports them.

> **Objects are the universal semantic unit in DataPro. Additional behavior comes from optional traits.** Timestamped objects are *temporal*. Objects with relationships are *linked*. Objects with numeric values are *measurable*. Traits tell DataPro which operations are valid for which object types.

This means we do **not** need separate top-level primitives for Events, Logs, Alerts, Relationships, or Metric Samples. They are all just Objects with the right trait combinations.

### Time Series

Time Series remains a parallel abstraction, separate from Objects, because the access pattern is fundamentally different. A time series is an **aggregate over many temporal+measurable points**, optimized for plotting and analytic operations at scale.

Any individual metric sample can be exposed as an Object (with `Temporal` and `Measurable` traits) when you want to inspect it or join it with other systems. But asking for "a year of data at daily resolution" should produce 365 aggregated points, not 31 million `MetricSample` objects. That bulk-aggregation use case is what the Time Series abstraction is for, and it bypasses Trino entirely (see "Architecture > Time Series: Direct, backend-specific").

Think of it this way:

- **Time Series** is the optimized lens for "the shape of data over time" at scale.
- **Objects (with traits)** are the universal semantic unit for everything else — including individual time-stamped events, samples, and observations when you care about the specifics.

The same backend often gets exposed both ways (see "Same Backend, Two Faces"): VictoriaMetrics offers a direct PromQL time-series view *and* a Trino-powered object view of `MetricSample`/`MetricSeries`/`Alert`/etc.

### Are These the Right Abstractions?

The Objects + Traits + Time Series model collapses what looked like a longer list of candidates (Events, Logs, Alerts, Relationships, Aggregates) into a single coherent system. Two genuine open questions remain:

- The starting set of traits is a guess. We'll add and refine as we encounter real data sources — possibilities include `Hierarchical` (parent/child trees), `Spatial` (geographic), `Stateful` (state machines), `Costed` (currency-valued), etc.
- Is there a third top-level abstraction that doesn't fit either Objects-with-traits or Time Series? Logs at massive scale might be a candidate, but might also just be `Textual + Temporal` Objects with a specialized backend catalog. Hard to know without more data sources in hand.

## Injectors and Their Role

Each data source gets an **injector** (see `specifications.md` §1) that is responsible for:

1. Exposing the backend's data as some combination of objects and time series (and whatever other abstractions we decide on)
2. Understanding identifiers — how an `Application` in Postgres maps to an `Application` in Kubernetes, so queries across sources line up
3. Translating normalized queries into backend-specific queries via hand-coded modules

Injectors are what make cross-source queries possible. If two injectors both expose objects of type `Application`, and they agree on the identifier scheme, then DataPro can merge their properties into a single object on the fly.

## Core and Dashboard

DataPro has two components: **Core** (the headless runtime) and **Dashboard** (the management UI). They serve fundamentally different purposes and have very different reliability and trust profiles.

|   | DataPro Core | DataPro Dashboard |
|---|---|---|
| **Role** | Data plane | Control plane |
| **What it does** | Executes DataPro queries against Trino + time-series backends | Inspects queries, debugs failures, manages catalogs, launches AI agents |
| **AI involvement** | None — fully deterministic | Yes — agents are launched here, proposals reviewed here |
| **Required to run?** | Yes — always-on runtime | No — Core works headlessly once configured |
| **Trust profile** | Production-critical, must be reliable | Operational tool, humans review everything |

> **DataPro Core is the data plane. DataPro Dashboard is the control plane. AI agents are the setup/repair workers, launched from the Dashboard.**

This is **not** the same thing as **DataViz**, the consumer-facing 2D spatial dashboard described in `specifications.md` (Phase 3 of the broader HMS vision). DataPro Dashboard is a tool for *operating DataPro* — setting up catalogs, debugging failures, reviewing AI proposals. DataViz is for *consuming DataPro's output* — exploring data spatially, building human-machine sympathy.

### DataPro Core

Core is the deterministic runtime. It accepts DataPro queries, plans them against catalog configurations and the link store, emits SQL to Trino (or native queries to time-series backends), and returns objects.

Everything in this document about query planning, deterministic execution, and catalog usage describes Core. **No AI runs in Core.** Core never makes a non-deterministic decision in the hot path.

Core can be run completely headlessly: in a server, behind an API, embedded in another service. It does not require Dashboard to function.

### DataPro Dashboard

Dashboard is the operational/control plane. It is where humans interact with DataPro. It provides:

- **Query inspection** — see what queries are coming in, what they returned, how long they took
- **Failure debugging** — when a query fails, see exactly why (missing catalog, broken catalog, broadness limit hit, ambiguous link, identity resolution failure, etc.)
- **Catalog management** — list registered catalogs, view their configurations, enable/disable, edit broadness limits, view advertised object types and traits
- **AI agent orchestration** — kick off the LangGraph-based catalog-building agents to add new data sources or repair broken ones
- **AI proposal review** — when an agent finishes, review what it proposes (object types, ID mappings, traits per type, links, broadness limits, sample objects) and approve, reject, or edit before the configuration is registered

AI lives in Dashboard, not in Core. **This is the principle that makes the architecture credible**: the runtime is deterministic, and the AI is contained in the management layer where humans can review its work before it ever affects a query.

### The Repair Loop

A canonical flow when a query fails because of a missing catalog:

1. **Query arrives at Core.** "Give me `AppInstance livestreamer_412`."
2. **Core fails.** It needs the XML config catalog to fetch `declared_config`, but no such catalog is registered.
3. **Dashboard surfaces the failure.** It shows:
   - Requested object type: `AppInstance`
   - Missing field: `declared_config`
   - Missing source: XML config repo
   - Suggested fix: create an XML repo catalog
4. **User approves agent run.** They click "create catalog" and authorize the agent.
5. **Agent inspects the source.** Reads the XML repo, infers schema, proposes a configuration.
6. **Agent presents proposals through Dashboard.** Something like:
   > I think this source exposes: `AppInstance`, `DeploymentConfig`.
   > I think `AppInstance`'s ID is composite: `app_name + environment`.
   > I think `AppInstance` has traits: `Identified + Linked + Versioned`.
   > I think this link exists: `DeploymentConfig.app_name → AppInstance.name` (`DESCRIBES`, many-to-one).
   > Here are 5 sample resolved objects. Do these look right?
7. **User sanity-checks the samples.** Approves or corrects each proposal.
8. **Catalog is registered.** Dashboard hands the approved configuration to Core.
9. **Query succeeds.** Core retries the original query with the new catalog available.

This is the product loop. It is what turns DataPro from "a thing that mysteriously fails when something is missing" into "a thing that walks you through fixing it."

### Human-in-the-Loop is First-Class

The sanity-check step is not optional, especially early on. Semantic mapping is **where AI is confidently wrong** — it's easy for an agent to look at a column called `created_at` and confidently propose it as the timestamp for a Temporal trait, when it's actually the row's database-creation time and the *event* timestamp lives in another column.

Catching this requires Dashboard to surface, for every AI proposal:

- The object types the agent thinks the source exposes
- The traits per type and which fields back them
- The identifier strategy proposed (with the formula or transform)
- The links proposed (with cardinality and named relationship)
- Sample resolved objects so the human can spot semantic errors

The Dashboard must make this review fast, fluent, and unavoidable. Auto-approving AI output without review defeats the whole architecture.

### Why This Split Matters

- **AI is not in the hot path.** Every query Core executes was produced by deterministic code consuming human-approved configuration. The bad failure modes of AI (hallucinations, regressions, prompt drift) cannot affect a running query.
- **Core stays stable.** Once configured, Core has no reason to change. Connector code is static. Configuration is static. The query engine is hand-written. This is what makes DataPro credible as an infrastructure component.
- **Dashboard is where the "vibe-coded" energy lives.** AI proposes, humans review, approved configuration becomes static data feeding Core. The dynamic, fuzzy, nondeterministic stuff is contained in setup time.
- **You can run Core without Dashboard.** For mature deployments, Dashboard becomes the place you go when something needs fixing or extending — not something that has to be running for queries to work.

## Vocabulary: Connector vs. Catalog

DataPro uses these two terms with the same strict distinction Trino does. Confusing them is the single easiest way to lose track of which layer something belongs to.

- **Connector** — *the how.* A plugin/implementation that knows how to read a class of backend. The Trino-built-in `postgresql` connector, a custom Trino plugin written for an unusual source, etc. Connectors are code, deployed into the Trino image. One connector typically serves many catalogs.
- **Catalog** — *the what.* A named, configured instance that picks a connector and binds it to a specific backend with specific properties. `prod_orders` (a catalog using the `postgresql` connector pointing at the prod orders DB), `app_logs` (a catalog pointing at `/var/log/apps`), etc. Catalogs are configuration, persisted in Core's metadata store, and managed via Core's HTTP API. Each registered data source in DataPro is a catalog.

The Trino-side mechanics mirror this: a Trino catalog is created with `CREATE CATALOG <name> USING <connector> WITH (...)`. DataPro Core's job is to keep Trino's actual catalogs converged to the desired set persisted in its metadata store.

The DataPro semantic layer that bolts on top (which object types this catalog advertises, trait mappings, proposed links, safety contract) is part of the catalog's configuration in this document — not a separate primitive.

> Throughout this document, "catalog" means a registered DataPro data source. "Connector" means a Trino plugin (built-in or custom). When the docs talk about "AI writes a connector," that refers to the rare case where AI generates a Trino plugin from scratch; the much more common "AI writes a catalog configuration" covers all the routine cases.

## Architecture

Rather than building federation and execution from scratch, DataPro is built on top of **Trino** as its core piece of infrastructure. The architecture splits cleanly along the two abstractions.

**Important distinction:** Trino gives us a normalized SQL access surface — one query language, one execution model, one connector ecosystem across many backends. But Trino does **not** give us semantic normalization. Object identity, property meaning, relationship meaning, and merge rules are all DataPro's job. Trino tells us what rows look like; DataPro tells us what they *mean*.

### Objects: Trino-based

**Trino is the base for the object abstraction.** Trino already handles:
- Query execution
- Federation across many backends (via connectors)
- A normalized SQL access surface

This takes a huge amount of work off our plate. Instead of building a SQL engine, a query planner, and a federation layer, we adopt Trino's and focus our effort on the semantic layer above it — the layer that actually defines what the data means.

The architecture has four layers:

**1. Trino connectors (the access plugins).** Trino ships many already (Postgres, MySQL, Mongo, Iceberg, Kafka, etc.) — each connector is reused across many catalogs. For data sources where no connector exists (e.g. a quirky internal API, an XML file format, a Git repo), **AI writes the missing connector** as a Trino plugin. This is one of AI's primary jobs in DataPro, though the much more common case is the next bullet.

**2. Catalog configuration (DataPro metadata about each catalog).** Every catalog has an associated configuration that tells DataPro how to use it. This configuration is **declarative metadata, not code**, and is provided/proposed by AI. It declares:

- **Which Trino connector this catalog uses, and the connection properties for that backend** — the foundation `CREATE CATALOG <name> USING <connector> WITH (...)` payload.
- **Which object types this catalog advertises** — e.g. "this Postgres catalog exposes `Application` and `Deployment` objects."
- **Which traits each object type has** — e.g. "`Deployment` is `Identified + Temporal + Linked + Versioned`." Traits determine which operations the query engine can offer for that type.
- **How fields map to object properties and trait fields** — which columns become which properties, including the identifier column, the timestamp column for the Temporal trait, the value/unit columns for the Measurable trait, the relationship-bearing columns for the Linked trait, etc.
- **Proposed link entries** — derived links the catalog wants to add to the link store (e.g. "my `Application.host_id` is a Host's `id`"). See "Links: The Link Store" below.
- **Query safety contract** — required constraints (must-have filters), forbidden query patterns, and cost rules (estimated row caps, execution-time caps, fan-out caps) that DataPro's planner uses to reject unsafe queries *before* they reach Trino. See "The AI Harness > Adversarial Performance Certification" for how this is produced.

**3. DataPro's deterministic query engine.** This is hand-written, not AI-generated. It is the heart of DataPro. Given an incoming DataPro query, it:

- Parses the query
- Resolves which object types are involved and which catalogs advertise them (a single object type can be advertised by multiple catalogs — see below)
- Resolves any link traversals against the link store, figuring out what to join on
- Applies broadness limits per catalog and rejects anything that exceeds them
- Generates SQL against Trino using each relevant catalog's configuration
- Executes the SQL via Trino
- Assembles the results into objects

**This deterministic engine is the part of DataPro that does the most actual work.** It is the layer AI does *not* touch. AI creates catalogs and (rarely) writes new Trino connectors; DataPro's engine takes the resulting configuration and turns DataPro queries into SQL.

**4. Object API (what consumers see).** Consumers never touch SQL directly. They issue queries in DataPro's own query language (see "DataPro's Query Language" below) and get back objects.

#### One Object, Many Catalogs

A single object can be backed by multiple catalogs. Catalogs advertise which object types they have information about, and multiple catalogs can advertise the same type — each contributing different fields.

Example: a `running AppInstance livestreamer_412` query may need to join data from:
- The Kubernetes catalog (live status, current node, resource usage)
- An XML config repo catalog (declared configuration)
- A Postgres metadata catalog (owner team, environment)

DataPro figures out which catalogs to hit for a given object request based on the catalog configurations (which advertise their object types and properties) and assembles the final object from all of them. The consumer says "give me AppInstance livestreamer_412" and gets one object — the multi-source merge is invisible.

The merge depends on each catalog agreeing on identifiers, which is itself a hard problem (see "Known Hard Problems > Identity Resolution"). It also requires a story for what happens when one of the source catalogs is missing or broken (see "Partial Data and Missing Catalogs").

### Time Series: Direct, backend-specific

Time series is a fundamentally different shape from objects, and forcing it through SQL would be wasteful. So **time series gets its own abstraction with backend-specific catalogs**, separate from Trino.

A time-series catalog speaks the native query language of whatever backend it's exposing — no SQL translation in the middle, just the right tool for the job. This gives us native performance and direct access to the backend's own aggregation engine.

Backends are not part of the core stack — they're whatever the user already has. Some examples of backends a time-series catalog could target:
- **VictoriaMetrics** (via PromQL)
- **Prometheus** (via PromQL)
- **InfluxDB** (via Flux or InfluxQL)
- **TimescaleDB** (via SQL extensions)

A user with VictoriaMetrics gets a VictoriaMetrics time-series catalog. A user with InfluxDB gets an InfluxDB one. They're all just catalogs plugged into the same time-series abstraction.

### Same Backend, Two Faces

A single backend can — and often should — be exposed through both abstractions. VictoriaMetrics is a good example:

- **As time series**: a direct PromQL catalog. Use this for plotting, aggregating over time, monitoring-style queries.
- **As objects**: a Trino-powered catalog that exposes the underlying entities of the backend. For VictoriaMetrics, the natural object types are things like `MetricSeries`, `MetricSample`, `Alert`, `ScrapeTarget`, and `RecordingRule`. These are generic — they describe what the backend actually is, not what the data represents in a domain.

Domain-specific meaning comes from a layer on top, where mappings turn generic objects into higher-level domain objects. Examples:

- `MetricSample` → `Order` (when the metric represents a bid/ask)
- `MetricSample` → `HostHealthReading` (when the metric represents a host's CPU or memory)
- `MetricSample` → `RequestLatencyObservation` (when the metric represents a request timing)

This separation matters: the generic layer is reusable across all VictoriaMetrics deployments, while the domain layer is specific to what each user's data actually means.

Use the time-series view when you're looking at the shape of data over time. Use the object view when you care about specific points and want to join them with other systems — e.g. inspecting a specific event, pulling up the full context around an anomaly, or joining metric samples with deployment events from another data source. Users get both views of the same underlying data, and pick whichever fits the question they're asking.

### Links: The Link Store

Links are how objects relate to each other across data sources (see `specifications.md` §3.1). They are a clean illustration of the "Trino mechanics, DataPro meaning" split.

- **The traversal mechanics come from Trino.** When you follow a link, the actual data fetching and joining is just SQL — Trino runs a query that pulls fields from one catalog and joins them against fields from another.
- **The meaning of the link comes from DataPro.** Trino has no idea that `apps.host_id` in a Postgres database refers to the same thing as `hosts.id` in a Kubernetes API. It just sees two columns. DataPro is what knows.

That meaning lives in a dedicated **link store**: a DataPro-owned store of relationship semantics between object types.

#### What the Link Store Stores

Critically, the link store does **not** materialize every concrete edge. For high-cardinality or dynamic data (e.g. every running pod's host assignment, every order's exchange), storing concrete edges would be both wasteful and stale — those relationships are best resolved at query time from the underlying data.

Instead, the link store holds two kinds of entries:

**1. Derived links — definitions of how fields map to object types.**

These are the bulk of the link store. Each entry says, in effect: "this field of object type A is an identifier for object type B." DataPro uses these definitions to resolve relationships at query time, going to Trino to fetch the actual joined data.

A derived link entry might look like:

```yaml
source_type:     Application
source_field:    host_id
target_type:     Host
target_id_field: id
relationship:    RUNS_ON
cardinality:     many_to_one
confidence:      explicit
```

This says: "an Application's `host_id` field is a Host's `id`. The relationship is called `RUNS_ON`. Many applications run on one host."

So the link store contains the *meaning*:

> `Application.host_id` means `Application RUNS_ON Host`

…not the concrete edge `Application:billing-api → Host:h1`. The concrete edge is computed at query time from Trino, using the definition.

**2. Explicit links — curated relationships between specific objects.**

For static, curated, or otherwise-not-derivable-from-data relationships, the link store can also hold concrete edges between specific objects. Examples:

- `Application:billing-api DEPENDS_ON Application:auth-service`
- `Service:checkout OWNED_BY Team:payments`
- `Runbook:db-failover APPLIES_TO Application:orders-api`

These are facts that don't live in any backend's data — they're relationships humans (or AI) declared based on knowledge that isn't otherwise queryable.

#### Why This Matters

The link store gives DataPro a clean answer to "where do joins come from?":

> Joins are not just SQL joins. They are semantic links between object types, backed by field mappings, explicit relationships, or both.

This is a strong abstraction. With the link store, DataPro can:

- **Expose link traversal in the object API.** "Give me this app's host" or "give me this host's apps" becomes a first-class operation, not something the consumer has to construct manually.
- **Validate joins.** Trino can technically join any two columns. With the link store, DataPro knows which joins are semantically meaningful and can reject or warn on the rest.
- **Compose objects across sources.** A single `Application` object can have its properties merged from multiple backends because the link store knows how the identifiers across those backends correspond.
- **Drive component composition in the UI.** A table of hosts with a child list of apps per row works because the link store provides the relationship that the UI traverses (see `specifications.md` §2.2 and §3.1).

#### How Links Get In

How entries enter the link store is itself a design problem. The expected pattern:

- AI proposes derived links by analyzing schemas and data shapes ("this column looks like a foreign key into that table").
- Humans confirm, correct, or reject proposals — especially for explicit links and for links AI is uncertain about (`confidence: explicit` vs `confidence: inferred`).
- Confirmed links are persisted to the link store and used at query time.

The exact mechanism is undecided (see Open Questions), but the architectural placement is clear: **the link store is part of DataPro's semantic layer, not Trino's**.

#### The Layered Picture

To summarize how the pieces stack:

| Layer | Role |
|---|---|
| **Trino** | Raw, queryable facts. SQL over many backends. |
| **DataPro Object Layer** | Object identity and properties. Turns rows into typed, identified objects. |
| **DataPro Link Store** | Relationship semantics. Field mappings (derived links) and curated edges (explicit links). |

Trino tells you what the data *is*. The Object Layer tells you what it *represents*. The Link Store tells you how it *connects*.

### Why This Split Works

- Trino does the hard infrastructure work (federation, query execution, SQL parsing, connector ecosystem) so we don't have to.
- AI's job becomes well-scoped: write missing Trino connectors and write catalog configuration. No AI-generated query code, no AI-generated execution logic.
- DataPro's deterministic query engine handles all query parsing, planning, SQL generation, and result assembly. Predictable, testable, debuggable.
- Time series stays fast and idiomatic by skipping SQL and going direct to whatever backend the user has.
- The two abstractions stay clean and don't pollute each other.

## DataPro's Query Language

> **This is currently undecided and is the most important open design question for DataPro.**

What does a query *into* DataPro look like? SQL is what comes out the other side, into Trino — but it should not be what goes in.

### Why Not SQL as Input

Exposing the full power of SQL as DataPro's input language is the wrong move:

- **JOINs in DataPro should always follow links.** Arbitrary SQL JOINs would let consumers join columns that have no semantic relationship — defeating the whole point of the link store. Joins in DataPro must be link traversals, not free-form column matches.
- **Even if more complex queries are eventually supported, DataPro must understand them anyway.** DataPro can't be a pass-through. To do its job at all, it needs to:
  1. Resolve relationships and figure out what to join on (via the link store)
  2. Figure out which backend catalogs to query for a given object (multiple catalogs per object type)
  3. Apply broadness limits per catalog and reject queries that are too broad
- If DataPro has to understand the query anyway, accepting SQL as input gives us nothing. Worse, it ties the input language to a syntax that doesn't naturally express object/link semantics.

### What the Query Language Must Support

Whatever shape it takes, DataPro's query language needs to support:

- **Object selection by type and ID** — "give me `Application:billing-api`"
- **Object selection by property filters** — "give me all `Application` objects where `environment = 'prod'`"
- **Link traversal** — "for this app, give me its host" / "for this host, give me all apps running on it"
- **Property selection** — "I only want `name` and `status`, not the full object"
- **Multi-hop traversal** — "for this incident, give me the apps it affected, and for each app, its team"
- **Broadness signals** — explicit time ranges, limits, etc. so the engine can validate the query against per-catalog broadness limits

What it must **not** do:

- Allow arbitrary JOINs that aren't backed by links
- Expose SQL semantics that leak Trino specifics
- Be so flexible that DataPro can no longer reason about the query before executing it

### Candidate Approaches (Not Yet Decided)

- **A graph-style query language**, similar in spirit to GraphQL or Cypher. Natural fit for object + link semantics. Expressive but well-bounded.
- **A constrained DSL** purpose-built for DataPro — a query is a structured object (selectors, traversals, filters, projections) rather than text. Easy to validate, easy for AI and humans to construct, but less familiar.
- **A REST-ish API** with object types as resources and links as related-resource endpoints. Simple, but limited expressiveness for multi-hop and complex filters.

A hybrid is likely: a structured query format as the canonical representation, with an optional surface syntax (text-based, GraphQL-like) for human authors.

This question deserves serious thought before any code is written.

## The AI Harness

AI has a precisely scoped role in DataPro. It is **not** writing the query engine, not generating SQL, and not making runtime decisions. The deterministic query engine in Core handles all of that. AI runs only at setup/repair time, launched from Dashboard, and its outputs are reviewed by humans before they're registered.

### What AI Does

For **object catalogs**, AI has two jobs:

1. **Write the Trino connector if one doesn't already exist.** Trino has a large connector ecosystem, so this is only needed for non-standard data sources (a quirky internal API, an XML file format, a Git repo, etc.). When a connector is needed, AI writes it within Trino's connector framework. (This is the rare case; configuring a catalog against an existing connector is the common case.)

2. **Write the catalog's configuration.** This is declarative metadata that DataPro's engine consumes:
   - Which Trino connector this catalog uses, and its connection properties
   - Which object types this catalog advertises
   - Which traits each of those object types has (Identified, Temporal, Linked, Measurable, etc.)
   - How fields/columns map to object properties and trait fields (identifier columns, timestamp columns, value/unit columns, relationship columns)
   - Proposed link entries for the link store (derived links inferred from schema, candidates for explicit links)
   - The catalog's **query safety contract** — required filter constraints, forbidden patterns, and cost rules used to reject unsafe queries before execution. Produced through adversarial certification (see below).

For **time-series catalogs**, AI may help wire up direct catalogs against new TSDBs, but the surface area is much smaller and the bulk of the AI work is on the object side.

### What AI Does NOT Do

This is just as important as what it does:

- AI does **not** write the code that turns DataPro queries into SQL. That's deterministic and owned by DataPro's query engine.
- AI does **not** write code that assembles objects from multi-source query results. Also deterministic.
- AI does **not** make runtime decisions about which catalogs to hit, how to plan a join, or whether a query is too broad. Those decisions are made by the engine using the catalog configurations AI provided up front.

In short: **AI does setup, not execution.** Setup happens once per data source (or when a data source changes); execution happens on every query.

### The Harness Itself

The remaining open question: **how do we build the AI harness such that AI can reliably do its setup jobs — ideally one-shot, without bugs?**

The harness needs to provide guarantees. It should:
- Constrain AI to use the existing Trino connector ecosystem and hand-coded modules wherever possible, rather than reinventing logic
- Validate that generated catalogs produce correctly-shaped data
- Validate that catalog configurations are well-formed: declared object types are coherent, field-to-property mappings are complete, proposed links reference valid types
- Enforce sanity checks on broadness limits (e.g. limits must actually constrain something)
- Catch errors and retry with feedback

Because AI's outputs are static configuration and connector code (not runtime decisions), they are inherently testable — you generate the config once, validate it once, and then DataPro's deterministic engine takes over. This is what makes the AI failure mode tolerable: bugs are caught at setup time, not in production query paths.

A good harness is the difference between AI-generated setup that works reliably and AI-generated setup that produces broken catalogs.

### Tooling: LangGraph + LlamaIndex

The catalog-building agent is a multi-step workflow with validation gates — exactly the shape **LangGraph** (the graph-based variant of LangChain) is designed for. The agent's job looks like:

> inspect source → infer schema → propose object types → propose identifiers → propose traits → propose links → propose initial safety contract → generate catalog config (and, for non-existent backends, connector code) → run functional tests → run adversarial certification → tighten safety contract from failures → produce final registered catalog

Each step is a node; the graph encodes branching, retries, and validation between steps. This is much more reliable than a single one-shot prompt because each step's output can be checked, and failures can route the agent back to the relevant earlier node with feedback.

**LlamaIndex** has a complementary, secondary role. It's the right tool when the agent needs to consult a lot of reference material:

- Trino connector documentation
- DataPro's catalog configuration spec
- Examples of good past configurations
- Backend-specific docs (e.g. PromQL reference, Postgres internals)
- Company schema docs and naming conventions

When the agent's prompt starts looking less like instructions and more like reference material, LlamaIndex is what turns that material into something the agent can retrieve from on demand.

The split:

- **LangGraph = agent workflow engine.** Drives the multi-step catalog-building process with validation, retries, and routing.
- **LlamaIndex = retrieval/context layer.** Provides docs, schemas, and examples to the agent when prompts get too large to inline.
- **DataPro = deterministic runtime system.** Owns query parsing, planning, SQL generation, multi-source assembly, broadness enforcement.
- **Trino = object query execution substrate.** Federation and SQL execution.

The architectural sentence to remember:

> **AI is used at setup/configuration time, not as the runtime query planner.**

This is what keeps the product credible. Runtime behavior stays deterministic, testable, cacheable, and enforceable. AI's nondeterminism is contained at the edge of the system, where it can be validated and persisted before any user query ever depends on it.

### Adversarial Performance Certification

Performance safety is part of catalog certification, not an afterthought. Before a catalog configuration is registered into Core, it has to survive an **adversarial validation loop** run by the agent itself:

1. The agent finishes a candidate catalog configuration
2. It generates a set of normal example queries — these should succeed within cost limits
3. It generates **adversarial queries** designed to be pathological — these should be rejected by DataPro's planner before reaching Trino
4. It runs/explains them safely (using `EXPLAIN`, dry-run plans, or limited test executions against representative data)
5. It detects bad behavior — full scans, unbounded fan-out, missing pushdowns, cross joins
6. It tightens the safety contract — adding required filters, forbidden patterns, and cost caps until the adversarial queries get rejected
7. Repeat until certified — until pathological queries are rejected by the planner and normal queries still succeed

#### The Query Safety Contract

Each catalog configuration carries a query safety contract per object type. Roughly:

```yaml
object_type: MetricSample
required_constraints:
  - metric_name
  - time_range.max: 24h
  - limit.max: 10000

forbidden_patterns:
  - unbounded_scan
  - full_table_without_partition_filter
  - cross_join
  - link_traversal_without_parent_limit

cost_rules:
  max_estimated_rows:    100000
  max_execution_seconds: 5
  max_fanout_per_parent: 100
```

These are not aspirational. The deterministic planner in Core consults the contract on every query and refuses queries that violate it — before any backend is contacted.

#### Examples of Adversarial Queries

The agent must throw queries like these at every candidate catalog:

- "Give me all `MetricSample` objects" (unbounded scan)
- "Give me all `Order` objects this year" (huge time range)
- "Give me all `Host` → `App` → `Log` → `MetricSample` objects" (deep fan-out)
- "Filter on a non-indexed property only" (forced full scan)
- "Sort a huge dataset by timestamp without a time bound" (huge sort)
- "Traverse a high-cardinality link without a parent limit" (fan-out explosion)

If any of these slip past the planner and actually hit Trino, the catalog is **not** certified. Either the safety contract gets tightened, or the catalog is sent back for redesign.

#### Two Lines of Defense

The architecture deliberately uses defense in depth for performance:

- **DataPro's planner = first line of defense.** Validates required constraints, time bounds, traversal depth, fan-out, and cost estimates. Refuses unsafe queries before any backend is hit.
- **Trino resource limits = last line of defense.** Query timeouts, memory limits, max stages/tasks, resource groups. Catches anything that the planner missed.

You want both. The planner stops bad queries fast and gives clear error messages with suggested fixes ("require `time_range <= 1h` or filter by `order_id`"). Trino catches anything that escapes — bugs in the planner, gaps in the safety contract, or genuinely nasty edge cases — and turns them into bounded failures rather than runaway resource consumption.

#### Dashboard Integration

Failed certification surfaces in Dashboard as actionable feedback, not opaque errors:

> **Catalog failed certification**
> - Query: `all Orders without time range`
> - Reason: estimated 4.2B rows
> - Suggested fix: require `time_range <= 1h` OR `order_id` filter

This is the same review-and-approve loop as the rest of the catalog setup process — humans see what the agent is constraining and can adjust before approval.

> **Catalog setup includes adversarial performance testing.** The AI agent attempts to generate pathological DataPro queries against the proposed catalog config; failures are used to tighten the safety contract until the catalog can reject unsafe queries before execution.

## Consumers and Use Cases

DataPro exposes itself as a query API. Different audiences use that API in very different ways. Designing for all of them from the start keeps the API shape honest and prevents accidentally optimizing for any single one.

### Human-Driven Tools

Developers, operators, and analysts using DataPro through code: CLIs, scripts, notebooks, custom dashboards, internal tools. These users already know how to wrangle data from multiple sources — what they want is for DataPro to make that wrangling unnecessary. They can:

- Build custom dashboards without writing a dozen different data fetchers
- Use AI assistants (locally) to generate visualizations against one consistent backend
- Write CLIs, scripts, and notebooks that traverse data across sources effortlessly

DataPro Dashboard is part of what makes this audience productive — it's how operators configure DataPro, hook up their data sources via AI agents, and debug query failures. Without Dashboard, setting up catalogs would require editing config files by hand, which would defeat the AI-assisted onboarding story.

### AI Agents (via MCP)

Arguably the most strategically important consumer. AI agents — incident debugging copilots, autonomous operators, in-product assistants — all need a structured, reliable view of an organization's systems. That is precisely what DataPro provides.

Without DataPro, an AI agent operating on a real-world system has to:

- Know where data lives across many backends
- Know how to query each backend (SQL, PromQL, REST, etc.)
- Know how to join across them
- Guess at semantic relationships that aren't documented anywhere

This is brittle. AI hallucinates relationships, misses sources, fabricates broken queries.

With DataPro, an AI agent has to:

- Ask for objects by type and ID
- Follow named links to related objects
- Read field-level state metadata to understand what's available

Concretely, an agent investigating a failing app might issue:

```json
{
  "from":  "AppInstance",
  "where": { "name": "DeltaChangeDataProcessor" },
  "include": {
    "dependencies":       { "select": ["name", "status"] },
    "recent_deployments": { "select": ["timestamp", "commit", "author"] },
    "metrics":            { "select": ["cpu", "memory"] }
  }
}
```

…and get back a single structured object graph. No SQL, no PromQL, no API stitching. The agent reasons over the *result*, not over the integration plumbing.

**DataPro will expose itself to AI agents through MCP** (Model Context Protocol). The exact shape of that interface is undecided (see Open Questions), but the core operations are clear: query objects, traverse links, inspect catalog/trait/link metadata, retrieve field-level state.

This is a different position from "AI inside DataPro" — there, AI helps build catalogs at setup time (`The AI Harness`). Here, AI is *outside* DataPro, calling it as a client at runtime. DataPro is deterministic; the AI agent is not. That's exactly the right split.

### Future: DataViz (the HMS Visualization Layer)

The 2D consumer-facing dashboard from `specifications.md` is a Phase 2 application that runs on top of DataPro. From DataPro's perspective, it's just another API consumer — no different in kind from the human and AI consumers above.

This is by design: if DataPro's API is good enough for AI agents, it's good enough for whatever UI we build later.

### Strategic Positioning

DataPro is not just a tool to help humans understand systems. It is a way to make systems **legible to AI** — a deterministic, query-able view of an organization's data, exposed in a shape AI agents are actually good at consuming.

> **DataPro is the semantic data layer for both humans and AI agents operating on real-world systems.**

Two consequences of taking this positioning seriously:

- **Determinism, performance, and bounded behavior matter more, not less.** AI agents will issue thousands of queries where humans issue dozens. Every guarantee in this document (deterministic execution, safety contracts, field-level state, identity correctness) gets exercised harder by AI consumers than by humans.
- **DataMCP is a first-class deliverable, not an afterthought.** It belongs in the roadmap alongside DataPro Dashboard, and ahead of DataViz.

If this framing is taken seriously, **DataMCP may be the cleanest first wedge**: easier to demo than the full DataViz product, immediately useful, aligned with where the AI ecosystem is going, and not directly competing with existing observability vendors early on. DataViz becomes the third application built on top of DataPro (after DataPro Dashboard and DataMCP), not the only one.

## Known Hard Problems

These are not the same kind of thing as "what does the query language look like?" — they are problems we already know we have, that don't have clean off-the-shelf answers, and that will require sustained design work. Calling them out explicitly so they don't ambush us later.

### 1. Identity Resolution

> **The core fear**: identity is the place this system silently breaks.

Nothing crashes. Everything returns data. It just isn't the right data — and consumers can't tell. That is much worse than a crash.

DataPro's whole value proposition is that it merges multi-source data into single coherent objects, hides the sources, and presents a single truth. If the merge is wrong, the entire premise collapses. So identity resolution is not a feature — it is the foundation of correctness.

This section is long because the failure modes are subtle and worth naming explicitly.

#### Failure Modes

**1. False merges (worst).** Two catalogs look like they're talking about the same thing because their identifiers happen to match, but they aren't.

- Kubernetes: `name = livestreamer` (production cluster)
- Postgres: `name = livestreamer` (staging environment)

Naive merge:

```json
{
  "id": "livestreamer",
  "status": "Running",     // from prod
  "owner":  "staging-team" // from staging
}
```

Completely wrong. No error. No warning. The object describes a system that doesn't exist.

**2. Missed merges (fragmentation).** The same logical entity has different identifiers across sources, and the system fails to unify them.

- K8s: `livestreamer_412`
- XML: `livestreamer`
- Postgres: `livestreamer-prod`

Result: three separate objects (`AppInstance`, `Application`, `Service`) that are actually the same thing. Links don't connect. Queries miss data. The UI looks inconsistent. Users feel the system is broken even though all the data is technically present.

**3. One-to-many mismatch.** Catalogs advertise different granularity but get collapsed into one type.

- Postgres has one row: `service = livestreamer`
- Kubernetes has three pods: `livestreamer_1`, `livestreamer_2`, `livestreamer_3`

If we merge as a single `livestreamer` object, "what's its status?" has no coherent answer — there are three pods running with possibly different statuses. The merge collapses distinct things into one and produces nonsense.

**4. Temporal drift.** Sources reflect the same entity at different points in time, and the merge silently mixes them.

- K8s: `livestreamer → host A` (now)
- Metrics: `livestreamer → host B` (5 minutes ago, before a reschedule)

Merge:

```json
{ "host": "A", "cpu_source_host": "B" }
```

The object contradicts itself. Without temporal awareness, multi-source merges across moving systems produce these contradictions all the time.

**5. Schema lies.** A column name suggests one meaning; the actual semantics are different.

A catalog configuration says `Temporal.timestamp = created_at`. The agent inferred this from the column name. But:

- `created_at` = the row's database insertion time
- The actual *event* timestamp lives in `event_time`

Now every time-based query against this object is silently wrong. Correlations fail. Alerts attach to the wrong moment. Nothing throws an error.

**6. Cascading errors.** Identity errors don't stay contained.

> Wrong `AppInstance` ID → wrong `Host` link → wrong metrics join → wrong alert attribution.

Every layer that consumes the object inherits the error and amplifies it. By the time a human sees the consequence, the original bad mapping is many hops away from the symptom.

#### Why This is Uniquely Dangerous in DataPro

Other systems get away with weaker identity stories because they expose their sources. SQL clients show you which table a row came from. Grafana shows the data source for each panel. Users can sanity-check.

DataPro deliberately hides sources and presents a unified object. That's the value proposition — and it's also why bad identity is a uniquely dangerous failure mode here. There's no fallback to "well, let me check the source data" once the merge has happened.

#### What Identity Actually Needs to Be

Not "this column is the ID." More like:

```
Identity system =
    canonical ID definition
  + mapping rules per catalog
  + transformations
  + confidence tracking
  + conflict detection
  + (eventually) time-aware identity
```

#### Minimal System

**1. Canonical ID per object type.** Declared at the type level, not at the catalog level.

```yaml
AppInstance:
  canonical_id: "{namespace}/{name}"
```

**2. Mapping rules per catalog.** Each catalog declares how its native data produces a canonical ID.

```yaml
k8s:
  id: "{namespace}/{name}"

xml:
  id: "{app_name}/prod"

postgres:
  id: normalize(service_name)
```

**3. Transformations.** Common building blocks the mapping rules can use:

- Strip suffixes (`_prod`, `-staging`)
- Normalize casing
- Split composite fields
- Apply regex extraction

These should be reusable primitives, not custom code per catalog.

**4. Confidence tracking.** Every mapping carries a confidence level.

```yaml
mapping:
  type:       inferred
  confidence: 0.7
```

vs.

```yaml
mapping:
  type:       explicit
  confidence: 1.0
```

Inferred mappings (especially AI-proposed ones) are visible to operators in Dashboard and can require explicit human approval before being trusted.

**5. Conflict detection.** When two catalogs produce conflicting values for the same property of the same object, surface the conflict instead of silently picking one.

```json
{
  "host": {
    "value":    "h1",
    "conflict": true,
    "sources":  ["k8s", "metrics"]
  }
}
```

This piggybacks on field-level state metadata (see Hard Problems §2) and is what turns silent wrongness into visible wrongness. Visible wrongness is fixable; silent wrongness is not.

**6. Time-aware identity (later).** Eventually, identity may need to depend on time — `livestreamer` was on `host_A` yesterday and `host_B` today, and the merge should respect that. Not needed early, but the data model should leave room for it.

#### Phasing

The implementation can be staged sensibly:

- **Phases 1–3** (`datapro_development_plan.md`): hardcode identity. Assume perfect alignment. Move fast. The internal model already separates native source ID from canonical ID (per Implementation Discipline), so adding the rest doesn't require restructuring later.
- **Phase 4+**: introduce canonical ID definitions per type, mapping rules per catalog, transformation primitives, and validation that the rules actually produce stable IDs against real data.
- **Phase 6+**: add confidence tracking and conflict detection. Start surfacing inferred mappings and conflicting properties to humans through Dashboard.
- **Later**: time-aware identity if real data demands it.

#### Bottom Line

The fear isn't complexity. It's silent correctness failure. The system will run fast, return data, and look clean — and be wrong in ways users cannot detect. Every other piece of DataPro (links, joins, object assembly, the UI on top of it) depends on identity being right.

> **Identity resolution is not a feature. It is the foundation of correctness.**

### 2. Partial Data and Missing Catalogs

When an object is composed from multiple catalogs and one of them is broken or missing, what happens?

- Returning a partial object silently is dangerous — consumers can't tell something's wrong
- Failing the whole query is annoying — one broken catalog kills everything
- The right answer is a partial object with explicit per-field status metadata

Concretely, every property in a returned object should have an attached state. Something like:

```json
{
  "status": "Running",
  "config": {
    "state": "unavailable",
    "reason": "catalog missing"
  }
}
```

This lets debugging stay sane. Without it, a missing catalog silently produces wrong-looking objects and consumers waste hours figuring out why. The exact shape of this metadata is part of designing the response format alongside the query language.

### 3. Query Planning Above Trino

Trino does query execution. It does not do **semantic query planning**, which is what DataPro must do on top.

DataPro's planner has to decide:

- Which catalogs to hit for a given query (multiple catalogs per object type)
- In what order
- Which filters and aggregations to push down to Trino vs. apply afterwards
- How to join based on link store entries
- Whether to fan out, batch, or fuse subqueries

This is a real query planner, not just a SQL generator. Trino helps a lot — it owns physical execution and many optimizations below the SQL boundary — but the semantic planning above is ours to build. It should not be underestimated.

### 4. Link Ambiguity and Named Relationships

A type can have multiple distinct relationships to the same target type:

- `Application.host_id → Host.id` (`RUNS_ON` — where it's running now)
- `Application.backup_host_id → Host.id` (`BACKED_UP_BY` — failover target)

If a consumer says "give me this app's host," which one do we return? Both? Neither? The wrong one?

**Hard rule: link traversal is always by relationship name, never by inferring from target type alone.** The link store schema already requires a `relationship` field on every entry, but the query language must enforce this:

- `app.RUNS_ON` returns the runtime host
- `app.BACKED_UP_BY` returns the failover host
- A bare `app.host` is rejected as ambiguous, or explicitly returns a structured set with all matching relationships

This is what makes named relationships first-class instead of a nicety.

### 5. Performance and Fan-Out

Multi-hop queries can explode. Consider:

> Give me 100 hosts → for each host, its apps → for each app, its deployments → for each deployment, its metrics.

That's a 4-hop query that can fan out to dozens of catalogs and hundreds of underlying backend queries. Naive execution is unusably slow.

DataPro's planner will need:

- **Batching** — collecting "apps for these 100 hosts" into a single bulk query, not 100 separate ones
- **Query fusion** — when multiple object types share a catalog, planning a single SQL query instead of one per type
- **Caching** — at the catalog level (results), at the planner level (plans), and possibly at the link traversal level (resolved edges)
- **Prefetch planning** — when the consumer's traversal pattern is predictable, pulling upstream and downstream data in the same round-trip
- **Safety-contract enforcement at plan time** — rejecting queries whose required constraints are missing, whose fan-out would exceed limits, or whose estimated cost exceeds the catalog's cost rules. This is the *first* line of defense; Trino's resource limits are the *last* line of defense (see "AI Harness > Adversarial Performance Certification").

The safety contract that drives plan-time rejection isn't hand-tuned — it's produced by the adversarial certification process at catalog setup time. That process is how we get reasonable contracts without humans manually enumerating pathological cases for every new data source.

These features are what determine whether DataPro is fast enough to be useful at scale. They aren't optional polish.

## Decisions Made

- **Trino is the only core infrastructure piece.** Federation, query execution, and a normalized SQL access surface come from Trino. We do not rebuild any of this.
- **Semantic normalization is DataPro's job, not Trino's.** Object identity, property meaning, relationship meaning, and merge rules across data sources all live in DataPro. Trino tells us what rows look like; DataPro tells us what they mean.
- **DataPro does not accept SQL as input.** SQL goes out the other side, to Trino. Input is DataPro's own query language (TBD). Arbitrary JOINs are explicitly disallowed — joins must be link traversals.
- **The query engine is deterministic and owned by DataPro.** Hand-written, not AI-generated. AI does setup (catalogs and configuration); the engine does execution. This is what makes AI failure modes tolerable.
- **DataPro is split into Core and Dashboard.** Core is the deterministic, headless data plane. Dashboard is the management/control plane where humans interact with the system, review AI proposals, and launch setup/repair agents. AI lives in Dashboard, never in Core's hot path.
- **Human review of AI proposals is mandatory, not optional.** Catalog configurations produced by AI must be reviewed (with sample resolved objects) before they enter Core. Auto-approving AI output is explicitly disallowed.
- **Catalog certification includes adversarial performance testing.** The setup agent generates pathological DataPro queries against the proposed catalog and tightens its safety contract (required constraints, forbidden patterns, cost rules) until pathological queries are rejected before execution. No catalog is registered without passing certification.
- **Performance safety is defense in depth.** DataPro's planner is the first line of defense (rejects unsafe queries based on the safety contract before contacting Trino); Trino's resource limits (timeouts, memory caps, resource groups) are the last line of defense. Both are required.
- **AI agents are a first-class consumer of DataPro.** Not just human-driven tools and DataViz — autonomous AI agents are a primary audience and arguably the strongest early wedge. They consume DataPro via **DataMCP**, the Model Context Protocol surface (Phase 2 of HMS). This is the runtime AI use case (AI calling DataPro as a client), distinct from the setup-time AI use case (AI inside DataPro building catalogs).
- **Positioning: DataPro is the semantic data layer for humans and AI agents.** Making systems legible to AI is a primary goal of the product, not a side effect. This raises, not lowers, the bar on determinism, performance, and bounded behavior.
- **Catalogs advertise object types via configuration.** Multiple catalogs can advertise the same object type; DataPro figures out which catalogs to hit for a given query and assembles the result.
- **Links live in a DataPro-owned link store.** Trino executes the join SQL, but the declared relationships between object types — which field on A is an ID for B, in which direction, with what cardinality — are persisted and managed by DataPro.
- **The link store stores meaning, not materialized edges.** Most entries are *derived links* (field-to-object-type definitions resolved at query time). For curated, non-data-derivable facts, the link store also holds *explicit links* between specific objects (e.g. `Application:billing-api DEPENDS_ON Application:auth-service`).
- **Joins are semantic, not just SQL.** A join in DataPro is a traversal of a link — backed by a field mapping, an explicit relationship, or both — not an arbitrary SQL JOIN.
- **Backends are whatever the user already has.** Postgres, VictoriaMetrics, Kafka, MongoDB, internal APIs, files in a repo — none of these are core to DataPro. We just need a connector for each one.
- **Time-series catalogs use direct, backend-specific implementations that bypass SQL.** They speak the backend's native query language (PromQL, Flux, etc.) for performance and idiomatic access.
- **The same backend can be exposed through both abstractions.** A time-series backend like VictoriaMetrics can also be exposed as objects via Trino, so users can pick the right view for the question they're asking.
- **AI's jobs are bounded and setup-only**: write missing Trino connectors, and write catalog configuration (object types advertised, traits per type, field/property mappings, proposed links, broadness limits). AI does not write query code, plan queries, or make runtime decisions.
- **Objects + Traits is the unifying model.** There is no separate `Event`, `Log`, `Alert`, `Relationship`, or `MetricSample` primitive. They are all Objects with the appropriate traits (Temporal, Linked, Textual, Measurable, etc.). This keeps the abstraction surface small while letting the query engine still reason about what operations are valid for each type.
- **The catalog-building agent is built on LangGraph.** Multi-step graph with validation gates: inspect → infer schema → propose object types/IDs/links/limits → generate catalog config (and, rarely, Trino connector code) → test → fix → register. LlamaIndex is a secondary tool for retrieval over reference docs (Trino connector docs, DataPro spec, past configs, backend docs) once prompts grow beyond what fits inline.
- **Identity is a first-class concern, not a single column.** Each catalog configuration must declare how its native identifier maps to the canonical ID for the object type — including string transforms, composite keys, and confidence. See Known Hard Problems §1.
- **Link traversal is always by relationship name.** Bare `app.host` is rejected; consumers must say `app.RUNS_ON` or `app.BACKED_UP_BY`. The link store's `relationship` field is required, not decorative. See Known Hard Problems §4.
- **Partial data is structured, not silent.** When a source catalog is unavailable, affected fields return explicit metadata (`state: unavailable`, `reason: ...`) rather than null. The whole query does not fail because one catalog is broken. See Known Hard Problems §2.

## Open Questions

1. **What does DataPro's query language look like?** SQL is out. The shape of the input language (graph-style, structured DSL, REST-ish, hybrid) is the most important undecided design question. Whatever it is, it must support object selection, link traversal, multi-hop queries, projections, and broadness signals — and must remain understandable enough for DataPro to reason about every query before executing it.

2. **What is the right set of traits?** The starting set (Identified, Temporal, Linked, Measurable, Textual, Versioned, Owned, Located) is a guess. We'll likely add and refine traits as we hit real data sources — candidates include `Hierarchical`, `Spatial`, `Stateful`, `Costed`, and others. The choice has direct consequences for what the query engine can express.

3. **How do we design the catalog-building LangGraph?** The shape of the workflow (node decomposition, validation gates between steps, retry/feedback loops, what's checked where) is a major design problem on its own. Tooling is decided (LangGraph + optional LlamaIndex); the graph itself is not.

4. **How exactly do links get into the link store?** AI proposes derived links from schema analysis, humans confirm or correct, but the workflow, UI, and persistence model are undecided.

5. **What does DataMCP look like?** AI agents are a first-class consumer (see "Consumers and Use Cases > AI Agents"), and DataMCP is the integration shape. The exact tool definitions (what operations are exposed, how object graphs are returned, how field-level state and identity confidence are surfaced to the agent, how links are described in tool metadata) need careful design — the wrong shape here makes DataPro hard for agents to use even when the underlying data is correct.

6. **What about other tools we haven't yet evaluated?**
   - **Knowledge graph / entity resolution tools** for the object abstraction — there may be existing systems for modeling entities across data sources that we should learn from or adopt.
   - **Existing query languages** (GraphQL, Cypher, OpenCypher, GQL, SPARQL) — any of these could be a starting point or inspiration for DataPro's query language.

   These remain worth surveying before we commit to building from scratch.
