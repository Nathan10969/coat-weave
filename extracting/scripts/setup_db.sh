#!/usr/bin/env bash
# Apply schema + seeds against the postgres started by docker compose.
# Reads connection params from .env (or the defaults below).
set -euo pipefail

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-coating_kg}"
DB_USER="${DB_USER:-postgres}"
export PGPASSWORD="${DB_PASSWORD:-postgres}"

PSQL_OPTS=(-h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
           --quiet --variable ON_ERROR_STOP=1)

# Wait for postgres to be ready (docker compose healthcheck handles this too,
# but let's not race the first run).
for i in {1..30}; do
  if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; then
    break
  fi
  echo "waiting for postgres ($i/30)..."
  sleep 1
done

echo "==> applying schema.sql"
psql "${PSQL_OPTS[@]}" -f db/schema.sql

echo "==> seeding property_directionality"
psql "${PSQL_OPTS[@]}" -f db/seed_property_directionality.sql

echo "==> seeding canonical starter nodes"
psql "${PSQL_OPTS[@]}" -f db/seed_canonical_starter.sql

echo "==> seeding forbidden_merge"
psql "${PSQL_OPTS[@]}" -f db/seed_forbidden_merge_starter.sql

echo "==> seeding must_merge"
psql "${PSQL_OPTS[@]}" -f db/seed_must_merge_starter.sql

echo "OK — schema + seeds applied."
