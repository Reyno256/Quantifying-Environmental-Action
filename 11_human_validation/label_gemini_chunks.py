"""
label_gemini_chunks.py — Blind keyboard-driven labeler for inter-rater
reliability between a human and the Gemini chunk classifier.

Pulls a random sample of already-classified rows from
page_chunk_classifications, shows ONLY the chunk text (never Gemini's
category), and lets you assign one of the 9 major categories (or N/A / Skip)
with a single keypress. Gemini's own label is recorded alongside your label
in the output file for later agreement analysis, but is never displayed
during the session — so your rating isn't anchored by it.

Saves progress after every entry, so you can quit and resume; already-rated
rows are skipped on the next run for the same seed/output file.

Usage:
    python label_gemini_chunks.py                       # 50 samples, seed 42
    python label_gemini_chunks.py --n 200
    python label_gemini_chunks.py --n 200 --seed 7 --out my_irr_run.csv

Output:
    gemini_chunk_irr.csv (or --out) — one row per rated chunk:
        chunk_id, web_page_id, chunk_index, page_path,
        human_label, gemini_category, gemini_major_category
"""

import argparse
import csv
import os
import random
import sys
import termios
import textwrap
import time
import tty
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent
while not (ROOT / ".env").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

CATEGORIES = [
    (1, "Community"),
    (2, "Spirituality & Worship"),
    (3, "Kitchen"),
    (4, "Waste"),
    (5, "Energy"),
    (6, "Operations & Maintenance"),
    (7, "Environmental & Climate Justice"),
    (8, "Water"),
    (9, "Other"),
]
KEY_MAP = {str(i): label for i, label in CATEGORIES}
KEY_MAP["0"] = "N/A"
KEY_MAP["s"] = "__SKIP__"
KEY_MAP["S"] = "__SKIP__"
KEY_MAP["q"] = "__QUIT__"
KEY_MAP["Q"] = "__QUIT__"

FIELDNAMES = [
    "chunk_id", "web_page_id", "chunk_index", "page_path",
    "human_label", "gemini_category", "gemini_major_category",
]


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── terminal helpers (same as label_tool.py / review_tool.py) ─────────────────

def getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def clear():
    os.system("clear")


def wrap(text: str, width: int = 88, indent: int = 4) -> str:
    prefix = " " * indent
    return "\n".join(
        textwrap.fill(line, width=width, initial_indent=prefix,
                      subsequent_indent=prefix)
        for line in text.splitlines()
    ) or prefix + "(empty)"


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_done(out_csv: Path) -> set[str]:
    if not out_csv.exists():
        return set()
    with open(out_csv, newline="", encoding="utf-8") as f:
        return {r["chunk_id"] for r in csv.DictReader(f) if r.get("chunk_id")}


def save_result(row: dict, out_csv: Path):
    exists = out_csv.exists()
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            w.writeheader()
        w.writerow(row)


# ── sampling ──────────────────────────────────────────────────────────────────

def build_sample(n: int, seed: int) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()

    # Light pass first: just the PKs of everything eligible, so we're not
    # pulling 670k+ chunk_text blobs over the tunnel just to sample from them.
    cur.execute("""
        SELECT id FROM page_chunk_classifications
        WHERE (error IS NULL OR error = '')
    """)
    all_ids = [r[0] for r in cur.fetchall()]

    rng = random.Random(seed)
    sample_ids = rng.sample(all_ids, min(n, len(all_ids)))

    cur.execute("""
        SELECT pcc.id, pcc.web_page_id, pcc.chunk_index, pcc.chunk_text,
               pcc.category, pcc.major_category, wp.page_path
        FROM page_chunk_classifications pcc
        JOIN web_pages wp ON wp.id = pcc.web_page_id
        WHERE pcc.id = ANY(%s)
    """, (sample_ids,))
    rows = cur.fetchall()
    conn.close()

    entries = []
    for chunk_id, page_id, chunk_index, chunk_text, category, major_category, page_path in rows:
        entries.append({
            "chunk_id": chunk_id,
            "web_page_id": page_id,
            "chunk_index": chunk_index,
            "page_path": page_path,
            "chunk_text": chunk_text or "",
            "gemini_category": category,
            "gemini_major_category": major_category,
        })
    rng.shuffle(entries)  # DB's ANY() doesn't preserve sample_ids order
    return entries


# ── display ───────────────────────────────────────────────────────────────────

def render(entry: dict, remaining: int, total: int, done_count: int):
    clear()
    W = 92
    print("═" * W)
    print(f"  Rated: {done_count}   Remaining: {remaining}   Total: {total}")
    print("═" * W)
    print(f"  Page : {entry['page_path']}")
    print(f"  Chunk: #{entry['chunk_index']}")
    print()
    print("  Chunk text:")
    print(wrap(entry["chunk_text"], width=88, indent=4))
    print()
    print("─" * W)
    print()
    left = CATEGORIES[:5]
    right = CATEGORIES[5:]
    for i, (lkey, lcat) in enumerate(left):
        if i < len(right):
            rkey, rcat = right[i]
            rpart = f"    {rkey}  {rcat}"
        else:
            rpart = ""
        print(f"    {lkey}  {lcat:<36s}{rpart}")
    print()
    print(f"    0  N/A (no environmental action){' ' * 8}s  Skip")
    print(f"    q  Save & quit")
    print()
    print("─" * W)
    print("  Key: ", end="", flush=True)


# ── review loop ───────────────────────────────────────────────────────────────

def run_review(queue: list[dict], out_csv: Path, done: set[str]):
    done_count = len(done)
    remaining = [e for e in queue if str(e["chunk_id"]) not in done]

    if not remaining:
        print(f"  All {len(queue)} sampled chunks already rated. Nothing to do.")
        return

    print(f"\n  {len(done)} already rated,  {len(remaining)} remaining this run.")
    print("  Your rating is recorded before Gemini's label is ever shown to you.")
    print("  Press any key to start…", end="", flush=True)
    getch()

    for idx, entry in enumerate(remaining):
        while True:
            render(entry, remaining=len(remaining) - idx, total=len(queue),
                   done_count=done_count)
            key = getch()
            if key not in KEY_MAP:
                print(f" '{key}' — unknown, use 0-9 / s / q")
                time.sleep(0.6)
                continue
            action = KEY_MAP[key]
            break

        if action == "__QUIT__":
            clear()
            print(f"\n  Saved and quit. {done_count} chunks rated total.\n")
            return

        if action == "__SKIP__":
            continue

        save_result({
            "chunk_id": entry["chunk_id"],
            "web_page_id": entry["web_page_id"],
            "chunk_index": entry["chunk_index"],
            "page_path": entry["page_path"],
            "human_label": action,
            "gemini_category": entry["gemini_category"],
            "gemini_major_category": entry["gemini_major_category"],
        }, out_csv)
        done_count += 1

    clear()
    print(f"\n  Done! {done_count} chunks rated -> {out_csv}\n")
    _print_agreement(out_csv)


def _print_agreement(out_csv: Path):
    """Post-session only — safe to reveal Gemini's labels now that rating is over."""
    with open(out_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return
    human_na = {r["human_label"] for r in rows if r["human_label"] == "N/A"}
    def gemini_bucket(r):
        return r["gemini_major_category"] or "N/A"
    agree = sum(1 for r in rows if r["human_label"] == gemini_bucket(r))
    print(f"  Human/Gemini exact-bucket agreement: {agree}/{len(rows)} "
          f"({agree / len(rows) * 100:.1f}%)")
    print(f"  (Run this file through your IRR metric of choice — e.g. Cohen's kappa —")
    print(f"   using the human_label vs. gemini_major_category columns.)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50,
                        help="Number of chunks to sample (default 50)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for sampling (default 42)")
    parser.add_argument("--out", default="gemini_chunk_irr.csv",
                        help="Output CSV filename, relative to this script's "
                             "directory (default gemini_chunk_irr.csv)")
    args = parser.parse_args()
    out_csv = Path(__file__).parent / args.out

    print(f"Sampling {args.n} classified chunks (seed={args.seed})…", flush=True)
    queue = build_sample(args.n, args.seed)
    print(f"  Sampled: {len(queue)}")

    done = load_done(out_csv)
    run_review(queue, out_csv, done)


if __name__ == "__main__":
    main()
