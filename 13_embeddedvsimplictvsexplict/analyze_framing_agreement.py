"""
Agreement analysis: Gemini vs Mistral (1000-chunk sample), plus a 3-way
Fleiss's kappa among Gemini, Mistral, and Shore's human-coded framing labels
for the subset of sampled chunks whose (domain, keyword) also appears in
Shore's America_results.csv.

Usage:
    python analyze_framing_agreement.py
"""

import csv
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE.parent / ".env")

LABELS = ["Explicit", "Embedded", "Implicit"]


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"),
        user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"),
    )


def domain_from_url(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


# ── 1. Gemini vs Mistral (full 1000-chunk sample) ─────────────────────────────

def two_way_stats(rows, label_a, label_b, name_a, name_b):
    valid = [r for r in rows if r[label_a] in LABELS and r[label_b] in LABELS]
    n = len(valid)
    agree = sum(1 for r in valid if r[label_a] == r[label_b])
    po = agree / n

    a_counts = Counter(r[label_a] for r in valid)
    b_counts = Counter(r[label_b] for r in valid)
    pe = sum((a_counts[l] / n) * (b_counts[l] / n) for l in LABELS)
    kappa = (po - pe) / (1 - pe)

    print(f"\n=== {name_a} vs {name_b} ({n} valid chunks) ===")
    print(f"Observed agreement: {po*100:.1f}%")
    print(f"Expected (chance) agreement: {pe*100:.1f}%")
    print(f"Cohen's kappa: {kappa:.3f}")

    print(f"\n{name_a} distribution: {dict(a_counts)}")
    print(f"{name_b} distribution: {dict(b_counts)}")

    print(f"\nConfusion matrix (rows={name_a}, cols={name_b}):")
    matrix = Counter((r[label_a], r[label_b]) for r in valid)
    print("            " + "  ".join(f"{l:>9}" for l in LABELS))
    for a in LABELS:
        print(f"{a:>10}  " + "  ".join(f"{matrix[(a, b)]:>9}" for b in LABELS))

    print(f"\nPer-label agreement (recall vs {name_a}):")
    for lbl in LABELS:
        sub = [r for r in valid if r[label_a] == lbl]
        a = [r for r in sub if r[label_b] == lbl]
        if sub:
            print(f"  {lbl}: {len(a)}/{len(sub)} = {len(a)/len(sub)*100:.1f}%")
        else:
            print(f"  {lbl}: n/a")

    return valid


# ── 2. Fleiss's kappa for 3 raters ────────────────────────────────────────────

def fleiss_kappa(rating_triples):
    """rating_triples: list of (rater1, rater2, rater3) label tuples."""
    n = len(rating_triples)
    N = 3  # raters per item

    p_i_sum = 0.0
    category_totals = Counter()
    for triple in rating_triples:
        counts = Counter(triple)
        p_i = sum(c * c for c in counts.values())
        p_i_sum += (p_i - N) / (N * (N - 1))
        category_totals.update(counts)

    p_bar = p_i_sum / n
    total_votes = n * N
    p_e = sum((category_totals[l] / total_votes) ** 2 for l in LABELS)

    kappa = (p_bar - p_e) / (1 - p_e)
    return kappa, p_bar, p_e, category_totals


def main():
    with open(HERE / "mistral_framing_comparison.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        r["chunk_id"] = int(r["chunk_id"])

    print(f"Loaded {len(rows)} rows from mistral_framing_comparison.csv")

    # ── Gemini vs Mistral ─────────────────────────────────────────────────────
    two_way_stats(rows, "gemini_framing", "mistral_framing", "Gemini", "Mistral")

    # ── Match against Shore's data ───────────────────────────────────────────
    conn = get_conn()
    cur = conn.cursor()
    chunk_ids = [r["chunk_id"] for r in rows]
    cur.execute(
        """
        SELECT lcc.id, w.domain, pkm.keyword
        FROM llm_chunk_classifications lcc
        JOIN page_keyword_matches pkm ON pkm.id = lcc.match_id
        JOIN web_pages wp ON wp.id = pkm.web_page_id
        JOIN websites w ON w.id = wp.website_id
        WHERE lcc.id = ANY(%s)
        """,
        (chunk_ids,),
    )
    chunk_domain_kw = {row[0]: (row[1].lower(), row[2].lower()) for row in cur.fetchall()}
    cur.close()
    conn.close()

    # Build Shore lookup: (domain, keyword_lower) -> framing label
    shore_lookup = {}
    with open(HERE / "America_results.csv", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        next(reader)  # header row 1
        next(reader)  # header row 2 (Implicit/Embedded/Explicit sub-labels)
        for row in reader:
            if len(row) < 7:
                continue
            link, keyword = row[2], row[3]
            implicit, embedded, explicit = row[4], row[5], row[6]
            if not link or not keyword:
                continue
            domain = domain_from_url(link)
            if implicit.strip().upper() == "TRUE":
                framing = "Implicit"
            elif embedded.strip().upper() == "TRUE":
                framing = "Embedded"
            elif explicit.strip().upper() == "TRUE":
                framing = "Explicit"
            else:
                continue
            shore_lookup[(domain, keyword.strip().lower())] = framing

    print(f"\nShore lookup entries: {len(shore_lookup)}")

    # ── Build 3-way triples (Gemini, Mistral, Shore) ────────────────────────
    triples = []
    for r in rows:
        if r["gemini_framing"] not in LABELS or r["mistral_framing"] not in LABELS:
            continue
        dk = chunk_domain_kw.get(r["chunk_id"])
        if dk is None:
            continue
        shore_framing = shore_lookup.get(dk)
        if shore_framing is None:
            continue
        triples.append((r["gemini_framing"], r["mistral_framing"], shore_framing))

    print(f"\nMatched chunks with Gemini + Mistral + Shore labels: {len(triples)}")

    if triples:
        # Pairwise agreement with Shore for context
        shore_counts = Counter(t[2] for t in triples)
        print(f"Shore label distribution (matched subset): {dict(shore_counts)}")

        gem_shore_agree = sum(1 for t in triples if t[0] == t[2])
        mis_shore_agree = sum(1 for t in triples if t[1] == t[2])
        gem_mis_agree = sum(1 for t in triples if t[0] == t[1])
        n = len(triples)
        print(f"\nGemini vs Shore agreement: {gem_shore_agree}/{n} = {gem_shore_agree/n*100:.1f}%")
        print(f"Mistral vs Shore agreement: {mis_shore_agree}/{n} = {mis_shore_agree/n*100:.1f}%")
        print(f"Gemini vs Mistral agreement (matched subset): {gem_mis_agree}/{n} = {gem_mis_agree/n*100:.1f}%")

        kappa, p_bar, p_e, totals = fleiss_kappa(triples)
        print(f"\n=== Fleiss's kappa (Gemini, Mistral, Shore) over {n} chunks ===")
        print(f"P_bar (observed agreement): {p_bar:.3f}")
        print(f"P_e (expected agreement): {p_e:.3f}")
        print(f"Fleiss's kappa: {kappa:.3f}")
        print(f"Category totals across all ratings: {dict(totals)}")
    else:
        print("No overlap between this sample and Shore's coded (domain, keyword) pairs.")


if __name__ == "__main__":
    main()
