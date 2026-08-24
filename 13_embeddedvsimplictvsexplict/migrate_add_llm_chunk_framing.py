"""
One-time migration: create llm_chunk_framing table.

Run once after llm_chunk_classifications has been populated:
    python 13_embeddedvsimplictvsexplict/migrate_add_llm_chunk_framing.py
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
    CREATE TABLE IF NOT EXISTS llm_chunk_framing (
        id            SERIAL PRIMARY KEY,
        chunk_id      INTEGER NOT NULL REFERENCES llm_chunk_classifications(id) ON DELETE CASCADE,
        framing       TEXT,
        model_used    VARCHAR(60),
        temperature   NUMERIC(4,3),
        error         TEXT,
        classified_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
""")
print("Table llm_chunk_framing ready.")

cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS lcf_chunk_unique
    ON llm_chunk_framing(chunk_id)
""")
print("Index lcf_chunk_unique ready.")

cur.execute("""
    CREATE INDEX IF NOT EXISTS lcf_framing_idx
    ON llm_chunk_framing(framing)
""")
print("Index lcf_framing_idx ready.")

cur.execute("SELECT COUNT(*) FROM llm_chunk_framing")
print(f"Existing rows in llm_chunk_framing: {cur.fetchone()[0]:,}")

cur.close()
conn.close()
print("Done.")
