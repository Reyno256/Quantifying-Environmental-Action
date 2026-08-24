"""
Re-scan live sitemap.xml for a sample of websites and extract the most recent
<lastmod> date. Compares against the last_modified value currently in the DB.

Input : sample_1000.txt  (id|url|db_last_modified, pipe-separated)
Output: sitemap_rescan_results.csv

Sitemap discovery per site:
  1. <root>/sitemap.xml
  2. Sitemap: directives in /robots.txt
For a <sitemapindex>, prefer the <lastmod> values listed inside the index
itself; only fetch child <urlset> sitemaps (capped) if the index carries none.
Handles gzipped sitemaps. Takes the max lastmod date seen.
"""

import csv
import gzip
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin

import requests
from requests.exceptions import RequestException

SESSION_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml,text/xml,*/*",
}

TIMEOUT = 12
MAX_CHILD_SITEMAPS = 5          # cap recursion into a sitemapindex
MAX_BYTES = 8 * 1024 * 1024     # don't read absurdly large sitemaps
WORKERS = 24

LASTMOD_RE = re.compile(r"<lastmod>\s*([^<\s]+)", re.IGNORECASE)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)", re.IGNORECASE)
IS_INDEX_RE = re.compile(r"<sitemapindex", re.IGNORECASE)
IS_SITEMAP_RE = re.compile(r"<(urlset|sitemapindex|loc)\b", re.IGNORECASE)


def _new_session():
    s = requests.Session()
    s.headers.update(SESSION_HEADERS)
    return s


def fetch_text(session, url):
    """Fetch a URL, gunzip if needed, return decoded text or None."""
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
    except RequestException:
        return None
    if r.status_code != 200:
        r.close()
        return None
    try:
        raw = r.raw.read(MAX_BYTES, decode_content=True)
    except Exception:
        r.close()
        return None
    finally:
        r.close()
    if not raw:
        return None
    if raw[:2] == b"\x1f\x8b" or url.lower().endswith(".gz"):
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def base_root(url):
    p = urlparse(url if "://" in url else "http://" + url)
    scheme = p.scheme or "http"
    return f"{scheme}://{p.netloc}"


def discover_sitemaps(session, root):
    """Return candidate sitemap URLs: root/sitemap.xml + robots.txt entries."""
    candidates = [urljoin(root + "/", "sitemap.xml")]
    robots = fetch_text(session, urljoin(root + "/", "robots.txt"))
    if robots:
        for line in robots.splitlines():
            if line.strip().lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm and sm not in candidates:
                    candidates.append(sm)
    return candidates


def lastmods_from_sitemap(session, url, depth=0):
    """Return (sitemap_present, lastmods) reachable from this sitemap URL."""
    text = fetch_text(session, url)
    if not text:
        return (False, [])
    present = bool(IS_SITEMAP_RE.search(text))
    lastmods = LASTMOD_RE.findall(text)
    if IS_INDEX_RE.search(text) and depth == 0:
        # It's an index. If it already lists lastmods, use them (cheap path).
        if lastmods:
            return (True, lastmods)
        # Otherwise fetch a capped number of child sitemaps.
        child_locs = LOC_RE.findall(text)[:MAX_CHILD_SITEMAPS]
        collected = []
        for loc in child_locs:
            _, child_lms = lastmods_from_sitemap(session, loc, depth + 1)
            collected.extend(child_lms)
        return (True, collected)
    return (present, lastmods)


def normalize_date(s):
    """Keep the date portion (YYYY-MM-DD...) for comparison; reject junk years."""
    s = s.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        # also accept bare YYYY-MM or YYYY
        m2 = re.match(r"(\d{4})", s)
        if m2 and 1995 <= int(m2.group(1)) <= 2027:
            return s
        return None
    yr = int(m.group(1))
    if yr < 1995 or yr > 2027:
        return None
    return s


def scan_site(row):
    sid, url, db_lm = row
    session = _new_session()
    result = {
        "id": sid, "url": url, "db_last_modified": db_lm,
        "live_last_modified": "", "sitemap_url": "", "status": "",
    }
    try:
        root = base_root(url)
        candidates = discover_sitemaps(session, root)
        best = None
        best_sm = ""
        any_sitemap = False
        for sm in candidates:
            present, lms = lastmods_from_sitemap(session, sm)
            if present:
                any_sitemap = True
            for raw in lms:
                nd = normalize_date(raw)
                if nd and (best is None or nd > best):
                    best = nd
                    best_sm = sm
        if best:
            result["live_last_modified"] = best
            result["sitemap_url"] = best_sm
            result["status"] = "ok"
        elif any_sitemap:
            result["status"] = "sitemap_no_lastmod"
        else:
            result["status"] = "no_sitemap"
    except Exception as e:
        result["status"] = f"error:{type(e).__name__}"
    finally:
        session.close()
    return result


def main():
    in_path = sys.argv[1]
    out_path = sys.argv[2]
    rows = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 3:
                parts = parts + [""] * (3 - len(parts))
            rows.append((parts[0], parts[1], parts[2]))

    print(f"Scanning {len(rows)} sites with {WORKERS} workers…", flush=True)
    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(scan_site, r): r for r in rows}
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 50 == 0:
                ok = sum(1 for x in results if x["status"] == "ok")
                print(f"  {done}/{len(rows)} — {ok} with live lastmod", flush=True)

    results.sort(key=lambda x: int(x["id"]) if x["id"].isdigit() else 0)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "url", "db_last_modified", "live_last_modified",
            "sitemap_url", "status"])
        w.writeheader()
        w.writerows(results)

    # summary
    from collections import Counter
    statuses = Counter(x["status"] for x in results)
    ok = statuses.get("ok", 0)
    print(f"\nDone. {ok}/{len(rows)} sites returned a live <lastmod>.")
    print("Status breakdown:")
    for s, c in statuses.most_common():
        print(f"  {s}: {c}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
