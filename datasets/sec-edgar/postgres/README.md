# SEC EDGAR Postgres

Standalone Postgres database for the downloaded SEC TSV datasets. This is separate from the HMS Trino sandbox.

## Run

```bash
cd HMS/datasets/sec-edgar/postgres
./start.sh
```

Connection details:

- Host: `localhost`
- Port: `55432`
- Database: `sec_edgar`
- User/password: `sec` / `sec`

## Loaded Schemas

- `sec_financial`: SEC 2026 Q1 Financial Statement Data Sets (`sub`, `num`, `tag`, `pre`)
- `sec_form345`: SEC 2026 Q1 Forms 3/4/5 insider transaction tables
- `sec_form13f`: SEC Dec 2025-Feb 2026 Form 13F tables

All imported columns are loaded as `text` to preserve raw values and avoid premature normalization. Row counts are recorded in `public.import_manifest`.

The init loader also creates basic indexes on accession numbers, CIKs, tags, and CUSIPs for common joins.
