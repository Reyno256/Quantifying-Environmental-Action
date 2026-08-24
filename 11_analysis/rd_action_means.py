"""
Compare mean environmental action counts for synagogues in Democratic vs
Republican congressional districts (2024 general election winner).

Action count = number of non-N/A rows in llm_chunk_classifications linked
to each synagogue's web pages.  Synagogues with no classified actions
contribute a count of 0.

Outputs:
  rd_action_means.json   — summary statistics + bootstrap CI
  rd_action_means.png    — bar chart with ±1 SD error bars

Usage:
    python 11_analysis/rd_action_means.py
    python 11_analysis/rd_action_means.py --n-boot 20000 --year 2022
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from dotenv import load_dotenv
from scipy import stats
import psycopg2

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent
OUT = HERE          # reassigned in main() from --source
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

rc.apply_font_sizes()     # central AXIS_FONT_SIZE typography
from recreate_common import excluded_ids_sql  # noqa: E402

DEMOCRATIC_PARTIES = {
    "democrat", "democratic", "democratic-farmer-labor",
    "democratic-farm-labor", "democratic-nonpartisan league", "democratic-npl",
}


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_data(year: int) -> dict[str, list[int]]:
    """Return {party: [n_actions_per_synagogue]} for D and R districts."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(f"""
        WITH site_actions AS (
            SELECT w.synagogue_id, COUNT(*) AS n_actions
            FROM websites w
            JOIN web_pages wp ON wp.website_id = w.id
            {rc.action_join_sql("wp", "lcc", "pkm")}
            WHERE lcc.category != 'N/A'
              AND (lcc.error IS NULL OR lcc.error = '')
            GROUP BY w.synagogue_id
        ),
        all_syns AS (
            SELECT s.id, s.congressional_district,
                   COALESCE(sa.n_actions, 0) AS n_actions
            FROM synagogues s
            LEFT JOIN site_actions sa ON sa.synagogue_id = s.id
            WHERE s.congressional_district IS NOT NULL
              AND {excluded_ids_sql("s")}
        ),
        ranked_candidates AS (
            SELECT er.district_id, ec.party,
                   RANK() OVER (PARTITION BY er.id ORDER BY ec.vote_share DESC) AS rnk
            FROM election_races er
            JOIN election_candidates ec ON ec.race_id = er.id
            WHERE er.year = %(year)s AND er.stage = 'GEN'
        ),
        district_winner AS (
            SELECT district_id, party FROM ranked_candidates WHERE rnk = 1
        )
        SELECT dw.party, a.n_actions
        FROM all_syns a
        JOIN district_winner dw ON dw.district_id = a.congressional_district
    """, {"year": year})
    rows = cur.fetchall()
    cur.close()
    conn.close()

    groups: dict[str, list[int]] = defaultdict(list)
    for party, n_actions in rows:
        if party in DEMOCRATIC_PARTIES:
            groups["Democrat"].append(n_actions)
        elif party == "republican":
            groups["Republican"].append(n_actions)
    return groups


# ── statistics ────────────────────────────────────────────────────────────────

def bootstrap_ci(arr: np.ndarray, n_boot: int = 10_000, alpha: float = 0.05):
    """Return (ci_lo, ci_hi) via percentile bootstrap."""
    rng = np.random.default_rng(42)
    boot_means = [
        np.mean(rng.choice(arr, len(arr), replace=True))
        for _ in range(n_boot)
    ]
    lo = 100 * alpha / 2
    hi = 100 * (1 - alpha / 2)
    return np.percentile(boot_means, [lo, hi])


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",   type=int, default=2024,
                        help="Election year to determine district party (default 2024)")
    parser.add_argument("--n-boot", type=int, default=10_000,
                        help="Bootstrap iterations for CI (default 10 000)")
    rc.add_source_arg(parser)
    args = parser.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    print(f"Loading data (election year {args.year})…", flush=True)
    groups = load_data(args.year)

    summary = {}
    print(f"\n{'Party':<12}  {'N':>6}  {'Mean':>7}  {'Median':>7}  "
          f"{'95% CI':>20}  {'SD':>7}")
    print("─" * 62)

    for party in ("Democrat", "Republican"):
        arr = np.array(groups[party])
        ci_lo, ci_hi = bootstrap_ci(arr, args.n_boot)
        mean   = np.mean(arr)
        median = np.median(arr)
        sd     = np.std(arr)
        print(f"{party:<12}  {len(arr):>6,}  {mean:>7.2f}  {median:>7.1f}  "
              f"[{ci_lo:>7.2f}, {ci_hi:>7.2f}]  {sd:>7.2f}")
        summary[party] = dict(
            n=len(arr), mean=float(mean), median=float(median),
            ci_lo=float(ci_lo), ci_hi=float(ci_hi), sd=float(sd),
        )

    # Mann-Whitney U test (non-parametric; counts are heavily right-skewed)
    d_arr = np.array(groups["Democrat"])
    r_arr = np.array(groups["Republican"])
    u_stat, p_val = stats.mannwhitneyu(d_arr, r_arr, alternative="two-sided")
    rank_biserial = 1 - 2 * u_stat / (len(d_arr) * len(r_arr))
    print(f"\nMann–Whitney U : U = {u_stat:.1f},  p = {p_val:.4f}")
    print(f"rank-biserial r : {rank_biserial:.3f}")
    summary["test"] = dict(U=float(u_stat), p=float(p_val), rank_biserial=float(rank_biserial),
                           election_year=args.year)

    # ── save JSON ─────────────────────────────────────────────────────────────
    json_path = OUT / "rd_action_means.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nJSON → {json_path}")

    # ── bar chart ─────────────────────────────────────────────────────────────
    parties = ["Democrat", "Republican"]
    means   = [summary[p]["mean"] for p in parties]
    sds     = [summary[p]["sd"]   for p in parties]
    colors  = ["#2166ac", "#d6604d"]

    fig, ax = plt.subplots(figsize=(6, 5))
    bars = ax.bar(parties, means, color=colors, width=0.5,
                  yerr=sds, capsize=8,
                  error_kw=dict(elinewidth=1.5, ecolor="black", capthick=1.5))

    for bar, mean, sd in zip(bars, means, sds):
        ax.text(bar.get_x() + bar.get_width() / 2, mean + sd + 0.08,
                f"{mean:.2f}\n(±{sd:.2f})",
                ha="center", va="bottom", fontsize=rc.FS_ANNOTATION)

    ax.set_ylabel("Mean environmental actions per synagogue")
    sig = "p < 0.001" if p_val < 0.001 else f"p = {p_val:.3f}"
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_ylim(0, max(m + s for m, s in zip(means, sds)) * 1.35)

    png_path = OUT / "rd_action_means.png"
    plt.tight_layout()
    plt.savefig(png_path, dpi=150)
    print(f"PNG  → {png_path}")


if __name__ == "__main__":
    main()
