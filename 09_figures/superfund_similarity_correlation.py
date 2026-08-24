"""
Correlate per-synagogue mean cosine similarity (to the environmental query
vector) with distance to the nearest EPA Superfund site boundary.

Superfund data: FAC_Superfund_Site_Boundaries_EPA_Public via ArcGIS FeatureServer
  https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services/
  FAC_Superfund_Site_Boundaries_EPA_Public/FeatureServer/0

Requires:
  - web_pages.embedding populated (run 08_database/embed_pages.py)
  - synagogues.location populated (run 08_database/migrate_add_postgis.py)
  - pip install scipy

Output: 09_figures/superfund_similarity_correlation.png
"""

import json
import os
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
from scipy import stats

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "08_database"))
from _embed import encode, to_pgvector

ARCGIS_BASE = (
    "https://services.arcgis.com/cJ9YHowT8TU7DUyn/arcgis/rest/services"
    "/FAC_Superfund_Site_Boundaries_EPA_Public/FeatureServer/0"
)

QUERY_TEXTS = [
    "environmental sustainability action synagogue green",
    "composting recycling waste reduction eco-friendly",
    "climate change carbon footprint renewable energy solar panels",
    "water conservation energy efficiency green building operations",
]

OUT_PNG = ROOT / "09_figures" / "superfund_similarity_correlation.png"


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


# ── query vector ──────────────────────────────────────────────────────────────

def build_query_vector() -> str:
    """Same method as semantic_filter.py — average + renormalise → pgvector literal."""
    import torch
    embs = encode(QUERY_TEXTS)
    avg  = torch.nn.functional.normalize(embs.mean(dim=0, keepdim=True), dim=-1)[0]
    return to_pgvector(avg)


# ── Superfund fetch ───────────────────────────────────────────────────────────

def fetch_superfund_sites() -> list[dict]:
    """Page through ArcGIS FeatureServer; return list of site dicts with GeoJSON geometry."""
    r = requests.get(f"{ARCGIS_BASE}/query",
                     params={"where": "1=1", "returnCountOnly": "true", "f": "json"},
                     timeout=30)
    r.raise_for_status()
    total = r.json()["count"]
    print(f"  {total} Superfund site features to fetch.", flush=True)

    sites = []
    page_size = 2000
    offset = 0
    while offset < total:
        resp = requests.get(f"{ARCGIS_BASE}/query", params={
            "where":             "1=1",
            "outFields":         "EPA_ID,SITE_NAME,STATE_CODE,NPL_STATUS_CODE",
            "outSR":             "4326",
            "f":                 "geojson",
            "resultOffset":      offset,
            "resultRecordCount": page_size,
        }, timeout=60)
        resp.raise_for_status()
        for feat in resp.json().get("features", []):
            geom = feat.get("geometry")
            if not geom:
                continue
            props = feat.get("properties", {})
            sites.append({
                "epa_id":     props.get("EPA_ID") or "",
                "site_name":  props.get("SITE_NAME") or "",
                "state":      props.get("STATE_CODE") or "",
                "npl_status": props.get("NPL_STATUS_CODE") or "",
                "geojson":    json.dumps(geom),
            })
        offset += page_size
        print(f"  Fetched {min(offset, total)}/{total}…", flush=True)
        time.sleep(0.1)

    return sites


# ── PostGIS table ─────────────────────────────────────────────────────────────

def load_superfund_table(cur, sites: list[dict]) -> None:
    cur.execute("DROP TABLE IF EXISTS superfund_sites")
    cur.execute("""
        CREATE TABLE superfund_sites (
            id         SERIAL PRIMARY KEY,
            epa_id     TEXT,
            site_name  TEXT,
            state      TEXT,
            npl_status TEXT,
            geom       GEOMETRY(Geometry, 4326)
        )
    """)
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO superfund_sites (epa_id, site_name, state, npl_status, geom)
        VALUES (%s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
    """, [(s["epa_id"], s["site_name"], s["state"], s["npl_status"], s["geojson"])
          for s in sites],
    page_size=500)
    cur.execute("CREATE INDEX superfund_geom_idx ON superfund_sites USING GIST(geom)")
    print(f"  {len(sites)} sites loaded into superfund_sites.", flush=True)


# ── analysis query ────────────────────────────────────────────────────────────

def compute_similarity_and_distance(cur, q_vec: str):
    """
    Per synagogue (with at least one embedded page and a known location):
      mean_sim : mean cosine similarity across all pages with embeddings
      dist_km  : great-circle distance to nearest Superfund site boundary (km)

    Uses LATERAL KNN (<->) for index-accelerated nearest-neighbour lookup,
    then ST_Distance with ::geography cast for accurate spherical distance.
    """
    cur.execute("""
        WITH page_sims AS (
            SELECT
                w.synagogue_id,
                AVG(1.0 - (wp.embedding <=> %s::vector)) AS mean_sim,
                COUNT(*)                                  AS n_pages
            FROM web_pages wp
            JOIN websites  w ON w.id = wp.website_id
            WHERE wp.embedding IS NOT NULL
            GROUP BY w.synagogue_id
        ),
        nearest_superfund AS (
            SELECT
                s.id AS synagogue_id,
                nn.dist_m
            FROM synagogues s
            CROSS JOIN LATERAL (
                SELECT ST_Distance(s.location::geography, ss.geom::geography) AS dist_m
                FROM superfund_sites ss
                ORDER BY s.location <-> ss.geom   -- GiST KNN for candidate
                LIMIT 1
            ) nn
            WHERE s.location IS NOT NULL
        )
        SELECT
            ps.mean_sim,
            ns.dist_m / 1000.0 AS dist_km,
            ps.n_pages
        FROM page_sims         ps
        JOIN nearest_superfund ns ON ns.synagogue_id = ps.synagogue_id
        ORDER BY ps.mean_sim
    """, (q_vec,))
    rows = cur.fetchall()
    mean_sim = np.array([r[0] for r in rows], dtype=float)
    dist_km  = np.array([r[1] for r in rows], dtype=float)
    n_pages  = np.array([r[2] for r in rows], dtype=int)
    return mean_sim, dist_km, n_pages


# ── plot ──────────────────────────────────────────────────────────────────────

def _p_str(p: float) -> str:
    return "<0.001" if p < 0.001 else f"= {p:.3f}"


def _add_scatter(ax, x, y, xlabel, ylabel, title, color="#2166ac"):
    r_p, p_p = stats.pearsonr(x, y)
    r_s, p_s = stats.spearmanr(x, y)
    slope, intercept, *_ = stats.linregress(x, y)

    ax.scatter(x, y, alpha=0.35, s=12, color=color, linewidths=0)
    x_line = np.linspace(x.min(), x.max(), 300)
    ax.plot(x_line, slope * x_line + intercept, color="#d6604d", lw=1.6,
            label=f"OLS  Pearson r {r_p:+.3f} (p {_p_str(p_p)})\n"
                  f"     Spearman ρ {r_s:+.3f} (p {_p_str(p_s)})")
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.25)
    return r_p, p_p, r_s, p_s


def plot(mean_sim, dist_km, out_path: Path) -> None:
    log_dist = np.log1p(dist_km)   # log(1 + km) — handles zeros, reduces right skew

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    n = len(mean_sim)

    _add_scatter(
        axes[0], mean_sim, dist_km,
        xlabel="Mean cosine similarity to environmental query vector",
        ylabel="Distance to nearest Superfund site (km)",
        title=f"Raw distance  (n = {n:,})",
    )

    r_p, p_p, r_s, p_s = _add_scatter(
        axes[1], mean_sim, log_dist,
        xlabel="Mean cosine similarity to environmental query vector",
        ylabel="log(1 + distance to nearest Superfund site)  [km]",
        title=f"Log-transformed distance  (n = {n:,})",
        color="#4d9221",
    )

    fig.suptitle(
        "Environmental website content vs. proximity to EPA Superfund sites",
        fontsize=12, y=1.01,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"  Log-distance — Pearson r {r_p:+.4f} (p {_p_str(p_p)}), "
          f"Spearman ρ {r_s:+.4f} (p {_p_str(p_s)})")
    print(f"  Saved → {out_path}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("Building environmental query vector…", flush=True)
    q_vec = build_query_vector()

    print("Fetching Superfund site boundaries from ArcGIS…", flush=True)
    sites = fetch_superfund_sites()

    conn = get_conn()
    cur  = conn.cursor()

    print("Loading Superfund sites into PostGIS…", flush=True)
    load_superfund_table(cur, sites)
    conn.commit()

    print("Computing per-synagogue mean similarity and nearest Superfund distance…",
          flush=True)
    mean_sim, dist_km, n_pages = compute_similarity_and_distance(cur, q_vec)
    print(f"  {len(mean_sim):,} synagogues with embeddings and known location.",
          flush=True)

    cur.close()
    conn.close()

    print("Plotting…", flush=True)
    plot(mean_sim, dist_km, OUT_PNG)


if __name__ == "__main__":
    main()
