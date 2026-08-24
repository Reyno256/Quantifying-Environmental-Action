"""
For each predictor in the logistic regression (P(>=1 environmental action) ~
distance to 7 OSM site types), plot the distribution of distance split by the
binary outcome: synagogues with >=1 action vs zero.

This visualizes what the logistic regression sees: how (little) the distance
distributions differ between the two outcome classes.  Distances are shown on a
log10(km) axis; the per-panel logistic odds ratio (per +1 SD) and p-value are
annotated for reference.

Input : action_vs_osm_distance.csv, action_logit_osm_distance.json
Output: action_logit_distance_distributions.png

FONT SIZES ARE INTENTIONALLY EXEMPT from recreate_common.AXIS_FONT_SIZE. The
grid is figsize=(4.2*ncol, 3.0*nrow), so panels shrink as predictors are added
and the labels clip at the shared 8pt. The literals below (labelsize=7,
fontsize=8/9) are deliberate; do not route them through the central constants.

Usage:
    python 11_analysis/action_logit_distance_distributions.py
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

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

# Colour-blind-safe pair (Wong palette): blue = zero, orange = >=1 action.
C_ZERO, C_ACT = "#0072B2", "#E69F00"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)
    OUT = rc.out_dir(HERE)
    df = pd.read_csv(OUT / "action_vs_osm_distance.csv")
    has = df["n_actions"] > 0

    or_p = {}
    jpath = OUT / "action_logit_osm_distance.json"
    if jpath.exists():
        for c in json.loads(jpath.read_text())["coefficients"]:
            or_p[c["feature"]] = (c["odds_ratio_per_sd"], c["p_value"])

    n = len(SITE_TYPES)
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.0 * nrow))
    axes = axes.ravel()

    for ax, t in zip(axes, SITE_TYPES):
        x = df[f"dist_{t}"] / 1000.0   # raw distance in km (no log)
        lo = x.min()
        hi = np.percentile(x, 99)      # cap x-axis at 99th percentile
        bins = np.linspace(lo, hi, 40)
        ax.set_xlim(lo, hi)
        ax.hist(x[~has], bins=bins, density=True, histtype="stepfilled",
                color=C_ZERO, alpha=0.45, label="zero actions")
        ax.hist(x[has], bins=bins, density=True, histtype="step",
                color=C_ACT, linewidth=1.8, label="≥1 action")
        # medians
        ax.axvline(x[~has].median(), color=C_ZERO, ls="--", lw=1)
        ax.axvline(x[has].median(), color=C_ACT, ls="--", lw=1)

        ax.set_title(t, fontsize=9)
        ax.set_yticks([])
        ax.tick_params(labelsize=7)
        ax.set_xlabel("distance (km)", fontsize=8)

    for ax in axes[n:]:
        ax.set_visible(False)

    # Shared legend in the empty slot (or top-right).
    handles, labels = axes[0].get_legend_handles_labels()
    legend_ax = axes[n] if n < len(axes) else axes[0]
    legend_ax.set_visible(True)
    legend_ax.axis("off")
    legend_ax.legend(handles, labels, loc="center", fontsize=11,
                     title="outcome", frameon=False)

    fig.tight_layout()
    out = OUT / "action_logit_distance_distributions.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
