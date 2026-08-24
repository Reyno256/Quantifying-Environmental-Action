"""
Apply the human review of the "Non-Denominational Conservative" / "Non-
Denominational Progressive" buckets to the database.

Source file (repo root, gitignored): non_denominational_conservative_synagogues.csv
Columns: name, address, state, website, lat, lon, denomination_source
         (prior source, informational only), <blank>, verdict, notes

The reviewer's verdict (column 8, "TY notes" in the header) is CASE-SENSITIVE:
    'conservative' (lowercase)  -> confirmed Non-Denominational Conservative
    'progressive'  (lowercase)  -> confirmed Non-Denominational Progressive
    'Conservative' (capital C)  -> actually denomination-affiliated Conservative
                                   (notes explain: "part of the Conservative
                                   movement (upper case C)")
    'Orthodox' / 'Reform' / 'Chabad' / 'Renewal' -> actually affiliated with
                                   that movement, not non-denominational
    'Reform/Conservative' -> dual-affiliation, bucketed as Conservative
                                   per explicit project-owner call
    (any other unrecognized multi-value or blank verdict without a remove
     flag) -> ambiguous, SKIP
    blank                        -> no clean verdict; see notes column

The word "remove" (whole word, case-insensitive) can appear in EITHER the
verdict column or the notes column (e.g. verdict "karaite - remove" vs. verdict
"conservative" + notes "duplicate entry - remove" for Kehilat Hadar NY). Any
row with "remove" in either column is a hard delete, regardless of what the
verdict column says.

This file is the HIGHEST-PRIORITY human source of truth: on disagreement it
overrides any existing denomination_canonical, including a prior
denomination_source = 'human-review' row from the earlier
denominations_review_sample_messianic_removed_finished.csv pass. It is
additive -- domains not present in this file are untouched.

Decisions baked in (per the project owner, consistent with apply_human_review.py):
  * Removals are HARD DELETES, cascading synagogues -> websites -> web_pages ->
    page_keyword_matches -> llm_chunk_classifications -> llm_chunk_framing.
    IRREVERSIBLE; a backup CSV of every deleted synagogue + its websites is
    written first.
  * Clean verdicts OVERWRITE denomination_canonical, source = 'human-review'.

Usage:
    python 08_database/apply_conservative_review.py            # dry run (default)
    python 08_database/apply_conservative_review.py --apply    # execute
"""

import argparse
import csv
import os
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

REVIEW_CSV = ROOT / "non_denominational_conservative_synagogues.csv"
BACKUP_CSV = ROOT / "08_database" / "conservative_review_deleted_backup.csv"

REMOVE_RE = re.compile(r"\bremove\b", re.I)

# Exact (case-sensitive) verdict -> canonical bucket.
VERDICT_MAP = {
    "conservative": "Non-Denominational Conservative",
    "progressive": "Non-Denominational Progressive",
    "Conservative": "Conservative",
    "Orthodox": "Orthodox",
    "Reform": "Reform",
    "Chabad": "Chabad",
    "Renewal": "Jewish Renewal",
    # Dual-affiliation "Reform/Conservative" (Temple Beth El of South Orange
    # County) -- project owner's explicit call: bucket as Conservative.
    "Reform/Conservative": "Conservative",
}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def domain_of(url):
    host = urllib.parse.urlparse(url).netloc or url
    return re.sub(r"^www\.", "", host).lower().rstrip("/")


def read_review():
    """Return (delete_reasons, denom_rows, skipped_rows).

    delete_reasons: {domain -> reason str}
    denom_rows:     list of (domain, canonical_bucket, name) for clean verdicts
    skipped_rows:   list of (name, domain, verdict, notes) with no clean verdict
                    and no remove flag (ambiguous / blank)
    """
    delete_reasons = {}
    denom_rows = []
    skipped_rows = []

    with open(REVIEW_CSV, newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if not any(row):
                continue
            row = (row + [""] * 10)[:10]
            name, address, state, url, lat, lon, prior_source, _, verdict, notes = row
            name, url = name.strip(), url.strip()
            verdict, notes = verdict.strip(), notes.strip()
            if not url:
                continue
            dom = domain_of(url)

            if REMOVE_RE.search(verdict) or REMOVE_RE.search(notes):
                reason = f"{verdict} | {notes}".strip(" |")
                delete_reasons.setdefault(dom, f"conservative_review: {reason}")
                continue

            bucket = VERDICT_MAP.get(verdict)
            if bucket:
                denom_rows.append((dom, bucket, name))
            else:
                skipped_rows.append((name, dom, verdict, notes))

    return delete_reasons, denom_rows, skipped_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = ap.parse_args()

    delete_reasons, denom_rows, skipped_rows = read_review()

    conn = get_conn()
    cur = conn.cursor()

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
    unmatched_denom = []
    for dom, bucket, name in denom_rows:
        sids = dom2sids.get(dom)
        if not sids:
            unmatched_denom.append((name, dom))
            continue
        for sid in sids:
            if sid in sid_reason:
                continue
            sid_bucket[sid] = bucket

    # ---- report ----
    print(f"DELETE: {len(delete_sids)} synagogues "
          f"(from {len(delete_reasons)} flagged domains, {len(unmatched_del)} unmatched)")
    if unmatched_del:
        print("  unmatched delete domains:", ", ".join(unmatched_del))
    print(f"DENOMINATION overwrite: {len(sid_bucket)} synagogues -> 'human-review'")
    if unmatched_denom:
        print(f"  unmatched denomination domains ({len(unmatched_denom)}):")
        for name, dom in unmatched_denom:
            print(f"    {name[:40]:40s} {dom}")
    if skipped_rows:
        print(f"\n{len(skipped_rows)} rows with no clean verdict and no remove flag, left untouched:")
        for name, dom, verdict, notes in skipped_rows:
            print(f"    {name[:32]:32s} {dom:34s} verdict={verdict!r} notes={notes!r}")

    # ---- backup the synagogues that will be deleted ----
    cur.execute(
        "SELECT id, name, state, source, denomination, denomination_canonical "
        "FROM synagogues WHERE id = ANY(%s) ORDER BY id",
        (delete_sids,),
    )
    syn_rows = cur.fetchall()
    cur.execute(
        "SELECT synagogue_id, url FROM websites "
        "WHERE synagogue_id = ANY(%s) ORDER BY synagogue_id",
        (delete_sids,),
    )
    sid_urls = defaultdict(list)
    for sid, url in cur.fetchall():
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
