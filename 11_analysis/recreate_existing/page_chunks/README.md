# `page_chunks/` — analyses over `page_chunk_classifications`

Everything in this directory was produced with `--source page_chunks`. The
figures one level up (in `recreate_existing/`) are the `keyword_chunks`
versions and are **not** superseded by these — the two sources measure
different things.

Regenerate with:

```sh
python analysis_overview.py     --source page_chunks
python analysis_categories.py   --source page_chunks
python analysis_denomination.py --source page_chunks
python analysis_political.py    --source page_chunks
python analysis_clustering.py   --source page_chunks
python analysis_framing.py      --source page_chunks
```

(Requires the SSH tunnel: `ssh -f -N cosdb` → `localhost:8989`.)

## What changed vs. the keyword-gated figures

| | `keyword_chunks` | `page_chunks` |
|---|---|---|
| Table | `llm_chunk_classifications` | `page_chunk_classifications` |
| Population | pages that hit a keyword | every page with `content_text` |
| Unit | one ±500-char window **per keyword match** | fixed 500-char non-overlapping chunks |
| Active denominator | 2,664 | 2,657 |
| Synagogues with ≥1 action | 1,200 | 1,353 |
| Action chunks | 16,615 | 13,561 |

`page_chunks` finds **more synagogues** but **fewer chunks**: the keyword-gated
table emits a window per match, so keyword-dense pages are counted repeatedly,
while page chunks count each span of text once.

**The populations are not nested.** `page_chunks` finds 221 synagogues that
`keyword_chunks` misses and misses 61 that it finds (1,177 in both). Do not
read these figures as a corrected version of the others — per-synagogue counts
are not comparable across sources.

The outlier cut is recomputed per source (45 synagogues excluded here vs 38),
and it drops 46% of all action chunks. See `../../KNOWN_DATA_ISSUES.md`.

## Framing

Framing (Implicit / Explicit / Embedded) is available for both sources.
`page_chunk_framing` classifies every confirmed-action `page_chunk_classifications`
row with the same judge used for `llm_chunk_framing`
(`13_embeddedvsimplictvsexplict/run_page_chunk_framing_classifications.py`) —
25,052 rows, 0 errors, matching PCC's non-N/A action-chunk count exactly.
`fig_framing.png`, `fig_framing_categories.png`,
`fig_framing_categories_by_party.png`, and `fig_denomination_framings.png` are
all produced here like every other figure.
