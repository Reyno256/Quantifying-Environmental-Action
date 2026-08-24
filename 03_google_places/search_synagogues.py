"""
Search the Google Places API (v1) for US synagogues using a tiled grid.

Strategy
--------
* Coarse 1° grid over the contiguous US (lat 24–49, lon −125 to −66)
* Dense 0.5° sub-grid over 8 high-density metro bounding boxes
* Each grid point → POST places:searchText with locationBias circle (50 km radius)
* Paginate via nextPageToken (up to 3 pages × 20 results = 60 per point)
* Deduplicate globally by Google place_id
* Resume-safe: completed grid points saved to search_progress.json

Usage
-----
    python3 search_synagogues.py                  # full run
    python3 search_synagogues.py --max-points 5   # smoke test (5 grid points)

Output
------
    google_places_raw.jsonl   — one JSON line per unique place
    search_progress.json      — set of completed "lat,lon" keys (for resume)

Cost estimate: ~2,000–2,500 API calls × $0.035 = ~$70–$88
(Enterprise SKU E967-44BC-B44D; websiteUri is an Enterprise-tier field)

API key: set GOOGLE_PLACES_API_KEY in environment or .env file.
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).parent

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.websiteUri",
    "places.businessStatus",
])

RAW_OUT    = BASE / "google_places_raw.jsonl"
PROGRESS   = BASE / "search_progress.json"

SEARCH_RADIUS_M = 50_000   # 50 km
SEARCH_RADIUS_KM = SEARCH_RADIUS_M / 1000
MAX_PER_PAGE    = 20
MAX_PAGES       = 3        # up to 60 results per grid point
DELAY_S         = 0.12     # ~8 req/s, well under 10 req/s quota


# ── Grid definition ───────────────────────────────────────────────────────────

# Contiguous US bounding box
US_LAT = (24.0, 49.0)
US_LON = (-125.0, -66.0)
COARSE_STEP = 1.0

# High-density metro bounding boxes — 0.5° sub-grid inside these
DENSE_BOXES = [
    # (lat_min, lat_max, lon_min, lon_max, label)
    (40.4, 41.4, -74.5, -71.8, "NYC metro"),
    (38.8, 40.2, -75.6, -73.9, "NJ / Philly"),
    (41.6, 42.5, -87.9, -87.3, "Chicago"),
    (33.5, 34.5, -118.7, -117.5, "Los Angeles"),
    (25.5, 26.3, -80.5, -80.0, "Miami"),
    (37.3, 37.9, -122.6, -121.7, "San Francisco Bay"),
    (38.7, 39.2, -77.5, -76.8, "DC / Maryland"),
    (42.2, 42.5, -71.3, -70.9, "Boston"),
]
DENSE_STEP = 0.5


def _frange(start: float, stop: float, step: float):
    """Generate floats from start to stop (inclusive) in step increments."""
    v = start
    while v <= stop + 1e-9:
        yield round(v, 6)
        v += step


def build_grid() -> list[tuple[float, float]]:
    """Return sorted list of (lat, lon) grid points, coarse + dense."""
    points: set[tuple[float, float]] = set()

    # Coarse grid
    for lat in _frange(US_LAT[0], US_LAT[1], COARSE_STEP):
        for lon in _frange(US_LON[0], US_LON[1], COARSE_STEP):
            points.add((lat, lon))

    # Dense sub-grids (override coarse in these areas)
    for lat_min, lat_max, lon_min, lon_max, _ in DENSE_BOXES:
        # Remove coarse points inside this box
        to_remove = {
            p for p in points
            if lat_min <= p[0] <= lat_max and lon_min <= p[1] <= lon_max
        }
        points -= to_remove
        # Add dense points
        for lat in _frange(lat_min, lat_max, DENSE_STEP):
            for lon in _frange(lon_min, lon_max, DENSE_STEP):
                points.add((lat, lon))

    return sorted(points)


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_key() -> str:
    key = os.environ.get("PLACES_API_KEY", "")
    if not key:
        # Walk up from script location to find .env
        search = BASE
        for _ in range(6):
            env_file = search / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.startswith("PLACES_API_KEY="):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
                if key:
                    break
            search = search.parent
    if not key:
        sys.exit(
            "Error: PLACES_API_KEY not set. "
            "Export it in your environment or add PLACES_API_KEY=... to .env"
        )
    return key


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def search_nearby(lat: float, lon: float, key: str,
                  page_token: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body: dict = {
        "textQuery": "synagogue",
        "includedType": "synagogue",
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": float(SEARCH_RADIUS_M),
            }
        },
        "maxResultCount": MAX_PER_PAGE,
        "languageCode": "en",
    }
    if page_token:
        body["pageToken"] = page_token

    resp = requests.post(PLACES_URL, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ── Main loop ─────────────────────────────────────────────────────────────────

def main(max_points: int | None = None):
    key = _api_key()

    grid = build_grid()
    print(f"Grid: {len(grid)} points  ({COARSE_STEP}° coarse + {DENSE_STEP}° dense metro)", flush=True)

    # Load progress
    done: set[str] = set()
    if PROGRESS.exists():
        done = set(json.loads(PROGRESS.read_text()))
        print(f"Resuming: {len(done)} points already completed.", flush=True)

    # Load existing place IDs from raw file (deduplication)
    seen_ids: set[str] = set()
    if RAW_OUT.exists():
        with open(RAW_OUT) as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    seen_ids.add(obj["id"])
                except Exception:
                    pass
        print(f"Already have {len(seen_ids)} unique places in {RAW_OUT.name}.", flush=True)

    todo = [p for p in grid if f"{p[0]},{p[1]}" not in done]
    if max_points is not None:
        todo = todo[:max_points]

    print(f"Points to search: {len(todo)}", flush=True)

    new_places = 0
    api_calls   = 0

    with open(RAW_OUT, "a") as out_f:
        for i, (lat, lon) in enumerate(todo):
            point_key = f"{lat},{lon}"
            page_token = None
            point_new  = 0

            for page in range(MAX_PAGES):
                try:
                    data = search_nearby(lat, lon, key, page_token)
                    api_calls += 1
                except requests.HTTPError as e:
                    print(f"  [{i+1}/{len(todo)}] ({lat:.2f},{lon:.2f}) "
                          f"page {page}: HTTP error {e}", flush=True)
                    break
                except Exception as e:
                    print(f"  [{i+1}/{len(todo)}] ({lat:.2f},{lon:.2f}) "
                          f"page {page}: {e}", flush=True)
                    break

                places = data.get("places", [])
                for place in places:
                    pid = place.get("id", "")
                    if not pid or pid in seen_ids:
                        continue
                    # Strict distance filter — locationBias is loose, not restrictive
                    loc = place.get("location", {})
                    plat, plon = loc.get("latitude"), loc.get("longitude")
                    if plat is None or plon is None:
                        continue
                    if haversine_km(lat, lon, plat, plon) > SEARCH_RADIUS_KM:
                        continue
                    seen_ids.add(pid)
                    out_f.write(json.dumps(place) + "\n")
                    point_new += 1

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                time.sleep(DELAY_S)

            new_places += point_new

            done.add(point_key)
            PROGRESS.write_text(json.dumps(sorted(done)))

            if (i + 1) % 25 == 0 or point_new > 0:
                print(
                    f"  [{i+1:4d}/{len(todo)}] ({lat:6.2f},{lon:7.2f}) "
                    f"+{point_new:3d} new  total={len(seen_ids):5d}  calls={api_calls}",
                    flush=True,
                )

            time.sleep(DELAY_S)

    print(f"\nDone. {new_places} new places written to {RAW_OUT.name}.")
    print(f"Total unique places: {len(seen_ids)}")
    print(f"Total API calls: {api_calls}  "
          f"(est. cost ${api_calls * 0.035:.2f} at $35/1,000 Enterprise tier)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search Google Places for US synagogues")
    parser.add_argument(
        "--max-points", type=int, default=None,
        help="Limit to first N grid points (for smoke testing)",
    )
    args = parser.parse_args()
    main(max_points=args.max_points)
