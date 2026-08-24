"""
Dependency-free fixed-size text chunking, shared by every chunk-vs-keyword /
chunk-vs-rubric research script in this directory AND by the production
population script (08_database/populate_page_chunk_classifications.py).

Deliberately has zero imports beyond the stdlib: chunk_vs_keyword_embed.py
(where this used to live) transitively imports compare_cohere_vs_keyword.py,
which requires `cohere` installed and COHERE_API_KEY set purely as an
accident of module layout — a production DB script has no business needing
a Cohere client just to import a 20-line string-splitting function.
"""

CHUNK_CHARS = 500     # target chunk size (snapped to nearest whitespace)
# No cap on total page length — the whole of content_text is chunked. Note a
# handful of pages sit at exactly 500,000 chars (e.g. chaiodom.com/parshos.html,
# congregationofmoses.org/yahrzeits.html) — almost certainly an upstream
# truncation limit from crawling/extraction, not organic page length; those
# pages will still produce ~1,000 chunks each here.


def chunk_text(text: str, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Split into ~chunk_chars windows, snapped to the nearest whitespace so
    words aren't cut in half. No cap on total length."""
    text = text or ""
    if not text.strip():
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_chars, n)
        if end < n:
            ws = text.rfind(" ", start, end)
            if ws > start:
                end = ws
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks
