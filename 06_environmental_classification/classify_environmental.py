"""
DB-native keyword classifier for environmental page detection.

For every page in web_pages that has content_text:
  - Finds all keyword matches (keyword, start_offset, end_offset)
  - Merges matches within MERGE_THRESHOLD chars into a single action span
  - Inserts one row per action span into page_keyword_matches
  - Updates web_pages.has_env_keywords
  - Aggregates per-domain stats into env_keyword_analysis
  - Writes classifying_environmental_action.csv

Prerequisite:
    python 08_database/migrate_add_keyword_matches.py
    python 08_database/populate_content_text.py

Usage:
    python classify_environmental.py
"""

import csv
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
from keyword_search import load_keywords, find_keyword_matches

load_dotenv(ROOT / ".env")

# ── config ────────────────────────────────────────────────────────────────────

OUT_CSV         = HERE / "classifying_environmental_action.csv"
BATCH_SIZE      = 2_000
MERGE_THRESHOLD = 15   # chars; matches closer than this are one action span


# ── helpers ───────────────────────────────────────────────────────────────────

def merge_nearby_matches(
    matches: list[tuple[str, int, int]],
    threshold: int = MERGE_THRESHOLD,
) -> list[tuple[str, int, int]]:
    """Merge keyword hits within *threshold* chars of each other into one span.

    Returns a list of (keywords, start_offset, end_offset) where *keywords* is
    a comma-separated string of all contributing keyword tokens.  Matches are
    assumed to be sorted by start_offset (find_keyword_matches guarantees this
    via finditer).
    """
    if not matches:
        return []
    merged: list[tuple[str, int, int]] = []
    cur_keywords = [matches[0][0]]
    cur_start    = matches[0][1]
    cur_end      = matches[0][2]

    for keyword, start, end in matches[1:]:
        if start - cur_end <= threshold:
            cur_keywords.append(keyword)
            cur_end = max(cur_end, end)
        else:
            merged.append((", ".join(cur_keywords), cur_start, cur_end))
            cur_keywords = [keyword]
            cur_start    = start
            cur_end      = end

    merged.append((", ".join(cur_keywords), cur_start, cur_end))
    return merged


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    pattern = load_keywords()

    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM web_pages WHERE content_text IS NOT NULL AND content_text != ''")
    total = cur.fetchone()[0]
    print(f"Pages with content_text : {total:,}", flush=True)

    # Full refresh — delete all previous match rows
    cur.execute("DELETE FROM page_keyword_matches")
    print("Cleared page_keyword_matches.", flush=True)

    # Reset has_env_keywords so pages whose content changed are re-evaluated
    cur.execute("UPDATE web_pages SET has_env_keywords = FALSE")
    conn.commit()

    # domain → {pages_with_hits, total_pages, total_keyword_occurrences}
    hits      = defaultdict(int)
    kw_counts = defaultdict(int)
    totals    = defaultdict(int)

    stream = conn.cursor("page_stream")
    stream.execute("""
        SELECT wp.id, wp.content_text, w.domain
        FROM web_pages wp
        JOIN websites w ON w.id = wp.website_id
        WHERE wp.content_text IS NOT NULL AND wp.content_text != ''
    """)

    processed = 0
    while True:
        batch = stream.fetchmany(BATCH_SIZE)
        if not batch:
            break

        match_rows   = []   # (web_page_id, keyword, start_offset, end_offset)
        env_page_ids = []   # pages in this batch that have ≥1 match

        for page_id, content_text, domain in batch:
            totals[domain] += 1
            raw_matches = find_keyword_matches(content_text, pattern)
            matches     = merge_nearby_matches(raw_matches)
            if matches:
                hits[domain]      += 1
                kw_counts[domain] += len(matches)
                env_page_ids.append(page_id)
                for keyword, start, end in matches:
                    match_rows.append((page_id, keyword, start, end))

        if match_rows:
            psycopg2.extras.execute_batch(
                cur,
                "INSERT INTO page_keyword_matches (web_page_id, keyword, start_offset, end_offset) "
                "VALUES (%s, %s, %s, %s)",
                match_rows,
                page_size=1_000,
            )
        if env_page_ids:
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE web_pages SET has_env_keywords = TRUE WHERE id = %s",
                [(pid,) for pid in env_page_ids],
                page_size=1_000,
            )

        processed += len(batch)
        print(f"  {processed:,} / {total:,} pages — "
              f"{sum(hits.values()):,} env pages found so far", flush=True)

    stream.close()
    conn.commit()

    # ── upsert env_keyword_analysis ───────────────────────────────────────────
    cur.execute("SELECT w.domain, w.id FROM websites w")
    domain_to_website_id = {row[0]: row[1] for row in cur.fetchall()}

    upsert_rows = []
    for domain in sorted(totals):
        wid = domain_to_website_id.get(domain)
        if wid is None:
            continue
        upsert_rows.append((
            wid,
            hits[domain],
            totals[domain],
            kw_counts[domain],
        ))

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
        upsert_rows,
        page_size=500,
    )
    conn.commit()

    # ── write CSV (backward-compatible columns) ───────────────────────────────
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["website", "pages_with_hits", "total_pages", "total_keyword_occurrences"])
        for domain in sorted(totals):
            writer.writerow([domain, hits[domain], totals[domain], kw_counts[domain]])

    # ── summary ───────────────────────────────────────────────────────────────
    total_env_pages   = sum(hits.values())
    domains_with_hits = sum(1 for d in totals if hits[d] > 0)
    total_match_rows  = sum(kw_counts.values())

    print(f"\n{'─' * 50}", flush=True)
    print(f"Pages scanned           : {processed:,}", flush=True)
    print(f"Pages with env keywords : {total_env_pages:,}", flush=True)
    print(f"Total keyword matches   : {total_match_rows:,}", flush=True)
    print(f"Domains with ≥1 hit     : {domains_with_hits} / {len(totals)}", flush=True)
    print(f"CSV written to          : {OUT_CSV.resolve()}", flush=True)

    top = sorted(totals, key=lambda d: -hits[d])[:10]
    print("\nTop 10 domains by page hits:")
    for d in top:
        print(f"  {d:50s}  {hits[d]:4d} / {totals[d]} pages  "
              f"({kw_counts[d]} total matches)", flush=True)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
