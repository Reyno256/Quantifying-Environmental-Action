"""
Congressional-district cartogram of environmental action.

Same data as district_action_map.py, but each district is additionally scaled
about its own centroid by sqrt(n / n_max), where n is the number of crawled
synagogues, so a district's drawn SIZE communicates its sample size and its
COLOUR is still the raw percentage with >= 1 action. True district outlines are
drawn underneath in pale grey, so the gap between outline and fill shows how
thin a district's data is.

This is the district equivalent of the county cartogram tried in
county_action_map.py's history. It works far better here: county areas span
~4 orders of magnitude (a normalised-area version collapsed to a blank map),
but congressional districts are districted to near-equal population, so their
areas are already far more comparable and n only ranges 1-52 (vs 1-174 for
counties). Same caveat still applies: because shape is preserved, a
geographically large rural district can draw bigger than a small dense one at
equal n — read size as "how much of its own outline this district fills", not
as a size comparison between districts.

Outputs (into the --source output dir):
  district_action_cartogram.html
  district_action_cartogram.png
  district_action_cartogram.csv   — per-district numerator / denominator / fraction / scale
  (reuses district_action_map.json for the summary counts — run
   district_action_map.py first if that file doesn't exist yet)

Usage:
    python 11_analysis/district_action_cartogram.py [--source page_chunks]
"""

import argparse
import json
import os
import sys
from pathlib import Path

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
from recreate_common import excluded_ids_sql  # noqa: E402

rc.apply_font_sizes()     # central AXIS_FONT_SIZE typography

# Linear factor = sqrt(n / n_max); drawn AREA is then proportional to n x
# (the district's own area). MIN_SCALE keeps n=1 districts from vanishing.
MIN_SCALE = 0.12

CD_SHP = ("/vsizip/vsicurl/https://www2.census.gov/geo/tiger/GENZ2024/shp/"
          "cb_2024_us_cd119_500k.zip")

FIPS_TO_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP",
    "72": "PR", "78": "VI",
}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_district_fractions() -> tuple[pd.DataFrame, int]:
    """Per-district numerator/denominator/fraction, plus unplaceable count."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        WITH crawled AS (   -- synagogues with >=1 successfully crawled site
            SELECT DISTINCT s.id, s.congressional_district AS district_id
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
        )
        SELECT c.district_id,
               COUNT(DISTINCT c.id) AS denominator,
               COUNT(DISTINCT a.id) AS numerator
        FROM crawled c
        LEFT JOIN acted a ON a.id = c.id
        GROUP BY c.district_id
        ORDER BY c.district_id
    """)
    rows = cur.fetchall()

    cur.execute(f"""
        SELECT COUNT(DISTINCT s.id)
        FROM synagogues s
        JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
        WHERE s.congressional_district IS NULL
          AND {excluded_ids_sql("s")}
    """)
    dropped = cur.fetchone()[0]
    cur.close()
    conn.close()

    df = pd.DataFrame(rows, columns=["district_id", "denominator", "numerator"])
    df["fraction"] = df["numerator"] / df["denominator"]
    df["pct"] = df["fraction"] * 100
    return df, dropped


def load_districts():
    """119th-Congress GeoDataFrame + the same boundaries as GeoJSON."""
    import geopandas as gpd

    gdf = gpd.read_file(CD_SHP,
                        columns=["STATEFP", "CD119FP", "GEOID", "geometry"])
    gdf = gdf.to_crs(4326)
    gdf["district_id"] = [
        f"{FIPS_TO_ABBR.get(sf, sf)}-{int(cd)}"
        for sf, cd in zip(gdf["STATEFP"], gdf["CD119FP"])
    ]
    gdf["geometry"] = gdf["geometry"].simplify(0.01, preserve_topology=True)
    geojson = json.loads(gdf.set_index("district_id").to_json())
    return gdf, geojson


def scaled_geojson(gdf, df: pd.DataFrame):
    """Each district shrunk about its own centroid by sqrt(n / n_max)."""
    from shapely.affinity import scale as shp_scale

    sub = gdf.merge(df[["district_id", "denominator", "numerator", "pct"]],
                    on="district_id", how="inner").copy()
    n = sub["denominator"].astype(float)
    factor = np.sqrt(n / n.max()).clip(lower=MIN_SCALE)
    sub["scale_factor"] = factor.values
    sub["geometry"] = [
        shp_scale(geom, xfact=f, yfact=f, origin="centroid")
        for geom, f in zip(sub.geometry, sub["scale_factor"])
    ]
    return json.loads(sub.set_index("district_id").to_json()), sub


def build_map(gdf, df: pd.DataFrame, geojson: dict) -> go.Figure:
    """Cartogram: true outlines (pale) + shapes scaled by sample size (filled)."""
    fig = go.Figure()

    all_ids = [f["id"] for f in geojson["features"]]
    fig.add_trace(go.Choropleth(
        geojson=geojson,
        locations=all_ids,
        featureidkey="id",
        z=[0] * len(all_ids),
        colorscale=[[0, "#f4f4f4"], [1, "#f4f4f4"]],
        showscale=False,
        marker_line_color="#dddddd",
        marker_line_width=0.3,
        hoverinfo="skip",
    ))

    scaled, sub = scaled_geojson(gdf, df)
    fig.add_trace(go.Choropleth(
        geojson=scaled,
        locations=sub["district_id"],
        featureidkey="id",
        z=sub["pct"],
        zmin=0, zmax=100,
        colorscale="YlGnBu",
        marker_line_color="#444444",
        marker_line_width=0.4,
        colorbar=dict(title="% with<br>action"),
        text=[
            f"{r['district_id']}<br>"
            f"{int(r['numerator'])}/{int(r['denominator'])} synagogues "
            f"({r['pct']:.0f}%)<br>drawn at {r['scale_factor']*100:.0f}% of size"
            for _, r in sub.iterrows()
        ],
        hovertemplate="%{text}<extra></extra>",
    ))

    fig.update_geos(scope="usa")
    fig.update_layout(
        font=dict(size=rc.FS_PLOTLY),
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    df, dropped = load_district_fractions()
    print(f"Districts: {len(df)}  |  synagogues placed: "
          f"{int(df['denominator'].sum())}  |  max: {int(df['denominator'].max())}")
    print(f"Crawled synagogues with no district assigned: {dropped}")

    gdf, geo = load_districts()
    known = {f["id"] for f in geo["features"]}
    missing = df[~df["district_id"].isin(known)]
    if len(missing):
        print(f"WARNING: {len(missing)} district ids had no matching boundary "
              f"({missing['denominator'].sum()} synagogues): "
              f"{', '.join(missing['district_id'].head(10))}")
        df = df[df["district_id"].isin(known)]

    fig = build_map(gdf, df, geo)

    df.to_csv(OUT / "district_action_cartogram.csv", index=False)
    fig.write_html(OUT / "district_action_cartogram.html")
    fig.write_image(OUT / "district_action_cartogram.png",
                    width=1100, height=700, scale=2)
    for ext in ("csv", "html", "png"):
        print(f"Wrote {OUT/f'district_action_cartogram.{ext}'}")


if __name__ == "__main__":
    main()
