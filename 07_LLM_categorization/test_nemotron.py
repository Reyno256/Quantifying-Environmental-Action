"""
Classify env pages using a three-model fallback chain via OpenRouter.

Model priority:
  1. nvidia/nemotron-3-super-120b-a12b:free
  2. openai/gpt-oss-120b:free
  3. poolside/laguna-m.1:free

Behaviour:
  - Sends 40 concurrent requests to exhaust per-minute rate limits on all models quickly.
  - When a model returns 429 the reset timestamp is parsed from the response headers
    and that model is parked until its quota refreshes.
  - When every model is rate-limited the script sleeps until the earliest reset,
    then resumes with all refilled quotas.
  - Continues cycling through pages until all are classified.
"""

import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup
from openai import OpenAI, RateLimitError
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from command_a_as_a_judge import build_prompt

# ── models ────────────────────────────────────────────────────────────────────

MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
    "poolside/laguna-m.1:free",
]

# ── config ────────────────────────────────────────────────────────────────────

ENV_DIR   = Path(__file__).parent.parent / "06_environmental_classification" / "environmental_html_pages"
N_PAGES   = 40          # pages to classify per run
WORKERS   = 40          # concurrent threads (blasts all models simultaneously)
MAX_CHARS = 6_000

# ── OpenRouter client ─────────────────────────────────────────────────────────

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("NEMOTRON_API_KEY"),
)

# ── per-model rate-limit tracker ──────────────────────────────────────────────

_lock        = threading.Lock()
_reset_at    = {m: 0.0 for m in MODELS}   # Unix timestamp when quota refills


def model_available(model: str) -> bool:
    return time.time() >= _reset_at[model]


def park_model(model: str, reset_ts_ms: int | None):
    """Mark a model as rate-limited until reset_ts_ms (milliseconds epoch)."""
    with _lock:
        if reset_ts_ms:
            _reset_at[model] = reset_ts_ms / 1000.0
        else:
            _reset_at[model] = time.time() + 61.0   # assume ~1-min window


def next_available_model() -> str | None:
    """Return the first non-rate-limited model, or None if all are parked."""
    with _lock:
        for m in MODELS:
            if time.time() >= _reset_at[m]:
                return m
    return None


def wait_for_any_model() -> str:
    """Block until the earliest-resetting model is available; return its id."""
    while True:
        with _lock:
            earliest = min(_reset_at.items(), key=lambda kv: kv[1])
        wait = earliest[1] - time.time()
        if wait <= 0:
            return earliest[0]
        model = earliest[0]
        reset_str = time.strftime("%H:%M:%S", time.localtime(earliest[1]))
        print(f"\n  ⏳ All models rate-limited. Waiting {wait:.0f}s "
              f"for {model.split('/')[1]} to reset at {reset_str}…", flush=True)
        time.sleep(min(wait, 5))   # wake up every 5 s to re-check


# ── classification ────────────────────────────────────────────────────────────

def extract_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:MAX_CHARS]
    except Exception:
        return html[:MAX_CHARS]


def call_model(model: str, prompt: str) -> str:
    res = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a text classifier"},
            {"role": "user",   "content": prompt},
        ],
    )
    return res.choices[0].message.content.strip()


def classify(path: Path) -> tuple[str, str, str]:
    """
    Classify one page. Tries each model in priority order, falling back on 429.
    Returns (page_name, category, model_used).
    """
    html   = path.read_text(encoding="utf-8", errors="replace")
    text   = extract_text(html)
    prompt = build_prompt(text)

    while True:
        model = next_available_model()
        if model is None:
            model = wait_for_any_model()

        try:
            category = call_model(model, prompt)
            return path.name, category, model.split("/")[1]

        except RateLimitError as e:
            # Parse reset timestamp from the error body
            reset_ms = None
            try:
                body = e.response.json()
                meta = body.get("error", {}).get("metadata", {})
                reset_ms = int(meta.get("headers", {}).get("X-RateLimit-Reset", 0))
            except Exception:
                pass

            short = model.split("/")[1]
            reset_str = (time.strftime("%H:%M:%S", time.localtime(reset_ms / 1000))
                         if reset_ms else "~1 min")
            print(f"  ⚠ {short} rate-limited → reset {reset_str}", flush=True)
            park_model(model, reset_ms)
            # Loop: will pick next available model (or wait)

        except Exception as e:
            return path.name, f"ERROR: {e}", model.split("/")[1]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    pages = sorted(ENV_DIR.rglob("*.html"))[:N_PAGES]
    print(f"Classifying {len(pages)} pages with {WORKERS} workers")
    print(f"Model chain: {' → '.join(m.split('/')[1] for m in MODELS)}\n")

    model_counts = {m.split("/")[1]: 0 for m in MODELS}
    model_counts["error"] = 0
    results = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(classify, p): p for p in pages}
        for fut in as_completed(futures):
            name, cat, model = fut.result()
            results.append((name, cat, model))
            if cat.startswith("ERROR"):
                model_counts["error"] += 1
                print(f"  {name[:50]:50s} [{model}] {cat}")
            else:
                model_counts[model] = model_counts.get(model, 0) + 1
                print(f"  {name[:50]:50s} [{model}] → {cat}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s  ({len(results)}/{len(pages)} attempted)")
    print("\nRequests per model:")
    for m, n in model_counts.items():
        if n:
            print(f"  {m}: {n}")


if __name__ == "__main__":
    main()
