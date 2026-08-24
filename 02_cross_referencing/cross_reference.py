"""
Enrich synagogues_us_all.csv by cross-referencing:
  1. Wikidata SPARQL (synagogues in the US with official websites)
  2. Wikipedia state-list pages (as a secondary source)

Writes synagogues_us_enriched.csv.
"""

import csv
import json
import re
import time
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from shapely.geometry import shape, Point

HERE = Path(__file__).parent

# ── US state reverse-geocoder ─────────────────────────────────────────────────

STATE_NAME_TO_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}

_STATE_SHAPES = None  # lazy-loaded

def _load_state_shapes():
    global _STATE_SHAPES
    if _STATE_SHAPES is not None:
        return _STATE_SHAPES
    with open(HERE / "us_states.geojson") as f:
        geojson = json.load(f)
    _STATE_SHAPES = [
        (STATE_NAME_TO_CODE.get(feat["properties"]["name"], ""),
         shape(feat["geometry"]))
        for feat in geojson["features"]
        if feat["properties"]["name"] in STATE_NAME_TO_CODE
    ]
    return _STATE_SHAPES


def latlon_to_state(lat, lon) -> str:
    """Return 2-letter state code for a coordinate, or '' if not in any state."""
    if lat is None or lon is None:
        return ""
    try:
        pt = Point(float(lon), float(lat))
        for code, poly in _load_state_shapes():
            if poly.contains(pt):
                return code
    except Exception:
        pass
    return ""

# ── helpers ───────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "SynagogueResearch/1.0 (quinn.reynolds@mail.utoronto.ca)",
    "Accept": "application/json",
})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


STOP = {"congregation", "synagogue", "temple", "of", "the", "and", "a",
        "beth", "bnai", "beit", "ohev", "shalom", "israel", "jewish",
        "center", "community", "conservative", "reform", "orthodox"}


def name_match(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    ta = set(na.split()) - STOP
    tb = set(nb.split()) - STOP
    if not ta or not tb:
        return na == nb
    overlap = ta & tb
    return len(overlap) / max(len(ta), len(tb)) >= 0.65


def state_match(osm_state: str, dir_state: str) -> bool:
    """True if states match. If OSM has a state but directory doesn't, reject."""
    if not osm_state:
        return True          # OSM has no state — can't rule anything out
    if not dir_state:
        return False         # OSM has state, directory doesn't — too risky
    return osm_state.strip().upper() == dir_state.strip().upper()


# ── Wikidata ──────────────────────────────────────────────────────────────────

SPARQL_URL = "https://query.wikidata.org/sparql"

QUERY = """
SELECT DISTINCT ?item ?name ?website ?lat ?lon ?cityLabel ?stateLabel WHERE {
  # Instance of synagogue (or subclass)
  { ?item wdt:P31 wd:Q34627. }
  UNION
  { ?item wdt:P31/wdt:P279* wd:Q34627. }

  # Located in the United States
  ?item wdt:P17 wd:Q30.

  # English label = synagogue name
  ?item rdfs:label ?name.
  FILTER(LANG(?name) = "en")

  # Coordinates
  OPTIONAL {
    ?item wdt:P625 ?coord.
    BIND(geof:latitude(?coord) AS ?lat)
    BIND(geof:longitude(?coord) AS ?lon)
  }

  # Official website (optional)
  OPTIONAL { ?item wdt:P856 ?website. }

  # Direct admin territory (city)
  OPTIONAL {
    ?item wdt:P131 ?city.
    ?city rdfs:label ?cityLabel.
    FILTER(LANG(?cityLabel) = "en")
  }

  # Walk up P131 chain to find a US state by label
  OPTIONAL {
    ?item wdt:P131+ ?state.
    ?state wdt:P31/wdt:P279* wd:Q35657.
    ?state rdfs:label ?stateLabel.
    FILTER(LANG(?stateLabel) = "en")
  }
}
"""


def scrape_wikidata():
    print("Querying Wikidata SPARQL for US synagogues…")
    try:
        r = SESSION.get(
            SPARQL_URL,
            params={"query": QUERY, "format": "json"},
            timeout=120,
        )
        r.raise_for_status()
        bindings = r.json()["results"]["bindings"]
    except Exception as e:
        print(f"  Wikidata query failed: {e}")
        return []

    # Consolidate by QID (multiple rows if multiple cities/states)
    by_qid: dict = {}
    for b in bindings:
        qid = b["item"]["value"].split("/")[-1]
        state_label = b.get("stateLabel", {}).get("value", "")
        state_code = STATE_NAME_TO_CODE.get(state_label, "")
        if qid not in by_qid:
            by_qid[qid] = {
                "name": b.get("name", {}).get("value", ""),
                "website": b.get("website", {}).get("value", ""),
                "city": b.get("cityLabel", {}).get("value", ""),
                "state_code": state_code,
                "lat": b.get("lat", {}).get("value", ""),
                "lon": b.get("lon", {}).get("value", ""),
                "source": "Wikidata",
            }
        else:
            rec = by_qid[qid]
            if not rec["website"] and b.get("website"):
                rec["website"] = b["website"]["value"]
            if not rec["city"] and b.get("cityLabel"):
                rec["city"] = b["cityLabel"]["value"]
            if not rec["state_code"] and state_code:
                rec["state_code"] = state_code
            if not rec["lat"] and b.get("lat"):
                rec["lat"] = b["lat"]["value"]
            if not rec["lon"] and b.get("lon"):
                rec["lon"] = b["lon"]["value"]

    records = list(by_qid.values())
    with_web = sum(1 for r in records if r["website"])
    print(f"  {len(records)} Wikidata synagogue entries, {with_web} with websites.")
    return records


# ── Wikipedia state lists ─────────────────────────────────────────────────────

# Wikipedia "List of synagogues in X" pages that include website columns
WIKI_STATES = [
    # Original 18 — highest Jewish population density
    ("New York", "NY"), ("California", "CA"), ("Florida", "FL"),
    ("Illinois", "IL"), ("Pennsylvania", "PA"), ("New Jersey", "NJ"),
    ("Texas", "TX"), ("Maryland", "MD"), ("Massachusetts", "MA"),
    ("Ohio", "OH"), ("Michigan", "MI"), ("Virginia", "VA"),
    ("Georgia", "GA"), ("Connecticut", "CT"), ("Colorado", "CO"),
    ("Washington", "WA"), ("Minnesota", "MN"), ("Missouri", "MO"),
    # Remaining states and DC — added to improve coverage
    ("Arizona", "AZ"), ("North Carolina", "NC"), ("Nevada", "NV"),
    ("Oregon", "OR"), ("Wisconsin", "WI"), ("Tennessee", "TN"),
    ("Indiana", "IN"), ("Louisiana", "LA"), ("Kentucky", "KY"),
    ("Rhode Island", "RI"), ("New Hampshire", "NH"), ("Delaware", "DE"),
    ("Utah", "UT"), ("New Mexico", "NM"), ("South Carolina", "SC"),
    ("Alabama", "AL"), ("Mississippi", "MS"), ("Arkansas", "AR"),
    ("Oklahoma", "OK"), ("Idaho", "ID"), ("Montana", "MT"),
    ("Wyoming", "WY"), ("North Dakota", "ND"), ("South Dakota", "SD"),
    ("Nebraska", "NE"), ("Kansas", "KS"), ("Iowa", "IA"),
    ("West Virginia", "WV"), ("Vermont", "VT"), ("Maine", "ME"),
    ("District of Columbia", "DC"), ("Hawaii", "HI"), ("Alaska", "AK"),
]


def scrape_wikipedia_state(state_name: str, state_code: str):
    # Wikipedia consolidated all US synagogue lists into one page; per-state
    # pages (List_of_synagogues_in_X) no longer exist and return 404.
    # Wikidata SPARQL already covers Wikipedia-indexed synagogues via P856.
    slug = f"List_of_synagogues_in_{state_name.replace(' ', '_')}"
    url = f"https://en.wikipedia.org/wiki/{slug}"
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "SynagogueResearch/1.0"})
        if r.status_code == 404:
            return []
        r.raise_for_status()
    except Exception as e:
        print(f"    {state_name}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    records = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable")):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        # Find column indices
        name_col = next((i for i, h in enumerate(headers) if "name" in h or "synagogue" in h), None)
        city_col = next((i for i, h in enumerate(headers) if "city" in h or "location" in h), None)
        web_col  = next((i for i, h in enumerate(headers) if "web" in h or "url" in h or "site" in h), None)
        if name_col is None:
            continue
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if not cells or len(cells) <= name_col:
                continue
            name = cells[name_col].get_text(strip=True)
            city = cells[city_col].get_text(strip=True) if city_col is not None and len(cells) > city_col else ""
            # Look for hyperlink in name cell or website cell
            website = ""
            if web_col is not None and len(cells) > web_col:
                a = cells[web_col].find("a", href=True)
                if a and a["href"].startswith("http"):
                    website = a["href"]
            if not website:
                a = cells[name_col].find("a", href=True)
                if a and a["href"].startswith("http") and "wikipedia.org" not in a["href"]:
                    website = a["href"]
            if name:
                records.append({"name": name, "city": city, "state_code": state_code,
                                 "website": website, "source": "Wikipedia"})
    return records


def scrape_wikipedia():
    print("\nScraping Wikipedia state lists…")
    all_records = []
    for state_name, code in WIKI_STATES:
        recs = scrape_wikipedia_state(state_name, code)
        if recs:
            with_web = sum(1 for r in recs if r["website"])
            print(f"  {state_name}: {len(recs)} rows, {with_web} with websites")
            all_records.extend(recs)
        time.sleep(0.5)
    return all_records


# ── merge ─────────────────────────────────────────────────────────────────────

import math

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat/2)**2 + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def coord_match_and_fill(osm_rows, wd_records, threshold_km=1.0):
    """One-to-one match by nearest coordinates (each Wikidata record used once)."""
    wd_with_coords = [
        r for r in wd_records
        if r.get("website") and r.get("lat") and r.get("lon")
    ]
    osm_needing = [r for r in osm_rows if not r["website"] and r.get("lat") and r.get("lon")]

    # Build all candidate pairs sorted by distance
    pairs = []
    for osm in osm_needing:
        for wd in wd_with_coords:
            try:
                d = haversine_km(osm["lat"], osm["lon"], wd["lat"], wd["lon"])
            except Exception:
                continue
            if d <= threshold_km:
                pairs.append((d, id(osm), id(wd), osm, wd))
    pairs.sort()

    used_wd = set()
    used_osm = set()
    filled = 0
    for d, osm_id, wd_id, osm, wd in pairs:
        if osm_id in used_osm or wd_id in used_wd:
            continue
        osm["website"] = wd["website"]
        osm["website_source"] = "Wikidata-coord"
        used_osm.add(osm_id)
        used_wd.add(wd_id)
        filled += 1
    return filled


def match_and_fill(osm_rows, directory_records):
    filled = 0
    for row in osm_rows:
        if row["website"]:
            continue
        for dr in directory_records:
            if not dr.get("website"):
                continue
            if not state_match(row.get("state", ""), dr.get("state_code", "")):
                continue
            if name_match(row["name"], dr["name"]):
                row["website"] = dr["website"]
                row["website_source"] = dr.get("source", "directory")
                filled += 1
                break
    return filled


# ── main ──────────────────────────────────────────────────────────────────────

def _load_osm(path: Path) -> list[dict]:
    """Load OSM CSV, falling back to ../01_osm_scraping/ if not found here."""
    candidates = [path, HERE.parent / "01_osm_scraping" / path.name]
    for p in candidates:
        if p.exists():
            rows = []
            with open(p, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    row["website_source"] = "OSM" if row["website"] else ""
                    rows.append(row)
            return rows
    raise FileNotFoundError(
        f"Cannot find {path.name} — run synagogues_osm.py first."
    )


def _load_harvest(path: Path) -> list[dict]:
    """Load directory_harvest.csv; normalise column name state→state_code."""
    records = []
    with open(path, newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            if "state_code" not in rec and "state" in rec:
                rec["state_code"] = rec["state"]
            records.append(rec)
    return records


def main():
    # ── 0. Load OSM base data ─────────────────────────────────────────────────
    rows = _load_osm(HERE / "synagogues_us_all.csv")
    osm_with = sum(1 for r in rows if r["website"])
    print(f"Loaded {len(rows)} OSM records ({osm_with} with websites).")

    # Fill missing states from coordinates
    missing_state = sum(1 for r in rows if not r["state"])
    print(f"Reverse-geocoding states for {missing_state} records without state tag…")
    _load_state_shapes()
    filled_states = 0
    for row in rows:
        if not row["state"] and row.get("lat") and row.get("lon"):
            code = latlon_to_state(row["lat"], row["lon"])
            if code:
                row["state"] = code
                filled_states += 1
    print(f"  Filled {filled_states} states from coordinates.")

    # ── 1. Wikidata — coordinate match first, then name+state ─────────────────
    wd_records = scrape_wikidata()
    filled_wd_coord = coord_match_and_fill(rows, wd_records, threshold_km=1.0)
    print(f"  Coordinate matches: {filled_wd_coord}")
    filled_wd_name = match_and_fill(rows, wd_records)
    print(f"  Name+state matches: {filled_wd_name}")
    print(f"Filled {filled_wd_coord + filled_wd_name} rows from Wikidata total.\n")

    # ── 2. Wikipedia state lists (all 50 states + DC) ─────────────────────────
    wiki_records = scrape_wikipedia()
    filled_wiki = match_and_fill(rows, wiki_records)
    print(f"Filled {filled_wiki} rows from Wikipedia.\n")

    # ── 3. Denomination directory harvest (URJ, etc.) ─────────────────────────
    harvest_path = HERE / "directory_harvest.csv"
    filled_harvest = 0
    if harvest_path.exists():
        harvest_records = _load_harvest(harvest_path)
        usable = [r for r in harvest_records if r.get("website") and r.get("name")]
        filled_harvest = match_and_fill(rows, usable)
        print(f"Filled {filled_harvest} rows from directory harvest "
              f"({len(usable)} usable records in {harvest_path.name}).\n")
    else:
        print(f"No {harvest_path.name} found — run harvest_directories.py first.\n")

    # ── Write outputs ─────────────────────────────────────────────────────────
    out_path      = HERE / "synagogues_us_enriched.csv"
    web_only_path = HERE / "synagogues_us_with_websites.csv"

    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with_web = [r for r in rows if r["website"]]
    with open(web_only_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(with_web)

    # ── Summary ───────────────────────────────────────────────────────────────
    from collections import Counter
    sources = Counter(r.get("website_source", "") for r in rows if r.get("website"))

    print(f"\n{'─'*50}")
    print(f"Full dataset  : {out_path}  ({len(rows)} rows)")
    print(f"With websites : {web_only_path}  ({len(with_web)} rows)")
    print(f"\nWebsite sources:")
    print(f"  OSM (original)        : {sources.get('OSM', 0)}")
    print(f"  Wikidata coord match  : {sources.get('Wikidata-coord', 0)}")
    print(f"  Wikidata name+state   : {sources.get('Wikidata', 0)}")
    print(f"  Wikipedia             : {sources.get('Wikipedia', 0)}")
    for src, cnt in sorted(sources.items()):
        if src not in ("OSM", "Wikidata-coord", "Wikidata", "Wikipedia", ""):
            print(f"  {src:22s}: {cnt}")
    print(f"  {'─'*30}")
    print(f"  Total                 : {len(with_web)}")

    print("\nSample new finds (non-OSM):")
    for r in [r for r in rows if r.get("website_source") and r["website_source"] != "OSM"][:15]:
        print(f"  [{r['website_source']:16s}] {r['name'][:40]:40s} → {r['website']}")


if __name__ == "__main__":
    main()
