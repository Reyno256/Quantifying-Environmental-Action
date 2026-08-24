"""
Corner plot (lower-triangle scatter matrix) of the 7 OSM-distance variables,
with each synagogue's point coloured by its environmental action count on a
colour-blind-friendly gradient (cividis).

Both the action counts and the distances are heavily right-skewed, so:
  * points are coloured by log(1 + action count);
  * distance axes are plotted as log10(distance_km + 0.01).

Diagonal panels show the (log) distance distribution for each site type.

Input : action_vs_osm_distance.csv
Output: action_distance_cornerplot.png

FONT SIZES ARE INTENTIONALLY EXEMPT from recreate_common.AXIS_FONT_SIZE. This is
a 7x7 grid at figsize=(2.2*k, 2.2*k), so each panel is only ~2.2in wide and its
tick labels overlap at the shared 8pt. The literals below (labelsize=6,
fontsize=8/9) are deliberate; do not route them through the central constants.

Usage:
    python 11_analysis/action_distance_cornerplot.py
"""

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
CMAP = "cividis"  # perceptually uniform & colour-blind friendly


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)
    OUT = rc.out_dir(HERE)
    df = pd.read_csv(OUT / "action_vs_osm_distance.csv")

    # log10(km + 0.01) for each distance; color by log(1 + actions)
    L = pd.DataFrame({
        t: np.log10(df[f"dist_{t}"] / 1000.0 + 0.01) for t in SITE_TYPES
    })
    color = np.log1p(df["n_actions"].values)

    # Plot lower-action points first so higher-action points sit on top.
    order = np.argsort(color)
    Lo = L.iloc[order].reset_index(drop=True)
    co = color[order]

    k = len(SITE_TYPES)
    fig, axes = plt.subplots(k, k, figsize=(2.2 * k, 2.2 * k))
    sc = None
    vmin, vmax = co.min(), co.max()

    for i in range(k):       # row  -> y variable
        for j in range(k):   # col  -> x variable
            ax = axes[i, j]
            if j > i:                       # upper triangle: hide
                ax.set_visible(False)
                continue
            if i == j:                      # diagonal: histogram
                ax.hist(L[SITE_TYPES[i]], bins=40, color="0.6", edgecolor="none")
                ax.set_yticks([])
            else:                           # lower triangle: scatter
                sc = ax.scatter(
                    Lo[SITE_TYPES[j]], Lo[SITE_TYPES[i]],
                    c=co, cmap=CMAP, vmin=vmin, vmax=vmax,
                    s=6, alpha=0.6, linewidths=0,
                )
            # axis labels only on the outer edge
            if i == k - 1:
                ax.set_xlabel(SITE_TYPES[j], fontsize=8)
            else:
                ax.set_xticklabels([])
            if j == 0 and i != 0:
                ax.set_ylabel(SITE_TYPES[i], fontsize=8)
            elif j == 0 and i == 0:
                ax.set_ylabel(SITE_TYPES[i], fontsize=8)
            else:
                ax.set_yticklabels([])
            ax.tick_params(labelsize=6)


    # Shared colorbar with action-count tick labels (mapped from log1p).
    cbar = fig.colorbar(sc, ax=axes, fraction=0.025, pad=0.02)
    ticks = [0, 1, 5, 20, 100, 500, 1500]
    cbar.set_ticks(np.log1p(ticks))
    cbar.set_ticklabels([str(t) for t in ticks])
    cbar.set_label("environmental action count", fontsize=9)

    out = OUT / "action_distance_cornerplot.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
