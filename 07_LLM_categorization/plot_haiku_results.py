"""
4 plots summarizing Claude Haiku 4.5 classification results on 1,241 env pages.
"""

import csv
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

HERE = Path(__file__).parent
CSV  = HERE / "haiku_classifications.csv"

CANONICAL = {
    "Community", "Spirituality and Worship", "Kitchen", "Waste", "Energy",
    "Operations and Maintenance", "Environmental and Climate Justice",
    "Water", "Other", "N/A",
}
CAT_COLORS = {
    "N/A":                              "#d0d0d0",
    "Community":                        "#4e79a7",
    "Spirituality and Worship":         "#f28e2b",
    "Energy":                           "#e15759",
    "Environmental and Climate Justice":"#76b7b2",
    "Kitchen":                          "#59a14f",
    "Other":                            "#edc948",
    "Operations and Maintenance":       "#b07aa1",
    "Waste":                            "#ff9da7",
    "Water":                            "#9c755f",
}

def normalize(cat: str) -> str:
    c = cat.strip().rstrip(".,").strip()
    return c if c in CANONICAL else "Other"

# ── load ────────────────────────────────────────────────────────────────────────
rows = [r for r in csv.DictReader(open(CSV, encoding="utf-8"))
        if r.get("category") and not r.get("error")]

for r in rows:
    r["category"] = normalize(r["category"])
    r["domain"]   = r["page"].split("/")[0]

# ── aggregates ──────────────────────────────────────────────────────────────────
cat_counts   = Counter(r["category"] for r in rows)
non_na       = {k: v for k, v in cat_counts.items() if k != "N/A"}
domain_cats  = defaultdict(list)
for r in rows:
    domain_cats[r["domain"]].append(r["category"])

# domains with ≥5 pages classified
active_domains = {d: cats for d, cats in domain_cats.items() if len(cats) >= 5}
domain_env_rate = {
    d: sum(1 for c in cats if c != "N/A") / len(cats)
    for d, cats in active_domains.items()
}
top_env_domains = sorted(domain_env_rate, key=domain_env_rate.get, reverse=True)[:15]

fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle("Claude Haiku 4.5 — Environmental Page Classifications (n=1,241)",
             fontsize=14, fontweight="bold", y=1.01)

# ── Plot 1: Full category distribution (horizontal bar) ────────────────────────
ax = axes[0, 0]
cats_sorted = sorted(cat_counts, key=cat_counts.get)
counts      = [cat_counts[c] for c in cats_sorted]
colors      = [CAT_COLORS.get(c, "#aaaaaa") for c in cats_sorted]
bars = ax.barh(cats_sorted, counts, color=colors, edgecolor="white", linewidth=0.5)
for bar, count in zip(bars, counts):
    pct = count / len(rows) * 100
    ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
            f"{pct:.1f}%", va="center", fontsize=8)
ax.set_xlabel("Pages")
ax.set_title("All Categories (incl. N/A)", fontweight="bold")
ax.set_xlim(0, max(counts) * 1.18)
ax.spines[["top", "right"]].set_visible(False)

# ── Plot 2: Non-N/A category breakdown (pie) ───────────────────────────────────
ax = axes[0, 1]
non_na_sorted = sorted(non_na, key=non_na.get, reverse=True)
sizes  = [non_na[c] for c in non_na_sorted]
colors = [CAT_COLORS.get(c, "#aaaaaa") for c in non_na_sorted]
wedges, texts, autotexts = ax.pie(
    sizes, labels=non_na_sorted, colors=colors,
    autopct=lambda p: f"{p:.1f}%" if p > 3 else "",
    startangle=140, pctdistance=0.75,
    wedgeprops={"edgecolor": "white", "linewidth": 1}
)
for t in texts:
    t.set_fontsize(8)
for at in autotexts:
    at.set_fontsize(8)
total_non_na = sum(sizes)
ax.set_title(f"Environmental Category Breakdown\n(excl. N/A, n={total_non_na})",
             fontweight="bold")

# ── Plot 3: N/A rate by domain (top 15 domains with most env content) ──────────
ax = axes[1, 0]
env_rates = [domain_env_rate[d] * 100 for d in top_env_domains]
bar_colors = ["#4e79a7" if r >= 50 else "#76b7b2" if r >= 25 else "#aed6f1"
              for r in env_rates]
y = np.arange(len(top_env_domains))
bars = ax.barh(y, env_rates, color=bar_colors, edgecolor="white")
ax.set_yticks(y)
ax.set_yticklabels([d.replace(".org","").replace(".com","")
                    for d in top_env_domains], fontsize=8)
ax.set_xlabel("% pages classified as environmental (non-N/A)")
ax.set_title("Top 15 Domains by Environmental Content Rate\n(≥5 pages classified)",
             fontweight="bold")
ax.axvline(x=sum(env_rates)/len(env_rates), color="red", linestyle="--",
           linewidth=1, alpha=0.6, label="sample mean")
ax.legend(fontsize=8)
ax.spines[["top", "right"]].set_visible(False)
for bar, rate in zip(bars, env_rates):
    n = len(active_domains[top_env_domains[int(bar.get_y() + 0.4)]])
    ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
            f"{rate:.0f}%", va="center", fontsize=8)

# ── Plot 4: Stacked bar — category mix for top domains by page volume ──────────
ax = axes[1, 1]
top_by_volume = sorted(active_domains, key=lambda d: len(active_domains[d]),
                       reverse=True)[:12]
cat_order = ["N/A", "Community", "Spirituality and Worship", "Energy",
             "Environmental and Climate Justice", "Kitchen", "Other",
             "Operations and Maintenance", "Waste", "Water"]

data = {c: [] for c in cat_order}
for d in top_by_volume:
    c_counts = Counter(active_domains[d])
    total    = len(active_domains[d])
    for c in cat_order:
        data[c].append(c_counts.get(c, 0) / total * 100)

x      = np.arange(len(top_by_volume))
bottom = np.zeros(len(top_by_volume))
for c in cat_order:
    vals = np.array(data[c])
    if vals.sum() > 0:
        ax.bar(x, vals, bottom=bottom, color=CAT_COLORS.get(c, "#aaa"),
               label=c, edgecolor="white", linewidth=0.4)
        bottom += vals

ax.set_xticks(x)
ax.set_xticklabels(
    [d.replace(".org","").replace(".com","") for d in top_by_volume],
    rotation=35, ha="right", fontsize=7)
ax.set_ylabel("% of domain's classified pages")
ax.set_title("Category Mix — Top 12 Domains by Page Volume", fontweight="bold")
ax.set_ylim(0, 105)
handles = [mpatches.Patch(color=CAT_COLORS[c], label=c)
           for c in cat_order if sum(data[c]) > 0]
ax.legend(handles=handles, fontsize=6.5, loc="upper right",
          ncol=1, framealpha=0.8)
ax.spines[["top", "right"]].set_visible(False)

plt.tight_layout()
out = HERE / "haiku_classification_plots.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"Saved → {out}")
plt.show()
