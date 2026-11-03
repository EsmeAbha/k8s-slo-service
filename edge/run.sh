#!/usr/bin/env bash
# Validate the Envoy config, start the stack, run the drill. Exit code is the drill's.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p edge/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=localhost -addext subjectAltName=DNS:localhost \
  -keyout edge/certs/edge.key -out edge/certs/edge.crt 2>/dev/null
chmod 644 edge/certs/edge.key   # throwaway test key; Envoy runs as a different user in the container

COMPOSE="docker compose -f edge/docker-compose.yml"
$COMPOSE run --rm --no-deps envoy --mode validate -c /etc/envoy/envoy.yaml

cleanup() { $COMPOSE logs --tail 40 envoy || true; $COMPOSE down -v || true; }
trap cleanup EXIT
$COMPOSE up --build -d
python3 -m edge.drill --report "${1:-edge-report.md}"
