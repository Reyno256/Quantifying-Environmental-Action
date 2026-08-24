"""
keyword_search.py — Search for keywords in a body of text.

Mirrors the keyword-matching logic from America_rvest.Rmd:
  - keywords.txt            -> exact word matches  (\bword\b)
  - keyword_variations.txt  -> prefix matches      (\bprefix\w*\b, trailing * stripped)

Usage:
  python keyword_search.py --text "some text to search"
  python keyword_search.py --file path/to/document.txt
  echo "some text" | python keyword_search.py
  python keyword_search.py --text "..." --keywords keywords.txt --variations keyword_variations.txt
"""

import re
import sys
import argparse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def _parse_quoted_list(path: Path) -> list[str]:
    """Parse a file containing a comma-separated list of quoted strings."""
    raw = path.read_text(encoding="utf-8")
    return [m.group(1) for m in re.finditer(r'"([^"]+)"', raw)]


def load_keywords(
    exact_path: Path = SCRIPT_DIR / "keywords.txt",
    variations_path: Path = SCRIPT_DIR / "keyword_variations.txt",
) -> re.Pattern:
    """
    Build and return a compiled regex from both keyword files.
    Exact keywords    -> \bword\b
    Variation keywords (trailing *) -> \bprefix\w*\b
    """
    exact_keywords = _parse_quoted_list(exact_path)
    variation_keywords = _parse_quoted_list(variations_path)

    patterns: list[str] = []

    for kw in exact_keywords:
        patterns.append(r"\b" + re.escape(kw) + r"\b")

    for kw in variation_keywords:
        base = kw.rstrip("*")
        patterns.append(r"\b" + re.escape(base) + r"\w*\b")

    combined = "|".join(patterns)
    return re.compile(combined, re.IGNORECASE)


def find_keywords(text: str, pattern: re.Pattern) -> list[str]:
    """Return deduplicated list of matched keywords (lowercased), preserving order."""
    seen: set[str] = set()
    results: list[str] = []
    for match in pattern.finditer(text.lower()):
        word = match.group(0)
        if word not in seen:
            seen.add(word)
            results.append(word)
    return results


def find_keyword_matches(text: str, pattern: re.Pattern) -> list[tuple[str, int, int]]:
    """Return every keyword occurrence as (keyword, start_offset, end_offset).

    Offsets are 0-based character positions into the original text; end is exclusive.
    All occurrences are returned (not deduplicated).
    """
    lower = text.lower()
    return [(m.group(0), m.start(), m.end()) for m in pattern.finditer(lower)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search for environment/sustainability keywords in text."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="Text to search (pass as a string)")
    source.add_argument("--file", help="Path to a text file to search")
    parser.add_argument(
        "--keywords",
        default=str(SCRIPT_DIR / "keywords.txt"),
        help="Path to exact-match keywords file (default: keywords.txt)",
    )
    parser.add_argument(
        "--variations",
        default=str(SCRIPT_DIR / "keyword_variations.txt"),
        help="Path to wildcard keywords file (default: keyword_variations.txt)",
    )
    args = parser.parse_args()

    pattern = load_keywords(Path(args.keywords), Path(args.variations))

    if args.text:
        body = args.text
    elif args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        parser.print_help()
        sys.exit(1)

    matches = find_keywords(body, pattern)

    if matches:
        print(", ".join(matches))
    else:
        print("(no keywords found)")


if __name__ == "__main__":
    main()
