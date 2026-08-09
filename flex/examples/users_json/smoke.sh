#!/usr/bin/env bash
# Smoke test: boot a transient Trino with the flex plugin, register a
# catalog pointing at this example's module, query it, tear down.
#
# Requires that the flex JAR and trino-flex Docker image are already
# built. The dev tooling (scripts/dev-up.sh) does both; you can also
# build them manually:
#
#   cd HMS/flex/connector && docker run --rm -v "$(pwd):/work" \
#       -v "$(pwd)/.m2:/root/.m2" -w /work \
#       maven:3.9-eclipse-temurin-25-alpine mvn -B -DskipTests package
#   cd HMS && docker build -t hms-datapro/trino-flex:dev \
#       -f datapro/trino-flex/Dockerfile .

set -euo pipefail

HMS_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$HMS_ROOT/flex/examples/users_json"
PORT="${PORT:-18080}"
CONTAINER="${CONTAINER:-flex-smoke}"

cleanup() {
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Fresh container per run so we don't conflict with hms-trino.
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run --rm -d --name "$CONTAINER" -p "$PORT:8080" \
  -v "$HMS_ROOT/datapro/trino/etc/config.properties:/etc/trino/config.properties:ro" \
  -v "$HERE:/var/datapro-flex/users:ro" \
  hms-datapro/trino-flex:dev >/dev/null

echo "waiting for trino..."
for i in $(seq 1 90); do
  if docker logs "$CONTAINER" 2>&1 | grep -q "SERVER STARTED"; then echo "trino up"; break; fi
  sleep 1
done

docker exec "$CONTAINER" trino --execute \
  "CREATE CATALOG users USING flex WITH (\"flex.module_path\" = '/var/datapro-flex/users/module.py')" \
  >/dev/null

echo "--- DESCRIBE users.default.users ---"
docker exec "$CONTAINER" trino --execute "DESCRIBE users.default.users"

echo "--- SELECT * ---"
docker exec "$CONTAINER" trino --execute "SELECT * FROM users.default.users ORDER BY id"

echo "--- predicate-pushdown probe (WHERE id BETWEEN 2 AND 4) ---"
docker exec "$CONTAINER" trino --execute \
  "SELECT name, age FROM users.default.users WHERE id BETWEEN 2 AND 4 ORDER BY id"

echo "--- COUNT(*) ---"
docker exec "$CONTAINER" trino --execute "SELECT COUNT(*) FROM users.default.users"

echo "smoke OK"
