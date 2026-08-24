# Figure statistics

Statistics for the figures in `11_analysis/`. The figures carry no titles and no in-plot test annotations; every test reported here is what used to be drawn on the figure itself.

- **Action-chunk source:** `keyword_chunks`
- **Generated:** 2026-08-18 by `11_analysis/generate_figure_statistics.py`
- **Regenerate with the figures** — these numbers are read from the analysis JSONs, so a stale run here means stale captions.

## Population

- Active denominator (≥1 successfully crawled page): **2664** synagogues
- Environmentally active (≥1 action): **1200** (45.0% ± 1.9 95% CI)
- Total action chunks: **16615**
- Mean per environmentally active synagogue: 13.85 ± 1.3 (95% CI)

Outlier exclusion (|z| > 3 on log1p action count) is applied throughout — see `KNOWN_DATA_ISSUES.md`.


## `fig_categories.png`

Distribution of action chunks per environmentally active synagogue, by category (box-and-whisker; ♦ marks the mean, which sits well above the median because the counts are heavily right-skewed).

- n = **1200** environmentally active synagogues
- **Friedman test** across the 9 categories: χ²(8) = 4526.8, p < 1e-300 (n = 1200)
- Pairwise Wilcoxon with Bonferroni correction: **29 of 36** pairs significant at 0.05

| category | mean chunks/active | SD | % with ≥1 | total chunks |
|---|---:|---:|---:|---:|
| Spirituality & Worship | 6.591 | 14.287 | 78.6% | 7909 |
| Community | 4.379 | 10.645 | 65.9% | 5255 |
| Kitchen | 0.938 | 4.282 | 22.9% | 1125 |
| Energy | 0.601 | 5.411 | 10.4% | 721 |
| Operations & Maintenance | 0.449 | 2.574 | 12.4% | 539 |
| Waste | 0.43 | 1.739 | 13.5% | 516 |
| Environmental & Climate Justice | 0.362 | 2.402 | 9.4% | 435 |
| Other | 0.049 | 0.316 | 3.2% | 59 |
| Water | 0.047 | 0.387 | 2.2% | 56 |


## `fig_categories_pct.png`

Percentage of environmentally active synagogues with ≥1 action in each category.

- n = **1200** environmentally active synagogues (denominator for every bar)
- Error bars are ±1 binomial SD; per-category values are in the table above.


## `fig_categories_per_synagogue.png`

Same distribution as `fig_categories.png`, but taken over ALL active-denominator synagogues (incl. zero-action), not just environmentally active ones — the y-axis is a plain mean action count per synagogue rather than per env-active synagogue.

- Population: all active-denominator synagogues; per-category means/SDs are the same underlying counts as `fig_categories.png` renormalised over the larger population.
- **Friedman test** across the 9 categories: χ²(8) = 4526.8, p < 1e-300 (n = 2664)
- Pairwise Wilcoxon with Bonferroni correction: **29 of 36** pairs significant at 0.05


## `fig_denomination_meanactions.png`

Distribution of action chunks per environmentally active synagogue, by denomination (ordered by median).

- **Kruskal–Wallis** across denominations: H(8) = 64.4, p = 6.48e-11 (9 groups, N = 1180)
- Significant pairwise differences (Mann–Whitney, Bonferroni): **9**
  - Reform vs Orthodox: means 17.70 vs 6.02, p(Bonf) = 0.0000
  - Reconstructionist vs Orthodox: means 23.54 vs 6.02, p(Bonf) = 0.0000
  - Conservative vs Orthodox: means 14.25 vs 6.02, p(Bonf) = 0.0000
  - Reform vs Chabad: means 17.70 vs 8.37, p(Bonf) = 0.0001
  - Jewish Renewal vs Orthodox: means 28.67 vs 6.02, p(Bonf) = 0.0017
  - Non-Denominational Progressive vs Orthodox: means 15.58 vs 6.02, p(Bonf) = 0.0064
  - Orthodox vs Chabad: means 6.02 vs 8.37, p(Bonf) = 0.0086
  - Reconstructionist vs Chabad: means 23.54 vs 8.37, p(Bonf) = 0.0344
  - Jewish Renewal vs Chabad: means 28.67 vs 8.37, p(Bonf) = 0.0348


## `fig_denomination_pct.png`

Percentage of synagogues in each denomination that took ≥1 environmental action. Bar labels carry the denominator n.

| denomination | n total | n env-active | % active | ±1 SD |
|---|---:|---:|---:|---:|
| Jewish Renewal | 25 | 18 | 72.0% | 9.0 |
| Reconstructionist | 66 | 48 | 72.7% | 5.5 |
| Humanistic | 22 | 13 | 59.1% | 10.5 |
| Reform | 557 | 341 | 61.2% | 2.1 |
| Non-Denominational Progressive | 185 | 106 | 57.3% | 3.6 |
| Conservative | 405 | 220 | 54.3% | 2.5 |
| Non-Denominational Conservative | 27 | 12 | 44.4% | 9.6 |
| Orthodox | 582 | 92 | 15.8% | 1.5 |
| Chabad | 721 | 330 | 45.8% | 1.9 |


## `fig_denomination_categories.png`

Category composition of environmental action by denomination (mean chunks per environmentally active synagogue, stacked).


## `fig_denomination_framings.png`

Framing composition (Implicit / Explicit / Embedded) by denomination.


## `fig_denomination_by_party.png`

Denominational makeup of synagogues in Democratic vs Republican congressional districts. Panels are labelled by party; the per-panel sample sizes are below.

- **Dem** districts: n = **1817** synagogues
- **Rep** districts: n = **653** synagogues


## `fig_denomination_meanactions_per_synagogue.png`

Same distribution as `fig_denomination_meanactions.png`, but taken over ALL synagogues of each denomination (incl. zero-action), not just environmentally active ones.

- Population: all active-denominator synagogues per denomination (`n_total` in the table above), not just environmentally active ones.
- **Kruskal–Wallis** across denominations: H(8) = 349.4, p = 1.24e-70 (9 groups, N = 2590)
- Significant pairwise differences (Dunn's post-hoc, Benjamini-Hochberg): **21**
  - Reform vs Orthodox: means 10.84 vs 0.95, p(adj) = 0.0000
  - Conservative vs Orthodox: means 7.74 vs 0.95, p(adj) = 0.0000
  - Orthodox vs Chabad: means 0.95 vs 3.83, p(adj) = 0.0000
  - Non-Denominational Progressive vs Orthodox: means 8.93 vs 0.95, p(adj) = 0.0000
  - Reconstructionist vs Orthodox: means 17.12 vs 0.95, p(adj) = 0.0000
  - Reform vs Chabad: means 10.84 vs 3.83, p(adj) = 0.0000
  - Jewish Renewal vs Orthodox: means 20.64 vs 0.95, p(adj) = 0.0000
  - Reconstructionist vs Chabad: means 17.12 vs 3.83, p(adj) = 0.0000
  - Humanistic vs Orthodox: means 9.59 vs 0.95, p(adj) = 0.0000
  - Jewish Renewal vs Chabad: means 20.64 vs 3.83, p(adj) = 0.0008


## `fig_denomination_categories_per_synagogue.png`

Same stacked category composition as `fig_denomination_categories.png`, renormalised over all synagogues of each denomination.

- Population: all active-denominator synagogues per denomination (`n_total` in the table above), not just environmentally active ones — not separately tested.


## `fig_denomination_framings_per_synagogue.png`

Same framing composition as `fig_denomination_framings.png`, renormalised over all synagogues of each denomination.

- Population: all active-denominator synagogues per denomination (`n_total` in the table above), not just environmentally active ones — not separately tested.


## `fig_denomination_categories_per_synagogue_proportion.png`

Relative-mix version of `fig_denomination_categories_per_synagogue.png`: each denomination's bar is normalised to its OWN total (100% stacked), showing category *share* rather than absolute mean action count.

- **Kruskal–Wallis** on each synagogue's Spirituality & Worship share of its own action total, across 9 denominations: H(8) = 76.5, p = 2.51e-13 (N = 1180)
- **Dunn's post-hoc** (Benjamini-Hochberg-corrected): **10 of 36** pairs significant at 0.05


## `fig_denomination_categories_per_synagogue_pie.png`

Same relative-mix data as the proportion figure above, as one pie chart per denomination instead of a single 100%-stacked bar. See `fig_denomination_categories_per_synagogue_proportion.png` above for the underlying test.



## `fig_denomination_framings_per_synagogue_proportion.png`

Relative-mix version of `fig_denomination_framings_per_synagogue.png`: each denomination's bar is normalised to its OWN total framed-action volume (100% stacked) by the Implicit/Explicit/Embedded axis.

- **Embedded share of framed actions** — Kruskal–Wallis: H(8) = 77.8, p = 1.38e-13 (N = 1180); Dunn's post-hoc (BH): **7 of 36** pairs significant
- **Explicit share of framed actions** — Kruskal–Wallis: H(8) = 50.3, p = 3.54e-08 (N = 1180); Dunn's post-hoc (BH): **18 of 36** pairs significant


## `fig_framing.png`

Percentage of environmentally active synagogues with ≥1 action of each framing. Framings are NOT mutually exclusive, so the bars sum to more than 100%.

- n = **1200** environmentally active synagogues
- Note: framings are non-exclusive; percentages sum to > 100%

| framing | n with ≥1 | % active | mean chunks/active | total |
|---|---:|---:|---:|---:|
| Implicit | 830 | 69.2% | 5.423 | 6507 |
| Explicit | 742 | 61.8% | 4.479 | 5375 |
| Embedded | 808 | 67.3% | 3.944 | 4733 |


## `fig_framing_categories.png`

Category composition of environmental action by framing.

- n = **1200**
- **Friedman test** across the 3 framings: χ²(2) = 17.6, p = 1.53e-04


## `fig_framing_categories_by_party.png`

Category composition by framing, split by congressional-district party. Each panel is normalised by its own party's env-active count.

- **Dem** districts: n = **852** environmentally active synagogues
- **Rep** districts: n = **282** environmentally active synagogues


## `fig_denomination_categories_by_party.png`

Category composition of action by DENOMINATION (x-axis), split by congressional-district party — the denomination analogue of `fig_framing_categories_by_party.png`. Each bar is normalised by that party+denomination's own environmentally-active count.

- **Dem** districts: n = **839** environmentally active synagogues across 9 denominations
- **Rep** districts: n = **276** environmentally active synagogues across 9 denominations


## `fig_framing_categories_per_synagogue.png`

Same category composition by framing as `fig_framing_categories.png`, renormalised over ALL active-denominator synagogues (incl. zero-action).

- Population: all active-denominator synagogues, not just environmentally active ones.
- **Friedman test** across the 3 framings: χ²(2) = 17.6, p = 1.53e-04 (n = 2664)


## `fig_framing_categories_by_party_per_synagogue.png`

Same two-panel framing chart as `fig_framing_categories_by_party.png`, renormalised over all active-denominator synagogues per party.

- Population: all active-denominator synagogues, not just environmentally active ones.
- **Mann–Whitney U** (Dem vs Rep), one test per framing, Bonferroni-corrected across the 3 — not pooled into a single Kruskal-Wallis/Dunn's omnibus, since framings are not mutually exclusive (the same synagogue contributes to all three, so only the cross-party comparison within a single framing is between independent groups):
  - Implicit: Dem mean 2.70 (n=1864) vs Rep mean 1.80 (n=679), U = 675399.5, p(Bonf) = 0.0043 **(significant)**
  - Explicit: Dem mean 2.25 (n=1864) vs Rep mean 1.00 (n=679), U = 697652.0, p(Bonf) = 0.0000 **(significant)**
  - Embedded: Dem mean 1.70 (n=1864) vs Rep mean 1.75 (n=679), U = 651385.0, p(Bonf) = 0.4892


## `fig_denomination_categories_by_party_per_synagogue.png`

Same two-panel denomination chart as `fig_denomination_categories_by_party.png`, renormalised over all active-denominator synagogues per party+denomination.

- Population: all active-denominator synagogues, not just environmentally active ones — not separately tested.


## `fig_state_meanactions.png`

Distribution of action chunks per environmentally active synagogue across four states (Dem-leaning CA, NY vs Rep-leaning TX, FL).

- **Kruskal–Wallis** across the 4 states: H(3) = 8.4, p = 0.038
- Pairwise Mann–Whitney with Bonferroni correction:
  - CA vs FL: means 17.01 vs 9.46, p(Bonf) = 0.0358 **(significant)**
  - NY vs FL: means 13.21 vs 9.46, p(Bonf) = 0.6720
  - CA vs NY: means 17.01 vs 13.21, p(Bonf) = 0.6851
  - CA vs TX: means 17.01 vs 14.45, p(Bonf) = 0.7669
  - NY vs TX: means 13.21 vs 14.45, p(Bonf) = 1.0000
  - TX vs FL: means 14.45 vs 9.46, p(Bonf) = 1.0000

| state | lean | n total | n env-active | mean chunks/active |
|---|---|---:|---:|---:|
| CA | Dem | 357 | 167 | 17.01 |
| NY | Dem | 569 | 213 | 13.21 |
| TX | Rep | 91 | 33 | 14.45 |
| FL | Rep | 219 | 87 | 9.46 |


## `fig_state_categories.png`

California vs Florida, action frequency by category. Stars on the bars mark significance: *** p<.001, ** p<.01, * p<.05 — all Bonferroni-corrected across the 9 categories.

| category | CA mean | FL mean | p (raw) | p (Bonferroni) |
|---|---:|---:|---:|---:|
| Community | 5.012 | 1.885 | 0.0048 | 0.0435 |
| Spirituality & Worship | 8.377 | 6.598 | 0.1996 | 1.0000 |
| Kitchen | 1.108 | 0.23 | 0.0556 | 0.5007 |
| Waste | 0.317 | 0.379 | 0.5579 | 1.0000 |
| Energy | 1.15 | 0.057 | 0.0325 | 0.2924 |
| Operations & Maintenance | 0.078 | 0.195 | 0.4582 | 1.0000 |
| Environmental & Climate Justice | 0.826 | 0.08 | 0.2005 | 1.0000 |
| Water | 0.072 | 0.023 | 0.5032 | 1.0000 |
| Other | 0.066 | 0.011 | 0.0993 | 0.8940 |


## `fig_district_lean_meanactions.png`

Action chunks per environmentally active synagogue by congressional-district lean (general-election winner).

- Election year: 2024
- **Mann–Whitney U** (Dem vs Rep): U = 139169.0, p = 5.90e-05
- Dem: n = 1864 (852 env-active), mean 14.56 ± 23.34
- Rep: n = 679 (282 env-active), mean 10.95 ± 19.95


## `fig_state_meanactions_per_synagogue.png`

Same four-state distribution as `fig_state_meanactions.png`, but taken over ALL synagogues in each state (incl. zero-action), not just environmentally active ones.

- Population: all active-denominator synagogues per state, not just environmentally active ones.
- **Kruskal–Wallis** across the 4 states: H(3) = 12.3, p = 0.006
- Pairwise Mann–Whitney with Bonferroni correction:
  - CA vs NY: means 7.96 vs 4.94, p(Bonf) = 0.0094 **(significant)**
  - CA vs FL: means 7.96 vs 3.76, p(Bonf) = 0.0908
  - CA vs TX: means 7.96 vs 5.24, p(Bonf) = 0.1848
  - NY vs TX: means 4.94 vs 5.24, p(Bonf) = 1.0000
  - NY vs FL: means 4.94 vs 3.76, p(Bonf) = 1.0000
  - TX vs FL: means 5.24 vs 3.76, p(Bonf) = 1.0000

| state | lean | n total | mean chunks/synagogue |
|---|---|---:|---:|
| CA | Dem | 357 | 7.96 |
| NY | Dem | 569 | 4.94 |
| TX | Rep | 91 | 5.24 |
| FL | Rep | 219 | 3.76 |


## `fig_state_categories_per_synagogue.png`

Same CA vs FL category comparison as `fig_state_categories.png`, renormalised over all synagogues in each state.

- Population: all active-denominator synagogues per state, not just environmentally active ones.
| category | CA mean | FL mean | p (raw) | p (Bonferroni) |
|---|---:|---:|---:|---:|
| Community | 2.345 | 0.749 | 0.0017 | 0.0155 |
| Spirituality & Worship | 3.919 | 2.621 | 0.0893 | 0.8038 |
| Kitchen | 0.518 | 0.091 | 0.0255 | 0.2292 |
| Waste | 0.148 | 0.151 | 0.8899 | 1.0000 |
| Energy | 0.538 | 0.023 | 0.0162 | 0.1454 |
| Operations & Maintenance | 0.036 | 0.078 | 0.6918 | 1.0000 |
| Environmental & Climate Justice | 0.387 | 0.032 | 0.1226 | 1.0000 |
| Water | 0.034 | 0.009 | 0.4067 | 1.0000 |
| Other | 0.031 | 0.005 | 0.0656 | 0.5907 |


## `fig_district_lean_meanactions_per_synagogue.png`

Same district-lean distribution as `fig_district_lean_meanactions.png`, but taken over ALL synagogues (incl. zero-action) rather than only environmentally active ones.

- **Mann–Whitney U** (Dem vs Rep, all synagogues): U = 678295.0, p = 0.002
- Dem: n = 1864, mean 6.65 ± 17.36
- Rep: n = 679, mean 4.55 ± 13.93


## `fig_cluster_silhouette.png`

k-means model selection. Panels: Silhouette (higher is better), Davies-Bouldin (lower is better), Inertia (elbow).

- n = **1200** environmentally active synagogues
- Chosen **k = 2**, silhouette = 0.654 (threshold 0.25) → **meaningful** cluster structure
- Best silhouette 0.654 at k=2 (above the 0.25 threshold) → meaningful cluster structure. Synagogues separate into distinct engagement clusters.


## `fig_cluster_scatter.png`

Environmentally active synagogues in PCA feature space, coloured by cluster.

- n = **1200** environmentally active synagogues
- Chosen **k = 2**, silhouette = 0.654 (threshold 0.25) → **meaningful** cluster structure
- Best silhouette 0.654 at k=2 (above the 0.25 threshold) → meaningful cluster structure. Synagogues separate into distinct engagement clusters.


## `fig_cluster_profiles.png`

Category composition of each engagement cluster.

- n = **1200** environmentally active synagogues
- Chosen **k = 2**, silhouette = 0.654 (threshold 0.25) → **meaningful** cluster structure
- Best silhouette 0.654 at k=2 (above the 0.25 threshold) → meaningful cluster structure. Synagogues separate into distinct engagement clusters.


## `fig_keyword_hitrate.png`

Keyword precision: hit rate (% of a keyword's occurrences classified as a real action) vs total occurrences, one point per matched keyword. Structurally keyword_chunks-only — "keyword" has no page_chunks equivalent, so this figure is not regenerated per source.

- **1782** distinct keywords, **262367** total occurrences, **29650** classified as a real action
- Pooled hit rate = **11.3%**; median per-keyword hit rate = **0.7%**


## `action_logit_table.png`

Logistic regression: P(≥1 environmental action) predicted from distance to 7 OSM site types. Significance column: *** p<.001, ** p<.01, * p<.05, . p<.10.

- n = **2561** synagogues (1140 with ≥1 action, 44.5%)
- **Likelihood-ratio test** vs intercept-only: χ²(7) = 13.38, p = 0.063
- McFadden pseudo-R² = 0.0038
- 5-fold CV accuracy = 0.554 (majority baseline 0.555)
- ROC AUC = 0.520 (chance 0.50)


## `action_logit_distance_distributions.png`

Distance distribution split by outcome, one panel per predictor. Dashed lines are group medians. Panel titles give only the site type; the per-predictor odds ratios are below.

| site type | OR per +1 SD | p |
|---|---:|---:|
| ? | 0.802 | 0.0000 |
| ? | 0.019 | 0.0029 |
| ? | 3.625 | 0.0401 |
| ? | 1.071 | 0.7872 |
| ? | 5.016 | 0.1854 |
| ? | 0.962 | 0.4000 |
| ? | 0.820 | 0.3950 |
| ? | 3.240 | 0.0370 |


## `action_distance_cornerplot.png`

Lower-triangle scatter matrix of the 7 OSM distances, coloured by log(1 + action count). Spearman correlations of action count against each distance:

- n = **2561** synagogues; Bonferroni over 7 tests

| site type | Spearman rho | p (raw) | p (Bonferroni) | median km |
|---|---:|---:|---:|---:|
| green_space | -0.0434 | 0.0282 | 0.1974 | 0.16 |
| env_hazard | 0.0474 | 0.0165 | 0.1152 | 2.27 |
| landfill | -0.0039 | 0.8441 | 1.0000 | 10.27 |
| industrial | 0.0296 | 0.1338 | 0.9369 | 1.43 |
| abattoir * | 0.0581 | 0.0033 | 0.0230 | 65.58 |
| military | -0.0105 | 0.5936 | 1.0000 | 6.92 |
| airport | 0.0452 | 0.0223 | 0.1559 | 6.69 |


## `action_political_leesL_map.png`

Local Lee's L — spatial co-patterning of Democratic lean and environmental action count. Orange = positive co-cluster, purple = negative. CONUS only.

- n = **2330** districts, k = 8 neighbours, 9999 permutations (2024)
- **Lee's L = 0.0485**, z = 2.538, two-sided p = 0.011
- Aspatial Pearson r = 0.0582 (p = 0.005)
- Moran's I — lean 0.7394, actions 0.0116
- H0: no bivariate spatial association (Lee's L = 0)


## `rd_action_means.png`

Mean environmental actions per synagogue by congressional-district party (bars are mean ± 1 SD; labels give both).

- **Democrat**: n = 3473, mean = 3.570976101353297, SD = 13.143702359548023
- **Republican**: n = 1268, mean = 2.434542586750789, SD = 10.436739244256453
- **Mann–Whitney U**: U = None, n/a, rank-biserial r = -0.0315693574860052


## `action_vs_dem_district` (no figure)

- n = **2549** synagogues (2024)
- Odds ratio (Dem vs Rep) = 1.182, 95% CI [0.99, 1.411]
- χ² = 3.419, p = 0.064, φ = 0.0366


## `county_action_map.png`

County-level choropleth. Each county is filled by the raw percentage of its crawled synagogues with ≥1 environmental action; counties with no crawled synagogue are pale grey. The fill does **not** account for sample size — see the counts below before reading any single county.

- **522** counties, **2561** crawled synagogues placed
- Sample sizes are thin: **243** counties have exactly 1 synagogue, only **104** have ≥5 (max 174). A fraction from n=1 is only ever 0% or 100% — which is why this is a bubble map and not a choropleth.
- **109** crawled synagogues are not shown: they have no `census_tract_geoid`, and all of them lack coordinates entirely, so no method could place them.


## `district_action_map.png`

Congressional-district choropleth (119th Congress), filled by the raw percentage of each district's crawled synagogues with ≥1 environmental action. Districts are drawn to roughly equal population, so the sample per unit is far more even than counties — this is the best-supported of the three geographies. Districts keep their true shape and size.

- **384** districts, **2561** crawled synagogues placed
- **74** districts rest on a single synagogue and **161** have ≥5 (max 52) — compare the county map, where 243 of 522 units have n=1.
- **109** crawled synagogues have no district assigned and are not shown.


## `district_action_cartogram.png`

Congressional-district choropleth (119th Congress), filled by the raw percentage of each district's crawled synagogues with ≥1 environmental action. Districts are drawn to roughly equal population, so the sample per unit is far more even than counties — this is the best-supported of the three geographies. Each district is additionally scaled about its own centroid by sqrt(n / n_max), so a district's drawn size reflects how many crawled synagogues back its colour; true outlines are shown in pale grey underneath.

- **384** districts, **2561** crawled synagogues placed
- **74** districts rest on a single synagogue and **161** have ≥5 (max 52) — compare the county map, where 243 of 522 units have n=1.
- **109** crawled synagogues have no district assigned and are not shown.


## `dense_area_bubble_map.png`

Two resolutions at once: a bubble wherever synagogue dots would visually overlap at the rendered scale (area = synagogue count, fill = % with ≥1 action; clusters found dynamically via DBSCAN, not a fixed city list), and one dot per synagogue left over (colour = took ≥1 action or not). See KNOWN_DATA_ISSUES.md for why the clustering is scale-dependent and this figure's network-access requirement.

- **2561** crawled synagogues placed into **75** bubbles (**2275** synagogues) plus **286** individual dots (`cluster_eps_px=10`, `min_cluster_size=3`, `fit_zoom_used=5`)
  - n=1009 @ (41.0235, -73.6365): 41.5% with ≥1 action
  - n=255 @ (33.9292, -118.1887): 43.9% with ≥1 action
  - n=127 @ (26.2164, -80.183): 30.7% with ≥1 action
  - n=106 @ (39.1457, -76.9539): 49.1% with ≥1 action
  - n=78 @ (42.0171, -87.7976): 65.4% with ≥1 action
  - n=75 @ (37.843, -122.1139): 58.7% with ≥1 action
  - n=36 @ (41.4185, -81.5664): 33.3% with ≥1 action
  - n=34 @ (47.6046, -122.3422): 58.8% with ≥1 action
  - n=30 @ (33.5267, -111.9921): 46.7% with ≥1 action
  - n=30 @ (29.3563, -81.5604): 43.3% with ≥1 action
  - n=28 @ (29.7463, -95.3965): 28.6% with ≥1 action
  - n=25 @ (27.7797, -82.4151): 56.0% with ≥1 action
  - n=23 @ (42.4596, -83.2552): 43.5% with ≥1 action
  - n=23 @ (32.9269, -96.9032): 34.8% with ≥1 action
  - n=21 @ (33.8854, -84.3795): 66.7% with ≥1 action
  - ... and 60 more bubbles
- Outside dots: **40.9%** (117/286) with ≥1 action

