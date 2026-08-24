"""
Load TIGER/Line boundary shapefiles into congressional_districts.boundary
and census_tracts.boundary.

Prerequisites:
    pip install pyshp shapely

Download TIGER/Line shapefiles from the US Census Bureau:
    Congressional districts (118th Congress, ~25 MB):
        https://www2.census.gov/geo/tiger/TIGER2023/CD/tl_2023_us_cd118.zip

    Census tracts (per state, ~5-30 MB each):
        https://www2.census.gov/geo/tiger/TIGER2023/TRACT/tl_2023_{statefips}_tract.zip
    e.g. tl_2023_06_tract.zip for California (FIPS 06)
    Only download states where your synagogues are located.

Usage:
    python load_boundaries.py --districts tl_2023_us_cd118.zip
    python load_boundaries.py --tracts /path/to/tract_zips/
    python load_boundaries.py --districts tl_2023_us_cd118.zip --tracts /path/to/tract_zips/

Connection config from ../.env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

import argparse
import io
import os
import sys
import zipfile
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

try:
    import shapefile
except ImportError:
    print("Missing dependency: pip install pyshp", file=sys.stderr)
    sys.exit(1)

try:
    from shapely.geometry import shape, MultiPolygon, Polygon
    from shapely.wkt import dumps as wkt_dumps
except ImportError:
    print("Missing dependency: pip install shapely", file=sys.stderr)
    sys.exit(1)

load_dotenv(Path(__file__).parent.parent / ".env")

# FIPS state code → USPS postal abbreviation
FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "72": "PR", "78": "VI",
}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def to_multipolygon(geom):
    """Ensure a shapely geometry is a MultiPolygon (wrap Polygon if needed)."""
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, MultiPolygon):
        return geom
    raise ValueError(f"Unexpected geometry type: {type(geom)}")


def open_shapefile(path: Path):
    """Return a shapefile.Reader from a .zip or .shp path."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            shp_name = next(n for n in zf.namelist() if n.endswith(".shp"))
            dbf_name = shp_name.replace(".shp", ".dbf")
            shx_name = shp_name.replace(".shp", ".shx")
            return shapefile.Reader(
                shp=io.BytesIO(zf.read(shp_name)),
                dbf=io.BytesIO(zf.read(dbf_name)),
                shx=io.BytesIO(zf.read(shx_name)),
            )
    return shapefile.Reader(str(path))


def load_districts(conn, shp_path: Path):
    """Load congressional district boundaries from tl_2023_us_cd118.zip."""
    print(f"Loading congressional district boundaries from {shp_path}…", flush=True)
    cur = conn.cursor()

    # Fetch known district_ids from DB so we skip non-matching records
    cur.execute("SELECT district_id FROM congressional_districts")
    known = {row[0] for row in cur.fetchall()}
    print(f"  {len(known)} districts in DB.", flush=True)

    sf = open_shapefile(shp_path)
    fields = [f[0] for f in sf.fields[1:]]  # skip DeletionFlag
    statefp_idx = fields.index("STATEFP")
    cd_idx = fields.index("CD118FP")

    updates = []
    skipped = 0
    for rec in sf.iterShapeRecords():
        statefp = rec.record[statefp_idx]
        cd118fp = rec.record[cd_idx]
        state_po = FIPS_TO_STATE.get(statefp)
        if not state_po:
            skipped += 1
            continue

        # at-large districts are coded '00' → district_id 'XX-0'
        district_id = f"{state_po}-{int(cd118fp)}"
        if district_id not in known:
            skipped += 1
            continue

        geom = to_multipolygon(shape(rec.shape.__geo_interface__))
        updates.append((wkt_dumps(geom), district_id))

    print(f"  {len(updates)} boundaries matched, {skipped} skipped.", flush=True)
    psycopg2.extras.execute_batch(
        cur,
        "UPDATE congressional_districts SET boundary = ST_GeomFromText(%s, 4326) WHERE district_id = %s",
        updates,
        page_size=100,
    )
    conn.commit()
    cur.close()
    print(f"  Done. {cur.rowcount if hasattr(cur, 'rowcount') else len(updates)} rows updated.", flush=True)


def load_tracts(conn, tracts_path: Path):
    """
    Load census tract boundaries from a directory of per-state TIGER/Line zips
    (tl_2023_??_tract.zip) or a single national shapefile/zip.

    Only GEOIDs already present in census_tracts are updated (filtered load).
    """
    print(f"Loading census tract boundaries from {tracts_path}…", flush=True)
    cur = conn.cursor()

    cur.execute("SELECT geoid FROM census_tracts")
    known_geoids = {row[0] for row in cur.fetchall()}
    print(f"  {len(known_geoids)} census tracts in DB to match against.", flush=True)

    tracts_path = Path(tracts_path)
    if tracts_path.is_dir():
        shp_files = sorted(tracts_path.glob("*.zip")) + sorted(tracts_path.glob("*.shp"))
    else:
        shp_files = [tracts_path]

    total_updates = 0
    total_skipped = 0

    for shp_file in shp_files:
        sf = open_shapefile(shp_file)
        fields = [f[0] for f in sf.fields[1:]]
        try:
            geoid_idx = fields.index("GEOID")
        except ValueError:
            print(f"  Skipping {shp_file.name}: no GEOID field.", flush=True)
            continue

        updates = []
        for rec in sf.iterShapeRecords():
            geoid = rec.record[geoid_idx]
            if geoid not in known_geoids:
                total_skipped += 1
                continue
            geom = to_multipolygon(shape(rec.shape.__geo_interface__))
            updates.append((wkt_dumps(geom), geoid))

        if updates:
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE census_tracts SET boundary = ST_GeomFromText(%s, 4326) WHERE geoid = %s",
                updates,
                page_size=200,
            )
            conn.commit()
            total_updates += len(updates)
            print(f"  {shp_file.name}: {len(updates)} matched.", flush=True)
        else:
            print(f"  {shp_file.name}: 0 matched.", flush=True)

    cur.close()
    print(f"Done. {total_updates} tracts loaded, {total_skipped} skipped.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Load TIGER/Line boundaries into PostGIS columns.")
    parser.add_argument("--districts", metavar="FILE",
                        help="Path to tl_2023_us_cd118.zip (or .shp)")
    parser.add_argument("--tracts", metavar="PATH",
                        help="Path to a directory of tl_2023_??_tract.zip files, or a single zip/shp")
    args = parser.parse_args()

    if not args.districts and not args.tracts:
        parser.print_help()
        sys.exit(1)

    conn = get_conn()
    try:
        if args.districts:
            load_districts(conn, Path(args.districts))
        if args.tracts:
            load_tracts(conn, Path(args.tracts))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
