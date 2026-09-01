#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

docker compose \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.test.yml \
  up -d --build --wait

sleep 5
docker compose \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.test.yml \
  exec -T postgres psql -U keenpay -d keenpay_test -f /dev/stdin < docs/SCHEMA.sql

curl -sf http://localhost:8000/api/v1/health/live
curl -sf http://localhost:8000/api/v1/health/ready

cd api && pytest tests/integration -v

docker compose \
  -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.test.yml \
  down -v

echo "Integration tests passed."
