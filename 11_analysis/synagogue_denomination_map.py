"""
Point cloud of synagogues across the United States, coloured by denomination.

Each synagogue with coordinates and a known canonical denomination
(synagogues.denomination_canonical, excluding NULL / 'Unknown') is plotted at
its lat/lon and coloured by that denomination. Unlike the action point cloud
this is a *categorical* map, so each denomination gets its own colour and its
own (legend-toggleable) trace in the interactive version.

Denomination is a property of the synagogue record independent of crawling, so
the full geocoded+denominated population is shown here (no crawl filter and no
action-outlier exclusion — those only matter for action-count analyses).

Outputs:
  synagogue_denomination_map.png   — static map (CONUS) with state outlines
  synagogue_denomination_map.html  — interactive plotly scattergeo (zoomable)

Usage:
    python 11_analysis/synagogue_denomination_map.py
"""

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402

rc.apply_font_sizes(plt)     # central AXIS_FONT_SIZE typography

STATES_GEOJSON = ("https://raw.githubusercontent.com/PublicaMundi/MappingAPI/"
                  "master/data/geojson/us-states.json")

# Fixed denomination order + colour-blind-aware qualitative palette, roughly
# arranged liberal -> traditional so neighbouring colours group sensibly.
DENOM_ORDER = [
    "Reconstructionist",
    "Jewish Renewal",
    "Reform",
    "Humanistic",
    "Non-Denominational Progressive",
    "Conservative",
    "Non-Denominational Conservative",
    "Orthodox",
    "Chabad",
]
# Modern Orthodox is folded into Orthodox at load time (DB unchanged).
DENOM_MERGE = {"Modern Orthodox": "Orthodox"}
DENOM_COLORS = {
    "Reconstructionist":               "#1b9e77",
    "Jewish Renewal":                  "#66c2a5",
    "Reform":                          "#1f78b4",
    "Humanistic":                      "#a6cee3",
    "Non-Denominational Progressive":  "#6a3d9a",
    "Conservative":                    "#ff7f00",
    "Non-Denominational Conservative": "#fdbf6f",
    "Orthodox":                        "#b15928",
    "Chabad":                          "#000000",
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
    cur.execute("""
        SELECT id, name, lat, lon, denomination_canonical
        FROM synagogues
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          AND denomination_canonical IS NOT NULL
          AND denomination_canonical <> 'Unknown'
    """)
    df = pd.DataFrame(cur.fetchall(),
                      columns=["id", "name", "lat", "lon", "denomination"])
    df["denomination"] = df["denomination"].replace(DENOM_MERGE)  # Modern Orthodox -> Orthodox
    cur.close(); conn.close()
    return df


def main():
    df = load_points()
    counts = df["denomination"].value_counts()
    # any denomination present but not in the fixed order gets appended
    order = [d for d in DENOM_ORDER if d in counts.index]
    order += [d for d in counts.index if d not in DENOM_ORDER]
    print(f"Synagogues plotted: {len(df)} across {len(order)} denominations")
    for d in order:
        print(f"  {counts[d]:5d}  {d}")

    import geopandas as gpd
    states = gpd.read_file(STATES_GEOJSON)

    # ── static PNG ─────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 8))
    states.boundary.plot(ax=ax, color="0.8", linewidth=0.5)
    for d in order:
        sub = df[df["denomination"] == d]
        ax.scatter(sub["lon"], sub["lat"],
                   c=DENOM_COLORS.get(d, "0.5"), s=12, alpha=0.8,
                   linewidths=0, label=f"{d} ({len(sub)})")
    ax.set_xlim(-125, -66); ax.set_ylim(24, 50)
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    ax.legend(loc="lower left", markerscale=1.5, framealpha=0.9,
              title="denomination (n)")
    fig.savefig(HERE / "synagogue_denomination_map.png", dpi=150, bbox_inches="tight")
    print(f"Wrote {HERE/'synagogue_denomination_map.png'}")

    # ── interactive HTML (one trace per denomination -> legend toggles) ─────────
    fig2 = go.Figure()
    for d in order:
        sub = df[df["denomination"] == d]
        fig2.add_trace(go.Scattergeo(
            lon=sub["lon"], lat=sub["lat"], mode="markers",
            name=f"{d} ({len(sub)})",
            marker=dict(size=5, color=DENOM_COLORS.get(d, "#888888"),
                        opacity=0.85, line=dict(width=0)),
            text=[f"{n}<br>{d}" for n in sub["name"]],
            hovertemplate="%{text}<extra></extra>",
        ))
    fig2.update_geos(scope="usa")
    fig2.update_layout(
        font=dict(size=rc.FS_PLOTLY),   # central AXIS_FONT_SIZE typography
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(title="denomination (n)", x=0.01, y=0.99,
                    yanchor="top", xanchor="left", bgcolor="rgba(255,255,255,0.8)"),
    )
    fig2.write_html(HERE / "synagogue_denomination_map.html")
    print(f"Wrote {HERE/'synagogue_denomination_map.html'}")


if __name__ == "__main__":
    main()
