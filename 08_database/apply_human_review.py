"""
Apply the human-augmented review of outliers / data issues / denominations to
the database.

Two hand-coded review files in the repo root drive this migration:

  1. outlier_audit.csv
       kNN/page-count outlier domains, each given a manual verdict in the
       "Real Synagogue Website(y, n, r)" column:
         y -> confirmed real synagogue website   (no DB change)
         r -> real synagogue, website missing/broken (kept; no DB change)
         n -> NOT a real synagogue (Nature Conservancy, Wikipedia, a city
              government site, a funeral home, ...) -> DELETE the synagogue.

  2. denominations_review_sample_messianic_removed_finished.csv
       a manual review of (name, state, url) giving a "Verified Denomination"
       and a free-text "Notes" column. Two signals are used:
         - Notes containing the word "remove" (Messianic congregations, Hillels,
           a Hindu temple, funeral homes, defunct/duplicate shells, ...)
             -> DELETE the synagogue.
         - A clean "Verified Denomination" that normalises to one of the
           canonical buckets -> overwrite synagogues.denomination_canonical at
           highest precedence (denomination_source = 'human-review'), beating
           the existing tag / America-Orig / llm sources.

Decisions baked in (per the project owner):
  * Removals are HARD DELETES. synagogues -> websites -> web_pages ->
    page_keyword_matches -> llm_chunk_classifications -> llm_chunk_framing all
    cascade (verified ON DELETE CASCADE), so the delete is clean. This is
    IRREVERSIBLE; a backup CSV of every deleted synagogue + its websites is
    written first.
  * Verified denominations OVERWRITE denomination_canonical at highest
    precedence. Verified values that do not map to a clean bucket
    (Messianic, multi-/dual-denomination, bare "non-denominational") are
    SKIPPED rather than overwriting a possibly-good existing value with
    'Unknown'.

The 17 rows whose Notes say "duplicate" (but not "remove") are a *different*
data issue (record de-duplication, not "this is not a synagogue") and are NOT
touched here -- they are listed at the end for follow-up.

NOTE on ordering: run this AFTER migrate_add_denomination_canonical.py.
That migration rebuilds denomination_canonical from the raw `denomination`
column on every run and would clobber the 'human-review' values written here.

Usage:
    python 08_database/apply_human_review.py            # dry run (default)
    python 08_database/apply_human_review.py --apply     # execute
"""

import argparse
import csv
import os
import re
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from migrate_add_denomination_canonical import CANONICAL_MAP

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

OUTLIER_CSV = ROOT / "outlier_audit.csv"
DENOM_CSV = ROOT / "denominations_review_sample_messianic_removed_finished.csv"
BACKUP_CSV = ROOT / "08_database" / "human_review_deleted_backup.csv"

REMOVE_RE = re.compile(r"\bremove\b", re.I)
DUP_RE = re.compile(r"duplicate", re.I)


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def domain_of(url):
    host = urllib.parse.urlparse(url).netloc or url
    return re.sub(r"^www\.", "", host).lower().rstrip("/")


def canonical_denomination(verified):
    """Map a free-text verified denomination to a canonical bucket, or None
    if it is empty / ambiguous (Messianic, multi-/dual-, bare non-denom)."""
    raw = verified.strip()
    if not raw or raw.lower().startswith("http"):
        return None
    for cand in (raw.lower(), raw.lower().replace(" - ", " "), raw.lower().replace("/", " ")):
        if cand in CANONICAL_MAP:
            return CANONICAL_MAP[cand]
    return None  # Messianic / multi- / dual / bare 'non-denominational' -> skip


def read_reviews():
    """Return (delete_reasons, denom_rows, dup_rows).

    delete_reasons: {domain -> reason str}
    denom_rows:     list of (domain, canonical_bucket) for clean verified denoms
    dup_rows:       list of (name, domain, note) for 'duplicate' (non-remove) rows
    """
    delete_reasons = {}
    denom_rows = []
    dup_rows = []

    with open(OUTLIER_CSV, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if len(row) <= 6:
                continue
            verdict = row[6].strip().lower()
            if verdict == "n":
                dom = re.sub(r"^www\.", "", row[0].strip().lower())
                delete_reasons.setdefault(dom, f"outlier_audit: not a synagogue (flags={row[5]})")

    with open(DENOM_CSV, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if not any(row):
                continue
            name, state, url, vd, notes = (row + [""] * 5)[:5]
            dom = domain_of(url.strip())
            if REMOVE_RE.search(notes):
                delete_reasons.setdefault(dom, f"denominations review: {notes.strip()}")
            else:
                if DUP_RE.search(notes):
                    dup_rows.append((name.strip(), dom, notes.strip()))
                bucket = canonical_denomination(vd)
                if bucket:
                    denom_rows.append((dom, bucket))

    return delete_reasons, denom_rows, dup_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = ap.parse_args()

    delete_reasons, denom_rows, dup_rows = read_reviews()

    conn = get_conn()
    cur = conn.cursor()

    # domain -> set of synagogue ids that own a website on that domain
    cur.execute("SELECT lower(domain), synagogue_id FROM websites")
    dom2sids = defaultdict(set)
    for dom, sid in cur.fetchall():
        dom2sids[dom].add(sid)

    # ---- resolve deletes ----
    sid_reason = {}
    unmatched_del = []
    for dom, reason in delete_reasons.items():
        sids = dom2sids.get(dom)
        if not sids:
            unmatched_del.append(dom)
            continue
        for sid in sids:
            sid_reason.setdefault(sid, reason)
    delete_sids = sorted(sid_reason)

    # ---- resolve denomination updates (excluding to-be-deleted) ----
    sid_bucket = {}
    for dom, bucket in denom_rows:
        for sid in dom2sids.get(dom, set()):
            if sid in sid_reason:
                continue
            sid_bucket[sid] = bucket  # later clean rows win; all clean anyway

    # ---- report ----
    print(f"DELETE: {len(delete_sids)} synagogues "
          f"(from {len(delete_reasons)} flagged domains, {len(unmatched_del)} unmatched)")
    if unmatched_del:
        print("  unmatched delete domains:", ", ".join(unmatched_del))
    print(f"DENOMINATION overwrite: {len(sid_bucket)} synagogues -> 'human-review'")
    if dup_rows:
        print(f"\n{len(dup_rows)} 'duplicate'-noted rows left untouched (de-dup is a separate task):")
        for name, dom, note in dup_rows:
            print(f"    {name[:32]:32s} {dom:34s} {note}")

    # ---- backup the synagogues that will be deleted ----
    cur.execute(
        "SELECT id, name, state, source, denomination, denomination_canonical "
        "FROM synagogues WHERE id = ANY(%s) ORDER BY id",
        (delete_sids,),
    )
    syn_rows = cur.fetchall()
    cur.execute(
        "SELECT synagogue_id, url, domain, has_error FROM websites "
        "WHERE synagogue_id = ANY(%s) ORDER BY synagogue_id",
        (delete_sids,),
    )
    sid_urls = defaultdict(list)
    for sid, url, dom, has_err in cur.fetchall():
        sid_urls[sid].append(url)

    BACKUP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKUP_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "state", "source", "denomination",
                    "denomination_canonical", "delete_reason", "websites"])
        for sid, name, state, src, denom, denom_c in syn_rows:
            w.writerow([sid, name, state, src, denom, denom_c,
                        sid_reason.get(sid, ""), " | ".join(sid_urls.get(sid, []))])
    print(f"\nBackup of {len(syn_rows)} to-be-deleted synagogues written to {BACKUP_CSV}")

    if not args.apply:
        print("\nDRY RUN — no changes made. Re-run with --apply to execute.")
        conn.close()
        return

    # ---- execute ----
    if delete_sids:
        cur.execute("DELETE FROM synagogues WHERE id = ANY(%s)", (delete_sids,))
        print(f"Deleted {cur.rowcount} synagogues (cascaded to websites/pages/classifications).")
    for sid, bucket in sid_bucket.items():
        cur.execute(
            "UPDATE synagogues SET denomination_canonical = %s, "
            "denomination_source = 'human-review' WHERE id = %s",
            (bucket, sid),
        )
    print(f"Updated denomination_canonical on {len(sid_bucket)} synagogues "
          f"(source='human-review').")

    conn.commit()

    # ---- post-state ----
    cur.execute("SELECT count(*) FROM synagogues")
    print(f"\nsynagogues now: {cur.fetchone()[0]}")
    cur.execute("SELECT denomination_source, count(*) FROM synagogues "
                "GROUP BY denomination_source ORDER BY 2 DESC")
    print("denomination_source distribution:")
    for src, n in cur.fetchall():
        print(f"  {n:5d}  {src or '(NULL)'}")
    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
