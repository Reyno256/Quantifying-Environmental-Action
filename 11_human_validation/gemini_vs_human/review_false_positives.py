"""
review_false_positives.py

Shows N random samples where Gemini and the human reviewer disagree.

Usage:
    python review_false_positives.py --type fp        # Gemini+ human- (default)
    python review_false_positives.py --type fn        # Gemini- human+
    python review_false_positives.py --type fp --n 10 --seed 99 --chars 2000
"""

import argparse
import csv
import os
import random
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent
while not (ROOT / ".env").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "06_environmental_classification"))
from keyword_search import load_keywords, find_keywords

_KW_PATTERN    = load_keywords()
GEMINI_MODEL   = "gemini-3.1-flash-lite"
ACTION_COL_START = 4


def url_to_path(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        return ""
    p = urlparse(url)
    domain = p.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    path = p.path.lstrip("/")
    return f"{domain}/{path}" if path else domain


_WEB_EXTS    = (".html", ".htm", ".php", ".asp", ".aspx", ".cfm", ".shtml")
_TRAIL_PUNCT = ".,-_~"

def canonical_path(path: str) -> str:
    p = path.strip().lower().split("?")[0].split("#")[0]
    p = p.rstrip("/").rstrip(_TRAIL_PUNCT)
    for idx in ("/index.html", "/index.htm"):
        if p.endswith(idx):
            p = p[:-len(idx)].rstrip("/").rstrip(_TRAIL_PUNCT)
            break
    for ext in _WEB_EXTS:
        if p.endswith(ext):
            p = p[:-len(ext)].rstrip("/").rstrip(_TRAIL_PUNCT)
            break
    return p


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_human_labels() -> dict[str, bool]:
    human: dict[str, bool] = {}
    with open(ROOT / "America_results.csv", encoding="latin-1", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        next(reader)
        for row in reader:
            if len(row) <= ACTION_COL_START:
                continue
            url = row[2].strip()
            if not url.startswith("http"):
                continue
            base = url_to_path(url)
            if not base:
                continue
            is_positive = any(v.strip().upper() == "TRUE" for v in row[ACTION_COL_START:])
            canon = canonical_path(base)
            if canon:
                human[canon] = is_positive
    return human


def main():
    parser = argparse.ArgumentParser(
        description="Review pages where Gemini and human reviewer disagree"
    )
    parser.add_argument(
        "--type", choices=["fp", "fn"], default="fp",
        help="fp = false positives (Gemini+, human−)  "
             "fn = false negatives (Gemini−, human+)  "
             "(default: fp)"
    )
    parser.add_argument("--n",     type=int, default=5,    help="Number of samples (default 5)")
    parser.add_argument("--seed",  type=int, default=42,   help="Random seed (default 42)")
    parser.add_argument("--chars", type=int, default=1500, help="Max content chars to show (default 1500)")
    args = parser.parse_args()

    label = "false positives (Gemini+, human−)" if args.type == "fp" \
            else "false negatives (Gemini−, human+)"

    print("Loading human labels…", flush=True)
    human = load_human_labels()

    print("Querying DB…", flush=True)
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT wp.page_path, wp.content_text, lc.category
        FROM   web_pages wp
        JOIN   llm_classifications lc ON lc.web_page_id = wp.id
        WHERE  lc.model_used = %s
          AND  lc.error      IS NULL
          AND  lc.category   IS NOT NULL
    """, (GEMINI_MODEL,))
    rows = cur.fetchall()
    conn.close()

    candidates = []
    seen: set[str] = set()
    for page_path, text, category in rows:
        if not text or not find_keywords(text, _KW_PATTERN):
            continue
        canon = canonical_path(page_path)
        if not canon or canon not in human:
            continue
        if canon in seen:
            continue
        seen.add(canon)

        gemini_pos = category.strip() != "N/A"
        human_pos  = human[canon]

        if args.type == "fp" and gemini_pos and not human_pos:
            candidates.append((page_path, text, category))
        elif args.type == "fn" and not gemini_pos and human_pos:
            candidates.append((page_path, text, category))

    print(f"Total {label}: {len(candidates)}\n")

    if not candidates:
        print("No matching pages found.")
        return

    rng     = random.Random(args.seed)
    samples = rng.sample(candidates, min(args.n, len(candidates)))
    width   = 72

    for i, (page_path, text, category) in enumerate(samples, 1):
        print("═" * width)
        print(f"  Sample {i} of {len(samples)}  [{label}]")
        print(f"  URL      : {page_path}")
        print(f"  Gemini   : {category}")
        # center window on the first keyword hit; mark all hits with [[ ]]
        first_match = _KW_PATTERN.search(text)
        if first_match:
            half  = args.chars // 2
            start = max(0, first_match.start() - half)
            end   = min(len(text), start + args.chars)
            if end == len(text):
                start = max(0, end - args.chars)
            excerpt = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
            excerpt = _KW_PATTERN.sub(lambda m: f"[[{m.group(0)}]]", excerpt)
            kw_list = ", ".join(find_keywords(text, _KW_PATTERN))
            print(f"  Keywords : {kw_list}")
        else:
            excerpt = text[:args.chars]

        print("─" * width)
        for line in textwrap.wrap(excerpt, width=width - 2):
            print(f"  {line}")
        print()

    print("═" * width)


if __name__ == "__main__":
    main()
