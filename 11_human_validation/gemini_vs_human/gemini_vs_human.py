"""
gemini_vs_human.py

Evaluates Gemini 3.1 Flash-Lite classifications against the human-labeled
America_results.csv baseline.

Restricted to pages that appear in BOTH the human dataset AND the remote DB
AND contain at least one keyword from keywords.txt / keyword_variations.txt.

Metrics: precision, recall, F1, accuracy, Cohen's kappa
"""

import argparse
import csv
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).parent
while not (ROOT / ".env").exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "06_environmental_classification"))
from keyword_search import load_keywords, find_keywords

_KW_PATTERN = load_keywords()

GEMINI_MODEL = "gemini-3.1-flash-lite"

# America_results.csv layout:
#   row 0 → category header names  (used as column headers by csv.reader)
#   row 1 → sub-item label row     (skipped)
#   row 2+ → data
# Columns:
#   0: Checked   1: Synagogue   2: Link   3: Keywords
#   4-6: Framing sub-columns (Implicit / Embedded / Explicit)
#   7+: specific environmental action sub-columns across all categories
ACTION_COL_START = 4   # include Framing; any TRUE here → page is human-positive


def url_to_path(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        return ""
    p = urlparse(url)
    domain = p.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    path = p.path.lstrip("/")
    return f"{domain}/{path}" if path else domain


def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 8989)),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


_WEB_EXTS     = (".html", ".htm", ".php", ".asp", ".aspx", ".cfm", ".shtml")
_TRAIL_PUNCT  = ".,-_~"

def canonical_path(path: str) -> str:
    """Normalize a page_path so extension/slash/punctuation variants all map to the same key."""
    p = path.strip().lower().split("?")[0].split("#")[0]
    p = p.rstrip("/").rstrip(_TRAIL_PUNCT)
    # index files collapse to their parent directory
    for idx in ("/index.html", "/index.htm"):
        if p.endswith(idx):
            p = p[:-len(idx)].rstrip("/").rstrip(_TRAIL_PUNCT)
            break
    # strip remaining web extensions
    for ext in _WEB_EXTS:
        if p.endswith(ext):
            p = p[:-len(ext)].rstrip("/").rstrip(_TRAIL_PUNCT)
            break
    return p


# ── metrics (no sklearn dependency) ──────────────────────────────────────────

def compute_metrics(y_true: list[int], y_pred: list[int]) -> dict:
    n  = len(y_true)
    tp = sum(t == 1 and p == 1 for t, p in zip(y_true, y_pred))
    fp = sum(t == 0 and p == 1 for t, p in zip(y_true, y_pred))
    fn = sum(t == 1 and p == 0 for t, p in zip(y_true, y_pred))
    tn = sum(t == 0 and p == 0 for t, p in zip(y_true, y_pred))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy  = (tp + tn) / n if n else 0.0

    # Cohen's kappa
    p_o   = accuracy
    p_yes = ((tp + fp) / n) * ((tp + fn) / n)
    p_no  = ((tn + fn) / n) * ((tn + fp) / n)
    p_e   = p_yes + p_no
    kappa = (p_o - p_e) / (1 - p_e) if (1 - p_e) else 0.0

    return dict(tp=tp, fp=fp, fn=fn, tn=tn, n=n,
                precision=precision, recall=recall, f1=f1,
                accuracy=accuracy, kappa=kappa)


# ── main ──────────────────────────────────────────────────────────────────────

def save_figure(m: dict, n: int, human_pos: int, gemini_pos: int, out: Path) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    import numpy as np

    fig = plt.figure(figsize=(11, 4.5), facecolor="white")
    fig.suptitle(
        f"Gemini 3.1 Flash-Lite vs Human Baseline  (n={n:,} keyword-matched pages)",
        fontsize=13, fontweight="bold", y=0.98,
    )
    gs = gridspec.GridSpec(1, 2, figure=fig, width_ratios=[1, 1.3], wspace=0.35)

    # ── left: confusion matrix ────────────────────────────────────────────────
    ax_cm = fig.add_subplot(gs[0])
    cm = np.array([[m["tn"], m["fp"]], [m["fn"], m["tp"]]])
    im = ax_cm.imshow(cm, cmap="Blues")

    ax_cm.set_xticks([0, 1]); ax_cm.set_xticklabels(["Pred  –\n(Gemini N/A)", "Pred  +\n(Gemini pos)"])
    ax_cm.set_yticks([0, 1]); ax_cm.set_yticklabels(["True  –\n(Human neg)", "True  +\n(Human pos)"])
    ax_cm.set_title("Confusion Matrix", fontsize=11, pad=10)

    thresh = cm.max() / 2
    for r in range(2):
        for c in range(2):
            label = ["TN", "FP", "FN", "TP"][r * 2 + c]
            ax_cm.text(c, r, f"{label}\n{cm[r, c]:,}",
                       ha="center", va="center", fontsize=12,
                       color="white" if cm[r, c] > thresh else "black")

    # ── right: metrics table ──────────────────────────────────────────────────
    ax_tbl = fig.add_subplot(gs[1])
    ax_tbl.axis("off")

    kappa = m["kappa"]
    if   kappa >= 0.80: strength = "almost perfect"
    elif kappa >= 0.60: strength = "substantial"
    elif kappa >= 0.40: strength = "moderate"
    elif kappa >= 0.20: strength = "fair"
    elif kappa >= 0.00: strength = "slight"
    else:               strength = "poor"

    rows = [
        ["Metric", "Value", "Detail"],
        ["Precision",  f"{m['precision']:.4f}", f"{m['tp']}/{m['tp']+m['fp']} Gemini+ correct"],
        ["Recall",     f"{m['recall']:.4f}",    f"{m['tp']}/{m['tp']+m['fn']} human+ caught"],
        ["F1",         f"{m['f1']:.4f}",         ""],
        ["Accuracy",   f"{m['accuracy']:.4f}",   f"{m['tp']+m['tn']:,}/{n:,} correct"],
        ["Cohen's κ",  f"{m['kappa']:.4f}",      strength],
        ["", "", ""],
        ["Human +",    f"{human_pos:,}",          f"{100*human_pos/n:.1f}% of matched pages"],
        ["Gemini +",   f"{gemini_pos:,}",         f"{100*gemini_pos/n:.1f}% of matched pages"],
    ]

    col_widths = [0.25, 0.2, 0.55]
    col_x      = [0.0, 0.25, 0.45]
    row_h      = 0.094
    y_start    = 0.92

    for r_i, row in enumerate(rows):
        y = y_start - r_i * row_h
        is_header = (r_i == 0)
        for c_i, cell in enumerate(row):
            ax_tbl.text(col_x[c_i], y, cell,
                        transform=ax_tbl.transAxes,
                        fontsize=10 if not is_header else 10,
                        fontweight="bold" if is_header else "normal",
                        va="top", ha="left",
                        color="#222222")
        if is_header:
            line_y = y - row_h / 2   # midpoint between header row and first data row
            ax_tbl.plot([0, 1], [line_y, line_y],
                        transform=ax_tbl.transAxes,
                        color="#888888", linewidth=0.8, clip_on=False)

    ax_tbl.set_title("Metrics", fontsize=11, pad=10)

    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\nFigure saved → {out}", flush=True)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", metavar="PATH",
                        help="Save summary figure to this path (e.g. fig_gemini_vs_human.png)")
    args = parser.parse_args()

    # ── 1. Load human labels ─────────────────────────────────────────────────
    print("Loading human labels from America_results.csv…", flush=True)
    human: dict[str, bool] = {}   # canonical_path → is_positive

    with open(ROOT / "America_results.csv", encoding="latin-1", newline="") as f:
        reader = csv.reader(f)
        next(reader)  # category header row
        next(reader)  # sub-item label row
        for row in reader:
            if len(row) <= ACTION_COL_START:
                continue
            url = row[2].strip()
            if not url.startswith("http"):
                continue
            base = url_to_path(url)
            if not base:
                continue
            is_positive = any(
                v.strip().upper() == "TRUE"
                for v in row[ACTION_COL_START:]
            )
            canon = canonical_path(base)
            if canon:
                human[canon] = is_positive

    print(f"  Human-labeled page paths : {len(human):,}", flush=True)

    # ── 2. Fetch Gemini-classified pages and apply keyword filter in Python ───
    print("\nQuerying DB…", flush=True)
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT wp.page_path, wp.content_text, lc.category
        FROM   web_pages wp
        JOIN   llm_classifications lc ON lc.web_page_id = wp.id
        WHERE  lc.model_used  = %s
          AND  lc.error       IS NULL
          AND  lc.category    IS NOT NULL
    """, (GEMINI_MODEL,))
    all_rows = cur.fetchall()
    conn.close()

    db_rows = [
        (path, cat)
        for path, text, cat in all_rows
        if text and find_keywords(text, _KW_PATTERN)
    ]
    print(f"  Gemini-classified pages fetched : {len(all_rows):,}", flush=True)
    print(f"  Passing keyword filter          : {len(db_rows):,}", flush=True)

    # ── 3. Inner join: only pages present in both datasets ───────────────────
    print("\nMatching pages…", flush=True)
    y_true: list[int] = []
    y_pred: list[int] = []

    seen: set[str] = set()
    for page_path, category in db_rows:
        canon = canonical_path(page_path)
        if not canon or canon not in human:
            continue
        if canon in seen:
            continue
        seen.add(canon)

        y_true.append(int(human[canon]))
        y_pred.append(int(category.strip() != "N/A"))

    n = len(y_true)
    print(f"  Matched pages: {n:,}", flush=True)

    if n == 0:
        print("\nNo pages matched between the two datasets. Cannot compute metrics.")
        return

    # ── 4. Report ─────────────────────────────────────────────────────────────
    m = compute_metrics(y_true, y_pred)

    human_pos  = sum(y_true)
    gemini_pos = sum(y_pred)

    print(f"\n{'='*60}")
    print(f" GEMINI vs HUMAN BASELINE")
    print(f" Model   : {GEMINI_MODEL}")
    print(f" Filter  : keyword scan on content_text (keywords.txt)")
    print(f" n       : {n:,} matched pages")
    print(f"{'='*60}")

    print(f"\nClass balance:")
    print(f"  Human   positive : {human_pos:>5}  ({100*human_pos/n:.1f}%)")
    print(f"  Human   negative : {n-human_pos:>5}  ({100*(n-human_pos)/n:.1f}%)")
    print(f"  Gemini  positive : {gemini_pos:>5}  ({100*gemini_pos/n:.1f}%)")
    print(f"  Gemini  negative : {n-gemini_pos:>5}  ({100*(n-gemini_pos)/n:.1f}%)")

    print(f"\nConfusion matrix (rows=human truth, cols=Gemini prediction):")
    print(f"                  Pred–   Pred+")
    print(f"  True–  (neg)  {m['tn']:>6}  {m['fp']:>6}")
    print(f"  True+  (pos)  {m['fn']:>6}  {m['tp']:>6}")

    print(f"\nMetrics (positive = has environmental action):")
    print(f"  Precision  : {m['precision']:.4f}   {m['tp']}/{m['tp']+m['fp']} Gemini positives are correct")
    print(f"  Recall     : {m['recall']:.4f}   {m['tp']}/{m['tp']+m['fn']} human positives caught")
    print(f"  F1         : {m['f1']:.4f}")
    print(f"  Accuracy   : {m['accuracy']:.4f}   {m['tp']+m['tn']}/{n} correctly classified")
    print(f"  Cohen's κ  : {m['kappa']:.4f}")

    kappa = m['kappa']
    if   kappa >= 0.80: strength = "almost perfect"
    elif kappa >= 0.60: strength = "substantial"
    elif kappa >= 0.40: strength = "moderate"
    elif kappa >= 0.20: strength = "fair"
    elif kappa >= 0.00: strength = "slight"
    else:               strength = "poor (less than chance)"
    print(f"             → {strength} agreement (Landis & Koch scale)")

    if args.plot:
        save_figure(m, n, human_pos, gemini_pos, Path(args.plot))


if __name__ == "__main__":
    main()
