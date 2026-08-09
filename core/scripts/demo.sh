#!/usr/bin/env bash
#
# End-to-end demo. Assumes:
#   - Trino is running on localhost:8080 (e.g. `cd ../datapro && docker compose up -d`)
#   - Core is running on localhost:5000 (e.g. `cd .. && uv run flask --app datapro_core.app run`)
#   - Postgres is running (e.g. `docker compose up -d postgres`)
#
# Walks through: register a catalog, see it in Trino, "lose" it, reconcile, see it back.

set -euo pipefail

CORE=${CORE:-http://localhost:5000}
TRINO=${TRINO:-http://localhost:8080}

pp() { python3 -m json.tool 2>/dev/null || cat; }

echo "==> /health"
curl -sS "$CORE/health" | pp
echo

echo "==> Before: no catalogs registered in Core"
curl -sS "$CORE/catalogs" | pp
echo

echo "==> Register a tpch catalog"
curl -sS -X POST "$CORE/catalogs" \
    -H 'content-type: application/json' \
    -d '{"name": "tpch_demo", "connector": "tpch"}' | pp
echo

echo "==> Core sees it"
curl -sS "$CORE/catalogs" | pp
echo

echo "==> Trino sees it (Core's view)"
curl -sS "$CORE/trino/state" | pp
echo

TRINO_CONTAINER=${TRINO_CONTAINER:-hms-trino}

echo "==> Run a query against the new catalog (via Trino CLI in $TRINO_CONTAINER)"
docker exec "$TRINO_CONTAINER" trino --execute "SELECT COUNT(*) AS nations FROM tpch_demo.tiny.nation"
echo

echo "==> Force-drop the catalog out-of-band in Trino (simulates restart wipe)"
docker exec "$TRINO_CONTAINER" trino --execute "DROP CATALOG tpch_demo"
echo

echo "==> Trino state: gone"
curl -sS "$CORE/trino/state" | pp
echo

echo "==> Reconcile via Core — catalog returns"
curl -sS -X POST "$CORE/reconcile" | pp
echo

echo "==> Trino state: back"
curl -sS "$CORE/trino/state" | pp
echo

echo "==> Clean up"
curl -sS -X DELETE "$CORE/catalogs/tpch_demo" | pp
echo
echo "Demo complete."
