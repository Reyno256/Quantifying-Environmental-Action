"""
Scoped, additive keyword scan for a specific set of domains.

Unlike classify_environmental.py (which does a destructive full refresh —
DELETE FROM page_keyword_matches, cascading to llm_chunk_classifications),
this scans ONLY the pages of the domains named in a list file and inserts
their action spans without disturbing any other domain's data.

For each page (web_pages.content_text) of a target domain:
  - find keyword matches (same regex as keyword_search.load_keywords)
  - merge matches within MERGE_THRESHOLD chars into one action span
  - insert one page_keyword_matches row per span
  - set web_pages.has_env_keywords
  - upsert the domain's env_keyword_analysis row

Idempotent: existing matches for the target domains' pages are deleted first
(scoped), so re-running produces the same result. It never touches pages
outside the target domains.

Usage:
    python 06_environmental_classification/classify_environmental_scoped.py DOMAINS_FILE

DOMAINS_FILE  newline-separated normalized domains (matches websites.domain)
"""

import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from keyword_search import load_keywords           # noqa: E402

load_dotenv(ROOT / ".env")

MERGE_THRESHOLD = 15   # chars; identical to classify_environmental.py


def find_keyword_matches(text, pattern):
    """(keyword, start, end) for every hit — identical to keyword_search.py."""
    lower = text.lower()
    return [(m.group(0), m.start(), m.end()) for m in pattern.finditer(lower)]


def merge_nearby_matches(matches, threshold=MERGE_THRESHOLD):
    """Merge hits within *threshold* chars into one span (keywords joined)."""
    if not matches:
        return []
    merged = []
    cur_keywords = [matches[0][0]]
    cur_start, cur_end = matches[0][1], matches[0][2]
    for keyword, start, end in matches[1:]:
        if start - cur_end <= threshold:
            cur_keywords.append(keyword)
            cur_end = max(cur_end, end)
        else:
            merged.append((", ".join(cur_keywords), cur_start, cur_end))
            cur_keywords, cur_start, cur_end = [keyword], start, end
    merged.append((", ".join(cur_keywords), cur_start, cur_end))
    return merged


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    domains = sorted({d.strip().lower()
                      for d in Path(sys.argv[1]).read_text().splitlines() if d.strip()})
    print(f"Target domains: {len(domains)}", flush=True)

    pattern = load_keywords()
    conn = get_conn()
    cur  = conn.cursor()

    # Pages of the target domains that have content_text
    cur.execute("""
        SELECT wp.id, wp.content_text, w.domain
        FROM web_pages wp
        JOIN websites w ON w.id = wp.website_id
        WHERE w.domain = ANY(%s)
          AND wp.content_text IS NOT NULL AND wp.content_text <> ''
    """, (domains,))
    pages = cur.fetchall()
    page_ids = [p[0] for p in pages]
    print(f"Pages with content_text: {len(pages):,}", flush=True)

    # Idempotency: clear existing matches for ONLY these pages (scoped).
    if page_ids:
        cur.execute("DELETE FROM page_keyword_matches WHERE web_page_id = ANY(%s)", (page_ids,))
        print(f"Cleared {cur.rowcount} prior matches for target pages.", flush=True)
        cur.execute("UPDATE web_pages SET has_env_keywords = FALSE WHERE id = ANY(%s)", (page_ids,))
    conn.commit()

    hits      = defaultdict(int)
    kw_counts = defaultdict(int)
    totals    = defaultdict(int)
    match_rows, env_page_ids = [], []

    for page_id, content_text, domain in pages:
        totals[domain] += 1
        spans = merge_nearby_matches(find_keyword_matches(content_text, pattern))
        if spans:
            hits[domain]      += 1
            kw_counts[domain] += len(spans)
            env_page_ids.append(page_id)
            for keyword, start, end in spans:
                match_rows.append((page_id, keyword, start, end))

    if match_rows:
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO page_keyword_matches (web_page_id, keyword, start_offset, end_offset) "
            "VALUES (%s, %s, %s, %s)",
            match_rows, page_size=1_000,
        )
    if env_page_ids:
        psycopg2.extras.execute_batch(
            cur,
            "UPDATE web_pages SET has_env_keywords = TRUE WHERE id = %s",
            [(pid,) for pid in env_page_ids], page_size=1_000,
        )
    conn.commit()

    # Upsert env_keyword_analysis for the target domains (additive).
    cur.execute("SELECT domain, id FROM websites WHERE domain = ANY(%s)", (domains,))
    domain_to_wid = {d: i for d, i in cur.fetchall()}
    upsert_rows = [(domain_to_wid[d], hits[d], totals[d], kw_counts[d])
                   for d in totals if d in domain_to_wid]
    if upsert_rows:
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO env_keyword_analysis
                (website_id, pages_with_hits, total_pages, total_keyword_occurrences)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (website_id) DO UPDATE SET
                pages_with_hits           = EXCLUDED.pages_with_hits,
                total_pages               = EXCLUDED.total_pages,
                total_keyword_occurrences = EXCLUDED.total_keyword_occurrences
            """,
            upsert_rows, page_size=500,
        )
    conn.commit()

    print(f"\n{'─'*55}", flush=True)
    print(f"Pages scanned          : {len(pages):,}", flush=True)
    print(f"Pages with env keywords: {sum(hits.values()):,}", flush=True)
    print(f"Action spans inserted  : {len(match_rows):,}", flush=True)
    print(f"Domains with >=1 hit    : {sum(1 for d in totals if hits[d] > 0)} / {len(totals)}", flush=True)
    for d in sorted(totals, key=lambda x: -hits[x])[:10]:
        print(f"  {d:40s} {hits[d]:3d}/{totals[d]:3d} pages  {kw_counts[d]:4d} spans", flush=True)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
