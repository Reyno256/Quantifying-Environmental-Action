"""
framing_judge.py

Gemini 3.1 Flash-Lite judge for the Implicit/Explicit/Embedded "framing" axis,
modeled on gemini_as_a_judge.py (context-caching pattern).

For a confirmed environmental-action chunk (one already assigned a category by
gemini_as_a_judge.py), classify *how* the action is framed:

  - Explicit — presented with a stated environmental/sustainability goal
  - Embedded — part of regular religious practice/worship; environmental
                benefit is incidental to a religious purpose
  - Implicit — clearly has an environmental effect, but framed as neither of
                the above (described matter-of-factly, without environmental
                or religious framing)

Definitions follow Baugh (2019) "Explicit and Embedded Environmentalism" and
Caldwell, Probstein & Yoreh (2022) "Shades of green", as operationalized in
Shore (2025) MES thesis.

Exposes:
    get_framing_judge(chunk_text: str, category: str) -> str

Requires:
    pip install google-genai
    GEMINI_API_KEY set in ../.env
"""

import csv
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

HERE = Path(__file__).parent

# ── credentials ───────────────────────────────────────────────────────────────

def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    return start.parent

load_dotenv(_find_root(Path(__file__).parent) / ".env")
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-3.1-flash-lite"

# ── system instruction ────────────────────────────────────────────────────────
# Built by combining the base instructions (sys_prompt.txt) with few-shot
# demonstrations (prompt_examples.txt — one Gemini-correct and one Gemini-wrong
# case per framing label, each labelled with Shore's gold framing).

_sys_ns: dict = {}
exec((HERE / "sys_prompt.txt").read_text(encoding="utf-8"), _sys_ns)
_BASE_PROMPT: str = _sys_ns["SYSTEM_PROMPT"]


def _load_example_block() -> str:
    blocks = []
    with open(HERE / "prompt_examples.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            blocks.append(
                f'Assigned category: "{row["category"]}"\n'
                f'Excerpt:\n"""{row["chunk_text"]}"""\n'
                f'Correct label: "{row["shore_framing"]}"'
            )
    return "\n\n".join(blocks)


SYSTEM_PROMPT = (
    _BASE_PROMPT.rstrip()
    + "\n\n# Examples (each excerpt shown with its correct framing label):\n\n"
    + _load_example_block()
    + "\n"
)

# ── prompt builder ─────────────────────────────────────────────────────────────

def build_prompt(chunk_text: str, category: str) -> str:
    return (
        f'Assigned category: "{category}"\n\n'
        f'Excerpt:\n"""{chunk_text}"""\n\n'
        "Return exactly one label from the valid labels listed in the instructions."
    )


# ── context cache — created once at import, reused for every call ─────────────

def _create_cache() -> str:
    """Create a context cache for the system instruction and return its name."""
    cache = _client.caches.create(
        model=MODEL,
        config=types.CreateCachedContentConfig(
            system_instruction=SYSTEM_PROMPT,
            ttl="14400s",  # 4 hours — covers a full ~31k-chunk batch run
        ),
    )
    return cache.name

_cache_failed: bool = False
_CACHE_NAME: str = ""
try:
    _CACHE_NAME = _create_cache()
except Exception as e:
    print(f"\n[framing_judge] Cache creation failed: {e}"
          f"\nFalling back to direct system_instruction for the whole session.\n",
          flush=True)
    _cache_failed = True


# ── inference ─────────────────────────────────────────────────────────────────

def _call_with_cache(prompt: str) -> str:
    return _client.models.generate_content(
        model=MODEL,
        config=types.GenerateContentConfig(
            cached_content=_CACHE_NAME,
            max_output_tokens=20,
            temperature=0.0,
        ),
        contents=prompt,
    ).text.strip().strip('"')


def _call_direct(prompt: str) -> str:
    return _client.models.generate_content(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=20,
            temperature=0.0,
        ),
        contents=prompt,
    ).text.strip().strip('"')


def get_framing_judge(chunk_text: str, category: str) -> str:
    """Classify the framing of the environmental action in *chunk_text*.

    Strategy:
      1. Use context cache (cheap).
      2. On cache miss/expiry: recreate the cache and retry.
      3. If cache recreation fails: warn once, then pass system_instruction
         directly for the remainder of the session.
    """
    global _CACHE_NAME, _cache_failed
    prompt = build_prompt(chunk_text, category)

    if not _cache_failed:
        try:
            return _call_with_cache(prompt)
        except Exception as e:
            if "CachedContent not found" not in str(e) and "PERMISSION_DENIED" not in str(e):
                raise
            # Cache expired — try to recreate
            try:
                _CACHE_NAME = _create_cache()
                return _call_with_cache(prompt)
            except Exception as recreate_err:
                print(f"\n[framing_judge] Cache recreation failed: {recreate_err}"
                      f"\nFalling back to direct system_instruction for remainder of session.\n",
                      flush=True)
                _cache_failed = True

    return _call_direct(prompt)
