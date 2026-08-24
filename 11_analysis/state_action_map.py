"""
Choropleth map of the US shading each state by the fraction of synagogues
that have at least one environmental *action*.

Definitions
-----------
* Successfully crawled site: a row in `websites` with has_error = FALSE.
* Action: a row in `llm_chunk_classifications` whose category is not 'N/A'
  (and which classified without error).
* Denominator (per state): synagogues that have >= 1 successfully crawled
  website.
* Numerator   (per state): those synagogues that additionally have >= 1
  action found on a successfully crawled website.

Synagogues with no successfully crawled website are excluded entirely, per
instruction to only use successfully crawled sites.

State geometries are loaded with geopandas (US states GeoJSON), and the map
is rendered with plotly.

Outputs:
  state_action_map.html  — interactive choropleth
  state_action_map.png   — static choropleth
  state_action_map.csv   — per-state numerator / denominator / fraction

Usage:
    python 11_analysis/state_action_map.py
    python 11_analysis/state_action_map.py --gray-low-sample
"""

import argparse
import os
import sys
from pathlib import Path

import geopandas as gpd
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

STATES_GEOJSON = (
    "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/"
    "master/data/geojson/us-states.json"
)

# States with fewer than this many crawled synagogues are greyed out when the
# --gray-low-sample flag is set.
MIN_SAMPLE = 5

# Full state name -> USPS abbreviation (matches the `state` column in the DB).
NAME_TO_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL",
    "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY",
    "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR",
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


def load_state_fractions() -> pd.DataFrame:
    """Per-state numerator / denominator / fraction over crawled synagogues."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        WITH crawled AS (   -- synagogues with >=1 successfully crawled site
            SELECT DISTINCT s.id, s.state
            FROM synagogues s
            JOIN websites w ON w.synagogue_id = s.id AND w.has_error = FALSE
            WHERE s.state IS NOT NULL
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
        SELECT c.state AS abbr,
               COUNT(DISTINCT c.id)  AS denominator,
               COUNT(DISTINCT a.id)  AS numerator
        FROM crawled c
        LEFT JOIN acted a ON a.id = c.id
        GROUP BY c.state
        ORDER BY c.state
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    df = pd.DataFrame(rows, columns=["abbr", "denominator", "numerator"])
    df["fraction"] = df["numerator"] / df["denominator"]
    return df


# ── map ────────────────────────────────────────────────────────────────────────

def build_map(df: pd.DataFrame, gray_low_sample: bool = False):
    # Load state geometries with geopandas and attach USPS abbreviations.
    gdf = gpd.read_file(STATES_GEOJSON)
    gdf["abbr"] = gdf["name"].map(NAME_TO_ABBR)
    gdf = gdf.merge(df, on="abbr", how="left")

    geojson = gdf.set_index("abbr").__geo_interface__

    gdf["pct"] = gdf["fraction"] * 100
    # States to grey out: no crawled synagogues, or (when flagged) below the
    # minimum sample size.
    low = gdf["denominator"].isna()
    if gray_low_sample:
        low = low | (gdf["denominator"] < MIN_SAMPLE)
    gdf["hover"] = gdf.apply(
        lambda r: (
            f"{r['name']}<br>"
            f"{int(r['numerator'])}/{int(r['denominator'])} synagogues "
            f"({r['pct']:.1f}%)"
            if pd.notna(r["fraction"]) else f"{r['name']}<br>no crawled synagogues"
        ),
        axis=1,
    )

    colored = gdf[~low]
    grayed = gdf[low]

    fig = go.Figure()
    if not grayed.empty:
        fig.add_trace(
            go.Choropleth(
                geojson=geojson,
                locations=grayed["abbr"],
                featureidkey="id",
                z=[0] * len(grayed),
                colorscale=[[0, "lightgray"], [1, "lightgray"]],
                showscale=False,
                marker_line_color="white",
                marker_line_width=0.5,
                text=grayed["hover"],
                hovertemplate="%{text}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Choropleth(
            geojson=geojson,
            locations=colored["abbr"],
            featureidkey="id",
            z=colored["pct"],
            zmin=0,
            zmax=colored["pct"].max(),
            colorscale="YlGnBu",
            marker_line_color="white",
            marker_line_width=0.5,
            colorbar=dict(title="% with<br>action"),
            text=colored["hover"],
            hovertemplate="%{text}<extra></extra>",
        )
    )
    fig.update_geos(scope="usa")
    fig.update_layout(
        font=dict(size=rc.FS_PLOTLY),   # central AXIS_FONT_SIZE typography
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--gray-low-sample",
        action="store_true",
        help=f"Grey out states with fewer than {MIN_SAMPLE} crawled synagogues "
             "and write to a separate filename.",
    )
    rc.add_source_arg(ap)
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    df = load_state_fractions()

    total_num = int(df["numerator"].sum())
    total_den = int(df["denominator"].sum())
    print(f"States: {len(df)}")
    print(f"Overall: {total_num}/{total_den} = {total_num / total_den:.3f}")
    if args.gray_low_sample:
        n_low = int((df["denominator"] < MIN_SAMPLE).sum())
        print(f"Greying out {n_low} states with <{MIN_SAMPLE} crawled synagogues")

    stem = "state_action_map_graylow" if args.gray_low_sample else "state_action_map"

    df.to_csv(OUT / f"{stem}.csv", index=False)

    fig = build_map(df, gray_low_sample=args.gray_low_sample)
    fig.write_html(OUT / f"{stem}.html")
    fig.write_image(OUT / f"{stem}.png", width=1100, height=700, scale=2)
    print(f"Wrote {OUT/f'{stem}.png'}")
    print(f"Wrote {OUT/f'{stem}.html'}")
    print(f"Wrote {OUT/f'{stem}.csv'}")


if __name__ == "__main__":
    main()
