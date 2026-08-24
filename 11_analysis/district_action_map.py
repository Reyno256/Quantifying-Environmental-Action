"""
Congressional-district choropleth of environmental action.

Each 119th-Congress district is filled by the percentage of its successfully
crawled synagogues that have >= 1 environmental action. Districts with no
crawled synagogue are left pale grey. Same colour treatment as
state_action_map.py and county_action_map.py, so the three are comparable.

Why this is the healthiest of the three geographies
---------------------------------------------------
Districts are drawn to hold roughly equal population, so the sample per unit is
far more even than counties:

    unit        units   n=1 units   n>=5 units   max n
    state          51           0           45    ~400
    district      384          73          163      52
    county        522         243          104     174

Only 73 of 384 districts rest on a single synagogue, against 243 of 522
counties, so district colour is much less likely to be an artefact of n=1 than
county colour is. n is still not encoded visually — see --min-sample and the
CSV for counts.

District identity
-----------------
`synagogues.congressional_district` is already populated as e.g. "NY-25" /
"IN-4" (see backfill_congressional_district_spatial.py). Boundaries come from
the Census 2024 cartographic file, and the same "{state abbr}-{int(CD119FP)}"
key is rebuilt from the shapefile, matching what synagogue_action_pointcloud.py
does. The DB's congressional_districts.boundary column is empty, so no SQL
spatial join is possible.

Outputs (into the --source output dir):
  district_action_map.html  — interactive
  district_action_map.png   — static
  district_action_map.csv   — per-district numerator / denominator / fraction
  district_action_map.json  — counts for figure_statistics.md

Usage:
    python 11_analysis/district_action_map.py [--source page_chunks] [--min-sample N]
"""

import argparse
import json
import os
import sys
from pathlib import Path

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


def load_districts() -> dict:
    """119th-Congress boundaries as GeoJSON keyed by "{abbr}-{number}"."""
    import geopandas as gpd

    gdf = gpd.read_file(CD_SHP,
                        columns=["STATEFP", "CD119FP", "GEOID", "geometry"])
    gdf = gdf.to_crs(4326)
    gdf["district_id"] = [
        f"{FIPS_TO_ABBR.get(sf, sf)}-{int(cd)}"
        for sf, cd in zip(gdf["STATEFP"], gdf["CD119FP"])
    ]
    gdf["geometry"] = gdf["geometry"].simplify(0.01, preserve_topology=True)
    return json.loads(gdf.set_index("district_id").to_json())


def build_map(df: pd.DataFrame, geojson: dict) -> go.Figure:
    """Plain choropleth: fill = % of crawled synagogues with >=1 action."""
    fig = go.Figure()

    have = set(df["district_id"])
    blank = [f["id"] for f in geojson["features"] if f["id"] not in have]

    if blank:
        fig.add_trace(go.Choropleth(
            geojson=geojson,
            locations=blank,
            featureidkey="id",
            z=[0] * len(blank),
            colorscale=[[0, "#f0f0f0"], [1, "#f0f0f0"]],
            showscale=False,
            marker_line_color="white",
            marker_line_width=0.25,
            hoverinfo="skip",
        ))

    fig.add_trace(go.Choropleth(
        geojson=geojson,
        locations=df["district_id"],
        featureidkey="id",
        z=df["pct"],
        zmin=0, zmax=100,
        colorscale="YlGnBu",
        marker_line_color="white",
        marker_line_width=0.25,
        colorbar=dict(title="% with<br>action"),
        text=[
            f"{r['district_id']}<br>"
            f"{int(r['numerator'])}/{int(r['denominator'])} synagogues "
            f"({r['pct']:.0f}%)"
            for _, r in df.iterrows()
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
    ap.add_argument("--min-sample", type=int, default=0,
                    help="drop districts with fewer than N crawled synagogues "
                         "(default 0 — show all)")
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    df, dropped = load_district_fractions()
    stats = {
        "n_districts": int(len(df)),
        "n_synagogues": int(df["denominator"].sum()),
        "n_districts_n1": int((df["denominator"] == 1).sum()),
        "n_districts_ge5": int((df["denominator"] >= 5).sum()),
        "max_n": int(df["denominator"].max()),
        "n_dropped": int(dropped),
        "min_sample": args.min_sample,
        "source": rc.get_source(),
    }
    print(f"Districts: {stats['n_districts']}  |  synagogues placed: "
          f"{stats['n_synagogues']}  |  n=1: {stats['n_districts_n1']}  |  "
          f"n>=5: {stats['n_districts_ge5']}  |  max: {stats['max_n']}")
    print(f"Crawled synagogues with no district assigned: {dropped}")

    if args.min_sample > 0:
        before = len(df)
        df = df[df["denominator"] >= args.min_sample]
        print(f"--min-sample {args.min_sample}: kept {len(df)} of {before}")

    geo = load_districts()
    known = {f["id"] for f in geo["features"]}
    missing = df[~df["district_id"].isin(known)]
    if len(missing):
        print(f"WARNING: {len(missing)} district ids had no matching boundary "
              f"({missing['denominator'].sum()} synagogues): "
              f"{', '.join(missing['district_id'].head(10))}")
        df = df[df["district_id"].isin(known)]

    fig = build_map(df, geo)

    df.to_csv(OUT / "district_action_map.csv", index=False)
    fig.write_html(OUT / "district_action_map.html")
    fig.write_image(OUT / "district_action_map.png", width=1100, height=700, scale=2)
    (OUT / "district_action_map.json").write_text(json.dumps(stats, indent=2))
    for ext in ("csv", "html", "png", "json"):
        print(f"Wrote {OUT/f'district_action_map.{ext}'}")


if __name__ == "__main__":
    main()
