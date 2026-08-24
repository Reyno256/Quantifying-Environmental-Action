"""
classify_union.py

Runs Claude Haiku on the 80% train split of pages selected by the union
of keyword and semantic filters (17,473 pages from the 22,386 matched set),
then reports agreement / disagreement with the human ground truth from
America_results.csv.

Output: union_classifications.csv  (resumable — skips already-classified pages)

Usage:
    python classify_union.py
"""

import csv
import os
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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
sys.path.insert(0, str(ROOT / "06_LLM_categorization"))
sys.path.insert(0, str(ROOT / "07_database"))

from keyword_search import load_keywords, find_keywords
from gemini_as_a_judge import get_response_judge
from _embed import encode

OUT_CSV    = Path(__file__).parent / "union_classifications.csv"
MAX_CHARS  = 6_000
WORKERS    = 5
SEED       = 42
FLUSH_EVERY= 50

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
    "Incorporate environmental prayers or songs in worship","Dedicate a worship service to environmentalism",
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
    "Ceiling fans are in regular use","Usage of caulk and spray foam to air-seal the building",
    "Weather stripping on windows and doors has been upgraded",
    "Replaced fossil fuel burning heating system with air or ground source heat pump",
    "Insulation added to walls and roof","Investigated the possibility of installing a renewable energy system",
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

# ── helpers ───────────────────────────────────────────────────────────────────

def norm_domain(u):
    if not u or not u.strip(): return ""
    u = u.strip().lower().rstrip("/")
    if not u.startswith("http"): u = "http://" + u
    try:
        d = urlparse(u).netloc
        return d[4:] if d.startswith("www.") else d
    except: return ""

def url_to_path(u):
    u = u.strip()
    if not u.startswith("http"): return ""
    p = urlparse(u); d = p.netloc
    d = d[4:] if d.startswith("www.") else d
    path = p.path.lstrip("/")
    return f"{d}/{path}" if path else d

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

def extract_category(label: str) -> str:
    """Map a 76-label response to a top-level category, or 'N/A'."""
    label = label.strip().strip('"')
    if label in ("N/A", ""):
        return "N/A"
    for cat in CATS:
        if label.startswith(cat + "_"):
            return cat
    # NA_ labels: map by checking subcategory position in the rubric list
    # (we treat them as a positive without a specific category)
    if label.startswith("NA_") or label == "NA_Other":
        return "Environmental (subcategory)"
    return label  # unexpected output — keep as-is

def load_done():
    if not OUT_CSV.exists(): return set()
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        return {int(r["page_id"]) for r in csv.DictReader(f)
                if r.get("page_id") and r.get("label") and not r.get("error")}

def save_batch(rows, write_header):
    mode = "w" if write_header else "a"
    with open(OUT_CSV, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["page_id","path","gt_label","label","category","error"])
        if write_header: w.writeheader()
        w.writerows(rows)

# ── classification worker ─────────────────────────────────────────────────────

def classify_one(page_id, text, gt_label):
    snippet = text[:MAX_CHARS]
    if not snippet.strip():
        return {"page_id": page_id, "path": "", "gt_label": gt_label,
                "label": "", "category": "", "error": "no text"}
    for attempt in range(5):
        try:
            raw = get_response_judge(snippet)
            cat = extract_category(raw)
            return {"page_id": page_id, "path": "", "gt_label": gt_label,
                    "label": raw.strip(), "category": cat, "error": ""}
        except Exception as e:
            if "429" in str(e) and attempt < 4:
                time.sleep(30 * (2 ** attempt))
            else:
                return {"page_id": page_id, "path": "", "gt_label": gt_label,
                        "label": "", "category": "", "error": str(e)[:120]}

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    # ── 1. Build ground truth ─────────────────────────────────────────────────
    print("Loading ground truth…", flush=True)
    with open(ROOT / "09_combined/synagogues_combined.csv", encoding="utf-8") as f:
        pd2 = {norm_domain(r["website"]) for r in csv.DictReader(f) if r.get("website")}
    with open(ROOT / "America_Orig.csv", encoding="latin-1") as f:
        hd = {norm_domain(r["Website"]) for r in csv.DictReader(f) if r.get("Website")}
    overlap = (pd2 - {""}) & (hd - {""})

    with open(ROOT / "America_results.csv", encoding="latin-1") as f:
        all_results = list(csv.DictReader(f))

    gt_map = {}; row_map = {}
    for r in all_results:
        if norm_domain(r.get("Link", "")) not in overlap: continue
        base = url_to_path(r.get("Link", ""))
        if not base: continue
        label = int(any(r.get(c, "").strip() not in ("FALSE", "") for c in CATS))
        for v in [base, base.rstrip("/"), base.rstrip("/")+"/index.html",
                  base.rstrip("/")+".html"]:
            gt_map[v] = label; row_map[v] = r

    # ── 2. Fetch pages from DB ────────────────────────────────────────────────
    print("Fetching pages from DB…", flush=True)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""SELECT wp.id, wp.page_path, wp.content_text
                   FROM web_pages wp
                   WHERE wp.page_path = ANY(%s)
                     AND wp.content_text IS NOT NULL AND wp.content_text != ''""",
                (list(gt_map.keys()),))
    rows = cur.fetchall()
    page_data = {pid: (path, text, gt_map[path])
                 for pid, path, text in rows if path in gt_map}
    page_ids = list(page_data.keys())
    print(f"  Pages: {len(page_data):,}", flush=True)

    # ── 3. Build union filter set ─────────────────────────────────────────────
    print("Running keyword scan…", flush=True)
    pattern = load_keywords()
    kw_pos = {pid for pid, (_, text, _) in page_data.items() if find_keywords(text, pattern)}

    print("Building query matrix…", flush=True)
    embs = encode(QUERY_TEXTS)
    embs = torch.nn.functional.normalize(embs, dim=-1).numpy()

    cur.execute("SELECT id, embedding::text FROM web_pages WHERE id = ANY(%s) AND embedding IS NOT NULL",
                (page_ids,))
    def pv(s): return np.fromstring(s.strip("[]"), sep=",", dtype=np.float32)
    max_scores = {pid: float((embs @ pv(v)).max()) for pid, v in cur.fetchall()}

    cur.execute("SELECT id FROM web_pages WHERE id = ANY(%s) AND content_tsv @@ to_tsquery(%s, %s)",
                (page_ids, "english", HC_FTS))
    hc_ids = {r[0] for r in cur.fetchall()}
    conn.close()

    sem_pos = {pid for pid, s in max_scores.items() if s >= 0.25 or pid in hc_ids}
    union   = kw_pos | sem_pos
    gt_pos  = {pid for pid, (_, _, gt) in page_data.items() if gt}
    gt_neg  = {pid for pid, (_, _, gt) in page_data.items() if not gt}

    print(f"  Union: {len(union):,}  (GT pos={len(union & gt_pos)}, GT neg={len(union & gt_neg)})")

    # ── 4. 80/20 stratified split ─────────────────────────────────────────────
    rng = np.random.default_rng(SEED)
    up = sorted(union & gt_pos); un = sorted(union & gt_neg)
    rng.shuffle(up); rng.shuffle(un)
    train_ids = set(up[:int(len(up)*0.8)]) | set(un[:int(len(un)*0.8)])
    test_ids  = set(up[int(len(up)*0.8):]) | set(un[int(len(un)*0.8):])
    print(f"  Train (80%): {len(train_ids):,}  pos={len(train_ids & gt_pos)}")
    print(f"  Test  (20%): {len(test_ids):,}   pos={len(test_ids & gt_pos)}")

    # ── 5. Skip already-classified ────────────────────────────────────────────
    done = load_done()
    queue = [(pid, page_data[pid][1], page_data[pid][2])
             for pid in train_ids if pid not in done]
    print(f"\n  Already classified : {len(done):,}")
    print(f"  Remaining          : {len(queue):,}")

    if not queue:
        print("Nothing to classify — proceeding to report.")
    else:
        # ── 6. Classify ───────────────────────────────────────────────────────
        write_header = not OUT_CSV.exists() or len(done) == 0
        csv_lock = threading.Lock()
        buffer = []
        completed = [0]
        t_start = time.time()

        def flush(force=False):
            with csv_lock:
                if buffer and (force or len(buffer) >= FLUSH_EVERY):
                    save_batch(buffer, write_header and not OUT_CSV.exists())
                    buffer.clear()

        print(f"\nClassifying {len(queue):,} pages with {WORKERS} workers…", flush=True)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(classify_one, pid, text, gt): pid
                       for pid, text, gt in queue}
            for fut in as_completed(futures):
                r = fut.result()
                with csv_lock:
                    buffer.append(r)
                    completed[0] += 1
                    n = completed[0]
                flush()
                if n % 500 == 0 or n == len(queue):
                    elapsed = time.time() - t_start
                    rate = n / elapsed * 60 if elapsed > 0 else 0
                    print(f"  {n:,}/{len(queue):,}  ({n/len(queue)*100:.0f}%)  "
                          f"{rate:.0f} pages/min", flush=True)

        flush(force=True)
        print("Classification complete.", flush=True)

    # ── 7. Report ─────────────────────────────────────────────────────────────
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    classified = [r for r in all_rows if r.get("label") and not r.get("error")]
    errors     = [r for r in all_rows if r.get("error")]

    gt_pos_rows = [r for r in classified if r["gt_label"] == "1"]
    gt_neg_rows = [r for r in classified if r["gt_label"] == "0"]

    def is_env(label):
        return label.strip() not in ("N/A", "", "N/A.")

    SEP = "─" * 65

    print(f"\n{'='*65}")
    print(f"RESULTS  (n={len(classified):,} classified, {len(errors)} errors)")
    print(SEP)
    print(f"  GT positive pages (n={len(gt_pos_rows)})")
    tp = sum(1 for r in gt_pos_rows if is_env(r["label"]))
    fn = len(gt_pos_rows) - tp
    print(f"    Claude found env action   : {tp}  ({tp/len(gt_pos_rows)*100:.1f}%)")
    print(f"    Claude returned N/A       : {fn}  ({fn/len(gt_pos_rows)*100:.1f}%)")
    print()
    print(f"  GT negative pages (n={len(gt_neg_rows)})")
    fp = sum(1 for r in gt_neg_rows if is_env(r["label"]))
    tn = len(gt_neg_rows) - fp
    print(f"    Claude found env action   : {fp}  ({fp/len(gt_neg_rows)*100:.2f}%)  <- false positives")
    print(f"    Claude returned N/A       : {tn}  ({tn/len(gt_neg_rows)*100:.1f}%)")
    print()
    prec = tp/(tp+fp) if (tp+fp) else 0
    rec  = tp/(tp+fn) if (tp+fn) else 0
    f1   = 2*prec*rec/(prec+rec) if (prec+rec) else 0
    print(f"  Precision : {prec:.3f}   Recall : {rec:.3f}   F1 : {f1:.3f}")

    # Category breakdown for GT positive pages that Claude found
    print(f"\n{SEP}")
    print("  Category breakdown — Claude labels on GT positive pages:")
    cat_counts = Counter(r["category"] for r in gt_pos_rows if is_env(r["label"]))
    total_found = sum(cat_counts.values())
    for cat, n in cat_counts.most_common():
        bar = "#" * int(n / total_found * 25) if total_found else ""
        print(f"    {cat:<45s}  {n:>3}  {n/total_found*100:5.1f}%  {bar}")

    # Human vs Claude category agreement (for GT positives Claude found)
    print(f"\n{SEP}")
    print("  Human vs Claude category agreement (GT positive, Claude non-N/A):")
    agree = 0; disagree = 0
    for r in gt_pos_rows:
        if not is_env(r["label"]): continue
        pid = int(r["page_id"])
        path = page_data.get(pid, ("",))[0] if pid in page_data else ""
        hr = row_map.get(path, {})
        human_cats = {c for c in CATS if hr.get(c, "").strip() not in ("FALSE", "")}
        claude_cat = r["category"]
        if claude_cat in human_cats:
            agree += 1
        else:
            disagree += 1
    total_ag = agree + disagree
    print(f"    Agree    : {agree}  ({agree/total_ag*100:.1f}%)" if total_ag else "    (no data)")
    print(f"    Disagree : {disagree}  ({disagree/total_ag*100:.1f}%)" if total_ag else "")

    # False positive breakdown by Claude category
    print(f"\n{SEP}")
    print("  Category breakdown — Claude labels on GT negative pages (false positives):")
    fp_cats = Counter(r["category"] for r in gt_neg_rows if is_env(r["label"]))
    for cat, n in fp_cats.most_common(10):
        print(f"    {cat:<45s}  {n:>4}")
    print(f"  → {OUT_CSV}")


if __name__ == "__main__":
    main()
