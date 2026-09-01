#!/usr/bin/env bash
# Daily Postgres backup — run via cron or CI scheduled job
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATABASE_URL="${DATABASE_URL:-postgresql://keenpay:keenpay@localhost:5432/keenpay}"

mkdir -p "$BACKUP_DIR"
pg_dump "$DATABASE_URL" --format=custom --file="$BACKUP_DIR/keenpay_${TIMESTAMP}.dump"
echo "Backup saved: $BACKUP_DIR/keenpay_${TIMESTAMP}.dump"

# Retain 7 days
find "$BACKUP_DIR" -name "keenpay_*.dump" -mtime +7 -delete
