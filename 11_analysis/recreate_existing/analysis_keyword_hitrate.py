"""
Keyword hit rate: for each matched keyword, what fraction of its occurrences were
actually classified as an environmental action (category <> 'N/A')?

Every page_keyword_match maps 1:1 to an llm_chunk_classification, so an
"occurrence" of a keyword is one match, and a "hit" is an occurrence the LLM
classified as a real action. This diagnoses which keywords are precise signals of
environmental action vs which are noisy (match a lot but rarely a real action).

Structurally keyword_chunks-only: "keyword" is a page_keyword_matches concept
with no page_chunks equivalent (page_chunks classifies every fixed-length chunk,
not keyword-triggered windows), so this script takes no --source flag.

Outputs:
  keyword_hitrate.csv          one row per keyword: total, hits, hit_rate
  keyword_hitrate.json         summary stats (n_keywords, global/median hit rate)
  fig_keyword_hitrate.png      scatter of hit rate (y) vs total occurrences (x, log)

    .venv/bin/python analysis_keyword_hitrate.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import recreate_common as rc

HERE = Path(__file__).resolve().parent

POINT_COLOR = "#2166ac"
LABEL_TOP_N = 10          # label this many highest-volume keywords


def load_keyword_hitrate(conn) -> pd.DataFrame:
    """One row per keyword: total occurrences, hits (real actions), hit_rate."""
    q = """
        SELECT pkm.keyword AS keyword,
               COUNT(*) AS total,
               COUNT(*) FILTER (
                   WHERE l.category <> 'N/A'
                     AND (l.error IS NULL OR l.error = '')) AS hits
        FROM llm_chunk_classifications l
        JOIN page_keyword_matches pkm ON pkm.id = l.match_id
        GROUP BY pkm.keyword
    """
    df = pd.read_sql(q, conn)
    df["hit_rate"] = df["hits"] / df["total"]
    return df.sort_values("total", ascending=False).reset_index(drop=True)


def main():
    conn = rc.get_conn()
    df = load_keyword_hitrate(conn)
    conn.close()

    global_rate = df["hits"].sum() / df["total"].sum()
    summary = {
        "n_keywords": int(len(df)),
        "total_occurrences": int(df["total"].sum()),
        "total_hits": int(df["hits"].sum()),
        "global_hit_rate": round(float(global_rate), 4),
        "median_keyword_hit_rate": round(float(df["hit_rate"].median()), 4),
    }
    df.to_csv(HERE / "keyword_hitrate.csv", index=False)
    (HERE / "keyword_hitrate.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    _plot(df, global_rate)


def _plot(df, global_rate):
    plt = rc.apply_style()
    fig, ax = plt.subplots(figsize=(11, 8.5))

    ax.scatter(df["total"], 100 * df["hit_rate"], s=18, color=POINT_COLOR,
               alpha=0.35, edgecolor="none", zorder=3)

    # reference line: the pooled hit rate across all occurrences
    ax.axhline(100 * global_rate, color="#b2182b", lw=1.5, ls="--", zorder=2,
               label=f"pooled hit rate = {100 * global_rate:.1f}%")

    ax.set_xscale("log")
    ax.set_xlabel("Total occurrences of keyword (log scale)")
    ax.set_ylabel("Hit rate  (% of occurrences classified as a real action)")
    ax.set_ylim(-3, 103)

    # Label the highest-volume keywords (never every point), nudged to the right.
    for _, r in df.head(LABEL_TOP_N).iterrows():
        ax.annotate(r["keyword"], (r["total"], 100 * r["hit_rate"]),
                    xytext=(6, 0), textcoords="offset points",
                    fontsize=rc.FS_ANNOTATION, color="#333333", va="center", zorder=4)

    ax.legend(loc="upper left", frameon=False)
    ax.grid(True, which="both", axis="x", alpha=0.15)
    fig.tight_layout()
    fig.savefig(HERE / "fig_keyword_hitrate.png")


if __name__ == "__main__":
    main()
