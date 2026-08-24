"""
Point cloud of synagogues across the United States, coloured by environmental
action count.

Each successfully crawled synagogue with coordinates is plotted at its lat/lon
and coloured by its action count (number of non-N/A llm_chunk_classifications;
0 if none).  Counts are heavily right-skewed, so colour is mapped on a
log(1 + count) scale with the colour-blind-friendly cividis ramp.

Outputs:
  synagogue_action_pointcloud.png   — static map (CONUS) with state outlines
  synagogue_action_pointcloud.html  — interactive plotly scattergeo (zoomable)

Usage:
    python 11_analysis/synagogue_action_pointcloud.py
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
import plotly.graph_objects as go
import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent
OUT = HERE          # reassigned in main() from --source
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

rc.apply_font_sizes()     # central AXIS_FONT_SIZE typography
from recreate_common import excluded_ids_sql  # noqa: E402

STATES_GEOJSON = ("https://raw.githubusercontent.com/PublicaMundi/MappingAPI/"
                  "master/data/geojson/us-states.json")
CD_URL = ("/vsizip/vsicurl/https://www2.census.gov/geo/tiger/GENZ2024/shp/"
          "cb_2024_us_cd119_500k.zip")
CMAP = "cividis"
TICKS = [0, 1, 5, 20, 100, 500, 1500]

FIPS_TO_ABBR = {
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT",
    "10":"DE","11":"DC","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL",
    "18":"IN","19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD",
    "25":"MA","26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE",
    "32":"NV","33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND",
    "39":"OH","40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD",
    "47":"TN","48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV",
    "55":"WI","56":"WY","60":"AS","66":"GU","69":"MP","72":"PR","78":"VI",
}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_points() -> pd.DataFrame:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        WITH crawled AS (
            SELECT DISTINCT s.id, s.name, s.lat, s.lon
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
        SELECT c.id, c.name, c.lat, c.lon, COALESCE(ac.n_actions, 0) AS n_actions
        FROM crawled c
        LEFT JOIN action_counts ac ON ac.id = c.id
    """)
    df = pd.DataFrame(cur.fetchall(), columns=["id", "name", "lat", "lon", "n_actions"])
    cur.close(); conn.close()
    return df


def load_district_results() -> pd.DataFrame:
    """Per-district 2024 GEN Democratic two-party vote share + winner."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        WITH gen AS (
            SELECT er.district_id,
                   SUM(ec.candidatevotes) FILTER (WHERE ec.party = 'democrat')   AS dem,
                   SUM(ec.candidatevotes) FILTER (WHERE ec.party = 'republican') AS rep
            FROM election_races er
            JOIN election_candidates ec ON ec.race_id = er.id
            WHERE er.year = 2024 AND er.stage = 'GEN'
            GROUP BY er.district_id
        )
        SELECT district_id, dem, rep
        FROM gen WHERE (dem + rep) > 0
    """)
    df = pd.DataFrame(cur.fetchall(), columns=["district_id", "dem", "rep"])
    cur.close(); conn.close()
    df["dem_share"] = df["dem"] / (df["dem"] + df["rep"])
    df["winner"] = np.where(df["dem_share"] >= 0.5, "Democrat", "Republican")
    return df


def load_district_layer():
    """GeoDataFrame of 119th CD boundaries merged with 2024 results, + geojson."""
    import geopandas as gpd
    res = load_district_results()
    gdf = gpd.read_file(CD_URL, columns=["STATEFP", "CD119FP", "GEOID", "geometry"])
    gdf["district_id"] = gdf.apply(
        lambda r: f"{FIPS_TO_ABBR.get(r['STATEFP'], r['STATEFP'])}-{int(r['CD119FP'])}",
        axis=1,
    )
    gdf = gdf.merge(res, on="district_id", how="inner").to_crs(4326)
    gdf["geometry"] = gdf["geometry"].simplify(0.01, preserve_topology=True)
    geojson = json.loads(gdf.set_index("district_id").to_json())
    return gdf, geojson


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)
    df = load_points()
    print(f"Synagogues plotted: {len(df)}  |  with >=1 action: "
          f"{(df['n_actions'] > 0).sum()}  |  max: {df['n_actions'].max()}")

    color = np.log1p(df["n_actions"].values)
    vmax = color.max()
    order = np.argsort(color)        # high-action points drawn on top

    # ── static PNG ─────────────────────────────────────────────────────────────
    import geopandas as gpd
    states = gpd.read_file(STATES_GEOJSON)
    fig, ax = plt.subplots(figsize=(14, 8))
    states.boundary.plot(ax=ax, color="0.8", linewidth=0.5)
    sc = ax.scatter(df["lon"].values[order], df["lat"].values[order],
                    c=color[order], cmap=CMAP, vmin=0, vmax=vmax,
                    s=12, alpha=0.8, linewidths=0)
    ax.set_xlim(-125, -66); ax.set_ylim(24, 50)
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    cbar = fig.colorbar(sc, ax=ax, fraction=0.025, pad=0.01)
    cbar.set_ticks(np.log1p(TICKS)); cbar.set_ticklabels([str(t) for t in TICKS])
    cbar.set_label("environmental action count")
    fig.savefig(OUT / "synagogue_action_pointcloud.png", dpi=150, bbox_inches="tight")
    print(f"Wrote {OUT/'synagogue_action_pointcloud.png'}")

    # ── interactive HTML (point cloud + togglable district election layer) ──────
    dgdf, dgeojson = load_district_layer()
    print(f"District layer: {len(dgdf)} districts with 2024 results")

    dfo = df.iloc[order]
    fig2 = go.Figure()

    # trace 0: congressional districts coloured by Dem two-party share.
    # Starts hidden; toggled on with the button below.  Drawn before the points
    # so the markers always sit on top.
    fig2.add_trace(go.Choropleth(
        geojson=dgeojson, locations=dgdf["district_id"], featureidkey="id",
        z=dgdf["dem_share"] * 100, zmin=0, zmax=100,
        colorscale="RdBu", zmid=50,
        marker_line_color="white", marker_line_width=0.3,
        visible=False,
        colorbar=dict(title="Dem<br>vote %", x=1.0, len=0.9),
        customdata=np.stack([dgdf["district_id"], dgdf["winner"],
                             (dgdf["dem_share"]*100).round(1)], axis=-1),
        hovertemplate=("%{customdata[0]}<br>winner: %{customdata[1]}"
                       "<br>Dem share: %{customdata[2]}%<extra></extra>"),
    ))

    # trace 1: synagogue point cloud (always visible).
    fig2.add_trace(go.Scattergeo(
        lon=dfo["lon"], lat=dfo["lat"], mode="markers",
        marker=dict(
            size=5, color=np.log1p(dfo["n_actions"]), colorscale=CMAP,
            cmin=0, cmax=vmax, opacity=0.85, line=dict(width=0),
            colorbar=dict(title="action<br>count", x=1.12, len=0.9,
                          tickvals=np.log1p(TICKS),
                          ticktext=[str(t) for t in TICKS]),
        ),
        text=[f"{n}<br>actions: {a}" for n, a in zip(dfo["name"], dfo["n_actions"])],
        hovertemplate="%{text}<extra></extra>",
    ))

    fig2.update_geos(scope="usa")
    fig2.update_layout(
        font=dict(size=rc.FS_PLOTLY),   # central AXIS_FONT_SIZE typography
        margin=dict(l=0, r=0, t=10, b=0),
        updatemenus=[dict(
            type="buttons", direction="right", showactive=True,
            x=0.01, xanchor="left", y=0.97, yanchor="top",
            buttons=[
                dict(label="Districts: off", method="update",
                     args=[{"visible": [False, True]}]),
                dict(label="Districts: 2024 Dem vote %", method="update",
                     args=[{"visible": [True, True]}]),
            ],
        )],
    )
    fig2.write_html(OUT / "synagogue_action_pointcloud.html")
    print(f"Wrote {OUT/'synagogue_action_pointcloud.html'}")


if __name__ == "__main__":
    main()
