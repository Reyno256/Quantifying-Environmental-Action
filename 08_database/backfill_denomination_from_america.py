"""
Backfill synagogues.denomination_canonical for rows that have no denomination
of their own, using the human-baseline America_Orig.csv ("Denominational
Affiliation" column). Joins America rows to DB synagogues by website domain.

Layer ordering (run after the tag migration):
    python 08_database/migrate_add_denomination_canonical.py   # source='tag'
    python 08_database/backfill_denomination_from_america.py   # source='America-Orig'

Only fills rows where denomination_canonical IS NULL (i.e. the synagogue had no
denomination tag), so the synagogue's own tag always wins over the baseline.
Idempotent: re-running only touches still-NULL rows. The tag migration's reset
is scoped to source IN (NULL,'tag'), so it will not wipe these rows.

America_Orig's affiliations are already clean (exactly the 10 canonical
buckets); we only fix casing to match the tag migration's bucket spelling.
"""

import csv
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

# America_Orig.csv is gitignored (data file), so it may live in the main
# checkout rather than a git worktree. Override with AMERICA_CSV if needed.
AMERICA_CSV = Path(os.getenv("AMERICA_CSV", ROOT / "America_Orig.csv"))

# America_Orig affiliation -> canonical bucket spelling used by the tag migration.
AFFILIATION_MAP = {
    "chabad": "Chabad",
    "reform": "Reform",
    "orthodox": "Orthodox",
    "conservative": "Conservative",
    "non-denominational progressive": "Non-Denominational Progressive",
    "modern orthodox": "Modern Orthodox",
    "non-denominational conservative": "Non-Denominational Conservative",
    "reconstructionist": "Reconstructionist",
    "humanistic": "Humanistic",
    "jewish renewal": "Jewish Renewal",
}


def norm_domain(url):
    """Bare registrable host: drop scheme, 'www.', path, port. Match DB.domain."""
    url = (url or "").strip().lower()
    if not url:
        return None
    if "://" not in url:
        url = "http://" + url
    host = urlparse(url).netloc.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def load_america():
    """domain -> canonical bucket, dropping domains with conflicting labels."""
    by_domain = defaultdict(set)
    unmapped = set()
    # America_Orig.csv is latin-1 encoded.
    with open(AMERICA_CSV, encoding="latin-1", newline="") as fh:
        for row in csv.DictReader(fh):
            dom = norm_domain(row.get("Website"))
            aff = (row.get("Denominational Affiliation") or "").strip()
            bucket = AFFILIATION_MAP.get(aff.lower())
            if not dom:
                continue
            if bucket is None:
                if aff:
                    unmapped.add(aff)
                continue
            by_domain[dom].add(bucket)

    if unmapped:
        print(f"  WARNING: unmapped affiliations skipped: {sorted(unmapped)}")

    resolved, conflicts = {}, []
    for dom, buckets in by_domain.items():
        if len(buckets) == 1:
            resolved[dom] = next(iter(buckets))
        else:
            conflicts.append((dom, sorted(buckets)))
    if conflicts:
        print(f"  {len(conflicts)} domain(s) had conflicting labels, skipped:")
        for dom, buckets in conflicts[:10]:
            print(f"    {dom}: {buckets}")
    return resolved


def main():
    domain_to_bucket = load_america()
    print(f"America_Orig: {len(domain_to_bucket)} usable domain->bucket entries.")

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Candidate rows: synagogues with no canonical denomination yet, that own at
    # least one website domain. Resolve to a single bucket via the America map.
    cur.execute(
        "SELECT s.id, w.domain FROM synagogues s "
        "JOIN websites w ON w.synagogue_id = s.id "
        "WHERE s.denomination_canonical IS NULL"
    )
    rows = cur.fetchall()

    # A synagogue may have multiple websites; collect the buckets each implies.
    syn_buckets = defaultdict(set)
    for syn_id, domain in rows:
        bucket = domain_to_bucket.get((domain or "").lower())
        if bucket:
            syn_buckets[syn_id].add(bucket)

    updates, skipped_conflict = [], 0
    for syn_id, buckets in syn_buckets.items():
        if len(buckets) == 1:
            updates.append((syn_id, next(iter(buckets))))
        else:
            skipped_conflict += 1

    by_bucket = defaultdict(list)
    for syn_id, bucket in updates:
        by_bucket[bucket].append(syn_id)

    total = 0
    for bucket, ids in by_bucket.items():
        cur.execute(
            "UPDATE synagogues "
            "SET denomination_canonical = %s, denomination_source = 'America-Orig' "
            "WHERE id = ANY(%s) AND denomination_canonical IS NULL",
            (bucket, ids),
        )
        total += cur.rowcount

    print(f"\nBackfilled {total} synagogue(s) from America_Orig.")
    if skipped_conflict:
        print(f"Skipped {skipped_conflict} synagogue(s) with conflicting "
              f"America labels across their domains.")

    # ── Report ───────────────────────────────────────────────────────────
    cur.execute(
        "SELECT denomination_canonical, COUNT(*) FROM synagogues "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    print("\nCanonical distribution (all sources):")
    for bucket, n in cur.fetchall():
        label = bucket if bucket is not None else "(NULL — still no denomination)"
        print(f"  {n:5d}  {label}")

    cur.execute(
        "SELECT denomination_source, COUNT(*) FROM synagogues "
        "WHERE denomination_canonical IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    )
    print("\nBy source:")
    for src, n in cur.fetchall():
        print(f"  {n:5d}  {src}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
