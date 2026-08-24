"""
filter_vs_ground_truth.py

Runs keyword scan and semantic filter (threshold 0.25) on the 22,386 pages
that exist in both the DB and the human-coded America_results.csv, then
reports precision, recall, and F1 for each approach against ground truth.
"""

import csv
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent
while not (ROOT / ".env").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "05_environmental_classification"))
sys.path.insert(0, str(ROOT / "07_database"))

from keyword_search import load_keywords, find_keywords
from _embed import encode, to_pgvector
import torch

# ── config ────────────────────────────────────────────────────────────────────

THRESHOLD = 0.25

HC_FTS = (
    "hazon | compost | solar | fossil | recycl | carbon | "
    "geotherm | hvac | photovoltaic | tashchit | tu_bishvat | emiss"
)

QUERY_TEXTS = [
    "environmental stewardship worship service land acknowledgment earth day sermon prayer climate",
    "community garden pollinator volunteering environmental cleanup tree planting outreach collaboration youth",
    "biodegradable non-toxic cleaning products sustainability coordinator procurement FSC certified recycled paper signage",
    "utility tracking energy consumption electric vehicle charging renewable energy transition solar panels",
    "low-flush toilets grey water rain barrels water conservation cistern watering schedule runoff",
    "composting recycling plastic reduction single-use elimination reusable containers waste municipal program",
    "vegetarian vegan organic local food fair trade reusable dishes farmers market food justice seasonal",
    "environmental justice climate justice Indigenous rights political advocacy discussion group land acknowledgment",
    "tikkun olam hazon bal tashchit eco-friendly carbon footprint green synagogue environmental action",
]

CATS = [
    "Spirituality & Worship", "Community", "Operations & Maintenance",
    "Energy", "Water", "Waste", "Kitchen", "Environmental & Climate Justice",
]

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

def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0
    r = tp / (tp + fn) if (tp + fn) else 0
    f = 2*p*r / (p+r) if (p+r) else 0
    return p, r, f

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    # ── 1. Build ground-truth map ─────────────────────────────────────────────
    print("Loading ground truth…", flush=True)

    with open(ROOT / "09_combined/synagogues_combined.csv", encoding="utf-8") as f:
        pipeline_domains = {norm_domain(r["website"]) for r in csv.DictReader(f) if r.get("website")}
    with open(ROOT / "America_Orig.csv", encoding="latin-1") as f:
        human_domains = {norm_domain(r["Website"]) for r in csv.DictReader(f) if r.get("Website")}
    pipeline_domains.discard(""); human_domains.discard("")
    overlap = pipeline_domains & human_domains

    def has_action(r):
        return any(r.get(c, "").strip() not in ("FALSE", "") for c in CATS)

    with open(ROOT / "America_results.csv", encoding="latin-1") as f:
        all_results = list(csv.DictReader(f))

    # path variant → (gt_label, result_row)
    gt_map  = {}
    row_map = {}
    for r in all_results:
        if norm_domain(r.get("Link", "")) not in overlap:
            continue
        base = url_to_path(r.get("Link", ""))
        if not base:
            continue
        label = has_action(r)
        for v in [base, base.rstrip("/"), base.rstrip("/")+"/index.html", base.rstrip("/")+".html"]:
            gt_map[v]  = label
            row_map[v] = r

    print(f"  GT path variants: {len(gt_map):,}", flush=True)

    # ── 2. Fetch matched pages ────────────────────────────────────────────────
    print("Fetching matched pages from DB…", flush=True)
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT wp.id, wp.page_path, wp.content_text
        FROM web_pages wp
        WHERE wp.page_path = ANY(%s)
          AND wp.content_text IS NOT NULL AND wp.content_text != ''
    """, (list(gt_map.keys()),))
    rows = cur.fetchall()

    page_data = {}  # id → (path, text, gt_bool)
    for pid, path, text in rows:
        if path in gt_map:
            page_data[pid] = (path, text, gt_map[path])

    gt_pos = {pid for pid, (_, _, gt) in page_data.items() if gt}
    gt_neg = {pid for pid, (_, _, gt) in page_data.items() if not gt}
    print(f"  Pages fetched : {len(page_data):,}", flush=True)
    print(f"  GT positive   : {len(gt_pos):,}", flush=True)
    print(f"  GT negative   : {len(gt_neg):,}", flush=True)

    # ── 3. Keyword scan ───────────────────────────────────────────────────────
    print("Running keyword scan…", flush=True)
    pattern = load_keywords()
    kw_pos  = {pid for pid, (_, text, _) in page_data.items() if find_keywords(text, pattern)}
    print(f"  Keyword flagged: {len(kw_pos):,}", flush=True)

    # ── 4. Semantic filter ────────────────────────────────────────────────────
    print("Building query vector…", flush=True)
    embs  = encode(QUERY_TEXTS)
    avg   = torch.nn.functional.normalize(embs.mean(dim=0, keepdim=True), dim=-1)[0]
    q_vec = to_pgvector(avg)

    print(f"Running semantic filter (threshold={THRESHOLD})…", flush=True)
    cur.execute("""
        SELECT id FROM web_pages
        WHERE id = ANY(%s)
          AND (
              content_tsv @@ to_tsquery('english', %s)
              OR 1.0 - (embedding <=> %s::vector) >= %s
          )
    """, (list(page_data.keys()), HC_FTS, q_vec, THRESHOLD))
    sem_pos = {r[0] for r in cur.fetchall()}
    print(f"  Semantic flagged: {len(sem_pos):,}", flush=True)
    conn.close()

    # ── 5. Evaluate ───────────────────────────────────────────────────────────
    def evaluate(flagged, label):
        tp = len(flagged & gt_pos)
        fp = len(flagged & gt_neg)
        fn = len(gt_pos - flagged)
        tn = len(gt_neg - flagged)
        p, r, f = prf(tp, fp, fn)
        print(f"\n── {label} {'─'*(58-len(label))}")
        print(f"  Flagged : {len(flagged):,}  |  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
        print(f"  Precision : {p:.3f}  ({tp}/{tp+fp})")
        print(f"  Recall    : {r:.3f}  ({tp}/{tp+fn})")
        print(f"  F1        : {f:.3f}")

    print(f"\n{'═'*60}")
    print(f"GROUND TRUTH  positive={len(gt_pos)}  negative={len(gt_neg)}  total={len(page_data):,}")
    print(f"{'═'*60}")
    evaluate(kw_pos,           "Keyword scan")
    evaluate(sem_pos,          "Semantic filter (0.25)")
    evaluate(kw_pos | sem_pos, "Union  (keyword OR semantic)")
    evaluate(kw_pos & sem_pos, "Intersection (keyword AND semantic)")

    # ── 6. Per-category recall ────────────────────────────────────────────────
    def per_cat_recall(flagged, label):
        print(f"\n── Per-category recall  [{label}] {'─'*(28-len(label))}")
        for cat in CATS:
            cat_ids = {pid for pid, (path, _, _) in page_data.items()
                       if row_map.get(path, {}).get(cat, "").strip() not in ("FALSE", "")}
            if not cat_ids:
                continue
            tp = len(flagged & cat_ids)
            r  = tp / len(cat_ids)
            bar = "█" * int(r * 20)
            print(f"  {cat:<42s}  {r:.2f}  ({tp:>3}/{len(cat_ids)})  {bar}")

    per_cat_recall(kw_pos,  "keyword")
    per_cat_recall(sem_pos, "semantic")


if __name__ == "__main__":
    main()
