# DataPro Flex

A Trino connector that delegates every callback to a per-catalog Python
worker subprocess over Arrow Flight. Users author a Python module that
implements two functions (`get_tables`, `read_table`) and Trino sees a
normal SQL table. Flex assumes small data: there are no user-facing
splits (the framework runs one internally) and no column-projection
hook (`read_table` returns every column; the worker projects for the
query).

This is the runtime side of the *flex* category from
`HMS/datapro_revised_v2.md` § Layer 2. The AI-driven module-authoring
Dashboard (the three-panel UI from the same doc) is a follow-up; this
slice ships only the runtime + a hand-authored example, so a developer
can spike a flex catalog with `vim module.py`.

## What it does

- A single Java Trino plugin (`flex`) lives in
  `/usr/lib/trino/plugin/flex/` inside the `hms-datapro/trino-flex:dev`
  image.
- Each catalog created with `USING flex` points at a Python module via
  the `flex.module_path` catalog property.
- On first query against the catalog, the plugin spawns
  `python -m datapro_flex.worker --module-path <path> --port 0` in a
  long-lived subprocess and connects to it over Arrow Flight (gRPC).
- The worker imports the user's module, then serves `get_tables`
  (Flight `do_action`) and `read_table` (Flight `do_get` streaming Arrow
  record batches) for the catalog's lifetime. A `get_splits` action also
  exists but is framework-internal — it always returns one synthetic
  split without consulting the module.
- Trino sees a normal connector: schemas, tables, splits, pages. The
  Python is invisible to the planner.

## Layout

```
flex/
├── connector/                              # Java
│   ├── pom.xml                             # Maven build (Trino SPI + Arrow Flight)
│   └── src/main/
│       ├── java/ai/hms/trino/flex/         # 12 classes, ~700 LOC
│       │   ├── FlexPlugin.java             # SPI entry point
│       │   ├── FlexConnectorFactory.java   # catalog-property validation
│       │   ├── FlexConnector.java          # per-catalog Connector
│       │   ├── FlexMetadata.java           # schemas/tables/columns + applyFilter
│       │   ├── FlexTableHandle.java        # opaque handle (record)
│       │   ├── FlexColumnHandle.java       # opaque column handle (record)
│       │   ├── FlexSplit.java              # opaque split (record)
│       │   ├── FlexSplitManager.java       # enumerates splits via worker
│       │   ├── FlexPageSourceProvider.java # factory for FlexPageSource
│       │   ├── FlexPageSource.java         # Arrow batch → Trino Page translation
│       │   ├── FlexPythonWorker.java       # subprocess + Flight client lifecycle
│       │   ├── FlexTransactionHandle.java  # singleton enum (no transactions)
│       │   ├── FlexTypes.java              # type-string ↔ Trino Type map
│       │   └── ConstraintSerializer.java   # TupleDomain → JSON for push-down hint
│       └── resources/META-INF/services/io.trino.spi.Plugin  # SPI registration
├── runtime/                                # Python
│   ├── pyproject.toml                      # pyarrow + grpcio
│   └── src/datapro_flex/
│       ├── __init__.py                     # public surface
│       ├── contract.py                     # FlexModule Protocol + dataclasses
│       ├── arrow_schema.py                 # type mapping + batch_from_rows helper
│       └── worker.py                       # Arrow Flight server (one per catalog)
├── examples/users_json/                    # hand-authored sample
│   ├── module.py
│   ├── users.json
│   └── smoke.sh                            # boots Trino + runs sample queries
└── README.md
```

## Build

Two artifacts: the connector JAR (Java) and the runtime venv (Python).
The custom Trino image bundles both.

```bash
# 1. Build the connector JAR (Maven-in-Docker, no host Java needed)
cd flex/connector
docker run --rm \
  -v "$(pwd):/work" \
  -v "$(pwd)/.m2:/root/.m2" \
  -w /work \
  maven:3.9-eclipse-temurin-25-alpine \
  mvn -B -DskipTests package
# Outputs: target/trino-flex-connector-0.1.0.jar (~19 KB)
#          target/plugin/*.jar  (Arrow + Jackson + gRPC runtime deps)

# 2. Build the custom Trino image (bundles JAR + Python + datapro_flex)
cd ../..  # back to repo root
docker build -t hms-datapro/trino-flex:dev -f datapro/trino-flex/Dockerfile .
```

`scripts/dev-up.sh` does both automatically if the artifacts are missing
or out of date.

## Try it

```bash
flex/examples/users_json/smoke.sh
```

Boots a transient Trino, registers a catalog pointing at the
`users_json` example, runs `DESCRIBE`, `SELECT *`, a `WHERE`-filtered
query, and `COUNT(*)`. Five rows out, no surprises.

For the full Core stack instead:

```bash
scripts/dev-up.sh                            # ensures the image is current
curl -fsS -XPOST http://127.0.0.1:5001/catalogs \
  -H 'content-type: application/json' \
  -d '{"name":"users","connector":"flex","properties":{"flex.module_path":"/var/datapro-flex/users_json/module.py"}}'
curl -fsS -XPOST http://127.0.0.1:5001/raw-trino-query \
  -H 'content-type: application/json' \
  -d '{"sql":"SELECT * FROM \"users\".\"default\".users"}'
```

## How to author a module

A flex module is any Python file that defines two module-level
callables. Read `flex/runtime/src/datapro_flex/contract.py` for the
authoritative shapes; in short:

```python
from typing import Iterable
import pyarrow as pa
from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "events",
    "columns": [
        {"name": "event_id",   "type": "BIGINT"},
        {"name": "occurred",   "type": "TIMESTAMP_TZ"},
        {"name": "actor",      "type": "VARCHAR"},
        {"name": "payload",    "type": "JSON"},
    ],
}

def get_tables() -> list[dict]:
    return [TABLE]

def read_table(table: str) -> Iterable[pa.RecordBatch]:
    # Yield Arrow record batches for the whole table. Always produce
    # every declared column — the worker projects down to the columns
    # the query needs. Use `batch_from_rows` if you prefer dict-of-row
    # iteration; it handles type coercion. For a large table, yield it
    # in chunks rather than one giant batch.
    for path in sorted(Path("/var/log/events").glob("*.jsonl")):
        rows = (json.loads(line) for line in open(path))
        yield batch_from_rows(rows, table=TABLE)
```

### Supported types (Phase A)

| Trino type   | flex type string | Python value             |
| ------------ | ---------------- | ------------------------ |
| BIGINT       | `BIGINT`         | `int`                    |
| INTEGER      | `INTEGER`        | `int`                    |
| DOUBLE       | `DOUBLE`         | `float`                  |
| BOOLEAN      | `BOOLEAN`        | `bool`                   |
| VARCHAR      | `VARCHAR`        | `str`                    |
| DATE         | `DATE`           | `datetime.date`          |
| TIMESTAMP TZ | `TIMESTAMP_TZ`   | tz-aware `datetime`      |
| JSON         | `JSON`           | `str` (caller serializes)|

DECIMAL, ARRAY, ROW, MAP, and the rest of Trino's type system are
intentionally not in Phase A. They land when a flex module needs them.

### Catalog properties

| property            | required | default                    | meaning                                                     |
| ------------------- | -------- | -------------------------- | ----------------------------------------------------------- |
| `flex.module_path`  | yes      | —                          | Absolute path to the module on the Trino container's FS     |
| `flex.python`       | no       | `/opt/flex-venv/bin/python3` | Python interpreter to invoke                              |

### Predicate pushdown (Phase A)

There is no module-facing pushdown. Flex assumes small data, so a
module always returns the whole table from `read_table` and Trino
applies any `WHERE` clause after the scan. (The connector still records
pushed-down predicates on its table handle internally, but never ferries
them to Python — see `FlexMetadata.applyFilter`.)

## What this is NOT

- **Not Phase B**: modules live as bind-mounted files on the Trino
  container today. Phase B moves storage into Core's Postgres with
  versioned materialization to a shared volume.
- **Not the catalog-building Dashboard**: the three-panel AI chat UI
  is Phase D. For now, modules are authored by hand.
- **Not yet multi-node-safe**: the worker is coordinator-local. Multi-
  worker Trino clusters need an updated process model (a worker per
  node, or a coordinator-served gRPC endpoint that all nodes reach).
- **Not for hot loops**: Python + IPC are 10–100× slower than Java for
  per-row work. Use stock or bespoke for high-throughput sources;
  flex's strength is the long tail (variable shapes, modest volumes).

## Architecture cross-references

- `HMS/datapro_revised_v2.md` § Layer 2 — the four-category source
  model (native / stock / flex / bespoke) and the flex contract.
- `HMS/examples/log-connector/` — a *stock*-shape connector built end
  to end in Java. flex generalizes the runtime cost (one Java plugin,
  many Python modules) at the price of Python performance.
