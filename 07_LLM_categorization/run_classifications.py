"""
Classify every env-flagged page (web_pages.has_env_keywords = TRUE) using
Claude Haiku 4.5 via the Anthropic API. Reads content_text from the DB;
results written to llm_classifications.csv.

Features:
  - 5 concurrent workers (respects Anthropic rate limits).
  - Exponential backoff on 429s (up to 5 retries per page).
  - Resume: skips pages already successfully recorded in llm_classifications.csv.
  - Flushes to disk every FLUSH_EVERY completions.
  - Merges per-domain LLM summary back into classifying_environmental_action.csv.
"""

import csv
import os
import sys
import time
import json
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic as _anthropic
import psycopg2
from dotenv import load_dotenv

HERE    = Path(__file__).parent
ROOT    = HERE.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(HERE))
from gemini_as_a_judge import get_response_judge

# ── config ────────────────────────────────────────────────────────────────────

OUT_CSV    = HERE / "llm_classifications.csv"
ENV_CSV    = ROOT / "06_environmental_classification" / "classifying_environmental_action.csv"

WORKERS     = 5
MAX_CHARS   = 6_000
FLUSH_EVERY = 50
MODEL_NAME  = "haiku-4-5"

# ── DB connection ─────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_env_pages() -> list[tuple[int, str, str, str]]:
    """Return (id, page_path, content_text, domain) for env-flagged pages."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT wp.id, wp.page_path, wp.content_text, w.domain
        FROM   web_pages wp
        JOIN   websites  w  ON w.id = wp.website_id
        WHERE  wp.has_env_keywords = TRUE
        ORDER  BY w.domain, wp.page_path
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


# ── helpers ───────────────────────────────────────────────────────────────────

def load_done(path: Path) -> set[str]:
    """Only count successfully classified pages as done (errors get retried)."""
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {
            row["page"] for row in csv.DictReader(f)
            if row.get("page") and row.get("category") and not row.get("error")
        }

# ── shared rate-limit state (all workers respect the same cooldown) ────────────

_rate_lock      = threading.Lock()
_rate_limit_until = [0.0]   # epoch seconds; workers wait until this passes


def _handle_rate_limit(exc: _anthropic.RateLimitError) -> None:
    """Parse retry-after from the response header and park all workers.
    Only prints if this call actually advances the shared cooldown window."""
    try:
        retry_after = float(exc.response.headers.get("retry-after", 60))
    except Exception:
        retry_after = 60.0
    resume_at = time.time() + retry_after
    with _rate_lock:
        extended = resume_at > _rate_limit_until[0]
        if extended:
            _rate_limit_until[0] = resume_at
    if extended:
        reset_str = time.strftime("%H:%M:%S", time.localtime(resume_at))
        print(f"  Rate limited — pausing {retry_after:.0f}s (until {reset_str})", flush=True)


def _wait_if_rate_limited() -> None:
    while True:
        with _rate_lock:
            wait = _rate_limit_until[0] - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 1.0))


# ── classification ────────────────────────────────────────────────────────────

def classify_page(page_id: int, page_path: str, content_text: str, domain: str) -> dict:
    text = (content_text or "")[:MAX_CHARS]
    for attempt in range(5):
        _wait_if_rate_limited()
        try:
            cat = get_response_judge(text)
            return {
                "domain":   domain,
                "page":     page_path,
                "category": cat.strip(),
                "model":    MODEL_NAME,
                "error":    "",
            }
        except _anthropic.RateLimitError as e:
            _handle_rate_limit(e)
            # loop → re-check _wait_if_rate_limited on next attempt
        except Exception as e:
            return {
                "domain":   domain,
                "page":     page_path,
                "category": "",
                "model":    MODEL_NAME,
                "error":    str(e)[:120],
            }
    # All 5 attempts exhausted by rate limits
    return {
        "domain":   domain,
        "page":     page_path,
        "category": "",
        "model":    MODEL_NAME,
        "error":    "rate limited after 5 attempts",
    }

# ── merge back to env CSV ─────────────────────────────────────────────────────

def update_env_csv(results: list[dict]):
    domain_cats = defaultdict(list)
    for r in results:
        if r.get("category") and not r.get("error"):
            domain_cats[r["domain"]].append(r["category"])

    env_rows = list(csv.DictReader(open(ENV_CSV, encoding="utf-8")))
    fieldnames = list(env_rows[0].keys())
    for col in ("top_llm_category", "llm_category_distribution", "llm_pages_classified"):
        if col not in fieldnames:
            fieldnames.append(col)

    for row in env_rows:
        cats = domain_cats.get(row["website"], [])
        if cats:
            c = Counter(cats)
            row["top_llm_category"]          = c.most_common(1)[0][0]
            row["llm_category_distribution"] = json.dumps(dict(c.most_common()))
            row["llm_pages_classified"]      = len(cats)
        else:
            row.setdefault("top_llm_category", "")
            row.setdefault("llm_category_distribution", "")
            row.setdefault("llm_pages_classified", "")

    with open(ENV_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(env_rows)
    print(f"Updated {ENV_CSV.name} with LLM summaries for {len(domain_cats)} domains.")

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    all_pages = load_env_pages()
    done      = load_done(OUT_CSV)
    remaining = [
        (page_id, page_path, content_text, domain)
        for page_id, page_path, content_text, domain in all_pages
        if page_path not in done
    ]

    print(f"Total env pages  : {len(all_pages)}")
    print(f"Already done     : {len(done)}")
    print(f"To classify      : {len(remaining)}")
    print(f"Workers          : {WORKERS}")
    print(f"Model            : claude-{MODEL_NAME}\n", flush=True)

    if not remaining:
        print("Nothing to do — running summary merge only.")
        all_results = list(csv.DictReader(open(OUT_CSV, encoding="utf-8")))
        update_env_csv(all_results)
        return

    write_header = not OUT_CSV.exists() or len(done) == 0
    csv_lock  = threading.Lock()
    buffer    = []
    completed = [0]

    def flush(force=False):
        with csv_lock:
            if buffer and (force or len(buffer) >= FLUSH_EVERY):
                mode = "w" if write_header and not OUT_CSV.exists() else "a"
                with open(OUT_CSV, mode, newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["domain", "page", "category", "model", "error"])
                    if mode == "w":
                        writer.writeheader()
                    writer.writerows(buffer)
                buffer.clear()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(classify_page, *args): args for args in remaining}
        for fut in as_completed(futures):
            result = fut.result()
            with csv_lock:
                buffer.append(result)
                completed[0] += 1
                n = completed[0]

            flush()

            if n % 10 == 0 or n == len(remaining):
                pct = n / len(remaining) * 100
                print(f"  {n}/{len(remaining)} ({pct:.0f}%)", flush=True)

    flush(force=True)

    all_done = list(csv.DictReader(open(OUT_CSV, encoding="utf-8")))
    update_env_csv(all_done)

    total_classified = sum(1 for r in all_done if r.get("category") and not r.get("error"))
    print(f"\nDone. {total_classified} pages classified across {len(all_pages)} total.")
    print("\nCategory breakdown:")
    cats = Counter(r["category"] for r in all_done if r.get("category") and not r.get("error"))
    for cat, n in cats.most_common():
        print(f"  {cat:<40s} {n:>5}")


if __name__ == "__main__":
    main()
