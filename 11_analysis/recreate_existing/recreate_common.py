"""
Shared utilities for recreating the Shore (2025) MES thesis
("Could Moses Split the Pacific Garbage Patch?") on our PostgreSQL
chunk-level classification data.

See thesis_summary.md for the source study and goals.md for the questions.

Key modelling decisions (see plan):
  * An "action" is a chunk classified as a real environmental action
    (category <> 'N/A'). We count chunks (action frequency), which is
    richer/more robust than the thesis's binary unique-action-type coding.
    The distinct-type count is still reported in the overview as a
    thesis-fidelity check.
  * Which table those chunks come from is selected by set_source():
    'keyword_chunks' (llm_chunk_classifications, keyword-gated — the historical
    default) or 'page_chunks' (page_chunk_classifications, every crawled page).
    Scripts expose this as --source and write page_chunks output to a
    page_chunks/ subdir. Framing is available for both sources via load_framing().
  * Each action chunk's 9-category bucket is recovered from the action
    text (the part after the LAST underscore in `category`), which exactly
    matches the thesis Table 3 action types. The category-name prefix in
    the DB is unreliable (many rows carry a buggy `NA_` prefix), so we
    classify on the action text instead.
  * "Active synagogue" (denominator for every % figure) = a synagogue with
    >= 1 successfully crawled web page (websites.has_error = FALSE).

Requires an SSH tunnel to the database:  ssh -f -N cosdb   (localhost:8989)
Run with any Python that has the requirements.txt stack, e.g.
    /Users/quinn/anaconda3/bin/python3 <script>.py [--source page_chunks]
(The .venv/ referenced by older docstrings does not exist in this checkout.)
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # repo root (…/cc_web_scraping)
load_dotenv(ROOT / ".env")
load_dotenv(HERE / ".env")                  # fall back to a local .env

# ── canonical category order (thesis Table 8 / Figure 2 order) ─────────────
CATEGORIES = [
    "Community",
    "Spirituality & Worship",
    "Kitchen",
    "Waste",
    "Energy",
    "Operations & Maintenance",
    "Environmental & Climate Justice",
    "Water",
    "Other",
]

CATEGORY_COLORS = {
    "Community":                        "#2ca02c",
    "Spirituality & Worship":           "#9467bd",
    "Kitchen":                          "#8c564b",
    "Waste":                            "#bcbd22",
    "Energy":                           "#1f77b4",
    "Operations & Maintenance":         "#e377c2",
    "Environmental & Climate Justice":  "#17becf",
    "Water":                            "#aec7e8",
    "Other":                            "#7f7f7f",
}

# Analysis-time denomination remapping (does NOT touch the DB). Modern Orthodox
# is folded into Orthodox so the analyses treat them as a single category.
DENOM_MERGE = {"Modern Orthodox": "Orthodox"}


def merge_denominations(s: "pd.Series") -> "pd.Series":
    """Apply DENOM_MERGE to a denomination Series (e.g. collapse Modern
    Orthodox into Orthodox). Use everywhere denomination_canonical is read so
    every analysis sees the same merged categories without changing the DB."""
    return s.replace(DENOM_MERGE)


# ── the 9 thesis denominations (excludes Unknown / unaffiliated; Modern
#    Orthodox merged into Orthodox via DENOM_MERGE), ordered by median action
#    count per synagogue (descending), over ALL active-denominator synagogues
#    of that denomination (incl. zero-action ones); ties (three-way tie at
#    median 0: Non-Denominational Conservative, Orthodox, Chabad) broken by
#    hand. ─────────────────────────────────────────────────────────────────
DENOM_ORDER = [
    "Jewish Renewal",
    "Reconstructionist",
    "Humanistic",
    "Reform",
    "Non-Denominational Progressive",
    "Conservative",
    "Non-Denominational Conservative",
    "Orthodox",
    "Chabad",
]
DENOM_EXCLUDE = {"Unknown", None, np.nan}

# ── thesis framing axis (Baugh 2019 / Caldwell et al. 2022; thesis Fig 1) ──
# Implicit: environmental effect but not stated as environmental.
# Explicit: stated goal of helping the environment.
# Embedded: part of regular religious living, not done for stated env reasons.
FRAMINGS = ["Implicit", "Explicit", "Embedded"]
FRAMING_COLORS = {
    "Implicit": "#4c72b0",
    "Explicit": "#55a868",
    "Embedded": "#c44e52",
}

# Outlier exclusion: action counts among active synagogues are z-scored on a
# log1p transform (find_outlier_synagogues below); anything with |z| > 3 is
# excluded from every analysis in 11_analysis/. This always catches
# synagogue_id 421 ("Nature Conservancy", NM, a mis-scraped non-synagogue
# entity) but, being a blunt statistical cut on a heavy-tailed distribution,
# also drops ~40 other synagogues that are simply very environmentally
# active rather than data errors. See ../KNOWN_DATA_ISSUES.md.
# Computed lazily and cached per-process by get_excluded_synagogue_ids() —
# every script in 11_analysis/ filters outliers through that one function,
# either via load_actions()/active_denominator() below or via
# excluded_ids_sql() for scripts that query the DB directly.
OUTLIER_Z_THRESH = 3.0
_excluded_ids_cache: set | None = None


# ── action-chunk source switch ─────────────────────────────────────────────
# Two tables answer "is this chunk an environmental action?", over different
# populations:
#
#   keyword_chunks  llm_chunk_classifications, reached via page_keyword_matches.
#                   Only pages that hit a keyword; one ±500-char window per
#                   keyword match, so keyword-dense pages are counted repeatedly.
#   page_chunks     page_chunk_classifications, keyed directly on web_page_id.
#                   EVERY page with content_text, cut into fixed 500-char
#                   non-overlapping chunks, so each span of text counts once.
#
# The populations are NOT nested: page_chunks finds 221 synagogues that
# keyword_chunks misses, and misses 61 that it finds. Per-synagogue action
# *counts* are therefore not comparable across sources — only run a given
# figure set against one source at a time. See ../KNOWN_DATA_ISSUES.md.
_SOURCE = "keyword_chunks"
SOURCES = ("keyword_chunks", "page_chunks")


def set_source(source: str = "keyword_chunks") -> None:
    """
    Select which classification table the action queries read. Call this BEFORE
    anything that touches the outlier set (load_actions, active_denominator,
    excluded_ids_sql, ...) — the |z|>3 cut is computed from action counts and so
    differs by source (38 excluded under keyword_chunks, 45 under page_chunks),
    which is why this resets the process-global cache.
    """
    global _SOURCE, _excluded_ids_cache
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    _SOURCE = source
    _excluded_ids_cache = None


def get_source() -> str:
    return _SOURCE


def action_join_sql(wp_alias: str = "wp", chunk_alias: str = "c",
                    match_alias: str = "m") -> str:
    """
    SQL JOIN fragment linking a web_pages alias to the action-chunk table for the
    active source. Embed directly after the web_pages join; the chunk table is
    exposed under `chunk_alias` so the caller's WHERE clause (category <> 'N/A',
    error IS NULL) is identical either way.
    """
    if _SOURCE == "page_chunks":
        return (f"JOIN page_chunk_classifications {chunk_alias} "
                f"ON {chunk_alias}.web_page_id = {wp_alias}.id")
    return (f"JOIN page_keyword_matches {match_alias} "
            f"ON {match_alias}.web_page_id = {wp_alias}.id\n"
            f"            JOIN llm_chunk_classifications {chunk_alias} "
            f"ON {chunk_alias}.match_id = {match_alias}.id")


def out_dir(base: "Path") -> "Path":
    """
    Output directory for the active source. keyword_chunks keeps the historical
    paths; page_chunks writes to a parallel subdir so the two figure sets can be
    compared side by side (same idea as the existing canada/ subdir).
    """
    d = base if _SOURCE == "keyword_chunks" else base / "page_chunks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_source_arg(parser) -> None:
    """Register the shared --source flag on an argparse parser."""
    parser.add_argument("--source", choices=SOURCES, default="keyword_chunks",
                        help="which action-chunk table to analyse "
                             "(default: keyword_chunks)")


def get_excluded_synagogue_ids(conn=None, z_thresh: float = OUTLIER_Z_THRESH) -> set:
    """Cached set of outlier synagogue ids (see find_outlier_synagogues)."""
    global _excluded_ids_cache
    if _excluded_ids_cache is None:
        outliers = find_outlier_synagogues(conn=conn, z_thresh=z_thresh)
        _excluded_ids_cache = set(outliers.loc[outliers["is_outlier"], "id"])
    return _excluded_ids_cache


def excluded_ids_sql(alias: str = "s") -> str:
    """
    SQL boolean fragment excluding the outlier ids, e.g. "s.id NOT IN (421, ...)".
    Embed in a WHERE/AND clause of any query that selects from `synagogues`.
    """
    ids = ", ".join(str(i) for i in sorted(get_excluded_synagogue_ids()))
    return f"{alias}.id NOT IN ({ids})"


def get_conn():
    """Connection to the synagogues DB via the localhost:8989 tunnel."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD", "research_pw_2026"),
    )


# ── action-text → 9-category classifier ────────────────────────────────────
# Validated against all 76 distinct action strings in the DB (0 unmapped);
# every assignment was checked by hand against thesis Table 3. Rules are
# ordered: the first matching bucket wins. Keyed on distinctive substrings so
# typo/truncation variants still classify correctly.
_RULES = [
    ("Environmental & Climate Justice", [
        "fossil fuel divest", "divest from any fossil", "environmental justice discussion",
        "indigenous peoples", "land acknowledgement", "indigenous communities",
        "climate justice action", "climate justice events",
        "educational materials about fossil", "call for action on climate change",
    ]),
    ("Spirituality & Worship", [
        "symbols of nature", "reconnect with nature", "prayers/songs",
        "worship service to environmental", "items used in worship",
        "in worship for religious",
    ]),
    ("Kitchen", [
        "food options are provided", "locally sourced food", "dishes and cutlery",
        "fair trade", "farmers market", "provides fresh vegetables",
    ]),
    ("Water", [
        "low-flow aerators", "grey water", "rain barrels", "xeriscaping", "rain garden",
        "insulating hot water", "exterior watering", "high-efficiency hot water heater",
        "low-flush",
    ]),
    ("Waste", [
        "blue bins", "recycling facilities are available", "composting", "composts regularly",
        "cloth bags", "waste reduction strategy", "eye glasses and clothes",
        "re-use or re-distribution", "selected from a bin",
    ]),
    ("Energy", [
        "renewable energy system", "utility expenditures", "utility data",
        "energy efficient lighting", "heating/cooling", "heat pump", "insulation has been added",
        "programmable thermostats", "energy audit has been completed", "weather stripping",
        "ceiling fans", "caulk and spray foam", "light switches", "solar hot water",
    ]),
    ("Operations & Maintenance", [
        "think twice before printing", "procurement policy for recycled", "cleaning products",
        "green maintenance plan", "sustainability coordinator", "operations committee",
        "well maintained and energy-efficient",
    ]),
    ("Community", [
        "environmental cleanup", "board committee", "community garden for native",
        "stewardship focus are available", "educational sessions",
        "support sustainable public policies", "summer camp", "collaboration with other faith",
        "volunteering initiatives", "greener lifestyle",
        "household energy audits and water conservation", "car-pooling",
        "younger people to experience", "volunteer coordinator",
        "carpooling preferential parking", "electric vehicle charging",
    ]),
]


def classify_action(action: str) -> str:
    """Map an action text to one of the 9 thesis categories. 'Other'/blank → Other."""
    if action is None:
        return "Other"
    s = action.strip().lower()
    if s in ("", "other", "na", "n/a"):
        return "Other"
    for cat, keys in _RULES:
        if any(k in s for k in keys):
            return cat
    return "??UNMAPPED"


# SQL fragment: pure action text = substring after the LAST underscore.
# (Action prose never contains underscores; all underscores are LLM prefixes.)
_PURE_ACTION_SQL = (
    "CASE WHEN position('_' in l.category) > 0 "
    "THEN reverse(split_part(reverse(l.category), '_', 1)) "
    "ELSE l.category END"
)


def pure_action(category: str) -> str:
    """Python equivalent of _PURE_ACTION_SQL: substring after the LAST underscore."""
    if category and "_" in category:
        return category.rsplit("_", 1)[-1]
    return category


def load_actions(conn=None) -> pd.DataFrame:
    """
    One row per action chunk (category <> 'N/A'), with its 9-category bucket and
    the owning synagogue's denomination / state / congressional district.

    Columns: chunk_id, synagogue_id, action, category, denomination, state,
             congressional_district
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        q = f"""
            SELECT l.id                       AS chunk_id,
                   w.synagogue_id             AS synagogue_id,
                   {_PURE_ACTION_SQL}         AS action,
                   s.denomination_canonical   AS denomination,
                   s.state                    AS state,
                   s.congressional_district   AS congressional_district
            FROM web_pages wp
            {action_join_sql("wp", "l", "pkm")}
            JOIN websites    w ON  w.id = wp.website_id
            JOIN synagogues  s ON  s.id =  w.synagogue_id
            WHERE l.category <> 'N/A'
              AND (l.error IS NULL OR l.error = '')
        """
        df = pd.read_sql(q, conn)
    finally:
        if own:
            conn.close()

    df = df[~df["synagogue_id"].isin(get_excluded_synagogue_ids())]
    df["denomination"] = merge_denominations(df["denomination"])
    df["category"] = df["action"].map(classify_action)
    unmapped = sorted(df.loc[df["category"] == "??UNMAPPED", "action"].unique())
    if unmapped:
        raise ValueError(
            f"{len(unmapped)} action string(s) did not classify; update _RULES:\n"
            + "\n".join(f"  - {a}" for a in unmapped[:20])
        )
    return df


def framing_join_sql(framing_alias: str = "f", chunk_alias: str = "l",
                     match_alias: str = "pkm") -> str:
    """
    SQL FROM/JOIN block from the framing table down to web_pages, for the
    active source. keyword_chunks reads llm_chunk_framing via
    llm_chunk_classifications -> page_keyword_matches; page_chunks reads
    page_chunk_framing via page_chunk_classifications directly.
    """
    if _SOURCE == "page_chunks":
        return (f"FROM page_chunk_framing {framing_alias}\n"
                f"            JOIN page_chunk_classifications {chunk_alias} "
                f"ON {chunk_alias}.id = {framing_alias}.chunk_id\n"
                f"            JOIN web_pages  wp ON wp.id = {chunk_alias}.web_page_id")
    return (f"FROM llm_chunk_framing {framing_alias}\n"
            f"            JOIN llm_chunk_classifications {chunk_alias} "
            f"ON {chunk_alias}.id = {framing_alias}.chunk_id\n"
            f"            JOIN page_keyword_matches {match_alias} "
            f"ON {match_alias}.id = {chunk_alias}.match_id\n"
            f"            JOIN web_pages  wp ON wp.id = {match_alias}.web_page_id")


def load_framing(conn=None) -> pd.DataFrame:
    """
    One row per framed action chunk, with the owning synagogue and the chunk's
    9-category bucket. Framing is the thesis Implicit/Explicit/Embedded axis,
    read from llm_chunk_framing (keyword_chunks) or page_chunk_framing
    (page_chunks) via framing_join_sql() — same shape as action_join_sql(), just
    starting one table further up the chain.

    Columns: chunk_id, synagogue_id, framing, action, category

    Available for both sources. page_chunk_framing was added via
    13_embeddedvsimplictvsexplict/run_page_chunk_framing_classifications.py,
    which classifies every confirmed-action page_chunk_classifications row with
    the same judge (framing_judge.get_framing_judge) used for keyword_chunks —
    25,052 rows, 0 errors, matching PCC's non-N/A action-chunk count exactly.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        q = f"""
            SELECT l.id                AS chunk_id,
                   w.synagogue_id      AS synagogue_id,
                   f.framing           AS framing,
                   {_PURE_ACTION_SQL}  AS action
            {framing_join_sql("f", "l", "pkm")}
            JOIN websites    w ON  w.id = wp.website_id
            WHERE l.category <> 'N/A'
              AND (l.error IS NULL OR l.error = '')
              AND f.framing IN ('Implicit', 'Explicit', 'Embedded')
        """
        df = pd.read_sql(q, conn)
    finally:
        if own:
            conn.close()

    df = df[~df["synagogue_id"].isin(get_excluded_synagogue_ids())]
    df["category"] = df["action"].map(classify_action)
    return df


def framing_count_matrix(framing: pd.DataFrame, syn_ids) -> pd.DataFrame:
    """
    index = synagogue_id (every id in `syn_ids`), columns = the 3 FRAMINGS,
    values = number of framed action chunks of that framing for that synagogue.
    """
    syn_ids = list(syn_ids)
    sub = framing[framing["synagogue_id"].isin(syn_ids)]
    m = (sub.groupby(["synagogue_id", "framing"]).size()
            .unstack(fill_value=0)
            .reindex(index=syn_ids, columns=FRAMINGS, fill_value=0))
    return m.fillna(0).astype(int)


def find_outlier_synagogues(conn=None, z_thresh: float = OUTLIER_Z_THRESH) -> pd.DataFrame:
    """
    Data-driven outlier detection for action counts among active synagogues
    (>=1 crawled page). This is the basis for get_excluded_synagogue_ids().

    Action counts are heavily right-skewed with many zeros, so they are
    log1p-transformed before z-scoring: zscore = (log1p(n) - mean) / std
    over all active synagogues. Rows with |zscore| > z_thresh are flagged.

    Returns one row per active synagogue (id, name, n_actions, log_n_actions,
    zscore, is_outlier), sorted by |zscore| descending. Does not apply the
    exclusion itself — use get_excluded_synagogue_ids() for that.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        q = f"""
            WITH active AS (
                SELECT DISTINCT s.id, s.name
                FROM synagogues s
                JOIN websites  w  ON w.synagogue_id = s.id AND w.has_error = FALSE
                JOIN web_pages wp ON wp.website_id = w.id
            ),
            counts AS (
                SELECT w.synagogue_id AS id, COUNT(*) AS n_actions
                FROM websites w
                JOIN web_pages wp ON wp.website_id = w.id
                {action_join_sql("wp", "l", "pkm")}
                WHERE l.category <> 'N/A'
                  AND (l.error IS NULL OR l.error = '')
                GROUP BY w.synagogue_id
            )
            SELECT a.id, a.name, COALESCE(c.n_actions, 0) AS n_actions
            FROM active a
            LEFT JOIN counts c ON c.id = a.id
        """
        df = pd.read_sql(q, conn)
    finally:
        if own:
            conn.close()

    df["log_n_actions"] = np.log1p(df["n_actions"])
    mu, sigma = df["log_n_actions"].mean(), df["log_n_actions"].std(ddof=0)
    df["zscore"] = (df["log_n_actions"] - mu) / sigma
    df["is_outlier"] = df["zscore"].abs() > z_thresh
    return (df.sort_values("zscore", key=lambda s: s.abs(), ascending=False)
              .reset_index(drop=True))


def active_denominator(conn=None) -> set:
    """
    Synagogue ids that form the denominator for every '% environmentally active'
    figure: synagogues with >= 1 successfully crawled web page.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        q = """
            SELECT DISTINCT s.id
            FROM synagogues s
            JOIN websites  w ON w.synagogue_id = s.id AND w.has_error = FALSE
            JOIN web_pages wp ON wp.website_id = w.id
        """
        ids = set(pd.read_sql(q, conn)["id"].tolist()) - get_excluded_synagogue_ids()
    finally:
        if own:
            conn.close()
    return ids


# ── district political lean (reused logic from action_vs_dem_district.py) ──
DEMOCRATIC_PARTIES = {
    "democrat", "democratic", "democratic-farmer-labor", "democratic-farm-labor",
    "democratic-nonpartisan league", "democratic-npl",
}
REPUBLICAN_PARTIES = {"republican"}


def district_lean(year: int = 2024, conn=None) -> dict:
    """
    {congressional_district_id: 'Dem' | 'Rep'} from the general-election winner
    of `year`. Districts whose winner is neither D nor R are omitted.
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        q = """
            WITH ranked AS (
                SELECT er.district_id, ec.party,
                       RANK() OVER (PARTITION BY er.id
                                    ORDER BY ec.candidatevotes DESC) AS rnk
                FROM election_races er
                JOIN election_candidates ec ON ec.race_id = er.id
                WHERE er.year = %(year)s AND er.stage = 'GEN'
            )
            SELECT district_id, party FROM ranked WHERE rnk = 1
        """
        rows = pd.read_sql(q, conn, params={"year": year})
    finally:
        if own:
            conn.close()
    out = {}
    for did, party in zip(rows["district_id"], rows["party"]):
        p = (party or "").strip().lower()
        if p in DEMOCRATIC_PARTIES:
            out[did] = "Dem"
        elif p in REPUBLICAN_PARTIES:
            out[did] = "Rep"
    return out


# ── per-synagogue category-count matrix (basis for counts & Friedman test) ─
def category_count_matrix(actions: pd.DataFrame, syn_ids) -> pd.DataFrame:
    """
    index = synagogue_id (every id in `syn_ids`), columns = the 9 CATEGORIES,
    values = number of action chunks of that category for that synagogue (0-filled).
    """
    syn_ids = list(syn_ids)
    sub = actions[actions["synagogue_id"].isin(syn_ids)]
    m = (sub.groupby(["synagogue_id", "category"]).size()
            .unstack(fill_value=0)
            .reindex(index=syn_ids, columns=CATEGORIES, fill_value=0))
    return m.fillna(0).astype(int)


# ── statistics helpers ─────────────────────────────────────────────────────
def friedman_within(matrix: pd.DataFrame) -> dict:
    """
    Friedman test (non-parametric repeated-measures): does the action count differ
    across the columns (categories/framings), with repeated measures nested within
    synagogue (rows)? The non-parametric analog of repeated-measures ANOVA, used
    because the per-synagogue counts are zero-inflated and heavily right-skewed.
    """
    from scipy import stats
    cols = [matrix[c].values for c in matrix.columns]
    chi2, p = stats.friedmanchisquare(*cols)
    return {
        "chi2": float(chi2),
        "df": int(matrix.shape[1] - 1),
        "p": float(p),
        "n_subjects": int(matrix.shape[0]),
    }


def kruskal_between(values_by_group: dict) -> dict:
    """Kruskal-Wallis H-test across groups (e.g. denomination, state). The
    non-parametric analog of one-way between-subjects ANOVA."""
    from scipy import stats
    groups = [np.asarray(v, float) for v in values_by_group.values() if len(v) > 1]
    H, p = stats.kruskal(*groups)
    k = len(groups)
    N = sum(len(g) for g in groups)
    return {"H": float(H), "df": k - 1, "p": float(p),
            "n_groups": k, "n_total": int(N)}


def pairwise_bonferroni(values_by_group: dict, paired=False, only_sig=True) -> pd.DataFrame:
    """
    Pairwise non-parametric tests with Bonferroni correction across groups.
    paired=True uses the within-synagogue Wilcoxon signed-rank test (for category
    vectors); otherwise the Mann-Whitney U test. Returns a tidy DataFrame.
    """
    from scipy import stats
    from itertools import combinations
    keys = list(values_by_group)
    pairs = list(combinations(keys, 2))
    m = len(pairs)
    rows = []
    for a, b in pairs:
        x = np.asarray(values_by_group[a], float)
        y = np.asarray(values_by_group[b], float)
        if paired:
            # Wilcoxon errors when all paired differences are zero -> p=1.
            try:
                stat, p = stats.wilcoxon(x, y)
            except ValueError:
                stat, p = 0.0, 1.0
        else:
            stat, p = stats.mannwhitneyu(x, y, alternative="two-sided")
        p_adj = min(p * m, 1.0)
        rows.append({"group1": a, "group2": b, "mean1": x.mean(), "mean2": y.mean(),
                     "stat": float(stat), "p_raw": float(p), "p_bonf": float(p_adj),
                     "sig": p_adj < 0.05})
    out = pd.DataFrame(rows).sort_values("p_bonf").reset_index(drop=True)
    return out[out["sig"]] if only_sig else out


def dunn_posthoc(values_by_group: dict, method="fdr_bh", only_sig=False) -> pd.DataFrame:
    """
    Dunn's test (Dunn 1964), the standard non-parametric post-hoc following a
    significant Kruskal-Wallis test. Ranks ALL observations together once across
    every group (not just each pair), derives one shared tie-correction term from
    that pooled ranking, then compares each pair via a z-statistic on the group
    mean ranks. This matches scikit-posthocs' posthoc_dunn (unlike running
    pairwise Mann-Whitney U per pair, which re-ranks locally each time).
    Multiple-comparison correction defaults to Benjamini-Hochberg (FDR); pass
    method="bonferroni" for Bonferroni. Returns a tidy DataFrame.
    """
    from scipy import stats
    from itertools import combinations
    from statsmodels.stats.multitest import multipletests

    keys = [k for k, v in values_by_group.items() if len(v) > 0]
    groups = {k: np.asarray(values_by_group[k], float) for k in keys}
    all_vals = np.concatenate([groups[k] for k in keys])
    N = len(all_vals)
    ranks = stats.rankdata(all_vals)
    rank_by_group, pos = {}, 0
    for k in keys:
        n = len(groups[k])
        rank_by_group[k] = ranks[pos:pos + n]
        pos += n
    mean_rank = {k: rank_by_group[k].mean() for k in keys}
    n_k = {k: len(groups[k]) for k in keys}
    _, counts = np.unique(all_vals, return_counts=True)
    tie_term = np.sum(counts ** 3 - counts) / (12.0 * (N - 1)) if N > 1 else 0.0
    sigma_base = N * (N + 1) / 12.0 - tie_term

    pairs = list(combinations(keys, 2))
    rows = []
    for a, b in pairs:
        se = np.sqrt(sigma_base * (1.0 / n_k[a] + 1.0 / n_k[b]))
        z = (mean_rank[a] - mean_rank[b]) / se if se > 0 else 0.0
        p = float(2 * stats.norm.sf(abs(z)))
        rows.append({"group1": a, "group2": b, "n1": n_k[a], "n2": n_k[b],
                     "mean1": float(groups[a].mean()), "mean2": float(groups[b].mean()),
                     "z": float(z), "p_raw": p})
    out = pd.DataFrame(rows)
    if len(out):
        out["p_adj"] = multipletests(out["p_raw"], method=method)[1]
        out["sig"] = out["p_adj"] < 0.05
        out = out.sort_values("p_adj").reset_index(drop=True)
    return out[out["sig"]] if only_sig and len(out) else out


def sem(x) -> float:
    x = np.asarray(x, float)
    return float(x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else 0.0


def ci95_mean(x) -> float:
    """Half-width of a 95% CI for the mean (1.96 * SEM)."""
    return 1.96 * sem(x)


def ci95_prop(k: int, n: int) -> float:
    """Half-width of a 95% normal-approx CI for a proportion (returned on 0-1 scale)."""
    if n == 0:
        return 0.0
    p = k / n
    return 1.96 * np.sqrt(p * (1 - p) / n)


def sd_mean(x) -> float:
    """Sample standard deviation of x (ddof=1) — the error-bar half-width for ±1 SD."""
    x = np.asarray(x, float)
    return float(x.std(ddof=1)) if len(x) > 1 else 0.0


def sd_prop(k: int, n: int) -> float:
    """Binomial standard deviation of a proportion (0-1 scale) — ±1 SD half-width."""
    if n == 0:
        return 0.0
    p = k / n
    return float(np.sqrt(p * (1 - p) / n))


# ── figure typography ──────────────────────────────────────────────────────
# ONE knob for every figure's text. Every other size below is derived from it,
# so changing this line rescales the whole figure family consistently.
#
# The offsets keep the roles in the same relative order they have always had
# (ticks and annotations a little below the axis labels, legends below those).
#
# Figures carry NO titles: they were stripped so captions live in the paper, and
# every statistic that used to sit in a title or an in-plot box now lives in
# figure_statistics.md (see 11_analysis/generate_figure_statistics.py). The only
# text above an axes is a short panel identifier on multi-panel figures, sized
# with FS_PANEL_LABEL — matplotlib's relative "large" would render those at
# 1.2x the base, i.e. larger than the axis labels, which reads oddly with no
# real title present.
AXIS_FONT_SIZE = 18                    # <-- the knob

FS_AXIS_LABEL   = AXIS_FONT_SIZE       # 18  set_xlabel / set_ylabel / colorbar label
FS_TICK         = AXIS_FONT_SIZE - 3   # 15  tick labels
FS_ANNOTATION   = AXIS_FONT_SIZE - 3   # 15  ax.text / annotate / bar value labels
FS_LEGEND       = AXIS_FONT_SIZE - 4   # 14  legend entries and legend titles
FS_PANEL_LABEL  = AXIS_FONT_SIZE       # 18  short per-panel identifiers
FS_PLOTLY       = AXIS_FONT_SIZE + 1   # 19  plotly base

# Two figures are deliberately exempt because their panels shrink with panel
# count and would clip at these sizes — see the header comments in
# 11_analysis/action_distance_cornerplot.py and
# 11_analysis/action_logit_distance_distributions.py.


def apply_font_sizes(plt=None):
    """
    Font rcParams only — no grid/spine/dpi opinions, so this is safe to call
    from any script without restyling its figures beyond typography.

    Note ax.text()/annotate() have NO dedicated rcParam (they resolve to
    font.size), so annotation call sites must pass fontsize=FS_ANNOTATION
    explicitly; everything else here is picked up automatically.
    """
    if plt is None:
        import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.size":             AXIS_FONT_SIZE,
        "axes.labelsize":        FS_AXIS_LABEL,
        "axes.titlesize":        FS_PANEL_LABEL,   # panel identifiers only
        "figure.titlesize":      FS_PANEL_LABEL,
        "xtick.labelsize":       FS_TICK,
        "ytick.labelsize":       FS_TICK,
        "legend.fontsize":       FS_LEGEND,
        "legend.title_fontsize": FS_LEGEND,
    })
    return plt


# ── matplotlib house style ─────────────────────────────────────────────────
def apply_style():
    """Full house style: typography (via apply_font_sizes) + grid/spines/dpi."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    apply_font_sizes(plt)
    plt.rcParams.update({
        "font.family": "sans-serif",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.alpha": 0.3,
        "figure.dpi": 150,
    })
    return plt
