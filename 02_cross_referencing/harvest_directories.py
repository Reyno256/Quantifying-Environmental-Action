"""
Scrape denomination directories to populate directory_harvest.csv.

Currently implemented:
  - URJ (Union for Reform Judaism) — ~800 US congregations, ~80 pages

Run from any directory; output lands in the same folder as this script.
"""

import csv
import re
import time
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
OUT_PATH = BASE / "directory_harvest.csv"

BROWSER = {"browser": "chrome", "platform": "darwin", "mobile": False}

FIELDNAMES = ["name", "city", "state_code", "website", "source"]


# ── helpers ───────────────────────────────────────────────────────────────────

def parse_us_address(text: str) -> tuple[str, str]:
    """Return (city, state_code) from a US address string, or ('', '').

    Handles both legacy 'City, ST 12345' and current 'City, ST, 12345-XXXX, US' formats.
    """
    # Current URJ format: "Street, City, ST, XXXXX-XXXX, US"
    # Also handles: "City, ST 12345" and "City, ST 12345-1234"
    m = re.search(r",\s+([A-Za-z][A-Za-z .'`\-]*),\s+([A-Z]{2})(?:[,\s]|$)", text)
    if m:
        return m.group(1).strip(), m.group(2)
    return "", ""


# ── URJ ───────────────────────────────────────────────────────────────────────

URJ_BASE = "https://urj.org/urj-congregations-communities"


def _scrape_urj_page(session, page: int) -> list[dict]:
    try:
        r = session.get(f"{URJ_BASE}?page={page}", timeout=30)
        if r.status_code != 200:
            print(f"  [URJ] page {page}: HTTP {r.status_code}", flush=True)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  [URJ] page {page}: {e}", flush=True)
        return []

    records = []
    for entry in soup.select(".views-row"):
        name_tag = entry.select_one(".views-field-congregation h3 a")
        if not name_tag:
            continue
        name = name_tag.get_text(strip=True)

        city, state_code = "", ""
        addr_tag = entry.select_one(".views-field-country .field-content")
        if addr_tag:
            city, state_code = parse_us_address(addr_tag.get_text(" ", strip=True))

        # Only keep US entries (state code present)
        if not state_code:
            continue

        web_tag = entry.select_one(".views-field-website a[href]")
        website = web_tag["href"].strip() if web_tag else ""

        # Skip back-links that point to the URJ site itself
        if website and "urj.org" in website:
            website = ""

        records.append({
            "name":       name,
            "city":       city,
            "state_code": state_code,
            "website":    website,
            "source":     "URJ",
        })
    return records


def scrape_urj(max_pages: int = 90) -> list[dict]:
    print("Scraping URJ congregations directory…", flush=True)
    session = cloudscraper.create_scraper(browser=BROWSER)

    # Warm the session cookie with a homepage visit
    try:
        session.get("https://urj.org/", timeout=20)
        time.sleep(2)
    except Exception:
        pass

    all_records: list[dict] = []
    consecutive_empty = 0

    for page in range(max_pages):
        recs = _scrape_urj_page(session, page)
        if recs:
            all_records.extend(recs)
            consecutive_empty = 0
            print(f"  [URJ] page {page:3d}: {len(recs):3d} US entries  "
                  f"(running total {len(all_records)})", flush=True)
        else:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                print(f"  [URJ] two empty pages in a row at page {page} — done.", flush=True)
                break

        time.sleep(1.5)

    with_web = sum(1 for r in all_records if r["website"])
    print(f"URJ total: {len(all_records)} US congregations, {with_web} with websites.\n",
          flush=True)
    return all_records


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    all_records: list[dict] = []

    # URJ
    all_records.extend(scrape_urj())

    # Write output
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_records)

    total      = len(all_records)
    with_web   = sum(1 for r in all_records if r["website"])
    print(f"{'─'*50}")
    print(f"Total records written : {total}")
    print(f"With websites         : {with_web}")
    print(f"Output                : {OUT_PATH}")

    print("\nSample (first 10 with websites):")
    for r in [r for r in all_records if r["website"]][:10]:
        print(f"  [{r['source']:4s}] {r['name'][:40]:40s}  {r['city']}, {r['state_code']}  →  {r['website']}")


if __name__ == "__main__":
    main()
