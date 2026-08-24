"""
Fallback last-updated scanner for sites that had no sitemap.xml.

For each site (read from sitemap_rescan_results.csv, status == 'no_sitemap'),
try several alternative sources of a "last updated" date and record which
source produced each one, plus the best (most recent) date overall:

  1. HTTP Last-Modified header (HEAD, then GET)
  2. Homepage HTML metadata:
       - JSON-LD  dateModified / datePublished
       - <meta>   article:modified_time, og:updated_time, last-modified, date, revised
  3. RSS / Atom feed: lastBuildDate / <updated> / newest <pubDate>
       (discovered via <link rel=alternate> + common feed paths)
  4. WordPress REST API: /wp-json/wp/v2/posts?orderby=modified

Input : sitemap_rescan_results.csv
Output: sitemap_rescan_fallback.csv
"""

import csv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from requests.exceptions import RequestException

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
# (connect, read) timeouts — keep connect short to skip dead hosts fast.
TIMEOUT = (5, 7)
MAX_BYTES = 4 * 1024 * 1024
WORKERS = 40

FEED_PATHS = ["/feed/", "/rss.xml", "/index.xml", "/atom.xml"]
META_KEYS = {"article:modified_time", "og:updated_time", "last-modified",
             "revised", "date", "dcterms.modified", "dc.date.modified"}

META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r'(\w[\w:.\-]*)\s*=\s*"([^"]*)"', re.IGNORECASE)
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)
FEED_LINK_RE = re.compile(
    r'<link\b[^>]*type=["\']application/(?:rss|atom)\+xml["\'][^>]*>',
    re.IGNORECASE)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
TAG_DATE_RE = {
    "lastBuildDate": re.compile(r"<lastBuildDate>\s*([^<]+)", re.IGNORECASE),
    "updated":       re.compile(r"<updated>\s*([^<]+)", re.IGNORECASE),
    "pubDate":       re.compile(r"<pubDate>\s*([^<]+)", re.IGNORECASE),
}


def new_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def root_of(url):
    p = urlparse(url if "://" in url else "http://" + url)
    return f"{p.scheme or 'http'}://{p.netloc}"


def get(session, url, method="GET"):
    """Return (status_code, headers, text). text is '' for HEAD/non-200."""
    try:
        r = session.request(method, url, timeout=TIMEOUT,
                            allow_redirects=True, stream=True)
    except RequestException:
        return (None, {}, "")
    headers = dict(r.headers)
    if method == "HEAD" or r.status_code != 200:
        r.close()
        return (r.status_code, headers, "")
    try:
        raw = r.raw.read(MAX_BYTES, decode_content=True) or b""
    except Exception:
        raw = b""
    finally:
        r.close()
    return (r.status_code, headers, raw.decode("utf-8", errors="replace"))


def norm_date(value):
    """Parse many date formats → 'YYYY-MM-DD'; reject implausible years."""
    if not value:
        return None
    s = value.strip()
    dt = None
    # RFC 822 / 1123 (HTTP header, RSS pubDate)
    if re.search(r"[A-Za-z]{3},?\s", s):
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            dt = None
    if dt is None:
        # ISO 8601 (allow trailing Z)
        iso = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
            if m:
                try:
                    dt = datetime(int(m[1]), int(m[2]), int(m[3]))
                except ValueError:
                    dt = None
    if dt is None:
        return None
    yr = dt.year
    if yr < 1995 or yr > 2027:
        return None
    return dt.strftime("%Y-%m-%d")


def _walk_jsonld(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in ("datemodified", "datepublished") and isinstance(v, str):
                out.append(v)
            else:
                _walk_jsonld(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _walk_jsonld(it, out)


def dates_from_homepage(html):
    """Return best meta/JSON-LD date from homepage HTML."""
    cands = []
    # JSON-LD
    for block in JSONLD_RE.findall(html):
        block = block.strip()
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        _walk_jsonld(data, cands)
    # <meta> tags
    for tag in META_RE.findall(html):
        attrs = {k.lower(): v for k, v in ATTR_RE.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or
               attrs.get("itemprop") or "").lower()
        if key in META_KEYS and attrs.get("content"):
            cands.append(attrs["content"])
    best = None
    for c in cands:
        d = norm_date(c)
        if d and (best is None or d > best):
            best = d
    return best


def dates_from_feed_text(text):
    best = None
    for rgx in TAG_DATE_RE.values():
        for raw in rgx.findall(text):
            d = norm_date(raw)
            if d and (best is None or d > best):
                best = d
    return best


def feed_date(session, root, homepage_html):
    urls = []
    if homepage_html:
        for link in FEED_LINK_RE.findall(homepage_html):
            m = HREF_RE.search(link)
            if m:
                urls.append(urljoin(root + "/", m.group(1)))
    for p in FEED_PATHS:
        urls.append(urljoin(root + "/", p.lstrip("/")))
    seen = set()
    best = None
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        _, _, text = get(session, u)
        if text and ("<rss" in text[:500].lower() or "<feed" in text[:500].lower()
                     or "<lastbuilddate" in text.lower() or "<pubdate" in text.lower()):
            d = dates_from_feed_text(text)
            if d and (best is None or d > best):
                best = d
        if best:   # one good feed is enough
            break
    return best


def wp_api_date(session, root):
    url = urljoin(root + "/", "wp-json/wp/v2/posts?orderby=modified&order=desc&per_page=1")
    _, _, text = get(session, url)
    if not text:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return norm_date(data[0].get("modified_gmt") or data[0].get("modified") or "")
    return None


def scan(row):
    sid, url, db_lm = row
    s = new_session()
    res = {"id": sid, "url": url, "db_last_modified": db_lm,
           "http_last_modified": "", "meta_date": "", "feed_date": "",
           "wp_date": "", "best_date": "", "best_source": "", "status": ""}
    try:
        root = root_of(url)
        # 1. HTTP Last-Modified header (HEAD then GET), also grab homepage HTML
        homepage_html = ""
        for method in ("HEAD", "GET"):
            code, headers, text = get(s, url, method)
            lm = headers.get("Last-Modified") or headers.get("last-modified")
            if method == "GET":
                homepage_html = text
            if lm:
                res["http_last_modified"] = norm_date(lm) or ""
                break
            if method == "HEAD" and code in (405, 501):
                continue  # HEAD not allowed → GET
        if not homepage_html:
            _, _, homepage_html = get(s, url, "GET")

        # If the host won't even serve its homepage and gave no header,
        # it's effectively dead — skip the extra feed/WordPress probes.
        host_alive = bool(homepage_html) or bool(res["http_last_modified"])

        # 2. homepage meta / JSON-LD
        if homepage_html:
            res["meta_date"] = dates_from_homepage(homepage_html) or ""
        if host_alive:
            # 3. RSS / Atom feed
            res["feed_date"] = feed_date(s, root, homepage_html) or ""
            # 4. WordPress API
            res["wp_date"] = wp_api_date(s, root) or ""
        else:
            res["status"] = "dead_host"

        # best = most recent across the content-meaningful sources;
        # HTTP Last-Modified used only if nothing else found.
        ranked = [("meta", res["meta_date"]), ("feed", res["feed_date"]),
                  ("wp", res["wp_date"])]
        best, best_src = None, ""
        for src, d in ranked:
            if d and (best is None or d > best):
                best, best_src = d, src
        if not best and res["http_last_modified"]:
            best, best_src = res["http_last_modified"], "http_header"
        if best:
            res["best_date"] = best
            res["best_source"] = best_src
            res["status"] = "found"
        elif res["status"] != "dead_host":
            res["status"] = "no_date"
    except Exception as e:
        res["status"] = f"error:{type(e).__name__}"
    finally:
        s.close()
    return res


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "sitemap_rescan_results.csv"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "sitemap_rescan_fallback.csv"
    rows = []
    with open(in_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["status"] == "no_sitemap":
                rows.append((r["id"], r["url"], r["db_last_modified"]))

    print(f"Fallback scan of {len(rows)} no-sitemap sites ({WORKERS} workers)…",
          flush=True)
    results, done = [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(scan, r): r for r in rows}
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 50 == 0:
                got = sum(1 for x in results if x["status"] == "found")
                print(f"  {done}/{len(rows)} — {got} with a date", flush=True)

    results.sort(key=lambda x: int(x["id"]) if x["id"].isdigit() else 0)
    cols = ["id", "url", "db_last_modified", "http_last_modified", "meta_date",
            "feed_date", "wp_date", "best_date", "best_source", "status"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)

    from collections import Counter
    found = sum(1 for x in results if x["status"] == "found")
    src = Counter(x["best_source"] for x in results if x["status"] == "found")
    print(f"\nDone. {found}/{len(rows)} no-sitemap sites yielded a date.")
    print("Best-source breakdown:")
    for srcname, c in src.most_common():
        print(f"  {srcname}: {c}")
    print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
