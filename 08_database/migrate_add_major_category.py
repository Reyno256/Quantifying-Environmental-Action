"""
One-time migration: add llm_chunk_classifications.major_category and populate
it by bucketing each row's `category` (one of the 76 thesis action labels, or
'N/A') into one of the 9 major categories used throughout 11_analysis/:

    Community, Spirituality & Worship, Kitchen, Waste, Energy,
    Operations & Maintenance, Environmental & Climate Justice, Water, Other

This makes the bucket a persisted attribute of the classification itself
instead of something every analysis script re-derives from `category` text at
read time. The bucketing rules are NOT duplicated here — they're imported from
11_analysis/recreate_existing/recreate_common.py (classify_action / _RULES),
which is the single validated source of truth (checked by hand against all 76
distinct action strings). Keep that the only place the rules are edited;
re-run this migration after any change there.

Idempotent: safe to re-run. major_category is fully rebuilt from `category`
on every run.

    python 08_database/migrate_add_major_category.py
"""

import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "11_analysis" / "recreate_existing"))
from recreate_common import classify_action, pure_action  # noqa: E402


def main():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "ALTER TABLE llm_chunk_classifications "
        "ADD COLUMN IF NOT EXISTS major_category TEXT"
    )
    print("Column llm_chunk_classifications.major_category ready.")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS lcc_major_category_idx "
        "ON llm_chunk_classifications(major_category)"
    )
    print("Index lcc_major_category_idx ready.")

    # Rebuilt from scratch every run: N/A (no action) -> NULL.
    cur.execute("UPDATE llm_chunk_classifications SET major_category = NULL "
                "WHERE category = 'N/A'")

    cur.execute("SELECT id, category FROM llm_chunk_classifications WHERE category <> 'N/A'")
    rows = cur.fetchall()

    updates = []
    unmapped = {}
    for chunk_id, category in rows:
        bucket = classify_action(pure_action(category))
        if bucket == "??UNMAPPED":
            unmapped.setdefault(category, 0)
            unmapped[category] += 1
            continue
        updates.append((chunk_id, bucket))

    if unmapped:
        raise ValueError(
            f"{len(unmapped)} distinct action string(s) did not classify; "
            f"update _RULES in recreate_common.py:\n"
            + "\n".join(f"  - ({n}x) {a!r}" for a, n in sorted(unmapped.items())[:20])
        )

    psycopg2.extras.execute_values(
        cur,
        "UPDATE llm_chunk_classifications AS t "
        "SET major_category = v.major_category "
        "FROM (VALUES %s) AS v(id, major_category) "
        "WHERE t.id = v.id",
        updates,
        page_size=1000,
    )
    print(f"Backfilled major_category for {len(updates):,} rows.")

    cur.execute(
        "SELECT major_category, COUNT(*) FROM llm_chunk_classifications "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    print("\nmajor_category distribution:")
    for bucket, n in cur.fetchall():
        label = bucket if bucket is not None else "(NULL — N/A category)"
        print(f"  {n:7,d}  {label}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
