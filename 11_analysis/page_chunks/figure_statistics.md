# Figure statistics

Statistics for the figures in `page_chunks/`. The figures carry no titles and no in-plot test annotations; every test reported here is what used to be drawn on the figure itself.

- **Action-chunk source:** `page_chunks`
- **Generated:** 2026-08-18 by `11_analysis/generate_figure_statistics.py`
- **Regenerate with the figures** — these numbers are read from the analysis JSONs, so a stale run here means stale captions.

## Population

- Active denominator (≥1 successfully crawled page): **2657** synagogues
- Environmentally active (≥1 action): **1353** (50.9% ± 1.9 95% CI)
- Total action chunks: **13561**
- Mean per environmentally active synagogue: 10.02 ± 0.85 (95% CI)

Outlier exclusion (|z| > 3 on log1p action count) is applied throughout — see `KNOWN_DATA_ISSUES.md`.


## `fig_categories.png`

Distribution of action chunks per environmentally active synagogue, by category (box-and-whisker; ♦ marks the mean, which sits well above the median because the counts are heavily right-skewed).

- n = **1353** environmentally active synagogues
- **Friedman test** across the 9 categories: χ²(8) = 4958.2, p < 1e-300 (n = 1353)
- Pairwise Wilcoxon with Bonferroni correction: **33 of 36** pairs significant at 0.05

| category | mean chunks/active | SD | % with ≥1 | total chunks |
|---|---:|---:|---:|---:|
| Spirituality & Worship | 4.788 | 9.813 | 77.4% | 6478 |
| Community | 2.97 | 6.381 | 61.6% | 4019 |
| Kitchen | 0.717 | 3.262 | 21.2% | 970 |
| Operations & Maintenance | 0.497 | 3.069 | 15.0% | 673 |
| Waste | 0.466 | 3.601 | 13.3% | 630 |
| Energy | 0.288 | 2.941 | 7.2% | 389 |
| Environmental & Climate Justice | 0.248 | 1.727 | 8.4% | 335 |
| Other | 0.028 | 0.202 | 2.1% | 38 |
| Water | 0.021 | 0.215 | 1.3% | 29 |


## `fig_categories_pct.png`

Percentage of environmentally active synagogues with ≥1 action in each category.

- n = **1353** environmentally active synagogues (denominator for every bar)
- Error bars are ±1 binomial SD; per-category values are in the table above.


## `fig_categories_per_synagogue.png`

Same distribution as `fig_categories.png`, but taken over ALL active-denominator synagogues (incl. zero-action), not just environmentally active ones — the y-axis is a plain mean action count per synagogue rather than per env-active synagogue.

- Population: all active-denominator synagogues; per-category means/SDs are the same underlying counts as `fig_categories.png` renormalised over the larger population.
- **Friedman test** across the 9 categories: χ²(8) = 4958.2, p < 1e-300 (n = 2657)
- Pairwise Wilcoxon with Bonferroni correction: **33 of 36** pairs significant at 0.05


## `fig_denomination_meanactions.png`

Distribution of action chunks per environmentally active synagogue, by denomination (ordered by median).

- **Kruskal–Wallis** across denominations: H(8) = 114.1, p = 5.44e-21 (9 groups, N = 1327)
- Significant pairwise differences (Mann–Whitney, Bonferroni): **9**
  - Reform vs Chabad: means 13.29 vs 5.33, p(Bonf) = 0.0000
  - Reform vs Orthodox: means 13.29 vs 4.71, p(Bonf) = 0.0000
  - Conservative vs Chabad: means 9.86 vs 5.33, p(Bonf) = 0.0000
  - Conservative vs Orthodox: means 9.86 vs 4.71, p(Bonf) = 0.0001
  - Reconstructionist vs Chabad: means 19.48 vs 5.33, p(Bonf) = 0.0001
  - Reconstructionist vs Orthodox: means 19.48 vs 4.71, p(Bonf) = 0.0002
  - Humanistic vs Chabad: means 21.00 vs 5.33, p(Bonf) = 0.0055
  - Humanistic vs Orthodox: means 21.00 vs 4.71, p(Bonf) = 0.0073
  - Non-Denominational Progressive vs Orthodox: means 12.44 vs 4.71, p(Bonf) = 0.0137


## `fig_denomination_pct.png`

Percentage of synagogues in each denomination that took ≥1 environmental action. Bar labels carry the denominator n.

| denomination | n total | n env-active | % active | ±1 SD |
|---|---:|---:|---:|---:|
| Jewish Renewal | 25 | 19 | 76.0% | 8.5 |
| Reconstructionist | 66 | 54 | 81.8% | 4.7 |
| Humanistic | 22 | 14 | 63.6% | 10.3 |
| Reform | 550 | 370 | 67.3% | 2.0 |
| Non-Denominational Progressive | 187 | 122 | 65.2% | 3.5 |
| Conservative | 402 | 247 | 61.4% | 2.4 |
| Non-Denominational Conservative | 27 | 12 | 44.4% | 9.6 |
| Orthodox | 582 | 111 | 19.1% | 1.6 |
| Chabad | 723 | 378 | 52.3% | 1.9 |


## `fig_denomination_categories.png`

Category composition of environmental action by denomination (mean chunks per environmentally active synagogue, stacked).


## `fig_denomination_framings.png`

Framing composition (Implicit / Explicit / Embedded) by denomination.


## `fig_denomination_by_party.png`

Denominational makeup of synagogues in Democratic vs Republican congressional districts. Panels are labelled by party; the per-panel sample sizes are below.

- **Dem** districts: n = **1810** synagogues
- **Rep** districts: n = **654** synagogues


## `fig_denomination_meanactions_per_synagogue.png`

Same distribution as `fig_denomination_meanactions.png`, but taken over ALL synagogues of each denomination (incl. zero-action), not just environmentally active ones.

- Population: all active-denominator synagogues per denomination (`n_total` in the table above), not just environmentally active ones.
- **Kruskal–Wallis** across denominations: H(8) = 413.8, p = 2.13e-84 (9 groups, N = 2584)
- Significant pairwise differences (Dunn's post-hoc, Benjamini-Hochberg): **21**
  - Reform vs Orthodox: means 8.94 vs 0.90, p(adj) = 0.0000
  - Conservative vs Orthodox: means 6.06 vs 0.90, p(adj) = 0.0000
  - Non-Denominational Progressive vs Orthodox: means 8.12 vs 0.90, p(adj) = 0.0000
  - Reconstructionist vs Orthodox: means 15.94 vs 0.90, p(adj) = 0.0000
  - Orthodox vs Chabad: means 0.90 vs 2.78, p(adj) = 0.0000
  - Reform vs Chabad: means 8.94 vs 2.78, p(adj) = 0.0000
  - Jewish Renewal vs Orthodox: means 15.48 vs 0.90, p(adj) = 0.0000
  - Reconstructionist vs Chabad: means 15.94 vs 2.78, p(adj) = 0.0000
  - Humanistic vs Orthodox: means 13.36 vs 0.90, p(adj) = 0.0000
  - Conservative vs Chabad: means 6.06 vs 2.78, p(adj) = 0.0000


## `fig_denomination_categories_per_synagogue.png`

Same stacked category composition as `fig_denomination_categories.png`, renormalised over all synagogues of each denomination.

- Population: all active-denominator synagogues per denomination (`n_total` in the table above), not just environmentally active ones — not separately tested.


## `fig_denomination_framings_per_synagogue.png`

Same framing composition as `fig_denomination_framings.png`, renormalised over all synagogues of each denomination.

- Population: all active-denominator synagogues per denomination (`n_total` in the table above), not just environmentally active ones — not separately tested.


## `fig_denomination_categories_per_synagogue_proportion.png`

Relative-mix version of `fig_denomination_categories_per_synagogue.png`: each denomination's bar is normalised to its OWN total (100% stacked), showing category *share* rather than absolute mean action count.

- **Kruskal–Wallis** on each synagogue's Spirituality & Worship share of its own action total, across 9 denominations: H(8) = 22.8, p = 0.004 (N = 1327)
- **Dunn's post-hoc** (Benjamini-Hochberg-corrected): **5 of 36** pairs significant at 0.05


## `fig_denomination_categories_per_synagogue_pie.png`

Same relative-mix data as the proportion figure above, as one pie chart per denomination instead of a single 100%-stacked bar. See `fig_denomination_categories_per_synagogue_proportion.png` above for the underlying test.



## `fig_denomination_framings_per_synagogue_proportion.png`

Relative-mix version of `fig_denomination_framings_per_synagogue.png`: each denomination's bar is normalised to its OWN total framed-action volume (100% stacked) by the Implicit/Explicit/Embedded axis.

- **Embedded share of framed actions** — Kruskal–Wallis: H(8) = 79.6, p = 5.77e-14 (N = 1327); Dunn's post-hoc (BH): **6 of 36** pairs significant
- **Explicit share of framed actions** — Kruskal–Wallis: H(8) = 58.7, p = 8.24e-10 (N = 1327); Dunn's post-hoc (BH): **22 of 36** pairs significant


## `fig_framing.png`

Percentage of environmentally active synagogues with ≥1 action of each framing. Framings are NOT mutually exclusive, so the bars sum to more than 100%.

- n = **1353** environmentally active synagogues
- Note: framings are non-exclusive; percentages sum to > 100%

| framing | n with ≥1 | % active | mean chunks/active | total |
|---|---:|---:|---:|---:|
| Implicit | 833 | 61.6% | 3.335 | 4512 |
| Explicit | 783 | 57.9% | 3.307 | 4475 |
| Embedded | 980 | 72.4% | 3.381 | 4574 |


## `fig_framing_categories.png`

Category composition of environmental action by framing.

- n = **1353**
- **Friedman test** across the 3 framings: χ²(2) = 53.8, p = 2.03e-12


## `fig_framing_categories_by_party.png`

Category composition by framing, split by congressional-district party. Each panel is normalised by its own party's env-active count.

- **Dem** districts: n = **953** environmentally active synagogues
- **Rep** districts: n = **328** environmentally active synagogues


## `fig_denomination_categories_by_party.png`

Category composition of action by DENOMINATION (x-axis), split by congressional-district party — the denomination analogue of `fig_framing_categories_by_party.png`. Each bar is normalised by that party+denomination's own environmentally-active count.

- **Dem** districts: n = **936** environmentally active synagogues across 9 denominations
- **Rep** districts: n = **320** environmentally active synagogues across 9 denominations


## `fig_framing_categories_per_synagogue.png`

Same category composition by framing as `fig_framing_categories.png`, renormalised over ALL active-denominator synagogues (incl. zero-action).

- Population: all active-denominator synagogues, not just environmentally active ones.
- **Friedman test** across the 3 framings: χ²(2) = 53.8, p = 2.03e-12 (n = 2657)


## `fig_framing_categories_by_party_per_synagogue.png`

Same two-panel framing chart as `fig_framing_categories_by_party.png`, renormalised over all active-denominator synagogues per party.

- Population: all active-denominator synagogues, not just environmentally active ones.
- **Mann–Whitney U** (Dem vs Rep), one test per framing, Bonferroni-corrected across the 3 — not pooled into a single Kruskal-Wallis/Dunn's omnibus, since framings are not mutually exclusive (the same synagogue contributes to all three, so only the cross-party comparison within a single framing is between independent groups):
  - Implicit: Dem mean 1.72 (n=1856) vs Rep mean 1.71 (n=680), U = 669265.5, p(Bonf) = 0.0128 **(significant)**
  - Explicit: Dem mean 1.78 (n=1856) vs Rep mean 1.05 (n=680), U = 678466.5, p(Bonf) = 0.0009 **(significant)**
  - Embedded: Dem mean 1.71 (n=1856) vs Rep mean 1.44 (n=680), U = 662985.5, p(Bonf) = 0.0689


## `fig_denomination_categories_by_party_per_synagogue.png`

Same two-panel denomination chart as `fig_denomination_categories_by_party.png`, renormalised over all active-denominator synagogues per party+denomination.

- Population: all active-denominator synagogues, not just environmentally active ones — not separately tested.


## `fig_state_meanactions.png`

Distribution of action chunks per environmentally active synagogue across four states (Dem-leaning CA, NY vs Rep-leaning TX, FL).

- **Kruskal–Wallis** across the 4 states: H(3) = 2.1, p = 0.560
- Pairwise Mann–Whitney with Bonferroni correction:
  - CA vs NY: means 10.33 vs 9.21, p(Bonf) = 1.0000
  - CA vs TX: means 10.33 vs 12.18, p(Bonf) = 1.0000
  - CA vs FL: means 10.33 vs 7.39, p(Bonf) = 1.0000
  - NY vs TX: means 9.21 vs 12.18, p(Bonf) = 1.0000
  - NY vs FL: means 9.21 vs 7.39, p(Bonf) = 1.0000
  - TX vs FL: means 12.18 vs 7.39, p(Bonf) = 1.0000

| state | lean | n total | n env-active | mean chunks/active |
|---|---|---:|---:|---:|
| CA | Dem | 354 | 184 | 10.33 |
| NY | Dem | 567 | 241 | 9.21 |
| TX | Rep | 91 | 40 | 12.18 |
| FL | Rep | 219 | 96 | 7.39 |


## `fig_state_categories.png`

California vs Florida, action frequency by category. Stars on the bars mark significance: *** p<.001, ** p<.01, * p<.05 — all Bonferroni-corrected across the 9 categories.

| category | CA mean | FL mean | p (raw) | p (Bonferroni) |
|---|---:|---:|---:|---:|
| Community | 2.728 | 2.198 | 0.0539 | 0.4849 |
| Spirituality & Worship | 5.429 | 4.198 | 0.5891 | 1.0000 |
| Kitchen | 0.957 | 0.24 | 0.0216 | 0.1945 |
| Waste | 0.272 | 0.344 | 0.2466 | 1.0000 |
| Energy | 0.158 | 0.01 | 0.0149 | 0.1338 |
| Operations & Maintenance | 0.179 | 0.323 | 0.3377 | 1.0000 |
| Environmental & Climate Justice | 0.549 | 0.042 | 0.0353 | 0.3177 |
| Water | 0.033 | 0.01 | 0.9687 | 1.0000 |
| Other | 0.022 | 0.021 | 0.7024 | 1.0000 |


## `fig_district_lean_meanactions.png`

Action chunks per environmentally active synagogue by congressional-district lean (general-election winner).

- Election year: 2024
- **Mann–Whitney U** (Dem vs Rep): U = 178601.5, p = 1.00e-04
- Dem: n = 1856 (953 env-active), mean 10.16 ± 15.04
- Rep: n = 680 (328 env-active), mean 8.7 ± 15.97


## `fig_state_meanactions_per_synagogue.png`

Same four-state distribution as `fig_state_meanactions.png`, but taken over ALL synagogues in each state (incl. zero-action), not just environmentally active ones.

- Population: all active-denominator synagogues per state, not just environmentally active ones.
- **Kruskal–Wallis** across the 4 states: H(3) = 9.4, p = 0.024
- Pairwise Mann–Whitney with Bonferroni correction:
  - CA vs NY: means 5.37 vs 3.92, p(Bonf) = 0.0257 **(significant)**
  - CA vs FL: means 5.37 vs 3.24, p(Bonf) = 0.1448
  - CA vs TX: means 5.37 vs 5.35, p(Bonf) = 0.8064
  - NY vs TX: means 3.92 vs 5.35, p(Bonf) = 1.0000
  - NY vs FL: means 3.92 vs 3.24, p(Bonf) = 1.0000
  - TX vs FL: means 5.35 vs 3.24, p(Bonf) = 1.0000

| state | lean | n total | mean chunks/synagogue |
|---|---|---:|---:|
| CA | Dem | 354 | 5.37 |
| NY | Dem | 567 | 3.92 |
| TX | Rep | 91 | 5.35 |
| FL | Rep | 219 | 3.24 |


## `fig_state_categories_per_synagogue.png`

Same CA vs FL category comparison as `fig_state_categories.png`, renormalised over all synagogues in each state.

- Population: all active-denominator synagogues per state, not just environmentally active ones.
| category | CA mean | FL mean | p (raw) | p (Bonferroni) |
|---|---:|---:|---:|---:|
| Community | 1.418 | 0.963 | 0.0128 | 0.1155 |
| Spirituality & Worship | 2.822 | 1.84 | 0.1277 | 1.0000 |
| Kitchen | 0.497 | 0.105 | 0.0079 | 0.0713 |
| Waste | 0.141 | 0.151 | 0.5187 | 1.0000 |
| Energy | 0.082 | 0.005 | 0.0076 | 0.0682 |
| Operations & Maintenance | 0.093 | 0.142 | 0.6946 | 1.0000 |
| Environmental & Climate Justice | 0.285 | 0.018 | 0.0174 | 0.1565 |
| Water | 0.017 | 0.005 | 0.8599 | 1.0000 |
| Other | 0.011 | 0.009 | 0.5891 | 1.0000 |


## `fig_district_lean_meanactions_per_synagogue.png`

Same district-lean distribution as `fig_district_lean_meanactions.png`, but taken over ALL synagogues (incl. zero-action) rather than only environmentally active ones.

- **Mann–Whitney U** (Dem vs Rep, all synagogues): U = 672985.5, p = 0.006
- Dem: n = 1856, mean 5.21 ± 11.91
- Rep: n = 680, mean 4.2 ± 11.91


## `fig_cluster_silhouette.png`

k-means model selection. Panels: Silhouette (higher is better), Davies-Bouldin (lower is better), Inertia (elbow).

- n = **1353** environmentally active synagogues
- Chosen **k = 2**, silhouette = 0.630 (threshold 0.25) → **meaningful** cluster structure
- Best silhouette 0.630 at k=2 (above the 0.25 threshold) → meaningful cluster structure. Synagogues separate into distinct engagement clusters.


## `fig_cluster_scatter.png`

Environmentally active synagogues in PCA feature space, coloured by cluster.

- n = **1353** environmentally active synagogues
- Chosen **k = 2**, silhouette = 0.630 (threshold 0.25) → **meaningful** cluster structure
- Best silhouette 0.630 at k=2 (above the 0.25 threshold) → meaningful cluster structure. Synagogues separate into distinct engagement clusters.


## `fig_cluster_profiles.png`

Category composition of each engagement cluster.

- n = **1353** environmentally active synagogues
- Chosen **k = 2**, silhouette = 0.630 (threshold 0.25) → **meaningful** cluster structure
- Best silhouette 0.630 at k=2 (above the 0.25 threshold) → meaningful cluster structure. Synagogues separate into distinct engagement clusters.


## `fig_keyword_hitrate.png`

Keyword precision: hit rate (% of a keyword's occurrences classified as a real action) vs total occurrences, one point per matched keyword. Structurally keyword_chunks-only — "keyword" has no page_chunks equivalent, so this figure is not regenerated per source.

- **1782** distinct keywords, **262367** total occurrences, **29650** classified as a real action
- Pooled hit rate = **11.3%**; median per-keyword hit rate = **0.7%**


## `action_logit_table.png`

Logistic regression: P(≥1 environmental action) predicted from distance to 7 OSM site types. Significance column: *** p<.001, ** p<.01, * p<.05, . p<.10.

- n = **2555** synagogues (1289 with ≥1 action, 50.5%)
- **Likelihood-ratio test** vs intercept-only: χ²(7) = 17.16, p = 0.016
- McFadden pseudo-R² = 0.0048
- 5-fold CV accuracy = 0.522 (majority baseline 0.504)
- ROC AUC = 0.526 (chance 0.50)


## `action_logit_distance_distributions.png`

Distance distribution split by outcome, one panel per predictor. Dashed lines are group medians. Panel titles give only the site type; the per-predictor odds ratios are below.

| site type | OR per +1 SD | p |
|---|---:|---:|
| ? | 1.020 | 0.6094 |
| ? | 0.029 | 0.0083 |
| ? | 7.869 | 0.0061 |
| ? | 1.017 | 0.9467 |
| ? | 2.784 | 0.4003 |
| ? | 0.952 | 0.2804 |
| ? | 0.656 | 0.0717 |
| ? | 2.481 | 0.1069 |


## `action_distance_cornerplot.png`

Lower-triangle scatter matrix of the 7 OSM distances, coloured by log(1 + action count). Spearman correlations of action count against each distance:

- n = **2555** synagogues; Bonferroni over 7 tests

| site type | Spearman rho | p (raw) | p (Bonferroni) | median km |
|---|---:|---:|---:|---:|
| green_space * | -0.0537 | 0.0066 | 0.0461 | 0.16 |
| env_hazard * | 0.0588 | 0.0029 | 0.0205 | 2.27 |
| landfill | -0.0093 | 0.6372 | 1.0000 | 10.29 |
| industrial | 0.0182 | 0.3572 | 1.0000 | 1.43 |
| abattoir * | 0.0593 | 0.0027 | 0.0190 | 65.39 |
| military | -0.0240 | 0.2247 | 1.0000 | 6.92 |
| airport | 0.0379 | 0.0554 | 0.3881 | 6.69 |


## `action_political_leesL_map.png`

Local Lee's L — spatial co-patterning of Democratic lean and environmental action count. Orange = positive co-cluster, purple = negative. CONUS only.

- n = **2322** districts, k = 8 neighbours, 9999 permutations (2024)
- **Lee's L = 0.0459**, z = 2.415, two-sided p = 0.015
- Aspatial Pearson r = 0.0470 (p = 0.023)
- Moran's I — lean 0.7399, actions 0.0197
- H0: no bivariate spatial association (Lee's L = 0)


## `rd_action_means.png`

Mean environmental actions per synagogue by congressional-district party (bars are mean ± 1 SD; labels give both).

- **Democrat**: n = 3465, mean = 2.793073593073593, SD = 9.093876819241103
- **Republican**: n = 1269, mean = 2.2498029944838454, SD = 8.956853605900479
- **Mann–Whitney U**: U = None, n/a, rank-biserial r = -0.026712242315079182


## `action_vs_dem_district` (no figure)

- n = **2542** synagogues (2024)
- Odds ratio (Dem vs Rep) = 1.129, 95% CI [0.948, 1.346]
- χ² = 1.849, p = 0.174, φ = 0.027


## `county_action_map.png`

County-level choropleth. Each county is filled by the raw percentage of its crawled synagogues with ≥1 environmental action; counties with no crawled synagogue are pale grey. The fill does **not** account for sample size — see the counts below before reading any single county.

- **521** counties, **2555** crawled synagogues placed
- Sample sizes are thin: **244** counties have exactly 1 synagogue, only **104** have ≥5 (max 172). A fraction from n=1 is only ever 0% or 100% — which is why this is a bubble map and not a choropleth.
- **108** crawled synagogues are not shown: they have no `census_tract_geoid`, and all of them lack coordinates entirely, so no method could place them.


## `district_action_map.png`

Congressional-district choropleth (119th Congress), filled by the raw percentage of each district's crawled synagogues with ≥1 environmental action. Districts are drawn to roughly equal population, so the sample per unit is far more even than counties — this is the best-supported of the three geographies. Districts keep their true shape and size.

- **383** districts, **2555** crawled synagogues placed
- **73** districts rest on a single synagogue and **161** have ≥5 (max 51) — compare the county map, where 243 of 522 units have n=1.
- **108** crawled synagogues have no district assigned and are not shown.


## `district_action_cartogram.png`

Congressional-district choropleth (119th Congress), filled by the raw percentage of each district's crawled synagogues with ≥1 environmental action. Districts are drawn to roughly equal population, so the sample per unit is far more even than counties — this is the best-supported of the three geographies. Each district is additionally scaled about its own centroid by sqrt(n / n_max), so a district's drawn size reflects how many crawled synagogues back its colour; true outlines are shown in pale grey underneath.

- **383** districts, **2555** crawled synagogues placed
- **73** districts rest on a single synagogue and **161** have ≥5 (max 51) — compare the county map, where 243 of 522 units have n=1.
- **108** crawled synagogues have no district assigned and are not shown.


## `dense_area_bubble_map.png`

Two resolutions at once: a bubble wherever synagogue dots would visually overlap at the rendered scale (area = synagogue count, fill = % with ≥1 action; clusters found dynamically via DBSCAN, not a fixed city list), and one dot per synagogue left over (colour = took ≥1 action or not). See KNOWN_DATA_ISSUES.md for why the clustering is scale-dependent and this figure's network-access requirement.

- **2555** crawled synagogues placed into **75** bubbles (**2269** synagogues) plus **286** individual dots (`cluster_eps_px=10`, `min_cluster_size=3`, `fit_zoom_used=5`)
  - n=1007 @ (41.0209, -73.6372): 47.8% with ≥1 action
  - n=251 @ (33.9303, -118.1876): 48.2% with ≥1 action
  - n=126 @ (26.2053, -80.1811): 36.5% with ≥1 action
  - n=106 @ (39.1423, -76.9579): 55.7% with ≥1 action
  - n=78 @ (42.0171, -87.7976): 67.9% with ≥1 action
  - n=76 @ (37.8427, -122.1156): 64.5% with ≥1 action
  - n=36 @ (41.4185, -81.5664): 44.4% with ≥1 action
  - n=35 @ (47.6075, -122.3385): 60.0% with ≥1 action
  - n=30 @ (33.5267, -111.9921): 53.3% with ≥1 action
  - n=30 @ (29.3563, -81.5604): 43.3% with ≥1 action
  - n=28 @ (29.7463, -95.3965): 42.9% with ≥1 action
  - n=25 @ (27.7797, -82.4151): 56.0% with ≥1 action
  - n=23 @ (42.4596, -83.2552): 56.5% with ≥1 action
  - n=23 @ (32.9269, -96.9032): 47.8% with ≥1 action
  - n=20 @ (33.8842, -84.3776): 70.0% with ≥1 action
  - ... and 60 more bubbles
- Outside dots: **45.8%** (131/286) with ≥1 action

