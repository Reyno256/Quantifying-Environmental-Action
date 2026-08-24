"""
Populate page_chunk_classifications: chunk every page's content_text into
fixed 500-char (whitespace-snapped) windows and classify each chunk
independently with the Gemini judge.

This is the production version of the chunk-vs-keyword/embedding method
validated in 06_environmental_classification/chunk_vs_keyword_embed.py +
chunk_gemini_ground_truth.py, applied at full scale. Unlike
llm_chunk_classifications (one row per page_keyword_matches span, so only
pages with a keyword hit ever get a row), this table gets a row for EVERY
chunk of EVERY page with content_text by default — the entire point of this
method vs. the keyword-gated pipeline is to also catch keyword-search misses.

chunk_text() is imported from chunk_common.py — a dependency-free module
(stdlib only) shared with the research scripts in
06_environmental_classification/, so this production script never pulls in
Cohere or any other research-only dependency just to chunk a string.

Prerequisite:
    python 08_database/migrate_add_page_chunk_classifications.py

Usage:
    python 08_database/populate_page_chunk_classifications.py --limit 5
    python 08_database/populate_page_chunk_classifications.py --workers 10
    python 08_database/populate_page_chunk_classifications.py --prefix canada_ --country Canadian
"""

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "06_environmental_classification"))
from chunk_common import chunk_text  # noqa: E402

sys.path.insert(0, str(ROOT / "07_LLM_categorization"))
import gemini_as_a_judge  # noqa: E402
_judge = gemini_as_a_judge.get_response_judge

sys.path.insert(0, str(ROOT / "11_analysis" / "recreate_existing"))
from recreate_common import classify_action, pure_action  # noqa: E402


def major_category_of(category: str) -> str | None:
    """9-bucket rollup; None for 'N/A'/'' or an unrecognized label (soft-fail —
    logged via the raw category staying in the row, not raised, so one bad
    label doesn't kill a multi-hour run)."""
    if not category or category == "N/A":
        return None
    bucket = classify_action(pure_action(category))
    return None if bucket == "??UNMAPPED" else bucket


MODEL_NAME  = gemini_as_a_judge.MODEL   # single source of truth, not re-hardcoded
TEMPERATURE = 0.0                        # matches _call_with_cache/_call_direct
MAX_CHARS   = 6_000                      # snippet cap before the judge call
                                          # (no-op at 500-char chunks; kept for
                                          # consistency with chunk_gemini_ground_truth.py)
PAGE_BATCH  = 200
FLUSH_EVERY = 100


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── rate-limit helpers — shared-state pattern from run_chunk_classifications.py ─

_rate_lock        = threading.Lock()
_rate_limit_until = [0.0]


def _handle_rate_limit(wait_secs: float = 60.0) -> None:
    resume_at = time.time() + wait_secs
    with _rate_lock:
        extended = resume_at > _rate_limit_until[0]
        if extended:
            _rate_limit_until[0] = resume_at
    if extended:
        reset_str = time.strftime("%H:%M:%S", time.localtime(resume_at))
        print(f"  Rate limited — pausing {wait_secs:.0f}s (until {reset_str})", flush=True)


def _wait_if_rate_limited() -> None:
    while True:
        with _rate_lock:
            wait = _rate_limit_until[0] - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 1.0))


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__
    msg  = str(exc)
    return "429" in msg or "ResourceExhausted" in name or "rate" in msg.lower()


# ── classification ────────────────────────────────────────────────────────────

def classify_chunk(page_id: int, chunk_index: int, text: str) -> dict:
    snippet = (text or "")[:MAX_CHARS]
    if not snippet.strip():
        return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
                "category": "", "error": "no text"}
    for attempt in range(5):
        _wait_if_rate_limited()
        try:
            cat = _judge(snippet).strip().rstrip(".,").strip()
            return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
                    "category": cat, "error": ""}
        except Exception as e:
            if _is_rate_limit(e):
                _handle_rate_limit(60.0)
            else:
                return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
                        "category": "", "error": str(e)[:200]}
    return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
            "category": "", "error": "rate limited after 5 attempts"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--prefix", default="",
                    help='table-name prefix, e.g. "canada_" (default US tables)')
    ap.add_argument("--country", default="US",
                    help='country descriptor in the judge prompt (default "US"; '
                         'pass "Canadian" for the Canada run)')
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of pages considered (deterministic ORDER BY id); "
                         "for verification runs / partial batches")
    ap.add_argument("--has-keywords-only", action="store_true",
                    help="restrict to has_env_keywords=TRUE pages (default: ALL pages "
                         "with content_text — this method is meant to also catch "
                         "keyword-search misses, so this flag is NOT the default)")
    ap.add_argument("--page-batch", type=int, default=PAGE_BATCH)
    ap.add_argument("--flush-every", type=int, default=FLUSH_EVERY)
    args = ap.parse_args()
    P = args.prefix

    gemini_as_a_judge.configure_country(args.country)

    conn = get_conn()
    cur  = conn.cursor()

    # ── target pages ──────────────────────────────────────────────────────────
    where = "content_text IS NOT NULL AND content_text != ''"
    if args.has_keywords_only:
        where += " AND has_env_keywords = TRUE"
    query = f"SELECT id FROM {P}web_pages WHERE {where} ORDER BY id"
    if args.limit:
        query += " LIMIT %s"
        cur.execute(query, (args.limit,))
    else:
        cur.execute(query)
    page_ids = [r[0] for r in cur.fetchall()]

    print(f"Target pages : {len(page_ids):,}"
          f"{' (has_env_keywords=TRUE only)' if args.has_keywords_only else ' (ALL content_text pages)'}")
    print(f"Workers      : {args.workers}")
    print(f"Country      : {args.country}\n", flush=True)

    if not page_ids:
        print("Nothing to do.")
        cur.close(); conn.close()
        return

    completed = 0
    total_chunks_seen = 0
    insert_buf: list[tuple] = []

    def flush_inserts(force: bool = False):
        if insert_buf and (force or len(insert_buf) >= args.flush_every):
            psycopg2.extras.execute_batch(
                cur,
                f"""
                INSERT INTO {P}page_chunk_classifications
                    (web_page_id, chunk_index, chunk_text, category, major_category,
                     model_used, temperature, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (web_page_id, chunk_index) DO UPDATE SET
                    chunk_text     = EXCLUDED.chunk_text,
                    category       = EXCLUDED.category,
                    major_category = EXCLUDED.major_category,
                    model_used     = EXCLUDED.model_used,
                    temperature    = EXCLUDED.temperature,
                    error          = EXCLUDED.error,
                    classified_at  = NOW()
                """,
                insert_buf,
                page_size=args.flush_every,
            )
            conn.commit()
            insert_buf.clear()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for batch_start in range(0, len(page_ids), args.page_batch):
            batch_ids = page_ids[batch_start: batch_start + args.page_batch]

            cur.execute(
                f"SELECT id, content_text FROM {P}web_pages WHERE id = ANY(%s)",
                (batch_ids,),
            )
            content_map = {row[0]: row[1] for row in cur.fetchall()}

            # ── build the FULL desired (page_id, chunk_index, text) set for this
            #    batch by re-running the deterministic chunker in Python — these
            #    rows don't exist in the DB yet at all for a never-processed page,
            #    so there's nothing to LEFT JOIN against for them. ─────────────
            desired: list[tuple[int, int, str]] = []
            for pid in batch_ids:
                for idx, c in enumerate(chunk_text(content_map.get(pid))):
                    desired.append((pid, idx, c))
            total_chunks_seen += len(desired)

            # ── subtract already-done (non-errored) (page_id, chunk_index)
            #    pairs — the DB-side half of resumability. This assumes
            #    content_text is write-once per page (true today: every
            #    loader uses ON CONFLICT DO NOTHING and populate_content_text.py
            #    only ever targets NULL/empty rows) — if that ever changes, a
            #    page whose content_text was updated in place after being
            #    fully classified would be silently skipped here, leaving
            #    stale chunk_text/category rows. ─────────────────────────────
            cur.execute(
                f"""SELECT web_page_id, chunk_index FROM {P}page_chunk_classifications
                    WHERE web_page_id = ANY(%s) AND (error IS NULL OR error = '')""",
                (batch_ids,),
            )
            done = {(r[0], r[1]) for r in cur.fetchall()}

            work_items = [(pid, idx, c) for pid, idx, c in desired if (pid, idx) not in done]

            futures = {
                pool.submit(classify_chunk, pid, idx, c): (pid, idx)
                for pid, idx, c in work_items
            }
            for fut in as_completed(futures):
                r = fut.result()
                bucket = major_category_of(r["category"])
                insert_buf.append((
                    r["page_id"], r["chunk_index"], r["chunk_text"],
                    r["category"], bucket, MODEL_NAME, TEMPERATURE,
                    r["error"] or None,
                ))
                completed += 1
                flush_inserts()

                if completed % 500 == 0:
                    print(f"  {completed:,} chunks classified so far…", flush=True)

    flush_inserts(force=True)

    # ── summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 55}")
    print(f"Done. {completed:,} chunks classified this run "
          f"({total_chunks_seen:,} total chunks seen across {len(page_ids):,} pages, "
          f"{total_chunks_seen - completed:,} already done / skipped).")

    cur.execute(f"""
        SELECT category, COUNT(*) FROM {P}page_chunk_classifications
        WHERE error IS NULL OR error = ''
        GROUP BY category ORDER BY 2 DESC LIMIT 20
    """)
    print("\nCategory breakdown (top 20):")
    for cat, n in cur.fetchall():
        print(f"  {(cat or 'N/A'):<70s} {n:>6,}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
