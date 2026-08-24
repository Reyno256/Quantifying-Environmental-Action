"""
One-time migration: add PostGIS extension and spatial columns to an existing database.

Run this on a live database before running load_boundaries.py. Safe to run multiple
times (all statements are idempotent).

Usage:
    python migrate_add_postgis.py

Connection config from ../.env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def run():
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    print("Enabling PostGIS extension…", flush=True)
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    print("Adding boundary column to congressional_districts…", flush=True)
    cur.execute("""
        ALTER TABLE congressional_districts
        ADD COLUMN IF NOT EXISTS boundary GEOMETRY(MultiPolygon, 4326)
    """)

    print("Adding boundary column to census_tracts…", flush=True)
    cur.execute("""
        ALTER TABLE census_tracts
        ADD COLUMN IF NOT EXISTS boundary GEOMETRY(MultiPolygon, 4326)
    """)

    print("Adding location column to synagogues…", flush=True)
    cur.execute("""
        ALTER TABLE synagogues
        ADD COLUMN IF NOT EXISTS location GEOMETRY(Point, 4326)
    """)

    print("Populating synagogues.location from lat/lon…", flush=True)
    cur.execute("""
        UPDATE synagogues
        SET location = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
        WHERE location IS NULL AND lon IS NOT NULL AND lat IS NOT NULL
    """)
    print(f"  {cur.rowcount} rows updated.", flush=True)

    print("Creating GiST spatial indexes…", flush=True)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS synagogues_location_idx
        ON synagogues USING GIST(location)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS districts_boundary_idx
        ON congressional_districts USING GIST(boundary)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS tracts_boundary_idx
        ON census_tracts USING GIST(boundary)
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete.", flush=True)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
