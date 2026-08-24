"""
Load all project CSVs and HTML page metadata into the synagogue PostgreSQL database.

Usage:
    python load_data.py                    # load everything, skip text extraction
    python load_data.py --extract-text     # also extract plain text from HTML files
    python load_data.py --only elections   # load only election data (needs .tab file)

Prerequisites:
    pip install psycopg2-binary python-dotenv beautifulsoup4

Connection config comes from ../.env:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

Harvard Dataverse election data (optional):
    Download 1976-2024-house.tab from https://doi.org/10.7910/DVN/IG0UN2
    and place it at 08_database/1976-2024-house.tab
"""

import argparse
import csv
import os
import re
import sys
import urllib.parse
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ── paths ─────────────────────────────────────────────────────────────────────

HERE    = Path(__file__).parent
ROOT    = HERE.parent
load_dotenv(ROOT / ".env")

COMBINED_CSV   = ROOT / "04_combined" / "synagogues_combined.csv"
ENV_CSV        = ROOT / "06_environmental_classification" / "classifying_environmental_action.csv"
LLM_CSV        = ROOT / "07_LLM_categorization" / "llm_classifications.csv"
HTML_DIR       = ROOT / "05_web_scraping"        / "html_pages"
ELECTION_TAB   = HERE / "1976-2024-house.tab"

# ── DB connection ─────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host     = os.getenv("DB_HOST", "localhost"),
        port     = int(os.getenv("DB_PORT", 5432)),
        dbname   = os.getenv("DB_NAME", "synagogues"),
        user     = os.getenv("DB_USER", "research"),
        password = os.getenv("DB_PASSWORD"),
    )

# ── helpers ───────────────────────────────────────────────────────────────────

def normalize_domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc or url
        return re.sub(r"^www\.", "", host).lower().rstrip("/")
    except Exception:
        return url


def extract_text(html_path: Path) -> str:
    """Strip HTML tags and return plain text (requires beautifulsoup4)."""
    try:
        from bs4 import BeautifulSoup
        html = html_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:500_000]   # cap at 500KB of text
    except Exception:
        return ""

def bulk_insert(cur, table: str, rows: list[dict], page_size: int = 500):
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join(["%s"] * len(cols))
    col_str = ",".join(f'"{c}"' for c in cols)
    sql = f'INSERT INTO {table} ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
    psycopg2.extras.execute_batch(
        cur, sql,
        [tuple(r[c] for c in cols) for r in rows],
        page_size=page_size,
    )

# ── loaders ───────────────────────────────────────────────────────────────────

def load_synagogues(cur):
    print("Loading synagogues, congressional_districts, census_tracts, websites…", flush=True)
    rows = list(csv.DictReader(open(COMBINED_CSV, encoding="utf-8")))

    # 1. congressional_districts
    districts = {}
    for r in rows:
        d = r.get("congressional_district", "").strip()
        if d and d not in districts:
            parts = d.rsplit("-", 1)
            districts[d] = {
                "district_id":     d,
                "state_po":        parts[0],
                "district_number": int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0,
                "congress_session": 119,
            }
    bulk_insert(cur, "congressional_districts", list(districts.values()))

    # 2. census_tracts
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
    bulk_insert(cur, "census_tracts", list(tracts.values()))

    # 3. synagogues — combined list has OSM, Google-Places, and Both entries
    syns = []
    for r in rows:
        d = r.get("congressional_district", "").strip() or None
        g = r.get("census_tract_geoid", "").strip()
        raw_osm_id = r.get("osm_id", "").strip()
        raw_place_id = r.get("place_id", "").strip()
        syns.append({
            "osm_id":                int(raw_osm_id) if raw_osm_id else None,
            "osm_type":              r.get("osm_type", "") or None,
            "place_id":              raw_place_id or None,
            "name":                  r["name"],
            "lat":                   float(r["lat"]) if r.get("lat") else None,
            "lon":                   float(r["lon"]) if r.get("lon") else None,
            "city":                  r.get("city", "") or None,
            "state":                 r.get("state", "") or None,
            "denomination":          r.get("denomination", "") or None,
            "address":               r.get("address", "") or None,
            "business_status":       r.get("business_status", "") or None,
            "source":                r.get("source", "") or None,
            "congressional_district": d if d in districts else None,
            "census_tract_geoid":    g if len(g) == 11 else None,
        })
    bulk_insert(cur, "synagogues", syns)
    cur.execute("""
        UPDATE synagogues
        SET location = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
        WHERE location IS NULL AND lon IS NOT NULL AND lat IS NOT NULL
    """)

    # 4. websites — look up synagogue_id by osm_id (OSM/Both) or place_id (GP-only)
    cur.execute("SELECT osm_id, place_id, id FROM synagogues")
    osm_to_id   = {}
    place_to_id = {}
    for osm_id, place_id, syn_id in cur.fetchall():
        if osm_id is not None:
            osm_to_id[osm_id] = syn_id
        if place_id:
            place_to_id[place_id] = syn_id

    websites = []
    for r in rows:
        url = r.get("website", "").strip()
        if not url:
            continue
        raw_osm_id  = r.get("osm_id", "").strip()
        raw_place_id = r.get("place_id", "").strip()
        if raw_osm_id:
            syn_id = osm_to_id.get(int(raw_osm_id))
        else:
            syn_id = place_to_id.get(raw_place_id)
        if not syn_id:
            continue
        websites.append({
            "synagogue_id":   syn_id,
            "url":            url,
            "domain":         normalize_domain(url),
            "website_source": r.get("website_source", "") or None,
        })
    bulk_insert(cur, "websites", websites)
    print(f"  {len(syns)} synagogues, {len(websites)} websites loaded.", flush=True)



def load_web_pages(cur, extract_text_flag: bool = False):
    if not HTML_DIR.exists():
        print("html_pages/ directory not found — skipping web_pages load.", flush=True)
        return
    cur.execute("SELECT domain, id FROM websites")
    domain_to_id = {row[0]: row[1] for row in cur.fetchall()}

    env_dir = ROOT / "06_environmental_classification" / "environmental_html_pages"
    env_pages = set()
    if env_dir.exists():
        env_pages = {str(p.relative_to(env_dir)) for p in env_dir.rglob("*.html")}

    print(f"Loading web_pages from {HTML_DIR} ({'with' if extract_text_flag else 'without'} text extraction)…", flush=True)
    batch, n_inserted = [], 0

    for html_file in HTML_DIR.rglob("*.html"):
        rel      = html_file.relative_to(HTML_DIR)
        domain   = rel.parts[0]
        page_path = str(rel)
        w_id     = domain_to_id.get(domain)
        if not w_id:
            continue
        has_env   = page_path in env_pages
        text      = extract_text(html_file) if extract_text_flag else None
        batch.append({
            "website_id":       w_id,
            "page_path":        page_path,
            "has_env_keywords": has_env,
            "content_text":     text,
        })
        if len(batch) >= 500:
            bulk_insert(cur, "web_pages", batch)
            n_inserted += len(batch)
            batch.clear()
            print(f"  {n_inserted} pages inserted…", flush=True)

    if batch:
        bulk_insert(cur, "web_pages", batch)
        n_inserted += len(batch)

    print(f"  {n_inserted} total web_pages loaded.", flush=True)


def load_env_analysis(cur):
    print("Loading env_keyword_analysis…", flush=True)
    cur.execute("SELECT domain, id FROM websites")
    domain_to_id = {row[0]: row[1] for row in cur.fetchall()}

    rows = list(csv.DictReader(open(ENV_CSV, encoding="utf-8")))
    batch = []
    for r in rows:
        w_id = domain_to_id.get(r["website"])
        if not w_id:
            continue
        batch.append({
            "website_id":                w_id,
            "pages_with_hits":           int(r.get("pages_with_hits", 0) or 0),
            "total_pages":               int(r.get("total_pages", 0) or 0),
            "total_keyword_occurrences": int(r.get("total_keyword_occurrences", 0) or 0),  # absent in semantic_filter output → 0
            "top_llm_category":          r.get("top_llm_category") or None,
            "llm_pages_classified":      int(r["llm_pages_classified"]) if r.get("llm_pages_classified") else None,
        })
    bulk_insert(cur, "env_keyword_analysis", batch)
    print(f"  {len(batch)} env_keyword_analysis rows loaded.", flush=True)


def load_llm_classifications(cur):
    if not LLM_CSV.exists() or LLM_CSV.stat().st_size < 100:
        print("llm_classifications.csv is empty or missing — skipping.", flush=True)
        return
    print("Loading llm_classifications…", flush=True)
    cur.execute("SELECT page_path, id FROM web_pages")
    path_to_id = {row[0]: row[1] for row in cur.fetchall()}

    rows = list(csv.DictReader(open(LLM_CSV, encoding="utf-8")))
    batch = []
    for r in rows:
        wp_id = path_to_id.get(r.get("page", ""))
        if not wp_id:
            continue
        batch.append({
            "web_page_id": wp_id,
            "category":    r.get("category") or None,
            "model_used":  r.get("model", "") or None,
            "temperature": None,  # not stored in current CSV; set when re-run
            "error":       r.get("error") or None,
        })
    bulk_insert(cur, "llm_classifications", batch)
    print(f"  {len(batch)} llm_classifications loaded.", flush=True)


def load_elections(cur):
    if not ELECTION_TAB.exists():
        print(f"\n⚠  Election data file not found at {ELECTION_TAB}")
        print("   Download 1976-2024-house.tab from https://doi.org/10.7910/DVN/IG0UN2")
        print("   and place it in 08_database/. Skipping.\n", flush=True)
        return

    print("Loading election_races and election_candidates…", flush=True)
    cur.execute("SELECT district_id FROM congressional_districts")
    known_districts = {row[0] for row in cur.fetchall()}

    race_key_to_id = {}
    races_batch, cands_batch = [], []

    with open(ELECTION_TAB, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)  # file uses comma despite .tab extension
        for row in reader:
            state  = (row.get("state_po") or "").strip().upper()
            dist   = row.get("district", "0").strip()
            try:
                dist_num = int(float(dist))
            except ValueError:
                dist_num = 0
            district_id = f"{state}-{dist_num}"

            # Insert district if not seen before
            if district_id not in known_districts:
                cur.execute("""
                    INSERT INTO congressional_districts
                        (district_id, state_po, district_number, congress_session)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (district_id, state, dist_num, 119))
                known_districts.add(district_id)

            year    = int(row.get("year", 0))
            stage   = (row.get("stage") or "gen").strip()
            mode    = (row.get("mode") or "total").strip()
            special = str(row.get("special", "")).lower() in ("true", "1", "yes")
            runoff  = str(row.get("runoff",  "")).lower() in ("true", "1", "yes")

            try:
                totalvotes = int(float(row.get("totalvotes") or 0))
            except ValueError:
                totalvotes = 0

            race_key = (district_id, year, stage, mode)
            if race_key not in race_key_to_id:
                cur.execute("""
                    INSERT INTO election_races (district_id, year, stage, special, runoff, mode, totalvotes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (district_id, year, stage, mode) DO NOTHING
                    RETURNING id
                """, (district_id, year, stage, special, runoff, mode, totalvotes))
                result = cur.fetchone()
                if result:
                    race_key_to_id[race_key] = result[0]
                else:
                    cur.execute("SELECT id FROM election_races WHERE district_id=%s AND year=%s AND stage=%s AND mode=%s",
                                (district_id, year, stage, mode))
                    race_key_to_id[race_key] = cur.fetchone()[0]

            race_id = race_key_to_id[race_key]
            try:
                candvotes = int(float(row.get("candidatevotes") or 0))
            except ValueError:
                candvotes = 0
            vote_share = round(candvotes / totalvotes, 6) if totalvotes else None

            cands_batch.append({
                "race_id":        race_id,
                "candidate":      row.get("candidate") or None,
                "party":          (row.get("party") or "").lower() or None,
                "candidatevotes": candvotes,
                "vote_share":     vote_share,
                "writein":        str(row.get("writein", "")).lower() in ("true", "1"),
                "fusion_ticket":  str(row.get("fusion_ticket", "")).lower() in ("true", "1"),
            })

            if len(cands_batch) >= 1000:
                bulk_insert(cur, "election_candidates", cands_batch)
                cands_batch.clear()

    if cands_batch:
        bulk_insert(cur, "election_candidates", cands_batch)

    cur.execute("SELECT COUNT(*) FROM election_races")
    n_races = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM election_candidates")
    n_cands = cur.fetchone()[0]
    print(f"  {n_races} election races, {n_cands} candidate rows loaded.", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Load synagogue pipeline data into PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Stages:
  ingest    Load synagogues, web_pages HTML (required before classification)
  classify  Load env_keyword_analysis and llm_classifications (requires ingest first)
  elections Load Harvard election data (independent)
  all       Run ingest + classify + elections (default)

For two-phase loading:
  python load_data.py --stage ingest
  # ... run environmental and LLM classification scripts ...
  python load_data.py --stage classify
""")
    parser.add_argument("--extract-text", action="store_true",
                        help="Extract plain text from HTML files (slow)")
    parser.add_argument("--stage", choices=["ingest","classify","elections","all"],
                        default="all",
                        help="Which stage to run (default: all)")
    parser.add_argument("--only", choices=["synagogues","pages","env","llm","elections"],
                        help="Load only one section (legacy; overrides --stage)")
    args = parser.parse_args()

    print("Connecting to database…", flush=True)
    try:
        conn = get_conn()
    except Exception as e:
        sys.exit(f"Connection failed: {e}\n"
                 "Make sure the container is running: docker compose up -d")
    conn.autocommit = False
    cur = conn.cursor()

    try:
        only = args.only
        stage = args.stage

        if only:
            # Legacy --only flag
            if only == "synagogues":  load_synagogues(cur)
            elif only == "pages":     load_web_pages(cur, extract_text_flag=args.extract_text)
            elif only == "env":       load_env_analysis(cur)
            elif only == "llm":       load_llm_classifications(cur)
            elif only == "elections": load_elections(cur)
            conn.commit()
        else:
            if stage in ("ingest", "all"):
                load_synagogues(cur)
                conn.commit()
                load_web_pages(cur, extract_text_flag=args.extract_text)
                conn.commit()
            if stage in ("classify", "all"):
                load_env_analysis(cur)
                conn.commit()
                load_llm_classifications(cur)
                conn.commit()
            if stage in ("elections", "all"):
                load_elections(cur)
                conn.commit()

        # Verification counts
        print("\n── Verification ──────────────────────────────────", flush=True)
        for table in ["synagogues","websites","web_pages","env_keyword_analysis",
                      "llm_classifications","election_races","election_candidates"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table:30s} {cur.fetchone()[0]:>8,}", flush=True)

    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
