# Trino log-files connector

A minimal **custom Trino connector** that exposes a directory tree of plain-text
log files as a single SQL table. Built as a hands-on test of the foreign data
source category in DataPro architecture (see
`HMS/datapro_revised_v2.md` § Layer 2).

## What it does

- Watches a configured root directory of the layout
  `<root>/<app>/<YYYY-MM-DD>.log`.
- Each line in each `.log` file must be `ISO-8601-timestamp,message`.
- Exposes a single virtual table `applogs."default".entries` with four VARCHAR
  columns:

  | column       | source                                     |
  | ------------ | ------------------------------------------ |
  | `app`        | the app-level directory name               |
  | `day`        | the file basename without `.log`           |
  | `event_time` | text before the first comma on each line   |
  | `message`    | text after the first comma on each line    |

That's the entire surface — no schema discovery, no partition pruning, no
predicate pushdown. Each `.log` file becomes one Trino split; queries scan all
splits in parallel and Trino does the filtering and aggregation.

## Layout of this directory

```
log-connector/
├── pom.xml                                 # Maven build, depends on trino-spi:480
├── src/main/java/ai/hms/trino/logs/        # 11 Java files, ~500 LOC total
│   ├── LogPlugin.java                      # entry point (registered via SPI)
│   ├── LogConnectorFactory.java            # creates the Connector from config
│   ├── LogConnector.java                   # holds metadata, splits, page sources
│   ├── LogTransactionHandle.java           # singleton enum (we don't do transactions)
│   ├── LogMetadata.java                    # exposes the one schema/table/columns
│   ├── LogTableHandle.java                 # opaque table handle (record)
│   ├── LogColumnHandle.java                # opaque column handle (record)
│   ├── LogSplit.java                       # one split per file
│   ├── LogSplitManager.java                # walks the root, builds splits
│   ├── LogPageSourceProvider.java          # creates a page source per split
│   └── LogPageSource.java                  # actually reads & parses each file
├── src/main/resources/META-INF/services/
│   └── io.trino.spi.Plugin                 # ServiceLoader entry: ai.hms.trino.logs.LogPlugin
├── generate_logs.py                        # writes 9 fake log files (3 apps × 3 days)
└── data/                                   # generated dummy data lives here
    ├── checkout/
    ├── search/
    └── inventory/
```

## Build

Requires only Docker (no host Java/Maven needed).

```bash
docker run --rm \
  -v "$(pwd):/work" \
  -v "$(pwd)/.m2:/root/.m2" \
  -w /work \
  maven:3.9-eclipse-temurin-25-alpine \
  mvn -B -DskipTests package
```

Output: `target/trino-log-connector-0.1.0.jar` (~16 KB).

## Wire into `HMS/datapro` (optional)

This example is **not** part of the default `datapro` compose stack. To try it,
extend `HMS/datapro/docker-compose.yml` on your machine, for example:

```yaml
# Paths relative to HMS/datapro when editing that compose file:
volumes:
  - ../examples/log-connector/target/trino-log-connector-0.1.0.jar:/usr/lib/trino/plugin/log_files/trino-log-connector-0.1.0.jar:ro
  - ../examples/log-connector/data:/var/log-data:ro
```

`HMS/datapro` uses **`catalog.management=dynamic`** and **`catalog.store=memory`**, so you **do not** add `*.properties` under `trino/etc/catalog/`. After mounting the JAR and data paths, register the catalog with SQL (same as DataPro Core will):

```sql
CREATE CATALOG applogs USING log_files
WITH ("logs.root" = '/var/log-data');
```

If you ever run Trino with **static** catalog management instead, you could add `applogs.properties` under `etc/catalog` and restart — see [Trino connector docs](https://trino.io/docs/current/connector.html).

## Generate data and query

```bash
python3 generate_logs.py
# After wiring the JAR + data volume + catalog into your Trino compose:
docker compose restart trino   # e.g. from HMS/datapro

docker exec hms-$HMS_STACK-trino trino --execute "SHOW TABLES FROM applogs.\"default\""
docker exec hms-$HMS_STACK-trino trino --execute "DESCRIBE applogs.\"default\".entries"

docker exec hms-$HMS_STACK-trino trino --execute "
  SELECT app, day, COUNT(*) AS lines
  FROM applogs.\"default\".entries
  GROUP BY app, day
  ORDER BY app, day"
```

Cross-catalog queries work once you register another catalog (e.g. Postgres
or `tpch`). Example with TPC-H:

```sql
SELECT n.name AS nation_name, l.app AS log_app, COUNT(*) AS log_lines
FROM tpch.tiny.nation n
JOIN applogs."default".entries l
  ON n.name LIKE '%' || UPPER(l.app) || '%'
GROUP BY n.name, l.app
ORDER BY n.name, l.app;
```

## What this teaches us about DataPro (stock vs flex vs bespoke)

This proof-of-concept is the **shape** of what DataPro ships as a **stock**
`logs` connector: parsing happens **inside Trino** in Java, across as many splits
as the planner schedules, with cross-catalog JOINs to native catalogs (Postgres,
TPC-H, Iceberg, …).

- It is **not** the old Core-side "adapted → `memory` connector" path — that
  design is superseded by **flex** (AI-written Python behind a single DataPro
  flex Trino plugin). See `datapro_revised_v2.md`.
- It is **not free** in engineering terms: ~500 lines of Java across 11 files
  just to expose a single table backed by two-column text files. The SPI is a
  real interface (metadata, splits, pages, transactions, addresses, retained
  sizes). Shipping this **once** as **stock** amortizes the cost; regenerating
  bespoke Java **per customer source** does not — hence **flex** for the long
  tail and **bespoke** only when Java hot-loop performance is non-negotiable.

**Rule of thumb:** directory-shaped logs with configurable parsers → **stock**
(YAML). Odd one-off wire formats or XML trees where Python iteration is fine →
**flex**. Extreme volume or exotic protocols where flex cannot keep up →
**bespoke** (templated from stock when possible).
