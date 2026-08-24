"""
Convert google_places_raw.jsonl → google_places_synagogues.csv

Input:  google_places_raw.jsonl  (written by search_synagogues.py)
Output: google_places_synagogues.csv

Columns match 02_cross_referencing/synagogues_us_enriched.csv for easy comparison:
  place_id, name, address, city, state, lat, lon, website, business_status
"""

import csv
import json
import re
from pathlib import Path

BASE = Path(__file__).parent

RAW_IN  = BASE / "google_places_raw.jsonl"
CSV_OUT = BASE / "google_places_synagogues.csv"

FIELDNAMES = [
    "place_id", "name", "address", "city", "state",
    "lat", "lon", "website", "business_status",
]

STATE_ABBRS = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
}

NAME_TO_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC",
}


def parse_address(formatted: str) -> tuple[str, str, str]:
    """
    Return (city, state_code, address_line) from a Google-formatted address.

    Google format: "Street, City, ST ZIPCODE, USA"
    or:            "City, ST ZIPCODE, USA"
    """
    # Strip country suffix
    addr = re.sub(r",?\s*USA\s*$", "", formatted.strip())

    # Match "..., City, ST 12345" — state is a 2-letter code before zip
    m = re.search(r",\s+([^,]+),\s+([A-Z]{2})\s+\d{5}(?:-\d{4})?$", addr)
    if m:
        city       = m.group(1).strip()
        state_code = m.group(2)
        return city, state_code, formatted

    # Fallback: match full state name
    for name, code in NAME_TO_CODE.items():
        if name in formatted:
            m2 = re.search(rf",\s+([^,]+),\s+{re.escape(name)}", formatted)
            if m2:
                return m2.group(1).strip(), code, formatted
            return "", code, formatted

    return "", "", formatted


def main():
    if not RAW_IN.exists():
        print(f"Error: {RAW_IN} not found — run search_synagogues.py first.")
        return

    rows = []
    skipped_non_us = 0

    with open(RAW_IN) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                place = json.loads(line)
            except json.JSONDecodeError:
                continue

            place_id    = place.get("id", "")
            name        = place.get("displayName", {}).get("text", "")
            address     = place.get("formattedAddress", "")
            lat         = place.get("location", {}).get("latitude", "")
            lon         = place.get("location", {}).get("longitude", "")
            website     = place.get("websiteUri", "")
            status      = place.get("businessStatus", "")

            city, state, _ = parse_address(address)

            # Filter to US only
            if not state:
                skipped_non_us += 1
                continue

            rows.append({
                "place_id":        place_id,
                "name":            name,
                "address":         address,
                "city":            city,
                "state":           state,
                "lat":             lat,
                "lon":             lon,
                "website":         website,
                "business_status": status,
            })

    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    total    = len(rows)
    with_web = sum(1 for r in rows if r["website"])
    active   = sum(1 for r in rows if r["business_status"] == "OPERATIONAL")

    from collections import Counter
    states = Counter(r["state"] for r in rows)

    print(f"Written {total} US synagogues to {CSV_OUT.name}")
    print(f"  With websites     : {with_web} ({with_web/total*100:.1f}%)" if total else "")
    print(f"  OPERATIONAL       : {active}")
    print(f"  Skipped (non-US)  : {skipped_non_us}")
    print(f"\nTop states:")
    for state, count in states.most_common(10):
        pct = count / total * 100
        print(f"  {state}: {count} ({pct:.1f}%)")


if __name__ == "__main__":
    main()
