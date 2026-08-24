"""
LLM layer for denomination_canonical: classify the synagogues that still have
NO denomination (denomination_canonical IS NULL) but DO have crawled website
text, using Gemini on the prompt in denomination_search_prompt.txt.

This is the third, lowest-precedence layer after:
    08_database/migrate_add_denomination_canonical.py   (source='tag')
    08_database/backfill_denomination_from_america.py    (source='America-Orig')

Provenance & reversibility (the point of this script):
    - Every model verdict is written to its own table,
      `denomination_llm_classifications` (raw response, model, pages used,
      errors, timestamp) — nothing is silently folded into synagogues.
    - synagogues rows we fill get denomination_source = 'llm'. So the whole
      layer is reversible with:
          DELETE FROM denomination_llm_classifications;
          UPDATE synagogues
             SET denomination_canonical = NULL, denomination_source = NULL
           WHERE denomination_source = 'llm';
    - The tag migration's reset is scoped to source IN (NULL,'tag') and the
      America backfill only touches NULL rows, so neither disturbs 'llm' rows.

Only NULL rows are ever written, so a synagogue's own tag and the human
baseline always win over the model. Idempotent / resumable: synagogues already
present in denomination_llm_classifications without an error are skipped.

    python 12_denominations/classify_denominations_llm.py
"""

import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from google import genai
from google.genai import types

HERE = Path(__file__).parent


def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start.parent


ROOT = _find_root(HERE)
load_dotenv(ROOT / ".env")

PROMPT_FILE = HERE / "denomination_search_prompt.txt"
SYSTEM_PROMPT = PROMPT_FILE.read_text(encoding="utf-8").strip()

MODEL = "gemini-3.1-flash-lite"
TEMPERATURE = 0.0
MAX_CHARS = 6_000          # cap of website text fed per synagogue
PER_PAGE_CHARS = 4_000     # cap pulled per page (bounds memory)
WORKERS = 5
FLUSH_EVERY = 25

# Canonical buckets (exact spelling shared with the tag migration). Anything
# off this list — including the prompt's "Unkown" typo — collapses to 'Unknown'.
BUCKETS = [
    "Reconstructionist", "Reform", "Jewish Renewal", "Conservative",
    "Non-Denominational Progressive", "Humanistic",
    "Non-Denominational Conservative", "Modern Orthodox", "Orthodox", "Chabad",
]
_BUCKET_BY_LOWER = {b.lower(): b for b in BUCKETS}

# Page-path keywords that tend to state affiliation; higher score => fed first.
PRIORITY_KEYWORDS = (
    "about", "who-we-are", "who_we_are", "whoweare", "belief", "denomination",
    "affiliat", "congregation", "welcome", "mission", "clergy", "rabbi",
    "our-", "history", "membership", "join",
)

_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def normalize_label(raw: str) -> str:
    """Map a raw model response to a canonical bucket or 'Unknown'."""
    if not raw:
        return "Unknown"
    cleaned = raw.strip().strip('".').strip()
    return _BUCKET_BY_LOWER.get(cleaned.lower(), "Unknown")


def page_score(path: str) -> int:
    p = (path or "").lower()
    if p.endswith("/index.html") or p.endswith("/index.htm") or p.count("/") <= 1:
        base = 5  # homepage
    else:
        base = 0
    return base + sum(2 for kw in PRIORITY_KEYWORDS if kw in p)


def build_text(pages):
    """pages: list of (path, content). Affiliation-likely pages first, capped."""
    ordered = sorted(pages, key=lambda pc: page_score(pc[0]), reverse=True)
    out, used = [], 0
    for _path, content in ordered:
        if not content:
            continue
        chunk = content[:PER_PAGE_CHARS]
        if used + len(chunk) > MAX_CHARS:
            chunk = chunk[: MAX_CHARS - used]
        out.append(chunk)
        used += len(chunk)
        if used >= MAX_CHARS:
            break
    return "\n\n---\n\n".join(out)


def build_prompt(text: str) -> str:
    return (
        "The following text is aggregated from a single US synagogue's website. "
        "Classify the synagogue's denomination.\n\n"
        f'Website content:\n"""{text}"""'
    )


def classify(text: str) -> str:
    return _client.models.generate_content(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=50,
            temperature=TEMPERATURE,
        ),
        contents=build_prompt(text),
    ).text


def main():
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    # ── provenance table ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS denomination_llm_classifications (
            id            SERIAL PRIMARY KEY,
            synagogue_id  INTEGER UNIQUE NOT NULL
                              REFERENCES synagogues(id) ON DELETE CASCADE,
            label         VARCHAR(50),     -- canonical bucket or 'Unknown'
            raw_response  TEXT,            -- exact model output, for audit
            model_used    VARCHAR(50),
            temperature   DOUBLE PRECISION,
            n_pages       INTEGER,         -- pages fed to the model
            error         TEXT,
            created_at    TIMESTAMPTZ DEFAULT now()
        )
    """)

    # ── candidates: NULL canonical, has crawled text, not already done ────
    cur.execute("""
        SELECT s.id, p.page_path, left(p.content_text, %s)
        FROM synagogues s
        JOIN websites w  ON w.synagogue_id = s.id
        JOIN web_pages p ON p.website_id  = w.id
        WHERE s.denomination_canonical IS NULL
          AND p.content_text IS NOT NULL AND p.content_text <> ''
          AND s.id NOT IN (
              SELECT synagogue_id FROM denomination_llm_classifications
              WHERE error IS NULL
          )
    """, (PER_PAGE_CHARS,))

    pages_by_syn = defaultdict(list)
    for syn_id, path, content in cur.fetchall():
        pages_by_syn[syn_id].append((path, content))

    # Clear any prior errored rows so they retry cleanly.
    cur.execute("""
        DELETE FROM denomination_llm_classifications
        WHERE synagogue_id = ANY(%s) AND error IS NOT NULL
    """, (list(pages_by_syn.keys()) or [-1],))

    work = []
    for syn_id, pages in pages_by_syn.items():
        text = build_text(pages)
        if text.strip():
            work.append((syn_id, text, len(pages)))

    print(f"Synagogues to classify: {len(work)}", flush=True)
    if not work:
        conn.close()
        return

    lock = threading.Lock()
    buffer = []
    done = [0]
    errs = [0]
    t0 = time.time()

    def run(syn_id, text, n_pages):
        try:
            raw = classify(text)
            return (syn_id, normalize_label(raw), raw, n_pages, None)
        except Exception as e:
            return (syn_id, None, None, n_pages, str(e)[:300])

    def flush(force=False):
        with lock:
            if buffer and (force or len(buffer) >= FLUSH_EVERY):
                # 1. record provenance
                psycopg2.extras.execute_batch(cur, """
                    INSERT INTO denomination_llm_classifications
                        (synagogue_id, label, raw_response, model_used,
                         temperature, n_pages, error)
                    VALUES (%(sid)s, %(label)s, %(raw)s, %(model)s,
                            %(temp)s, %(n)s, %(err)s)
                    ON CONFLICT (synagogue_id) DO UPDATE SET
                        label = EXCLUDED.label,
                        raw_response = EXCLUDED.raw_response,
                        model_used = EXCLUDED.model_used,
                        temperature = EXCLUDED.temperature,
                        n_pages = EXCLUDED.n_pages,
                        error = EXCLUDED.error,
                        created_at = now()
                """, buffer)
                # 2. fill synagogues (NULL-guarded, tagged 'llm')
                fills = [
                    (b["label"], b["sid"]) for b in buffer
                    if b["err"] is None and b["label"]
                ]
                if fills:
                    psycopg2.extras.execute_batch(cur, """
                        UPDATE synagogues
                           SET denomination_canonical = %s,
                               denomination_source = 'llm'
                         WHERE id = %s AND denomination_canonical IS NULL
                    """, fills)
                buffer.clear()

    print(f"Classifying with {MODEL}, {WORKERS} workers…\n", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(run, *w) for w in work]
        for fut in as_completed(futures):
            sid, label, raw, n_pages, err = fut.result()
            with lock:
                buffer.append({
                    "sid": sid, "label": label, "raw": raw, "model": MODEL,
                    "temp": TEMPERATURE, "n": n_pages, "err": err,
                })
                done[0] += 1
                if err:
                    errs[0] += 1
            flush()
            n = done[0]
            if n % 50 == 0 or n == len(work):
                rate = n / (time.time() - t0) * 60
                print(f"  {n}/{len(work)} ({n/len(work)*100:.0f}%)  "
                      f"{rate:.0f}/min  errors={errs[0]}", flush=True)

    flush(force=True)

    # ── summary ───────────────────────────────────────────────────────────
    cur.execute("""
        SELECT label, COUNT(*) FROM denomination_llm_classifications
        WHERE error IS NULL GROUP BY label ORDER BY COUNT(*) DESC
    """)
    rows = cur.fetchall()
    total = sum(n for _, n in rows)
    print(f"\n{'='*56}\nLLM verdicts (this + prior runs): {total}")
    for label, n in rows:
        bar = "#" * int(n / total * 30) if total else ""
        print(f"  {str(label):<32s} {n:>4}  {n/total*100:5.1f}%  {bar}")

    cur.execute("""
        SELECT denomination_source, COUNT(*) FROM synagogues
        WHERE denomination_canonical IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC
    """)
    print("\nsynagogues.denomination_canonical by source:")
    for src, n in cur.fetchall():
        print(f"  {n:5d}  {src}")
    cur.execute("SELECT COUNT(*) FROM synagogues WHERE denomination_canonical IS NULL")
    print(f"  {cur.fetchone()[0]:5d}  (still NULL — no usable text)")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
