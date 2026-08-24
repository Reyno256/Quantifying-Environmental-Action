"""
Deterministic denomination layer: if a synagogue's website URL/domain literally
contains a denomination name (e.g. 'chabad', 'reform', 'orthodox'), classify the
synagogue into that bucket. Useful for sites that were never crawled — the URL
alone carries the signal.

Fills rows with no denomination yet (denomination_canonical IS NULL) AND
upgrades rows currently labelled 'Unknown' when the URL implies a real bucket.
It never overrides a confident tag / America / LLM label. Written rows are
tagged denomination_source='url-keyword'. Fully reversible:
    UPDATE synagogues SET denomination_canonical=NULL, denomination_source=NULL
     WHERE denomination_source='url-keyword';
    -- then re-run migrate_add_denomination_canonical.py to restore any
    -- tag-derived 'Unknown' rows that were upgraded.

The tag migration's writes are source-guarded (source IN (NULL,'tag')), so
these url-keyword rows survive a re-run of that migration.

    python 08_database/backfill_denomination_from_url.py           # dry run
    python 08_database/backfill_denomination_from_url.py --apply   # write
"""

import os
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

APPLY = "--apply" in sys.argv

# (regex on lowercased url, bucket). Order matters: most specific first.
URL_RULES = [
    (re.compile(r"reconstruction"),            "Reconstructionist"),
    (re.compile(r"modern[\-_]?orthodox"),      "Modern Orthodox"),
    (re.compile(r"chabad|lubavitch"),          "Chabad"),
    (re.compile(r"conservative"),              "Conservative"),
    (re.compile(r"reform"),                    "Reform"),
    (re.compile(r"humanist"),                  "Humanistic"),
    (re.compile(r"renewal"),                   "Jewish Renewal"),
    (re.compile(r"orthodox"),                  "Orthodox"),
]


def bucket_for(url, domain):
    text = f"{domain or ''} {url or ''}".lower()
    for rx, bucket in URL_RULES:
        if rx.search(text):
            return bucket
    return None


def main():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    # one (synagogue, url) per row, with current state
    cur.execute("""
        SELECT s.id, w.url, w.domain,
               s.denomination_canonical, s.denomination_source
        FROM synagogues s JOIN websites w ON w.synagogue_id = s.id
    """)
    rows = cur.fetchall()

    # synagogue -> bucket implied by URL (skip synagogues with conflicting URLs)
    implied = {}
    conflict = set()
    for syn_id, url, domain, _can, _src in rows:
        b = bucket_for(url, domain)
        if not b:
            continue
        if syn_id in implied and implied[syn_id] != b:
            conflict.add(syn_id)
        else:
            implied[syn_id] = b

    # current canonical/source per synagogue
    state = {}
    for syn_id, _url, _dom, can, src in rows:
        state[syn_id] = (can, src)

    writable = []        # NULL or 'Unknown' -> set to URL bucket
    agree = disagree = 0  # confident label, NO change
    disagree_examples = []
    for syn_id, b in implied.items():
        if syn_id in conflict:
            continue
        can, src = state[syn_id]
        if can is None or can == "Unknown":
            writable.append((syn_id, b, "fill-NULL" if can is None else "upgrade-Unknown"))
        elif can == b:
            agree += 1
        else:
            disagree += 1
            if len(disagree_examples) < 12:
                disagree_examples.append((syn_id, can, src, b))

    fill_by_bucket = Counter(b for _s, b, _k in writable)
    n_null = sum(1 for _s, _b, k in writable if k == "fill-NULL")
    n_upg = sum(1 for _s, _b, k in writable if k == "upgrade-Unknown")

    print(f"{'DRY RUN — no writes' if not APPLY else 'APPLYING'}")
    print(f"\nSynagogues with a denomination keyword in URL : {len(implied)}")
    print(f"  conflicting URLs (skipped)                  : {len(conflict)}")
    print(f"\nRows that WOULD be written                    : {len(writable)}"
          f"  (fill NULL={n_null}, upgrade Unknown={n_upg})")
    for b, n in fill_by_bucket.most_common():
        print(f"    {b:<32s} {n}")
    print(f"\nConfident labels (NO change):")
    print(f"    URL agrees with existing label            : {agree}")
    print(f"    URL DISAGREES with existing label         : {disagree}")
    for syn_id, can, src, b in disagree_examples:
        print(f"      syn#{syn_id}: have {can!r} ({src}) but URL implies {b!r}")

    if conflict:
        cur.execute("""
            SELECT s.id, array_agg(w.url) FROM synagogues s
            JOIN websites w ON w.synagogue_id=s.id
            WHERE s.id = ANY(%s) GROUP BY s.id LIMIT 8
        """, (list(conflict),))
        print("\n  conflicting-URL examples:")
        for syn_id, urls in cur.fetchall():
            print(f"    syn#{syn_id}: {urls}")

    if not APPLY:
        print("\nRe-run with --apply to write the rows.")
        conn.close()
        return

    # ── apply (guarded: only NULL or 'Unknown' rows) ──────────────────────
    by_bucket = {}
    for syn_id, b, _k in writable:
        by_bucket.setdefault(b, []).append(syn_id)
    total = 0
    for b, ids in by_bucket.items():
        cur.execute("""
            UPDATE synagogues
               SET denomination_canonical = %s, denomination_source = 'url-keyword'
             WHERE id = ANY(%s)
               AND (denomination_canonical IS NULL OR denomination_canonical = 'Unknown')
        """, (b, ids))
        total += cur.rowcount
    print(f"\nWrote {total} synagogue(s) with source='url-keyword'.")

    cur.execute("""
        SELECT denomination_source, COUNT(*) FROM synagogues
        WHERE denomination_canonical IS NOT NULL GROUP BY 1 ORDER BY 2 DESC
    """)
    print("\nsynagogues.denomination_canonical by source:")
    for src, n in cur.fetchall():
        print(f"  {n:5d}  {src}")
    cur.execute("SELECT COUNT(*) FROM synagogues WHERE denomination_canonical IS NULL")
    print(f"  {cur.fetchone()[0]:5d}  (still NULL)")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
