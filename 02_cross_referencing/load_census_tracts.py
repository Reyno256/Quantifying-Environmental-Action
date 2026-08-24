"""
Load census tract GEOIDs from synagogues_combined.csv into the database.

Run after add_census_tracts.py has geocoded the combined CSV:
    python load_census_tracts.py

Does two things:
  1. INSERT INTO census_tracts (geoid, state_fips, county_fips, tract_number, name)
  2. UPDATE synagogues SET census_tract_geoid = ... WHERE osm_id = ... OR place_id = ...
"""

import csv
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

COMBINED_CSV = Path(__file__).parent.parent / "04_combined" / "synagogues_combined.csv"


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    rows = list(csv.DictReader(open(COMBINED_CSV, encoding="utf-8")))
    print(f"Loaded {len(rows)} rows from {COMBINED_CSV}", flush=True)

    tracts = {}
    for r in rows:
        g = r.get("census_tract_geoid", "").strip()
        if len(g) == 11 and g not in tracts:
            tracts[g] = {
                "geoid":        g,
                "state_fips":   g[:2],
                "county_fips":  g[2:5],
                "tract_number": g[5:],
                "name":         r.get("census_tract_name", ""),
            }

    print(f"Unique census tracts found: {len(tracts):,}", flush=True)

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    # 1. Insert census_tracts
    if tracts:
        cols = ["geoid", "state_fips", "county_fips", "tract_number", "name"]
        psycopg2.extras.execute_batch(
            cur,
            'INSERT INTO census_tracts (geoid, state_fips, county_fips, tract_number, name) '
            'VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
            [(t["geoid"], t["state_fips"], t["county_fips"], t["tract_number"], t["name"])
             for t in tracts.values()],
            page_size=500,
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM census_tracts")
        print(f"census_tracts table now has {cur.fetchone()[0]:,} rows.", flush=True)

    # 2. Update synagogues.census_tract_geoid
    updated = 0
    for r in rows:
        g = r.get("census_tract_geoid", "").strip()
        if len(g) != 11:
            continue
        osm_id    = r.get("osm_id", "").strip()
        place_id  = r.get("place_id", "").strip()
        if osm_id:
            cur.execute(
                "UPDATE synagogues SET census_tract_geoid = %s WHERE osm_id = %s AND census_tract_geoid IS NULL",
                (g, int(osm_id)),
            )
        elif place_id:
            cur.execute(
                "UPDATE synagogues SET census_tract_geoid = %s WHERE place_id = %s AND census_tract_geoid IS NULL",
                (g, place_id),
            )
        updated += cur.rowcount

    conn.commit()
    print(f"Updated {updated:,} synagogue rows with census_tract_geoid.", flush=True)

    cur.execute("SELECT COUNT(*) FROM synagogues WHERE census_tract_geoid IS NOT NULL")
    print(f"Synagogues with a tract GEOID: {cur.fetchone()[0]:,}", flush=True)

    cur.close()
    conn.close()
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
