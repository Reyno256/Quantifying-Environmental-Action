"""
Forward-geocode synagogues that are missing coordinates.

The `America-import` synagogues carry a street address / city / state but no
lat/lon (and therefore no PostGIS `location`, congressional district, or census
tract).  This script forward-geocodes them with the free US Census Bureau batch
geocoder (no API key required) and, with --commit, writes lat / lon / location
back into the `synagogues` table.

Dry run (default): geocode and report the match rate, write a results CSV, but
do NOT touch the database.
--commit            : additionally UPDATE synagogues.lat, lon, location.

Only rows with a non-null street address and currently NULL lat are processed,
so the operation is additive and idempotent.

Outputs:
  geocode_missing_results.csv   — id, name, status, lat, lon, matched_address

Usage:
    python 02_cross_referencing/geocode_missing.py
    python 02_cross_referencing/geocode_missing.py --commit
"""

import argparse
import csv
import io
import os
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK = "Public_AR_Current"
CHUNK = 5000   # Census allows up to 10,000 addresses per batch

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "SynagogueResearch/1.0 (quinn.reynolds@mail.utoronto.ca)"


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_missing(cur):
    cur.execute("""
        SELECT id, name, address, city, state
        FROM synagogues
        WHERE lat IS NULL AND address IS NOT NULL AND address <> ''
        ORDER BY id
    """)
    return cur.fetchall()


def clean(s):
    # Census batch CSV is comma-delimited; strip embedded commas from fields.
    return s.replace(",", " ").strip() if s else ""


def geocode_batch(rows):
    """rows: list of (id, name, address, city, state). Returns {id: (lat, lon, matched)}."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for sid, _name, addr, city, state in rows:
        # columns: Unique ID, Street, City, State, ZIP
        w.writerow([sid, clean(addr), clean(city), clean(state), ""])
    payload = buf.getvalue().encode("utf-8")

    resp = SESSION.post(
        BATCH_URL,
        data={"benchmark": BENCHMARK},
        files={"addressFile": ("addrs.csv", payload, "text/csv")},
        timeout=300,
    )
    resp.raise_for_status()

    out = {}
    reader = csv.reader(io.StringIO(resp.text))
    for rec in reader:
        # id, input, match, matchtype, matched_addr, "lon,lat", tigerline, side
        if len(rec) < 6:
            continue
        sid = int(rec[0])
        if rec[2] == "Match":
            matched_addr = rec[4]
            lon, lat = rec[5].split(",")
            out[sid] = (float(lat), float(lon), matched_addr)
        else:
            out[sid] = None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="Write lat/lon/location back to the database.")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    rows = load_missing(cur)
    print(f"Rows to geocode (missing coords, has address): {len(rows)}")

    results = {}
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        print(f"  geocoding batch {i//CHUNK + 1} ({len(chunk)} addresses)…", flush=True)
        results.update(geocode_batch(chunk))

    matched = {sid: v for sid, v in results.items() if v}
    print(f"\nMatched: {len(matched)} / {len(rows)} "
          f"({100*len(matched)/max(len(rows),1):.1f}%)")

    # Write results CSV
    by_id = {r[0]: r for r in rows}
    out_csv = HERE / "geocode_missing_results.csv"
    with open(out_csv, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "name", "status", "lat", "lon", "matched_address"])
        for sid, r in by_id.items():
            v = results.get(sid)
            if v:
                wr.writerow([sid, r[1], "Match", v[0], v[1], v[2]])
            else:
                wr.writerow([sid, r[1], "No_Match", "", "", ""])
    print(f"Wrote {out_csv}")

    if not args.commit:
        print("\nDry run — no database changes. Re-run with --commit to write "
              "lat/lon/location.")
        cur.close(); conn.close()
        return

    # ── write back ─────────────────────────────────────────────────────────────
    print(f"\nWriting {len(matched)} coordinates to synagogues…", flush=True)
    n = 0
    for sid, (lat, lon, _addr) in matched.items():
        cur.execute("""
            UPDATE synagogues
            SET lat = %s, lon = %s,
                location = ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            WHERE id = %s AND lat IS NULL
        """, (lat, lon, lon, lat, sid))
        n += cur.rowcount
    conn.commit()
    print(f"Updated {n} rows.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
