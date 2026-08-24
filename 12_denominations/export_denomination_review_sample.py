"""
Build a manual-review CSV combining four groups of interest:

  llm_unknown            - LLM ran but returned 'Unknown'           (105 synagogues)
  llm_not_run_has_website- denomination NULL but the synagogue HAS a
                           website (LLM had no text to read)        (365 synagogues)
  llm_classified_sample  - a random, seeded sample of synagogues the
                           LLM DID classify into a real bucket, EXCLUDING sites
                           with 'chabad' in the URL (trivial confirms)  (20)

Social-media links (facebook, instagram, x/twitter, youtube, …) are dropped
entirely — they are never the synagogue's own site. If that leaves an
otherwise-interesting synagogue with no link, it still gets one blank-URL row.

One row per (synagogue, URL). A row can belong to several groups at once; the
`groups` column lists all that apply. LLM verdict + raw response are included so
the sampled / Unknown rows can be spot-checked. Sample is reproducible (SEED).

    python 12_denominations/export_denomination_review_sample.py [OUT.csv]
"""

import csv
import os
import random
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

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "denomination_review_sample.csv"
SAMPLE_N = 20
SEED = 42

# Social media / link aggregators — dropped from the file entirely.
SOCIAL_DOMAINS = {
    "facebook.com", "m.facebook.com", "fb.com", "instagram.com", "twitter.com",
    "x.com", "youtube.com", "youtu.be", "linkedin.com", "tiktok.com",
    "pinterest.com", "linktr.ee",
}
def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def is_social(url, domain):
    # Match the parsed domain, but also scan the raw URL: malformed links like
    # "http://https://www.facebook.com/..." parse to domain 'https:' and would
    # otherwise slip through.
    if domain in SOCIAL_DOMAINS:
        return True
    u = (url or "").lower()
    return any(s in u for s in SOCIAL_DOMAINS)


def main():
    conn = get_conn()
    cur = conn.cursor()

    # ── all websites (social excluded) ────────────────────────────────────
    cur.execute("SELECT synagogue_id, url, domain FROM websites")
    websites = [r for r in cur.fetchall() if not is_social(r[1], r[2])]
    sites_by_syn = defaultdict(list)
    for syn_id, url, domain in websites:
        sites_by_syn[syn_id].append((url, domain))

    # ── group membership sets ─────────────────────────────────────────────
    cur.execute("SELECT id FROM synagogues "
                "WHERE denomination_source='llm' AND denomination_canonical='Unknown'")
    s_unknown = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT DISTINCT s.id FROM synagogues s "
                "JOIN websites w ON w.synagogue_id=s.id "
                "WHERE s.denomination_canonical IS NULL")
    s_notrun = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT id FROM synagogues "
                "WHERE denomination_source='llm' AND denomination_canonical<>'Unknown'")
    classified = sorted(r[0] for r in cur.fetchall())
    # Drop sites with 'chabad' in any URL/domain from the sampling pool — an LLM
    # 'Chabad' verdict on a chabad URL is a trivial confirm, not worth reviewing.
    chabad_url_syns = {
        syn_id for syn_id, urls in sites_by_syn.items()
        if any("chabad" in f"{d or ''} {u or ''}".lower() for u, d in urls)
    }
    pool = [i for i in classified if i not in chabad_url_syns]
    random.seed(SEED)
    s_sample = set(random.sample(pool, min(SAMPLE_N, len(pool))))

    # ── synagogue + LLM verdict lookups ───────────────────────────────────
    cur.execute("SELECT id, name, state, denomination_canonical, denomination_source "
                "FROM synagogues")
    syn = {r[0]: r[1:] for r in cur.fetchall()}

    cur.execute("SELECT synagogue_id, label, raw_response "
                "FROM denomination_llm_classifications")
    llm = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    null_reason_cache = {}

    def null_reason(syn_id):
        if syn_id not in null_reason_cache:
            cur.execute("""
                SELECT count(p.id),
                       count(p.id) FILTER (WHERE p.content_text IS NOT NULL
                                            AND p.content_text <> '')
                FROM websites w LEFT JOIN web_pages p ON p.website_id=w.id
                WHERE w.synagogue_id=%s
            """, (syn_id,))
            n_pages, n_text = cur.fetchone()
            null_reason_cache[syn_id] = (
                "Website on record but never crawled (no pages stored)" if n_pages == 0
                else "Crawled but no extractable text (JS-only/empty pages or crawl error)"
                if n_text == 0
                else "Has text but unclassified — eligible for an LLM re-run")
        return null_reason_cache[syn_id]

    # ── assemble row universe (per synagogue,url) ─────────────────────────
    full_syns = s_unknown | s_notrun | s_sample
    seen = set()
    rows = []

    def make_row(syn_id, url, domain):
        name, state, denom, source = syn.get(syn_id, ("", "", "", ""))
        groups = []
        if syn_id in s_unknown:
            groups.append("llm_unknown")
        if syn_id in s_notrun:
            groups.append("llm_not_run_has_website")
        if syn_id in s_sample:
            groups.append("llm_classified_sample")
        label, raw = llm.get(syn_id, ("", ""))
        if syn_id in s_unknown:
            note = "LLM ran but could not determine denomination from page text"
        elif syn_id in s_notrun:
            note = null_reason(syn_id)
        else:
            note = ""
        return [
            "+".join(groups), syn_id, name, state, url, domain,
            denom or "", source or "", label or "",
            (raw or "").replace("\n", " ").strip(), note,
        ]

    def emit(syn_id, url, domain):
        if (syn_id, url) in seen:
            return
        seen.add((syn_id, url))
        rows.append(make_row(syn_id, url, domain))

    for syn_id in full_syns:
        urls = sites_by_syn.get(syn_id, [])
        if urls:
            for url, domain in urls:
                emit(syn_id, url, domain)
        else:
            # all of this synagogue's links were social (or none) — keep it anyway
            emit(syn_id, "", "")

    order = {"llm_unknown": 0, "llm_not_run_has_website": 1,
             "llm_classified_sample": 2}
    rows.sort(key=lambda r: (
        min((order[g] for g in r[0].split("+")), default=9),
        r[3] or "", (r[2] or "").lower(), r[1],
    ))

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "groups", "synagogue_id", "name", "state", "url", "domain",
            "denomination_canonical", "denomination_source",
            "llm_label", "llm_raw_response", "note",
        ])
        w.writerows(rows)

    def count(g):
        return sum(1 for r in rows if g in r[0].split("+"))
    print(f"Wrote {OUT}")
    print(f"  total rows (per URL)        : {len(rows)}")
    print(f"  llm_unknown links           : {count('llm_unknown')}")
    print(f"  llm_not_run_has_website     : {count('llm_not_run_has_website')}")
    print(f"  llm_classified_sample       : {count('llm_classified_sample')}  "
          f"(synagogues: {len(s_sample)}, seed={SEED})")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
