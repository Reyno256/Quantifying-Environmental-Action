"""
One-time migration: create page_keyword_matches table.

Run once on an existing database before running classify_environmental.py:
    python 08_database/migrate_add_keyword_matches.py
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
    CREATE TABLE IF NOT EXISTS page_keyword_matches (
        id           SERIAL PRIMARY KEY,
        web_page_id  INTEGER NOT NULL REFERENCES web_pages(id) ON DELETE CASCADE,
        keyword      TEXT    NOT NULL,
        start_offset INTEGER NOT NULL,
        end_offset   INTEGER NOT NULL
    )
""")
print("Table page_keyword_matches ready.")

cur.execute("""
    CREATE INDEX IF NOT EXISTS pkm_page_idx
    ON page_keyword_matches(web_page_id)
""")
print("Index pkm_page_idx ready.")

cur.execute("""
    CREATE INDEX IF NOT EXISTS pkm_keyword_idx
    ON page_keyword_matches(keyword)
""")
print("Index pkm_keyword_idx ready.")

cur.execute("SELECT COUNT(*) FROM page_keyword_matches")
print(f"Existing rows in page_keyword_matches: {cur.fetchone()[0]:,}")

cur.close()
conn.close()
print("Done.")
