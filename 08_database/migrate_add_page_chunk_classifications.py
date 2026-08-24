"""
One-time migration: create page_chunk_classifications table.

One row per fixed-size (500-char, whitespace-snapped) chunk of every page's
content_text, classified independently by the Gemini judge — the "chunk vs
keyword/embedding" method validated in 06_environmental_classification/
chunk_vs_keyword_embed.py + chunk_gemini_ground_truth.py. Unlike
llm_chunk_classifications (one row per page_keyword_matches span — only pages
with a keyword hit get a row), every page with content_text gets chunked and
classified here, which is the point of this method: it can surface positives
the keyword/span pipeline never produced a row for in the first place.

Idempotent: safe to re-run.

    python 08_database/migrate_add_page_chunk_classifications.py
    python 08_database/migrate_add_page_chunk_classifications.py --prefix canada_
"""

import argparse
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="",
                    help='table-name prefix, e.g. "canada_" (default US tables)')
    args = ap.parse_args()
    P = args.prefix

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {P}page_chunk_classifications (
            id             SERIAL PRIMARY KEY,
            web_page_id    INTEGER NOT NULL REFERENCES {P}web_pages(id) ON DELETE CASCADE,
            chunk_index    INTEGER NOT NULL,   -- 0-based index within the page's
                                                -- deterministic chunk_text() sequence
            chunk_text     TEXT,               -- exact excerpt sent to the LLM
            category       TEXT,               -- one of the 76 labels, or 'N/A'
            major_category TEXT,               -- 9-bucket rollup of category;
                                                -- NULL when category = 'N/A'
            model_used     VARCHAR(60),
            temperature    NUMERIC(4,3),
            error          TEXT,
            classified_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (web_page_id, chunk_index)
        )
    """)
    print(f"Table {P}page_chunk_classifications ready.")

    # No separate index on web_page_id alone: the UNIQUE(web_page_id, chunk_index)
    # constraint's composite btree already indexes WHERE web_page_id = ... lookups.

    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS {P}pcc_category_idx
        ON {P}page_chunk_classifications(category)
    """)
    print(f"Index {P}pcc_category_idx ready.")

    cur.execute(f"""
        CREATE INDEX IF NOT EXISTS {P}pcc_major_category_idx
        ON {P}page_chunk_classifications(major_category)
    """)
    print(f"Index {P}pcc_major_category_idx ready.")

    cur.execute(f"SELECT COUNT(*) FROM {P}page_chunk_classifications")
    print(f"Existing rows in {P}page_chunk_classifications: {cur.fetchone()[0]:,}")

    cur.execute(f"""
        SELECT major_category, COUNT(*) FROM {P}page_chunk_classifications
        GROUP BY 1 ORDER BY 2 DESC
    """)
    rows = cur.fetchall()
    if rows:
        print("\nmajor_category distribution:")
        for bucket, n in rows:
            print(f"  {n:7,d}  {bucket or '(NULL — N/A / not yet classified)'}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
