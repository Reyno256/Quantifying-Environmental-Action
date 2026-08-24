"""
Correlate "synagogue has at least one environmental action" with
"synagogue is in a Democratic congressional district".

Population
---------
Synagogues with >= 1 successfully crawled website (websites.has_error = FALSE)
that also sit in a congressional district whose general-election winner in the
chosen year was either Democratic or Republican.

Variables (both binary, per synagogue)
--------------------------------------
* has_action : 1 if >= 1 non-N/A llm_chunk_classification on a crawled site.
* is_dem     : 1 if the district's general-election winner was Democratic.

Statistics
----------
* 2x2 contingency table with row proportions.
* Phi coefficient (== Pearson/point-biserial r for two binaries).
* Chi-square test of independence (with p-value).
* Odds ratio with a 95% confidence interval (Haldane-Anscombe corrected).

Outputs:
  action_vs_dem_district.json  — table + statistics
  (also printed to stdout)

Usage:
    python 11_analysis/action_vs_dem_district.py
    python 11_analysis/action_vs_dem_district.py --year 2022
"""

import argparse
import json
import math
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from scipy import stats

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent
OUT = HERE          # reassigned in main() from --source
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402
from recreate_common import excluded_ids_sql  # noqa: E402

DEMOCRATIC_PARTIES = {
    "democrat", "democratic", "democratic-farmer-labor",
    "democratic-farm-labor", "democratic-nonpartisan league", "democratic-npl",
}
REPUBLICAN_PARTIES = {"republican"}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_rows(year: int):
    """Return list of (party, has_action) for crawled synagogues in D/R districts."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        WITH crawled AS (   -- synagogues with >=1 successfully crawled site
            SELECT DISTINCT s.id, s.congressional_district
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            WHERE s.congressional_district IS NOT NULL
              AND {excluded_ids_sql("s")}
        ),
        acted AS (          -- crawled synagogues with >=1 action
            SELECT DISTINCT s.id
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            JOIN web_pages wp ON wp.website_id = w.id
            {rc.action_join_sql("wp", "c", "m")}
            WHERE c.category <> 'N/A'
              AND c.category IS NOT NULL
              AND (c.error IS NULL OR c.error = '')
              AND {excluded_ids_sql("s")}
        ),
        ranked AS (
            SELECT er.district_id, ec.party,
                   RANK() OVER (PARTITION BY er.id
                                ORDER BY ec.candidatevotes DESC) AS rnk
            FROM election_races er
            JOIN election_candidates ec ON ec.race_id = er.id
            WHERE er.year = %(year)s AND er.stage = 'GEN'
        ),
        winner AS (
            SELECT district_id, party FROM ranked WHERE rnk = 1
        )
        SELECT dw.party,
               CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END AS has_action
        FROM crawled c
        JOIN winner dw ON dw.district_id = c.congressional_district
        LEFT JOIN acted a ON a.id = c.id
    """, {"year": year})
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2024,
                    help="General-election year used to classify districts.")
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    rows = load_rows(args.year)

    # 2x2 counts: [is_dem][has_action]
    #   a = Dem & action, b = Dem & no action
    #   c = Rep & action, d = Rep & no action
    a = b = c = d = 0
    for party, has_action in rows:
        is_dem = party in DEMOCRATIC_PARTIES
        is_rep = party in REPUBLICAN_PARTIES
        if not (is_dem or is_rep):
            continue
        if is_dem:
            if has_action: a += 1
            else:          b += 1
        else:
            if has_action: c += 1
            else:          d += 1

    n_dem, n_rep = a + b, c + d
    n = n_dem + n_rep

    p_dem = a / n_dem if n_dem else float("nan")
    p_rep = c / n_rep if n_rep else float("nan")

    table = [[a, b], [c, d]]
    chi2, p_chi, dof, _ = stats.chi2_contingency(table, correction=False)
    phi = math.sqrt(chi2 / n) * (1 if p_dem >= p_rep else -1)

    # Odds ratio (Haldane-Anscombe correction for stability / zero cells).
    aa, bb, cc, dd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    odds_ratio = (aa * dd) / (bb * cc)
    se_log_or = math.sqrt(1/aa + 1/bb + 1/cc + 1/dd)
    lo = math.exp(math.log(odds_ratio) - 1.96 * se_log_or)
    hi = math.exp(math.log(odds_ratio) + 1.96 * se_log_or)

    result = {
        "year": args.year,
        "population": "synagogues with >=1 successfully crawled site, in D/R districts",
        "n_total": n,
        "contingency": {
            "dem_action": a, "dem_no_action": b,
            "rep_action": c, "rep_no_action": d,
        },
        "pct_with_action": {
            "democratic_district": round(100 * p_dem, 1),
            "republican_district": round(100 * p_rep, 1),
        },
        "phi_coefficient": round(phi, 4),
        "chi_square": round(chi2, 3),
        "chi_square_p_value": p_chi,
        "odds_ratio_dem_vs_rep": round(odds_ratio, 3),
        "odds_ratio_95ci": [round(lo, 3), round(hi, 3)],
    }

    print(json.dumps(result, indent=2))
    print()
    print(f"Democratic districts: {a}/{n_dem} = {100*p_dem:.1f}% have >=1 action")
    print(f"Republican districts: {c}/{n_rep} = {100*p_rep:.1f}% have >=1 action")
    print(f"Phi (point-biserial r) = {phi:+.3f},  chi2 p = {p_chi:.2e}")
    print(f"Odds ratio (Dem vs Rep) = {odds_ratio:.2f} "
          f"(95% CI {lo:.2f}-{hi:.2f})")

    (OUT / "action_vs_dem_district.json").write_text(json.dumps(result, indent=2))
    print(f"\nWrote {OUT/'action_vs_dem_district.json'}")


if __name__ == "__main__":
    main()
