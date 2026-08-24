"""
Kruskal-Wallis H-test: does the per-synagogue environmental action count differ
across distance-to-nearest-feature tiers, for each of the 7 OSM site types?

For each site type the distance to the nearest feature is binned into three
fixed proximity tiers:
    close  : < 25 km
    medium : 25-50 km
    far    : > 50 km
and a Kruskal-Wallis test compares the action-count distribution across the
tiers.  Null hypothesis: all tier distributions are equal (no relation between
proximity tier and action count).  P-values are Bonferroni-corrected over the
7 tests.  The non-parametric test is used because action counts are zero-inflated
and heavily right-skewed (many zeros, long tail), violating ANOVA's assumptions.
Empty tiers (e.g. green_space, which is almost always < 25 km away) are dropped;
a test needs at least two non-empty tiers.

(Filename retains the historical "_anova" suffix to avoid breaking references.)

Input : action_vs_osm_distance.csv  (n_actions + dist_<type> per synagogue)
Outputs:
  action_vs_osm_distance_anova.json
  (summary also printed to stdout)

Usage:
    python 11_analysis/action_vs_osm_distance_anova.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import argparse
import sys

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

OUT = HERE          # reassigned in main() from --source

SITE_TYPES = [
    "green_space", "env_hazard", "landfill",
    "industrial", "abattoir", "military", "airport",
]

# Fixed proximity tiers, in metres.
BIN_EDGES = [0, 25_000, 50_000, np.inf]
BIN_LABELS = ["close (<25km)", "medium (25-50km)", "far (>50km)"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)
    OUT = rc.out_dir(HERE)
    df = pd.read_csv(OUT / "action_vs_osm_distance.csv")
    n_tests = len(SITE_TYPES)

    print(f"n = {len(df)} synagogues; tiers: close <25km, medium 25-50km, far >50km\n")
    print(f"{'site_type':14} {'H':>8} {'p_kw':>11} {'p_bonf':>11}  "
          f"tier counts / mean actions")

    results = []
    for ft in SITE_TYPES:
        col = f"dist_{ft}"
        tier = pd.cut(df[col], bins=BIN_EDGES, labels=BIN_LABELS,
                      include_lowest=True)
        groups, tier_stats = [], []
        for lbl in BIN_LABELS:
            vals = df.loc[tier == lbl, "n_actions"].values
            tier_stats.append({
                "tier": lbl,
                "n": int(len(vals)),
                "mean_actions": round(float(vals.mean()), 2) if len(vals) else None,
            })
            if len(vals) > 0:
                groups.append(vals)

        entry = {"site_type": ft, "tiers": tier_stats, "n_nonempty_tiers": len(groups)}
        if len(groups) >= 2:
            H, p_kw = stats.kruskal(*groups)
            p_kw_bonf = min(1.0, p_kw * n_tests)
            entry.update({
                "kruskal_H": round(float(H), 4),
                "kruskal_p": float(p_kw),
                "kruskal_p_bonferroni": float(p_kw_bonf),
                "kruskal_significant_0.05": bool(p_kw_bonf < 0.05),
            })
            disp = " ".join(f"{t['tier'].split()[0]}:{t['n']}/{t['mean_actions']}"
                            for t in tier_stats)
            print(f"{ft:14} {H:>8.3f} {p_kw:>11.3e} {p_kw_bonf:>11.3e}  {disp}")
        else:
            entry["note"] = "only one non-empty tier; Kruskal-Wallis not applicable"
            print(f"{ft:14} {'--':>8} {'--':>11} {'--':>11}  "
                  "(only one non-empty tier)")
        results.append(entry)

    summary = {
        "test": "Kruskal-Wallis H-test of action count across fixed distance tiers",
        "tiers": {"close": "<25 km", "medium": "25-50 km", "far": ">50 km"},
        "null_hypothesis": "all tier action-count distributions are equal",
        "n_synagogues": int(len(df)),
        "multiple_comparison": f"Bonferroni over {n_tests} tests",
        "note": "Non-parametric (action counts are zero-inflated and right-skewed). "
                "Empty tiers dropped.",
        "results": results,
    }
    (OUT / "action_vs_osm_distance_anova.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {OUT/'action_vs_osm_distance_anova.json'}")


if __name__ == "__main__":
    main()
