import requests
import json
import csv
import time

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

QUERY = """
[out:json][timeout:120];
area["ISO3166-1"="US"][admin_level=2]->.usa;
(
  node["amenity"="place_of_worship"]["religion"="jewish"](area.usa);
  way["amenity"="place_of_worship"]["religion"="jewish"](area.usa);
  relation["amenity"="place_of_worship"]["religion"="jewish"](area.usa);
);
out center tags;
"""

def fetch_synagogues():
    headers = {"User-Agent": "synagogue-research/1.0 (educational)"}
    for url in OVERPASS_ENDPOINTS:
        print(f"Trying {url} ...")
        try:
            resp = requests.post(url, data={"data": QUERY}, headers=headers, timeout=150)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"  Failed: {e}")
            time.sleep(5)
    raise RuntimeError("All Overpass endpoints failed.")

def extract_records(data):
    records = []
    for elem in data.get("elements", []):
        tags = elem.get("tags", {})
        name = tags.get("name", "")
        website = (
            tags.get("website")
            or tags.get("contact:website")
            or tags.get("url")
            or ""
        )
        city = tags.get("addr:city", "")
        state = tags.get("addr:state", "")
        denomination = tags.get("denomination", "")

        if not name:
            continue

        # Get coordinates
        if elem["type"] == "node":
            lat, lon = elem.get("lat"), elem.get("lon")
        else:
            center = elem.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")

        records.append({
            "name": name,
            "website": website,
            "city": city,
            "state": state,
            "denomination": denomination,
            "lat": lat,
            "lon": lon,
            "osm_type": elem["type"],
            "osm_id": elem["id"],
        })
    return records

def main():
    data = fetch_synagogues()
    records = extract_records(data)

    print(f"Found {len(records)} synagogues total.")
    with_website = [r for r in records if r["website"]]
    print(f"  {len(with_website)} have a website listed.")

    # Write all synagogues
    all_path = "synagogues_us_all.csv"
    with open(all_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "website", "city", "state", "denomination", "lat", "lon", "osm_type", "osm_id"])
        writer.writeheader()
        writer.writerows(records)
    print(f"All synagogues written to {all_path}")

    # Write only those with websites
    web_path = "synagogues_us_with_websites.csv"
    with open(web_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "website", "city", "state", "denomination", "lat", "lon", "osm_type", "osm_id"])
        writer.writeheader()
        writer.writerows(with_website)
    print(f"Synagogues with websites written to {web_path}")

    # Print a sample
    print("\nSample (first 10 with websites):")
    for r in with_website[:10]:
        loc = f"{r['city']}, {r['state']}" if r['city'] or r['state'] else "location unknown"
        print(f"  {r['name']} ({loc}) — {r['website']}")

if __name__ == "__main__":
    main()
