"""
Correlate the number of environmental actions per synagogue with the distance
to the nearest OSM feature of each of 7 environmental site types.

Site types (combine point + polygon geometries of each type):
    green_space, env_hazard, landfill, industrial, abattoir, military, airport
Highways (the only line features) are excluded by design.

Population
---------
Synagogues with >= 1 successfully crawled website (websites.has_error = FALSE)
and non-null coordinates.  The action count is the number of non-N/A
llm_chunk_classifications on those crawled sites (0 if none).

Distance
--------
Great-circle distance in metres (PostGIS geography) to the nearest point of the
nearest feature of the given type.  For polygons this is the distance to the
nearest point on the polygon (0 if the synagogue is inside).  Computed via a
GiST KNN pre-filter (5 nearest candidates) followed by an exact geography
distance, taking the minimum over the point and polygon tables.

Statistics (per site type)
--------------------------
Spearman rank correlation between action count and distance.
Null hypothesis: no monotonic relationship (rho = 0).
P-values are Bonferroni-corrected for the 7 tests.

Outputs:
  action_vs_osm_distance.json  — per-type rho, raw p, Bonferroni p, n
  action_vs_osm_distance.csv   — per-synagogue action count + 7 distances
  (summary also printed to stdout)

Usage:
    python 11_analysis/action_vs_osm_distance.py
"""

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
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

SITE_TYPES = [
    "green_space", "env_hazard", "landfill",
    "industrial", "abattoir", "military", "airport",
]
KNN = 5  # nearest candidates per table to refine with exact geography distance


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_action_counts(cur) -> pd.DataFrame:
    """Per-synagogue action count over crawled sites; population = crawled + coords."""
    cur.execute(f"""
        WITH crawled AS (
            SELECT DISTINCT s.id, s.lat, s.lon
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
              AND {excluded_ids_sql("s")}
        ),
        action_counts AS (
            SELECT s.id, COUNT(*) AS n_actions
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            JOIN web_pages wp ON wp.website_id = w.id
            {rc.action_join_sql("wp", "c", "m")}
            WHERE c.category <> 'N/A' AND c.category IS NOT NULL
              AND (c.error IS NULL OR c.error = '')
              AND {excluded_ids_sql("s")}
            GROUP BY s.id
        )
        SELECT c.id, COALESCE(ac.n_actions, 0) AS n_actions
        FROM crawled c
        LEFT JOIN action_counts ac ON ac.id = c.id
        ORDER BY c.id
    """)
    return pd.DataFrame(cur.fetchall(), columns=["id", "n_actions"])


def load_distances(cur, feature_type: str) -> pd.DataFrame:
    """Nearest-feature distance (metres) per synagogue for one site type."""
    cur.execute("""
        WITH syn AS (
            SELECT DISTINCT s.id,
                   ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326) AS g
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
        )
        SELECT syn.id,
               LEAST(
                 COALESCE((SELECT MIN(ST_Distance(syn.g::geography, p.geom::geography))
                           FROM (SELECT geom FROM osm.osm_points
                                 WHERE feature_type = %(ft)s
                                 ORDER BY geom <-> syn.g LIMIT %(k)s) p), 'Infinity'),
                 COALESCE((SELECT MIN(ST_Distance(syn.g::geography, pg.geom::geography))
                           FROM (SELECT geom FROM osm.osm_polygons
                                 WHERE feature_type = %(ft)s
                                 ORDER BY geom <-> syn.g LIMIT %(k)s) pg), 'Infinity')
               ) AS dist
        FROM syn
    """, {"ft": feature_type, "k": KNN})
    return pd.DataFrame(cur.fetchall(), columns=["id", f"dist_{feature_type}"])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)
    conn = get_conn()
    cur = conn.cursor()

    df = load_action_counts(cur)
    print(f"Synagogues (crawled, with coords): {len(df)}")
    print(f"  with >=1 action: {(df['n_actions'] > 0).sum()}  "
          f"| max actions: {df['n_actions'].max()}")

    for ft in SITE_TYPES:
        d = load_distances(cur, ft)
        df = df.merge(d, on="id", how="left")
        print(f"  computed distances: {ft}")

    cur.close()
    conn.close()

    df.to_csv(OUT / "action_vs_osm_distance.csv", index=False)

    n_tests = len(SITE_TYPES)
    results = []
    for ft in SITE_TYPES:
        col = f"dist_{ft}"
        rho, p = stats.spearmanr(df["n_actions"], df[col])
        p_bonf = min(1.0, p * n_tests)
        results.append({
            "site_type": ft,
            "spearman_rho": round(float(rho), 4),
            "p_value": float(p),
            "p_value_bonferroni": float(p_bonf),
            "significant_bonferroni_0.05": bool(p_bonf < 0.05),
            "median_dist_km": round(float(df[col].median()) / 1000, 2),
            "n": int(len(df)),
        })

    summary = {
        "population": "synagogues with >=1 crawled site and coords",
        "action_metric": "count of non-N/A llm_chunk_classifications",
        "distance_metric": "metres to nearest point of nearest feature (point+polygon)",
        "test": "Spearman rank correlation; H0: rho = 0 (no monotonic relation)",
        "multiple_comparison": f"Bonferroni over {n_tests} tests",
        "n_synagogues": int(len(df)),
        "results": results,
    }
    (OUT / "action_vs_osm_distance.json").write_text(json.dumps(summary, indent=2))

    print("\n=== Spearman correlation: action count vs distance to nearest feature ===")
    print(f"{'site_type':14} {'rho':>8} {'p_raw':>11} {'p_bonf':>11}  sig  median_km")
    for r in sorted(results, key=lambda x: x["p_value"]):
        sig = "***" if r["p_value_bonferroni"] < 0.001 else \
              "**" if r["p_value_bonferroni"] < 0.01 else \
              "*" if r["p_value_bonferroni"] < 0.05 else ""
        print(f"{r['site_type']:14} {r['spearman_rho']:>8.3f} "
              f"{r['p_value']:>11.3e} {r['p_value_bonferroni']:>11.3e}  "
              f"{sig:>3}  {r['median_dist_km']:>7.1f}")
    print(f"\nWrote {OUT/'action_vs_osm_distance.json'}")
    print(f"Wrote {OUT/'action_vs_osm_distance.csv'}")


if __name__ == "__main__":
    main()
