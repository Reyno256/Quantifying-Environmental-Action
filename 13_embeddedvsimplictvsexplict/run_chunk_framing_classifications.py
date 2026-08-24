"""
Classify every confirmed-action chunk in llm_chunk_classifications into the
Implicit/Explicit/Embedded "framing" axis (Baugh 2019; Caldwell et al. 2022).

Only chunks with category != 'N/A' are considered (category == 'N/A' means the
chunk is not a real environmental action and has no framing).

Results are written to llm_chunk_framing (one row per chunk).
Already-classified chunks are skipped on re-run (resume safe).

Usage:
    python run_chunk_framing_classifications.py
    python run_chunk_framing_classifications.py --workers 10 --limit 50
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

def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start.parent

load_dotenv(_find_root(HERE) / ".env")

sys.path.insert(0, str(HERE))
from framing_judge import get_framing_judge

# ── config ────────────────────────────────────────────────────────────────────

WORKERS     = 5
FLUSH_EVERY = 100   # insert batch size
MODEL_NAME  = "gemini-3.1-flash-lite"
VALID_LABELS = {"Explicit", "Embedded", "Implicit"}

# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


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

def classify_chunk(chunk_id: int, chunk_text: str, category: str) -> dict:
    for attempt in range(5):
        _wait_if_rate_limited()
        try:
            label = get_framing_judge(chunk_text, category).strip()
            if label not in VALID_LABELS:
                return {"chunk_id": chunk_id, "framing": "",
                        "error": f"invalid label: {label!r}"[:200]}
            return {"chunk_id": chunk_id, "framing": label, "error": ""}
        except Exception as e:
            if _is_rate_limit(e):
                _handle_rate_limit(60.0)
            else:
                return {"chunk_id": chunk_id, "framing": "", "error": str(e)[:200]}
    return {"chunk_id": chunk_id, "framing": "", "error": "rate limited after 5 attempts"}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--limit",   type=int, default=None,
                        help="classify at most N chunks (for testing)")
    args = parser.parse_args()

    conn = get_conn()
    cur  = conn.cursor()

    # Load all unclassified (or previously errored) confirmed-action chunks
    print("Loading unclassified chunks…", flush=True)
    query = """
        SELECT lcc.id, lcc.chunk_text, lcc.category
        FROM llm_chunk_classifications lcc
        LEFT JOIN llm_chunk_framing lcf ON lcf.chunk_id = lcc.id
        WHERE lcc.category != 'N/A'
          AND (lcf.id IS NULL OR (lcf.error IS NOT NULL AND lcf.error != ''))
        ORDER BY lcc.id
    """
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    cur.execute(query)
    rows = cur.fetchall()  # (chunk_id, chunk_text, category)

    total = len(rows)
    print(f"  Chunks to classify: {total:,}", flush=True)
    print(f"  Workers           : {args.workers}\n", flush=True)

    if total == 0:
        print("Nothing to do.")
        cur.close(); conn.close()
        return

    completed  = 0
    insert_buf: list[tuple] = []

    def flush_inserts(force: bool = False):
        if insert_buf and (force or len(insert_buf) >= FLUSH_EVERY):
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO llm_chunk_framing
                    (chunk_id, framing, model_used, error)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    framing       = EXCLUDED.framing,
                    model_used    = EXCLUDED.model_used,
                    error         = EXCLUDED.error,
                    classified_at = NOW()
                """,
                insert_buf,
                page_size=FLUSH_EVERY,
            )
            conn.commit()
            insert_buf.clear()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(classify_chunk, cid, text or "", cat or "N/A"): cid
            for cid, text, cat in rows
        }
        for fut in as_completed(futures):
            r = fut.result()
            insert_buf.append((
                r["chunk_id"],
                r["framing"] or None,
                MODEL_NAME,
                r["error"] or None,
            ))
            completed += 1
            flush_inserts()

            if completed % 500 == 0 or completed == total:
                pct = completed / total * 100
                print(f"  {completed:,} / {total:,} ({pct:.1f}%)", flush=True)

    flush_inserts(force=True)

    # ── summary ───────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT framing, COUNT(*) AS n
        FROM llm_chunk_framing
        WHERE error IS NULL OR error = ''
        GROUP BY framing ORDER BY n DESC
    """)
    print(f"\n{'-' * 55}")
    print(f"Done. {completed:,} chunks classified.")
    print("\nFraming breakdown (chunks):")
    for framing, n in cur.fetchall():
        print(f"  {(framing or 'N/A'):<12s} {n:>6,}")

    # % of synagogues with at least one chunk of each framing
    cur.execute("""
        SELECT lcf.framing, COUNT(DISTINCT s.id) AS n_synagogues
        FROM llm_chunk_framing lcf
        JOIN llm_chunk_classifications lcc ON lcc.id = lcf.chunk_id
        JOIN page_keyword_matches pkm ON pkm.id = lcc.match_id
        JOIN web_pages p ON p.id = pkm.web_page_id
        JOIN websites w ON w.id = p.website_id
        JOIN synagogues s ON s.id = w.synagogue_id
        WHERE lcf.framing IS NOT NULL
        GROUP BY lcf.framing ORDER BY n_synagogues DESC
    """)
    print("\nSynagogues with >=1 chunk of each framing:")
    for framing, n in cur.fetchall():
        print(f"  {framing:<12s} {n:>6,}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
