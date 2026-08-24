"""
label_tool.py — Keyboard-driven labeler for America_Orig.csv human entries.

Shows every field for each row that has substantive Environmental Actions text,
lets you assign one of the nine LLM categories (or N/A / Skip) with a single
keypress, and saves progress after every entry so you can quit and resume.

Usage:
    python label_tool.py                  # label all unlabeled rows
    python label_tool.py --show-labeled   # review already-labeled rows too

Output:
    human_labels.csv  — one row per entry: all original fields + your label
"""

import csv
import os
import sys
import termios
import textwrap
import tty
from pathlib import Path

def _find_project_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "America_Orig.csv").exists():
            return p
    return start.parent

ROOT    = _find_project_root(Path(__file__).parent)
SRC_CSV = ROOT / "America_Orig.csv"
OUT_CSV = Path(__file__).parent / "human_labels.csv"

CATEGORIES = [
    (1, "Community"),
    (2, "Spirituality and Worship"),
    (3, "Kitchen"),
    (4, "Waste"),
    (5, "Energy"),
    (6, "Operations and Maintenance"),
    (7, "Environmental and Climate Justice"),
    (8, "Water"),
    (9, "Other"),
]
KEY_MAP = {str(i): label for i, label in CATEGORIES}
KEY_MAP["0"] = "N/A"
KEY_MAP["s"] = "__SKIP__"
KEY_MAP["S"] = "__SKIP__"
KEY_MAP["q"] = "__QUIT__"
KEY_MAP["Q"] = "__QUIT__"

FIELDNAMES = [
    "Name", "Location", "Address", "Website",
    "Denominational Affiliation", "Environmental Actions",
    "Found on", "Completed", "Activity Status", "label",
]

SKIP_AUTO = {"no", "n/a", ""}


def is_auto_na(text: str) -> bool:
    return text.strip().lower() in SKIP_AUTO


# ── terminal helpers ──────────────────────────────────────────────────────────

def getch() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def clear():
    os.system("clear")


def wrap(text: str, width: int = 90, indent: int = 4) -> str:
    prefix = " " * indent
    return "\n".join(
        textwrap.fill(line, width=width, initial_indent=prefix,
                      subsequent_indent=prefix)
        for line in text.splitlines()
    ) or prefix + "(empty)"


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_source() -> list[dict]:
    with open(SRC_CSV, encoding="latin-1") as f:
        return list(csv.DictReader(f))


def load_done() -> set[str]:
    if not OUT_CSV.exists():
        return set()
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        return {r["Website"] for r in csv.DictReader(f) if r.get("Website")}


def save_label(row: dict, label: str):
    exists = OUT_CSV.exists()
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow({**row, "label": label})


# ── display ───────────────────────────────────────────────────────────────────

def render(row: dict, remaining: int, total: int, done_count: int):
    clear()
    W = 92
    print("═" * W)
    print(f"  Labeled: {done_count}   Remaining: {remaining}   Total scoreable: {total}")
    print("═" * W)
    print(f"  Name        : {row.get('Name', '').strip()}")
    print(f"  Location    : {row.get('Location', '').strip()}")
    print(f"  Address     : {row.get('Address', '').strip()}")
    print(f"  Website     : {row.get('Website', '').strip()}")
    print(f"  Affiliation : {row.get('Denominational Affiliation', '').strip()}")
    print(f"  Found on    : {row.get('Found on', '').strip()}")
    print(f"  Completed   : {row.get('Completed', '').strip()}")
    print(f"  Status      : {row.get('Activity Status', '').strip()}")
    print()
    print("  Environmental Actions (human note):")
    print(wrap(row.get("Environmental Actions", "").strip(), width=88, indent=4))
    print()
    print("─" * W)
    print()
    left  = CATEGORIES[:5]
    right = CATEGORIES[5:]
    for i, (lkey, lcat) in enumerate(left):
        if i < len(right):
            rkey, rcat = right[i]
            rpart = f"    {rkey}  {rcat}"
        else:
            rpart = ""
        print(f"    {lkey}  {lcat:<36s}{rpart}")
    print()
    print(f"    0  N/A{' ' * 37}s  Skip (label later)")
    print(f"    q  Save & quit")
    print()
    print("─" * W)
    print("  Key: ", end="", flush=True)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-labeled", action="store_true",
                        help="Review already-labeled rows too")
    args = parser.parse_args()

    all_rows = load_source()
    scoreable = [r for r in all_rows
                 if not is_auto_na(r.get("Environmental Actions", ""))]
    done = load_done()

    queue = scoreable if args.show_labeled else [
        r for r in scoreable if r.get("Website", "").strip() not in done
    ]

    total = len(scoreable)

    if not queue:
        print(f"All {total} scoreable rows already labeled. "
              "Use --show-labeled to review.")
        return

    print(f"\n  {len(done)} already labeled,  {len(queue)} remaining.\n"
          "  Press any key to start…", end="", flush=True)
    getch()

    done_count = len(done)
    for idx, row in enumerate(queue):
        while True:
            render(row, remaining=len(queue) - idx, total=total,
                   done_count=done_count)
            key = getch()
            if key not in KEY_MAP:
                print(f" '{key}' — unknown, use 0-9 / s / q")
                import time; time.sleep(0.8)
                continue
            action = KEY_MAP[key]
            break

        if action == "__QUIT__":
            clear()
            print(f"\n  Saved and quit. {done_count} rows labeled total.\n")
            return

        if action == "__SKIP__":
            continue

        save_label(row, action)
        done_count += 1

    clear()
    print(f"\n  Done! All {done_count} rows labeled → {OUT_CSV}\n")

    # Auto-write confirmed-negative rows as N/A
    auto_done = load_done()
    na_rows = [r for r in all_rows
               if is_auto_na(r.get("Environmental Actions", ""))
               and r.get("Website", "").strip() not in auto_done]
    if na_rows:
        for r in na_rows:
            save_label(r, "N/A")
        print(f"  Auto-labeled {len(na_rows)} 'No' rows as N/A → {OUT_CSV}\n")


if __name__ == "__main__":
    main()
