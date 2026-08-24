"""
Backfill synagogues.congressional_district for records that have coordinates
but no district.

The normal assignment (02_cross_referencing/load_congressional_districts.py)
matches a reference file by osm_id / place_id. The `America-import` records
(from America_Orig.csv) have neither, so they were never assigned a district
even though the geocoder later gave them lat/lon (and a census tract). The DB's
congressional_districts.boundary column is empty, so we can't do the join in
SQL; instead we do point-in-polygon in geopandas against the same 2024 119th
Congress TIGER shapefile used for the election-lean map layer, so the assigned
district_id (e.g. 'CO-2') matches the election data.

Only districts that already exist in congressional_districts (FK target) are
assigned; any point landing in a district not in that table is left NULL and
reported.

Usage:
    python 08_database/backfill_congressional_district_spatial.py          # dry run
    python 08_database/backfill_congressional_district_spatial.py --apply    # execute
"""

import argparse
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

CD_URL = ("/vsizip/vsicurl/https://www2.census.gov/geo/tiger/GENZ2024/shp/"
          "cb_2024_us_cd119_500k.zip")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = ap.parse_args()

    import geopandas as gpd
    from shapely.geometry import Point

    conn = get_conn()
    cur = conn.cursor()

    # candidate synagogues: no district, but have coordinates
    cur.execute("""
        SELECT id, name, state, lat, lon
        FROM synagogues
        WHERE congressional_district IS NULL AND lat IS NOT NULL AND lon IS NOT NULL
    """)
    rows = cur.fetchall()
    print(f"Candidates (CD null, have coords): {len(rows)}")
    if not rows:
        conn.close(); return

    pts = gpd.GeoDataFrame(
        {"id": [r[0] for r in rows], "name": [r[1] for r in rows],
         "state": [r[2] for r in rows]},
        geometry=[Point(r[4], r[3]) for r in rows], crs="EPSG:4326",
    )

    print("Reading 2024 cd119 district boundaries…", flush=True)
    cd = gpd.read_file(CD_URL, columns=["STATEFP", "CD119FP", "geometry"]).to_crs(4326)
    cd["district_id"] = cd.apply(
        lambda r: f"{FIPS_TO_ABBR.get(r['STATEFP'], r['STATEFP'])}-{int(r['CD119FP'])}",
        axis=1)

    joined = gpd.sjoin(pts, cd[["district_id", "geometry"]], how="left", predicate="within")
    joined = joined.drop_duplicates(subset="id")

    # only assign districts that exist in the FK target table
    cur.execute("SELECT district_id FROM congressional_districts")
    known = {r[0] for r in cur.fetchall()}

    assign, no_match, unknown_fk = [], [], []
    for _, r in joined.iterrows():
        d = r["district_id"]
        if d is None or (isinstance(d, float)):
            no_match.append((r["id"], r["name"], r["state"]))
        elif d not in known:
            unknown_fk.append((r["id"], r["name"], r["state"], d))
        else:
            assign.append((d, int(r["id"])))

    print(f"\nWould assign a district: {len(assign)}")
    print(f"Point fell in no polygon (left NULL): {len(no_match)}")
    print(f"District not in DB FK table (left NULL): {len(unknown_fk)}")
    for i, n, s, d in unknown_fk:
        print(f"    id={i} '{n}' {s} -> {d} (not in congressional_districts)")

    from collections import Counter
    print("\nAssignment distribution (top):")
    for d, n in Counter(d for d, _ in assign).most_common(12):
        print(f"    {n:3d}  {d}")

    if not args.apply:
        print("\nDRY RUN — no changes made. Re-run with --apply to execute.")
        conn.close(); return

    cur.executemany(
        "UPDATE synagogues SET congressional_district = %s WHERE id = %s", assign)
    print(f"\nUpdated {cur.rowcount} synagogues.")
    conn.commit()

    cur.execute("SELECT count(*) FROM synagogues WHERE congressional_district IS NULL")
    print(f"synagogues with NULL congressional_district now: {cur.fetchone()[0]}")
    cur.close(); conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
