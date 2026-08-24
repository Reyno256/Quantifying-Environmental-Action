"""
Lee's L: bivariate spatial association between political lean and environmental
action count, across synagogues.

Variables (per synagogue)
-------------------------
* lean        : Democratic two-party vote share of the synagogue's congressional
                district in the 2024 general election (dem / (dem + rep)).
* n_actions   : count of non-N/A llm_chunk_classifications on crawled sites.

Population: synagogues with >=1 successfully crawled site (has_error = FALSE),
non-null coordinates, in a district with a contested 2024 GEN race.

Method
------
Spatial weights W: k-nearest-neighbour (great-circle), row-standardized.
Lee's L (Lee 2001) = z_x^T (W^T W) z_y / (1^T (W^T W) 1), with z = z-scored
variables (population SD).  This is exactly esda.Spatial_Pearson.

L in [-1, 1].  L > 0  => the two variables are spatially co-patterned (areas of
high Democratic lean tend to coincide with areas of high action count, and vice
versa), beyond what aspatial correlation alone implies.

Inference: conditional permutation.  z_y is shuffled across locations R times
(x and W fixed); the pseudo p-value is the share of |L_perm| >= |L_obs|.
Null hypothesis: no bivariate spatial association.

For context we also report the aspatial Pearson r(lean, actions) and the
univariate Moran's I of each variable.  A map of the local Lee's L cross-product
(which sums to the global L) is written too.

Outputs:
  action_political_leesL.json
  action_political_leesL_map.png

Usage:
    python 11_analysis/action_political_leesL.py
    python 11_analysis/action_political_leesL.py --k 8 --perms 9999
"""

import argparse
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psycopg2
import scipy.sparse as sp
from dotenv import load_dotenv
from scipy import stats
from sklearn.neighbors import BallTree

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent
OUT = HERE          # reassigned in main() from --source
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

rc.apply_font_sizes()     # central AXIS_FONT_SIZE typography
from recreate_common import excluded_ids_sql  # noqa: E402

STATES_GEOJSON = (
    "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/"
    "master/data/geojson/us-states.json"
)


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_data(year: int) -> pd.DataFrame:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        WITH crawled AS (
            SELECT DISTINCT s.id, s.lat, s.lon, s.congressional_district
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            WHERE s.lat IS NOT NULL AND s.lon IS NOT NULL
              AND s.congressional_district IS NOT NULL
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
        ),
        gen AS (
            SELECT er.district_id,
                   SUM(ec.candidatevotes) FILTER (WHERE ec.party = 'democrat')   AS dem,
                   SUM(ec.candidatevotes) FILTER (WHERE ec.party = 'republican') AS rep
            FROM election_races er
            JOIN election_candidates ec ON ec.race_id = er.id
            WHERE er.year = %(year)s AND er.stage = 'GEN'
            GROUP BY er.district_id
        )
        SELECT c.id, c.lat, c.lon,
               COALESCE(ac.n_actions, 0) AS n_actions,
               g.dem::float / (g.dem + g.rep) AS lean
        FROM crawled c
        JOIN gen g ON g.district_id = c.congressional_district AND (g.dem + g.rep) > 0
        LEFT JOIN action_counts ac ON ac.id = c.id
    """, {"year": year})
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return pd.DataFrame(rows, columns=["id", "lat", "lon", "n_actions", "lean"])


def knn_weights(lat, lon, k: int) -> sp.csr_matrix:
    """Row-standardized KNN spatial weights using great-circle distance."""
    pts = np.radians(np.column_stack([lat, lon]))
    tree = BallTree(pts, metric="haversine")
    _, idx = tree.query(pts, k=k + 1)        # +1 because self is nearest
    n = len(lat)
    rows = np.repeat(np.arange(n), k)
    cols = idx[:, 1:].ravel()                # drop self column
    data = np.full(n * k, 1.0 / k)           # row-standardized
    return sp.csr_matrix((data, (rows, cols)), shape=(n, n))


def zscore(v: np.ndarray) -> np.ndarray:
    return (v - v.mean()) / v.std(ddof=0)


def morans_i(z: np.ndarray, W: sp.csr_matrix) -> float:
    n = len(z)
    s0 = W.sum()
    return (n / s0) * (z @ (W @ z)) / (z @ z)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--k", type=int, default=8, help="number of neighbours")
    ap.add_argument("--perms", type=int, default=9999)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    df = load_data(args.year)
    n = len(df)
    print(f"n = {n} synagogues  |  k = {args.k} neighbours  |  "
          f"perms = {args.perms}")
    print(f"lean: mean {df['lean'].mean():.3f}  | actions: mean "
          f"{df['n_actions'].mean():.2f}, {(df['n_actions']>0).mean()*100:.1f}% >0")

    W = knn_weights(df["lat"].values, df["lon"].values, args.k)
    WtW = (W.T @ W).tocsr()

    zx = zscore(df["lean"].values.astype(float))       # political lean
    zy = zscore(df["n_actions"].values.astype(float))  # action count

    denom = float(np.ones(n) @ (WtW @ np.ones(n)))     # 1^T WtW 1
    c = WtW @ zx                                        # fixed vector
    L_obs = float(zx @ (WtW @ zy)) / denom

    # Fast conditional permutation: only z_y is shuffled, L = (c . zy_perm)/denom
    rng = np.random.default_rng(0)
    perm = np.empty(args.perms)
    for i in range(args.perms):
        perm[i] = (c @ rng.permutation(zy)) / denom
    # two-sided pseudo p-value
    p_two = (1 + np.sum(np.abs(perm) >= abs(L_obs))) / (args.perms + 1)
    p_one = (1 + np.sum(perm >= L_obs)) / (args.perms + 1)
    z_score = (L_obs - perm.mean()) / perm.std()

    pearson_r, pearson_p = stats.pearsonr(df["lean"], df["n_actions"])
    I_lean = morans_i(zx, W)
    I_act = morans_i(zy, W)

    result = {
        "year": args.year, "k_neighbours": args.k, "n": n, "perms": args.perms,
        "variables": {"x": "district Democratic two-party vote share (lean)",
                      "y": "environmental action count"},
        "lees_L": round(L_obs, 4),
        "perm_mean": round(float(perm.mean()), 4),
        "perm_std": round(float(perm.std()), 4),
        "z_score": round(float(z_score), 3),
        "p_value_one_sided": float(p_one),
        "p_value_two_sided": float(p_two),
        "null_hypothesis": "no bivariate spatial association (Lee's L = 0)",
        "aspatial_pearson_r": round(float(pearson_r), 4),
        "aspatial_pearson_p": float(pearson_p),
        "morans_I_lean": round(float(I_lean), 4),
        "morans_I_actions": round(float(I_act), 4),
    }
    (OUT / "action_political_leesL.json").write_text(json.dumps(result, indent=2))

    print(f"\nLee's L = {L_obs:+.4f}")
    print(f"  permutation null: mean {perm.mean():+.4f}, sd {perm.std():.4f}, "
          f"z = {z_score:+.2f}")
    print(f"  pseudo p (one-sided) = {p_one:.4f} | (two-sided) = {p_two:.4f}")
    print(f"aspatial Pearson r(lean, actions) = {pearson_r:+.4f} (p = {pearson_p:.4f})")
    print(f"Moran's I  lean = {I_lean:+.3f}  actions = {I_act:+.3f}")

    # ── local Lee's L map ──────────────────────────────────────────────────────
    local = (W @ zx) * (W @ zy) * (n / denom)   # local cross-products, sum ~ n*L
    make_map(df, local)

    print(f"\nWrote {OUT/'action_political_leesL.json'}")
    print(f"Wrote {OUT/'action_political_leesL_map.png'}")


def make_map(df: pd.DataFrame, local: np.ndarray):
    import geopandas as gpd
    states = gpd.read_file(STATES_GEOJSON)

    fig, ax = plt.subplots(figsize=(13, 8))
    states.boundary.plot(ax=ax, color="0.8", linewidth=0.5)

    lim = np.percentile(np.abs(local), 98)      # robust symmetric scale
    order = np.argsort(np.abs(local))           # strong points on top
    sc = ax.scatter(
        df["lon"].values[order], df["lat"].values[order],
        c=local[order], cmap="PuOr_r", vmin=-lim, vmax=lim,
        s=14, alpha=0.85, linewidths=0,
    )
    ax.set_xlim(-125, -66); ax.set_ylim(24, 50)   # CONUS
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    cbar = fig.colorbar(sc, ax=ax, fraction=0.03, pad=0.01)
    cbar.set_label("local Lee's L contribution")
    fig.savefig(OUT / "action_political_leesL_map.png", dpi=150,
                bbox_inches="tight")


if __name__ == "__main__":
    main()
