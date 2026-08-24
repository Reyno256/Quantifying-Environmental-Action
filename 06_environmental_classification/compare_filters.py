"""
compare_filters.py

Runs keyword scan AND semantic filter on pages that have DB embeddings
(all 327 domains), then classifies a sample of pages with Claude Haiku
to compare precision (N/A rate) and category coverage of each approach.

Uses content_text from the DB for both filtering and classification —
no local HTML files needed.

Usage:
    python compare_filters.py
    python compare_filters.py --max-classify 100
    python compare_filters.py --threshold 0.28
"""

import argparse
import csv
import os
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = Path(__file__).parent

# Worktrees live inside .claude/worktrees/<name>/ — walk up to find the real
# project root (the directory that contains the .env file).
def _find_project_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start.parent  # fallback

ROOT = _find_project_root(HERE)

sys.path.insert(0, str(HERE))
from keyword_search import load_keywords, find_keywords

sys.path.insert(0, str(ROOT / "06_LLM_categorization"))
from gemini_as_a_judge import get_response_judge

sys.path.insert(0, str(ROOT / "07_database"))
from _embed import encode, to_pgvector

load_dotenv(ROOT / ".env")

# ── config ────────────────────────────────────────────────────────────────────

SEED       = 42
MAX_CHARS  = 6_000
WORKERS    = 5
DEFAULT_N  = 150   # max pages to classify per group

DEFAULT_THRESHOLD = 0.30

# Same QUERY_TEXTS as tuned semantic_filter.py (one phrase per rubric category)
QUERY_TEXTS = [
    # Spirituality & Worship
    "environmental stewardship worship service land acknowledgment earth day sermon prayer climate",
    # Community
    "community garden pollinator volunteering environmental cleanup tree planting outreach collaboration youth",
    # Operations & Maintenance
    "biodegradable non-toxic cleaning products sustainability coordinator procurement FSC certified recycled paper signage",
    # Energy
    "utility tracking energy consumption electric vehicle charging renewable energy transition solar panels",
    # Water
    "low-flush toilets grey water rain barrels water conservation cistern watering schedule runoff",
    # Waste
    "composting recycling plastic reduction single-use elimination reusable containers waste municipal program",
    # Kitchen
    "vegetarian vegan organic local food fair trade reusable dishes farmers market food justice seasonal",
    # Environmental & Climate Justice
    "environmental justice climate justice Indigenous rights political advocacy discussion group land acknowledgment",
    # Generic / cross-cutting
    "tikkun olam hazon bal tashchit eco-friendly carbon footprint green synagogue environmental action",
]

HC_FTS = (
    "hazon | compost | solar | fossil | recycl | carbon | "
    "geotherm | hvac | photovoltaic | tashchit | tu_bishvat | emiss"
)

# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def fetch_embedded_pages(conn):
    """Return list of (id, content_text) for all pages with embeddings."""
    cur = conn.cursor("page_cursor")  # server-side cursor for large result
    cur.execute("""
        SELECT wp.id, wp.content_text
        FROM web_pages wp
        JOIN websites w ON w.id = wp.website_id
        WHERE wp.embedding IS NOT NULL
          AND wp.content_text IS NOT NULL
          AND wp.content_text != ''
    """)
    rows = cur.fetchall()
    cur.close()
    return rows  # list of (id, text)


def run_semantic_filter_ids(conn, q_vec, threshold):
    """Return set of page IDs that pass the DB semantic filter."""
    cur = conn.cursor()
    cur.execute("""
        SELECT wp.id
        FROM web_pages wp
        WHERE wp.embedding IS NOT NULL
          AND (
              wp.content_tsv @@ to_tsquery('english', %s)
              OR 1.0 - (wp.embedding <=> %s::vector) >= %s
          )
    """, (HC_FTS, q_vec, threshold))
    result = {r[0] for r in cur.fetchall()}
    cur.close()
    return result


# ── keyword scan (Python-side on content_text) ────────────────────────────────

def keyword_scan(pages, pattern):
    """Return set of page IDs whose content_text matches any keyword."""
    hits = set()
    for page_id, text in pages:
        if text and find_keywords(text, pattern):
            hits.add(page_id)
    return hits


# ── classification ────────────────────────────────────────────────────────────

def classify_one(page_id, text):
    snippet = text[:MAX_CHARS] if text else ""
    if not snippet.strip():
        return {"id": page_id, "category": "", "error": "no text"}
    for attempt in range(5):
        try:
            cat = get_response_judge(snippet)
            return {"id": page_id, "category": cat.strip(), "error": ""}
        except Exception as e:
            if "429" in str(e) and attempt < 4:
                time.sleep(30 * (2 ** attempt))
            else:
                return {"id": page_id, "category": "", "error": str(e)[:120]}


def classify_sample(id_text_map, label, n):
    random.seed(SEED)
    ids = random.sample(list(id_text_map), min(n, len(id_text_map)))
    print(f"\nClassifying {len(ids)} pages [{label}]…", flush=True)
    results = []
    done = [0]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(classify_one, i, id_text_map[i]): i for i in ids}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == len(ids):
                print(f"  {done[0]}/{len(ids)}", flush=True)
    return results


def report(label, results, out_csv):
    classified = [r for r in results if r.get("category") and not r.get("error")]
    errors     = [r for r in results if r.get("error")]
    total = len(classified)
    cats  = Counter(r["category"].strip().rstrip(".,").strip() for r in classified)
    na    = cats.get("N/A", 0)

    print(f"\n── {label} {'─' * (62 - len(label))}")
    print(f"  Sampled / classified / errors : {len(results)} / {total} / {len(errors)}")
    if total:
        print(f"  N/A rate : {na / total * 100:.1f}%")
        print(f"  Category breakdown (excl. N/A):")
        for cat, n in cats.most_common():
            if cat == "N/A":
                continue
            bar = "█" * int(n / total * 30)
            print(f"    {cat:<42s} {n:>4}  {n/total*100:5.1f}%  {bar}")
    else:
        print("  (no results)")

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "category", "error"])
        w.writeheader()
        w.writerows(results)
    print(f"  → {out_csv}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-kw-only",  type=int, default=DEFAULT_N,
                        help=f"Pages from keyword-only group (default {DEFAULT_N})")
    parser.add_argument("--n-sem-only", type=int, default=DEFAULT_N,
                        help=f"Pages from semantic-only group (default {DEFAULT_N})")
    parser.add_argument("--n-both",     type=int, default=DEFAULT_N,
                        help=f"Pages from both group (default {DEFAULT_N})")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Cosine similarity threshold (default {DEFAULT_THRESHOLD})")
    args = parser.parse_args()

    conn = get_conn()

    # ── 1. Fetch all embedded pages ───────────────────────────────────────────
    print("Fetching embedded pages from DB…", flush=True)
    pages = fetch_embedded_pages(conn)
    id_text = {pid: txt for pid, txt in pages}
    print(f"  Pages with embedding + content : {len(id_text):,}")

    # ── 2. Keyword scan (Python regex on content_text) ────────────────────────
    print("Running keyword scan (Python regex)…", flush=True)
    pattern   = load_keywords()
    kw_ids    = keyword_scan(pages, pattern)
    print(f"  Keyword matched : {len(kw_ids):,}")

    # ── 3. Semantic filter (DB cosine + FTS) ──────────────────────────────────
    print("Building query vector…", flush=True)
    import torch
    embs  = encode(QUERY_TEXTS)
    avg   = torch.nn.functional.normalize(embs.mean(dim=0, keepdim=True), dim=-1)[0]
    q_vec = to_pgvector(avg)
    print("Running semantic filter (DB)…", flush=True)
    sem_ids = run_semantic_filter_ids(conn, q_vec, args.threshold)
    print(f"  Semantic matched : {len(sem_ids):,}")
    conn.close()

    # ── 4. Set breakdown ──────────────────────────────────────────────────────
    both_ids    = kw_ids & sem_ids
    kw_only_ids = kw_ids - sem_ids
    sem_only_ids= sem_ids - kw_ids
    union_ids   = kw_ids | sem_ids

    print(f"\n{'─'*55}")
    print(f"  Keyword only  : {len(kw_only_ids):>6,}  (true positives missed by semantic)")
    print(f"  Semantic only : {len(sem_only_ids):>6,}  (extra pages found by semantic)")
    print(f"  Both          : {len(both_ids):>6,}  (agreed positives)")
    print(f"  Union         : {len(union_ids):>6,}")
    print(f"{'─'*55}")

    # ── 5. Classify each group ────────────────────────────────────────────────
    r_kw_only  = classify_sample({i: id_text[i] for i in kw_only_ids  if i in id_text}, "keyword-only",  args.n_kw_only)
    r_sem_only = classify_sample({i: id_text[i] for i in sem_only_ids if i in id_text}, "semantic-only", args.n_sem_only)
    r_both     = classify_sample({i: id_text[i] for i in both_ids     if i in id_text}, "both",          args.n_both)

    # ── 6. Reports ────────────────────────────────────────────────────────────
    report("Keyword-only pages",  r_kw_only,  HERE / "comparison_kw_only.csv")
    report("Semantic-only pages", r_sem_only, HERE / "comparison_sem_only.csv")
    report("Pages matched by both", r_both,   HERE / "comparison_both.csv")

    # ── 7. Overall summary ────────────────────────────────────────────────────
    all_results = r_kw_only + r_sem_only + r_both
    classified_all = [r for r in all_results if r.get("category") and not r.get("error")]
    cats_all = Counter(r["category"].strip().rstrip(".,").strip() for r in classified_all)
    total_all = len(classified_all)

    print(f"\n{'═'*65}")
    print(f"OVERALL (all classified pages, n={total_all})")
    print(f"  N/A rate : {cats_all.get('N/A',0)/total_all*100:.1f}%" if total_all else "")
    print(f"  Category breakdown:")
    for cat, n_cat in cats_all.most_common():
        bar = "█" * int(n_cat / total_all * 30) if total_all else ""
        print(f"    {cat:<42s} {n_cat:>4}  {n_cat/total_all*100:5.1f}%  {bar}")


if __name__ == "__main__":
    main()
