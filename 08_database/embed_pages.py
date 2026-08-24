"""
Compute and store all-MiniLM-L6-v2 embeddings for every row in web_pages.

Prerequisites:
    1. pgvector extension enabled   (python migrate_add_vector.py)
    2. content_text populated       (python populate_content_text.py)

Usage:
    python embed_pages.py
    python embed_pages.py --batch 256   # encoding batch size (default 256)
    python embed_pages.py --device cpu  # override device (mps/cpu)
"""

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from _embed import encode, load_model, to_pgvector

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch",  type=int, default=256)
    parser.add_argument("--device", type=str, default=None, help="mps or cpu")
    args = parser.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    # Verify the embedding column exists
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'web_pages' AND column_name = 'embedding'
    """)
    if not cur.fetchone():
        sys.exit("Column 'embedding' not found. Run migrate_add_vector.py first.")

    cur.execute("""
        SELECT id, LEFT(content_text, 2000)
        FROM web_pages
        WHERE embedding IS NULL
          AND content_text IS NOT NULL
          AND content_text != ''
        ORDER BY id
    """)
    rows  = cur.fetchall()
    total = len(rows)

    if total == 0:
        print("All pages already have embeddings.")
        cur.close(); conn.close(); return

    # Load model and report device before starting the clock
    _, _, device = load_model(args.device)
    print(f"Pages to embed : {total:,}")
    print(f"Batch size     : {args.batch}")
    print(f"Device         : {device}\n", flush=True)

    t_start = time.perf_counter()
    done    = 0

    for i in range(0, total, args.batch):
        chunk = rows[i : i + args.batch]
        ids   = [r[0] for r in chunk]
        texts = [r[1] for r in chunk]

        embs    = encode(texts, batch_size=args.batch)
        updates = [(to_pgvector(embs[j]), ids[j]) for j in range(len(ids))]

        psycopg2.extras.execute_batch(
            cur,
            "UPDATE web_pages SET embedding = %s::vector WHERE id = %s",
            updates,
            page_size=len(updates),
        )
        conn.commit()

        done   += len(chunk)
        elapsed = time.perf_counter() - t_start
        rate    = done / elapsed
        eta     = (total - done) / rate if done < total else 0
        print(f"  {done:>6,}/{total:,} ({done/total*100:.0f}%)  "
              f"{rate:.0f} pages/sec  ETA {eta:.0f}s", flush=True)

    elapsed = time.perf_counter() - t_start
    print(f"\nDone. {total:,} embeddings written in {elapsed:.1f}s "
          f"({total/elapsed:.0f} pages/sec)", flush=True)

    # Build ivfflat index now that the column is populated
    print("Building ivfflat index (lists=100)…", flush=True)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS web_pages_embedding_idx
        ON web_pages USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    """)
    conn.commit()
    print("Index built.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
