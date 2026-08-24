"""
Categories of action (goals.md lines 5, 9): frequency of environmental action by
category, and the fraction of synagogues taking each. Reproduces thesis Table 8 /
Figure 2, but with the chunk-count (frequency) metric on the y-axis.

Outputs:
  categories.json
  fig_categories.png        distribution of action chunks / env-active synagogue, by
                            category (box-and-whisker — the per-category counts are
                            zero-inflated and heavily right-skewed, so mean ± SD is
                            misleading; see KNOWN_DATA_ISSUES.md)
  fig_categories_pct.png    % of env-active synagogues with >=1 action, by category
  fig_categories_per_synagogue.png
                            same distribution as fig_categories.png, but a 'mean
                            action count' copy taken over ALL active-denominator
                            synagogues (incl. zero-action), not just env-active ones

Outputs go to page_chunks/ when --source page_chunks is given.

    python analysis_categories.py [--source page_chunks]
"""

import argparse
import json
from pathlib import Path

import matplotlib.ticker as mticker
import numpy as np
import recreate_common as rc

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)          # must precede any outlier-set use
    out = rc.out_dir(HERE)

    conn = rc.get_conn()
    actions = rc.load_actions(conn)
    denom = rc.active_denominator(conn)
    conn.close()

    env_ids = set(actions["synagogue_id"].unique()) & denom
    n_env = len(env_ids)
    m = rc.category_count_matrix(actions, env_ids)        # rows = env-active syn, cols = 9 cats

    # frequency (mean chunks/synagogue) + presence (% with >=1) per category, ordered by freq
    rows = []
    for cat in rc.CATEGORIES:
        col = m[cat].values
        n_with = int((col > 0).sum())
        rows.append({
            "category": cat,
            "mean_chunks_per_active": round(float(col.mean()), 3),
            "sd": round(rc.sd_mean(col), 3),
            "n_synagogues_with_action": n_with,
            "pct_active_with_action": round(100 * n_with / n_env, 1),
            "pct_sd": round(100 * rc.sd_prop(n_with, n_env), 1),
            "total_chunks": int(col.sum()),
        })
    rows.sort(key=lambda r: r["mean_chunks_per_active"], reverse=True)

    # Friedman test across categories + Bonferroni Wilcoxon pairwise (thesis Tables 9-10)
    friedman = rc.friedman_within(m)
    vecs = {cat: m[cat].values for cat in rc.CATEGORIES}
    pw = rc.pairwise_bonferroni(vecs, paired=True, only_sig=False)

    # 'mean action count' variant: distribution over ALL active-denominator
    # synagogues (incl. those with zero env action), not just env-active ones.
    m_all = rc.category_count_matrix(actions, denom)
    vecs_all = {cat: m_all[cat].values for cat in rc.CATEGORIES}

    # Same Friedman + Bonferroni Wilcoxon pairwise as `friedman`/`pw` above, but
    # over ALL active-denominator synagogues instead of env-active only -- the
    # test for fig_categories_per_synagogue.png, which previously had none.
    friedman_all = rc.friedman_within(m_all)
    pw_all = rc.pairwise_bonferroni(vecs_all, paired=True, only_sig=False)

    result = {
        "source": rc.get_source(),
        "n_environmentally_active": n_env,
        "metric": "action chunks per environmentally active synagogue",
        "categories": rows,
        "friedman": friedman,
        "n_pairwise_significant": int(pw["sig"].sum()),
        "n_pairwise_total": int(len(pw)),
        "friedman_all_synagogues": friedman_all,
        "n_pairwise_significant_all_synagogues": int(pw_all["sig"].sum()),
        "n_pairwise_total_all_synagogues": int(len(pw_all)),
    }
    (out / "categories.json").write_text(json.dumps(result, indent=2))
    pw.to_csv(out / "categories_pairwise.csv", index=False)
    pw_all.to_csv(out / "categories_pairwise_all_synagogues.csv", index=False)

    _plot(rows, vecs, out)
    _plot_freq_box(rows, vecs_all, out,
                   "fig_categories_per_synagogue.png",
                   "Action count per synagogue", label_means=True, linthresh=6)
    print(json.dumps(result, indent=2))


def _plot_freq_box(rows, vecs, out, fname, ylabel, label_means=False, linthresh=20):
    """Box-and-whisker of the per-synagogue action-count distribution, one box per
    category (category order fixed by `rows`). The population — env-active only vs
    all active-denominator synagogues — is baked into `vecs` by the caller; this
    only varies the y-axis label and filename. If `label_means`, each x-tick label
    gets a "(mean=X.X)" suffix computed directly from `vecs`, so it always matches
    whichever population this particular call is plotting. `linthresh` is the
    symlog linear/log boundary (see below) -- the all-synagogues population has
    much smaller typical values than env-active-only, so it uses a tighter
    threshold for better resolution near zero."""
    plt = rc.apply_style()
    def _label(r):
        name = (r["category"].replace("Environmental & Climate Justice", "Env. & Climate Justice")
                              .replace("Operations & Maintenance", "Ops & Maintenance"))
        if label_means:
            name = f"{name} (mean={vecs[r['category']].mean():.1f})"
        return name
    short = [_label(r) for r in rows]
    colors = [rc.CATEGORY_COLORS[r["category"]] for r in rows]
    data = [vecs[r["category"]] for r in rows]
    fig, ax = plt.subplots(figsize=(13, 7))
    bp = ax.boxplot(data, positions=range(len(rows)), widths=0.55, patch_artist=True,
                     showmeans=True, meanprops=dict(marker="D", markerfacecolor="white",
                     markeredgecolor="black", markersize=5),
                     flierprops=dict(marker="o", markersize=3, alpha=0.4),
                     medianprops=dict(color="black"))
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_edgecolor("black")
        patch.set_alpha(0.85)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(short, rotation=30, ha="right")
    ax.set_ylabel(ylabel)

    # Symlog y-scale: linear near zero (so the many 0 medians/whiskers still
    # plot), log beyond linthresh, so the long right-skewed tail of outliers
    # doesn't crush the typical (mostly 0-20) range into a sliver at the bottom.
    ymax = max(v.max() for v in data)
    ax.set_yscale("symlog", linthresh=linthresh)
    ax.set_ylim(0, ymax * 1.05)
    yticks = [0, 5, 10, 20, 40, 70, 110]
    # Only add the actual max as its own labeled tick if it's far enough past
    # the last fixed tick to avoid overlapping it (e.g. ymax=112 vs 110).
    if ymax > yticks[-1] * 1.15:
        yticks.append(round(ymax))
    ax.set_yticks(yticks)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: f"{y:g}"))
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())
    fig.tight_layout()
    fig.savefig(out / fname)


def _plot(rows, vecs, out):
    plt = rc.apply_style()
    short = [r["category"].replace("Environmental & Climate Justice", "Env. & Climate Justice")
                          .replace("Operations & Maintenance", "Ops & Maintenance")
             for r in rows]
    colors = [rc.CATEGORY_COLORS[r["category"]] for r in rows]

    # Figure A: distribution of action chunks / synagogue (frequency), box-and-whisker
    _plot_freq_box(rows, vecs, out, "fig_categories.png",
                   "Action count per env-active synagogue", label_means=True)

    # Figure B: % of env-active synagogues with >=1 action in category (presence)
    pcts = [r["pct_active_with_action"] for r in rows]
    fig2, ax2 = plt.subplots(figsize=(13, 7))
    ax2.bar(range(len(rows)), pcts, color=colors,
            edgecolor="white", linewidth=0.5, width=0.62)
    ax2.set_xticks(range(len(rows)))
    ax2.set_xticklabels(short, rotation=30, ha="right")
    ax2.set_ylabel("% of env-active synagogues with ≥ 1 action")
    for i, p in enumerate(pcts):
        ax2.text(i, p + 1.0, f"{p:.0f}%", ha="center", va="bottom", fontsize=rc.FS_ANNOTATION)
    ax2.set_ylim(0, 105)
    fig2.tight_layout()
    fig2.savefig(out / "fig_categories_pct.png")


if __name__ == "__main__":
    main()
