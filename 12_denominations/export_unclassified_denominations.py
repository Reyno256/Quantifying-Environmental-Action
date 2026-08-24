"""
Export the synagogues whose denomination is still not pinned to a real bucket,
for manual review:

  cohort = 'null_denomination'  -> denomination_canonical IS NULL
                                   (the LLM could NOT be run on these)
  cohort = 'llm_unknown'        -> LLM ran but returned 'Unknown'
                                   (denomination_source='llm', canonical='Unknown')

One row per website URL (synagogues with no website still get one blank-URL
row). Each link is flagged as a potential duplicate, and every row carries the
reason the LLM produced no usable denomination.

    python 12_denominations/export_unclassified_denominations.py [OUT.csv]

Default output: 12_denominations/unclassified_denominations.csv (in this repo).
"""

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

HERE = Path(__file__).parent


def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start.parent


ROOT = _find_root(HERE)
load_dotenv(ROOT / ".env")

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "unclassified_denominations.csv"

# Domains that are shared platforms / payment / directories, not a single org's
# site — a synagogue sharing one of these is not a true site duplicate.
PLATFORM_DOMAINS = {
    "facebook.com", "m.facebook.com", "godaven.com", "sites.google.com",
    "secure.cardknox.com", "shulspace.org",
}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    conn = get_conn()
    cur = conn.cursor()

    # ── duplicate maps over the WHOLE websites table ──────────────────────
    cur.execute("SELECT domain, synagogue_id FROM websites")
    dom_synids = defaultdict(set)
    dom_rows = defaultdict(int)
    for domain, syn_id in cur.fetchall():
        dom_synids[domain].add(syn_id)
        dom_rows[domain] += 1

    def dup_info(domain):
        if not domain:
            return ("no", "")
        if domain == "https:" or "." not in domain:
            return ("yes", "malformed domain (URL parse failure)")
        if domain in PLATFORM_DOMAINS:
            return ("yes", f"shared-platform/non-org domain ({domain})")
        others = sorted(dom_synids[domain])
        if len(others) > 1:
            return ("yes", f"domain shared across {len(others)} synagogues: "
                           f"{','.join('#'+str(i) for i in others)}")
        if dom_rows[domain] > 1:
            return ("yes", "multiple URL variants (scheme/www) for same synagogue")
        return ("no", "")

    # ── cohorts + per-synagogue crawl stats (to explain the LLM reason) ───
    cur.execute("""
        SELECT
            s.id, s.name, s.state,
            CASE WHEN s.denomination_source = 'llm'
                      AND s.denomination_canonical = 'Unknown'
                 THEN 'llm_unknown' ELSE 'null_denomination' END           AS cohort,
            count(DISTINCT w.id)                                           AS n_sites,
            count(p.id)                                                    AS n_pages,
            count(p.id) FILTER (WHERE p.content_text IS NOT NULL
                                  AND p.content_text <> '')                AS n_text
        FROM synagogues s
        LEFT JOIN websites  w ON w.synagogue_id = s.id
        LEFT JOIN web_pages p ON p.website_id  = w.id
        WHERE s.denomination_canonical IS NULL
           OR (s.denomination_source = 'llm' AND s.denomination_canonical = 'Unknown')
        GROUP BY s.id, s.name, s.state, cohort
    """)
    syn_rows = cur.fetchall()

    def llm_reason(cohort, n_sites, n_pages, n_text):
        if cohort == "llm_unknown":
            return "LLM ran but could not determine denomination from page text"
        if n_sites == 0:
            return "No website on record — nothing to crawl or classify"
        if n_pages == 0:
            return "Website on record but never crawled (no pages stored)"
        if n_text == 0:
            return "Crawled but no extractable text (JS-only/empty pages or crawl error)"
        return "Has text but unclassified — eligible for an LLM re-run"

    # ── websites per synagogue ────────────────────────────────────────────
    cur.execute("SELECT synagogue_id, url, domain FROM websites")
    sites_by_syn = defaultdict(list)
    for syn_id, url, domain in cur.fetchall():
        sites_by_syn[syn_id].append((url, domain))

    rows = []
    for syn_id, name, state, cohort, n_sites, n_pages, n_text in syn_rows:
        reason = llm_reason(cohort, n_sites, n_pages, n_text)
        sites = sites_by_syn.get(syn_id, [])
        if not sites:
            rows.append((cohort, syn_id, name, state, "", "", "no", "", reason))
        for url, domain in sites:
            dflag, dreason = dup_info(domain)
            rows.append((cohort, syn_id, name, state, url, domain,
                         dflag, dreason, reason))

    # Synagogues with no website (blank URL) sort to the very bottom.
    rows.sort(key=lambda r: (r[4] == "", r[0], r[3] or "", (r[2] or "").lower(), r[1]))

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "cohort", "synagogue_id", "name", "state", "url", "domain",
            "potential_duplicate", "duplicate_reason", "llm_reason",
        ])
        w.writerows(rows)

    # ── summary ───────────────────────────────────────────────────────────
    n_syn = len(syn_rows)
    n_null = sum(1 for r in syn_rows if r[3] == "null_denomination")
    n_unk = sum(1 for r in syn_rows if r[3] == "llm_unknown")
    n_dup = sum(1 for r in rows if r[6] == "yes")
    print(f"Wrote {OUT}")
    print(f"  rows (per URL)        : {len(rows)}")
    print(f"  synagogues            : {n_syn}  (null={n_null}, llm_unknown={n_unk})")
    print(f"  links flagged dup     : {n_dup}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
