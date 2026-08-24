"""
upload_buffered_results.py

Phase 3 of the VPN-concurrency workaround (see classify_offline_buffered.py):
reads the local JSONL results file produced offline and upserts every row
into page_chunk_classifications. Needs the DB / VPN active.

Idempotent: safe to re-run against the same file (ON CONFLICT DO UPDATE), and
safe to run repeatedly against a growing file as classify_offline_buffered.py
continues appending to it -- already-uploaded lines just get overwritten with
identical values.

Usage:
    python 08_database/upload_buffered_results.py
    python 08_database/upload_buffered_results.py --in my_results.jsonl --prefix canada_
"""

import argparse
import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env")

DEFAULT_IN = HERE / "buffered_results.jsonl"
BATCH_SIZE = 500


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    ap.add_argument("--prefix", default="")
    args = ap.parse_args()
    P = args.prefix

    in_path = Path(args.in_path)
    if not in_path.exists():
        print(f"No such file: {in_path}")
        return

    conn = get_conn()
    cur = conn.cursor()

    buf = []
    total = 0
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            buf.append((
                r["page_id"], r["chunk_index"], r["chunk_text"],
                r["category"] or None, r["major_category"],
                r["model_used"], r["temperature"], r["error"],
            ))
            if len(buf) >= BATCH_SIZE:
                _flush(cur, conn, buf, P)
                total += len(buf)
                print(f"  Uploaded {total:,} rows so far…", flush=True)
                buf.clear()

    if buf:
        _flush(cur, conn, buf, P)
        total += len(buf)

    print(f"\nDone. Uploaded {total:,} rows from {in_path}.")

    cur.execute(f"SELECT COUNT(*) FROM {P}page_chunk_classifications WHERE error IS NULL OR error=''")
    print(f"Successful rows in DB now: {cur.fetchone()[0]:,}")
    cur.execute(f"SELECT COUNT(*) FROM {P}page_chunk_classifications")
    print(f"Total rows in DB now: {cur.fetchone()[0]:,}")

    cur.close()
    conn.close()


def _flush(cur, conn, buf, P):
    psycopg2.extras.execute_batch(
        cur,
        f"""
        INSERT INTO {P}page_chunk_classifications
            (web_page_id, chunk_index, chunk_text, category, major_category,
             model_used, temperature, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (web_page_id, chunk_index) DO UPDATE SET
            chunk_text     = EXCLUDED.chunk_text,
            category       = EXCLUDED.category,
            major_category = EXCLUDED.major_category,
            model_used     = EXCLUDED.model_used,
            temperature    = EXCLUDED.temperature,
            error          = EXCLUDED.error,
            classified_at  = NOW()
        """,
        buf,
        page_size=BATCH_SIZE,
    )
    conn.commit()


if __name__ == "__main__":
    main()
