"""
Add 'census_tract_geoid' and 'census_tract_name' columns to
synagogues_combined.csv using the US Census Bureau Geocoder API.

  census_tract_geoid  — 11-digit FIPS (state+county+tract), e.g. "06073010902"
  census_tract_name   — readable label, e.g. "Tract 109.02, San Diego County, CA"

Resumable: skips rows already populated.
"""

import csv
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ── config ────────────────────────────────────────────────────────────────────

CSV_PATH = Path(__file__).parent.parent / "04_combined" / "synagogues_combined.csv"
WORKERS  = 10
DELAY    = 0.1

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"

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

# ── geocoding ─────────────────────────────────────────────────────────────────

def lookup_tract(lat: str, lon: str) -> tuple[str, str]:
    """Return (geoid, readable_name) for the 2020 census tract, or ('','')."""
    try:
        r = SESSION.get(
            CENSUS_URL,
            params={
                "x": lon,
                "y": lat,
                "benchmark": "Public_AR_Current",
                "vintage":   "Current_Current",
                "layers":    "8",        # 2020 Census Tracts
                "format":    "json",
            },
            timeout=15,
        )
        r.raise_for_status()
        geographies = r.json().get("result", {}).get("geographies", {})
        tracts = geographies.get(
            "Census Tracts",
            geographies.get("2020 Census Tracts", []),
        )
        if tracts:
            t       = tracts[0]
            state   = t.get("STATE", "")
            county  = t.get("COUNTY", "")
            tract   = t.get("TRACT", "")
            geoid      = state + county + tract          # 11-digit GEOID
            # NAME field already formatted, e.g. "Census Tract 83.76"
            name       = t.get("NAME", f"Census Tract {tract}")
            state_abbr = FIPS_TO_ABBR.get(state, state)
            label      = f"{name}, {state_abbr}"
            return geoid, label
    except Exception:
        pass
    return "", ""


# ── worker ────────────────────────────────────────────────────────────────────

def process_row(args):
    idx, row = args
    lat, lon = row.get("lat", ""), row.get("lon", "")
    if not lat or not lon:
        return idx, "", ""
    geoid, label = lookup_tract(lat, lon)
    time.sleep(DELAY)
    return idx, geoid, label


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader    = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows      = list(reader)

    for col in ("census_tract_geoid", "census_tract_name"):
        if col not in fieldnames:
            fieldnames.append(col)
    for row in rows:
        row.setdefault("census_tract_geoid", "")
        row.setdefault("census_tract_name",  "")

    todo = [(i, row) for i, row in enumerate(rows)
            if not row["census_tract_geoid"]]

    print(f"Loaded {len(rows)} rows. {len(todo)} need tract lookup.", flush=True)

    found = done = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process_row, t): t for t in todo}
        for fut in as_completed(futures):
            idx, geoid, label = fut.result()
            rows[idx]["census_tract_geoid"] = geoid
            rows[idx]["census_tract_name"]  = label
            if geoid:
                found += 1
            done += 1
            if done % 100 == 0:
                pct = done / len(todo) * 100
                print(f"  {done}/{len(todo)} ({pct:.0f}%) — {found} tracts found",
                      flush=True)

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {found}/{len(rows)} census tracts found.")
    print(f"Updated: {CSV_PATH}")

    print("\nSample:")
    for r in [r for r in rows if r["census_tract_geoid"]][:8]:
        print(f"  {r['name'][:40]:40s}  {r['census_tract_geoid']}  {r['census_tract_name']}")


if __name__ == "__main__":
    main()
