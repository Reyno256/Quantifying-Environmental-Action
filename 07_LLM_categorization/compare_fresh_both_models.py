"""
Run both Cohere Command-A+ and claude-haiku-4-5 on 40 freshly-selected pages.
Reports agreement, Cohen's Kappa, and disagreement breakdown.
"""

import random
import sys
import csv
import time
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(Path(__file__).parent))
from command_a_as_a_judge import get_response, build_prompt
from gemini_as_a_judge import get_response_judge

# ── config ─────────────────────────────────────────────────────────────────────

ENV_DIR   = ROOT / "06_environmental_classification" / "environmental_html_pages"
PREV_CSV  = Path(__file__).parent / "llm_classifications.csv"
N         = 40
SEED      = 137
MAX_CHARS = 6_000

CANONICAL = {
    "community", "spirituality and worship", "kitchen", "waste", "energy",
    "operations and maintenance", "environmental and climate justice", "water",
    "other", "n/a",
}

# ── helpers ────────────────────────────────────────────────────────────────────

def normalize(cat: str) -> str:
    c = cat.strip().rstrip(".,;").strip().lower()
    return c if c in CANONICAL else "other"


def extract_text(path: Path) -> str:
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:MAX_CHARS]
    except Exception:
        return ""


# ── pick pages ─────────────────────────────────────────────────────────────────

prev_pages = set()
if PREV_CSV.exists():
    with open(PREV_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            prev_pages.add(row.get("page", ""))

all_pages  = sorted(ENV_DIR.rglob("*.html"))
candidates = [p for p in all_pages if str(p.relative_to(ENV_DIR)) not in prev_pages]

random.seed(SEED)
sample = random.sample(candidates, min(N, len(candidates)))
print(f"Selected {len(sample)} pages (seed={SEED}, excluding {len(prev_pages)} previously seen).\n")

# ── classify with both models ──────────────────────────────────────────────────

results = []
for i, path in enumerate(sample, 1):
    rel  = str(path.relative_to(ENV_DIR))
    text = extract_text(path)
    if not text:
        print(f"  [{i:2d}] SKIP (no text): {rel}")
        continue

    try:
        cmd_raw = get_response(text)
    except Exception as e:
        print(f"  [{i:2d}] Command A+ ERROR: {e}")
        continue

    try:
        haiku_raw = get_response_judge(text)
    except Exception as e:
        print(f"  [{i:2d}] Haiku ERROR: {e}")
        continue

    cmd_norm   = normalize(cmd_raw)
    haiku_norm = normalize(haiku_raw)
    match = "✓" if cmd_norm == haiku_norm else "✗"

    print(f"  [{i:2d}] {match}  cmd-a+: {cmd_norm:<35s}  haiku: {haiku_norm}")
    time.sleep(3)   # stay under 20 req/min on Cohere trial key
    results.append({
        "page":      rel,
        "cmd":       cmd_norm,
        "haiku":     haiku_norm,
        "cmd_raw":   cmd_raw,
        "haiku_raw": haiku_raw,
    })

# ── Cohen's Kappa ──────────────────────────────────────────────────────────────

cmd_labels   = [r["cmd"]   for r in results]
haiku_labels = [r["haiku"] for r in results]
n     = len(results)
agree = sum(c == h for c, h in zip(cmd_labels, haiku_labels))

p_o   = agree / n
c_cnt = Counter(cmd_labels)
h_cnt = Counter(haiku_labels)
cats  = set(cmd_labels) | set(haiku_labels)
p_e   = sum((c_cnt[c] / n) * (h_cnt[c] / n) for c in cats)
kappa = (p_o - p_e) / (1 - p_e) if p_e < 1 else 1.0

kappa_interp = (
    "almost perfect (≥0.80)" if kappa >= 0.80 else
    "substantial (0.60–0.80)" if kappa >= 0.60 else
    "moderate (0.40–0.60)"   if kappa >= 0.40 else
    "fair (0.20–0.40)"       if kappa >= 0.20 else
    "slight (<0.20)"
)

print(f"\n── Results {'─'*58}")
print(f"  Pages compared : {n}")
print(f"  Agreements     : {agree} / {n} ({agree/n*100:.1f}%)")
print(f"  Cohen's Kappa  : {kappa:.4f}  [{kappa_interp}]")

# ── disagreement summary ───────────────────────────────────────────────────────

disagree = [r for r in results if r["cmd"] != r["haiku"]]
if disagree:
    print(f"\n── Disagreements ({len(disagree)} pages) {'─'*44}")
    pair_counts = Counter((r["cmd"], r["haiku"]) for r in disagree)
    print("\n  command-a+ → haiku  (by frequency):")
    for (c, h), cnt in pair_counts.most_common():
        print(f"    {c:<35s} → {h}  (×{cnt})")

    print("\n  Sample disagreements:")
    for r in disagree[:10]:
        print(f"    {Path(r['page']).name}")
        print(f"      cmd-a+ : {r['cmd_raw']!r}")
        print(f"      haiku  : {r['haiku_raw']!r}")

# ── category distributions ─────────────────────────────────────────────────────

print(f"\n── Category distributions {'─'*43}")
all_cats = sorted(set(cmd_labels) | set(haiku_labels))
print(f"  {'Category':<38s}  cmd-a+  haiku")
for cat in all_cats:
    print(f"  {cat:<38s}  {c_cnt[cat]:>6d}  {h_cnt[cat]:>5d}")
