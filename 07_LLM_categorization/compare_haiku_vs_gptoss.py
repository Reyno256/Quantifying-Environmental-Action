"""
Compare Claude Haiku vs gpt-oss-120b:free classifications on 50 pages
already classified by gpt-oss. Reports Cohen's Kappa and disagreements.
"""

import csv
import random
import sys
from pathlib import Path

from collections import Counter
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from gemini_as_a_judge import get_response_judge, build_prompt

# ── config ─────────────────────────────────────────────────────────────────────

ROOT    = Path(__file__).parent.parent
CSV_IN  = Path(__file__).parent / "llm_classifications.csv"
ENV_DIR = ROOT / "06_environmental_classification" / "environmental_html_pages"
N       = 50
MAX_CHARS = 6_000
SEED    = 42

CANONICAL = {
    "community", "spirituality and worship", "kitchen", "waste", "energy",
    "operations and maintenance", "environmental and climate justice", "water", "other",
}

# ── helpers ────────────────────────────────────────────────────────────────────

def normalize(cat: str) -> str:
    """Strip punctuation/whitespace and lowercase; map to 'other' if unrecognized."""
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


# ── load gpt-oss classified pages ──────────────────────────────────────────────

rows = []
with open(CSV_IN, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if (row.get("model") == "gpt-oss-120b:free"
                and row.get("category")
                and not row.get("error")):
            rows.append(row)

random.seed(SEED)
sample = random.sample(rows, min(N, len(rows)))
print(f"Sampled {len(sample)} pages from gpt-oss-classified rows.\n")

# ── classify with Haiku ────────────────────────────────────────────────────────

results = []
for i, row in enumerate(sample, 1):
    page_path = ENV_DIR / row["page"]
    text = extract_text(page_path)
    if not text:
        print(f"  [{i:2d}] SKIP (no text): {row['page']}")
        continue

    haiku_raw = get_response_judge(text)
    gptoss_norm = normalize(row["category"])
    haiku_norm  = normalize(haiku_raw)

    match = "✓" if gptoss_norm == haiku_norm else "✗"
    print(f"  [{i:2d}] {match}  gpt-oss: {gptoss_norm:<35s}  haiku: {haiku_norm}")

    results.append({
        "page":      row["page"],
        "gptoss":    gptoss_norm,
        "haiku":     haiku_norm,
        "gptoss_raw": row["category"],
        "haiku_raw":  haiku_raw,
    })

# ── Cohen's Kappa ──────────────────────────────────────────────────────────────

gptoss_labels = [r["gptoss"] for r in results]
haiku_labels  = [r["haiku"]  for r in results]

agree = sum(g == h for g, h in zip(gptoss_labels, haiku_labels))
n = len(results)
# Cohen's Kappa: κ = (p_o - p_e) / (1 - p_e)
p_o = agree / n
g_counts_all = Counter(gptoss_labels)
h_counts_all = Counter(haiku_labels)
all_labels = set(gptoss_labels) | set(haiku_labels)
p_e = sum((g_counts_all[c] / n) * (h_counts_all[c] / n) for c in all_labels)
kappa = (p_o - p_e) / (1 - p_e) if p_e < 1 else 1.0

print(f"\n── Results ({'─'*50})")
print(f"  Pages compared : {len(results)}")
print(f"  Agreements     : {agree} / {len(results)} ({agree/len(results)*100:.1f}%)")
kappa_interp = (
    "almost perfect (≥0.80)" if kappa >= 0.80 else
    "substantial (0.60–0.80)" if kappa >= 0.60 else
    "moderate (0.40–0.60)" if kappa >= 0.40 else
    "fair (0.20–0.40)" if kappa >= 0.20 else
    "slight (<0.20)"
)
print(f"  Cohen's Kappa  : {kappa:.4f}  [{kappa_interp}]")

# ── disagreement summary ───────────────────────────────────────────────────────

disagree = [r for r in results if r["gptoss"] != r["haiku"]]
if disagree:
    print(f"\n── Disagreements ({len(disagree)} pages) {'─'*40}")
    pair_counts = Counter((r["gptoss"], r["haiku"]) for r in disagree)
    print("\n  gpt-oss → haiku  (disagreement pairs, by frequency):")
    for (g, h), n in pair_counts.most_common():
        print(f"    {g:<35s} → {h}  (×{n})")

    print("\n  Sample disagreements:")
    for r in disagree[:10]:
        print(f"    {Path(r['page']).name}")
        print(f"      gpt-oss : {r['gptoss_raw']!r}")
        print(f"      haiku   : {r['haiku_raw']!r}")

# ── category distribution comparison ──────────────────────────────────────────

print(f"\n── Category distributions {'─'*43}")
g_counts = Counter(gptoss_labels)
h_counts = Counter(haiku_labels)
all_cats = sorted(set(gptoss_labels) | set(haiku_labels))
print(f"  {'Category':<38s}  gpt-oss  haiku")
for cat in all_cats:
    print(f"  {cat:<38s}  {g_counts[cat]:>7d}  {h_counts[cat]:>5d}")
