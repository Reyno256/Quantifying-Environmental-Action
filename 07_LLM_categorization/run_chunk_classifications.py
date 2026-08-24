"""
Classify every action span in page_keyword_matches using Gemini.

For each span, a context window is extracted from web_pages.content_text:
  - Extends ±TARGET_PAD chars from the span boundaries
  - Capped at the midpoint to the nearest neighbouring span so that
    adjacent chunks on the same page never overlap

Results are written to llm_chunk_classifications (one row per span).
Already-classified spans are skipped on re-run (resume safe).

Usage:
    python run_chunk_classifications.py
    python run_chunk_classifications.py --workers 10 --pad 300
"""

import argparse
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent

def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start.parent

load_dotenv(_find_root(HERE) / ".env")

sys.path.insert(0, str(HERE))
from gemini_as_a_judge import get_response_judge

sys.path.insert(0, str(ROOT / "11_analysis" / "recreate_existing"))
from recreate_common import classify_action, pure_action  # noqa: E402


def major_category_of(category: str) -> str | None:
    """
    9-category bucket for a raw `category` label; None for 'N/A' (no action) or
    '' (classify_chunk() only ever sets '' on an outright classification error,
    never as a real LLM response, so it isn't a genuine 'Other' action).
    """
    if not category or category == "N/A":
        return None
    bucket = classify_action(pure_action(category))
    return None if bucket == "??UNMAPPED" else bucket

# ── config ────────────────────────────────────────────────────────────────────

TARGET_PAD  = 500   # chars of context on each side of the span
WORKERS     = 5
FLUSH_EVERY = 100   # insert batch size
PAGE_BATCH  = 200   # pages whose content_text to fetch at once
MODEL_NAME  = "gemini-3.1-flash-lite"

# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── window extraction ─────────────────────────────────────────────────────────

def extract_windows(
    content_text: str,
    spans: list[tuple[int, int, int]],  # (match_id, start, end) sorted by start
    pad: int,
) -> list[tuple[int, str]]:
    """Return (match_id, chunk_text) for each span.

    The window extends `pad` chars in each direction but is capped at the
    midpoint to the adjacent span so neighbouring chunks never overlap.
    """
    result = []
    n = len(spans)
    for i, (match_id, start, end) in enumerate(spans):
        if i > 0:
            prev_end  = spans[i - 1][2]
            left_pad  = min(pad, (start - prev_end) // 2)
        else:
            left_pad  = pad

        if i < n - 1:
            next_start = spans[i + 1][1]
            right_pad  = min(pad, (next_start - end) // 2)
        else:
            right_pad  = pad

        chunk_start = max(0, start - left_pad)
        chunk_end   = min(len(content_text), end + right_pad)
        result.append((match_id, content_text[chunk_start:chunk_end]))
    return result


# ── rate-limit helpers ────────────────────────────────────────────────────────

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

def classify_chunk(match_id: int, chunk_text: str) -> dict:
    for attempt in range(5):
        _wait_if_rate_limited()
        try:
            cat = get_response_judge(chunk_text)
            return {"match_id": match_id, "chunk_text": chunk_text,
                    "category": cat.strip(), "error": ""}
        except Exception as e:
            if _is_rate_limit(e):
                _handle_rate_limit(60.0)
            else:
                return {"match_id": match_id, "chunk_text": chunk_text,
                        "category": "", "error": str(e)[:200]}
    return {"match_id": match_id, "chunk_text": chunk_text,
            "category": "", "error": "rate limited after 5 attempts"}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--pad",     type=int, default=TARGET_PAD,
                        help="chars of context on each side of the span")
    args = parser.parse_args()

    conn = get_conn()
    cur  = conn.cursor()

    # Add UNIQUE constraint on match_id if not already present (idempotent)
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'lcc_match_unique'
            ) THEN
                ALTER TABLE llm_chunk_classifications
                ADD CONSTRAINT lcc_match_unique UNIQUE (match_id);
            END IF;
        END $$
    """)
    conn.commit()

    # Load all unclassified (or previously errored) spans, grouped by page
    print("Loading unclassified spans…", flush=True)
    cur.execute("""
        SELECT pkm.id, pkm.web_page_id, pkm.start_offset, pkm.end_offset
        FROM page_keyword_matches pkm
        LEFT JOIN llm_chunk_classifications lcc ON lcc.match_id = pkm.id
        WHERE lcc.id IS NULL
           OR (lcc.error IS NOT NULL AND lcc.error != '')
        ORDER BY pkm.web_page_id, pkm.start_offset
    """)
    rows = cur.fetchall()   # (match_id, web_page_id, start, end)

    page_spans: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for match_id, page_id, start, end in rows:
        page_spans[page_id].append((match_id, start, end))

    total_spans = len(rows)
    total_pages = len(page_spans)
    print(f"  Spans to classify : {total_spans:,}", flush=True)
    print(f"  Pages involved    : {total_pages:,}", flush=True)
    print(f"  Workers           : {args.workers}", flush=True)
    print(f"  Context pad       : ±{args.pad} chars\n", flush=True)

    if total_spans == 0:
        print("Nothing to do.")
        cur.close(); conn.close()
        return

    page_ids   = list(page_spans.keys())
    completed  = 0
    insert_buf: list[tuple] = []

    def flush_inserts(force: bool = False):
        if insert_buf and (force or len(insert_buf) >= FLUSH_EVERY):
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO llm_chunk_classifications
                    (match_id, chunk_text, category, major_category, model_used, error)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (match_id) DO UPDATE SET
                    chunk_text     = EXCLUDED.chunk_text,
                    category       = EXCLUDED.category,
                    major_category = EXCLUDED.major_category,
                    model_used     = EXCLUDED.model_used,
                    error          = EXCLUDED.error,
                    classified_at  = NOW()
                """,
                insert_buf,
                page_size=FLUSH_EVERY,
            )
            conn.commit()
            insert_buf.clear()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for batch_start in range(0, total_pages, PAGE_BATCH):
            batch_ids = page_ids[batch_start : batch_start + PAGE_BATCH]

            cur.execute(
                "SELECT id, content_text FROM web_pages WHERE id = ANY(%s)",
                (batch_ids,),
            )
            content_map = {row[0]: (row[1] or "") for row in cur.fetchall()}

            work_items: list[tuple[int, str]] = []
            for page_id in batch_ids:
                text  = content_map.get(page_id, "")
                spans = page_spans[page_id]
                if text:
                    work_items.extend(extract_windows(text, spans, args.pad))
                else:
                    for match_id, _, _ in spans:
                        work_items.append((match_id, ""))

            futures = {
                pool.submit(classify_chunk, mid, chunk): mid
                for mid, chunk in work_items
            }
            for fut in as_completed(futures):
                r = fut.result()
                insert_buf.append((
                    r["match_id"],
                    r["chunk_text"],
                    r["category"],
                    major_category_of(r["category"]),
                    MODEL_NAME,
                    r["error"] or None,
                ))
                completed += 1
                flush_inserts()

                if completed % 500 == 0 or completed == total_spans:
                    pct = completed / total_spans * 100
                    print(f"  {completed:,} / {total_spans:,} ({pct:.1f}%)", flush=True)

    flush_inserts(force=True)

    # ── summary ───────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT category, COUNT(*) AS n
        FROM llm_chunk_classifications
        WHERE error IS NULL OR error = ''
        GROUP BY category ORDER BY n DESC
    """)
    print(f"\n{'─' * 55}")
    print(f"Done. {completed:,} spans classified.")
    print("\nCategory breakdown:")
    for cat, n in cur.fetchall():
        print(f"  {(cat or 'N/A'):<55s} {n:>6,}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
