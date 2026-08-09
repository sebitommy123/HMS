HMS · DataViz — Specifications

> **Scope:** This document specifies **DataViz**, the human-facing 2D spatial dashboard product within HMS. DataViz is one of three HMS products; the others are **DataPro** (the semantic data layer — see `datapro.md`) and **DataMCP** (the AI agent surface). DataViz is built on top of DataPro and is delivered in Phase 3 (see `development_plan.md`).
>
> Historically this document covered both the data-processing layer ("Injectors") and the visualization layer. The data-processing concepts have since been formalized as DataPro and live in `datapro.md`. The Injectors section below is preserved for context but should be considered superseded by DataPro for any new work — it documents the original product thinking that motivated DataPro's design.

---

At its core, DataViz is a flexible visualization program for data. It consumes the normalized object and time-series API exposed by DataPro and renders results on a spatial, explorable 2D dashboard. There are two hard problems to solve and a set of essential supporting features.

---

## 1. Injectors

An injector turns any data source — a Postgres table, a repo, an XML file, a Kubernetes API — into something the client can query and render uniformly. When a user points the system at a new data source ("hey, look at this table"), AI is kicked off to build an injector for it.

An injector has three parts:

### 1.1 Standardization

The standardization layer is a function that translates standardized, normalized requests into specific requests against the actual backend. This is a critical distinction: **we are not copying or maintaining a normalized replica of the data**. We are building a translation function that knows how to take a generic query and turn it into whatever the backend needs.

For example, "give me data in time range X" means very different things for different backends. For a SQL database, it means figuring out which column holds the timestamp and writing the right WHERE clause. For a collection of XML files, it might mean sorting through filenames or parsing date fields inside the documents. The standardizer handles all of this so the rest of the system doesn't have to care.

### 1.2 Filtering

The filtering layer defines which axes the data can be filtered on and how to actually apply those filters against the backend. The client can do additional filtering on its end, but the data needs to be reduced server-side first so we're not shipping enormous volumes to the browser.

This layer is both a specification (which axes exist, what values are valid) and an implementation (how to turn a filter request into a backend query).

### 1.3 Aggregation

Many data sources contain billions or trillions of data points — nanosecond-precision metrics spanning years. We cannot send all of that to the client. The aggregation layer reduces the data to a consumable size based on what the client actually needs.

The client specifies the aggregation strategy. For example, if the client wants one year of data displayed on a 1000px-wide graph, it might request daily averages — producing 365 points instead of billions. Different strategies are supported: averages, sums, min/max, percentiles, bucketing by time window, etc. The component rendering the data determines which strategy and granularity to request.

### 1.4 Modules

**AI must not reinvent the wheel.** Aggregation, filtering, standardization — these operations are largely the same across data sources that share a backend type. A SQL aggregation layer should not be vibe-coded from scratch for every new Postgres table.

Instead, we maintain a library of hand-coded, well-tested modules. These modules handle the complex, error-prone logic: SQL aggregation, time-range queries, pagination, streaming, etc. They are written once, by hand, and done right.

AI's job when building an injector is narrow:
- Analyze the data source: what columns exist, what they mean, how time is represented, what the schema looks like
- Map the data source's specifics onto the module interfaces
- Wire the modules together into a working injector

AI is doing basic analysis and configuration. It is **not** writing heavy data-processing code. This is essential — otherwise we end up with inconsistent, fragile, hard-to-debug injectors that each work slightly differently. The modules guarantee consistency and correctness.

---

## 2. User Interface

The user interface is a 2D spatial dashboard — an open-world map you can scroll, pan, and zoom. It is not a traditional click-through dashboard hierarchy (though navigation between views will be possible). The primary interaction is spatial: dragging around to explore, zooming in and out, building spatial memory of where things are and how they relate.

### 2.1 Pre-made Components

The naive approach would be to let AI generate arbitrary widgets on the fly. This will be available as an escape hatch, but it is not the primary path. AI-generated widgets have a meaningful failure rate and are cumbersome for the user to specify.

Instead, the system provides a library of pre-made, hand-tested components:
- Graphs (line, bar, area, etc.)
- Tables
- Lists
- Indicator lights / status badges
- Buttons / actions
- Layouts (grids, rows, columns, sections)
- Text / labels

AI's role is to **select** the right component and **configure** it — not to create new components. For example, if the user asks for "a graph of CPU usage for host A," the AI:
1. Selects the graph component
2. Sets the x-axis to time
3. Sets the y-axis label to "CPU %"
4. Points the data source at the appropriate injector with the right filters

This is configuration, not code generation.

### 2.2 Composability

Components are modular and composable — components can take other components as children. Examples:
- A table where one column contains a list component for each row
- A grid of graphs, one per host
- A layout section containing a mix of tables, graphs, and status indicators

All of this is driven by configuration. A table specifies which columns to pull from the data source, and one of those "columns" might be configured to render a child component (like a list) that itself pulls data based on the current row's context.

No vibe coding is required for composition — it's all declarative configuration that the AI sets up.

---

## 3. Other Essential Features

### 3.1 Links

Data in an organization is spread across many sources, but it's deeply interconnected. Hosts might come from a back-office spreadsheet. Applications deployed on those hosts come from Kubernetes. These need to be joinable.

Links are declarative configuration that defines how to navigate from one normalized object to another across data sources. For example:
- "The `id` field on a Host object in data source A corresponds to the `host_id` field on an App object in data source B"

This is not a traditional database join. It's a declared relationship that the system can traverse at query time.

Links are powerful because they enable component composition across data sources. A table of hosts can have a column that renders a list of apps — and that list is populated by following the link from each host to its associated apps. All through configuration.

**Open question:** How exactly links are set up is undecided. AI will likely need to establish them, but whether that's part of the injector process or a separate step remains to be determined.

### 3.2 Future Expansion

The MVP is the bare-bones version of everything above: injectors that can standardize/filter/aggregate, a spatial 2D canvas with composable pre-made components, and links between data sources.

Beyond MVP, planned areas of expansion include:
- **Alerts**: Visual indicators when something goes wrong — flashing components, color changes, notification banners
- **Conditional rendering**: Components that change what they display based on the current state of the data (e.g., show a detailed breakdown only when error rates exceed a threshold)
- **Richer interactions**: Clicking on an element to drill down, hover tooltips with context, right-click menus for actions
- **Collaboration**: Shared dashboards, annotations, comments
- **Temporal exploration**: Replaying historical states of the dashboard, comparing time windows side by side

The architecture should accommodate these from the start, but they are not part of the initial build.
