"""
Crawl a targeted subset of synagogue websites and log per-site crawl outcomes.

Unlike fetch_from_commoncrawl.py (which crawls the whole combined list and only
counts failures), this crawls just the domains named in a list file and writes a
results manifest recording, for each site:

    domain, url, out_dir, pages_saved, pages_failed, has_error, error_text

`has_error` is TRUE when the crawl produced zero usable pages; `error_text` is the
reason the root page could not be fetched (exception, non-200 status, or non-HTML
content type). load_crawl_results.py consumes this manifest to insert the new
web_pages and populate websites.has_error / websites.error_text.

Usage:
    python 05_web_scraping/crawl_missing.py DOMAINS_FILE [MANIFEST_CSV]

DOMAINS_FILE  newline-separated normalized domains (e.g. 'bethshalom.org')
MANIFEST_CSV  output path (default: 05_web_scraping/crawl_results.csv)

HTML is written to html_pages/<domain>/ exactly as fetch_from_commoncrawl.py does,
so the existing loader (load_data.py) can also read it.
"""

import csv
import re
import sys
import time
import urllib.parse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from requests.exceptions import RequestException

import fetch_from_commoncrawl as fc

HERE        = Path(__file__).parent
COMBINED_CSV = HERE.parent / "04_combined" / "synagogues_combined.csv"
DEFAULT_MANIFEST = HERE / "crawl_results.csv"


def normalize_domain(url: str) -> str:
    """Match 08_database/load_data.py exactly so manifest domains == websites.domain."""
    try:
        host = urllib.parse.urlparse(url).netloc or url
        return re.sub(r"^www\.", "", host).lower().rstrip("/")
    except Exception:
        return url


def crawl_site_logged(start_url: str) -> tuple[str, int, int, str | None]:
    """BFS-crawl a site; return (out_dir_name, saved, failed, error_text).

    error_text is the reason the root page failed, surfaced only when the whole
    crawl yields zero saved pages.
    """
    domain   = fc.domain_of(start_url)
    site_dir = fc.safe_dir(domain)
    site_dir.mkdir(parents=True, exist_ok=True)

    queue      = deque([start_url])
    visited    = set()
    saved      = 0
    failed     = 0
    root_error = None   # reason the first (root) fetch failed

    while queue and (saved + failed) < fc.MAX_PAGES_PER_SITE:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        is_root = (url == start_url)

        out_path = fc.url_to_path(site_dir, url)
        if out_path.exists():           # resume: already downloaded
            saved += 1
            continue

        try:
            r  = fc.SESSION.get(url, timeout=fc.TIMEOUT, allow_redirects=True)
            ct = r.headers.get("Content-Type", "")
            if r.status_code != 200:
                failed += 1
                if is_root:
                    root_error = f"HTTP {r.status_code}"
                time.sleep(fc.REQUEST_DELAY)
                continue
            if "text/html" not in ct:
                failed += 1
                if is_root:
                    root_error = f"non-HTML Content-Type: {ct[:80] or 'unknown'}"
                time.sleep(fc.REQUEST_DELAY)
                continue
            html = r.text
        except RequestException as e:
            failed += 1
            if is_root:
                root_error = f"{type(e).__name__}: {str(e)[:200]}"
            time.sleep(fc.REQUEST_DELAY)
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        saved += 1

        for link in fc.extract_links(html, url, domain):
            if link not in visited:
                queue.append(link)
        time.sleep(fc.REQUEST_DELAY)

    error_text = None
    if saved == 0:
        error_text = root_error or "no pages saved"
    return site_dir.name, saved, failed, error_text


def load_domain_to_url() -> dict[str, str]:
    """Map normalized domain -> first website URL found in the combined CSV."""
    mapping: dict[str, str] = {}
    with open(COMBINED_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            url = (r.get("website") or "").strip()
            if not url:
                continue
            dom = normalize_domain(url)
            mapping.setdefault(dom, url)
    return mapping


def task(args):
    idx, total, domain, url = args
    try:
        out_dir, saved, failed, err = crawl_site_logged(url)
    except Exception as e:                       # never let one site kill the pool
        out_dir, saved, failed, err = fc.safe_dir(fc.domain_of(url)).name, 0, 0, \
            f"{type(e).__name__}: {str(e)[:200]}"
    has_error = saved == 0
    flag = "ERR" if has_error else "ok "
    msg = f"[{idx}/{total}] {flag} {domain}: {saved} saved, {failed} failed" \
          + (f" — {err}" if err else "")
    return {
        "domain": domain, "url": url, "out_dir": out_dir,
        "pages_saved": saved, "pages_failed": failed,
        "has_error": has_error, "error_text": err or "",
    }, msg


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    domains_file = Path(sys.argv[1])
    manifest_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MANIFEST

    wanted = [d.strip().lower() for d in domains_file.read_text().splitlines() if d.strip()]
    dom_to_url = load_domain_to_url()

    tasks, unresolved = [], []
    for d in wanted:
        url = dom_to_url.get(d)
        if url:
            tasks.append(d)
        else:
            unresolved.append(d)
    if unresolved:
        print(f"WARNING: {len(unresolved)} domains not found in combined CSV "
              f"(skipped): {unresolved[:10]}{'…' if len(unresolved) > 10 else ''}",
              flush=True)

    total = len(tasks)
    print(f"Crawling {total} sites with {fc.WORKERS} workers "
          f"(of {len(wanted)} requested)…", flush=True)

    args = [(i + 1, total, d, dom_to_url[d]) for i, d in enumerate(tasks)]
    results, done, errors, pages = [], 0, 0, 0
    with ThreadPoolExecutor(max_workers=fc.WORKERS) as pool:
        futures = [pool.submit(task, a) for a in args]
        for fut in as_completed(futures):
            row, msg = fut.result()
            results.append(row)
            done += 1
            pages += row["pages_saved"]
            errors += int(row["has_error"])
            print(msg, flush=True)
            if done % 50 == 0:
                print(f"  ── {done}/{total} done │ {pages} pages │ {errors} errored ──",
                      flush=True)

    results.sort(key=lambda r: r["domain"])
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "url", "out_dir",
                                          "pages_saved", "pages_failed",
                                          "has_error", "error_text"])
        w.writeheader()
        w.writerows(results)

    print(f"\n{'─'*55}", flush=True)
    print(f"Sites crawled        : {total}", flush=True)
    print(f"Sites with pages     : {total - errors}", flush=True)
    print(f"Sites with has_error : {errors}", flush=True)
    print(f"Total pages saved    : {pages}", flush=True)
    print(f"Manifest written to  : {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
