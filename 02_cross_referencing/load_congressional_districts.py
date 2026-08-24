"""
Load congressional district data from synagogues_combined.csv into the database.

Run after add_congressional_districts.py has geocoded the combined CSV:
    python load_congressional_districts.py

Does two things:
  1. INSERT INTO congressional_districts (district_id, state_po, district_number, congress_session)
  2. UPDATE synagogues SET congressional_district = ... WHERE osm_id = ... OR place_id = ...
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

    districts = {}
    for r in rows:
        d = r.get("congressional_district", "").strip()
        if d and d not in districts:
            parts = d.rsplit("-", 1)
            districts[d] = {
                "district_id":     d,
                "state_po":        parts[0] if len(parts) == 2 else d,
                "district_number": int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0,
                "congress_session": 119,
            }

    print(f"Unique congressional districts found: {len(districts):,}", flush=True)

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    # 1. Insert congressional_districts
    if districts:
        psycopg2.extras.execute_batch(
            cur,
            'INSERT INTO congressional_districts (district_id, state_po, district_number, congress_session) '
            'VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
            [(d["district_id"], d["state_po"], d["district_number"], d["congress_session"])
             for d in districts.values()],
            page_size=500,
        )
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM congressional_districts")
        print(f"congressional_districts table now has {cur.fetchone()[0]:,} rows.", flush=True)

    # 2. Update synagogues.congressional_district
    updated = 0
    for r in rows:
        d = r.get("congressional_district", "").strip()
        if not d:
            continue
        osm_id   = r.get("osm_id", "").strip()
        place_id = r.get("place_id", "").strip()
        if osm_id:
            cur.execute(
                "UPDATE synagogues SET congressional_district = %s WHERE osm_id = %s AND congressional_district IS NULL",
                (d, int(osm_id)),
            )
        elif place_id:
            cur.execute(
                "UPDATE synagogues SET congressional_district = %s WHERE place_id = %s AND congressional_district IS NULL",
                (d, place_id),
            )
        updated += cur.rowcount

    conn.commit()
    print(f"Updated {updated:,} synagogue rows with congressional_district.", flush=True)

    cur.execute("SELECT COUNT(*) FROM synagogues WHERE congressional_district IS NOT NULL")
    print(f"Synagogues with a district: {cur.fetchone()[0]:,}", flush=True)

    cur.close()
    conn.close()
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
