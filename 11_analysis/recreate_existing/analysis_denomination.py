"""
Action by denomination (goals.md lines 11-17): fraction environmentally active,
action frequency, and category composition per denomination. Reproduces thesis
Tables 4 / 13 and Figure 3 (with the chunk-count frequency metric).

Denomination effect on relative mix (proportion of a synagogue's own action/framed-
action total, not absolute frequency): Kruskal-Wallis + Dunn's post-hoc (BH-corrected)
for the share of a synagogue's actions that are Spirituality & Worship, and the
share of its framed actions that are Embedded / Explicit -> denomination.json
"proportion_tests".

Outputs:
  denomination.json
  fig_denomination_meanactions.png   distribution of action chunks / env-active synagogue,
                                      by denomination (box-and-whisker — see KNOWN_DATA_ISSUES.md)
  fig_denomination_pct.png           % environmentally active, by denomination (±1 SD)
  fig_denomination_categories.png    category composition (mean chunks/active) by denomination
  fig_denomination_framings.png      same chart, but each denomination's bar is stacked by
                                      the Implicit/Explicit/Embedded framing axis
  fig_denomination_by_party.png      two panels: denominational makeup (% of party's
                                      synagogues per denomination) for synagogues in
                                      Democratic vs Republican congressional districts
  fig_denomination_meanactions_per_synagogue.png,
  fig_denomination_categories_per_synagogue.png,
  fig_denomination_framings_per_synagogue.png
                                      'mean action count' copies of the three charts
                                      above, normalised over ALL synagogues of each
                                      denomination (incl. zero-action) rather than
                                      only the env-active ones
  fig_denomination_categories_per_synagogue_proportion.png
                                      relative-mix version of
                                      fig_denomination_categories_per_synagogue.png:
                                      each denomination's bar normalised to its OWN
                                      total (100% stacked) instead of showing the
                                      absolute mean action count per synagogue
  fig_denomination_categories_per_synagogue_pie.png
                                      same relative-mix data as the proportion
                                      figure above, as one pie chart per
                                      denomination (small multiples) instead of a
                                      single 100%-stacked bar chart
  fig_denomination_framings_per_synagogue_proportion.png
                                      relative-mix version of
                                      fig_denomination_framings_per_synagogue.png:
                                      each denomination's bar normalised to its OWN
                                      total framed-action volume (100% stacked) by
                                      the Implicit/Explicit/Embedded axis, instead
                                      of the absolute mean framed-action count

All outputs, including fig_denomination_framings.png, go to page_chunks/ when
--source page_chunks is given — page_chunk_framing now covers both sources.

    python analysis_denomination.py [--source page_chunks]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import recreate_common as rc

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)          # must precede any outlier-set use
    out_path = rc.out_dir(HERE)

    conn = rc.get_conn()
    actions = rc.load_actions(conn)
    framing = rc.load_framing(conn)
    denom_ids = rc.active_denominator(conn)
    # denomination per synagogue (only active-denominator synagogues)
    syn = pd.read_sql(
        "SELECT id AS synagogue_id, denomination_canonical AS denomination, "
        "congressional_district FROM synagogues", conn)
    lean = rc.district_lean(2024, conn)
    conn.close()

    syn = syn[syn["synagogue_id"].isin(denom_ids)]
    syn["denomination"] = rc.merge_denominations(syn["denomination"])  # Modern Orthodox -> Orthodox
    syn = syn[syn["denomination"].isin(rc.DENOM_ORDER)]   # 9 thesis denominations
    syn["lean"] = syn["congressional_district"].map(lean)

    env_ids = set(actions["synagogue_id"].unique())
    m = rc.category_count_matrix(actions, syn["synagogue_id"])   # all denom synagogues (0 for inactive)
    total = m.sum(axis=1)

    rows = []
    chunks_by_denom = {}
    for d in rc.DENOM_ORDER:
        ids = syn.loc[syn["denomination"] == d, "synagogue_id"]
        n = len(ids)
        if n == 0:
            continue
        act = [i for i in ids if i in env_ids]
        n_act = len(act)
        chunks_active = total.reindex(act).values if n_act else np.array([0.0])
        chunks_by_denom[d] = chunks_active
        rows.append({
            "denomination": d,
            "n_total": n,
            "n_env_active": n_act,
            "pct_env_active": round(100 * n_act / n, 1),
            "pct_sd": round(100 * rc.sd_prop(n_act, n), 1),
            "mean_chunks_all": round(float(total.reindex(ids).mean()), 2),
            "mean_chunks_per_active": round(float(chunks_active.mean()), 2) if n_act else 0.0,
            "sd_per_active": round(rc.sd_mean(chunks_active), 2) if n_act > 1 else 0.0,
        })

    # Kruskal-Wallis on action frequency across denominations (active syns) + Bonferroni
    freq_by_denom = {}
    for d in rc.DENOM_ORDER:
        ids = syn.loc[syn["denomination"] == d, "synagogue_id"]
        act = [i for i in ids if i in env_ids]
        if len(act) > 1:
            freq_by_denom[d] = total.reindex(act).values
    kruskal = rc.kruskal_between(freq_by_denom)
    pw = rc.pairwise_bonferroni(freq_by_denom, paired=False, only_sig=True)

    # category composition (mean chunks/active) by denomination -> thesis Table 13
    comp = {}
    for d in rc.DENOM_ORDER:
        ids = syn.loc[syn["denomination"] == d, "synagogue_id"]
        act = [i for i in ids if i in env_ids]
        if not act:
            continue
        comp[d] = {c: round(float(m.reindex(act)[c].mean()), 3) for c in rc.CATEGORIES}

    # framing composition (mean framed chunks/active) by denomination -- same as
    # `comp` but the stack is the Implicit/Explicit/Embedded framing axis.
    fcomp = {}
    mf = rc.framing_count_matrix(framing, syn["synagogue_id"])
    for d in rc.DENOM_ORDER:
        ids = syn.loc[syn["denomination"] == d, "synagogue_id"]
        act = [i for i in ids if i in env_ids]
        if not act:
            continue
        fcomp[d] = {fr: round(float(mf.reindex(act)[fr].mean()), 3) for fr in rc.FRAMINGS}

    # Denomination effect on RELATIVE MIX (proportion of a synagogue's own action/
    # framing total), as opposed to absolute frequency above: does denomination
    # predict what SHARE of a synagogue's actions are Spirituality & Worship, or
    # what share of its framed actions are Embedded / Explicit? Kruskal-Wallis
    # omnibus + Dunn's test post-hoc (Benjamini-Hochberg-corrected), each restricted
    # to synagogues with >=1 action (or >=1 framed action) of the relevant kind,
    # since the proportion is undefined at a total of zero.
    syn_by_id = syn.set_index("synagogue_id")

    def prop_groups(numerator: pd.Series, denominator: pd.Series, population: set):
        prop = numerator / denominator
        return {d: prop.reindex(syn_by_id.index[(syn_by_id["denomination"] == d) &
                                                 (syn_by_id.index.isin(population))]).dropna().values
                for d in rc.DENOM_ORDER}

    env_ids_framing = set(framing["synagogue_id"].unique())
    frame_total = mf.sum(axis=1)
    tests_to_run = [
        ("spirituality_worship_share_of_actions", "Spirituality & Worship", total, env_ids, m),
        ("embedded_share_of_framed_actions", "Embedded", frame_total, env_ids_framing, mf),
        ("explicit_share_of_framed_actions", "Explicit", frame_total, env_ids_framing, mf),
    ]

    prop_tests = {}
    for label, num_col, denom_series, pop, m_ in tests_to_run:
        groups = prop_groups(m_[num_col], denom_series, pop)
        groups = {d: v for d, v in groups.items() if len(v) > 0}
        kw = rc.kruskal_between(groups)
        dunn = rc.dunn_posthoc(groups, method="fdr_bh")
        prop_tests[label] = {
            "n_denominations_tested": kw["n_groups"],
            "n_synagogues": kw["n_total"],
            "kruskal": kw,
            "group_stats": [{"denomination": d, "n": len(v),
                             "mean": round(float(v.mean()), 4),
                             "median": round(float(np.median(v)), 4)}
                            for d, v in groups.items()],
            "dunn_posthoc_bh": dunn.round(6).to_dict(orient="records"),
        }

    # 'mean action count' variants: same metrics normalised over ALL synagogues of
    # the denomination (incl. those with zero env action), not just active ones.
    chunks_all_by_denom, comp_all, fcomp_all = {}, {}, {}
    for d in rc.DENOM_ORDER:
        ids = syn.loc[syn["denomination"] == d, "synagogue_id"]
        if len(ids) == 0:
            continue
        allvals = total.reindex(ids).values
        chunks_all_by_denom[d] = allvals
        comp_all[d] = {c: round(float(m.reindex(ids)[c].mean()), 3) for c in rc.CATEGORIES}
        fcomp_all[d] = {fr: round(float(mf.reindex(ids)[fr].mean()), 3) for fr in rc.FRAMINGS}

    # Same Kruskal-Wallis as `kruskal` above, but over chunks_all_by_denom (ALL
    # synagogues per denomination, incl. zero-action) instead of chunks_by_denom
    # (env-active only) -- the test for fig_denomination_meanactions_per_synagogue.png.
    # Post-hoc is Dunn's test (Benjamini-Hochberg-corrected), matching the
    # proportion_tests post-hoc below, rather than pairwise Mann-Whitney U with
    # Bonferroni: Dunn's ranks all denominations together once and shares one
    # pooled tie-correction term, instead of re-ranking locally for each pair.
    kruskal_all = rc.kruskal_between(chunks_all_by_denom)
    dunn_all = rc.dunn_posthoc(chunks_all_by_denom, method="fdr_bh", only_sig=True)

    # Denomination prevalence within each party's synagogues: % of that party's
    # (active-denominator) synagogues that fall in each denomination.
    prevalence_by_party, n_by_party = {}, {}
    for p in ("Dem", "Rep"):
        sub = syn[syn["lean"] == p]
        n = len(sub)
        n_by_party[p] = int(n)
        vc = sub["denomination"].value_counts()
        prevalence_by_party[p] = {d: round(100 * int(vc.get(d, 0)) / n, 1) if n else 0.0
                                  for d in rc.DENOM_ORDER}

    out = {
        "source": rc.get_source(),
        "metric": "action chunks per environmentally active synagogue",
        "denominations": rows,
        "kruskal": kruskal,
        "pairwise_significant": pw.to_dict(orient="records"),
        "kruskal_all_synagogues": kruskal_all,
        "dunn_posthoc_bh_all_synagogues": dunn_all.to_dict(orient="records"),
        "category_composition_per_active": comp,
        "framing_composition_per_active": fcomp,
        "denomination_prevalence_by_party": {
            p: {"n_synagogues": n_by_party[p], "pct_by_denomination": prevalence_by_party[p]}
            for p in ("Dem", "Rep")
        },
        "proportion_tests": prop_tests,
    }
    (out_path / "denomination.json").write_text(json.dumps(out, indent=2))
    _plot(rows, chunks_by_denom, comp, kruskal, out_path)
    _plot_framing_composition(fcomp, out_path)
    _plot_by_party(prevalence_by_party, n_by_party, out_path)

    # 'mean action count per synagogue' copies (normalised over all synagogues)
    _plot_denom_meanall(rows, chunks_all_by_denom, comp_all, out_path)
    _plot_framing_composition(fcomp_all, out_path,
                              fname="fig_denomination_framings_per_synagogue.png",
                              ylabel="Mean action count per synagogue")
    _plot_denom_framing_proportions(fcomp_all, out_path)
    _plot_denom_category_proportions(comp_all, out_path)
    n_total_by_denom = {r["denomination"]: r["n_total"] for r in rows}
    _plot_denom_category_pies(comp_all, n_total_by_denom, out_path)
    print(json.dumps({k: out[k] for k in ("metric", "denominations", "kruskal")}, indent=2))
    print(f"\nsignificant pairwise denomination differences: {len(pw)}")
    print(f"significant pairwise denomination differences (all synagogues, Dunn+BH): {len(dunn_all)}")


def _plot(rows, chunks_by_denom, comp, kruskal, out_path):
    plt = rc.apply_style()
    rows = [r for r in rows if r["n_env_active"] > 0]
    order = sorted(rows, key=lambda r: np.median(chunks_by_denom[r["denomination"]]),
                   reverse=True)
    labels = [r["denomination"].replace("Non-Denominational", "N-D") + f" (n={r['n_env_active']})"
              for r in order]

    # distribution of action chunks per active synagogue, horizontal box-and-whisker
    data = [chunks_by_denom[r["denomination"]] for r in order]
    fig, ax = plt.subplots(figsize=(10, 7))
    bp = ax.boxplot(data, positions=range(len(order)), widths=0.6, patch_artist=True,
                     vert=False,
                     showmeans=True, meanprops=dict(marker="D", markerfacecolor="white",
                     markeredgecolor="black", markersize=5),
                     flierprops=dict(marker="o", markersize=3, alpha=0.4),
                     medianprops=dict(color="black"))
    for patch in bp["boxes"]:
        patch.set_facecolor("#4c72b0")
        patch.set_edgecolor("black")
        patch.set_alpha(0.85)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()        # highest-median denomination at top
    ax.set_xlabel("Action count per env-active synagogue")
    fig.tight_layout()
    fig.savefig(out_path / "fig_denomination_meanactions.png")

    # % environmentally active
    pct_order = sorted(rows, key=lambda r: r["pct_env_active"], reverse=True)
    pct_labels = [r["denomination"].replace("Non-Denominational", "N-D")
                  for r in pct_order]
    pcts = [r["pct_env_active"] for r in pct_order]
    psds = [r["pct_sd"] for r in pct_order]
    fig2, ax2 = plt.subplots(figsize=(13, 7))
    ax2.bar(range(len(pct_order)), pcts, yerr=psds, capsize=4, color="#55a868",
            edgecolor="white", width=0.66)
    ax2.set_xticks(range(len(pct_order)))
    ax2.set_xticklabels(pct_labels, rotation=30, ha="right")
    ax2.set_ylabel("% environmentally active")
    for i, (p, sd, r) in enumerate(zip(pcts, psds, pct_order)):
        ax2.text(i, p + sd + 1.5, f"{p:.0f}%\n(n={r['n_total']})", ha="center",
                 va="bottom", fontsize=rc.FS_ANNOTATION)
    ax2.set_ylim(0, 105)
    fig2.tight_layout()
    fig2.savefig(out_path / "fig_denomination_pct.png")

    # stacked category composition (mean chunks/active) by denomination
    denoms = [d for d in rc.DENOM_ORDER if d in comp]
    fig3, ax3 = plt.subplots(figsize=(11, 7.5))
    bottom = np.zeros(len(denoms))
    x = np.arange(len(denoms))
    for cat in rc.CATEGORIES:
        vals = np.array([comp[d][cat] for d in denoms])
        ax3.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
                edgecolor="white", linewidth=0.4)
        bottom += vals
    ax3.set_xticks(x)
    ax3.set_xticklabels([d.replace("Non-Denominational", "N-D") for d in denoms],
                        rotation=30, ha="right")
    ax3.set_ylabel("Mean action count per env-active synagogue")
    ax3.legend(title="Category", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig3.tight_layout()
    fig3.savefig(out_path / "fig_denomination_categories.png")


def _plot_framing_composition(fcomp, out_path, fname="fig_denomination_framings.png",
                              ylabel="Mean action count per env-active synagogue"):
    """Same as fig_denomination_categories, but each denomination's bar is stacked
    by the Implicit/Explicit/Embedded framing axis instead of the 9 categories.
    The population (env-active vs all synagogues) is baked into `fcomp`."""
    plt = rc.apply_style()
    denoms = [d for d in rc.DENOM_ORDER if d in fcomp]
    fig, ax = plt.subplots(figsize=(11, 7.5))
    bottom = np.zeros(len(denoms))
    x = np.arange(len(denoms))
    for fr in rc.FRAMINGS:
        vals = np.array([fcomp[d][fr] for d in denoms])
        ax.bar(x, vals, bottom=bottom, color=rc.FRAMING_COLORS[fr], label=fr,
               edgecolor="white", linewidth=0.4)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([d.replace("Non-Denominational", "N-D") for d in denoms],
                       rotation=30, ha="right")
    ax.set_ylabel(ylabel)
    ax.legend(title="Framing", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path / fname)


def _plot_denom_framing_proportions(fcomp_all, out_path):
    """Relative-mix version of fig_denomination_framings_per_synagogue.png: each
    denomination's bar is normalised to its OWN total framed-action volume (100%
    stacked) by the Implicit/Explicit/Embedded axis, instead of showing the
    absolute mean framed-action count per synagogue. The framing analogue of
    _plot_denom_category_proportions."""
    plt = rc.apply_style()
    denoms = [d for d in rc.DENOM_ORDER if d in fcomp_all]
    totals = {d: sum(fcomp_all[d][fr] for fr in rc.FRAMINGS) for d in denoms}

    fig, ax = plt.subplots(figsize=(11, 7.5))
    bottom = np.zeros(len(denoms))
    x = np.arange(len(denoms))
    for fr in rc.FRAMINGS:
        vals = np.array([100 * fcomp_all[d][fr] / totals[d] if totals[d] else 0.0
                         for d in denoms])
        ax.bar(x, vals, bottom=bottom, color=rc.FRAMING_COLORS[fr], label=fr,
               edgecolor="white", linewidth=0.4)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([d.replace("Non-Denominational", "N-D") for d in denoms],
                       rotation=30, ha="right")
    ax.set_ylabel("% of denomination's total framed-action count")
    ax.set_ylim(0, 100)
    ax.legend(title="Framing", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path / "fig_denomination_framings_per_synagogue_proportion.png",
               bbox_inches="tight")


def _plot_denom_meanall(rows, chunks_all_by_denom, comp_all, out_path):
    """'mean action count per synagogue' copies of fig_denomination_meanactions and
    fig_denomination_categories: the same box-and-whisker and stacked-category
    charts, but normalised over ALL synagogues of each denomination (incl. those
    with zero env action) rather than only the env-active ones."""
    plt = rc.apply_style()
    order = sorted((r for r in rows if r["denomination"] in chunks_all_by_denom),
                   key=lambda r: np.median(chunks_all_by_denom[r["denomination"]]),
                   reverse=True)
    labels = [r["denomination"].replace("Non-Denominational", "N-D") + f" (n={r['n_total']})"
              for r in order]

    # distribution of action chunks per synagogue (all), horizontal box-and-whisker
    data = [chunks_all_by_denom[r["denomination"]] for r in order]
    fig, ax = plt.subplots(figsize=(10, 7))
    bp = ax.boxplot(data, positions=range(len(order)), widths=0.6, patch_artist=True,
                     vert=False,
                     showmeans=True, meanprops=dict(marker="D", markerfacecolor="white",
                     markeredgecolor="black", markersize=5),
                     flierprops=dict(marker="o", markersize=3, alpha=0.4),
                     medianprops=dict(color="black"))
    for patch in bp["boxes"]:
        patch.set_facecolor("#4c72b0")
        patch.set_edgecolor("black")
        patch.set_alpha(0.85)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()        # highest-median denomination at top
    ax.set_xlabel("Action count per synagogue")
    fig.tight_layout()
    fig.savefig(out_path / "fig_denomination_meanactions_per_synagogue.png")

    # stacked category composition (mean chunks/synagogue) by denomination
    denoms = [d for d in rc.DENOM_ORDER if d in comp_all]
    fig3, ax3 = plt.subplots(figsize=(11, 6.5))
    bottom = np.zeros(len(denoms))
    x = np.arange(len(denoms))
    for cat in rc.CATEGORIES:
        vals = np.array([comp_all[d][cat] for d in denoms])
        ax3.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
                edgecolor="white", linewidth=0.4)
        bottom += vals
    ax3.set_xticks(x)
    ax3.set_xticklabels([d.replace("Non-Denominational", "N-D") for d in denoms],
                        rotation=30, ha="right")
    ax3.set_ylabel("Mean action count per synagogue")
    ax3.legend(title="Category", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig3.tight_layout()
    fig3.savefig(out_path / "fig_denomination_categories_per_synagogue.png")


def _plot_denom_category_proportions(comp_all, out_path):
    """Relative-mix version of fig_denomination_categories_per_synagogue.png: each
    denomination's bar is normalised to its OWN total (100% stacked), showing what
    share of that denomination's action volume falls in each category, rather than
    the absolute mean action count per synagogue. Uses the same per-synagogue means
    (over all active-denominator synagogues, incl. non-active) as the absolute
    figure — proportions are scale-invariant, so dividing means or raw totals by
    the denomination's own total gives the same shares."""
    plt = rc.apply_style()
    denoms = [d for d in rc.DENOM_ORDER if d in comp_all]
    totals = {d: sum(comp_all[d][c] for c in rc.CATEGORIES) for d in denoms}

    fig, ax = plt.subplots(figsize=(11, 6.5))
    bottom = np.zeros(len(denoms))
    x = np.arange(len(denoms))
    for cat in rc.CATEGORIES:
        vals = np.array([100 * comp_all[d][cat] / totals[d] if totals[d] else 0.0
                         for d in denoms])
        ax.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
               edgecolor="white", linewidth=0.4)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([d.replace("Non-Denominational", "N-D") for d in denoms],
                       rotation=30, ha="right")
    ax.set_ylabel("% of denomination's total action count")
    ax.set_ylim(0, 100)
    ax.legend(title="Category", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path / "fig_denomination_categories_per_synagogue_proportion.png",
               bbox_inches="tight")


def _plot_denom_category_pies(comp_all, n_total_by_denom, out_path):
    """Pie-chart small multiples of fig_denomination_categories_per_synagogue_proportion:
    one pie per denomination, each wedge the % share of that denomination's own
    total action volume in a category. Same data/normalisation as the stacked-bar
    proportion figure, just one relative-mix breakdown per panel instead of all
    nine side by side. Wedges < 4% are left unlabelled to avoid clutter; a single
    shared legend (fixed category order/color) covers every panel."""
    plt = rc.apply_style()
    denoms = [d for d in rc.DENOM_ORDER if d in comp_all]
    colors = [rc.CATEGORY_COLORS[c] for c in rc.CATEGORIES]

    ncols = 3
    nrows = -(-len(denoms) // ncols)   # ceil
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 4.2 * nrows))
    axes = np.atleast_1d(axes).flatten()

    for ax, d in zip(axes, denoms):
        vals = [comp_all[d][c] for c in rc.CATEGORIES]
        total = sum(vals)
        label = d.replace("Non-Denominational", "N-D")
        n = n_total_by_denom.get(d, 0)
        if total <= 0:
            ax.text(0.5, 0.5, "no action data", ha="center", va="center",
                    fontsize=rc.FS_ANNOTATION, color="gray", transform=ax.transAxes)
            ax.set_title(f"{label} (n={n})")
            ax.axis("off")
            continue
        ax.pie(vals, colors=colors, startangle=90, counterclock=False,
               wedgeprops=dict(edgecolor="white", linewidth=0.7),
               autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
               pctdistance=0.75, textprops=dict(fontsize=rc.FS_ANNOTATION, color="white"))
        ax.set_title(f"{label} (n={n})")
        ax.set_aspect("equal")

    for ax in axes[len(denoms):]:
        ax.axis("off")

    handles = [plt.Line2D([0], [0], marker="s", linestyle="", markersize=9,
                          markerfacecolor=rc.CATEGORY_COLORS[c], markeredgecolor="none")
               for c in rc.CATEGORIES]
    fig.legend(handles, rc.CATEGORIES, title="Category", loc="center left",
              bbox_to_anchor=(1.0, 0.5))
    fig.tight_layout()
    fig.savefig(out_path / "fig_denomination_categories_per_synagogue_pie.png",
               bbox_inches="tight")


def _plot_by_party(prevalence_by_party, n_by_party, out_path):
    """Two-panel denominational makeup: % of each party's (active-denominator)
    synagogues in each denomination, Democratic vs Republican districts."""
    plt = rc.apply_style()
    panels = [("Dem", "Democratic", "#2166ac"), ("Rep", "Republican", "#b2182b")]
    denoms = rc.DENOM_ORDER
    labels = [d.replace("Non-Denominational", "N-D") for d in denoms]
    x = np.arange(len(denoms))
    ymax = max((max(prevalence_by_party[p].values()) if n_by_party[p] else 0.0)
               for p, _, _ in panels)

    fig, axes = plt.subplots(1, 2, figsize=(13, 6), sharey=True)
    for ax, (p, label, color) in zip(axes, panels):
        vals = [prevalence_by_party[p][d] for d in denoms]
        ax.bar(x, vals, color=color, edgecolor="white", width=0.7)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=30, ha="right")
        ax.set_title(label, pad=4, color=color)
        for i, v in enumerate(vals):
            ax.text(i, v + ymax * 0.01, f"{v:.0f}%", ha="center", va="bottom", fontsize=rc.FS_ANNOTATION)
    axes[0].set_ylabel("% of party's synagogues")
    if ymax > 0:
        axes[0].set_ylim(0, ymax * 1.15)
    fig.tight_layout()
    fig.savefig(out_path / "fig_denomination_by_party.png", bbox_inches="tight")


if __name__ == "__main__":
    main()
