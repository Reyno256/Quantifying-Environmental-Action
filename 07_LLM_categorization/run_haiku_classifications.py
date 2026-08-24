"""
Classify ~1000 environmental pages with Claude Haiku 4.5.
Results written to haiku_classifications.csv.
"""

import csv
import random
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT    = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from gemini_as_a_judge import get_response_judge

ENV_DIR  = ROOT / "06_environmental_classification" / "environmental_html_pages"
OUT_CSV  = Path(__file__).parent / "haiku_classifications.csv"
N        = 1000
WORKERS  = 5
MAX_CHARS = 6_000
SEED     = 77
FLUSH_EVERY = 50

import time

def extract_text(path: Path) -> str:
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:MAX_CHARS]
    except Exception:
        return ""

def classify(path: Path) -> dict:
    rel = str(path.relative_to(ENV_DIR))
    text = extract_text(path)
    if not text:
        return {"page": rel, "category": "", "error": "no text"}
    for attempt in range(5):
        try:
            cat = get_response_judge(text)
            return {"page": rel, "category": cat.strip(), "error": ""}
        except Exception as e:
            if "429" in str(e) and attempt < 4:
                wait = 30 * (2 ** attempt)
                time.sleep(wait)
            else:
                return {"page": rel, "category": "", "error": str(e)[:120]}

def load_done(path: Path) -> set:
    """Only count successfully classified pages as done (errors get retried)."""
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r["page"] for r in csv.DictReader(f)
                if r.get("page") and r.get("category") and not r.get("error")}

# ── main ───────────────────────────────────────────────────────────────────────

all_pages = sorted(ENV_DIR.rglob("*.html"))
done      = load_done(OUT_CSV)

random.seed(SEED)
pool_pages = [p for p in all_pages if str(p.relative_to(ENV_DIR)) not in done]
sample     = random.sample(pool_pages, min(N, len(pool_pages)))

print(f"Total env pages : {len(all_pages)}")
print(f"Already done    : {len(done)}")
print(f"To classify     : {len(sample)}")
print(f"Workers         : {WORKERS}\n", flush=True)

write_header = not OUT_CSV.exists() or len(done) == 0
csv_lock  = threading.Lock()
buffer    = []
results   = []
completed  = [0]
token_est  = [0]          # rough input token count (chars/4)
t_start    = time.time()

AVG_TOKENS_PER_PAGE = 1271   # measured: ~363 fixed + ~908 content

def flush(force=False):
    with csv_lock:
        if buffer and (force or len(buffer) >= FLUSH_EVERY):
            mode = "w" if write_header and not OUT_CSV.exists() else "a"
            with open(OUT_CSV, mode, newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["page", "category", "error"])
                if mode == "w":
                    w.writeheader()
                w.writerows(buffer)
            results.extend(buffer)
            buffer.clear()

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = {pool.submit(classify, p): p for p in sample}
    for fut in as_completed(futures):
        r = fut.result()
        with csv_lock:
            buffer.append(r)
            completed[0] += 1
            n = completed[0]
            if r.get("category") and not r.get("error"):
                token_est[0] += AVG_TOKENS_PER_PAGE
        flush()
        if n % 100 == 0 or n == len(sample):
            elapsed = time.time() - t_start
            tpm = int(token_est[0] / elapsed * 60) if elapsed > 0 else 0
            print(f"  {n}/{len(sample)} ({n/len(sample)*100:.0f}%)  |  TPM ~{tpm:,}", flush=True)

flush(force=True)

# ── report ─────────────────────────────────────────────────────────────────────

all_done = list(csv.DictReader(open(OUT_CSV, encoding="utf-8")))
classified = [r for r in all_done if r.get("category") and not r.get("error")]
errors     = [r for r in all_done if r.get("error")]

cats = Counter(r["category"].strip().rstrip(".,").strip() for r in classified)

print(f"\n── Report {'─'*59}")
print(f"  Classified  : {len(classified)}")
print(f"  Errors      : {len(errors)}")
print(f"  N/A rate    : {cats.get('N/A', 0) / len(classified) * 100:.1f}%")
print(f"\n  Category breakdown:")
for cat, n in cats.most_common():
    bar = "█" * int(n / len(classified) * 40)
    print(f"    {cat:<40s} {n:>5}  {n/len(classified)*100:5.1f}%  {bar}")
