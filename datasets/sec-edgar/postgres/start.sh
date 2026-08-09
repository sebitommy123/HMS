#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONTAINER_NAME=hms-sec-edgar-postgres

cd "$SCRIPT_DIR"

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  echo "$CONTAINER_NAME is already running."
else
  echo "Starting $CONTAINER_NAME..."
  docker compose up -d
fi

printf 'Waiting for Postgres healthcheck'
while :; do
  status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
  case "$status" in
    healthy)
      echo
      echo "$CONTAINER_NAME is healthy."
      echo "Connect with: psql postgresql://sec:sec@localhost:55432/sec_edgar"
      exit 0
      ;;
    exited|dead)
      echo
      echo "$CONTAINER_NAME is $status. Recent logs:" >&2
      docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
      exit 1
      ;;
  esac
  printf '.'
  sleep 2
done
