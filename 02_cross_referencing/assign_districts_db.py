"""
Assign congressional_district to synagogues that have coordinates but no
district, using the free US Census Bureau Geocoder (coordinates -> 119th
Congressional Districts).  Writes 'STATE-DISTRICT' (e.g. 'CA-50') back to the
synagogues table.

The DB's congressional_districts.boundary column is empty, so a PostGIS spatial
join is not possible; this mirrors 02_cross_referencing/add_congressional_districts.py
(which geocodes a CSV) but reads from and writes to the database directly.

Dry run (default): look up districts, report counts, no DB write.
--commit            : UPDATE synagogues.congressional_district.

Only rows with non-null location and NULL congressional_district are processed,
so the operation is additive and idempotent.

Usage:
    python 02_cross_referencing/assign_districts_db.py
    python 02_cross_referencing/assign_districts_db.py --commit
"""

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
WORKERS = 10

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "SynagogueResearch/1.0 (quinn.reynolds@mail.utoronto.ca)"

FIPS_TO_ABBR = {
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT",
    "10":"DE","11":"DC","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL",
    "18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD",
    "25":"MA","26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE",
    "32":"NV","33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND",
    "39":"OH","40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD",
    "47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV",
    "55":"WI","56":"WY","60":"AS","66":"GU","69":"MP","72":"PR","78":"VI",
}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def lookup_district(lat: float, lon: float) -> str:
    """Return 'STATE-DISTRICT' (e.g. 'CA-50') or '' on failure."""
    try:
        r = SESSION.get(CENSUS_URL, params={
            "x": lon, "y": lat,
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "layers": "54",          # 119th Congressional Districts
            "format": "json",
        }, timeout=20)
        r.raise_for_status()
        geos = r.json().get("result", {}).get("geographies", {})
        cds = geos.get("119th Congressional Districts",
                       geos.get("Congressional Districts", []))
        if cds:
            cd = cds[0]
            state_abbr = FIPS_TO_ABBR.get(cd.get("STATE", ""), cd.get("STATE", ""))
            number = cd.get("CD119") or cd.get("BASENAME", "")
            district = str(int(number)) if str(number).isdigit() else number
            return f"{state_abbr}-{district}" if state_abbr else district
    except Exception:
        pass
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, lat, lon FROM synagogues
        WHERE congressional_district IS NULL AND location IS NOT NULL
        ORDER BY id
    """)
    rows = cur.fetchall()
    print(f"Rows needing district (have coords, null district): {len(rows)}")

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(lookup_district, lat, lon): sid for sid, lat, lon in rows}
        done = 0
        for fut in as_completed(futs):
            sid = futs[fut]
            results[sid] = fut.result()
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(rows)}…", flush=True)

    matched = {sid: d for sid, d in results.items() if d}
    print(f"\nResolved: {len(matched)} / {len(rows)} "
          f"({100*len(matched)/max(len(rows),1):.1f}%)")

    if not args.commit:
        print("\nDry run — no DB changes. Re-run with --commit to write.")
        cur.close(); conn.close()
        return

    n = 0
    for sid, dist in matched.items():
        cur.execute("""
            UPDATE synagogues SET congressional_district = %s
            WHERE id = %s AND congressional_district IS NULL
        """, (dist, sid))
        n += cur.rowcount
    conn.commit()
    print(f"Updated {n} rows.")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
