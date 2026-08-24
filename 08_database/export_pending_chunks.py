"""
export_pending_chunks.py

Phase 1 of the VPN-concurrency workaround (see classify_offline_buffered.py
for the full explanation). Connects to the DB, finds every
(web_page_id, chunk_index, chunk_text) not yet successfully classified
(same definition as populate_page_chunk_classifications.py: error IS NULL/''),
writes it to a local JSONL file, then exits.

This script's own process ends once the file is written -- there is
deliberately no follow-on classification step in this process, so "safe to
disconnect the VPN" means the process has actually exited, not "a phase
inside a still-running process."

Usage:
    python 08_database/export_pending_chunks.py
    python 08_database/export_pending_chunks.py --limit 5000 --out test.jsonl
"""

import argparse
import json
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

HERE = Path(__file__).parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env")

import sys
sys.path.insert(0, str(ROOT / "06_environmental_classification"))
from chunk_common import chunk_text  # noqa: E402

DEFAULT_OUT = HERE / "pending_chunks.jsonl"


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
    ap.add_argument("--prefix", default="")
    ap.add_argument("--limit", type=int, default=None,
                     help="cap number of pages considered, for test runs")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    P = args.prefix
    out_path = Path(args.out)

    conn = get_conn()
    cur = conn.cursor()

    where = "content_text IS NOT NULL AND content_text != ''"
    query = f"SELECT id FROM {P}web_pages WHERE {where} ORDER BY id"
    if args.limit:
        query += " LIMIT %s"
        cur.execute(query, (args.limit,))
    else:
        cur.execute(query)
    page_ids = [r[0] for r in cur.fetchall()]
    print(f"Eligible pages: {len(page_ids):,}", flush=True)

    batch_size = 500
    total_pending = 0
    with open(out_path, "w", encoding="utf-8") as out_f:
        for start in range(0, len(page_ids), batch_size):
            batch_ids = page_ids[start:start + batch_size]

            cur.execute(f"SELECT id, content_text FROM {P}web_pages WHERE id = ANY(%s)", (batch_ids,))
            content_map = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                f"""SELECT web_page_id, chunk_index FROM {P}page_chunk_classifications
                    WHERE web_page_id = ANY(%s) AND (error IS NULL OR error = '')""",
                (batch_ids,),
            )
            done = {(r[0], r[1]) for r in cur.fetchall()}

            for pid in batch_ids:
                for idx, c in enumerate(chunk_text(content_map.get(pid))):
                    if (pid, idx) not in done:
                        out_f.write(json.dumps({"page_id": pid, "chunk_index": idx, "chunk_text": c}) + "\n")
                        total_pending += 1

            if (start // batch_size) % 20 == 0:
                print(f"  Scanned {min(start + batch_size, len(page_ids)):,}/{len(page_ids):,} pages, "
                      f"{total_pending:,} pending chunks so far…", flush=True)

    cur.close()
    conn.close()

    print(f"\nDone. {total_pending:,} pending chunks written to {out_path}")
    print(f"\n>>> This process has exited. Safe to disconnect the VPN now. <<<\n")
    print(f"Next: python 08_database/classify_offline_buffered.py --in {out_path}  (no VPN needed)")


if __name__ == "__main__":
    main()
