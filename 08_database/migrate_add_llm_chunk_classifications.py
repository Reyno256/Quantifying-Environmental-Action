"""
One-time migration: create llm_chunk_classifications table.

Run once after migrate_add_keyword_matches.py:
    python 08_database/migrate_add_llm_chunk_classifications.py
"""

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 8989)),
    dbname=os.getenv("DB_NAME", "synagogues"),
    user=os.getenv("DB_USER", "research"),
    password=os.getenv("DB_PASSWORD"),
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS llm_chunk_classifications (
        id            SERIAL PRIMARY KEY,
        match_id      INTEGER NOT NULL REFERENCES page_keyword_matches(id) ON DELETE CASCADE,
        chunk_text    TEXT,
        category      TEXT,
        model_used    VARCHAR(60),
        temperature   NUMERIC(4,3),
        error         TEXT,
        classified_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
""")
print("Table llm_chunk_classifications ready.")

cur.execute("""
    CREATE INDEX IF NOT EXISTS lcc_match_idx
    ON llm_chunk_classifications(match_id)
""")
print("Index lcc_match_idx ready.")

cur.execute("""
    CREATE INDEX IF NOT EXISTS lcc_category_idx
    ON llm_chunk_classifications(category)
""")
print("Index lcc_category_idx ready.")

cur.execute("SELECT COUNT(*) FROM llm_chunk_classifications")
print(f"Existing rows in llm_chunk_classifications: {cur.fetchone()[0]:,}")

cur.close()
conn.close()
print("Done.")
