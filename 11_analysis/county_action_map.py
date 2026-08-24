"""
County-level choropleth of environmental action.

Each county is filled by the percentage of its successfully crawled synagogues
that have >= 1 environmental action. Counties with no crawled synagogue are
left pale grey.

County identity
---------------
`synagogues.census_tract_geoid` is an 11-digit FIPS (state 2 + county 3 +
tract 6), so the first five digits ARE the county. No PostGIS work or boundary
loading is needed — which matters, because every `boundary` column in the DB is
empty. Crawled synagogues without a tract geoid are dropped; all of them lack
coordinates entirely, so no method could place them.

Boundaries come from the Census 2024 cartographic file rather than plotly's
counties GeoJSON, which predates Connecticut's 2022 switch from counties to
planning regions and silently drops ~69 CT synagogues.

Note on sample size: the fill is the raw percentage and does NOT account for n.
243 of the ~522 mapped counties contain exactly one crawled synagogue, so their
colour can only ever be 0% or 100%. Counts per county are in the CSV and the
summary block of figure_statistics.md; use --min-sample to drop thin counties.

Outputs (into the --source output dir):
  county_action_map.html  — interactive
  county_action_map.png   — static
  county_action_map.csv   — per-county numerator / denominator / fraction
  county_action_map.json  — counts for figure_statistics.md

Usage:
    python 11_analysis/county_action_map.py [--source page_chunks] [--min-sample N]
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

# Census cartographic boundaries, same fetch pattern as
# backfill_congressional_district_spatial.py. NOT the plotly datasets counties
# file: that one predates Connecticut's 2022 switch from counties to planning
# regions (FIPS 09110-09190) and silently drops ~69 CT synagogues, plus USVI.
# GEOID is the 5-digit county FIPS.
COUNTIES_SHP = ("/vsizip/vsicurl/https://www2.census.gov/geo/tiger/GENZ2024/"
                "shp/cb_2024_us_county_500k.zip")



def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_county_fractions() -> tuple[pd.DataFrame, int]:
    """Per-county numerator/denominator/fraction, plus the count we had to drop."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        WITH crawled AS (   -- synagogues with >=1 successfully crawled site
            SELECT DISTINCT s.id, substr(s.census_tract_geoid, 1, 5) AS county_fips
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            WHERE s.census_tract_geoid IS NOT NULL
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
        SELECT c.county_fips,
               COUNT(DISTINCT c.id) AS denominator,
               COUNT(DISTINCT a.id) AS numerator
        FROM crawled c
        LEFT JOIN acted a ON a.id = c.id
        GROUP BY c.county_fips
        ORDER BY c.county_fips
    """)
    rows = cur.fetchall()

    # crawled synagogues we cannot place (no tract geoid at all)
    cur.execute(f"""
        SELECT COUNT(DISTINCT s.id)
        FROM synagogues s
        JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
        WHERE s.census_tract_geoid IS NULL
          AND {excluded_ids_sql("s")}
    """)
    dropped = cur.fetchone()[0]
    cur.close()
    conn.close()

    df = pd.DataFrame(rows, columns=["county_fips", "denominator", "numerator"])
    df["fraction"] = df["numerator"] / df["denominator"]
    df["pct"] = df["fraction"] * 100
    return df, dropped


def load_counties():
    """County GeoDataFrame, boundary GeoJSON (keyed by FIPS), centroid table."""
    import geopandas as gpd

    gdf = gpd.read_file(COUNTIES_SHP,
                        columns=["GEOID", "NAME", "STATEFP", "geometry"])
    gdf = gdf.to_crs(4326).rename(columns={"GEOID": "county_fips"})
    # Simplify for a much smaller HTML; tolerance is well below county scale.
    gdf["geometry"] = gdf["geometry"].simplify(0.01, preserve_topology=True)

    geojson = json.loads(gdf.set_index("county_fips").to_json())

    # EPSG:5070 (CONUS Albers equal-area) so the point is geometrically sane;
    # representative_point() guarantees the marker lands inside the polygon
    # even for concave counties.
    pts = gdf.to_crs(5070).representative_point().to_crs(4326)
    cent = pd.DataFrame({
        "county_fips": gdf["county_fips"].values,
        "name": gdf["NAME"].values,
        "state_fips": gdf["STATEFP"].values,
        "lon": pts.x.values,
        "lat": pts.y.values,
    })
    return gdf, geojson, cent


def build_map(gdf, df: pd.DataFrame, geojson: dict) -> go.Figure:
    """Plain choropleth: fill = % of crawled synagogues with >=1 action."""
    fig = go.Figure()

    have = set(df["county_fips"])
    blank = [f["id"] for f in geojson["features"] if f["id"] not in have]

    # Layer 1 — counties with no crawled synagogue.
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

    # Layer 2 — counties with data, filled by raw percentage.
    fig.add_trace(go.Choropleth(
        geojson=geojson,
        locations=df["county_fips"],
        featureidkey="id",
        z=df["pct"],
        zmin=0, zmax=100,
        colorscale="YlGnBu",
        marker_line_color="white",
        marker_line_width=0.25,
        colorbar=dict(title="% with<br>action"),
        text=[
            f"{r['name']} County<br>"
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
                    help="drop counties with fewer than N crawled synagogues "
                         "(default 0 — bubble area already encodes n)")
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    df, dropped = load_county_fractions()
    stats = {
        "n_counties": int(len(df)),
        "n_synagogues": int(df["denominator"].sum()),
        "n_counties_n1": int((df["denominator"] == 1).sum()),
        "n_counties_ge5": int((df["denominator"] >= 5).sum()),
        "max_n": int(df["denominator"].max()),
        "n_dropped": int(dropped),
        "min_sample": args.min_sample,
        "source": rc.get_source(),
    }
    print(f"Counties: {stats['n_counties']}  |  synagogues placed: "
          f"{stats['n_synagogues']}  |  n=1: {stats['n_counties_n1']}  |  "
          f"n>=5: {stats['n_counties_ge5']}  |  max: {stats['max_n']}")
    print(f"Crawled synagogues without a tract geoid (unplaceable): {dropped}")

    if args.min_sample > 0:
        before = len(df)
        df = df[df["denominator"] >= args.min_sample]
        print(f"--min-sample {args.min_sample}: kept {len(df)} of {before} counties")

    gdf, geo, cent = load_counties()
    df = df.merge(cent, on="county_fips", how="left")
    unmatched = df["lat"].isna().sum()
    if unmatched:
        print(f"WARNING: {unmatched} counties had no matching geometry; dropped")
        df = df.dropna(subset=["lat", "lon"])

    fig = build_map(gdf, df, geo)

    df.to_csv(OUT / "county_action_map.csv", index=False)
    fig.write_html(OUT / "county_action_map.html")
    fig.write_image(OUT / "county_action_map.png", width=1100, height=700, scale=2)
    (OUT / "county_action_map.json").write_text(json.dumps(stats, indent=2))
    for ext in ("csv", "html", "png", "json"):
        print(f"Wrote {OUT/f'county_action_map.{ext}'}")


if __name__ == "__main__":
    main()
