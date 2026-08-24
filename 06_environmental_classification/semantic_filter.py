"""
Semantic environmental filter — DB-native replacement for classify_environmental.py.

Two-stage hybrid:
  1. High-confidence PostgreSQL FTS match (to_tsquery) — instant pass, no threshold.
  2. Max cosine similarity of page embedding vs. each of 76 rubric subcategory
     phrases (one per Table 3 action item). A page passes if its similarity to
     ANY phrase meets --threshold (default 0.25).

     Using max rather than an averaged vector gives per-category coverage:
     recall vs. human ground truth rises from 0.53 (avg-9) to 0.95 (max-76).

Pages that pass either stage are:
  - Flagged in web_pages.has_env_keywords
  - Summarised in classifying_environmental_action.csv

Prerequisites:
    python 08_database/migrate_add_vector.py
    python 08_database/embed_pages.py

Usage:
    python semantic_filter.py
    python semantic_filter.py --threshold 0.25
"""

import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "08_database"))
from _embed import encode, to_pgvector

load_dotenv(ROOT / ".env")

# ── config ────────────────────────────────────────────────────────────────────

DEFAULT_THRESHOLD = 0.25
OUT_CSV  = HERE / "classifying_environmental_action.csv"

# Every subcategory action from the Table 3 rubric (76 phrases).
# The filter passes a page if its embedding is within --threshold of ANY phrase
# (max similarity), giving full per-category coverage vs. the old single-vector
# averaged approach.
QUERY_TEXTS = [
    # Spirituality & Worship
    "Include symbols of nature in place of worship, gardens, or meditation areas",
    "Focus on environmentalism stewardship climate action in worship for religious or community events holidays earth day",
    "Incorporate environmental prayers or songs in worship",
    "Dedicate a worship service to environmentalism",
    "Offer retreats prayers or reflections to reconnect with nature",
    "Use environmentally-friendly alternatives for items used in worship",
    # Community
    "Promote and facilitate household energy audits and water conservation",
    "Assisted in local environmental cleanup restoration projects or tree planting initiatives",
    "Encouraged car-pooling public transit use and bicycle use",
    "Encourage volunteering initiatives aimed at environmental responsibility reducing individual carbon footprints and taking climate action",
    "Resources with a nature and environmental stewardship focus are available in the faith community library common area or website",
    "Encourage members to adopt a greener lifestyle to join community environmental projects and to practice the 3 Rs at home",
    "Planted a community garden for native pollinator-friendly plants and vegetables",
    "Bike racks and carpooling preferential parking spots have been installed on the faith community property",
    "Hold or support a holiday or summer camp with an environmental stewardship climate theme",
    "Hold educational sessions to educate people on environmental stewardship climate action that link to the faith tradition and promote faith-based and local environmental organizations",
    "Undertake environmental projects in collaboration with other faith communities or local groups planting pollinator gardens planting trees community spring clean-up naturalization of schoolyards and sacred spaces",
    "Engage with local provincial or federal governments to support sustainable public policies",
    "A volunteer coordinator has been appointed to facilitate carpooling and to ensure all community members are aware of public transit routes to the facility",
    "Electric vehicle charging stations have been installed on the property or identified nearby",
    "Create and support opportunities for younger people to experience how people in their own and other communities are affected by the planetary crisis and how they can work for change",
    "A board committee focusing on environmental and sustainability issues has been established",
    # Operations & Maintenance
    "Have a think twice before printing policy and an active paper reduction and recycling policy",
    "Have a procurement policy for recycled and FSC certified paper office envelopes bulletins bathroom tissue",
    "A sustainability coordinator and team has been created and are developing sustainable practices within the place of worship",
    "Biodegradable non-toxic phosphate-free cleaning products are purchased and used within the facility",
    "Make their own eco-friendly cleaning products using natural materials baking soda white vinegar essential oils",
    "Have a green maintenance plan including using alternatives to salt in the winter and seeking lot and yard maintenance providers who use green practices",
    # Energy
    "Utility expenditures and energy consumption are tracked and documented",
    "Utility data is analyzed regularly to identify activities that cause high consumption patterns are identified and plans are in place to reduce consumption",
    "An energy audit has been completed by a trained professional auditor and they are working to resolve some of the identified issues",
    "Signage posted at all light switches reminds people to turn off lights when not in use",
    "Energy efficient lighting LEDs have been installed where possible dimmers timers and motion sensors are installed where possible",
    "Programmable thermostats are in use and winter summer set-back temperatures are in place",
    "Heating cooling system has a regular maintenance schedule furnace filters are cleaned or replaced and ducts are clean",
    "High-efficiency heating cooling or solar hot water system installed",
    "Ceiling fans are in regular use",
    "Usage of caulk and spray foam to air-seal the building",
    "Weather stripping on windows and doors has been upgraded",
    "Replaced their fossil fuel burning heating system with an air or ground source heat pump",
    "Insulation has been added to walls and roof",
    "Investigated the possibility of installing a renewable energy system of any kind in their facility",
    "Installed a renewable energy system in their facility",
    # Water
    "Basic efficiency steps have been taken such as low-flow aerators on all faucets posting signs advising people to limit water usage and regular checks to ensure no leaky faucets or toilets",
    "Systems that limit total hot water usage such as a hot water heat recovery system or a pre-mixing of hot and cold water system have been installed",
    "Steps have been taken such as insulating hot water tanks and pipes",
    "Installed a high-efficiency hot water heater such as an on-demand unit or a solar domestic hot water heating system",
    "Low-flush toilets and urinals are installed where possible throughout the facility",
    "A grey water system has been installed that allows the facility to reuse water for a second time or a large cistern has been installed to capture rainwater for interior building uses",
    "Exterior watering of the grounds only occurs in the morning or evening and is shut off when it is raining or rain is expected",
    "The grounds have been specifically landscaped with native pollinator plants that do not require much watering xeriscaping or installed a rain garden to capture runoff",
    "Rain barrels have been installed to collect roof run-off water for the purpose of watering the grounds",
    # Waste
    "Blue bins can be found in every area signs are posted near the bins advising people as to what can and cannot be recycled",
    "Recycling facilities are available at the faith community for dropping off items such as ink cartridges cell phones CFL bulbs",
    "Items such as eye glasses and clothes are donated to the faith community for re-use or re-distribution",
    "Have a backyard composting system for yard waste and kitchen waste",
    "Composts regularly through their municipal composting program",
    "Reusable or cloth bags are used while shopping at grocery stores to reduce plastic waste",
    "Fruits and vegetables are selected from a bin rather than those on styrofoam trays and shrink-wrapped in plastic",
    "A waste reduction strategy has been planned and implemented track total waste to determine if targets are being met",
    # Kitchen
    "Locally sourced food and drinks are used when possible",
    "Vegetarian vegan organic and in-season food options are provided and served when possible",
    "Reusable recyclable and compostable dishes and cutlery are in regular use",
    "A policy of using washable dishes and cutlery during events and break times is in place",
    "Single-use dishes and cutlery have been completely eliminated from use",
    "Promotes fair trade through a purchasing policy and by offering fairly traded products for sale",
    "Community garden regularly provides fresh vegetables to events held at the facility or to local food programs",
    "Support local food initiatives through actions such as hosting a local farmers market or other types of food-related events",
    # Environmental & Climate Justice
    "Studied its faith traditions recent statements on fossil fuel divestment",
    "Formed an environmental justice discussion group and climate justice themes have been incorporated into services and events and promoted across social media platforms",
    "Met with political leaders to urge them to honour the rights of Indigenous peoples to a clean and healthy environment and to take immediate action on behalf of Indigenous communities impacted by environmental pollution",
    "Researched the traditional territory and treaty on which it is located and incorporates a statement of land acknowledgement into its services and events",
    "Hosted a workshop or discussion group concerning environmental issues being faced by Indigenous communities today and committed to taking action",
    "Members have been provided with educational materials about fossil fuel divestment",
    "Hosts environmental climate justice events screenings workshops study sessions and offers space for community groups to meet on these issues",
    "Reviewed their investments and agreed to divest from any fossil fuel investments or to invest in renewable energy",
    "Met with political leaders to call for action on climate change and participating in a climate justice action",
    # Other
    "Other environmental action",
]

# Stems passed to to_tsquery('english', ...) — high-precision terms with
# near-zero false-positive rate in synagogue content.
# Excluded:
#   renew    → English stemmer maps both "renewal" AND "renewable" to "renew"
#   sustainab/biodiv → PostgreSQL English stemmer produces no matching lexeme
#   climat   → matches "climate" in generic org/emotional sense (too broad)
#   dayenu   → Passover song title, used outside environmental context
HC_FTS = (
    "hazon | compost | solar | fossil | recycl | carbon | "
    "geotherm | hvac | photovoltaic | tashchit | tu_bishvat | emiss"
)


# ── helpers ───────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def build_query_matrix():
    """Encode all rubric phrases → L2-normalised numpy array [N x 384]."""
    import torch
    embs = encode(QUERY_TEXTS)
    return torch.nn.functional.normalize(embs, dim=-1).numpy()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="Cosine similarity threshold (default 0.30)")
    args = parser.parse_args()

    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM web_pages WHERE embedding IS NOT NULL")
    n_embedded = cur.fetchone()[0]
    if n_embedded == 0:
        sys.exit("No embeddings found — run 08_database/embed_pages.py first.")

    import numpy as np
    import re

    print(f"Pages with embeddings : {n_embedded:,}")
    print(f"Similarity threshold  : {args.threshold}")
    print(f"Building query matrix ({len(QUERY_TEXTS)} rubric phrases)…", flush=True)
    t0 = time.perf_counter()
    q_matrix = build_query_matrix()          # [76, 384]
    print(f"  done ({time.perf_counter() - t0:.1f}s)\n", flush=True)

    # ── Stage 1: high-confidence FTS (instant, stays in DB) ──────────────────
    print("Stage 1 — high-confidence FTS…", flush=True)
    cur.execute("""
        SELECT wp.id, wp.page_path, w.domain
        FROM web_pages wp
        JOIN websites w ON w.id = wp.website_id
        WHERE wp.embedding IS NOT NULL
          AND wp.content_tsv @@ to_tsquery('english', %s)
    """, (HC_FTS,))
    hc_rows   = cur.fetchall()
    hc_ids    = {r[0] for r in hc_rows}
    print(f"  HC-FTS matched : {len(hc_ids):,}", flush=True)

    # ── Stage 2: max cosine similarity over all rubric phrases ───────────────
    print("Stage 2 — fetching embeddings for max-similarity computation…", flush=True)
    cur2 = conn.cursor("emb_cursor")          # server-side cursor — streams result
    cur2.execute("""
        SELECT id, embedding::text
        FROM web_pages
        WHERE embedding IS NOT NULL
    """)

    def parse_vec(s):
        return np.fromstring(s.strip("[]"), sep=",", dtype=np.float32)

    sem_ids     = set()
    id_score    = {}           # id → max similarity score (for reporting)
    batch_size  = 5_000
    processed   = 0

    while True:
        batch = cur2.fetchmany(batch_size)
        if not batch:
            break
        pids  = [r[0] for r in batch]
        vecs  = np.stack([parse_vec(r[1]) for r in batch])   # [B, 384]
        sims  = vecs @ q_matrix.T                             # [B, 76]
        maxes = sims.max(axis=1)                              # [B]
        for pid, score in zip(pids, maxes):
            id_score[pid] = float(score)
            if score >= args.threshold:
                sem_ids.add(pid)
        processed += len(batch)
        print(f"  {processed:,} / {n_embedded:,} processed…", end="\r", flush=True)

    cur2.close()
    print(f"\n  Cosine matched : {len(sem_ids):,}", flush=True)

    match_ids = hc_ids | sem_ids
    print(f"\nTotal matched  : {len(match_ids):,}  "
          f"(HC-FTS={len(hc_ids)}, cosine={len(sem_ids - hc_ids)}, both={len(hc_ids & sem_ids)})\n",
          flush=True)

    # ── update has_env_keywords in DB ─────────────────────────────────────────
    cur.execute("UPDATE web_pages SET has_env_keywords = FALSE")
    if match_ids:
        psycopg2.extras.execute_batch(
            cur,
            "UPDATE web_pages SET has_env_keywords = TRUE WHERE id = %s",
            [(pid,) for pid in match_ids],
            page_size=1000,
        )
    conn.commit()

    # ── aggregate per domain ──────────────────────────────────────────────────
    cur.execute("""
        SELECT w.domain, COUNT(wp.id), COUNT(wp.id) FILTER (WHERE wp.has_env_keywords)
        FROM websites w
        JOIN web_pages wp ON wp.website_id = w.id
        GROUP BY w.domain
    """)
    domain_stats = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    # ── write CSV ─────────────────────────────────────────────────────────────
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["website", "pages_with_hits", "total_pages"])
        for domain in sorted(domain_stats):
            total_pg, hit_pg = domain_stats[domain]
            writer.writerow([domain, hit_pg, total_pg])
    print(f"CSV written → {OUT_CSV}")

    # ── summary ───────────────────────────────────────────────────────────────
    n_domains_hit = sum(1 for t, h in domain_stats.values() if h > 0)
    print(f"\nDomains with ≥1 match  : {n_domains_hit} / {len(domain_stats)}")
    print(f"Total matched pages    : {len(match_ids):,}")
    print(f"  High-confidence FTS  : {len(hc_ids):,}")
    print(f"  Cosine only          : {len(sem_ids - hc_ids):,}")

    top = sorted(
        [(d, h) for d, (_, h) in domain_stats.items() if h > 0],
        key=lambda x: -x[1],
    )[:10]
    print("\nTop 10 domains by matched pages:")
    for domain, hits in top:
        total_pg = domain_stats[domain][0]
        print(f"  {domain:<50s}  {hits:3d} / {total_pg}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
