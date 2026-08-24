"""
Page-level agreement between Shore's human subcategory coding
(America_results.csv) and the LLM chunk classifier (llm_chunk_classifications).

Grain: page = normalized (domain + path) URL present in BOTH datasets.
  Shore page label set S = union of TRUE subcategory columns (cols 7..84)
    across all Shore rows (keywords) sharing that URL.
  LLM page label set  L = set of subcategories with >=1 non-N/A chunk on the
    matching web_page (across all its keyword matches).

Agreement rule: a page AGREES iff  S ∩ L != empty  (the LLM placed >0 chunks
in some subcategory that Shore also marked TRUE). The full S/L contingency is
reported so the empty cases are visible, plus a coarser 9-category view.

IMPORTANT — URL normalization: the DB stores `web_pages.page_path` WITH a
`.html`/`.htm` suffix (the crawler saved files that way) while Shore's links are
clean URLs. norm_url() strips that suffix and collapses `index` to the homepage;
without this the join silently collapses (~492 pages instead of ~13.6k).

Note on coverage: only ~27% of Shore's ~50k pages overlap the DB at all —
Shore's list is NOT a DB subset (it includes off-site links like instagram/
youtube, many synagogue domains were never crawled, and the two independent
crawls visit different URLs). This script only assesses the pages that DO match.

Usage:
    python 07_LLM_categorization/shore_agreement.py
Writes shore_agreement_per_page.csv next to this script.
"""
import os, csv, re, difflib
from pathlib import Path
from urllib.parse import urlparse, unquote
from collections import defaultdict, Counter

import psycopg2
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent


def _find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "America_results.csv").exists():
            return p
    return start.parent


ROOT = _find_root(HERE)
load_dotenv(ROOT / ".env")
SHORE_CSV = ROOT / "America_results.csv"
OUT_CSV = HERE / "shore_agreement_per_page.csv"

# ── canonical subcategories + their 9-category group (Shore header rows 0,1) ──
with open(SHORE_CSV, encoding="latin-1") as f:
    r = csv.reader(f); h0 = next(r); h1 = next(r)
canon, col_cat = {}, {}
cur_cat = None
for i in range(7, 85):
    if h0[i].strip():
        cur_cat = h0[i].strip()
    canon[i] = h1[i].strip()
    col_cat[i] = cur_cat


def norm(s):
    return re.sub(r'\s+', ' ', (s or '').lower().strip().strip('"')).strip()


canon_norm = {i: norm(v) for i, v in canon.items()}


def sub_of(label):
    """Subcategory text of an LLM label (part after the 'Category_' prefix)."""
    lab = (label or '').strip().strip('"')
    if lab.upper() in ("N/A", "NA", ""):
        return None
    parts = lab.split("_", 1)
    s = parts[1] if len(parts) == 2 else lab
    if s.upper().startswith("NA_"):   # stray double prefix
        s = s[3:]
    return s


def map_to_col(label):
    """Map an LLM label to a Shore subcategory column index (handles the
    50-token output truncation by prefix/fuzzy matching). None if off-list."""
    s = sub_of(label)
    if s is None:
        return None
    ns = norm(s)
    if not ns:
        return None
    for i, cv in canon_norm.items():
        if cv == ns:
            return i
    cands = [i for i, cv in canon_norm.items() if cv.startswith(ns)]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        return min(cands, key=lambda i: len(canon_norm[i]))
    cands = [i for i, cv in canon_norm.items() if ns.startswith(cv)]
    if cands:
        return max(cands, key=lambda i: len(canon_norm[i]))
    best = difflib.get_close_matches(ns, list(canon_norm.values()), n=1, cutoff=0.6)
    if best:
        for i, cv in canon_norm.items():
            if cv == best[0]:
                return i
    return None


def norm_url(u):
    u = (u or '').strip().lower()
    if not u:
        return ""
    if not u.startswith("http"):
        u = "http://" + u
    pr = urlparse(u)
    net = pr.netloc[4:] if pr.netloc.startswith("www.") else pr.netloc
    path = unquote(pr.path).rstrip("/")
    for ext in (".html", ".htm"):
        if path.endswith(ext):
            path = path[:-len(ext)]
            break
    if path.endswith("/index"):
        path = path[:-6]
    if path == "/index":
        path = ""
    return net + path


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME", "synagogues"), user=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD"))


def main():
    # ── Shore: page_url -> set(TRUE subcat col idx) ───────────────────────────
    shore = defaultdict(set)
    shore_pages = set()
    with open(SHORE_CSV, encoding="latin-1") as f:
        rd = csv.reader(f); next(rd); next(rd)
        for row in rd:
            if len(row) < 85 or not row[2]:
                continue
            pu = norm_url(row[2])
            shore_pages.add(pu)
            for i in range(7, 85):
                if row[i].strip().upper() == "TRUE":
                    shore[pu].add(i)

    # ── LLM: page_url -> set(subcat col idx with >=1 chunk) ───────────────────
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT wp.page_path, lcc.category
        FROM llm_chunk_classifications lcc
        JOIN page_keyword_matches pkm ON pkm.id = lcc.match_id
        JOIN web_pages wp ON wp.id = pkm.web_page_id
        WHERE lcc.error IS NULL OR lcc.error = ''
    """)
    llm = defaultdict(set)
    llm_pages = set()
    for path, cat in cur.fetchall():
        pu = norm_url(path)
        llm_pages.add(pu)
        col = map_to_col(cat)
        if col is not None:
            llm[pu].add(col)
    conn.close()

    overlap = sorted(shore_pages & llm_pages)
    n = len(overlap)
    print(f"Shore pages: {len(shore_pages):,} | LLM pages: {len(llm_pages):,} | OVERLAP: {n:,}\n")

    def cats_of(cols):
        return {col_cat[c] for c in cols}

    buckets = Counter()
    per_page = []
    for pu in overlap:
        S, L = shore.get(pu, set()), llm.get(pu, set())
        if not S and not L:        b = "both_none"
        elif S and not L:          b = "shore_only"
        elif L and not S:          b = "llm_only"
        elif S & L:                b = "agree_overlap"
        else:                      b = "disjoint"
        buckets[b] += 1
        per_page.append((pu, sorted(S), sorted(L), b))

    both = buckets["agree_overlap"] + buckets["disjoint"]
    print("== Subcategory-level (agreement rule: S∩L≠∅) ==")
    for k in ("agree_overlap", "disjoint", "shore_only", "llm_only", "both_none"):
        print(f"  {k:<14}: {buckets[k]:>6}")
    print(f"  {'total':<14}: {n:>6}\n")
    print(f"  agree / all overlap                 : {buckets['agree_overlap']}/{n} = {buckets['agree_overlap']/n*100:.1f}%")
    print(f"  agree incl. both-none / all overlap : {(buckets['agree_overlap']+buckets['both_none'])}/{n} = {(buckets['agree_overlap']+buckets['both_none'])/n*100:.1f}%")
    if both:
        print(f"  agree among pages both code action  : {buckets['agree_overlap']}/{both} = {buckets['agree_overlap']/both*100:.1f}%")

    cb = Counter()
    for pu in overlap:
        S, L = cats_of(shore.get(pu, set())), cats_of(llm.get(pu, set()))
        if not S and not L:   cb["both_none"] += 1
        elif S and not L:     cb["shore_only"] += 1
        elif L and not S:     cb["llm_only"] += 1
        elif S & L:           cb["agree_overlap"] += 1
        else:                 cb["disjoint"] += 1
    cboth = cb["agree_overlap"] + cb["disjoint"]
    print("\n== 9-category-level (robustness check) ==")
    print(f"  agree / all overlap                 : {cb['agree_overlap']}/{n} = {cb['agree_overlap']/n*100:.1f}%")
    if cboth:
        print(f"  agree among pages both code action  : {cb['agree_overlap']}/{cboth} = {cb['agree_overlap']/cboth*100:.1f}%")

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["page_url", "bucket", "shore_subcats", "llm_subcats", "shore_cats", "llm_cats"])
        for pu, S, L, b in per_page:
            w.writerow([pu, b,
                        " | ".join(canon[c] for c in S),
                        " | ".join(canon[c] for c in L),
                        " | ".join(sorted(cats_of(set(S)))),
                        " | ".join(sorted(cats_of(set(L))))])
    print(f"\nPer-page detail -> {OUT_CSV}")


if __name__ == "__main__":
    main()
