#!/usr/bin/env bash
#
# End-to-end demo. Assumes:
#   - This checkout's stack is up (scripts/dev-up.sh)
#   - Its env is loaded, so CORE/TRINO/TRINO_CONTAINER point at it:
#       eval "$(scripts/hms.py env)"
#
# Walks through: register a catalog, see it in Trino, "lose" it, reconcile, see it back.

set -euo pipefail

# Default to this checkout's slot when the env is loaded, else the main clone's.
CORE=${CORE:-http://localhost:${CORE_PORT:-5001}}
TRINO=${TRINO:-http://localhost:${TRINO_PORT:-5004}}

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

TRINO_CONTAINER=${TRINO_CONTAINER:-hms-${HMS_STACK:-main}-trino}

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
