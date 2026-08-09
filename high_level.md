HMS — Human-Machine Sympathy

**The HMS platform is three products on one semantic foundation:**

1. **DataPro** — the deterministic semantic data layer. Turns scattered data sources (databases, K8s, time-series, files, APIs) into typed objects with identity, named relationships, field-level provenance, and safety contracts. The foundation everything else builds on. *(See `datapro.md`.)*
2. **DataMCP** — the AI agent surface. Exposes DataPro through the Model Context Protocol so AI agents (copilots, autonomous ops, in-product assistants) can ask structured questions about systems and get reliable, bounded, provenance-tracked answers.
3. **DataViz** — the human surface. A 2D spatial dashboard that lets engineers pan, zoom, and explore their entire system as a navigable map. *(See `specifications.md`.)*

The shipping order is **DataPro → DataMCP → DataViz** (see `development_plan.md`). Throughout this document, "HMS" refers to the platform as a whole; specific products are referred to by name.

---

## Why HMS — three layers of value

Customers adopt HMS for three reasons that sit on a defense/offense axis. The order is the order of urgency: pain drives adoption first, opportunity drives expansion later.

### Defense I — Institutionalize knowledge before it walks out

Companies forget. When the senior engineer who knew where the data lives leaves, their replacement spends three to six months re-deriving a map that should have been written down: where each thing lives, which service writes which table, what "stale" means for this metric, why these two columns are actually the same field. Most of that map is never written down, because writing it down separately is expensive and the artifact rots the day it's published.

HMS captures the map as the **side effect of using the system**. Every catalog configuration, link declaration, identity rule, and safety contract is durable, version-controlled, and reviewable. The new engineer's first day starts with a working system and an AI agent (DataMCP) that can answer questions about it — not a treasure hunt.

> **The pitch line:** the day the senior engineer leaves, the system still knows what they knew.

This maps primarily to **DataPro** — the configurations *are* the institutional record.

### Defense II — Be useful when seconds matter

In an incident, the bottleneck is almost never "we don't have the data." It's "we can't reach it fast enough, in the right shape, without setting off three other alarms." Three engineers in three Slack channels each writing their own ad-hoc SQL across two services, eyeballing the joins, missing edges. A runbook two years out of date. A timestamp in three different timezones.

HMS collapses the operational chaos into one conversation: "show me everything that touches order #12345 in the last hour, including the host, the deployment, the recent logs." DataMCP carries the conversation; DataViz shows the topology; DataPro answers; safety contracts ensure the broad question doesn't take down the source systems.

> **The pitch line:** at 3am you don't have time to learn five SQL dialects, you have time to ask one question.

This maps to **DataMCP** (conversational interface) and **DataViz** (visual situational awareness), with DataPro as the substrate.

### Offense — Manage complexity instead of paying for it

The first two layers are about defending against bad scenarios. The third is about *enabling growth*.

Complexity tax compounds. Every new service in a growing org adds N new "how does this relate to…" conversations, and N grows superlinearly with system count. Without something like HMS, an operations team scales linearly with system count and eventually gives up — and a complexity ceiling becomes the gating factor on the company's ability to add capability.

With HMS, adding a service is a contained operation: configure one catalog, declare its links, done. The link store becomes the company's **complexity map** — explicit, queryable, evolvable. This is what lets a small operations team scale to a large system without proportional cognitive overhead, and it is the substrate AI agents need in order to reason about the system at all.

> **The pitch line:** your link store is your complexity map. Add a service, declare its links, done.

This maps to **all three products** — but especially the link layer in DataPro, which is the visible artifact of the complexity map.

### How the three layers compose

The three are not independent product lines; they are three angles on the same product. The same catalog configuration that institutionalizes knowledge (Defense I) is what's queried under pressure during an incident (Defense II) and is what allows complexity to scale gracefully (Offense). The order of adoption is almost always the order in this list — companies start because of pain, then realize the same system enables growth.

---

**The problem**  
In most companies, observability usually consists of an assorted collection of tableau graphs, grafana, databricks visualizations, ad-hoc jupyter notebooks, log files and custom web UIs. This makes it very hard to know where to go to get what, delaying incident investigations and overall making it hard to turn data into a cohesive story about your system.

**User story**  
It’s 2pm, Friday evening. DeltaChangeDataProcessor starts outputting hundreds of errors per second. The user needs to know:

1. What is DeltaChangeDataProcessor?  
2. Who does it speak to and why?  
3. How important is it?  
4. Is this situation rare? If it happened before, what did we do about it?  
5. Any changes in behavior in apps that speak to it?  
6. Any recent changes to DeltaChangeDataProcessor?

How can we make it easy for an engineer to learn this, remember it, and internalize it at a glance? We need human-machine sympathy.

**Human-machine sympathy**  
Human-machine sympathy is very hard to build \- programs operate at timescales we can’t comprehend, can enter complicated states and communicate with dozens of other machines in complicated ways. It takes hundreds of painstaking human hours of making changes to our programs, reading through logs and observing cause-and-effect for this sympathy to emerge. Can we fix this with UI?

**2D space**  
Humans have outstanding spatial memory. If shown a diagram with service A in the middle and service B in the top right, with a line connecting them, we will forever remember service B as being “positioned” above and to the right of service A. When trying to understand what's happening, this spatial memory is crucial. A single good diagram has a tremendous impact on human-machine sympathy. How far can we take this?

**AI**  
AI fundamentally challenges this landscape \- ingesting data, creating visualizations and addressing data quality issues can all be automated with minima  
l human-in-the-loop. AI, with help, can create and maintain our 2D space. But a well-defined “language” is still needed for the AI to ensure scalability and maintain performance guarantees.

**Challenges**  
1\. Data source types: Varied and company-specific

* Databases (SQL, NoSQL, GraphQL, VictoriaMetrics, etc…)  
* Structured files (JSON, XML, Yaml, etc…)  
* Unstructured files (Log files, emails, incident reports, slack conversations, code, etc…)  
* APIs (K8s, AWS, Github, confluence, internal services, etc…)

2\. Data scale: there can be a lot of data

* Query time: Queries can take minutes or even hours to complete  
* Rate limits: APIs and databases can establish rate limits

3\. Data change: Data moves, the meaning changes

* Columns can get deprecated  
* Data quality can deteriorate  
* Downtime

4\. Code maintenance: Data ingestion and visualization code breaks

* APIs can change  
* Datasets can be deleted

5\. Developer time: Setting up the 2D space takes time, uses considerable dev resources

* A lot of it will essentially just be duplicating existing documentation but in a 2D format  
* Setting up the data ingestion will also take time

**Solutions**  
AI \- The complexity of data source types, data change, code maintenance and developer time will all be significantly ameliorated with AI. AI will code the ingestion layer, will make sure it still works when data changes or the code breaks, and will enable developers to modify the 2D space with words.

Data infrastructure \- To manage the huge scale of potential data sources, we need things like caching, throttling, and compression. This will be pre-programmed into components so that AI can assemble them into a pipeline customized to each data source. They won’t be “winged” by AI every time, AI will only do the easier part of validation and calling into the components as necessary.

UI elements \- AI will pick from pre-programmed UI elements, that will be pre-tested to make sure they scale to large amount of data and are versatile to many different situations.

