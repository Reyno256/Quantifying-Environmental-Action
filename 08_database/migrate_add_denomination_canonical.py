"""
One-time migration: add synagogues.denomination_canonical and populate it by
collapsing the messy free-text `denomination` values into a fixed set of
canonical buckets.

Idempotent: safe to re-run. The column is rebuilt from `denomination` on every
run, so editing CANONICAL_MAP below and re-running re-normalises everything.

    python 08_database/migrate_add_denomination_canonical.py

Buckets (the only values written, besides 'Unknown' and NULL):
    Reconstructionist, Reform, Jewish Renewal, Conservative,
    Non-Denominational Progressive, Humanistic, Non-Denominational Conservative,
    Modern Orthodox, Orthodox, Chabad

Rules:
    - raw value present and confidently mappable  -> its canonical bucket
    - raw value present but NOT confidently one bucket (generic 'jewish',
      messianic, 'traditional', dual tags like 'reform;conservative', etc.)
      -> 'Unknown'
    - raw value NULL/empty (no denomination on record)  -> NULL
"""

import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

# Keys are the raw denomination string lower-cased and stripped. Any raw value
# whose normalised form is absent here is treated as 'Unknown' (logged below).
CANONICAL_MAP = {
    # ── Chabad ────────────────────────────────────────────────────────────
    "chabad": "Chabad",
    "lubavitch": "Chabad",                       # Chabad-Lubavitch, definitionally Chabad
    # ── Orthodox (incl. Hasidic/Haredi/Sephardi-Mizrahi practice) ─────────
    "orthodox": "Orthodox",
    "orthodox_jewish": "Orthodox",
    "ashkenazi_orthodox": "Orthodox",
    "orthodox_sefard": "Orthodox",
    "hasidic": "Orthodox",                       # broader than Chabad -> generic Orthodox
    "chasidic": "Orthodox",
    "haredi": "Orthodox",
    "ultra_orthodox": "Orthodox",
    "sephardi": "Orthodox",
    "sephardic": "Orthodox",
    "mizrahi": "Orthodox",
    "mizrachi": "Orthodox",
    "mizrachi_jerusalemite": "Orthodox",
    # ── Modern Orthodox ──────────────────────────────────────────────────
    "modern_orthodox": "Modern Orthodox",
    "modern orthodox": "Modern Orthodox",
    "orthodox_(modern)": "Modern Orthodox",
    "egalitarian_orthodox": "Modern Orthodox",   # partnership/egal -> modern wing
    # ── Conservative (incl. Masorti) ─────────────────────────────────────
    "conservative": "Conservative",
    "conservative_jewish": "Conservative",
    "conservative_judaism": "Conservative",
    "conservative_egalitarian": "Conservative",
    "masorti": "Conservative",                   # Masorti == Conservative outside the US
    "united synagogue of conservative judaism": "Conservative",
    # ── Reform ───────────────────────────────────────────────────────────
    "reform": "Reform",
    "reformed": "Reform",
    "reform_jewish": "Reform",
    "reform_judaism": "Reform",
    "reformed_judaism": "Reform",
    "reform judaism": "Reform",
    "union of american hebrew congregations": "Reform",  # historical Reform body
    # ── Reconstructionist ────────────────────────────────────────────────
    "reconstructionist": "Reconstructionist",
    "reconstructionalist": "Reconstructionist",  # misspelling
    # ── Jewish Renewal ───────────────────────────────────────────────────
    "jewish renewal": "Jewish Renewal",
    "renewal": "Jewish Renewal",
    # ── Humanistic ───────────────────────────────────────────────────────
    "humanistic": "Humanistic",
    # ── Non-Denominational Progressive / Conservative ────────────────────
    "non-denominational progressive": "Non-Denominational Progressive",
    "non-denominational conservative": "Non-Denominational Conservative",
}

CANONICAL_BUCKETS = [
    "Reconstructionist", "Reform", "Jewish Renewal", "Conservative",
    "Non-Denominational Progressive", "Humanistic",
    "Non-Denominational Conservative", "Modern Orthodox", "Orthodox", "Chabad",
]


def main():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "ALTER TABLE synagogues "
        "ADD COLUMN IF NOT EXISTS denomination_canonical VARCHAR(50)"
    )
    # Provenance for denomination_canonical so later layers (America-Orig
    # backfill, future LLM inference) can coexist. This script owns the rows it
    # derives from the synagogue's own `denomination` tag: source = 'tag'.
    cur.execute(
        "ALTER TABLE synagogues "
        "ADD COLUMN IF NOT EXISTS denomination_source VARCHAR(20)"
    )
    print("Columns denomination_canonical / denomination_source ready.")

    # Reset only the rows THIS script owns so the column stays a pure function
    # of `denomination` + CANONICAL_MAP on every run, without clobbering values
    # backfilled from other sources (source <> 'tag').
    cur.execute(
        "UPDATE synagogues SET denomination_canonical = NULL "
        "WHERE denomination_source IS NULL OR denomination_source = 'tag'"
    )

    # Only write rows THIS migration owns: never overwrite a value another layer
    # (America-Orig, llm, url-keyword) set on a row whose raw denomination tag
    # happens to be non-null/ambiguous.
    OWNED = "(denomination_source IS NULL OR denomination_source = 'tag')"

    # Present-but-unmappable -> 'Unknown'; NULL/empty stays NULL.
    cur.execute(
        "UPDATE synagogues "
        "SET denomination_canonical = 'Unknown', denomination_source = 'tag' "
        f"WHERE denomination IS NOT NULL AND btrim(denomination) <> '' AND {OWNED}"
    )

    # Apply each confident mapping. Group raw keys by target bucket so we issue
    # one UPDATE per bucket using ANY(%s) on the normalised raw value.
    by_bucket = {}
    for raw_key, bucket in CANONICAL_MAP.items():
        by_bucket.setdefault(bucket, []).append(raw_key)

    for bucket, raw_keys in by_bucket.items():
        cur.execute(
            "UPDATE synagogues "
            "SET denomination_canonical = %s, denomination_source = 'tag' "
            f"WHERE lower(btrim(denomination)) = ANY(%s) AND {OWNED}",
            (bucket, raw_keys),
        )

    # ── Report ───────────────────────────────────────────────────────────
    cur.execute(
        "SELECT denomination_canonical, COUNT(*) FROM synagogues "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    print("\nCanonical distribution:")
    for bucket, n in cur.fetchall():
        label = bucket if bucket is not None else "(NULL — no denomination)"
        print(f"  {n:5d}  {label}")

    # Raw values that fell through to 'Unknown', for auditing.
    cur.execute(
        "SELECT denomination, COUNT(*) FROM synagogues "
        "WHERE denomination_canonical = 'Unknown' GROUP BY 1 ORDER BY 2 DESC"
    )
    rows = cur.fetchall()
    if rows:
        print("\nRaw values mapped to 'Unknown' (audit):")
        for raw, n in rows:
            print(f"  {n:5d}  {raw!r}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
