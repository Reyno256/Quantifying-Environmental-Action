"""
Render the logistic-regression results (action_logit_osm_distance.json) as a
PNG table: per-feature coefficient, odds ratio per +1 SD, 95% CI, p-value and
significance, plus a footer with the overall model statistics.

Input : action_logit_osm_distance.json
Output: action_logit_table.png

Usage:
    python 11_analysis/action_logit_table.py
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import argparse
import sys

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

rc.apply_font_sizes()     # central AXIS_FONT_SIZE typography

OUT = HERE          # reassigned in main() from --source


def stars(p: float) -> str:
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    if p < 0.10:
        return "."
    return "ns"


def fmt_p(p: float) -> str:
    return f"{p:.3f}" if p >= 1e-3 else f"{p:.1e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)
    OUT = rc.out_dir(HERE)
    data = json.loads((OUT / "action_logit_osm_distance.json").read_text())

    header = ["Predictor", "Coef (β)", "Odds ratio\n(per +1 SD)",
              "95% CI (OR)", "p-value", "Sig."]
    rows, cell_colors = [], []
    for c in data["coefficients"]:
        name = "intercept" if c["feature"] == "const" else c["feature"]
        lo, hi = c["or_95ci"]
        s = stars(c["p_value"])
        rows.append([
            name,
            f"{c['coef']:+.3f}",
            f"{c['odds_ratio_per_sd']:.3f}",
            f"[{lo:.3f}, {hi:.3f}]",
            fmt_p(c["p_value"]),
            s,
        ])
        # tint significant rows (none expected, but future-proof)
        tint = "#d9ead3" if (s not in ("ns", ".") and name != "intercept") else "white"
        cell_colors.append([tint] * len(header))

    fig, ax = plt.subplots(figsize=(9.5, 0.55 * (len(rows) + 2) + 1.6))
    ax.axis("off")

    tbl = ax.table(
        cellText=rows, colLabels=header, cellColours=cell_colors,
        cellLoc="center", loc="center",
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(rc.FS_ANNOTATION)
    tbl.scale(1, 1.6)

    # Header styling
    for j in range(len(header)):
        cell = tbl[0, j]
        cell.set_facecolor("#34495e")
        cell.set_text_props(color="white", fontweight="bold")
    # Left-align predictor column
    for i in range(1, len(rows) + 1):
        tbl[i, 0].set_text_props(ha="left")
        tbl[i, 0].PAD = 0.03


    out = OUT / "action_logit_table.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
