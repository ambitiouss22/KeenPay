#!/usr/bin/env bash
set -euo pipefail

DUMP_FILE="${1:?Usage: restore_postgres.sh <dump_file>}"
DATABASE_URL="${DATABASE_URL:-postgresql://keenpay:keenpay@localhost:5432/keenpay}"

pg_restore --clean --if-exists --dbname="$DATABASE_URL" "$DUMP_FILE"
echo "Restore complete from $DUMP_FILE"
