"""
One-time migration: add crawl-outcome columns to websites.

    has_error   BOOLEAN NOT NULL DEFAULT FALSE
    error_text  TEXT  (NULL unless has_error)

Adding the columns with DEFAULT FALSE marks every existing website row
(including all previously-crawled sites) as has_error = FALSE. We then
explicitly re-assert that any site with >=1 web_page is has_error = FALSE,
so "has pages => success" holds regardless of later updates.

Run once on an existing database:
    python 08_database/migrate_add_crawl_errors.py
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

cur.execute("ALTER TABLE websites ADD COLUMN IF NOT EXISTS has_error BOOLEAN NOT NULL DEFAULT FALSE")
cur.execute("ALTER TABLE websites ADD COLUMN IF NOT EXISTS error_text TEXT")
print("Columns websites.has_error / websites.error_text ready.")

# Any site that already has crawled pages is, by definition, a success.
cur.execute("""
    UPDATE websites w
       SET has_error = FALSE, error_text = NULL
     WHERE EXISTS (SELECT 1 FROM web_pages p WHERE p.website_id = w.id)
""")
print(f"Re-asserted has_error = FALSE for {cur.rowcount:,} sites with >=1 web_page.")

cur.execute("SELECT count(*) FILTER (WHERE has_error), count(*) FROM websites")
err, total = cur.fetchone()
print(f"websites: {err:,} has_error=TRUE / {total:,} total")

cur.close()
conn.close()
print("Done.")
