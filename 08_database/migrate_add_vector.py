"""
One-time migration: enable pgvector and add the embedding column.

Run once after switching docker-compose.yml to pgvector/pgvector:pg16:
    docker compose down && docker compose up -d
    python migrate_add_vector.py
"""

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "synagogues"),
    user=os.getenv("DB_USER", "research"),
    password=os.getenv("DB_PASSWORD"),
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
print("Extension 'vector' enabled.")

cur.execute("ALTER TABLE web_pages ADD COLUMN IF NOT EXISTS embedding vector(384)")
print("Column web_pages.embedding vector(384) ready.")

cur.execute("SELECT COUNT(*) FROM web_pages WHERE embedding IS NOT NULL")
print(f"Existing embeddings in DB: {cur.fetchone()[0]:,}")

cur.close()
conn.close()
print("Done.")
