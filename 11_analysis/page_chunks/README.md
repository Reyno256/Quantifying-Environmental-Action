# `page_chunks/` — top-level analyses over `page_chunk_classifications`

Everything here was produced with `--source page_chunks`. The same-named
outputs one level up are the `keyword_chunks` versions; they are not
superseded, because the two sources measure different things (see
`../KNOWN_DATA_ISSUES.md`).

Requires the SSH tunnel: `ssh -f -N cosdb` → `localhost:8989`.

## Run order

`action_vs_osm_distance.py` writes the CSV the five re-plotters read, so it
must run first. Everything else is independent.

```sh
python action_vs_osm_distance.py --source page_chunks
  python action_vs_osm_distance_anova.py        --source page_chunks
  python action_logit_osm_distance.py           --source page_chunks
  python action_logit_table.py                  --source page_chunks
  python action_logit_distance_distributions.py --source page_chunks
  python action_distance_cornerplot.py          --source page_chunks

python state_action_map.py            --source page_chunks
python state_action_map.py            --source page_chunks --gray-low-sample
python synagogue_action_pointcloud.py --source page_chunks
python action_political_leesL.py      --source page_chunks
python action_vs_dem_district.py      --source page_chunks
python rd_action_means.py             --source page_chunks
```

## Not regenerated here

* `synagogue_denomination_map.py` — plots denomination and coordinates only,
  touches no classification table, so it is source-independent. The existing
  figure in the parent directory is already correct for both sources.
* Framing figures — no framing data exists for page chunks; see
  `TODO(page_chunk_framing)` in `recreate_common.load_framing()`.
