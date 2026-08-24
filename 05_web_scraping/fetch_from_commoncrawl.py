"""
Crawl each synagogue website and save all internal HTML pages.

For each site in commoncrawl_results.csv:
  - BFS-crawl all pages on the same domain (up to MAX_PAGES_PER_SITE)
  - Save each page to html_pages/<domain>/<path>.html
  - Skip already-downloaded files (safe to resume)

Respects robots.txt is intentionally skipped for academic research —
add 'Crawl-Delay' sleep if you want to be extra polite.
"""

import csv
import hashlib
import os
import re
import time
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

# ── config ────────────────────────────────────────────────────────────────────

HERE               = Path(__file__).parent
OUT_DIR            = HERE / "html_pages"
INPUT_CSV          = HERE.parent / "04_combined" / "synagogues_combined.csv"
MAX_PAGES_PER_SITE = 150    # max pages per synagogue site
REQUEST_DELAY      = 0.2    # seconds between requests within a site
TIMEOUT            = 10     # per-request timeout
WORKERS            = 10     # concurrent site crawlers

SKIP_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
    ".mp3", ".mp4", ".zip", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".css", ".js", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".map",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

# ── helpers ───────────────────────────────────────────────────────────────────

def domain_of(url: str) -> str:
    return re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc.lower())


def safe_dir(domain: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)[:80]
    return OUT_DIR / safe


def url_to_path(base_domain_dir: Path, url: str) -> Path:
    """Map a URL to a filesystem path inside base_domain_dir."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/") or "index"
    # Strip bad characters
    path = re.sub(r"[^a-zA-Z0-9/._-]", "_", path)
    # If path has a skip extension, replace it
    stem, ext = os.path.splitext(path)
    if ext.lower() in SKIP_EXTENSIONS:
        path = stem
    if not path.endswith(".html"):
        path = path.rstrip("/") + ".html"
    # Flatten to avoid deeply nested dirs (hash query string into filename)
    if parsed.query:
        q_hash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        path = path[:-5] + f"_{q_hash}.html"
    return base_domain_dir / path


def extract_links(html: str, base_url: str, domain: str) -> list[str]:
    """Return absolute internal links from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        try:
            abs_url = urllib.parse.urljoin(base_url, href)
        except ValueError:
            continue
        # Strip fragment
        abs_url = abs_url.split("#")[0]
        # Only follow same domain
        if domain_of(abs_url) != domain:
            continue
        # Skip non-HTML extensions
        ext = os.path.splitext(urllib.parse.urlparse(abs_url).path)[1].lower()
        if ext in SKIP_EXTENSIONS:
            continue
        links.append(abs_url)
    return links


def crawl_site(start_url: str) -> tuple[int, int]:
    """BFS-crawl a site; return (pages_saved, pages_failed)."""
    domain = domain_of(start_url)
    site_dir = safe_dir(domain)
    site_dir.mkdir(parents=True, exist_ok=True)

    queue    = deque([start_url])
    visited  = set()
    saved    = 0
    failed   = 0

    while queue and (saved + failed) < MAX_PAGES_PER_SITE:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        out_path = url_to_path(site_dir, url)

        # Resume: skip if already saved
        if out_path.exists():
            saved += 1
            continue

        try:
            r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
            ct = r.headers.get("Content-Type", "")
            if "text/html" not in ct:
                failed += 1
                time.sleep(REQUEST_DELAY)
                continue
            if r.status_code != 200:
                failed += 1
                time.sleep(REQUEST_DELAY)
                continue
            html = r.text
        except RequestException:
            failed += 1
            time.sleep(REQUEST_DELAY)
            continue

        # Save
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        saved += 1

        # Enqueue new internal links
        for link in extract_links(html, url, domain):
            if link not in visited:
                queue.append(link)

        time.sleep(REQUEST_DELAY)

    return saved, failed


# ── main ──────────────────────────────────────────────────────────────────────

def crawl_site_task(args):
    idx, total, row = args
    url   = row.get("website", "").strip()
    name  = row.get("name", "")[:35]
    state = row.get("state", "")
    if not url:
        return idx, 0, 0, ""
    domain   = domain_of(url)
    site_dir = safe_dir(domain)
    existing = len(list(site_dir.rglob("*.html"))) if site_dir.exists() else 0
    if existing >= MAX_PAGES_PER_SITE:
        return idx, existing, 0, f"[{idx}/{total}] {name} ({state}) — {domain}: already {existing} pages, skipped"
    saved, failed = crawl_site(url)
    return idx, saved, failed, f"[{idx}/{total}] {name} ({state}) — {domain}: {saved} saved, {failed} failed"


def main():
    OUT_DIR.mkdir(exist_ok=True)

    rows = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("website", "").strip()]

    print(f"Loaded {len(rows)} sites. Crawling with {WORKERS} workers…", flush=True)

    total_saved = total_failed = done = 0
    tasks = [(i + 1, len(rows), row) for i, row in enumerate(rows)]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(crawl_site_task, t): t for t in tasks}
        for fut in as_completed(futures):
            idx, saved, failed, msg = fut.result()
            total_saved  += saved
            total_failed += failed
            done         += 1
            if msg:
                print(msg, flush=True)
            if done % 50 == 0:
                print(f"  ── {done}/{len(rows)} sites done │ {total_saved} pages saved ──", flush=True)

    print(f"\n{'─'*50}", flush=True)
    print(f"Total HTML pages saved : {total_saved}", flush=True)
    print(f"Total fetch failures   : {total_failed}", flush=True)
    print(f"Output directory       : {OUT_DIR.resolve()}", flush=True)


if __name__ == "__main__":
    main()
