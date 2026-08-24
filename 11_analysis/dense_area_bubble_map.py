"""
Two-resolution map of environmental action: bubbles wherever synagogue dots
would visually overlap at the rendered scale, individual dots everywhere else.

Bubbles
-------
Rather than a fixed list of metro areas, bubbles are formed dynamically: every
crawled, coordinate-having synagogue is projected into the same Web Mercator
pixel space Leaflet itself uses at the zoom level this map's `fit_bounds` will
land on (see `compute_fit_zoom`), then clustered with DBSCAN using a pixel-
distance threshold (`CLUSTER_EPS_PX`) approximating "these dots would overlap
on screen." Any cluster with >= `MIN_CLUSTER_SIZE` points becomes one bubble:
bubble AREA (not radius) is proportional to synagogue count, bubble FILL COLOR
is the percentage of that cluster's synagogues with >=1 environmental action.
This naturally produces many small bubbles for small/mid-size cities and a few
large ones for the biggest metros, with no hand-maintained city list — see
KNOWN_DATA_ISSUES.md for why this is scale-dependent (tied to the fixed
MAP_WIDTH_PX/MAP_HEIGHT_PX render size below).

Dots
----
Every synagogue DBSCAN leaves as noise (not close enough to >= MIN_CLUSTER_SIZE
neighbors to form a bubble) gets its own small dot, colored by whether it has
>=1 action.

Rendering
---------
Built with Folium (real Leaflet.js, unlike every other interactive map in this
repo, which is Plotly). The tracked deliverable is a static PNG: the Folium
HTML is rendered headless in Chrome (via selenium) and screenshotted. This is
the ONLY figure in `11_analysis` that needs live internet access at
regeneration time (to fetch CartoDB basemap tiles) in addition to the DB
tunnel every other script needs. No on-map legend or zoom control — kept
deliberately minimal; bubble/dot semantics are documented here and in
figure_statistics.md instead.

Outputs (into the --source output dir):
  dense_area_bubble_map.html   interactive Folium map
  dense_area_bubble_map.png    static screenshot (primary tracked figure)
  dense_area_bubble_map.json   per-cluster + outside-area counts, for
                                figure_statistics.md

Usage:
    python 11_analysis/dense_area_bubble_map.py [--source page_chunks] [--tile-wait 3.0]
        [--color-by {pct_action,mean_actions}] [--radius-scale {sqrt,log}] [--legend]

    Non-default --color-by/--radius-scale/--legend combos write to a suffixed
    filename (e.g. dense_area_bubble_map_mean_actions_legend.png) rather than
    the default dense_area_bubble_map.png, so variants can be compared side by
    side without overwriting the tracked figure.
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import folium
from branca.colormap import linear
from dotenv import load_dotenv
from sklearn.cluster import DBSCAN

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
HERE = Path(__file__).parent
OUT = HERE          # reassigned in main() from --source
sys.path.insert(0, str(HERE / "recreate_existing"))
import recreate_common as rc  # noqa: E402
from recreate_common import excluded_ids_sql  # noqa: E402

# Same contiguous-US box the original crawl was scoped to.
US_LAT = (24.0, 49.0)
US_LON = (-125.0, -66.0)

MAP_WIDTH_PX = 1400
MAP_HEIGHT_PX = 800
TILE_SIZE = 256          # Leaflet's default tile size, in px

# Two dots whose projected centers land within this many px of each other are
# treated as "would visually overlap" at the fit zoom computed below. Tuned by
# eye against the rendered PNG, not derived analytically.
CLUSTER_EPS_PX = 10
MIN_CLUSTER_SIZE = 3     # fewer than this stays individual dots, not a bubble

MIN_RADIUS_PX = 10
MAX_RADIUS_PX = 46
ACTION_COLOR = "#238b45"     # outside dot, took >=1 action
NO_ACTION_COLOR = "#969696"  # outside dot, no action


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def load_points() -> pd.DataFrame:
    """One row per crawled, coordinate-having synagogue: id, name, lat, lon,
    n_actions (0 if none). Same population as synagogue_action_pointcloud.py."""
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


def _mercator_xy(lat: float, lon: float) -> tuple[float, float]:
    """Normalized (x, y) in [0, 1] x [0, 1], standard Web Mercator (EPSG:3857)
    as used by every slippy-map tile scheme, including Leaflet's default."""
    x = (lon + 180.0) / 360.0
    lat_rad = math.radians(lat)
    y = 0.5 - math.log(math.tan(math.pi / 4 + lat_rad / 2)) / (2 * math.pi)
    return x, y


def compute_fit_zoom(width_px: int, height_px: int,
                      lat_range: tuple, lon_range: tuple) -> int:
    """The zoom level Leaflet's `fit_bounds` will settle on for this bbox and
    container size. Leaflet computes a continuous best-fit zoom then snaps
    DOWN to the nearest integer (default zoomSnap=1) so the bounds are
    guaranteed to still fit — replicate both steps so clustering happens at
    the zoom the final screenshot will actually render at."""
    x0, _ = _mercator_xy(0, lon_range[0])
    x1, _ = _mercator_xy(0, lon_range[1])
    _, y_north = _mercator_xy(lat_range[1], 0)
    _, y_south = _mercator_xy(lat_range[0], 0)
    bbox_w, bbox_h = (x1 - x0), (y_south - y_north)
    scale = min(width_px / (bbox_w * TILE_SIZE), height_px / (bbox_h * TILE_SIZE))
    return math.floor(math.log2(scale))


def _project_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    x, y = _mercator_xy(lat, lon)
    world_px = TILE_SIZE * (2 ** zoom)
    return x * world_px, y * world_px


def cluster_points(df: pd.DataFrame) -> tuple[list[dict], pd.DataFrame, int]:
    """DBSCAN over each point's on-screen pixel position at the zoom this map
    will actually render at. Returns (bubbles, outside_df, zoom_used)."""
    zoom = compute_fit_zoom(MAP_WIDTH_PX, MAP_HEIGHT_PX, US_LAT, US_LON)
    coords = np.array([_project_px(lat, lon, zoom)
                        for lat, lon in zip(df["lat"], df["lon"])])
    labels = DBSCAN(eps=CLUSTER_EPS_PX, min_samples=MIN_CLUSTER_SIZE).fit_predict(coords)
    df = df.copy()
    df["cluster"] = labels
    df["took_action"] = df["n_actions"] > 0

    bubbles = []
    for cluster_id, g in df[df["cluster"] != -1].groupby("cluster"):
        bubbles.append({
            "n": int(len(g)),
            "n_with_action": int(g["took_action"].sum()),
            "pct_with_action": round(100 * g["took_action"].mean(), 1),
            # mean action count per synagogue in the cluster, over ALL of its
            # synagogues (incl. zero-action ones) — the "per_synagogue" metric,
            # not "per env-active synagogue" (matches fig_categories_per_synagogue
            # etc. elsewhere in this repo).
            "mean_actions": round(float(g["n_actions"].mean()), 2),
            "centroid_lat": round(float(g["lat"].mean()), 4),
            "centroid_lon": round(float(g["lon"].mean()), 4),
        })
    bubbles.sort(key=lambda b: -b["n"])

    outside = df[df["cluster"] == -1]
    return bubbles, outside, zoom


def build_stats(df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    bubbles, outside, zoom = cluster_points(df)
    stats = {
        "source": rc.get_source(),
        "n_total": int(len(df)),
        "cluster_eps_px": CLUSTER_EPS_PX,
        "min_cluster_size": MIN_CLUSTER_SIZE,
        "fit_zoom_used": zoom,
        "n_bubbles": len(bubbles),
        "buckets": bubbles,
        "n_outside": int(len(outside)),
        "n_outside_with_action": int(outside["took_action"].sum()),
        "pct_outside_with_action": (round(100 * outside["took_action"].mean(), 1)
                                     if len(outside) else None),
    }
    return stats, outside


def bubble_radius(n: int, min_n: int, max_n: int, scale: str = "sqrt") -> float:
    """Radius (px), by one of two scales:
      - "sqrt" (default): drawn AREA proportional to n, standard cartographic
        practice, anchored at 0 (radius 0 would mean n=0).
      - "log": linear in log(n) between the smallest and largest bubble in this
        run, i.e. equal ratios of n get equal radius steps — compresses the
        huge gap between a 1000+ -synagogue metro and a 3-synagogue cluster
        (both survive MIN_CLUSTER_SIZE) far less harshly than sqrt does.
    """
    if max_n <= 0:
        return MIN_RADIUS_PX
    if scale == "log":
        if max_n <= min_n:
            return MAX_RADIUS_PX
        frac = (math.log(n) - math.log(min_n)) / (math.log(max_n) - math.log(min_n))
        return MIN_RADIUS_PX + (MAX_RADIUS_PX - MIN_RADIUS_PX) * frac
    return MIN_RADIUS_PX + (MAX_RADIUS_PX - MIN_RADIUS_PX) * math.sqrt(n / max_n)


def build_map(stats: dict, outside: pd.DataFrame, color_by: str = "pct_action",
              radius_scale: str = "sqrt", legend: bool = False) -> folium.Map:
    center_lat = sum(US_LAT) / 2
    center_lon = sum(US_LON) / 2
    m = folium.Map(location=[center_lat, center_lon], tiles="cartodbpositron",
                    zoom_start=5, zoom_control=False, control_scale=True,
                    width=MAP_WIDTH_PX, height=MAP_HEIGHT_PX)
    m.get_root().html.add_child(folium.Element(
        "<style>body{margin:0;padding:0;}</style>"))
    m.fit_bounds([[US_LAT[0], US_LON[0]], [US_LAT[1], US_LON[1]]])

    if color_by == "mean_actions":
        color_field, color_caption = "mean_actions", "Mean action count per synagogue"
        color_max = max((b["mean_actions"] for b in stats["buckets"]), default=1) or 1
        cmap = linear.YlGnBu_09.scale(0, color_max)
    else:
        color_field, color_caption = "pct_with_action", "% of synagogues with ≥ 1 environmental action"
        cmap = linear.YlGnBu_09.scale(0, 100)
    cmap.caption = color_caption
    cmap.add_to(m)

    ns = [b["n"] for b in stats["buckets"]]
    min_n, max_n = (min(ns), max(ns)) if ns else (1, 1)

    # outside dots first, so bubbles draw on top
    for _, r in outside.iterrows():
        color = ACTION_COLOR if r["took_action"] else NO_ACTION_COLOR
        folium.CircleMarker(
            location=[r["lat"], r["lon"]], radius=3,
            color=None, fill=True, fill_color=color, fill_opacity=0.55,
            weight=0,
            tooltip=f"{r['name']}<br>actions: {int(r['n_actions'])}",
        ).add_to(m)

    for b in stats["buckets"]:
        folium.CircleMarker(
            location=[b["centroid_lat"], b["centroid_lon"]],
            radius=bubble_radius(b["n"], min_n, max_n, radius_scale),
            color="#333333", weight=1, opacity=0.7,
            fill=True, fill_color=cmap(b[color_field]), fill_opacity=0.78,
            tooltip=(f"{b['n']} synagogues<br>"
                     f"{b['pct_with_action']:.0f}% with ≥ 1 action<br>"
                     f"{b['mean_actions']:.2f} mean actions/synagogue"),
        ).add_to(m)

    if legend:
        _add_legend(m, min_n, max_n, radius_scale)
    return m


def _add_legend(m: folium.Map, min_n: int, max_n: int, radius_scale: str) -> None:
    """Bubble-size reference + outside-dot color key, appended after the map's
    own div (fixed pixel size, not "100%") so it renders as a normal block
    below the map instead of a floating overlay that could hide data."""
    ref_counts = sorted({int(max(min_n, round(max_n * f, -1) or min_n))
                          for f in (0.15, 0.5, 1.0)} | {min_n})
    size_items = "".join(
        f'<div style="display:flex;align-items:center;margin-right:22px;">'
        f'<div style="width:{2*bubble_radius(n, min_n, max_n, radius_scale):.0f}px;'
        f'height:{2*bubble_radius(n, min_n, max_n, radius_scale):.0f}px;border-radius:50%;'
        f'background:#cccccc;border:1px solid #333;flex-shrink:0;"></div>'
        f'<span style="margin-left:6px;white-space:nowrap;">{n} synagogues</span></div>'
        for n in ref_counts
    )
    legend_html = f"""
    <div style="width:{MAP_WIDTH_PX}px; box-sizing:border-box; background:white;
                padding: 10px 16px; border-top: 1px solid #ccc;
                font-size: 13px; font-family: sans-serif; display:flex;
                align-items:center; flex-wrap:wrap; gap:10px 30px;">
      <div style="display:flex; align-items:center; flex-wrap:wrap;">
        <span style="font-weight:bold; margin-right:14px; white-space:nowrap;">
          Bubble size = synagogue count ({radius_scale} radius)</span>
        {size_items}
      </div>
      <div style="display:flex; align-items:center;">
        <span style="font-weight:bold; margin-right:14px; white-space:nowrap;">
          Outside dots</span>
        <div style="display:flex;align-items:center;margin-right:16px;">
          <div style="width:10px;height:10px;border-radius:50%;
                      background:{ACTION_COLOR};flex-shrink:0;"></div>
          <span style="margin-left:6px;white-space:nowrap;">&ge; 1 action</span></div>
        <div style="display:flex;align-items:center;">
          <div style="width:10px;height:10px;border-radius:50%;
                      background:{NO_ACTION_COLOR};flex-shrink:0;"></div>
          <span style="margin-left:6px;white-space:nowrap;">no action found</span></div>
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


LEGEND_HEIGHT_PX = 110   # empirically enough for one row of the legend bar


def render_png(html_path: Path, png_path: Path, width: int = MAP_WIDTH_PX,
               height: int = MAP_HEIGHT_PX, tile_wait: float = 3.0) -> None:
    """Headless-Chrome screenshot of the saved Folium HTML, cropped to exactly
    (width, height). Needs internet access at run time to fetch basemap tiles
    from the CartoDB tile server.

    Screenshotting the raw viewport at exactly (width, height) isn't reliable:
    Leaflet keeps whole 256px tiles and a zoom-animation "proxy" element in
    the DOM past the container's clipped edge, which can inflate
    document.body.scrollHeight well beyond the map's true 800px (observed
    907px) despite rendering fine visually. Sidestep that entirely by
    rendering into a deliberately taller window, then hard-cropping the
    screenshot with PIL to the intended pixel size — the map div is always
    placed at (0, 0), so this is exact regardless of what Leaflet leaves
    poking out below it."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from PIL import Image

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument(f"--window-size={width},{height + 200}")
    opts.add_argument("--hide-scrollbars")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.set_window_size(width, height + 200)
        driver.get(f"file://{html_path.resolve()}")
        time.sleep(tile_wait)   # let Leaflet tiles + markers finish loading
        raw_path = png_path.with_suffix(".raw.png")
        driver.save_screenshot(str(raw_path))
    finally:
        driver.quit()

    Image.open(raw_path).crop((0, 0, width, height)).save(png_path)
    raw_path.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    rc.add_source_arg(ap)
    ap.add_argument("--tile-wait", type=float, default=3.0,
                     help="seconds to wait for basemap tiles before "
                          "screenshotting (default 3.0)")
    ap.add_argument("--color-by", choices=("pct_action", "mean_actions"),
                     default="pct_action",
                     help="bubble fill: %% with >=1 action (default), or mean "
                          "action count per synagogue in the cluster")
    ap.add_argument("--radius-scale", choices=("sqrt", "log"), default="sqrt",
                     help="bubble radius vs. synagogue count: area-proportional "
                          "sqrt (default), or log-spaced between the smallest "
                          "and largest bubble found")
    ap.add_argument("--legend", action="store_true",
                     help="add the bubble-size / outside-dot legend bar below "
                          "the map (off by default)")
    args = ap.parse_args()
    global OUT
    rc.set_source(args.source)   # must precede excluded_ids_sql()
    OUT = rc.out_dir(HERE)

    # Non-default combos write to a distinctly suffixed filename so a variant
    # run never overwrites the tracked default figure — lets two renders sit
    # side by side for comparison.
    suffix_parts = []
    if args.color_by != "pct_action":
        suffix_parts.append(args.color_by)
    if args.radius_scale != "sqrt":
        suffix_parts.append(f"{args.radius_scale}radius")
    if args.legend:
        suffix_parts.append("legend")
    base_name = "dense_area_bubble_map" + ("_" + "_".join(suffix_parts) if suffix_parts else "")

    df = load_points()
    stats, outside = build_stats(df)
    stats["color_by"] = args.color_by
    stats["radius_scale"] = args.radius_scale
    print(f"Synagogues placed: {stats['n_total']}  |  fit_zoom={stats['fit_zoom_used']}  "
          f"|  {stats['n_bubbles']} bubbles covering "
          f"{sum(b['n'] for b in stats['buckets'])}  |  outside: {stats['n_outside']}")
    for b in stats["buckets"][:15]:
        print(f"  n={b['n']:5d}  {b['pct_with_action']:5.1f}% with action  "
              f"mean={b['mean_actions']:5.2f}  @ ({b['centroid_lat']}, {b['centroid_lon']})")
    if stats["n_bubbles"] > 15:
        print(f"  ... and {stats['n_bubbles'] - 15} more bubbles")

    m = build_map(stats, outside, color_by=args.color_by,
                  radius_scale=args.radius_scale, legend=args.legend)
    html_path = OUT / f"{base_name}.html"
    png_path = OUT / f"{base_name}.png"
    m.save(str(html_path))
    print(f"Wrote {html_path}")

    render_height = MAP_HEIGHT_PX + (LEGEND_HEIGHT_PX if args.legend else 0)
    render_png(html_path, png_path, height=render_height, tile_wait=args.tile_wait)
    print(f"Wrote {png_path}")

    (OUT / f"{base_name}.json").write_text(json.dumps(stats, indent=2))
    print(f"Wrote {OUT / f'{base_name}.json'}")


if __name__ == "__main__":
    main()
