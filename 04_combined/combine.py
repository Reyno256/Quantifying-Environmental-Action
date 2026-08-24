"""
Merge OSM-enriched synagogue list with Google Places synagogue list.

Inputs
------
  02_cross_referencing/synagogues_us_enriched.csv   (2,101 rows)
  03_google_places/google_places_synagogues.csv      (1,505 rows)

Matching strategy (applied in order, each GP entry matched at most once)
-------------------------------------------------------------------------
  1. Coordinate match  : haversine ≤ 200 m  (same building)
  2. Name + coord      : normalised name overlap ≥ 65 % AND haversine ≤ 1 km
                         AND same state  (handles imprecise OSM coords)

Resolution rules
----------------
  - Website   : prefer Google Places (higher coverage, more recent); fall back to OSM
  - Coords    : prefer OSM (surveyed); fall back to Google Places
  - Name      : prefer OSM; fall back to Google Places
  - Metadata  : keep all fields from both sources; set source = "Both" | "OSM" | "Google-Places"

Output
------
  04_combined/synagogues_combined.csv
  Columns: name, website, website_source, city, state, denomination,
           lat, lon, osm_type, osm_id, place_id, address, business_status, source
"""

import csv
import math
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

HERE     = Path(__file__).parent
OSM_CSV  = HERE.parent / "02_cross_referencing" / "synagogues_us_enriched.csv"
GP_CSV   = HERE.parent / "03_google_places"      / "google_places_synagogues.csv"
OUT_CSV  = HERE / "synagogues_combined.csv"

FIELDNAMES = [
    "name", "website", "website_source",
    "city", "state", "denomination",
    "lat", "lon",
    "osm_type", "osm_id",
    "place_id", "address", "business_status",
    "congressional_district", "census_tract_geoid", "census_tract_name",
    "source",
]

COORD_MATCH_KM  = 0.20   # 200 m — same building
NAME_MATCH_KM   = 1.00   # 1 km  — same neighbourhood, name must confirm

# ── helpers ───────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(float(lat1)))
         * math.cos(math.radians(float(lat2)))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


STOP = {"congregation", "synagogue", "temple", "of", "the", "and", "a",
        "center", "community", "jewish"}


def name_tokens(s: str) -> set[str]:
    return set(normalize(s).split()) - STOP


def name_match(a: str, b: str) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return normalize(a) == normalize(b)
    overlap = ta & tb
    return len(overlap) / max(len(ta), len(tb)) >= 0.65


def best_website(osm_site: str, gp_site: str, osm_source: str) -> tuple[str, str]:
    """Return (website, website_source), preferring Google Places."""
    if gp_site:
        return gp_site, "Google-Places"
    if osm_site:
        return osm_site, osm_source or "OSM"
    return "", ""


# ── spatial grid index ────────────────────────────────────────────────────────

def grid_key(lat: float, lon: float, cell_deg: float = 0.05) -> tuple[int, int]:
    return int(lat / cell_deg), int(lon / cell_deg)


def build_index(rows: list[dict], cell_deg: float = 0.05) -> dict:
    idx: dict[tuple, list] = defaultdict(list)
    for row in rows:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (ValueError, KeyError):
            continue
        idx[grid_key(lat, lon, cell_deg)].append(row)
    return idx


def candidates(idx: dict, lat: float, lon: float,
               cell_deg: float = 0.05) -> list[dict]:
    """Return rows from surrounding 3×3 grid cells."""
    gi, gj = grid_key(lat, lon, cell_deg)
    result = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            result.extend(idx.get((gi + di, gj + dj), []))
    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    # Load OSM
    with open(OSM_CSV, newline="", encoding="utf-8") as f:
        osm_rows = list(csv.DictReader(f))
    print(f"OSM:    {len(osm_rows):,} rows  "
          f"({sum(1 for r in osm_rows if r['website']):,} with websites)")

    # Load Google Places
    with open(GP_CSV, newline="", encoding="utf-8") as f:
        gp_rows = list(csv.DictReader(f))
    print(f"Google: {len(gp_rows):,} rows  "
          f"({sum(1 for r in gp_rows if r['website']):,} with websites)")

    # Build spatial index over OSM rows
    osm_idx = build_index(osm_rows)

    # Track which OSM rows have been matched
    osm_matched: set[int] = set()   # indices into osm_rows
    combined: list[dict] = []

    matched_coord = matched_name = 0

    for gp in gp_rows:
        try:
            glat, glon = float(gp["lat"]), float(gp["lon"])
        except (ValueError, KeyError):
            # No coords — add as GP-only
            combined.append(_gp_only(gp))
            continue

        near = candidates(osm_idx, glat, glon)
        best_osm = None
        best_dist = float("inf")
        match_type = None

        for osm in near:
            idx_osm = id(osm)
            if idx_osm in osm_matched:
                continue
            try:
                olat, olon = float(osm["lat"]), float(osm["lon"])
            except (ValueError, KeyError):
                continue

            d = haversine(glat, glon, olat, olon)

            if d <= COORD_MATCH_KM and d < best_dist:
                best_osm, best_dist, match_type = osm, d, "coord"
            elif (d <= NAME_MATCH_KM
                  and osm.get("state", "").upper() == gp.get("state", "").upper()
                  and name_match(gp["name"], osm["name"])
                  and d < best_dist):
                best_osm, best_dist, match_type = osm, d, "name"

        if best_osm is not None:
            osm_matched.add(id(best_osm))
            combined.append(_merge(best_osm, gp))
            if match_type == "coord":
                matched_coord += 1
            else:
                matched_name += 1
        else:
            combined.append(_gp_only(gp))

    # Add unmatched OSM rows
    osm_only = 0
    for osm in osm_rows:
        if id(osm) not in osm_matched:
            combined.append(_osm_only(osm))
            osm_only += 1

    # Write output
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(combined)

    # Summary
    total     = len(combined)
    with_web  = sum(1 for r in combined if r["website"])
    both      = sum(1 for r in combined if r["source"] == "Both")
    gp_only_n = sum(1 for r in combined if r["source"] == "Google-Places")

    print(f"\n{'─'*55}")
    print(f"Matching:")
    print(f"  Coordinate matches (≤200 m) : {matched_coord:,}")
    print(f"  Name + coord matches (≤1 km): {matched_name:,}")
    print(f"  Google-Places only (new)    : {gp_only_n:,}")
    print(f"  OSM only                    : {osm_only:,}")
    print(f"\nCombined output: {OUT_CSV}")
    print(f"  Total rows    : {total:,}")
    print(f"  With websites : {with_web:,}  ({with_web/total*100:.1f}%)")
    print(f"  Source = Both : {both:,}")
    print(f"  Source = OSM  : {osm_only:,}")
    print(f"  Source = GP   : {gp_only_n:,}")

    from collections import Counter
    sources = Counter(r["website_source"] for r in combined if r["website"])
    print(f"\nWebsite sources:")
    for src, cnt in sources.most_common():
        print(f"  {src:22s}: {cnt:,}")


# ── row builders ──────────────────────────────────────────────────────────────

def _merge(osm: dict, gp: dict) -> dict:
    website, ws_source = best_website(
        osm.get("website", ""), gp.get("website", ""), osm.get("website_source", "")
    )
    return {
        "name":                   osm.get("name") or gp.get("name", ""),
        "website":                website,
        "website_source":         ws_source,
        "city":                   osm.get("city") or gp.get("city", ""),
        "state":                  osm.get("state") or gp.get("state", ""),
        "denomination":           osm.get("denomination", ""),
        "lat":                    osm.get("lat") or gp.get("lat", ""),
        "lon":                    osm.get("lon") or gp.get("lon", ""),
        "osm_type":               osm.get("osm_type", ""),
        "osm_id":                 osm.get("osm_id", ""),
        "place_id":               gp.get("place_id", ""),
        "address":                gp.get("address", ""),
        "business_status":        gp.get("business_status", ""),
        "congressional_district": osm.get("congressional_district", ""),
        "census_tract_geoid":     osm.get("census_tract_geoid", ""),
        "census_tract_name":      osm.get("census_tract_name", ""),
        "source":                 "Both",
    }


def _osm_only(osm: dict) -> dict:
    return {
        "name":                   osm.get("name", ""),
        "website":                osm.get("website", ""),
        "website_source":         osm.get("website_source", ""),
        "city":                   osm.get("city", ""),
        "state":                  osm.get("state", ""),
        "denomination":           osm.get("denomination", ""),
        "lat":                    osm.get("lat", ""),
        "lon":                    osm.get("lon", ""),
        "osm_type":               osm.get("osm_type", ""),
        "osm_id":                 osm.get("osm_id", ""),
        "place_id":               "",
        "address":                "",
        "business_status":        "",
        "congressional_district": osm.get("congressional_district", ""),
        "census_tract_geoid":     osm.get("census_tract_geoid", ""),
        "census_tract_name":      osm.get("census_tract_name", ""),
        "source":                 "OSM",
    }


def _gp_only(gp: dict) -> dict:
    return {
        "name":                   gp.get("name", ""),
        "website":                gp.get("website", ""),
        "website_source":         "Google-Places" if gp.get("website") else "",
        "city":                   gp.get("city", ""),
        "state":                  gp.get("state", ""),
        "denomination":           "",
        "lat":                    gp.get("lat", ""),
        "lon":                    gp.get("lon", ""),
        "osm_type":               "",
        "osm_id":                 "",
        "place_id":               gp.get("place_id", ""),
        "address":                gp.get("address", ""),
        "business_status":        gp.get("business_status", ""),
        "congressional_district": "",
        "census_tract_geoid":     "",
        "census_tract_name":      "",
        "source":                 "Google-Places",
    }


if __name__ == "__main__":
    main()
