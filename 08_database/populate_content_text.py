"""
Populate web_pages.content_text for all rows where it is currently NULL.

Reads HTML files from disk using multiprocessing (real CPU parallelism for
BeautifulSoup), then batch-UPDATEs the database.

Usage:
    python populate_content_text.py
    python populate_content_text.py --workers 8   # default: cpu_count()
    python populate_content_text.py --batch 1000  # DB update batch size
"""

import argparse
import os
import sys
import time
from multiprocessing import Pool, cpu_count
from pathlib import Path

import subprocess

import psycopg2
import psycopg2.extras
from bs4 import BeautifulSoup
from dotenv import load_dotenv


def _find_root() -> Path:
    """Return the main git worktree root (where HTML files live)."""
    # 'git worktree list --porcelain' lists main worktree first
    out = subprocess.check_output(
        ["git", "worktree", "list", "--porcelain"],
        cwd=Path(__file__).parent,
        text=True,
    )
    first_line = out.splitlines()[0]          # e.g. "worktree /path/to/repo"
    main_root = Path(first_line.split(None, 1)[1])
    html_dir = main_root / "05_web_scraping" / "html_pages"
    if not html_dir.exists():
        # fallback: script's own parent tree
        return Path(__file__).parent.parent
    return main_root


ROOT = _find_root()
load_dotenv(ROOT / ".env")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def extract_one(args: tuple) -> tuple:
    """Worker function: read + parse one HTML file, return (id, text)."""
    row_id, rel_path = args
    full_path = ROOT / rel_path
    try:
        html = full_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)[:500_000]
        # Remove NUL characters that PostgreSQL cannot store in string columns
        text = text.replace('\x00', '')
    except Exception:
        text = ""
    return row_id, text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=cpu_count(),
                        help="Parallel extraction workers (default: cpu count)")
    parser.add_argument("--batch", type=int, default=500,
                        help="DB update batch size (default: 500)")
    args = parser.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT id, '05_web_scraping/html_pages/' || page_path FROM web_pages WHERE content_text IS NULL OR content_text = '' ORDER BY id")
    rows = cur.fetchall()
    total = len(rows)
    if total == 0:
        print("content_text is already populated for all rows.")
        cur.close(); conn.close(); return

    print(f"Rows to update : {total:,}")
    print(f"Workers        : {args.workers}")
    print(f"DB batch size  : {args.batch}")
    print(f"Root           : {ROOT}\n", flush=True)

    t_start = time.perf_counter()
    done = 0

    with Pool(processes=args.workers) as pool:
        buffer = []
        for row_id, text in pool.imap_unordered(extract_one, rows, chunksize=64):
            buffer.append((text, row_id))
            done += 1

            if len(buffer) >= args.batch:
                psycopg2.extras.execute_batch(
                    cur,
                    "UPDATE web_pages SET content_text = %s WHERE id = %s",
                    buffer,
                    page_size=args.batch,
                )
                conn.commit()
                buffer.clear()
                elapsed = time.perf_counter() - t_start
                rate = done / elapsed
                eta = (total - done) / rate
                print(f"  {done:>6,}/{total:,}  ({done/total*100:.0f}%)  "
                      f"{rate:.0f} pages/sec  ETA {eta:.0f}s", flush=True)

        if buffer:
            psycopg2.extras.execute_batch(
                cur,
                "UPDATE web_pages SET content_text = %s WHERE id = %s",
                buffer,
                page_size=args.batch,
            )
            conn.commit()

    elapsed = time.perf_counter() - t_start
    print(f"\nDone. {total:,} rows updated in {elapsed:.1f}s ({total/elapsed:.0f} pages/sec)")

    cur.execute("SELECT COUNT(*) FROM web_pages WHERE content_text IS NOT NULL AND length(content_text) > 50")
    print(f"Rows with meaningful text: {cur.fetchone()[0]:,}")

    cur.close(); conn.close()


if __name__ == "__main__":
    main()
