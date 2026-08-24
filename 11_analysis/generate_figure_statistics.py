"""
Emit figure_statistics.md — the statistics that used to be printed on the
figures themselves.

The figures carry no titles and no in-plot test annotations any more, so the
inference has to live somewhere. This reads ONLY the JSON files the analysis
scripts already write beside each figure — it recomputes nothing — so the
markdown cannot drift from the figures as long as it is regenerated with them.

Every section names the PNG it describes, so a caption can be written straight
from it.

    python 11_analysis/generate_figure_statistics.py [--source page_chunks]

Output: <out_dir>/figure_statistics.md
        (11_analysis/figure_statistics.md for the default source,
         11_analysis/page_chunks/figure_statistics.md for --source page_chunks)
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402


def load(path: Path):
    """Parsed JSON, or None if the analysis hasn't been run for this source."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def fmt_p(p) -> str:
    """p-values: avoid printing a bare 0.000 for values that are merely tiny."""
    if p is None:
        return "n/a"
    if p == 0:
        return "p < 1e-300"
    if p < 0.001:
        return f"p = {p:.2e}"
    return f"p = {p:.3f}"


def section(out: list, png: str, what: str):
    out.append(f"\n## `{png}`\n")
    out.append(f"{what}\n")


def missing(out: list, why: str):
    out.append(f"*Not available — {why}.*\n")


def build(out_dir: Path, top: Path, re_dir: Path) -> str:
    md: list[str] = []
    src = rc.get_source()
    md.append("# Figure statistics\n")
    md.append(
        f"Statistics for the figures in `{out_dir.name if src != 'keyword_chunks' else '11_analysis'}/`. "
        "The figures carry no titles and no in-plot test annotations; every test "
        "reported here is what used to be drawn on the figure itself.\n")
    md.append(f"- **Action-chunk source:** `{src}`")
    md.append(f"- **Generated:** {date.today().isoformat()} "
              "by `11_analysis/generate_figure_statistics.py`")
    md.append("- **Regenerate with the figures** — these numbers are read from the "
              "analysis JSONs, so a stale run here means stale captions.\n")

    ov = load(re_dir / "overview.json")
    if ov:
        md.append("## Population\n")
        md.append(f"- Active denominator (≥1 successfully crawled page): "
                  f"**{ov['n_active_denominator']}** synagogues")
        md.append(f"- Environmentally active (≥1 action): "
                  f"**{ov['n_environmentally_active']}** "
                  f"({ov['pct_environmentally_active']}% "
                  f"± {ov['pct_env_active_ci95']} 95% CI)")
        md.append(f"- Total action chunks: **{ov['total_action_chunks']}**")
        md.append(f"- Mean per environmentally active synagogue: "
                  f"{ov['mean_action_chunks_per_active']} "
                  f"± {ov['ci95_action_chunks_per_active']} (95% CI)\n")
        md.append("Outlier exclusion (|z| > 3 on log1p action count) is applied "
                  "throughout — see `KNOWN_DATA_ISSUES.md`.\n")

    # ── categories ────────────────────────────────────────────────────────
    cat = load(re_dir / "categories.json")
    section(md, "fig_categories.png",
            "Distribution of action chunks per environmentally active synagogue, "
            "by category (box-and-whisker; ♦ marks the mean, which sits well "
            "above the median because the counts are heavily right-skewed).")
    if cat:
        f = cat["friedman"]
        md.append(f"- n = **{cat['n_environmentally_active']}** environmentally "
                  "active synagogues")
        md.append(f"- **Friedman test** across the 9 categories: "
                  f"χ²({f['df']}) = {f['chi2']:.1f}, {fmt_p(f['p'])} "
                  f"(n = {f['n_subjects']})")
        md.append(f"- Pairwise Wilcoxon with Bonferroni correction: "
                  f"**{cat['n_pairwise_significant']} of "
                  f"{cat['n_pairwise_total']}** pairs significant at 0.05\n")
        md.append("| category | mean chunks/active | SD | % with ≥1 | total chunks |")
        md.append("|---|---:|---:|---:|---:|")
        for r in cat["categories"]:
            md.append(f"| {r['category']} | {r['mean_chunks_per_active']} | "
                      f"{r['sd']} | {r['pct_active_with_action']}% | "
                      f"{r['total_chunks']} |")
        md.append("")
    else:
        missing(md, "`categories.json` not found")

    section(md, "fig_categories_pct.png",
            "Percentage of environmentally active synagogues with ≥1 action in "
            "each category.")
    if cat:
        md.append(f"- n = **{cat['n_environmentally_active']}** environmentally "
                  "active synagogues (denominator for every bar)")
        md.append("- Error bars are ±1 binomial SD; per-category values are in "
                  "the table above.\n")
    else:
        missing(md, "`categories.json` not found")

    section(md, "fig_categories_per_synagogue.png",
            "Same distribution as `fig_categories.png`, but taken over ALL "
            "active-denominator synagogues (incl. zero-action), not just "
            "environmentally active ones — the y-axis is a plain mean action "
            "count per synagogue rather than per env-active synagogue.")
    if cat and "friedman_all_synagogues" in cat:
        fa = cat["friedman_all_synagogues"]
        md.append("- Population: all active-denominator synagogues; per-category "
                  "means/SDs are the same underlying counts as `fig_categories.png` "
                  "renormalised over the larger population.")
        md.append(f"- **Friedman test** across the 9 categories: "
                  f"χ²({fa['df']}) = {fa['chi2']:.1f}, {fmt_p(fa['p'])} "
                  f"(n = {fa['n_subjects']})")
        md.append(f"- Pairwise Wilcoxon with Bonferroni correction: "
                  f"**{cat['n_pairwise_significant_all_synagogues']} of "
                  f"{cat['n_pairwise_total_all_synagogues']}** pairs significant "
                  "at 0.05\n")
    elif cat:
        md.append("- Population: all active-denominator synagogues; per-category "
                  "means/SDs are the same underlying counts as `fig_categories.png` "
                  "renormalised over the larger population, not separately "
                  "tested.\n")
    else:
        missing(md, "`categories.json` not found")

    # ── denomination ──────────────────────────────────────────────────────
    den = load(re_dir / "denomination.json")
    section(md, "fig_denomination_meanactions.png",
            "Distribution of action chunks per environmentally active synagogue, "
            "by denomination (ordered by median).")
    if den:
        k = den["kruskal"]
        md.append(f"- **Kruskal–Wallis** across denominations: "
                  f"H({k['df']}) = {k['H']:.1f}, {fmt_p(k['p'])} "
                  f"({k['n_groups']} groups, N = {k['n_total']})")
        md.append(f"- Significant pairwise differences (Mann–Whitney, Bonferroni): "
                  f"**{len(den['pairwise_significant'])}**")
        for r in den["pairwise_significant"][:10]:
            md.append(f"  - {r['group1']} vs {r['group2']}: "
                      f"means {r['mean1']:.2f} vs {r['mean2']:.2f}, "
                      f"p(Bonf) = {r['p_bonf']:.4f}")
        md.append("")
    else:
        missing(md, "`denomination.json` not found")

    section(md, "fig_denomination_pct.png",
            "Percentage of synagogues in each denomination that took ≥1 "
            "environmental action. Bar labels carry the denominator n.")
    if den:
        md.append("| denomination | n total | n env-active | % active | ±1 SD |")
        md.append("|---|---:|---:|---:|---:|")
        for r in den["denominations"]:
            md.append(f"| {r['denomination']} | {r['n_total']} | "
                      f"{r['n_env_active']} | {r['pct_env_active']}% | "
                      f"{r['pct_sd']} |")
        md.append("")
    else:
        missing(md, "`denomination.json` not found")

    section(md, "fig_denomination_categories.png",
            "Category composition of environmental action by denomination "
            "(mean chunks per environmentally active synagogue, stacked).")
    section(md, "fig_denomination_framings.png",
            "Framing composition (Implicit / Explicit / Embedded) by "
            "denomination.")
    section(md, "fig_denomination_by_party.png",
            "Denominational makeup of synagogues in Democratic vs Republican "
            "congressional districts. Panels are labelled by party; the "
            "per-panel sample sizes are below.")
    if den:
        for p, blk in den["denomination_prevalence_by_party"].items():
            md.append(f"- **{p}** districts: n = **{blk['n_synagogues']}** synagogues")
        md.append("")
    else:
        missing(md, "`denomination.json` not found")

    section(md, "fig_denomination_meanactions_per_synagogue.png",
            "Same distribution as `fig_denomination_meanactions.png`, but taken "
            "over ALL synagogues of each denomination (incl. zero-action), not "
            "just environmentally active ones.")
    if den and "kruskal_all_synagogues" in den:
        ka = den["kruskal_all_synagogues"]
        md.append("- Population: all active-denominator synagogues per "
                  "denomination (`n_total` in the table above), not just "
                  "environmentally active ones.")
        md.append(f"- **Kruskal–Wallis** across denominations: "
                  f"H({ka['df']}) = {ka['H']:.1f}, {fmt_p(ka['p'])} "
                  f"({ka['n_groups']} groups, N = {ka['n_total']})")
        dunn_all = den.get("dunn_posthoc_bh_all_synagogues", [])
        md.append(f"- Significant pairwise differences (Dunn's post-hoc, "
                  f"Benjamini-Hochberg): **{len(dunn_all)}**")
        for r in dunn_all[:10]:
            md.append(f"  - {r['group1']} vs {r['group2']}: "
                      f"means {r['mean1']:.2f} vs {r['mean2']:.2f}, "
                      f"p(adj) = {r['p_adj']:.4f}")
        md.append("")
    elif den:
        md.append("- Population: all active-denominator synagogues per "
                  "denomination (`n_total` in the table above), not just "
                  "environmentally active ones — not separately tested.\n")
    else:
        missing(md, "`denomination.json` not found")

    for png, what in [
        ("fig_denomination_categories_per_synagogue.png",
         "Same stacked category composition as `fig_denomination_categories.png`, "
         "renormalised over all synagogues of each denomination."),
        ("fig_denomination_framings_per_synagogue.png",
         "Same framing composition as `fig_denomination_framings.png`, "
         "renormalised over all synagogues of each denomination."),
    ]:
        section(md, png, what)
        if den:
            md.append("- Population: all active-denominator synagogues per "
                      "denomination (`n_total` in the table above), not just "
                      "environmentally active ones — not separately tested.\n")
        else:
            missing(md, "`denomination.json` not found")

    pt = den.get("proportion_tests") if den else None

    section(md, "fig_denomination_categories_per_synagogue_proportion.png",
            "Relative-mix version of `fig_denomination_categories_per_synagogue.png`: "
            "each denomination's bar is normalised to its OWN total (100% "
            "stacked), showing category *share* rather than absolute mean "
            "action count.")
    if pt and "spirituality_worship_share_of_actions" in pt:
        t = pt["spirituality_worship_share_of_actions"]
        k = t["kruskal"]
        md.append(f"- **Kruskal–Wallis** on each synagogue's Spirituality & "
                  f"Worship share of its own action total, across "
                  f"{t['n_denominations_tested']} denominations: "
                  f"H({k['df']}) = {k['H']:.1f}, {fmt_p(k['p'])} "
                  f"(N = {t['n_synagogues']})")
        n_sig = sum(1 for r in t["dunn_posthoc_bh"] if r["sig"])
        md.append(f"- **Dunn's post-hoc** (Benjamini-Hochberg-corrected): "
                  f"**{n_sig} of {len(t['dunn_posthoc_bh'])}** pairs significant "
                  "at 0.05\n")
    else:
        missing(md, "`denomination.json` proportion_tests not found")

    section(md, "fig_denomination_categories_per_synagogue_pie.png",
            "Same relative-mix data as the proportion figure above, as one pie "
            "chart per denomination instead of a single 100%-stacked bar. See "
            "`fig_denomination_categories_per_synagogue_proportion.png` above "
            "for the underlying test.\n")

    section(md, "fig_denomination_framings_per_synagogue_proportion.png",
            "Relative-mix version of `fig_denomination_framings_per_synagogue.png`: "
            "each denomination's bar is normalised to its OWN total framed-"
            "action volume (100% stacked) by the Implicit/Explicit/Embedded "
            "axis.")
    if pt and "embedded_share_of_framed_actions" in pt:
        for label, name in [("embedded_share_of_framed_actions", "Embedded"),
                            ("explicit_share_of_framed_actions", "Explicit")]:
            t = pt[label]
            k = t["kruskal"]
            n_sig = sum(1 for r in t["dunn_posthoc_bh"] if r["sig"])
            md.append(f"- **{name} share of framed actions** — Kruskal–Wallis: "
                      f"H({k['df']}) = {k['H']:.1f}, {fmt_p(k['p'])} "
                      f"(N = {t['n_synagogues']}); Dunn's post-hoc (BH): "
                      f"**{n_sig} of {len(t['dunn_posthoc_bh'])}** pairs "
                      "significant")
        md.append("")
    else:
        missing(md, "`denomination.json` proportion_tests not found")

    # ── framing ───────────────────────────────────────────────────────────
    fr = load(re_dir / "framing.json")
    section(md, "fig_framing.png",
            "Percentage of environmentally active synagogues with ≥1 action of "
            "each framing. Framings are NOT mutually exclusive, so the bars sum "
            "to more than 100%.")
    if fr:
        md.append(f"- n = **{fr['n_environmentally_active']}** environmentally "
                  "active synagogues")
        md.append(f"- Note: {fr['note']}\n")
        md.append("| framing | n with ≥1 | % active | mean chunks/active | total |")
        md.append("|---|---:|---:|---:|---:|")
        for r in fr["framings"]:
            md.append(f"| {r['framing']} | {r['n_synagogues_with_framing']} | "
                      f"{r['pct_active_with_framing']}% | "
                      f"{r['mean_chunks_per_active']} | {r['total_chunks']} |")
        md.append("")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    section(md, "fig_framing_categories.png",
            "Category composition of environmental action by framing.")
    if fr:
        f = fr["friedman"]
        md.append(f"- n = **{fr['n_environmentally_active']}**")
        md.append(f"- **Friedman test** across the 3 framings: "
                  f"χ²({f['df']}) = {f['chi2']:.1f}, {fmt_p(f['p'])}\n")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    section(md, "fig_framing_categories_by_party.png",
            "Category composition by framing, split by congressional-district "
            "party. Each panel is normalised by its own party's env-active count.")
    if fr:
        for p, blk in fr["category_composition_by_party"].items():
            md.append(f"- **{p}** districts: n = **{blk['n_env_active']}** "
                      "environmentally active synagogues")
        md.append("")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    section(md, "fig_denomination_categories_by_party.png",
            "Category composition of action by DENOMINATION (x-axis), split by "
            "congressional-district party — the denomination analogue of "
            "`fig_framing_categories_by_party.png`. Each bar is normalised by "
            "that party+denomination's own environmentally-active count.")
    if fr:
        for p, blk in fr["denomination_category_composition_by_party"].items():
            n_tot = sum(blk["n_env_active_by_denomination"].values())
            md.append(f"- **{p}** districts: n = **{n_tot}** environmentally "
                      "active synagogues across 9 denominations")
        md.append("")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    section(md, "fig_framing_categories_per_synagogue.png",
            "Same category composition by framing as `fig_framing_categories.png`, "
            "renormalised over ALL active-denominator synagogues (incl. "
            "zero-action).")
    if fr and "friedman_all_synagogues" in fr:
        fa = fr["friedman_all_synagogues"]
        md.append("- Population: all active-denominator synagogues, not just "
                  "environmentally active ones.")
        md.append(f"- **Friedman test** across the 3 framings: "
                  f"χ²({fa['df']}) = {fa['chi2']:.1f}, {fmt_p(fa['p'])} "
                  f"(n = {fa['n_subjects']})\n")
    elif fr:
        md.append("- Population: all active-denominator synagogues, not just "
                  "environmentally active ones — not separately tested.\n")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    section(md, "fig_framing_categories_by_party_per_synagogue.png",
            "Same two-panel framing chart as `fig_framing_categories_by_party.png`, "
            "renormalised over all active-denominator synagogues per party.")
    if fr and "party_effect_by_framing_all_synagogues" in fr:
        md.append("- Population: all active-denominator synagogues, not just "
                  "environmentally active ones.")
        md.append("- **Mann–Whitney U** (Dem vs Rep), one test per framing, "
                  "Bonferroni-corrected across the 3 — not pooled into a single "
                  "Kruskal-Wallis/Dunn's omnibus, since framings are not "
                  "mutually exclusive (the same synagogue contributes to all "
                  "three, so only the cross-party comparison within a single "
                  "framing is between independent groups):")
        for r in fr["party_effect_by_framing_all_synagogues"]:
            star = " **(significant)**" if r["sig"] else ""
            md.append(f"  - {r['framing']}: Dem mean {r['mean_dem']:.2f} "
                      f"(n={r['n_dem']}) vs Rep mean {r['mean_rep']:.2f} "
                      f"(n={r['n_rep']}), U = {r['U']:.1f}, "
                      f"p(Bonf) = {r['p_bonf']:.4f}{star}")
        md.append("")
    elif fr:
        md.append("- Population: all active-denominator synagogues, not just "
                  "environmentally active ones — not separately tested.\n")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    section(md, "fig_denomination_categories_by_party_per_synagogue.png",
            "Same two-panel denomination chart as "
            "`fig_denomination_categories_by_party.png`, renormalised over all "
            "active-denominator synagogues per party+denomination.")
    if fr:
        md.append("- Population: all active-denominator synagogues, not just "
                  "environmentally active ones — not separately tested.\n")
    else:
        missing(md, "`framing.json` not found — run analysis_framing.py")

    # ── political ─────────────────────────────────────────────────────────
    pol = load(re_dir / "political.json")
    section(md, "fig_state_meanactions.png",
            "Distribution of action chunks per environmentally active synagogue "
            "across four states (Dem-leaning CA, NY vs Rep-leaning TX, FL).")
    if pol:
        fs = pol["four_state"]
        k = fs["kruskal"]
        md.append(f"- **Kruskal–Wallis** across the 4 states: "
                  f"H({k['df']}) = {k['H']:.1f}, {fmt_p(k['p'])}")
        md.append("- Pairwise Mann–Whitney with Bonferroni correction:")
        for r in fs["pairwise"]:
            star = " **(significant)**" if r["sig"] else ""
            md.append(f"  - {r['group1']} vs {r['group2']}: "
                      f"means {r['mean1']:.2f} vs {r['mean2']:.2f}, "
                      f"p(Bonf) = {r['p_bonf']:.4f}{star}")
        md.append("")
        md.append("| state | lean | n total | n env-active | mean chunks/active |")
        md.append("|---|---|---:|---:|---:|")
        for r in fs["states"]:
            md.append(f"| {r['state']} | {r['lean']} | {r['n_total']} | "
                      f"{r['n_env_active']} | {r['mean_chunks_per_active']} |")
        md.append("")
    else:
        missing(md, "`political.json` not found")

    section(md, "fig_state_categories.png",
            "California vs Florida, action frequency by category. Stars on the "
            "bars mark significance: *** p<.001, ** p<.01, * p<.05 — all "
            "Bonferroni-corrected across the 9 categories.")
    if pol:
        md.append("| category | CA mean | FL mean | p (raw) | p (Bonferroni) |")
        md.append("|---|---:|---:|---:|---:|")
        for r in pol["ca_vs_fl_categories"]:
            md.append(f"| {r['category']} | {r['CA_mean']} | {r['FL_mean']} | "
                      f"{r['p_raw']:.4f} | {r['p_bonf']:.4f} |")
        md.append("")
    else:
        missing(md, "`political.json` not found")

    section(md, "fig_district_lean_meanactions.png",
            "Action chunks per environmentally active synagogue by "
            "congressional-district lean (general-election winner).")
    if pol:
        dl = pol["district_lean"]
        mw = dl["mann_whitney"]
        md.append(f"- Election year: {pol['election_year']}")
        md.append(f"- **Mann–Whitney U** (Dem vs Rep): U = {mw['U']:.1f}, "
                  f"{fmt_p(mw['p'])}")
        for r in dl["groups"]:
            md.append(f"- {r['district_lean']}: n = {r['n_total']} "
                      f"({r['n_env_active']} env-active), "
                      f"mean {r['mean_chunks_per_active']} ± {r['sd']}")
        md.append("")
    else:
        missing(md, "`political.json` not found")

    section(md, "fig_state_meanactions_per_synagogue.png",
            "Same four-state distribution as `fig_state_meanactions.png`, but "
            "taken over ALL synagogues in each state (incl. zero-action), not "
            "just environmentally active ones.")
    if pol and "four_state_all_synagogues" in pol:
        fsa = pol["four_state_all_synagogues"]
        ka = fsa["kruskal"]
        md.append("- Population: all active-denominator synagogues per state, "
                  "not just environmentally active ones.")
        md.append(f"- **Kruskal–Wallis** across the 4 states: "
                  f"H({ka['df']}) = {ka['H']:.1f}, {fmt_p(ka['p'])}")
        md.append("- Pairwise Mann–Whitney with Bonferroni correction:")
        for r in fsa["pairwise"]:
            star = " **(significant)**" if r["sig"] else ""
            md.append(f"  - {r['group1']} vs {r['group2']}: "
                      f"means {r['mean1']:.2f} vs {r['mean2']:.2f}, "
                      f"p(Bonf) = {r['p_bonf']:.4f}{star}")
        md.append("")
        md.append("| state | lean | n total | mean chunks/synagogue |")
        md.append("|---|---|---:|---:|")
        for r in fsa["states"]:
            md.append(f"| {r['state']} | {r['lean']} | {r['n_total']} | "
                      f"{r['mean_chunks_per_synagogue']} |")
        md.append("")
    elif pol:
        md.append("- Population: all active-denominator synagogues per "
                  "state, not just environmentally active ones — not "
                  "separately tested.\n")
    else:
        missing(md, "`political.json` not found")

    section(md, "fig_state_categories_per_synagogue.png",
            "Same CA vs FL category comparison as `fig_state_categories.png`, "
            "renormalised over all synagogues in each state.")
    if pol and "ca_vs_fl_categories_all_synagogues" in pol:
        md.append("- Population: all active-denominator synagogues per state, "
                  "not just environmentally active ones.")
        md.append("| category | CA mean | FL mean | p (raw) | p (Bonferroni) |")
        md.append("|---|---:|---:|---:|---:|")
        for r in pol["ca_vs_fl_categories_all_synagogues"]:
            md.append(f"| {r['category']} | {r['CA_mean']} | {r['FL_mean']} | "
                      f"{r['p_raw']:.4f} | {r['p_bonf']:.4f} |")
        md.append("")
    elif pol:
        md.append("- Population: all active-denominator synagogues per "
                  "state, not just environmentally active ones — not "
                  "separately tested.\n")
    else:
        missing(md, "`political.json` not found")

    section(md, "fig_district_lean_meanactions_per_synagogue.png",
            "Same district-lean distribution as "
            "`fig_district_lean_meanactions.png`, but taken over ALL synagogues "
            "(incl. zero-action) rather than only environmentally active ones.")
    dla = pol.get("district_lean_all_synagogues") if pol else None
    if dla:
        mw = dla["mann_whitney"]
        md.append(f"- **Mann–Whitney U** (Dem vs Rep, all synagogues): "
                  f"U = {mw['U']:.1f}, {fmt_p(mw['p'])}")
        for r in dla["groups"]:
            md.append(f"- {r['district_lean']}: n = {r['n_total']}, "
                      f"mean {r['mean_chunks_per_synagogue']} ± {r['sd']}")
        md.append("")
    else:
        missing(md, "`political.json` district_lean_all_synagogues not found")

    # ── clustering ────────────────────────────────────────────────────────
    cl = load(re_dir / "clustering.json")
    for png, what in [
        ("fig_cluster_silhouette.png",
         "k-means model selection. Panels: Silhouette (higher is better), "
         "Davies-Bouldin (lower is better), Inertia (elbow)."),
        ("fig_cluster_scatter.png",
         "Environmentally active synagogues in PCA feature space, coloured by "
         "cluster."),
        ("fig_cluster_profiles.png",
         "Category composition of each engagement cluster."),
    ]:
        section(md, png, what)
        if cl:
            md.append(f"- n = **{cl['n_env_active']}** environmentally active "
                      "synagogues")
            md.append(f"- Chosen **k = {cl['best_k']}**, silhouette = "
                      f"{cl['best_silhouette']:.3f} "
                      f"(threshold {cl['silhouette_threshold']}) → "
                      f"**{cl['cluster_structure']}** cluster structure")
            md.append(f"- {cl['interpretation']}\n")
        else:
            missing(md, "`clustering.json` not found")

    # ── keyword hit rate (keyword_chunks-only; fixed path regardless of source) ──
    re_base = re_dir if src == "keyword_chunks" else re_dir.parent
    kh = load(re_base / "keyword_hitrate.json")
    section(md, "fig_keyword_hitrate.png",
            "Keyword precision: hit rate (% of a keyword's occurrences "
            "classified as a real action) vs total occurrences, one point per "
            "matched keyword. Structurally keyword_chunks-only — \"keyword\" "
            "has no page_chunks equivalent, so this figure is not regenerated "
            "per source.")
    if kh:
        md.append(f"- **{kh['n_keywords']}** distinct keywords, "
                  f"**{kh['total_occurrences']}** total occurrences, "
                  f"**{kh['total_hits']}** classified as a real action")
        md.append(f"- Pooled hit rate = **{100*kh['global_hit_rate']:.1f}%**; "
                  f"median per-keyword hit rate = "
                  f"**{100*kh['median_keyword_hit_rate']:.1f}%**\n")
    else:
        missing(md, "`keyword_hitrate.json` not found — run "
                    "analysis_keyword_hitrate.py")

    # ── top-level figures ─────────────────────────────────────────────────
    lg = load(top / "action_logit_osm_distance.json")
    section(md, "action_logit_table.png",
            "Logistic regression: P(≥1 environmental action) predicted from "
            "distance to 7 OSM site types. Significance column: *** p<.001, "
            "** p<.01, * p<.05, . p<.10.")
    if lg:
        lr = lg["likelihood_ratio_test"]
        md.append(f"- n = **{lg['n']}** synagogues "
                  f"({lg['n_has_action']} with ≥1 action, "
                  f"{100*lg['n_has_action']/lg['n']:.1f}%)")
        md.append(f"- **Likelihood-ratio test** vs intercept-only: "
                  f"χ²({lr['df']}) = {lr['chi2']:.2f}, {fmt_p(lr['p_value'])}")
        md.append(f"- McFadden pseudo-R² = {lg['mcfadden_pseudo_r2']:.4f}")
        md.append(f"- 5-fold CV accuracy = {lg['cv_accuracy']:.3f} "
                  f"(majority baseline {lg['majority_baseline']:.3f})")
        md.append(f"- ROC AUC = {lg['cv_roc_auc']:.3f} (chance 0.50)\n")
    else:
        missing(md, "`action_logit_osm_distance.json` not found")

    section(md, "action_logit_distance_distributions.png",
            "Distance distribution split by outcome, one panel per predictor. "
            "Dashed lines are group medians. Panel titles give only the site "
            "type; the per-predictor odds ratios are below.")
    if lg:
        md.append("| site type | OR per +1 SD | p |")
        md.append("|---|---:|---:|")
        for c in lg["coefficients"]:
            name = c.get("predictor") or c.get("site_type") or "?"
            orr = c.get("odds_ratio_per_sd", c.get("odds_ratio"))
            md.append(f"| {name} | {orr:.3f} | {c.get('p_value', float('nan')):.4f} |")
        md.append("")
    else:
        missing(md, "`action_logit_osm_distance.json` not found")

    osm = load(top / "action_vs_osm_distance.json")
    section(md, "action_distance_cornerplot.png",
            "Lower-triangle scatter matrix of the 7 OSM distances, coloured by "
            "log(1 + action count). Spearman correlations of action count "
            "against each distance:")
    if osm:
        md.append(f"- n = **{osm['n_synagogues']}** synagogues; "
                  f"{osm['multiple_comparison']}\n")
        md.append("| site type | Spearman rho | p (raw) | p (Bonferroni) | median km |")
        md.append("|---|---:|---:|---:|---:|")
        for r in osm["results"]:
            sig = " *" if r.get("significant_bonferroni_0.05") else ""
            md.append(f"| {r['site_type']}{sig} | {r['spearman_rho']:.4f} | "
                      f"{r['p_value']:.4f} | {r['p_value_bonferroni']:.4f} | "
                      f"{r['median_dist_km']} |")
        md.append("")
    else:
        missing(md, "`action_vs_osm_distance.json` not found")

    lee = load(top / "action_political_leesL.json")
    section(md, "action_political_leesL_map.png",
            "Local Lee's L — spatial co-patterning of Democratic lean and "
            "environmental action count. Orange = positive co-cluster, "
            "purple = negative. CONUS only.")
    if lee:
        md.append(f"- n = **{lee['n']}** districts, k = {lee['k_neighbours']} "
                  f"neighbours, {lee['perms']} permutations ({lee['year']})")
        md.append(f"- **Lee's L = {lee['lees_L']:.4f}**, z = {lee['z_score']:.3f}, "
                  f"two-sided {fmt_p(lee['p_value_two_sided'])}")
        md.append(f"- Aspatial Pearson r = {lee['aspatial_pearson_r']:.4f} "
                  f"({fmt_p(lee['aspatial_pearson_p'])})")
        md.append(f"- Moran's I — lean {lee.get('morans_I_lean')}, "
                  f"actions {lee.get('morans_I_actions')}")
        md.append(f"- H0: {lee['null_hypothesis']}\n")
    else:
        missing(md, "`action_political_leesL.json` not found")

    rd = load(top / "rd_action_means.json")
    section(md, "rd_action_means.png",
            "Mean environmental actions per synagogue by congressional-district "
            "party (bars are mean ± 1 SD; labels give both).")
    if rd:
        t = rd.get("test", {})
        for party in ("Democrat", "Republican"):
            b = rd.get(party)
            if isinstance(b, dict):
                md.append(f"- **{party}**: n = {b.get('n')}, "
                          f"mean = {b.get('mean')}, SD = {b.get('sd')}")
        if t:
            md.append(f"- **Mann–Whitney U**: U = {t.get('u_statistic')}, "
                      f"{fmt_p(t.get('p_value'))}, "
                      f"rank-biserial r = {t.get('rank_biserial')}")
        md.append("")
    else:
        missing(md, "`rd_action_means.json` not found")

    dem = load(top / "action_vs_dem_district.json")
    if dem:
        md.append("\n## `action_vs_dem_district` (no figure)\n")
        md.append(f"- n = **{dem['n_total']}** synagogues ({dem['year']})")
        md.append(f"- Odds ratio (Dem vs Rep) = {dem['odds_ratio_dem_vs_rep']}, "
                  f"95% CI {dem['odds_ratio_95ci']}")
        md.append(f"- χ² = {dem['chi_square']}, "
                  f"{fmt_p(dem['chi_square_p_value'])}, "
                  f"φ = {dem['phi_coefficient']}\n")

    cm = load(out_dir / "county_action_map.json")
    section(md, "county_action_map.png",
            "County-level choropleth. Each county is filled by the raw "
            "percentage of its crawled synagogues with ≥1 environmental "
            "action; counties with no crawled synagogue are pale grey. The "
            "fill does **not** account for sample size — see the counts below "
            "before reading any single county.")
    if cm:
        md.append(f"- **{cm['n_counties']}** counties, "
                  f"**{cm['n_synagogues']}** crawled synagogues placed")
        md.append(f"- Sample sizes are thin: **{cm['n_counties_n1']}** counties "
                  f"have exactly 1 synagogue, only **{cm['n_counties_ge5']}** "
                  f"have ≥5 (max {cm['max_n']}). A fraction from n=1 is only "
                  "ever 0% or 100% — which is why this is a bubble map and not "
                  "a choropleth.")
        md.append(f"- **{cm['n_dropped']}** crawled synagogues are not shown: "
                  "they have no `census_tract_geoid`, and all of them lack "
                  "coordinates entirely, so no method could place them.\n")
    else:
        missing(md, "`county_action_map.json` not found — run "
                    "`county_action_map.py`")

    dm = load(out_dir / "district_action_map.json")
    for png, extra in [
        ("district_action_map.png",
         "Districts keep their true shape and size."),
        ("district_action_cartogram.png",
         "Each district is additionally scaled about its own centroid by "
         "sqrt(n / n_max), so a district's drawn size reflects how many "
         "crawled synagogues back its colour; true outlines are shown in pale "
         "grey underneath."),
    ]:
        section(md, png,
                "Congressional-district choropleth (119th Congress), filled by "
                "the raw percentage of each district's crawled synagogues with "
                "\u22651 environmental action. Districts are drawn to roughly "
                "equal population, so the sample per unit is far more even than "
                "counties \u2014 this is the best-supported of the three "
                "geographies. " + extra)
        if dm:
            md.append(f"- **{dm['n_districts']}** districts, "
                      f"**{dm['n_synagogues']}** crawled synagogues placed")
            md.append(f"- **{dm['n_districts_n1']}** districts rest on a single "
                      f"synagogue and **{dm['n_districts_ge5']}** have \u22655 "
                      f"(max {dm['max_n']}) \u2014 compare the county map, where "
                      "243 of 522 units have n=1.")
            md.append(f"- **{dm['n_dropped']}** crawled synagogues have no "
                      "district assigned and are not shown.\n")
        else:
            missing(md, "`district_action_map.json` not found \u2014 run "
                        "`district_action_map.py`")

    dam = load(out_dir / "dense_area_bubble_map.json")
    section(md, "dense_area_bubble_map.png",
            "Two resolutions at once: a bubble wherever synagogue dots would "
            "visually overlap at the rendered scale (area = synagogue count, "
            "fill = % with ≥1 action; clusters found dynamically via DBSCAN, "
            "not a fixed city list), and one dot per synagogue left over "
            "(colour = took ≥1 action or not). See KNOWN_DATA_ISSUES.md for "
            "why the clustering is scale-dependent and this figure's "
            "network-access requirement.")
    if dam:
        md.append(f"- **{dam['n_total']}** crawled synagogues placed into "
                  f"**{dam['n_bubbles']}** bubbles (**"
                  f"{sum(b['n'] for b in dam['buckets'])}** synagogues) plus "
                  f"**{dam['n_outside']}** individual dots (`cluster_eps_px="
                  f"{dam['cluster_eps_px']}`, `min_cluster_size="
                  f"{dam['min_cluster_size']}`, `fit_zoom_used="
                  f"{dam['fit_zoom_used']}`)")
        for b in sorted(dam["buckets"], key=lambda b: -b["n"])[:15]:
            md.append(f"  - n={b['n']} @ ({b['centroid_lat']}, "
                      f"{b['centroid_lon']}): {b['pct_with_action']}% with "
                      "≥1 action")
        if dam["n_bubbles"] > 15:
            md.append(f"  - ... and {dam['n_bubbles'] - 15} more bubbles")
        md.append(f"- Outside dots: **{dam['pct_outside_with_action']}%** "
                  f"({dam['n_outside_with_action']}/{dam['n_outside']}) with "
                  "≥1 action\n")
    else:
        missing(md, "`dense_area_bubble_map.json` not found — run "
                    "`dense_area_bubble_map.py`")

    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    rc.set_source(args.source)
    out_dir = rc.out_dir(HERE)
    re_dir = rc.out_dir(HERE / "recreate_existing")

    md = build(out_dir, out_dir, re_dir)
    dest = out_dir / "figure_statistics.md"
    dest.write_text(md)
    n_sections = md.count("\n## ")
    print(f"Wrote {dest}  ({n_sections} sections, {len(md.splitlines())} lines)")


if __name__ == "__main__":
    main()
