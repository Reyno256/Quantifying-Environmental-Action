"""
Populate census_tract_demographics from two sources:

  1. ACS 5-Year 2023 (Census API) — income + language composition
  2. USDA ERS RUCA 2020 (Excel download) — urbanicity codes

Usage:
    python add_census_demographics.py

Prerequisites:
    pip install psycopg2-binary python-dotenv requests openpyxl

The script is resumable: tracts already in census_tract_demographics are skipped
unless you pass --refresh to overwrite everything.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

# ── Census API ────────────────────────────────────────────────────────────────

ACS_BASE = "https://api.census.gov/data/2023/acs/acs5"

# Income
INCOME_VARS = ["B19013_001E"]

# Language spoken at home (C16001) — all 26 variables
LANG_VARS = [f"C16001_{i:03d}E" for i in range(1, 27)]

CENSUS_SESSION = requests.Session()
CENSUS_SESSION.headers["User-Agent"] = (
    "SynagogueResearch/1.0 (quinn.reynolds@mail.utoronto.ca)"
)

# ── USDA RUCA ─────────────────────────────────────────────────────────────────

# 2020 RUCA codes (census tract version) from USDA ERS
RUCA_URL = (
    "https://www.ers.usda.gov/media/5443/"
    "2020-rural-urban-commuting-area-codes-census-tracts.csv?v=51941"
)
RUCA_CACHE = Path(__file__).parent / "ruca2020.csv"

# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )

# ── helpers ───────────────────────────────────────────────────────────────────

def safe_int(val) -> int | None:
    """Convert Census API value to int, treating sentinel -666666666 as NULL."""
    try:
        n = int(val)
        return None if n < -1_000_000 else n
    except (TypeError, ValueError):
        return None

def sum_lang(*vals) -> int | None:
    """Sum a group of language sub-variables; None if all are None."""
    parts = [safe_int(v) for v in vals]
    valid = [p for p in parts if p is not None]
    return sum(valid) if valid else None

# ── ACS fetch ─────────────────────────────────────────────────────────────────

def fetch_acs_for_state(state_fips: str, variables: list[str]) -> list[dict]:
    """Return list of {var: value, …, 'state': xx, 'county': yyy, 'tract': tttttt}."""
    get_str = ",".join(variables)
    params = {
        "get": get_str,
        "for": "tract:*",
        "in": f"state:{state_fips} county:*",
    }
    api_key = os.getenv("CENSUS_API_KEY")
    if api_key:
        params["key"] = api_key

    try:
        r = CENSUS_SESSION.get(ACS_BASE, params=params, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    ACS API error for state {state_fips}: {e}", flush=True)
        return []

    try:
        data = r.json()
    except requests.exceptions.JSONDecodeError as e:
        print(f"    JSON decode error for state {state_fips}: {e}", flush=True)
        return []

    if not data or len(data) < 1:
        print(f"    No data returned for state {state_fips}", flush=True)
        return []

    header = data[0]
    return [dict(zip(header, row)) for row in data[1:]]

# ── RUCA fetch ────────────────────────────────────────────────────────────────

def load_ruca() -> dict[str, tuple[int | None, float | None]]:
    """Return {geoid: (primary_code, secondary_code)} from USDA RUCA 2020 CSV."""
    import csv as _csv

    if not RUCA_CACHE.exists():
        print("Downloading RUCA 2020 from USDA ERS…", flush=True)
        try:
            r = requests.get(RUCA_URL, timeout=120)
            r.raise_for_status()
        except requests.RequestException as e:
            sys.exit(
                f"Failed to download RUCA data: {e}\n"
                f"Download manually from https://www.ers.usda.gov/data-products/"
                f"rural-urban-commuting-area-codes/ and save to {RUCA_CACHE}"
            )
        RUCA_CACHE.write_text(r.text, encoding="utf-8")
        print(f"  Saved to {RUCA_CACHE}", flush=True)
    else:
        print(f"Using cached RUCA file: {RUCA_CACHE}", flush=True)

    ruca: dict[str, tuple[int | None, float | None]] = {}
    with open(RUCA_CACHE, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            geoid = row.get("TractFIPS20", "").strip()
            if len(geoid) != 11:
                continue
            primary_raw   = row.get("PrimaryRUCA", "").strip()
            secondary_raw = row.get("SecondaryRUCA", "").strip()
            try:
                primary = int(primary_raw) if primary_raw else None
            except ValueError:
                primary = None
            try:
                secondary = float(secondary_raw) if secondary_raw else None
            except ValueError:
                secondary = None
            ruca[geoid] = (primary, secondary)

    print(f"  RUCA loaded: {len(ruca):,} tracts.", flush=True)
    return ruca

# ── main ──────────────────────────────────────────────────────────────────────

ALL_FIPS = [
    "01","02","04","05","06","08","09","10","11","12","13","15","16","17","18",
    "19","20","21","22","23","24","25","26","27","28","29","30","31","32","33",
    "34","35","36","37","38","39","40","41","42","44","45","46","47","48","49",
    "50","51","53","54","55","56",
]

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true",
                        help="Overwrite existing rows (default: skip already-loaded tracts)")
    args = parser.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    # 1. Find which GEOIDs we need
    cur.execute("SELECT geoid, state_fips FROM census_tracts ORDER BY geoid")
    all_tracts = cur.fetchall()
    print(f"Total census tracts in DB: {len(all_tracts):,}", flush=True)

    if not args.refresh:
        cur.execute("SELECT geoid FROM census_tract_demographics")
        done = {row[0] for row in cur.fetchall()}
        needed = [(g, s) for g, s in all_tracts if g not in done]
    else:
        cur.execute("DELETE FROM census_tract_demographics")
        conn.commit()
        needed = all_tracts

    if not needed:
        print("All tracts already loaded. Use --refresh to reload.", flush=True)
        return

    needed_geoids = {g for g, _ in needed}
    states_needed = sorted({s for _, s in needed if s})
    print(f"Tracts to load: {len(needed):,} across {len(states_needed)} states", flush=True)

    # 2. Load RUCA
    ruca = load_ruca()

    # 3. Fetch ACS data state by state and accumulate rows
    all_vars = INCOME_VARS + LANG_VARS
    acs_data: dict[str, dict] = {}

    for i, state in enumerate(states_needed, 1):
        print(f"  [{i}/{len(states_needed)}] ACS state {state}…", end=" ", flush=True)
        rows = fetch_acs_for_state(state, all_vars)
        count = 0
        for r in rows:
            geoid = r.get("state", "") + r.get("county", "") + r.get("tract", "")
            if len(geoid) == 11 and geoid in needed_geoids:
                acs_data[geoid] = r
                count += 1
        print(f"{count} tracts", flush=True)
        time.sleep(0.1)  # be polite to Census API

    # 4. Assemble rows and upsert
    batch = []
    for geoid in needed_geoids:
        r = acs_data.get(geoid, {})
        ruca_primary, ruca_secondary = ruca.get(geoid, (None, None))

        # Language aggregation
        v = lambda key: r.get(key)
        lang_total       = safe_int(v("C16001_001E"))
        lang_english     = safe_int(v("C16001_002E"))
        lang_spanish     = sum_lang(v("C16001_003E"), v("C16001_004E"))
        lang_ie          = sum_lang(*[v(f"C16001_{k:03d}E") for k in range(5, 13)])
        lang_ap          = sum_lang(*[v(f"C16001_{k:03d}E") for k in range(13, 23)])
        lang_arabic      = sum_lang(v("C16001_023E"), v("C16001_024E"))
        lang_other       = sum_lang(v("C16001_025E"), v("C16001_026E"))

        batch.append({
            "geoid":                   geoid,
            "median_household_income": safe_int(v("B19013_001E")),
            "lang_total":              lang_total,
            "lang_english_only":       lang_english,
            "lang_spanish":            lang_spanish,
            "lang_indo_european":      lang_ie,
            "lang_asian_pacific":      lang_ap,
            "lang_arabic":             lang_arabic,
            "lang_other":              lang_other,
            "ruca_code":               ruca_primary,
            "ruca_secondary_code":     ruca_secondary,
            "acs_year":                2023,
        })

    print(f"\nInserting {len(batch):,} rows…", flush=True)
    cols = list(batch[0].keys())
    placeholders = ",".join(["%s"] * len(cols))
    col_str = ",".join(f'"{c}"' for c in cols)
    update_set = ",".join(
        f'"{c}" = EXCLUDED."{c}"' for c in cols if c != "geoid"
    )
    sql = (
        f'INSERT INTO census_tract_demographics ({col_str}) VALUES ({placeholders}) '
        f'ON CONFLICT (geoid) DO UPDATE SET {update_set}'
    )

    PAGE = 500
    for start in range(0, len(batch), PAGE):
        chunk = batch[start:start + PAGE]
        psycopg2.extras.execute_batch(
            cur, sql, [tuple(r[c] for c in cols) for r in chunk], page_size=PAGE
        )
        conn.commit()
        print(f"  {min(start + PAGE, len(batch)):,}/{len(batch):,} inserted", flush=True)

    # 5. Summary
    cur.execute("SELECT COUNT(*) FROM census_tract_demographics")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM census_tract_demographics WHERE median_household_income IS NOT NULL")
    has_income = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM census_tract_demographics WHERE ruca_code IS NOT NULL")
    has_ruca = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM census_tract_demographics WHERE lang_total IS NOT NULL")
    has_lang = cur.fetchone()[0]

    print(f"\n── Summary ───────────────────────────────────────")
    print(f"  Total rows in census_tract_demographics: {total:,}")
    print(f"  With income data:    {has_income:,}")
    print(f"  With language data:  {has_lang:,}")
    print(f"  With RUCA code:      {has_ruca:,}")

    cur.execute("""
        SELECT ruca_code, COUNT(*) AS n
        FROM census_tract_demographics
        WHERE ruca_code IS NOT NULL
        GROUP BY ruca_code ORDER BY ruca_code
    """)
    print("\n  RUCA distribution:")
    labels = {
        1: "Urban core",
        2: "Urban, high commute",
        3: "Urban, low commute",
        4: "Large rural, high commute",
        5: "Large rural, low commute",
        6: "Large rural town",
        7: "Small rural, high commute",
        8: "Small rural, low commute",
        9: "Small rural town",
        10: "Isolated rural",
    }
    for code, count in cur.fetchall():
        label = labels.get(code, "")
        print(f"    {code:2d}  {label:35s}  {count:>6,}")

    cur.close()
    conn.close()
    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
