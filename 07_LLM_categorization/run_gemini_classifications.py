"""
run_gemini_classifications.py

Replaces the 656 haiku-4-5 rows in llm_classifications with Gemini 3.1
Flash-Lite classifications on the same pages.

Usage:
    python 06_LLM_categorization/run_gemini_classifications.py
"""

import csv
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import psycopg2
import psycopg2.extras
import torch
from dotenv import load_dotenv

HERE = Path(__file__).parent

def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "America_Orig.csv").exists():
            return p
    return start.parent

WORKTREE = HERE.parent   # .claude/worktrees/tune-semantic-filter
# Data files (CSVs, .env) live in the actual project root, not the worktree
ROOT     = _find_root(HERE)
# But pipeline data (04_combined etc.) lives in the worktree
DATA_ROOT = WORKTREE if (WORKTREE / "04_combined").exists() else ROOT

sys.path.insert(0, str(HERE))
# prefer worktree copies of pipeline modules (keyword_search, _embed)
sys.path.insert(0, str(WORKTREE / "06_environmental_classification"))
sys.path.insert(0, str(WORKTREE / "08_database"))
sys.path.insert(0, str(ROOT / "06_environmental_classification"))
sys.path.insert(0, str(ROOT / "08_database"))

from gemini_as_a_judge import get_response_judge
from keyword_search import load_keywords, find_keywords
from _embed import encode

load_dotenv(ROOT / ".env")

MODEL       = "gemini-3.1-flash-lite"
TEMPERATURE = 0.0
MAX_CHARS   = 6_000
WORKERS     = 5
FLUSH_EVERY = 50
THRESHOLD   = 0.25

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

def norm_domain(url):
    if not url or not url.strip(): return ""
    url = url.strip().lower().rstrip("/")
    if not url.startswith("http"): url = "http://" + url
    try:
        d = urlparse(url).netloc
        return d[4:] if d.startswith("www.") else d
    except: return ""

def parse_vec(s):
    return np.fromstring(s.strip("[]"), sep=",", dtype=np.float32)

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )

def main():
    conn = get_conn()
    cur  = conn.cursor()

    # ── 1. Overlap domains ────────────────────────────────────────────────────
    print("Loading overlap domains…", flush=True)
    with open(DATA_ROOT / "04_combined/synagogues_combined.csv", encoding="utf-8") as f:
        pipeline_domains = {norm_domain(r["website"]) for r in csv.DictReader(f) if r.get("website")}
    with open(ROOT / "America_Orig.csv", encoding="latin-1") as f:
        human_domains = {norm_domain(r["Website"]) for r in csv.DictReader(f) if r.get("Website")}
    overlap = (pipeline_domains - {""}) & (human_domains - {""})
    print(f"  Overlap domains: {len(overlap):,}", flush=True)

    # ── 2. Fetch pages from overlap sites ─────────────────────────────────────
    print("Fetching pages from DB…", flush=True)
    cur.execute("""
        SELECT wp.id, wp.content_text, wp.embedding::text
        FROM web_pages wp
        JOIN websites w ON w.id = wp.website_id
        WHERE w.domain = ANY(%s)
          AND wp.content_text IS NOT NULL AND wp.content_text != ''
          AND wp.embedding IS NOT NULL
    """, (list(overlap),))
    rows = cur.fetchall()
    print(f"  Pages: {len(rows):,}", flush=True)

    # ── 3. Union filter ───────────────────────────────────────────────────────
    print("Running keyword scan…", flush=True)
    pattern = load_keywords()
    kw_ids  = {pid for pid, text, _ in rows if find_keywords(text, pattern)}

    print("Building semantic query matrix…", flush=True)
    embs = encode(QUERY_TEXTS)
    embs = torch.nn.functional.normalize(embs, dim=-1).numpy()

    sem_ids = {pid for pid, _, vec_str in rows
               if float((embs @ parse_vec(vec_str)).max()) >= THRESHOLD}

    cur.execute("SELECT id FROM web_pages WHERE id = ANY(%s) AND content_tsv @@ to_tsquery('english', %s)",
                ([r[0] for r in rows], HC_FTS))
    sem_ids |= {r[0] for r in cur.fetchall()}

    union_ids = kw_ids | sem_ids
    print(f"  Keyword: {len(kw_ids):,}  Semantic: {len(sem_ids):,}  Union: {len(union_ids):,}", flush=True)

    # ── 4. Skip successfully classified pages; retry errors ──────────────────
    cur.execute("""
        SELECT web_page_id FROM llm_classifications
        WHERE model_used = %s AND error IS NULL AND category IS NOT NULL
    """, (MODEL,))
    done_ids = {r[0] for r in cur.fetchall()}

    # Delete any errored rows so they can be retried cleanly
    cur.execute("""
        DELETE FROM llm_classifications
        WHERE model_used = %s AND (error IS NOT NULL OR category IS NULL)
    """, (MODEL,))
    deleted = cur.rowcount
    conn.commit()
    if deleted:
        print(f"  Cleared {deleted:,} errored rows for retry.", flush=True)

    id_to_text = {pid: text for pid, text, _ in rows}
    pages = {pid: id_to_text[pid] for pid in union_ids if pid not in done_ids}
    print(f"  Already classified: {len(done_ids):,}  To classify: {len(pages):,}\n", flush=True)

    if not pages:
        print("Nothing to classify.")
        conn.close()
        return

    # ── 5. Classify with Gemini and insert ────────────────────────────────────
    queue  = list(pages.items())
    lock   = threading.Lock()
    buffer = []
    done   = [0]
    errs   = [0]
    t0     = time.time()

    def classify(pid, text):
        try:
            label = get_response_judge(text[:MAX_CHARS]).strip().strip('"')
            return (pid, label, None)
        except Exception as e:
            return (pid, None, str(e)[:200])

    def flush(force=False):
        with lock:
            if buffer and (force or len(buffer) >= FLUSH_EVERY):
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO llm_classifications
                        (web_page_id, category, model_used, temperature, error)
                    VALUES (%s, %s, %s, %s, %s)
                """, buffer, page_size=500)
                conn.commit()
                buffer.clear()

    print(f"\nClassifying {len(queue)} pages with {WORKERS} workers…\n", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(classify, pid, text): pid for pid, text in queue}
        for fut in as_completed(futures):
            pid, label, err = fut.result()
            with lock:
                buffer.append((pid, label, MODEL, TEMPERATURE, err))
                done[0] += 1
                if err: errs[0] += 1
            flush()
            n = done[0]
            if n % 100 == 0 or n == len(queue):
                rate = n / (time.time() - t0) * 60
                print(f"  {n}/{len(queue)}  ({n/len(queue)*100:.0f}%)  {rate:.0f} pages/min  errors={errs[0]}", flush=True)

    flush(force=True)

    # ── 5. Summary ────────────────────────────────────────────────────────────
    cur.execute("SELECT category, COUNT(*) FROM llm_classifications WHERE model_used = %s GROUP BY category ORDER BY COUNT(*) DESC", (MODEL,))
    breakdown = cur.fetchall()
    conn.close()

    total = sum(n for _, n in breakdown)
    print(f"\n{'='*60}")
    print(f"Inserted {total} {MODEL} classifications.")
    print(f"Errors: {errs[0]}")
    print(f"\nCategory breakdown:")
    for cat, n in breakdown:
        bar = "#" * int(n / total * 30) if total else ""
        print(f"  {str(cat)[:55]:<55s}  {n:>4}  {n/total*100:5.1f}%  {bar}")

if __name__ == "__main__":
    main()
