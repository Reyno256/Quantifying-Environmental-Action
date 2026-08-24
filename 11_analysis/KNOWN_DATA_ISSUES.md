# Known data issues — `11_analysis/`

> **Status note (2026-08-07).** The `synagogue_id = 421` narrative below is
> **stale**. That record now has **0 action chunks in both classification
> tables**, so it is no longer an outlier and is no longer excluded by either
> source — the classification tables have been re-populated since this section
> was written. The Mardia result was computed on n=2,732 active synagogues;
> the active count today is 2,702.

> **Status note (2026-08-17).** The outlier-exclusion mechanism was
> experimentally replaced with a nonparametric IQR (Tukey) fence several
> times today — raw-scale at 1.5x and 3.0x, then log1p-scale at 3.0x and
> 1.5x — then **reverted back to the original z-score method**, which is
> what actually runs now (`OUTLIER_Z_THRESH = 3.0` in
> `recreate_existing/recreate_common.py`, unchanged from before today). See
> "IQR (Tukey fence) exploration, 2026-08-17 (reverted)" below for the full
> history and numbers, kept for the record. One permanent change *did* stick
> from today's work: the post-hoc test behind
> `fig_denomination_meanactions_per_synagogue.png` changed from pairwise
> Mann-Whitney U (Bonferroni) to Dunn's test (Benjamini-Hochberg) — see
> `analysis_denomination.py`. That change is independent of the outlier
> method and applies on top of the (reverted-to-z-score) population.

## Outlier removal: z-score on log1p(action count)

Action counts per synagogue are heavily right-skewed (many zeros, a long
tail of very active synagogues), and at least one record —
`synagogue_id = 421` ("Nature Conservancy", NM) — was a mis-scraped
non-synagogue entity with an extreme count (1,615 chunks vs a mean of ~24
across environmentally-active synagogues) that distorted means, CIs, and
clustering. *(See the 2026-08-07 status note above: this no longer holds.)*

**Outliers are removed automatically and centrally**, not via a hand-picked
id list: `recreate_existing/recreate_common.py::find_outlier_synagogues()`
log1p-transforms each active synagogue's action count and z-scores it;
`get_excluded_synagogue_ids()` (cached per-process) returns the ids with
`|zscore| > OUTLIER_Z_THRESH` (default 3.0). At the current threshold this
flags ~41 synagogues, not just id 421 — most of the rest are genuinely very
active synagogues caught by the same statistical cut, not data errors. This
is a known trade-off of the blunt z-score approach; re-run
`find_outlier_synagogues()` and inspect the `name` column before changing
`OUTLIER_Z_THRESH` if that trade-off ever needs revisiting. (A nonparametric
IQR-fence alternative was tried and reverted today — see the status note
above and the exploration section below.)

**Mardia's test confirms log1p(action count) is not normal even after the
transform.** Run on the full active-synagogue vector (n=2,732, p=1):
skewness b1,p = 2.267 (χ² = 1032.2, df = 1, p ≈ 0) and kurtosis b2,p = 4.749
vs. the Gaussian expectation of 3.0 (Z = 18.66, p ≈ 0) — both components
reject normality overwhelmingly. This means the `|z| > 3` cutoff is a
heuristic, not a calibrated normal-theory threshold: the tail is fatter than
Gaussian, so a "3-sigma" cut catches a much larger and less clean-cut chunk
of the distribution than it would under normality, consistent with there
being no real gap between id 421 and the other ~40 flagged ids.

Manually checking URLs for the next several highest counts after 421
confirms this: at `z_thresh = 4.5`, only id 421 and id 4668 ("Ahavath Achim
Synagogue", aasynagogue.org) clear the bar, and 4668 is a real, live
synagogue site — not a scraping error. The next 5 highest counts (Temple
Emanuel of South Hills, Temple Sinai RI, Ann Arbor Reconstructionist
Congregation, Adat Shalom Synagogue, Temple Hillel B'nai Torah) all resolve
to legitimate synagogue websites too. So among the high-count tail, only
id 421 is an actual data error — the rest, including everything additionally
swept in at the default `z_thresh = 3.0`, are real synagogues that happen to
be very environmentally active.

`load_actions()` and `active_denominator()` apply the exclusion already.
Any other script in `11_analysis/` that queries `synagogues`, `websites`,
`web_pages`, or either classification table directly should import
`excluded_ids_sql()` from `recreate_existing/recreate_common.py` (add
`recreate_existing/` to `sys.path`) and embed it as
`AND {excluded_ids_sql("s")}` in every CTE/query that selects from
`synagogues`. Do not hardcode `s.id <> 421` or any other id locally.

## Outlier cut, measured today (2026-08-17, z-score method, current)

| | `keyword_chunks` | `page_chunks` |
|---|---|---|
| Active synagogues (pre-exclusion) | 2,702 | 2,702 |
| Excluded at \|z\|>3 | 38 | 45 |
| Active denominator (post-exclusion) | 2,664 | 2,657 |
| Synagogues with ≥1 action | 1,200 | 1,353 |
| Action chunks kept | 16,615 | 13,561 |
| Action chunks **dropped** by the cut | 13,036 (44%) | 11,491 (46%) |

**The cut is doing far more work than "drops ~41 synagogues" suggests.** It
removes 45 of 2,702 synagogues (1.7%) but **46% of all action chunks** under
`page_chunks`, because every excluded synagogue has ≥126 action chunks. The
highest remaining count is id 4668 ("Ahavath Achim Synagogue",
aasynagogue.org) at 1,315 chunks — a real, live synagogue site, not a scraping
error. Anyone revisiting `OUTLIER_Z_THRESH` should weigh that: the threshold is
currently deciding roughly half the corpus, not trimming a handful of errors.

## IQR (Tukey fence) exploration, 2026-08-17 (reverted)

Four IQR-based variants were tried today as a nonparametric replacement for
the z-score method (motivated by the Mardia's-test non-normality result
above), then **reverted** — `find_outlier_synagogues()` is back to the
z-score implementation described above, byte-for-byte the same as before
today. Kept here for the record in case IQR fencing is revisited.

**1. Raw scale (no log transform), `OUTLIER_IQR_MULT = 1.5`:** far more
aggressive than z-score. Because `n_actions` is zero-inflated, Q1 collapses
to 0 on the raw scale, so the upper fence reduces to `2.5 * Q3`. Excluded
367 (`keyword_chunks`) / 376 (`page_chunks`) synagogues, dropping 87.6% /
86.0% of action chunks (vs. 44% / 46% under z-score).

**2. Raw scale, `OUTLIER_IQR_MULT = 3.0`:** widening the multiplier pulled
exclusions back somewhat (240 / 264 synagogues, 80.8% / 80.1% of chunks
dropped) but didn't change the basic picture — Q1 still collapses to 0
regardless of multiplier on the raw scale, so even a much wider gate still
dropped a large majority of the corpus.

**3. log1p scale, `OUTLIER_IQR_MULT = 3.0`:** restoring the log1p transform
(same skew correction the z-score method uses) reversed the direction
entirely — this was *more lenient* than z-score, excluding only **1
synagogue per source** (id 4668, "Ahavath Achim Synagogue" — the same one
the z-score writeup above already identifies as a real, legitimate site,
not a scraping error) and dropping only 4.4% / 5.2% of chunks.

**4. log1p scale, `OUTLIER_IQR_MULT = 1.5`:** the middle ground among
everything tried — excluded 77 (`keyword_chunks`) / 89 (`page_chunks`)
synagogues, dropping 58.6% / 59.4% of chunks. More exclusion than z-score
(38/45, 44-46%) but far less than either raw-scale variant. Every synagogue
z-score excluded was still excluded here; the additional ~40 synagogues per
source started around 55-94 action chunks each — a noticeably higher floor
than the raw-scale variants' newly-added exclusions (~11-22 chunks each),
consistent with the log1p transform pulling in only the genuinely high tail.

None of the four was adopted as the default; `OUTLIER_Z_THRESH`-based
z-score remains the production method. `recreate_existing/
compare_outlier_methods.py` and `recreate_existing/test_outlier_detection.py`
(added during this exploration) still exist in the repo for reference /
re-running if IQR fencing is revisited, but are not part of the routine
figure-generation pipeline.

## Action-chunk source: `keyword_chunks` vs `page_chunks`

`recreate_common.set_source()` selects which table defines an "action":

* `keyword_chunks` — `llm_chunk_classifications`, reached via
  `page_keyword_matches`. Only pages that hit a keyword, and one ±500-char
  window **per keyword match**, so keyword-dense pages are counted repeatedly.
* `page_chunks` — `page_chunk_classifications`, keyed on `web_page_id`. Every
  page with `content_text`, cut into fixed 500-char non-overlapping chunks, so
  each span of text counts exactly once.

**The two populations are not nested**: `page_chunks` finds 221 synagogues that
`keyword_chunks` misses, and misses 61 that it finds (1,177 in both). Per-
synagogue action *counts* are not comparable across sources — treat the two
figure sets as separate analyses, not as before/after of the same measure.

Framing (Implicit/Explicit/Embedded) is available for both sources.
`llm_chunk_framing` (keyed to `llm_chunk_classifications`) covers `keyword_chunks`;
`page_chunk_framing` (keyed to `page_chunk_classifications`, added 2026-08-11 via
`13_embeddedvsimplictvsexplict/run_page_chunk_framing_classifications.py`) covers
`page_chunks` — 25,052 rows, 0 errors, matching PCC's non-N/A action-chunk count
exactly. `recreate_common.load_framing()` reads whichever table matches the
active source via `framing_join_sql()`.

## `DENOM_ORDER` is a hand-derived snapshot, not a live computation

`recreate_common.DENOM_ORDER` is a fixed list (descending median action count
per synagogue, over all active-denominator synagogues of that denomination,
ties broken by hand) — it is **not** recomputed from the current DB on import.
It was derived once against a specific data pull; if denomination labels or
action counts drift enough to change the ranking, every denomination-axis
figure will keep using the stale order until someone re-derives it by hand.
This is a deliberate trade-off (a fixed order keeps every figure comparable
run-to-run) — re-deriving it is out of scope for routine figure regeneration.

## District-lean synagogue counts are smaller than the active denominator

`fig_district_lean_meanactions*.png` and `political.json`'s `district_lean`
block cover fewer synagogues than `active_denominator()` returns. Two
exclusions stack on top of the outlier cut before a synagogue counts toward
the Dem/Rep totals:

1. Its `congressional_district` doesn't match any row in the elections data.
2. `recreate_common.district_lean()` only returns districts whose 2024
   general-election winner was Democratic or Republican — a district won by
   an independent/third party (or with no resolvable race) is omitted from
   the lookup entirely, so any synagogue in it maps to `NaN` and is dropped
   via `dropna(subset=["lean"])` in `analysis_political.py`.

Concretely (measured on the same pull as the "Outlier cut" table above): 2,657
active-denominator synagogues under `page_chunks`, minus 121 with no
resolvable Dem/Rep district lean, leaves the 1,856 Dem + 680 Rep = 2,536 shown
in `fig_district_lean_meanactions_per_synagogue.png`. Under `keyword_chunks`
the active denominator is 2,664 and the same 121-synagogue gap (district-lean
resolution doesn't depend on the action-classification source) leaves 1,864 +
679 = 2,543. This is expected, not a bug — the district-lean figures are a
proper subset of the active denominator, not "all crawled synagogues."

## `dense_area_bubble_map.py`'s bubbles are formed dynamically, and are scale-dependent

Bubbles are **not** a fixed metro list — every crawled, coordinate-having
synagogue is projected into the same Web Mercator pixel space Leaflet uses at
the zoom level `fit_bounds` will land on for this map's fixed render size
(`MAP_WIDTH_PX`/`MAP_HEIGHT_PX`), then clustered with DBSCAN
(`CLUSTER_EPS_PX` = pixel distance treated as "would visually overlap",
`MIN_CLUSTER_SIZE` = minimum synagogues to promote a cluster to a bubble
instead of leaving its points as individual dots). This means the bubbles this
script draws are a function of the chosen render size and the two threshold
constants, not a ground-truth geographic boundary — if `MAP_WIDTH_PX`/
`MAP_HEIGHT_PX` changes, `compute_fit_zoom()` picks a different zoom, points
project to different pixel positions, and the clustering (and therefore which
cities get bubbles, and how many) can shift. `cluster_eps_px`/
`min_cluster_size`/`fit_zoom_used` are recorded in `dense_area_bubble_map.json`
for exactly this reason — to make a given run's clustering basis inspectable.

This is also the **first figure in `11_analysis` that needs live internet
access to regenerate**, in addition to the usual DB tunnel: it renders a
Folium/Leaflet map to HTML, then screenshots it with headless Chrome, and that
screenshot only shows real basemap tiles if the CartoDB tile server is
reachable at run time. Every other figure in this directory is computed
entirely from the DB pull and needs no other network access.
