#!/usr/bin/env bash
# import_osm.sh — Run on the DB VM.
#
# Downloads us-latest.osm.pbf from Geofabrik, imports selected features via
# osm2pgsql flex output, then builds spatial indexes.
#
# Prerequisites on the VM:
#   sudo apt install -y osm2pgsql    # requires osm2pgsql >= 1.5
#
# Copy these two files to the VM alongside this script:
#   osm_flex.lua
#   migrate_add_osm_indexes.sql
#
# Usage:
#   DB_PASSWORD=<password> bash import_osm.sh
#
# Optional overrides (export before running or prefix the command):
#   DB_HOST, DB_PORT, DB_NAME, DB_USER   — default to Docker Compose values
#   PBF_DIR                              — directory to store the PBF (needs ~12 GB free)
#   OSM2PGSQL_CACHE                      — node cache MB (default 4096; raise if RAM allows)
#   OSM2PGSQL_PROCS                      — parallel processes (default 4)

set -euo pipefail

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-synagogues}"
DB_USER="${DB_USER:-research}"
DB_PASSWORD="${DB_PASSWORD:?Set DB_PASSWORD before running}"

PBF_DIR="${PBF_DIR:-$HOME}"
PBF_PATH="$PBF_DIR/us-latest.osm.pbf"

OSM2PGSQL_CACHE="${OSM2PGSQL_CACHE:-4096}"
OSM2PGSQL_PROCS="${OSM2PGSQL_PROCS:-4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LUA_STYLE="$SCRIPT_DIR/osm_flex.lua"
INDEX_SQL="$SCRIPT_DIR/migrate_add_osm_indexes.sql"

PSQL="psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME"

# ── 1. Download ────────────────────────────────────────────────────────────────
echo "==> [1/4] Downloading us-latest.osm.pbf (~11 GB) to $PBF_PATH ..."
wget -c --show-progress \
    -O "$PBF_PATH" \
    "https://download.geofabrik.de/north-america/us-latest.osm.pbf"

# ── 2. Create osm schema ───────────────────────────────────────────────────────
echo "==> [2/4] Creating osm schema ..."
PGPASSWORD="$DB_PASSWORD" $PSQL -c "CREATE SCHEMA IF NOT EXISTS osm;"

# ── 3. Import via osm2pgsql flex ───────────────────────────────────────────────
# --create       one-shot import (no incremental updates)
# --slim         stores node/way middle tables in PostgreSQL instead of RAM (required for large imports)
# --output=flex  Lua-driven table mapping (osm_flex.lua)
# --cache        RAM cache for frequently accessed nodes in slim mode (MB)
echo "==> [3/4] Running osm2pgsql (estimated 1–3 hours) ..."
osm2pgsql \
    --create \
    --slim \
    --output=flex \
    --style="$LUA_STYLE" \
    --cache="$OSM2PGSQL_CACHE" \
    --number-processes="$OSM2PGSQL_PROCS" \
    -d "host=${DB_HOST} port=${DB_PORT} dbname=${DB_NAME} user=${DB_USER} password=${DB_PASSWORD}" \
    "$PBF_PATH"

# ── 4. Build indexes ───────────────────────────────────────────────────────────
echo "==> [4/4] Building spatial and type indexes ..."
PGPASSWORD="$DB_PASSWORD" $PSQL -f "$INDEX_SQL"

echo ""
echo "==> Done. OSM data is in the 'osm' schema:"
PGPASSWORD="$DB_PASSWORD" $PSQL -c "
    SELECT table_name,
           pg_size_pretty(pg_total_relation_size('osm.' || table_name)) AS size
    FROM information_schema.tables
    WHERE table_schema = 'osm'
    ORDER BY table_name;
"
