"""
Add a 'congressional_district' column to synagogues_combined.csv using the
US Census Bureau Geocoder API (free, no API key required).

Each synagogue's lat/lon is looked up against the 119th Congressional Districts
layer. The result is formatted as e.g. "CA-50".

Writes the result back to synagogues_combined.csv in place.
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
DELAY    = 0.1   # seconds between requests per worker

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "SynagogueResearch/1.0 (quinn.reynolds@mail.utoronto.ca)"

_print_lock = threading.Lock()


# ── geocoding ─────────────────────────────────────────────────────────────────

def lookup_district(lat: str, lon: str) -> str:
    """Return 'STATE-DISTRICT' (e.g. 'CA-50') or '' on failure."""
    try:
        r = SESSION.get(
            CENSUS_URL,
            params={
                "x": lon,
                "y": lat,
                "benchmark": "Public_AR_Current",
                "vintage":   "Current_Current",
                "layers":    "54",          # 119th Congressional Districts
                "format":    "json",
            },
            timeout=15,
        )
        r.raise_for_status()
        geographies = r.json().get("result", {}).get("geographies", {})
        cds = geographies.get(
            "119th Congressional Districts",
            geographies.get("Congressional Districts", []),
        )
        if cds:
            cd     = cds[0]
            state  = cd.get("STATE", "")
            number = cd.get("CD119") or cd.get("BASENAME", "")
            # Map state FIPS → abbreviation
            state_abbr = FIPS_TO_ABBR.get(state, state)
            district   = str(int(number)) if number.isdigit() else number
            return f"{state_abbr}-{district}" if state_abbr else district
    except Exception:
        pass
    return ""


# ── FIPS → state abbreviation ─────────────────────────────────────────────────

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


# ── worker ────────────────────────────────────────────────────────────────────

def process_row(args):
    idx, total, row = args
    lat, lon = row.get("lat", ""), row.get("lon", "")
    if not lat or not lon:
        return idx, ""
    district = lookup_district(lat, lon)
    time.sleep(DELAY)
    return idx, district


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "congressional_district" not in fieldnames:
        fieldnames.append("congressional_district")
    for row in rows:
        row.setdefault("congressional_district", "")

    # Skip rows already filled (resume support)
    todo = [(i, len(rows), row) for i, row in enumerate(rows)
            if not row["congressional_district"]]

    print(f"Loaded {len(rows)} rows. {len(todo)} need district lookup.", flush=True)

    found = 0
    done  = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process_row, t): t for t in todo}
        for fut in as_completed(futures):
            idx, district = fut.result()
            rows[idx]["congressional_district"] = district
            if district:
                found += 1
            done += 1
            if done % 100 == 0:
                pct = done / len(todo) * 100
                print(f"  {done}/{len(todo)} ({pct:.0f}%) — {found} districts found",
                      flush=True)

    # Write back
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {found}/{len(rows)} congressional districts found.")
    print(f"Updated: {CSV_PATH}")

    # Sample
    sample = [r for r in rows if r["congressional_district"]][:8]
    print("\nSample:")
    for r in sample:
        print(f"  {r['name'][:45]:45s} → {r['congressional_district']}")


if __name__ == "__main__":
    main()
