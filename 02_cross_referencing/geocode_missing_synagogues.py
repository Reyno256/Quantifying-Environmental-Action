"""
Geocode synagogues in the DB that are missing geography, and assign their
2020 census tract — directly against the Postgres DB (not the stale CSVs).

Two phases, both resumable and idempotent (only touch rows still missing data):

  Phase A — coords -> tract
      For synagogues that HAVE coordinates but no census_tract_geoid, look up
      the tract from lat/lon via the Census 'geographies/coordinates' endpoint.

  Phase B — address -> coords + tract
      For synagogues with an address but NO coordinates, geocode the address.
      Census 'geographies/onelineaddress' is tried first (returns matched
      coords AND tract in one call). If it misses and --google is set, fall
      back to the Google Geocoding API (PLACES_API_KEY), accepting only precise
      results (ROOFTOP / RANGE_INTERPOLATED / GEOMETRIC_CENTER) and then reverse
      -looking-up the tract from those coords via the Census endpoint. Coords
      from Google, tract always from Census.

Addresses in the America-import rows are loose free text and often omit the
city/state, so each query is augmented with hints parsed from the synagogue
name (trailing token = state; text after the last ' of ' = city).

For every resolved row we write: lat, lon, location (Point, SRID 4326),
census_tract_geoid, and state (from the authoritative tract FIPS). Any tract
not already in census_tracts is inserted as a stub row (boundary left NULL) to
satisfy the synagogues.census_tract_geoid FK.

After running, backfill demographics for the newly inserted tracts with:
    python add_census_demographics.py        # resumable; picks up new tracts

Usage:
    python geocode_missing_synagogues.py [--google] [--no-coords-to-tract]
                                         [--no-address] [--workers N]

Prerequisites:
    pip install psycopg2-binary python-dotenv requests
    A reachable PostGIS-enabled DB (ST_SetSRID/ST_MakePoint) with the schema
    used by this project; DB creds + PLACES_API_KEY in the project .env.
"""

import argparse
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import psycopg2.extras
import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

# ── endpoints ───────────────────────────────────────────────────────────────
CENSUS_COORDS = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
CENSUS_ADDR = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

RETRIES = 3
GOOGLE_ACCEPT = {"ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}
PLACEHOLDER = re.compile(r"not\s*listed|defined|personal request|without walls|doesn", re.I)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "SynagogueResearch/1.0 (quinn.reynolds@mail.utoronto.ca)"

STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH",
    "NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT",
    "VT","VA","WA","WV","WI","WY","PR","GU","VI","AS","MP",
}
FIPS_TO_ABBR = {
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT","10":"DE",
    "11":"DC","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL","18":"IN","19":"IA",
    "20":"KS","21":"KY","22":"LA","23":"ME","24":"MD","25":"MA","26":"MI","27":"MN",
    "28":"MS","29":"MO","30":"MT","31":"NE","32":"NV","33":"NH","34":"NJ","35":"NM",
    "36":"NY","37":"NC","38":"ND","39":"OH","40":"OK","41":"OR","42":"PA","44":"RI",
    "45":"SC","46":"SD","47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA",
    "54":"WV","55":"WI","56":"WY","60":"AS","66":"GU","69":"MP","72":"PR","78":"VI",
}


# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── name parsing (city/state hints) ─────────────────────────────────────────
def parse_name(name):
    """Return (city, state) parsed from a synagogue name, e.g.
    'Chabad of Fountain Hills AZ' -> ('Fountain Hills', 'AZ')."""
    toks = name.strip().split()
    state = toks[-1].upper() if toks and toks[-1].upper() in STATES else None
    body = name[: name.rfind(toks[-1])] if state else name
    m = re.search(r"\bof\s+(.+)$", body.strip(), re.IGNORECASE)
    city = m.group(1).strip().rstrip(",").strip() if m else None
    return city, state


# ── Census tract from coordinates ────────────────────────────────────────────
def census_tract_from_coords(lat, lon):
    """Return the 2020 census tract dict for a point, or None (e.g. non-US)."""
    for attempt in range(RETRIES):
        try:
            r = SESSION.get(CENSUS_COORDS, params={
                "x": lon, "y": lat, "benchmark": "Public_AR_Current",
                "vintage": "Current_Current", "layers": "8", "format": "json"},
                timeout=20)
            r.raise_for_status()
            geos = r.json().get("result", {}).get("geographies", {})
            tracts = geos.get("Census Tracts", geos.get("2020 Census Tracts", []))
            if tracts:
                return _tract_dict(tracts[0])
            return None
        except Exception:
            if attempt < RETRIES - 1:
                time.sleep(0.4 * (attempt + 1))
    return None


# ── Census address geocoder (coords + tract in one call) ─────────────────────
def census_geocode_address(query):
    for attempt in range(RETRIES):
        try:
            r = SESSION.get(CENSUS_ADDR, params={
                "address": query, "benchmark": "Public_AR_Current",
                "vintage": "Current_Current", "layers": "8", "format": "json"},
                timeout=25)
            r.raise_for_status()
            matches = r.json().get("result", {}).get("addressMatches", [])
            if matches:
                m = matches[0]
                c = m.get("coordinates", {})
                geos = m.get("geographies", {})
                tracts = geos.get("Census Tracts", geos.get("2020 Census Tracts", []))
                if c.get("x") is not None and c.get("y") is not None and tracts:
                    out = _tract_dict(tracts[0])
                    if out:
                        out.update({"lat": float(c["y"]), "lon": float(c["x"])})
                        return out
            return None
        except Exception:
            if attempt < RETRIES - 1:
                time.sleep(0.4 * (attempt + 1))
    return None


# ── Google geocoder fallback ─────────────────────────────────────────────────
def google_geocode(query, api_key):
    for attempt in range(RETRIES):
        try:
            r = SESSION.get(GOOGLE_URL, params={"address": query, "key": api_key}, timeout=20)
            r.raise_for_status()
            j = r.json()
            if j.get("status") == "OK" and j.get("results"):
                res = j["results"][0]
                loc = res["geometry"]["location"]
                ltype = res["geometry"].get("location_type", "")
                in_us = any("country" in c.get("types", []) and c.get("short_name") == "US"
                            for c in res.get("address_components", []))
                return loc["lat"], loc["lng"], ltype, in_us
            if j.get("status") in ("ZERO_RESULTS", "OK"):
                return None
        except Exception:
            pass
        time.sleep(0.4 * (attempt + 1))
    return None


def _tract_dict(t):
    geoid = t.get("STATE", "") + t.get("COUNTY", "") + t.get("TRACT", "")
    if len(geoid) != 11:
        return None
    return {"geoid": geoid, "state": t.get("STATE", ""), "county": t.get("COUNTY", ""),
            "tract": t.get("TRACT", ""), "tname": t.get("NAME", "")}


# ── per-row workers ──────────────────────────────────────────────────────────
def resolve_coords_to_tract(row):
    sid, lat, lon = row
    return sid, census_tract_from_coords(lat, lon)


def resolve_address(row, use_google, api_key):
    """Census onelineaddress first, then optional Google fallback."""
    sid, name, address = row
    city, state = parse_name(name)
    addr = "" if PLACEHOLDER.search(address) else address.strip().rstrip(",").strip()

    # Census: try strongest forms first
    candidates = []
    if addr and city and state:
        candidates.append(f"{addr}, {city}, {state}")
    if addr and state:
        candidates.append(f"{addr}, {state}")
    if addr:
        candidates.append(addr)
    seen = set()
    for q in candidates:
        if q in seen:
            continue
        seen.add(q)
        res = census_geocode_address(q)
        if res:
            res["via"] = "census-address"
            return sid, res
        time.sleep(0.03)

    if not use_google or not api_key:
        return sid, None

    # Google fallback (precise results only), tract reverse-looked-up via Census
    gq = ", ".join(p for p in (addr, city, state, "USA") if p)
    g = google_geocode(gq, api_key)
    if not g:
        return sid, None
    lat, lon, ltype, in_us = g
    if ltype not in GOOGLE_ACCEPT or not in_us:
        return sid, None
    tr = census_tract_from_coords(lat, lon)
    if not tr:
        return sid, None
    tr.update({"lat": lat, "lon": lon, "via": f"google:{ltype}"})
    return sid, tr


# ── tract-stub + synagogue writes ────────────────────────────────────────────
def insert_new_tracts(cur, results, existing):
    new = {r["geoid"]: r for r in results.values() if r["geoid"] not in existing}
    if new:
        psycopg2.extras.execute_batch(cur,
            "INSERT INTO census_tracts (geoid, state_fips, county_fips, tract_number, name) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (geoid) DO NOTHING",
            [(t["geoid"], t["state"], t["county"], t["tract"], t["tname"]) for t in new.values()])
    return len(new)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--google", action="store_true",
                    help="Use Google Geocoding API fallback for addresses Census can't match")
    ap.add_argument("--no-coords-to-tract", action="store_true",
                    help="Skip Phase A (assigning tracts to rows that already have coords)")
    ap.add_argument("--no-address", action="store_true",
                    help="Skip Phase B (geocoding addressed rows that lack coords)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    api_key = os.getenv("PLACES_API_KEY")
    if args.google and not api_key:
        print("WARNING: --google set but PLACES_API_KEY missing; fallback disabled.", flush=True)

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT geoid FROM census_tracts")
    existing = {r[0] for r in cur.fetchall()}

    # ── Phase A: coords -> tract ──────────────────────────────────────────────
    if not args.no_coords_to_tract:
        cur.execute("SELECT id, lat, lon FROM synagogues "
                    "WHERE location IS NOT NULL AND census_tract_geoid IS NULL")
        todo = cur.fetchall()
        print(f"[Phase A] coords-but-no-tract: {len(todo)}", flush=True)
        results = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for fut in as_completed([pool.submit(resolve_coords_to_tract, r) for r in todo]):
                sid, res = fut.result()
                if res:
                    results[sid] = res
        n_new = insert_new_tracts(cur, results, existing)
        existing |= {r["geoid"] for r in results.values()}
        psycopg2.extras.execute_batch(cur,
            "UPDATE synagogues SET census_tract_geoid=%s, "
            "state=COALESCE(state,%s) WHERE id=%s",
            [(r["geoid"], FIPS_TO_ABBR.get(r["state"], r["state"]), sid)
             for sid, r in results.items()])
        conn.commit()
        print(f"[Phase A] assigned {len(results)} tracts ({n_new} new census_tracts); "
              f"{len(todo)-len(results)} unresolved (non-US/offshore).", flush=True)

    # ── Phase B: address -> coords + tract ────────────────────────────────────
    if not args.no_address:
        cur.execute("SELECT id, name, address FROM synagogues "
                    "WHERE location IS NULL AND address IS NOT NULL ORDER BY id")
        todo = cur.fetchall()
        print(f"[Phase B] address-but-no-coords: {len(todo)}", flush=True)
        results = {}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = [pool.submit(resolve_address, r, args.google, api_key) for r in todo]
            for fut in as_completed(futs):
                sid, res = fut.result()
                if res:
                    results[sid] = res
        n_new = insert_new_tracts(cur, results, existing)
        existing |= {r["geoid"] for r in results.values()}
        psycopg2.extras.execute_batch(cur,
            "UPDATE synagogues SET lat=%s, lon=%s, "
            "location=ST_SetSRID(ST_MakePoint(%s,%s),4326), "
            "census_tract_geoid=%s, state=%s WHERE id=%s",
            [(r["lat"], r["lon"], r["lon"], r["lat"], r["geoid"],
              FIPS_TO_ABBR.get(r["state"], r["state"]), sid)
             for sid, r in results.items()])
        conn.commit()
        by_src = {}
        for r in results.values():
            key = r["via"].split(":")[0]
            by_src[key] = by_src.get(key, 0) + 1
        print(f"[Phase B] geocoded {len(results)}/{len(todo)} "
              f"({dict(by_src)}); {n_new} new census_tracts.", flush=True)
        print("          remaining rows have no precise address (placeholders, "
              "PO boxes, city-only) and need manual lookup.", flush=True)

    cur.close()
    conn.close()
    print("\nDone. Run `python add_census_demographics.py` to backfill demographics "
          "for any newly inserted tracts.", flush=True)


if __name__ == "__main__":
    main()
