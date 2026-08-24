"""
Re-classify EVERY confirmed-action chunk (category != 'N/A') into the
Implicit/Explicit/Embedded framing axis using the few-shot framing_judge
prompt (sys_prompt.txt + prompt_examples.txt), OVERWRITING the existing
llm_chunk_framing rows in place (model_used stays 'gemini-3.1-flash-lite').

Unlike run_chunk_framing_classifications.py (which skips already-classified
chunks), this re-does all chunks. Resume-safety comes from a local progress
CSV (fewshot_overwrite_progress.csv): chunk_ids already logged there are
skipped, so an interrupted run can be restarted without re-spending API calls.

Usage:
    python run_framing_fewshot_overwrite.py --workers 8
"""

import argparse
import csv
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
from framing_judge import get_framing_judge  # noqa: E402

MODEL_NAME   = "gemini-3.1-flash-lite"
VALID_LABELS = {"Explicit", "Embedded", "Implicit"}
FLUSH_EVERY  = 100
PROGRESS     = HERE / "fewshot_overwrite_progress.csv"


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


def classify_chunk(chunk_id: int, chunk_text: str, category: str) -> dict:
    for _ in range(5):
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


def load_done_ids() -> set:
    done = set()
    if PROGRESS.exists():
        with open(PROGRESS, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if row:
                    done.add(int(row[0]))
    return done


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = get_conn()
    cur = conn.cursor()

    print("Loading all confirmed-action chunks…", flush=True)
    query = """
        SELECT id, chunk_text, category
        FROM llm_chunk_classifications
        WHERE category != 'N/A'
        ORDER BY id
    """
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    cur.execute(query)
    rows = cur.fetchall()

    done = load_done_ids()
    rows = [r for r in rows if r[0] not in done]
    total = len(rows)
    print(f"  Universe: {len(done) + total:,}  |  already done: {len(done):,}  |  to do: {total:,}", flush=True)
    print(f"  Workers : {args.workers}\n", flush=True)

    if total == 0:
        print("Nothing to do.")
        cur.close(); conn.close()
        return

    write_header = not PROGRESS.exists()
    prog_f = open(PROGRESS, "a", newline="", encoding="utf-8")
    prog_w = csv.writer(prog_f)
    if write_header:
        prog_w.writerow(["chunk_id", "framing", "error"])

    insert_buf: list[tuple] = []
    completed = 0
    counts = {"Explicit": 0, "Embedded": 0, "Implicit": 0, "": 0}

    def flush(force=False):
        if insert_buf and (force or len(insert_buf) >= FLUSH_EVERY):
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO llm_chunk_framing (chunk_id, framing, model_used, error)
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
            prog_f.flush()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(classify_chunk, cid, text or "", cat or "N/A"): cid
            for cid, text, cat in rows
        }
        for fut in as_completed(futures):
            r = fut.result()
            insert_buf.append((r["chunk_id"], r["framing"] or None, MODEL_NAME, r["error"] or None))
            prog_w.writerow([r["chunk_id"], r["framing"], r["error"]])
            counts[r["framing"] if r["framing"] in counts else ""] += 1
            completed += 1
            flush()
            if completed % 500 == 0 or completed == total:
                pct = completed / total * 100
                print(f"  {completed:,}/{total:,} ({pct:.1f}%)  "
                      f"Imp={counts['Implicit']} Emb={counts['Embedded']} Exp={counts['Explicit']} err={counts['']}",
                      flush=True)

    flush(force=True)
    prog_f.close()

    print(f"\nDone. Re-classified {completed:,} chunks (few-shot, overwritten in place).")
    print(f"  Implicit={counts['Implicit']:,}  Embedded={counts['Embedded']:,}  "
          f"Explicit={counts['Explicit']:,}  errors={counts['']:,}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
