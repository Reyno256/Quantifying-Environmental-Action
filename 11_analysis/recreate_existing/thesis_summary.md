# Previous analysis — Shore (2025) MES thesis

**"Could Moses Split the Pacific Garbage Patch? Exploring Environmental Action in
Faith Communities Using Novel Approaches"** — Daniel Maxwell Shore, MES thesis,
School of the Environment, University of Toronto, 2025.

This is the *original* study that `recreate_existing/goals.md` is asking us to
reproduce on our pipeline's (larger, automated) data. The thesis PDF itself is
not redistributed in this repo for copyright reasons — see the citation above
to obtain the original from the University of Toronto. The four figures and
three Figure-4 sub-panels referenced throughout this analysis are extracted to
`figures_extracted/`.

---

## What the thesis did

**Research question.** Empirically measure environmental action in US Jewish
places of worship as a test case for the *Greening of Religion* (GoR) hypothesis.

**Data collection (the "novel methodology").**
- Compiled a list of US synagogues by hand (Google, Wikipedia, GuideStar, JWiz,
  regional federations), recording name, location, website, denomination, and
  whether the website was active (any update in the last 5 years).
- Searched each active site with the **rvest** R package for ~104 exact keywords
  + 41 wildcard keyword stems (Table 1), drawn from the Greening Sacred Spaces
  checklist plus Judaism-specific terms.
- This produced ~61,686 candidate links from 2,526 active synagogues. A human
  then **manually opened every link and coded each environmental action** into a
  category and a framing in Excel (binary: did this synagogue take this action
  type, yes/no — *frequency of repeats was not recorded*).

**Coding scheme (human-coded, Appendix 2 + Table 3).**
- **9 categories**, each with an explicit checklist of specific action types:
  Community, Spirituality & Worship, Kitchen, Waste, Energy, Operations &
  Maintenance, Environmental & Climate Justice, Water, Other.
- **3 framings** (Baugh 2019 / Caldwell et al. 2022):
  - *Implicit* — environmental effect but not stated as environmental.
  - *Explicit* — stated goal of helping the environment.
  - *Embedded* — part of regular religious living, not done for stated
    environmental reasons.

**Statistics.** Descriptive frequencies/percentages, then **repeated-measures
ANOVA** (measures nested within synagogue) + **pairwise t-tests with Bonferroni
correction** to test differences across framings, categories, denominations, and
states.

---

## Key findings (the numbers to reproduce)

### Overview (n = 2,526 active synagogues)
- **42.2%** (n = 1,065) took ≥1 environmental action.
- Mean **1.8** unique action types per synagogue (all); **4.2** per
  *environmentally active* synagogue.

### Framing (Fig 1, Table 5) — among environmentally active (n = 1,065)
- Implicit **90.3%**, Explicit **48.6%**, Embedded **17.7%**.
- All three framings differ significantly (RM-ANOVA F(2,2112)=837.1, p<0.001;
  all pairwise p<0.001).

### Categories (Fig 2, Table 8) — mean unique actions per active synagogue
| Category | % active w/ ≥1 | mean/active |
|---|---|---|
| Community | 68% (724) | 1.79 |
| Spirituality & Worship | 73% (777) | 1.11 |
| Kitchen | 35% (373) | 0.47 |
| Waste | 21.8% (232) | 0.32 |
| Energy | 13.8% (147) | 0.27 |
| Operations & Maintenance | (51) | 0.07 |
| Env. & Climate Justice | (50) | 0.06 |
| Water | (36) | 0.05 |
| Other | (35) | 0.03 |

(RM-ANOVA F(8,8448)=530.7, p<0.001. Waste≈Energy n.s.; Ops/Justice/Water/Other
mutually n.s.; everything else differs.)

### Denomination (Fig 3, Tables 4, 5, 11–13)
Ranked by mean actions per active synagogue: Reconstructionist 6.3 (84.5% active)
> Reform 4.9 (72.8%) > Jewish Renewal 4.5 (70.6%) > Conservative 4.3 (70.1%) >
Non-denom. progressive 4.1 (51.2%) > Humanistic 3.6 (61.9%) > Non-denom.
conservative 3.5 (41.4%) > Modern Orthodox 1.9 (44.3%) > Orthodox 1.8 (19.7%) >
Chabad 1.5 (5.7%). Community & Spirituality were the top-2 categories in every
denomination. (RM-ANOVA F(9,1055)=9.30, p<0.001.)

### Regional / political lean (Fig 4, Tables 14–17)
- No significant difference across all 50 states (F(49,1015)=1.18, p=0.19).
- Among the 4 highest-synagogue states — **Dem: CA, NY** vs **Rep: TX, FL** —
  means differ (F(3,418)=4.31, p<0.01). Only **CA > FL** is pairwise significant
  (p=0.007).
- Drilling into CA vs FL: CA has more *explicit* actions, and more Community,
  Spirituality, Kitchen, Waste, Energy, and Env./Climate Justice actions.

---

## How the thesis differs from `goals.md`

`goals.md` is essentially a **to-do list distilled from this thesis** — same
study, re-scoped for our automated pipeline. Differences:

| Aspect | Thesis | goals.md |
|---|---|---|
| Framing analysis | Central (implicit/explicit/embedded — Fig 1, Tables 5–7) | **Dropped.** goals.md never mentions framing. |
| "Mean actions per synagogue" | Reported (1.8 all / 4.2 active) | Asked for explicitly (active vs all). |
| Per-category fractions | Reported (Table 8) | Asked for ("what fraction take a given action"). |
| **Clustering by total action count** | *Not done* — thesis just reports means | **New ask** ("split by synagogue total action count (clustering?)"). |
| Denomination | Full breakdown | Same asks (fraction active, # actions, categories). |
| Political lean | 4 states (CA/NY vs TX/FL) + CA-vs-FL deep dive | 4 states **+ a finer congressional-district breakdown** (new). |
| Stats | RM-ANOVA + Bonferroni t-tests | Not specified (just "how many / what fraction"). |

Net: goals.md keeps the descriptive backbone (overview, categories, denomination,
political lean), **drops the framing axis**, and **adds two things the thesis did
not do — clustering on action count, and a congressional-district-level political
breakdown.**

## How the thesis differs from `09_figures/` (the prior automated recreation)

`09_figures/generate_figures.py` was an earlier attempt to recreate this thesis
from our PostgreSQL DB (`llm_chunk_classifications`). It diverges from the thesis
in several ways — worth knowing so we don't inherit its choices:

| | Thesis | 09_figures |
|---|---|---|
| **Unit of measure** | Binary human-coded *action types* per synagogue (each type counted once) | Raw count of **LLM-classified chunks** (a noisier proxy; Fig 2 & 3 y-axis is "# chunks", not action types) |
| **Categories** | 9 fixed checklist categories, human-assigned | 8 buckets reconstructed by **SQL keyword/`ILIKE` matching** on free-text LLM category strings (no "Other"; Kitchen/Ops folded around) |
| **Fig 1 (framing)** | Implicit / Explicit / Embedded | **Re-defined** as Environmental / Community / Religious / Operational — *not the thesis's framing concept at all* |
| **Fig 3 (denomination)** | Mean actions per active synagogue (bar) | Stacked chunk counts by category; denominations collapsed to ~6 groups |
| **Fig 4 (political)** | CA/NY vs TX/FL, RM-ANOVA on action counts | **State-level env-active rate**, Dem vs Rep across *all* states, Mann–Whitney U |
| **Stats** | RM-ANOVA + Bonferroni t-tests | Binomial SE / Mann–Whitney |

So `09_figures` is a *loose, chunk-based* reinterpretation; the thesis is a
*strict, action-type, human-coded* analysis. Our `recreate_existing` task is to
reproduce the **thesis's** definitions and statistics on our data — closer to the
thesis than 09_figures was.

---

## Extracted figures (`figures_extracted/`)
- `thesis_fig1_framing.png` — % of active synagogues by framing (Implicit/Explicit/Embedded).
- `thesis_fig2_categories.png` — mean # actions per active synagogue by category.
- `thesis_fig3_denomination_meanactions.png` — mean # actions per active synagogue by denomination.
- `thesis_fig4a_state_meanactions.png` — mean # actions, CA/NY (Dem) vs TX/FL (Rep).
- `thesis_fig4b_CA_vs_FL_framing.png` — CA vs FL by framing.
- `thesis_fig4c_CA_vs_FL_categories.png` — CA vs FL by category.
