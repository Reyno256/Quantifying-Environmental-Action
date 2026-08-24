"""
Load the output of 05_web_scraping/crawl_missing.py into the database.

  1. Insert newly-crawled HTML pages into web_pages (with extracted content_text).
  2. Set websites.has_error / websites.error_text from the crawl manifest.
  3. Re-assert has_error = FALSE for every site that has >=1 web_page, so
     "has pages => success" always holds.

All inserts use ON CONFLICT DO NOTHING (additive; safe to re-run). Requires the
has_error / error_text columns (run migrate_add_crawl_errors.py first).

Usage:
    python 08_database/load_crawl_results.py [MANIFEST_CSV]

MANIFEST_CSV defaults to 05_web_scraping/crawl_results.csv
"""

import csv
import sys
from pathlib import Path

# reuse the canonical DB connection, text extractor, and bulk-insert helper
import load_data as ld

ROOT          = Path(__file__).parent.parent
HTML_DIR      = ROOT / "05_web_scraping" / "html_pages"
DEFAULT_MANIFEST = ROOT / "05_web_scraping" / "crawl_results.csv"


def main():
    manifest = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MANIFEST
    if not manifest.exists():
        sys.exit(f"Manifest not found: {manifest}")

    rows = list(csv.DictReader(open(manifest, encoding="utf-8")))
    print(f"Manifest: {len(rows)} sites from {manifest}", flush=True)

    conn = ld.get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT domain, id FROM websites")
    domain_to_id = {d: i for d, i in cur.fetchall()}

    # ── 1. insert newly-crawled pages ────────────────────────────────────────
    inserted, no_dir, unmatched = 0, [], []
    batch = []
    for r in rows:
        if r["has_error"].lower() == "true":
            continue
        domain = r["domain"]
        w_id = domain_to_id.get(domain)
        if not w_id:
            unmatched.append(domain)
            continue
        site_dir = HTML_DIR / r["out_dir"]
        if not site_dir.exists():
            no_dir.append(domain)
            continue
        for html_file in site_dir.rglob("*.html"):
            page_path = str(html_file.relative_to(HTML_DIR))
            batch.append({
                "website_id":       w_id,
                "page_path":        page_path,
                "has_env_keywords": False,
                "content_text":     ld.extract_text(html_file),
            })
            if len(batch) >= 500:
                ld.bulk_insert(cur, "web_pages", batch)
                inserted += len(batch)
                batch.clear()
                print(f"  {inserted} pages inserted…", flush=True)
    if batch:
        ld.bulk_insert(cur, "web_pages", batch)
        inserted += len(batch)
    conn.commit()
    print(f"web_pages insert attempted for {inserted} files "
          f"(ON CONFLICT DO NOTHING dedupes re-runs).", flush=True)
    if unmatched:
        print(f"WARNING: {len(unmatched)} manifest domains not in websites table: "
              f"{unmatched[:10]}{'…' if len(unmatched) > 10 else ''}", flush=True)
    if no_dir:
        print(f"NOTE: {len(no_dir)} success rows had no html dir on disk: "
              f"{no_dir[:10]}{'…' if len(no_dir) > 10 else ''}", flush=True)

    # ── 2. write crawl-outcome columns from the manifest ─────────────────────
    updated = 0
    for r in rows:
        has_error = r["has_error"].lower() == "true"
        err_text  = (r["error_text"].strip() or None) if has_error else None
        cur.execute(
            "UPDATE websites SET has_error = %s, error_text = %s WHERE domain = %s",
            (has_error, err_text, r["domain"]),
        )
        updated += cur.rowcount
    conn.commit()
    print(f"Set has_error/error_text on {updated} website rows from manifest.", flush=True)

    # ── 3. safety net: any site with pages is a success ──────────────────────
    cur.execute("""
        UPDATE websites w
           SET has_error = FALSE, error_text = NULL
         WHERE EXISTS (SELECT 1 FROM web_pages p WHERE p.website_id = w.id)
           AND (has_error IS TRUE OR error_text IS NOT NULL)
    """)
    conn.commit()
    print(f"Re-asserted success for {cur.rowcount} sites that have web_pages.", flush=True)

    # ── verification ─────────────────────────────────────────────────────────
    cur.execute("SELECT count(*) FILTER (WHERE has_error), count(*) FROM websites")
    err, total = cur.fetchone()
    cur.execute("SELECT count(DISTINCT website_id) FROM web_pages")
    with_pages = cur.fetchone()[0]
    print("\n── Verification ──────────────────────────────────", flush=True)
    print(f"  websites total            {total:>8,}", flush=True)
    print(f"  websites has_error=TRUE   {err:>8,}", flush=True)
    print(f"  websites with web_pages   {with_pages:>8,}", flush=True)

    cur.close()
    conn.close()
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
