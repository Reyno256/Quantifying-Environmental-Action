"""
Sample chunks already framing-classified by Gemini (llm_chunk_framing) and
re-classify them with Mistral (mistral-medium-latest) using the SAME prompt
and temperature, to assess inter-model agreement.

Results are written to a local CSV only (13_embeddedvsimplictvsexplict/
mistral_framing_comparison.csv) -- nothing is written back to the database.

Resume-safe: chunk_ids already present in the output CSV are skipped.

Usage:
    python compare_mistral_framing.py
    python compare_mistral_framing.py --n 1000 --workers 5
"""

import argparse
import csv
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from mistralai.client import Mistral

HERE = Path(__file__).parent
load_dotenv(HERE.parent / ".env")

client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))

MODEL = "mistral-medium-latest"
VALID_LABELS = {"Explicit", "Embedded", "Implicit"}

# ── prompt (loaded from sys_prompt.txt — refined Decision-priority version) ───

_sys_prompt_ns: dict = {}
exec((HERE / "sys_prompt.txt").read_text(encoding="utf-8"), _sys_prompt_ns)
SYSTEM_PROMPT = _sys_prompt_ns["SYSTEM_PROMPT"]


def build_prompt(chunk_text: str, category: str) -> str:
    return (
        f'Assigned category: "{category}"\n\n'
        f'Excerpt:\n"""{chunk_text}"""\n\n'
        "Return exactly one label from the valid labels listed in the instructions."
    )


# ── DB ──────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── rate-limit helpers ─────────────────────────────────────────────────────

_rate_lock = threading.Lock()
_rate_limit_until = [0.0]


def _wait_if_rate_limited() -> None:
    while True:
        with _rate_lock:
            wait = _rate_limit_until[0] - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 1.0))


def _handle_rate_limit(wait_secs: float = 65.0) -> None:
    resume_at = time.time() + wait_secs
    with _rate_lock:
        extended = resume_at > _rate_limit_until[0]
        if extended:
            _rate_limit_until[0] = resume_at
    if extended:
        reset_str = time.strftime("%H:%M:%S", time.localtime(resume_at))
        print(f"  Rate limited - pausing {wait_secs:.0f}s (until {reset_str})", flush=True)


def _is_rate_limit(exc: Exception) -> bool:
    return "429" in str(exc) or "rate" in str(exc).lower()


# ── classification ───────────────────────────────────────────────────────────

def classify_with_mistral(chunk_text: str, category: str) -> dict:
    prompt = build_prompt(chunk_text, category)
    for attempt in range(5):
        _wait_if_rate_limited()
        try:
            res = client.chat.complete(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            label = res.choices[0].message.content.strip().strip('"')
            if label not in VALID_LABELS:
                return {"framing": "", "error": f"invalid label: {label!r}"[:200]}
            return {"framing": label, "error": ""}
        except Exception as e:
            if _is_rate_limit(e):
                _handle_rate_limit(65.0)
            else:
                return {"framing": "", "error": str(e)[:200]}
    return {"framing": "", "error": "rate limited after 5 attempts"}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000, help="sample size")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--seed", type=float, default=0.42, help="setseed() value for reproducible sampling")
    args = parser.parse_args()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT setseed(%s)", (args.seed,))
    cur.execute(
        """
        SELECT lcc.id, lcc.chunk_text, lcc.category, lcf.framing
        FROM llm_chunk_framing lcf
        JOIN llm_chunk_classifications lcc ON lcc.id = lcf.chunk_id
        WHERE lcf.model_used = 'gemini-3.1-flash-lite'
          AND (lcf.error IS NULL OR lcf.error = '')
        ORDER BY RANDOM()
        LIMIT %s
        """,
        (args.n,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    out_path = HERE / "mistral_framing_comparison.csv"

    done_ids = set()
    write_header = True
    if out_path.exists():
        with open(out_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is not None:
                write_header = False
                for row in reader:
                    if row:
                        done_ids.add(int(row[0]))

    rows = [r for r in rows if r[0] not in done_ids]

    print(f"{len(done_ids):,} already done. Running Mistral ({MODEL}) on remaining {len(rows):,} "
          f"with {args.workers} workers...\n", flush=True)

    completed = 0
    total = len(rows)

    with open(out_path, "a", newline="", encoding="utf-8") as f, \
         ThreadPoolExecutor(max_workers=args.workers) as pool:

        writer = csv.writer(f)
        if write_header:
            writer.writerow(["chunk_id", "category", "gemini_framing", "mistral_framing", "mistral_error", "chunk_text"])

        futures = {
            pool.submit(classify_with_mistral, text or "", cat or "N/A"): (cid, text, cat, gem_framing)
            for cid, text, cat, gem_framing in rows
        }
        for fut in as_completed(futures):
            cid, text, cat, gem_framing = futures[fut]
            r = fut.result()
            writer.writerow([cid, cat, gem_framing, r["framing"], r["error"], text])
            completed += 1
            if completed % 50 == 0 or completed == total:
                print(f"  {completed:,} / {total:,} ({completed/total*100:.1f}%)", flush=True)

    print(f"\nWrote results to {out_path}")


if __name__ == "__main__":
    main()
