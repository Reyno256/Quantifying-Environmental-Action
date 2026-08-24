"""
Framing of environmental action (thesis Fig 1 / Table 5): the Implicit /
Explicit / Embedded axis. This axis was previously dropped because the DB had no
framing labels; it now exists in `llm_chunk_framing`, so we recreate Shore's
Figure 1.

Two views:
  fig_framing.png            % of env-active synagogues with >=1 action of each
                              framing (thesis Fig 1), with ±1 SD
  fig_framing_categories.png mean action chunks per env-active synagogue, by
                              framing, stacked by the 9 action categories — each
                              framing's bar shows its category composition
                              rather than a single solid total.
  fig_framing_categories_by_party.png
                              the same category-composition-by-framing chart,
                              split into two panels: synagogues in Democratic vs
                              Republican congressional districts (2024 winner),
                              each normalised by its own party's env-active count.
  fig_denomination_categories_by_party.png
                              category composition of action by DENOMINATION,
                              same two-panel Dem-vs-Rep stacked-bar style. Each
                              bar is the mean action count per env-active
                              synagogue of that denomination within that party
                              (normalised by that party+denomination's own
                              env-active count, as in fig_denomination_categories).

Note: framings are NOT mutually exclusive (a synagogue can take actions of more
than one framing), so the percentages in fig_framing.png sum to > 100%.

Inferential layer (thesis Table 5): Friedman test across the three framings +
Bonferroni Wilcoxon pairwise (repeated measures -- framings are not mutually
exclusive, so the same synagogue contributes to all three).

Party effect within each framing (fig_framing_categories_by_party_per_synagogue.png):
3 independent Mann-Whitney U tests, one per framing (Dem vs Rep), Bonferroni
across the 3. NOT pooled into one Kruskal-Wallis/Dunn's omnibus across all
6 framing x party groups -- only the cross-party comparison within a single
framing is between independent synagogues; pooling across framings would
reuse the same synagogues in multiple groups.

Outputs: framing.json, fig_framing.png, fig_framing_categories.png,
         fig_framing_categories_by_party.png,
         fig_denomination_categories_by_party.png
         + '_per_synagogue' copies of the three category-composition figures
         above, normalised over ALL active-denominator synagogues (incl. those
         with zero env action) so the y-axis is a plain 'mean action count':
         fig_framing_categories_per_synagogue.png,
         fig_framing_categories_by_party_per_synagogue.png,
         fig_denomination_categories_by_party_per_synagogue.png

Available for both --source keyword_chunks (default) and --source
page_chunks — page_chunk_framing covers every confirmed-action
page_chunk_classifications row via the same judge used for keyword_chunks.

    python analysis_framing.py [--source page_chunks]
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
import recreate_common as rc

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)          # must precede any outlier-set use
    out_path = rc.out_dir(HERE)

    conn = rc.get_conn()
    framing = rc.load_framing(conn)
    actions = rc.load_actions(conn)
    denom = rc.active_denominator(conn)
    syn_cd = pd.read_sql(
        "SELECT id AS synagogue_id, denomination_canonical AS denomination, "
        "congressional_district FROM synagogues", conn)
    lean = rc.district_lean(2024, conn)
    conn.close()
    lean_by_syn = dict(zip(syn_cd["synagogue_id"],
                           syn_cd["congressional_district"].map(lean)))

    env_ids = set(framing["synagogue_id"].unique()) & denom
    n_env = len(env_ids)
    m = rc.framing_count_matrix(framing, env_ids)   # rows = env-active syn, cols = 3 framings

    rows = []
    for fr in rc.FRAMINGS:
        col = m[fr].values
        n_with = int((col > 0).sum())
        rows.append({
            "framing": fr,
            "n_synagogues_with_framing": n_with,
            "pct_active_with_framing": round(100 * n_with / n_env, 1),
            "pct_sd": round(100 * rc.sd_prop(n_with, n_env), 1),
            "mean_chunks_per_active": round(float(col.mean()), 3),
            "sd": round(rc.sd_mean(col), 3),
            "total_chunks": int(col.sum()),
        })

    # Friedman test across framings + Bonferroni Wilcoxon pairwise (Table 5)
    friedman = rc.friedman_within(m)
    vecs = {fr: m[fr].values for fr in rc.FRAMINGS}
    pw = rc.pairwise_bonferroni(vecs, paired=True, only_sig=False)

    # Same test, but over ALL active-denominator synagogues instead of
    # env-active only -- the test for fig_framing_categories_per_synagogue.png,
    # which previously had none.
    m_all = rc.framing_count_matrix(framing, denom)
    friedman_all = rc.friedman_within(m_all)
    vecs_all_fr = {fr: m_all[fr].values for fr in rc.FRAMINGS}
    pw_all_fr = rc.pairwise_bonferroni(vecs_all_fr, paired=True, only_sig=False)

    # Party effect within each framing (ALL active-denominator synagogues) --
    # Mann-Whitney U, one test per framing, NOT pooled into a single
    # Kruskal-Wallis/Dunn's omnibus: framings are not mutually exclusive (a
    # synagogue can carry all three), so only the cross-party comparison
    # *within* a single framing is between independent groups (a synagogue is
    # Dem or Rep, never both) -- pooling across framings would reuse the same
    # synagogues in multiple groups and violate independence. Bonferroni
    # across the 3 framing tests. This is the test for
    # fig_framing_categories_by_party_per_synagogue.png.
    party_ids_all = {p: [i for i in denom if lean_by_syn.get(i) == p]
                     for p in ("Dem", "Rep")}
    party_effect_by_framing = []
    for fr in rc.FRAMINGS:
        dem_vals = m_all[fr].reindex(party_ids_all["Dem"]).values
        rep_vals = m_all[fr].reindex(party_ids_all["Rep"]).values
        stat, p_raw = stats.mannwhitneyu(dem_vals, rep_vals, alternative="two-sided")
        p_bonf = min(p_raw * len(rc.FRAMINGS), 1.0)
        party_effect_by_framing.append({
            "framing": fr,
            "n_dem": int(len(dem_vals)), "n_rep": int(len(rep_vals)),
            "mean_dem": round(float(dem_vals.mean()), 3),
            "mean_rep": round(float(rep_vals.mean()), 3),
            "U": float(stat), "p_raw": float(p_raw), "p_bonf": float(p_bonf),
            "sig": bool(p_bonf < 0.05),
        })

    # framing x category composition: mean chunks/active-synagogue per cell,
    # restricted to the env-active population (n_env) used throughout this folder.
    sub = framing[framing["synagogue_id"].isin(env_ids)]
    counts = (sub.groupby(["framing", "category"]).size()
                 .unstack(fill_value=0)
                 .reindex(index=rc.FRAMINGS, columns=rc.CATEGORIES, fill_value=0))
    composition = (counts / n_env)   # mean chunks per env-active synagogue, per cell

    # Same composition, split by the synagogue's congressional-district lean.
    # Each panel is normalised by its own party's env-active count, so the two
    # are directly comparable as mean action chunks per env-active synagogue.
    def composition_for(ids, denom_count=None):
        s = framing[framing["synagogue_id"].isin(ids)]
        c = (s.groupby(["framing", "category"]).size()
              .unstack(fill_value=0)
              .reindex(index=rc.FRAMINGS, columns=rc.CATEGORIES, fill_value=0))
        d = denom_count if denom_count is not None else len(ids)
        return (c / d) if d else c

    party_ids = {p: {i for i in env_ids if lean_by_syn.get(i) == p}
                 for p in ("Dem", "Rep")}
    comp_by_party = {p: composition_for(ids) for p, ids in party_ids.items()}
    n_by_party = {p: len(ids) for p, ids in party_ids.items()}

    # denomination x category composition, split by party (same two-panel style,
    # but denomination on the x-axis instead of framing). Each bar is the mean
    # action count per env-active synagogue of that denomination within that
    # party — i.e. normalised by that party+denomination's own env-active count,
    # matching fig_denomination_categories. Restricted to the active-denominator
    # population and the 9 thesis denominations (Modern Orthodox -> Orthodox).
    dsyn = syn_cd[syn_cd["synagogue_id"].isin(denom)].copy()
    dsyn["denomination"] = rc.merge_denominations(dsyn["denomination"])
    dsyn = dsyn[dsyn["denomination"].isin(rc.DENOM_ORDER)]
    dsyn["lean"] = dsyn["congressional_district"].map(lean)
    act_env_ids = set(actions["synagogue_id"].unique())
    cat_m = rc.category_count_matrix(actions, dsyn["synagogue_id"])

    denom_comp_by_party, denom_n_by_party = {}, {}
    for p in ("Dem", "Rep"):
        sub = dsyn[dsyn["lean"] == p]
        comp_d, n_d = {}, {}
        for d in rc.DENOM_ORDER:
            ids = sub.loc[sub["denomination"] == d, "synagogue_id"]
            act = [i for i in ids if i in act_env_ids]
            n_d[d] = len(act)
            comp_d[d] = ({c: float(cat_m.reindex(act)[c].mean()) for c in rc.CATEGORIES}
                         if act else {c: 0.0 for c in rc.CATEGORIES})
        denom_comp_by_party[p] = comp_d
        denom_n_by_party[p] = n_d

    # ── 'mean action count' variants: identical numerators, but normalised over
    # ALL active-denominator synagogues (incl. those with zero environmental
    # action) rather than only the env-active ones. These feed the _per_synagogue
    # copies of each figure, whose y-axis is a plain 'mean action count'. ──
    composition_all = counts / len(denom)

    party_denom_n = {p: sum(1 for i in denom if lean_by_syn.get(i) == p)
                     for p in ("Dem", "Rep")}
    comp_by_party_all = {p: composition_for(party_ids[p], party_denom_n[p])
                         for p in ("Dem", "Rep")}

    denom_comp_by_party_all, denom_n_all = {}, {}
    for p in ("Dem", "Rep"):
        sub = dsyn[dsyn["lean"] == p]
        comp_d, n_d = {}, {}
        for d in rc.DENOM_ORDER:
            ids = list(sub.loc[sub["denomination"] == d, "synagogue_id"])
            n_d[d] = len(ids)   # ALL active-denominator synagogues in this cell
            comp_d[d] = ({c: float(cat_m.reindex(ids)[c].mean()) for c in rc.CATEGORIES}
                         if ids else {c: 0.0 for c in rc.CATEGORIES})
        denom_comp_by_party_all[p] = comp_d
        denom_n_all[p] = n_d

    out = {
        "n_environmentally_active": n_env,
        "metric": "framed action chunks per environmentally active synagogue",
        "note": "framings are non-exclusive; percentages sum to > 100%",
        "framings": rows,
        "friedman": friedman,
        "pairwise": pw.to_dict(orient="records"),
        "friedman_all_synagogues": friedman_all,
        "pairwise_all_synagogues": pw_all_fr.to_dict(orient="records"),
        "party_effect_by_framing_all_synagogues": party_effect_by_framing,
        "category_composition_per_active": {
            fr: {cat: round(float(composition.loc[fr, cat]), 3) for cat in rc.CATEGORIES}
            for fr in rc.FRAMINGS
        },
        "category_composition_by_party": {
            p: {"n_env_active": n_by_party[p],
                "composition": {fr: {cat: round(float(comp_by_party[p].loc[fr, cat]), 3)
                                     for cat in rc.CATEGORIES} for fr in rc.FRAMINGS}}
            for p in ("Dem", "Rep")
        },
        "denomination_category_composition_by_party": {
            p: {"n_env_active_by_denomination": denom_n_by_party[p],
                "composition": {d: {cat: round(denom_comp_by_party[p][d][cat], 3)
                                    for cat in rc.CATEGORIES} for d in rc.DENOM_ORDER}}
            for p in ("Dem", "Rep")
        },
    }
    (out_path / "framing.json").write_text(json.dumps(out, indent=2))
    _plot(rows, composition, out_path)
    _plot_by_party(comp_by_party, n_by_party, out_path)
    _plot_denom_by_party(denom_comp_by_party, denom_n_by_party, out_path)

    # 'mean action count' copies (normalised over all active-denominator synagogues)
    _plot_framing_categories(
        composition_all, out_path,
        "fig_framing_categories_per_synagogue.png",
        "Mean action count per synagogue")
    _plot_by_party(
        comp_by_party_all, party_denom_n, out_path,
        fname="fig_framing_categories_by_party_per_synagogue.png",
        ylabel="Mean action count per synagogue")
    _plot_denom_by_party(
        denom_comp_by_party_all, denom_n_all, out_path,
        fname="fig_denomination_categories_by_party_per_synagogue.png",
        ylabel="Mean action count per synagogue")
    print(json.dumps(out, indent=2))


def _plot(rows, composition, out_path):
    plt = rc.apply_style()
    labels = [r["framing"] for r in rows]
    colors = [rc.FRAMING_COLORS[r["framing"]] for r in rows]

    # Figure A: % of env-active synagogues with >=1 action of each framing (thesis Fig 1)
    pcts = [r["pct_active_with_framing"] for r in rows]
    psds = [r["pct_sd"] for r in rows]
    fig, ax = plt.subplots(figsize=(7, 5.5))
    ax.bar(range(len(rows)), pcts, yerr=psds, capsize=6, color=colors,
           edgecolor="white", linewidth=0.5, width=0.6)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(labels)
    ax.set_ylabel("% of env-active synagogues with ≥ 1 action")
    for i, (p, sd) in enumerate(zip(pcts, psds)):
        ax.text(i, p + sd + 1.0, f"{p:.1f}%", ha="center", va="bottom", fontsize=rc.FS_ANNOTATION)
    ax.set_ylim(0, 105)
    fig.tight_layout()
    fig.savefig(out_path / "fig_framing.png")

    # Figure B: mean framed action chunks per env-active synagogue, stacked by category
    _plot_framing_categories(composition, out_path, "fig_framing_categories.png",
                             "Mean action count per env-active synagogue")


def _plot_framing_categories(composition, out_path, fname, ylabel):
    """Stacked category composition of framed action, one bar per framing.
    `composition` is a FRAMINGS x CATEGORIES frame of mean chunks per synagogue;
    the denominator (env-active vs all active-denominator) is baked in by the
    caller, so this only differs by the y-axis label / filename."""
    plt = rc.apply_style()
    fig, ax = plt.subplots(figsize=(9, 7.5))
    x = np.arange(len(rc.FRAMINGS))
    bottom = np.zeros(len(rc.FRAMINGS))
    for cat in rc.CATEGORIES:
        vals = composition[cat].values
        ax.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
               edgecolor="white", linewidth=0.4, width=0.6)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels(rc.FRAMINGS, rotation=30, ha="right")
    ax.set_ylabel(ylabel)
    for i, total in enumerate(bottom):
        ax.text(i, total + 0.1, f"{total:.2f}", ha="center", va="bottom", fontsize=rc.FS_ANNOTATION)
    ax.legend(title="Category", bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path / fname)


def _plot_by_party(comp_by_party, n_by_party, out_path,
                   fname="fig_framing_categories_by_party.png",
                   ylabel="Mean action count per env-active synagogue"):
    """Two-panel version of fig_framing_categories: category composition of
    action by framing, for synagogues in Democratic vs Republican districts."""
    plt = rc.apply_style()
    panels = [("Dem", "Democratic", "#2166ac"), ("Rep", "Republican", "#b2182b")]
    x = np.arange(len(rc.FRAMINGS))
    ymax = max((comp_by_party[p].values.sum(axis=1).max() if n_by_party[p] else 0.0)
               for p, _, _ in panels)

    fig, axes = plt.subplots(1, 2, figsize=(13, 7.5), sharey=True)
    for ax, (p, label, title_color) in zip(axes, panels):
        comp = comp_by_party[p]
        bottom = np.zeros(len(rc.FRAMINGS))
        for cat in rc.CATEGORIES:
            vals = comp[cat].values
            ax.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
                   edgecolor="white", linewidth=0.4, width=0.6)
            bottom += vals
        ax.set_xticks(x)
        ax.set_xticklabels(rc.FRAMINGS)
        ax.set_title(label, pad=4, color=title_color)
        for i, total in enumerate(bottom):
            ax.text(i, total + ymax * 0.01, f"{total:.2f}", ha="center", va="bottom", fontsize=rc.FS_ANNOTATION)
    axes[0].set_ylabel(ylabel)
    if ymax > 0:
        axes[0].set_ylim(0, ymax * 1.12)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, title="Category", bbox_to_anchor=(1.005, 0.5),
               loc="center left")
    fig.tight_layout()
    fig.savefig(out_path / fname, bbox_inches="tight")


def _plot_denom_by_party(comp_by_party, n_by_party, out_path,
                         fname="fig_denomination_categories_by_party.png",
                         ylabel="Mean action count per env-active synagogue"):
    """Two-panel category-composition chart with DENOMINATION on the x-axis (the
    denomination analogue of fig_framing_categories_by_party): mean action count
    per synagogue, stacked by the 9 action categories, for synagogues in
    Democratic vs Republican districts. Each bar is normalised by that
    party+denomination's own synagogue count (env-active or all, per caller)."""
    plt = rc.apply_style()
    panels = [("Dem", "Democratic", "#2166ac"), ("Rep", "Republican", "#b2182b")]
    denoms = rc.DENOM_ORDER
    x = np.arange(len(denoms))
    totals = {p: [sum(comp_by_party[p][d][c] for c in rc.CATEGORIES) for d in denoms]
              for p, _, _ in panels}
    ymax = max((max(t) for t in totals.values()), default=0.0)

    fig, axes = plt.subplots(1, 2, figsize=(22, 7.5), sharey=True)
    for ax, (p, label, title_color) in zip(axes, panels):
        comp = comp_by_party[p]
        bottom = np.zeros(len(denoms))
        for cat in rc.CATEGORIES:
            vals = np.array([comp[d][cat] for d in denoms])
            ax.bar(x, vals, bottom=bottom, color=rc.CATEGORY_COLORS[cat], label=cat,
                   edgecolor="white", linewidth=0.4, width=0.7)
            bottom += vals
        panel_labels = [f"{d.replace('Non-Denominational', 'N-D')}\n(n={n_by_party[p][d]})"
                        for d in denoms]
        ax.set_xticks(x)
        ax.set_xticklabels(panel_labels, rotation=30, ha="right")
        ax.set_title(label, pad=4, color=title_color)
        for i, total in enumerate(bottom):
            ax.text(i, total + ymax * 0.01, f"{total:.1f}", ha="center",
                    va="bottom", fontsize=rc.FS_ANNOTATION)
    axes[0].set_ylabel(ylabel)
    if ymax > 0:
        axes[0].set_ylim(0, ymax * 1.12)

    handles, leg_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, leg_labels, title="Category", bbox_to_anchor=(1.005, 0.5),
               loc="center left")
    fig.tight_layout()
    fig.savefig(out_path / fname, bbox_inches="tight")


if __name__ == "__main__":
    main()
