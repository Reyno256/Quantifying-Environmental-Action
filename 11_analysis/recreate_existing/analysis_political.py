"""
Action by political lean (goals.md lines 19-24). Like the thesis (and per user
direction) the metric is the *action count* (chunk frequency) per env-active
synagogue.

  1. Four-state comparison: Dem-leaning CA, NY  vs  Rep-leaning TX, FL
     (thesis Fig 4a) + the CA-vs-FL category breakdown (thesis Fig 4c).
  2. Finer breakdown by congressional-district political lean (general-election
     winner), reusing the winner logic from action_vs_dem_district.py.

Outputs:
  political.json
  fig_state_meanactions.png     distribution of action chunks/active synagogue, 4 states by
                                lean (box-and-whisker — see KNOWN_DATA_ISSUES.md)
  fig_state_categories.png      CA vs FL category composition (mean chunks/active, ±1 SD)
  fig_district_lean_meanactions.png   distribution of action chunks/active synagogue by
                                district lean (box-and-whisker)
  fig_state_meanactions_per_synagogue.png,
  fig_state_categories_per_synagogue.png,
  fig_district_lean_meanactions_per_synagogue.png
                                'mean action count' copies of the three figures
                                above, with the mean/distribution taken over ALL
                                synagogues of each group (incl. zero-action)

    .venv/bin/python analysis_political.py [--year 2024]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import recreate_common as rc

HERE = Path(__file__).resolve().parent

DEM_STATES = ["CA", "NY"]
REP_STATES = ["TX", "FL"]
FOUR = DEM_STATES + REP_STATES
LEAN_OF = {**{s: "Dem" for s in DEM_STATES}, **{s: "Rep" for s in REP_STATES}}
STATE_COLOR = {"CA": "#2166ac", "NY": "#4393c3", "TX": "#d6604d", "FL": "#b2182b"}


def sig_stars(p):
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "n.s."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)          # must precede any outlier-set use
    out = rc.out_dir(HERE)

    conn = rc.get_conn()
    actions = rc.load_actions(conn)
    denom = rc.active_denominator(conn)
    syn = pd.read_sql(
        "SELECT id AS synagogue_id, state, congressional_district FROM synagogues", conn)
    lean = rc.district_lean(args.year, conn)
    conn.close()

    syn = syn[syn["synagogue_id"].isin(denom)]
    env_ids = set(actions["synagogue_id"].unique())
    m = rc.category_count_matrix(actions, syn["synagogue_id"])
    total = m.sum(axis=1)

    # ── 1. four-state comparison (action chunks per env-active synagogue) ──
    state_freq = {}
    state_rows = []
    for s in FOUR:
        ids = syn.loc[syn["state"] == s, "synagogue_id"]
        act = [i for i in ids if i in env_ids]
        vals = total.reindex(act).values if act else np.array([0.0])
        state_freq[s] = vals
        state_rows.append({"state": s, "lean": LEAN_OF[s], "n_total": int(len(ids)),
                           "n_env_active": len(act),
                           "mean_chunks_per_active": round(float(vals.mean()), 2),
                           "sd": round(rc.sd_mean(vals), 2)})
    state_kruskal = rc.kruskal_between(state_freq)
    state_pw = rc.pairwise_bonferroni(state_freq, paired=False, only_sig=False)

    # ── CA vs FL category breakdown (mean chunks/active) ──
    def cat_means(state):
        ids = syn.loc[syn["state"] == state, "synagogue_id"]
        act = [i for i in ids if i in env_ids]
        sub = m.reindex(act)
        return {c: (sub[c].values if len(act) else np.array([0.0])) for c in rc.CATEGORIES}
    ca, fl = cat_means("CA"), cat_means("FL")
    cat_rows = []
    for c in rc.CATEGORIES:
        u, p = stats.mannwhitneyu(ca[c], fl[c], alternative="two-sided")
        cat_rows.append({"category": c,
                         "CA_mean": round(float(ca[c].mean()), 3), "CA_sd": round(rc.sd_mean(ca[c]), 3),
                         "FL_mean": round(float(fl[c].mean()), 3), "FL_sd": round(rc.sd_mean(fl[c]), 3),
                         "p_raw": float(p)})
    for r in cat_rows:
        r["p_bonf"] = min(r["p_raw"] * len(cat_rows), 1.0)

    # ── 2. congressional-district lean breakdown ──
    syn["lean"] = syn["congressional_district"].map(lean)
    dl = syn.dropna(subset=["lean"])
    lean_freq = {}
    lean_rows = []
    for L in ["Dem", "Rep"]:
        ids = dl.loc[dl["lean"] == L, "synagogue_id"]
        act = [i for i in ids if i in env_ids]
        vals = total.reindex(act).values if act else np.array([0.0])
        lean_freq[L] = vals
        lean_rows.append({"district_lean": L, "n_total": int(len(ids)),
                          "n_env_active": len(act),
                          "mean_chunks_per_active": round(float(vals.mean()), 2),
                          "sd": round(rc.sd_mean(vals), 2)})
    u_dl, pu_dl = stats.mannwhitneyu(lean_freq["Dem"], lean_freq["Rep"], alternative="two-sided")

    # ── 'mean action count' variants: same comparisons, but the mean/distribution
    # is taken over ALL synagogues of each group (incl. those with zero env
    # action), not just the env-active ones. Feeds the _per_synagogue figures. ──
    state_freq_all = {s: (total.reindex(syn.loc[syn["state"] == s, "synagogue_id"]).values
                          if (syn["state"] == s).any() else np.array([0.0])) for s in FOUR}
    state_rows_all = [{"state": s, "lean": LEAN_OF[s],
                       "n_total": int(len(state_freq_all[s])),
                       "mean_chunks_per_synagogue": round(float(state_freq_all[s].mean()), 2),
                       "sd": round(rc.sd_mean(state_freq_all[s]), 2)} for s in FOUR]
    # Same Kruskal-Wallis + Bonferroni pairwise Mann-Whitney as `state_kruskal`/
    # `state_pw` above, but over ALL synagogues per state instead of env-active
    # only -- the test for fig_state_meanactions_per_synagogue.png, which
    # previously had none.
    state_kruskal_all = rc.kruskal_between(state_freq_all)
    state_pw_all = rc.pairwise_bonferroni(state_freq_all, paired=False, only_sig=False)

    def cat_all(state):
        ids = syn.loc[syn["state"] == state, "synagogue_id"]
        sub = m.reindex(ids)
        return {c: (sub[c].values if len(ids) else np.array([0.0])) for c in rc.CATEGORIES}
    ca_all, fl_all = cat_all("CA"), cat_all("FL")
    cat_rows_all = []
    for c in rc.CATEGORIES:
        u, p = stats.mannwhitneyu(ca_all[c], fl_all[c], alternative="two-sided")
        cat_rows_all.append({"category": c,
                             "CA_mean": round(float(ca_all[c].mean()), 3), "CA_sd": round(rc.sd_mean(ca_all[c]), 3),
                             "FL_mean": round(float(fl_all[c].mean()), 3), "FL_sd": round(rc.sd_mean(fl_all[c]), 3),
                             "p_raw": float(p)})
    for r in cat_rows_all:
        r["p_bonf"] = min(r["p_raw"] * len(cat_rows_all), 1.0)

    lean_freq_all = {L: (total.reindex(dl.loc[dl["lean"] == L, "synagogue_id"]).values
                         if (dl["lean"] == L).any() else np.array([0.0])) for L in ["Dem", "Rep"]}
    u_dl_all, pu_dl_all = stats.mannwhitneyu(lean_freq_all["Dem"], lean_freq_all["Rep"],
                                             alternative="two-sided")
    lean_rows_all = [{"district_lean": L, "n_total": int(len(lean_freq_all[L])),
                      "mean_chunks_per_synagogue": round(float(lean_freq_all[L].mean()), 2),
                      "sd": round(rc.sd_mean(lean_freq_all[L]), 2)} for L in ["Dem", "Rep"]]

    result = {
        "source": rc.get_source(),
        "election_year": args.year,
        "metric": "action chunks per environmentally active synagogue",
        "four_state": {"states": state_rows, "kruskal": state_kruskal,
                       "pairwise": state_pw.to_dict(orient="records")},
        "ca_vs_fl_categories": cat_rows,
        "four_state_all_synagogues": {"states": state_rows_all, "kruskal": state_kruskal_all,
                                      "pairwise": state_pw_all.to_dict(orient="records")},
        "ca_vs_fl_categories_all_synagogues": cat_rows_all,
        "district_lean": {"groups": lean_rows,
                          "mann_whitney": {"U": float(u_dl), "p": float(pu_dl)}},
        "district_lean_all_synagogues": {
            "metric": "action chunks per synagogue (incl. zero-action)",
            "groups": lean_rows_all,
            "mann_whitney": {"U": float(u_dl_all), "p": float(pu_dl_all)}},
    }
    (out / "political.json").write_text(json.dumps(result, indent=2))
    _plot_state_box(state_rows, state_freq, out, "fig_state_meanactions.png",
                    "Action count per env-active synagogue", "n_env_active")
    _plot_ca_fl_categories(cat_rows, out, "fig_state_categories.png",
                           "Mean action count per env-active synagogue")
    _plot_lean_box(lean_rows, lean_freq, out,
                   "fig_district_lean_meanactions.png",
                   "Action count per env-active synagogue", "n_env_active")

    # 'mean action count' copies (mean/distribution over all synagogues, incl. zeros)
    _plot_state_box(state_rows, state_freq_all, out,
                    "fig_state_meanactions_per_synagogue.png",
                    "Action count per synagogue", "n_total")
    _plot_ca_fl_categories(cat_rows_all, out, "fig_state_categories_per_synagogue.png",
                           "Mean action count per synagogue")
    _plot_lean_box(lean_rows, lean_freq_all, out,
                   "fig_district_lean_meanactions_per_synagogue.png",
                   "Action count per synagogue", "n_total")
    print(json.dumps(result, indent=2))


def _plot_state_box(state_rows, state_freq, out, fname, ylabel, n_key):
    """Four-state action-count distribution box plot, colored by lean. The
    population (env-active vs all synagogues) is baked into `state_freq`; `n_key`
    picks which per-state count ('n_env_active' or 'n_total') labels the axis."""
    plt = rc.apply_style()
    fig, ax = plt.subplots(figsize=(7, 5.5))
    xs = range(len(state_rows))
    data = [state_freq[r["state"]] for r in state_rows]
    bp = ax.boxplot(data, positions=list(xs), widths=0.55, patch_artist=True,
                     showmeans=True, meanprops=dict(marker="D", markerfacecolor="white",
                     markeredgecolor="black", markersize=5),
                     flierprops=dict(marker="o", markersize=3, alpha=0.4),
                     medianprops=dict(color="black"))
    for patch, r in zip(bp["boxes"], state_rows):
        patch.set_facecolor(STATE_COLOR[r["state"]])
        patch.set_edgecolor("black")
        patch.set_alpha(0.85)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{r['state']}\n({r['lean']}, n={r[n_key]})" for r in state_rows])
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(out / fname)


def _plot_ca_fl_categories(cat_rows, out, fname, ylabel):
    """CA vs FL category composition (grouped bars, ±1 SD). Population baked into
    the CA/FL means/SDs of `cat_rows`."""
    plt = rc.apply_style()
    fig2, ax2 = plt.subplots(figsize=(13, 8))
    x = np.arange(len(cat_rows))
    w = 0.4
    ax2.bar(x - w/2, [r["CA_mean"] for r in cat_rows], w, yerr=[r["CA_sd"] for r in cat_rows],
            capsize=3, color="#2166ac", label="CA (Dem)", edgecolor="white")
    ax2.bar(x + w/2, [r["FL_mean"] for r in cat_rows], w, yerr=[r["FL_sd"] for r in cat_rows],
            capsize=3, color="#b2182b", label="FL (Rep)", edgecolor="white")
    ax2.set_xticks(x)
    ax2.set_xticklabels([r["category"].replace("Operations", "Ops")
                         for r in cat_rows], rotation=30, ha="right")
    ax2.set_ylabel(ylabel)
    for i, r in enumerate(cat_rows):
        if r["p_bonf"] < 0.05:
            y = max(r["CA_mean"] + r["CA_sd"], r["FL_mean"] + r["FL_sd"])
            ax2.text(i, y + 0.05, sig_stars(r["p_bonf"]), ha="center", fontsize=rc.FS_ANNOTATION, fontweight="bold")
    ax2.legend()
    fig2.tight_layout()
    fig2.savefig(out / fname)


def _plot_lean_box(lean_rows, lean_freq, out, fname, ylabel, n_key):
    """District-lean action-count distribution box plot (Dem vs Rep). Population
    baked into `lean_freq`; `n_key` picks the per-group count for the axis."""
    plt = rc.apply_style()
    fig3, ax3 = plt.subplots(figsize=(6, 5.5))
    data = [lean_freq[r["district_lean"]] for r in lean_rows]
    bp3 = ax3.boxplot(data, positions=[0, 1], widths=0.5, patch_artist=True,
                       showmeans=True, meanprops=dict(marker="D", markerfacecolor="white",
                       markeredgecolor="black", markersize=5),
                       flierprops=dict(marker="o", markersize=3, alpha=0.4),
                       medianprops=dict(color="black"))
    for patch, color in zip(bp3["boxes"], ["#2166ac", "#b2182b"]):
        patch.set_facecolor(color)
        patch.set_edgecolor("black")
        patch.set_alpha(0.85)
    ax3.set_xticks([0, 1])
    ax3.set_xticklabels([f"{r['district_lean']}\n(n={r[n_key]})" for r in lean_rows])
    ax3.set_ylabel(ylabel)
    fig3.tight_layout()
    fig3.savefig(out / fname)


if __name__ == "__main__":
    main()
