#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill in API keys before running payments."
fi

echo "Starting Postgres + Redis..."
docker compose -f deploy/compose/docker-compose.yml up -d postgres redis

echo "Waiting for Postgres..."
sleep 3

export DATABASE_URL="${DATABASE_URL:-postgresql://keenpay:keenpay@localhost:5432/keenpay}"
psql "$DATABASE_URL" -f docs/SCHEMA.sql
psql "$DATABASE_URL" -f db/seeds/dev_products.sql

echo "Bootstrap complete."
