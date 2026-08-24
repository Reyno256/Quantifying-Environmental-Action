"""
review_tool.py — CLI page browser for manual review of filter outputs.

Two modes:

  gt (default)
    Browse ground-truth-positive pages caught by keyword but missed by
    the semantic filter. Mark each as genuine environmental action or
    false positive in the ground truth.
    Output: review_results_gt.csv

  semantic-only
    Browse the top-N highest-cosine pages from the full DB that were NOT
    caught by the keyword filter. Pages shown in random order.
    Output: review_results_sem.csv

Keys (both modes):
  y  Genuine environmental action
  n  Not environmental / false positive
  s  Skip
  q  Save & quit

Usage:
    python review_tool.py
    python review_tool.py --mode gt --category "Spirituality & Worship"
    python review_tool.py --mode semantic-only --top-n 100
    python review_tool.py --mode semantic-only --top-n 100 --threshold 0.25
"""

import argparse
import csv
import os
import random
import sys
import termios
import textwrap
import time
import tty
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import psycopg2
import torch
from dotenv import load_dotenv

ROOT = Path(__file__).parent
while not (ROOT / ".env").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "05_environmental_classification"))
sys.path.insert(0, str(ROOT / "07_database"))
from keyword_search import load_keywords, find_keywords
from _embed import encode

OUT_GT  = Path(__file__).parent / "review_results_gt.csv"
OUT_SEM = Path(__file__).parent / "review_results_sem.csv"

CATS = [
    "Spirituality & Worship", "Community", "Operations & Maintenance",
    "Energy", "Water", "Waste", "Kitchen", "Environmental & Climate Justice",
]
HC_FTS = (
    "hazon | compost | solar | fossil | recycl | carbon | "
    "geotherm | hvac | photovoltaic | tashchit | tu_bishvat | emiss"
)
QUERY_TEXTS = [
    "Include symbols of nature in place of worship gardens or meditation areas",
    "Focus on environmentalism stewardship climate action in worship for religious or community events holidays earth day",
    "Incorporate environmental prayers or songs in worship",
    "Dedicate a worship service to environmentalism",
    "Offer retreats prayers or reflections to reconnect with nature",
    "Use environmentally-friendly alternatives for items used in worship",
    "Promote and facilitate household energy audits and water conservation",
    "Assisted in local environmental cleanup restoration projects or tree planting initiatives",
    "Encouraged car-pooling public transit use and bicycle use",
    "Encourage volunteering initiatives aimed at environmental responsibility reducing carbon footprints",
    "Resources with a nature and environmental stewardship focus are available in the faith community",
    "Encourage members to adopt a greener lifestyle join community environmental projects practice 3 Rs",
    "Planted a community garden for native pollinator-friendly plants and vegetables",
    "Bike racks and carpooling preferential parking spots installed on the faith community property",
    "Hold or support a holiday or summer camp with an environmental stewardship climate theme",
    "Hold educational sessions on environmental stewardship climate action linked to faith tradition",
    "Undertake environmental projects in collaboration with other faith communities or local groups",
    "Engage with local provincial or federal governments to support sustainable public policies",
    "A volunteer coordinator appointed to facilitate carpooling and public transit awareness",
    "Electric vehicle charging stations installed on the property or identified nearby",
    "Create opportunities for younger people to experience and address the planetary crisis",
    "A board committee focusing on environmental and sustainability issues has been established",
    "Have a think twice before printing policy and active paper reduction recycling policy",
    "Have a procurement policy for recycled and FSC certified paper",
    "A sustainability coordinator and team created developing sustainable practices",
    "Biodegradable non-toxic phosphate-free cleaning products purchased and used within the facility",
    "Make their own eco-friendly cleaning products using natural materials baking soda white vinegar",
    "Have a green maintenance plan using alternatives to salt in winter seeking green yard maintenance",
    "Utility expenditures and energy consumption are tracked and documented",
    "Utility data analyzed regularly to identify high consumption patterns and reduce consumption",
    "An energy audit completed by a trained professional auditor resolving identified issues",
    "Signage posted at all light switches reminds people to turn off lights",
    "Energy efficient lighting LEDs installed dimmers timers and motion sensors where possible",
    "Programmable thermostats in use with winter summer set-back temperatures",
    "Heating cooling system regular maintenance schedule furnace filters cleaned ducts clean",
    "High-efficiency heating cooling or solar hot water system installed",
    "Ceiling fans are in regular use",
    "Usage of caulk and spray foam to air-seal the building",
    "Weather stripping on windows and doors has been upgraded",
    "Replaced fossil fuel burning heating system with air or ground source heat pump",
    "Insulation added to walls and roof",
    "Investigated the possibility of installing a renewable energy system",
    "Installed a renewable energy system in their facility",
    "Low-flow aerators on all faucets signs advising people to limit water usage no leaky faucets",
    "Systems that limit total hot water usage hot water heat recovery system pre-mixing",
    "Steps taken such as insulating hot water tanks and pipes",
    "Installed high-efficiency hot water heater on-demand unit or solar domestic hot water heating",
    "Low-flush toilets and urinals installed where possible throughout the facility",
    "Grey water system or large cistern installed to capture rainwater for building uses",
    "Exterior watering of grounds only occurs in morning or evening shut off when raining",
    "Grounds landscaped with native pollinator plants xeriscaping or installed a rain garden",
    "Rain barrels installed to collect roof run-off water for watering the grounds",
    "Blue bins can be found in every area signs posted near bins about what can be recycled",
    "Recycling facilities available for dropping off ink cartridges cell phones CFL bulbs",
    "Items such as eye glasses and clothes donated for re-use or re-distribution",
    "Have a backyard composting system for yard waste and kitchen waste",
    "Composts regularly through their municipal composting program",
    "Reusable or cloth bags used while shopping to reduce plastic waste",
    "Fruits and vegetables selected from a bin rather than styrofoam trays shrink-wrapped plastic",
    "A waste reduction strategy planned and implemented tracking total waste against targets",
    "Locally sourced food and drinks used when possible",
    "Vegetarian vegan organic and in-season food options provided and served",
    "Reusable recyclable and compostable dishes and cutlery in regular use",
    "A policy of using washable dishes and cutlery during events is in place",
    "Single-use dishes and cutlery have been completely eliminated from use",
    "Promotes fair trade through a purchasing policy and by offering fairly traded products",
    "Community garden regularly provides fresh vegetables to events or local food programs",
    "Support local food initiatives hosting a local farmers market or food-related events",
    "Studied its faith traditions recent statements on fossil fuel divestment",
    "Formed an environmental justice discussion group climate justice incorporated into services",
    "Met with political leaders to urge them to honour rights of Indigenous peoples to clean environment",
    "Researched traditional territory and treaty incorporates land acknowledgement into services",
    "Hosted a workshop on environmental issues facing Indigenous communities today",
    "Members provided with educational materials about fossil fuel divestment",
    "Hosts environmental climate justice events screenings workshops study sessions",
    "Reviewed investments and agreed to divest from fossil fuel investments or invest in renewable energy",
    "Met with political leaders to call for action on climate change participating in climate justice action",
    "Other environmental action",
]

SEED = 42

# ── helpers ───────────────────────────────────────────────────────────────────

def norm_domain(url):
    if not url or not url.strip(): return ""
    url = url.strip().lower().rstrip("/")
    if not url.startswith("http"): url = "http://" + url
    try:
        d = urlparse(url).netloc
        return d[4:] if d.startswith("www.") else d
    except: return ""

def url_to_path(url):
    url = url.strip()
    if not url.startswith("http"): return ""
    p = urlparse(url)
    domain = p.netloc
    domain = domain[4:] if domain.startswith("www.") else domain
    path = p.path.lstrip("/")
    return f"{domain}/{path}" if path else domain

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

def clear():
    os.system("clear")

def wrap(text, width=88, indent=4):
    prefix = " " * indent
    return "\n".join(
        textwrap.fill(line, width=width, initial_indent=prefix,
                      subsequent_indent=prefix)
        for line in text.splitlines()
    ) or prefix + "(empty)"

def parse_vec(s):
    return np.fromstring(s.strip("[]"), sep=",", dtype=np.float32)

def content_snippet(text, pattern, window=700):
    """Snippet centred on first keyword hit, or first N chars if none."""
    m = pattern.search(text.lower())
    if not m:
        return text[:window]
    start = max(0, m.start() - window // 2)
    end   = min(len(text), m.end() + window // 2)
    snippet = text[start:end]
    if start > 0: snippet = "..." + snippet
    if end < len(text): snippet = snippet + "..."
    return snippet

def load_done(out_csv):
    if not out_csv.exists(): return set()
    with open(out_csv, newline="", encoding="utf-8") as f:
        return {r["page_id"] for r in csv.DictReader(f) if r.get("page_id")}

def save_result(row, out_csv):
    exists = out_csv.exists()
    with open(out_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["page_id","path","cos_score",
                                           "best_phrase","verdict"])
        if not exists: w.writeheader()
        w.writerow(row)

# ── display ───────────────────────────────────────────────────────────────────

def render(entry, idx, total, done_count, mode):
    clear()
    W = 92
    label_y = "Genuine environmental action"
    label_n = "Not environmental / false positive"
    if mode == "gt":
        label_y = "Genuine env. action  (semantic missed a real positive)"
        label_n = "False positive in ground truth  (keyword / coder error)"

    print("=" * W)
    print(f"  Reviewed: {done_count}   Remaining: {total - idx}   Total: {total}   [{mode}]")
    print("=" * W)
    print(f"  Path       : {entry['path']}")
    print(f"  Cos score  : {entry['cos_score']:.4f}  (threshold {entry['threshold']})")
    print(f"  Best phrase: {entry['best_phrase']}")
    if entry.get("kw_matches"):
        print(f"  KW matches : {', '.join(entry['kw_matches'])}")
    print()

    if entry.get("annotations"):
        print("  Human-coded categories:")
        for cat, val in entry["annotations"].items():
            print(f"    [{cat}]")
            print(wrap(val, width=86, indent=6))
        print()

    print("-" * W)
    print("  Content snippet:")
    print(wrap(entry["snippet"], width=88, indent=4))
    print()
    print("-" * W)
    print()
    print(f"    y  {label_y}")
    print(f"    n  {label_n}")
    print(f"    s  Skip")
    print(f"    q  Save & quit")
    print()
    print("-" * W)
    print("  Key: ", end="", flush=True)


# ── queue builders ────────────────────────────────────────────────────────────

def build_gt_queue(conn, args, embs_matrix):
    """Pages that are GT-positive, keyword-caught, semantic-missed."""
    with open(ROOT / "09_combined/synagogues_combined.csv", encoding="utf-8") as f:
        pipeline_domains = {norm_domain(r["website"]) for r in csv.DictReader(f) if r.get("website")}
    with open(ROOT / "America_Orig.csv", encoding="latin-1") as f:
        human_domains = {norm_domain(r["Website"]) for r in csv.DictReader(f) if r.get("Website")}
    overlap = (pipeline_domains - {""}) & (human_domains - {""})

    def has_action(r):
        return any(r.get(c, "").strip() not in ("FALSE", "") for c in CATS)

    with open(ROOT / "America_results.csv", encoding="latin-1") as f:
        all_results = list(csv.DictReader(f))

    gt_map = {}; row_map = {}
    for r in all_results:
        if norm_domain(r.get("Link", "")) not in overlap: continue
        base = url_to_path(r.get("Link", ""))
        if not base: continue
        label = has_action(r)
        for v in [base, base.rstrip("/"), base.rstrip("/")+"/index.html",
                  base.rstrip("/")+".html"]:
            gt_map[v] = label; row_map[v] = r

    cur = conn.cursor()
    cur.execute("""
        SELECT wp.id, wp.page_path, wp.content_text, wp.embedding::text
        FROM web_pages wp
        WHERE wp.page_path = ANY(%s)
          AND wp.content_text IS NOT NULL AND wp.content_text != ''
          AND wp.embedding IS NOT NULL
    """, (list(gt_map.keys()),))
    rows = cur.fetchall()
    cur.close()

    pattern = load_keywords()
    pages = []
    for pid, path, text, vec_str in rows:
        gt = gt_map.get(path)
        if not gt: continue
        kws = find_keywords(text, pattern)
        if not kws: continue       # must be keyword-positive
        vec   = parse_vec(vec_str)
        sims  = embs_matrix @ vec
        best  = int(sims.argmax())
        score = float(sims[best])
        if score >= args.threshold: continue  # must be semantic-missed
        ann = {c: row_map[path].get(c, "").strip()
               for c in CATS if row_map[path].get(c, "").strip() not in ("FALSE", "")}
        if args.category and not ann.get(args.category):
            continue
        pages.append({
            "page_id":    pid,
            "path":       path,
            "cos_score":  score,
            "best_phrase":QUERY_TEXTS[best],
            "kw_matches": kws,
            "annotations":ann,
            "snippet":    content_snippet(text, pattern),
            "threshold":  args.threshold,
        })
    return pages


def build_semantic_only_queue(conn, args, embs_matrix):
    """Top-N highest-cosine pages from the overlap sites not caught by keyword."""
    # Restrict to sites in both the pipeline list and the human-generated list
    with open(ROOT / "09_combined/synagogues_combined.csv", encoding="utf-8") as f:
        pipeline_domains = {norm_domain(r["website"]) for r in csv.DictReader(f) if r.get("website")}
    with open(ROOT / "America_Orig.csv", encoding="latin-1") as f:
        human_domains = {norm_domain(r["Website"]) for r in csv.DictReader(f) if r.get("Website")}
    overlap = (pipeline_domains - {""}) & (human_domains - {""})
    print(f"Restricting to {len(overlap)} overlap domains…", flush=True)

    print("Fetching embeddings for overlap sites from DB…", flush=True)
    pattern = load_keywords()

    cur = conn.cursor("sem_cursor")
    cur.execute("""
        SELECT wp.id, wp.page_path, wp.content_text, wp.embedding::text
        FROM web_pages wp
        JOIN websites w ON w.id = wp.website_id
        WHERE w.domain = ANY(%s)
          AND wp.embedding IS NOT NULL
          AND wp.content_text IS NOT NULL AND wp.content_text != ''
    """, (list(overlap),))

    candidates = []
    processed  = 0
    batch_size = 5_000

    while True:
        batch = cur.fetchmany(batch_size)
        if not batch: break
        for pid, path, text, vec_str in batch:
            if find_keywords(text, pattern):
                continue           # skip keyword matches
            vec   = parse_vec(vec_str)
            sims  = embs_matrix @ vec
            best  = int(sims.argmax())
            score = float(sims[best])
            if score >= args.threshold:
                candidates.append((score, best, pid, path, text))
        processed += len(batch)
        print(f"  {processed:,} pages scanned, {len(candidates):,} candidates…",
              end="\r", flush=True)

    cur.close()
    print(f"\n  Done. {len(candidates):,} semantic-only candidates above threshold {args.threshold}.")

    # Sort by score desc, take top-N, then shuffle
    candidates.sort(reverse=True)
    top = candidates[:args.top_n]
    random.seed(SEED)
    random.shuffle(top)

    pages = []
    for score, best_idx, pid, path, text in top:
        pages.append({
            "page_id":    pid,
            "path":       path,
            "cos_score":  score,
            "best_phrase":QUERY_TEXTS[best_idx],
            "kw_matches": [],
            "annotations":{},
            "snippet":    text[:700],
            "threshold":  args.threshold,
        })
    return pages


# ── review loop ───────────────────────────────────────────────────────────────

def run_review(queue, out_csv, mode, done):
    done_count = len(done)
    remaining  = [e for e in queue if str(e["page_id"]) not in done]

    if not remaining:
        print(f"  All {len(queue)} pages already reviewed. Nothing to do.")
        return

    print(f"\n  {len(done)} already reviewed,  {len(remaining)} remaining.")
    print("  Press any key to start…", end="", flush=True)
    getch()

    for idx, entry in enumerate(remaining):
        while True:
            render(entry, idx, len(remaining), done_count, mode)
            key = getch()
            if key in ("y", "Y"):    verdict = "genuine"
            elif key in ("n", "N"):  verdict = "false_positive"
            elif key in ("s", "S"):  verdict = "skip"
            elif key in ("q", "Q"):
                clear()
                print(f"\n  Saved & quit. {done_count} reviewed total.\n")
                return
            else:
                time.sleep(0.4); continue
            break

        if verdict != "skip":
            save_result({
                "page_id":    entry["page_id"],
                "path":       entry["path"],
                "cos_score":  f"{entry['cos_score']:.4f}",
                "best_phrase":entry["best_phrase"],
                "verdict":    verdict,
            }, out_csv)
            done_count += 1

    clear()
    print(f"\n  Done! {done_count} pages reviewed -> {out_csv}\n")

    with open(out_csv, newline="", encoding="utf-8") as f:
        verdicts = [r["verdict"] for r in csv.DictReader(f)]
    genuine = verdicts.count("genuine")
    fp      = verdicts.count("false_positive")
    total_v = genuine + fp
    if total_v:
        print(f"  Genuine environmental  : {genuine} ({genuine/total_v*100:.0f}%)")
        print(f"  Not environmental      : {fp} ({fp/total_v*100:.0f}%)")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["gt", "semantic-only"],
                        default="gt",
                        help="gt: keyword-caught/semantic-missed GT pages; "
                             "semantic-only: top-N high-cosine non-keyword pages")
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--top-n", type=int, default=100,
                        help="Semantic-only mode: how many top pages to review")
    parser.add_argument("--category", default=None,
                        help="GT mode: filter to one category")
    args = parser.parse_args()

    print(f"Building query matrix ({len(QUERY_TEXTS)} rubric phrases)…", flush=True)
    raw_embs   = encode(QUERY_TEXTS)
    embs_matrix= torch.nn.functional.normalize(raw_embs, dim=-1).numpy()

    conn = get_conn()

    if args.mode == "gt":
        out_csv = OUT_GT
        print("Building GT review queue…", flush=True)
        queue = build_gt_queue(conn, args, embs_matrix)
    else:
        out_csv = OUT_SEM
        queue   = build_semantic_only_queue(conn, args, embs_matrix)

    conn.close()

    done = load_done(out_csv)
    print(f"  Queue size : {len(queue)}")
    run_review(queue, out_csv, args.mode, done)


if __name__ == "__main__":
    main()
