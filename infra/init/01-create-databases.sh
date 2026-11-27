#!/usr/bin/env bash
set -Eeuo pipefail

IFS=',' read -r -a databases <<< "${POSTGRES_MULTIPLE_DATABASES:-}"
for database in "${databases[@]}"; do
  [[ "$database" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || {
    echo "Invalid PostgreSQL database name: $database" >&2
    exit 1
  }
  psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --command="CREATE DATABASE \"$database\""
done
