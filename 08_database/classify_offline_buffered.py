"""
classify_offline_buffered.py

Workaround for this machine's Cisco VPN tunnel (cscotun0) capping concurrent
connections to Google's API much lower than the original M3 MacBook could
sustain (confirmed: 5 workers -> 99% success, 10+ workers -> ~18% success,
almost all "[Errno 113] No route to host", all routed via `ip route get
<google-ip>` -> cscotun0). The DB (page_chunk_classifications) is itself
only reachable *through* that same VPN (it's a tunneled port to a remote
docker host), so the two needs are in tension on this machine, even though
they weren't on the original Mac.

This is Phase 2 of a three-script split (Phase 1: export_pending_chunks.py,
Phase 3: upload_buffered_results.py). Phase 1 runs to completion and exits
in its own process while the VPN is still up; only once that process has
actually exited is it safe to disconnect. This script then reads the
resulting JSONL file and does the Gemini calls at full concurrency with no
DB connection at all, so it never needs the VPN.

Resumable: chunks already present *without an error* in the output file are
skipped on a re-run. Chunks that errored (including transient network
errors) are re-attempted, since an error record is not proof the chunk is
unclassifiable -- only a genuine result is.

Processes the input file in bounded batches (default 5,000) rather than
submitting the entire backlog to the thread pool at once, so memory stays
proportional to one batch instead of the full ~1M+ chunk backlog.

Usage:
    python 08_database/classify_offline_buffered.py --workers 40
    python 08_database/classify_offline_buffered.py --limit 500 --workers 10
    python 08_database/classify_offline_buffered.py --in my_pending.jsonl --out my_results.jsonl
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent

sys.path.insert(0, str(ROOT / "07_LLM_categorization"))
import gemini_as_a_judge  # noqa: E402
_judge = gemini_as_a_judge.get_response_judge

sys.path.insert(0, str(ROOT / "11_analysis" / "recreate_existing"))
from recreate_common import classify_action, pure_action  # noqa: E402

DEFAULT_IN = HERE / "pending_chunks.jsonl"
DEFAULT_OUT = HERE / "buffered_results.jsonl"
MAX_CHARS = 6_000
DEFAULT_BATCH_SIZE = 5_000


def major_category_of(category: str) -> str | None:
    if not category or category == "N/A":
        return None
    bucket = classify_action(pure_action(category))
    return None if bucket == "??UNMAPPED" else bucket


MODEL_NAME = gemini_as_a_judge.MODEL
TEMPERATURE = 0.0


# ── rate-limit helpers -- same shared-state pattern as populate script ────────

_rate_lock = threading.Lock()
_rate_limit_until = [0.0]


def _handle_rate_limit(wait_secs: float = 60.0) -> None:
    resume_at = time.time() + wait_secs
    with _rate_lock:
        extended = resume_at > _rate_limit_until[0]
        if extended:
            _rate_limit_until[0] = resume_at
    if extended:
        reset_str = time.strftime("%H:%M:%S", time.localtime(resume_at))
        print(f"  Rate limited -- pausing {wait_secs:.0f}s (until {reset_str})", flush=True)


def _wait_if_rate_limited() -> None:
    while True:
        with _rate_lock:
            wait = _rate_limit_until[0] - time.time()
        if wait <= 0:
            return
        time.sleep(min(wait, 1.0))


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    return "429" in msg or "ResourceExhausted" in name or "rate" in msg.lower()


def classify_chunk(page_id: int, chunk_index: int, text: str) -> dict:
    snippet = (text or "")[:MAX_CHARS]
    if not snippet.strip():
        return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
                "category": "", "error": "no text"}
    for attempt in range(5):
        _wait_if_rate_limited()
        try:
            cat = _judge(snippet).strip().rstrip(".,").strip()
            return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
                    "category": cat, "error": ""}
        except Exception as e:
            if _is_rate_limit(e):
                _handle_rate_limit(60.0)
            else:
                return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
                        "category": "", "error": str(e)[:200]}
    return {"page_id": page_id, "chunk_index": chunk_index, "chunk_text": text,
            "category": "", "error": "rate limited after 5 attempts"}


def stream_pending(in_path: Path, limit: int | None):
    with open(in_path, encoding="utf-8") as f:
        n = 0
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            yield r["page_id"], r["chunk_index"], r["chunk_text"]
            n += 1
            if limit and n >= limit:
                return


def load_already_buffered(out_path: Path) -> set[tuple]:
    """Chunks with a genuine (error-free) result. Errored chunks are NOT
    considered done -- a transient failure shouldn't permanently block a
    chunk from being retried on the next run."""
    if not out_path.exists():
        return set()
    done = set()
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not r.get("error"):
                done.add((r["page_id"], r["chunk_index"]))
    return done


class Progress:
    def __init__(self):
        self.completed = 0
        self.errors = 0
        self.t0 = time.time()
        self.lock = threading.Lock()


def run_batch(pool: ThreadPoolExecutor, batch: list[tuple], out_f, write_lock: threading.Lock,
              progress: Progress, total_work: int) -> None:
    futures = {pool.submit(classify_chunk, pid, idx, c): (pid, idx) for pid, idx, c in batch}
    for fut in as_completed(futures):
        r = fut.result()
        bucket = major_category_of(r["category"])
        record = {
            "page_id": r["page_id"], "chunk_index": r["chunk_index"],
            "chunk_text": r["chunk_text"], "category": r["category"],
            "major_category": bucket, "model_used": MODEL_NAME,
            "temperature": TEMPERATURE, "error": r["error"] or None,
        }
        with write_lock:
            out_f.write(json.dumps(record) + "\n")
            out_f.flush()
        with progress.lock:
            progress.completed += 1
            if r["error"]:
                progress.errors += 1
            completed, errors = progress.completed, progress.errors
        if completed % 500 == 0:
            rate = completed / (time.time() - progress.t0) * 60
            print(f"  {completed:,}/{total_work:,}  ({rate:.0f} chunks/min)  errors={errors:,}",
                  flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--country", default="US")
    ap.add_argument("--limit", type=int, default=None,
                     help="cap number of pending chunks read from --in, for test runs")
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = ap.parse_args()
    in_path = Path(args.in_path)
    out_path = Path(args.out)

    if not in_path.exists():
        print(f"No such file: {in_path}")
        print("Run export_pending_chunks.py first (needs VPN) to produce it.")
        return

    gemini_as_a_judge.configure_country(args.country)

    already = load_already_buffered(out_path)
    if already:
        print(f"Resuming: {len(already):,} chunks already successfully buffered in {out_path}", flush=True)

    # Count total pending work up front (cheap: one pass, no chunk text retained)
    # so progress lines can show a real denominator.
    total_pending = 0
    total_skipped = 0
    for pid, idx, _ in stream_pending(in_path, args.limit):
        if (pid, idx) in already:
            total_skipped += 1
        else:
            total_pending += 1
    print(f"Pending in {in_path}: {total_pending + total_skipped:,} "
          f"({total_skipped:,} already done, {total_pending:,} to classify)", flush=True)

    if total_pending == 0:
        print("Nothing to do.")
        return

    print(f"Classifying {total_pending:,} chunks with {args.workers} workers "
          f"in batches of {args.batch_size:,} -> {out_path}\n", flush=True)

    write_lock = threading.Lock()
    progress = Progress()

    with open(out_path, "a", encoding="utf-8") as out_f:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            batch: list[tuple] = []
            for pid, idx, c in stream_pending(in_path, args.limit):
                if (pid, idx) in already:
                    continue
                batch.append((pid, idx, c))
                if len(batch) >= args.batch_size:
                    run_batch(pool, batch, out_f, write_lock, progress, total_pending)
                    batch = []
            if batch:
                run_batch(pool, batch, out_f, write_lock, progress, total_pending)

    print(f"\n{'-'*55}")
    print(f"Done. {progress.completed:,} chunks classified this run, {progress.errors:,} errors.")
    print(f"Results buffered in {out_path}")
    print(f"Next: python 08_database/upload_buffered_results.py --in {out_path}  (VPN back on)")


if __name__ == "__main__":
    main()
