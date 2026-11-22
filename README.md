# af-jobad-archive

Rolling **12-month archive of Swedish job ads** from
[JobTech Development](https://jobtechdev.se/) (Arbetsförmedlingen /
Arbetsförmedlingen JobTech) — downloadable, prunable, and published as one
gzip asset per calendar quarter.

Data sources:

| API | Endpoint | What it gives |
|---|---|---|
| Historical ads | `https://historical.api.jobtechdev.se/search` | full ad JSON incl. expired ads, the archive source |
| JobAd Enrichments | `https://jobad-enrichments-api.jobtechdev.se/enrichtextdocuments` | occupations / competencies / traits / geos with a *demanded* prediction (0..1) |

Both are open, key-less, and free.

## What a cut looks like (measured 2026-11-22)

Five assets: the four complete quarters in the window plus the partial current
quarter, cut at download time.

| Quarter | ads | raw JSON | ads .gz | enrichment .gz |
|---|---|---|---|---|
| 2025Q4 (from 2025-11-22) | 54,435 | 0.32 GB | 75.4 MB | 32.1 MB |
| 2026Q1 | 152,404 | 0.88 GB | 203.6 MB | 84.5 MB |
| 2026Q2 | 149,565 | 0.86 GB | 198.2 MB | 83.4 MB |
| 2026Q3 | 121,594 | 0.72 GB | 164.5 MB | 69.8 MB |
| 2026Q4 (partial, to 2026-11-19) | 44,665 | 0.27 GB | 61.1 MB | 26.4 MB |
| **total** | **522,663** | **3.05 GB** | **702.7 MB** | **296.1 MB** |

So a full rolling year is **≈ 1 GB gzipped**, measured end to end. Every file
was checked against its manifest (`verify`: sha256 + byte size, 10/10) and
content-checked (`tools/verify_pack.py`: unique ids, publication order, window
bounds, 100 % enrichment coverage). Checksums, spans and caveats:
[`docs/DATASET.md`](docs/DATASET.md).

Rules of thumb: **ads × 5.8 kB** for raw JSON, **÷ 4.34** for gzip, and the
enrichment sidecar at **≈ 0.57 kB/ad gzipped** (a bit over a third of the ad
dump). Ten years of history would be ≈ 5M ads ≈ 29 GB raw / ≈ 6.7 GB gzip for
the ads plus ≈ 2.8 GB of sidecar.

`dist/` is gitignored; the data ships as GitHub release assets next to its
manifest.

## Quick start

```bash
python3 af_jobs_archive.py plan                 # window + projected size
python3 af_jobs_archive.py download --all       # fetch 4 quarters (resumable)
python3 af_jobs_archive.py pack --all           # dedupe, sort, gzip -> dist/
python3 af_jobs_archive.py enrich --all         # JobAd Enrichments sidecar
python3 af_jobs_archive.py prune --all          # drop > 12 months old
python3 af_jobs_archive.py verify --all         # checksums vs manifests
python3 tools/verify_pack.py 2025Q4 2026Q1     # content checks (ids, order, join)
python3 af_jobs_archive.py release --quarter 2026Q1
```

`--all` is the four most recent **complete** quarters. The current partial
quarter is not included in `--all`; add it by name to get a data set that really
reaches today:

```bash
python3 af_jobs_archive.py download --quarters 2026Q4
python3 af_jobs_archive.py pack     --quarters 2026Q4
python3 af_jobs_archive.py enrich   --quarters 2026Q4
```

`pack` and `enrich` are both resumable. `pack` is idempotent per quarter (it
rewrites the quarter atomically and re-derives the manifest). `enrich` keeps
every document it already fetched — a killed run leaves
`dist/ads-<q>-enrichment.jsonl.gz.tmp`, the next run repairs the truncated
gzip tail, keeps all complete records, fetches only what is missing, then
dedupes by `doc_id` on the way to the final file.

If the JobTech API is flaky from your network (502/504 on deep pages), send the
traffic through a SOCKS5 egress — every HTTP call then goes via `curl`:

```bash
export AF_PROXY=socks5h://user:pass@host:port
python3 af_jobs_archive.py download --all --workers 20
```

`--workers 20` is safe through a rotating proxy; direct connections are happiest
at 6-8.

`download` is resumable: window-level state lives in `data/state/<quarter>.json`,
so an interrupted run continues where it stopped and never re-fetches a window.

## How the pruning works

`--end` (default: today) defines the window `[end-12 months, end)`.
Quarters that end before the cutoff are deleted outright (`prune`), and `pack`
drops individual ads published before the cutoff, so the oldest quarter is
trimmed row-by-row instead of being thrown away whole.

## API constraints that shape the design

* `limit` ≤ 100 and `offset` ≤ **2,000** → at most 2,100 ads are reachable per
  query. A busy day has ~2,900 ads, so the pipeline walks **6-hour windows** and
  recursively halves any window whose `total` exceeds 2,100.
* `published-after` is inclusive, `published-before` is **also inclusive** at
  second granularity (measured: an ad with `publication_date` exactly at
  `2026-01-01T00:00:00` comes back from both the Q4 and the Q1 window). Adjacent
  windows therefore overlap by that one second; `pack` assigns each ad to a
  quarter by its own `publication_date`, and counts the strays in the manifest's
  `dropped_after_quarter_end`.
* Duplicates are keyed on `id`; if an ad is republished, the record with the
  latest `publication_date` wins.
* Ad records are safe to append: each window is written as its own gzip member.

## Output format

`dist/ads-<quarter>.jsonl.gz` — one ad per line, compact JSON, exactly the
`hits[]` objects of the historical API (headline, description, employer,
workplace_address, occupation*, must_have/nice_to_have, dates, source_type…).

`dist/ads-<quarter>-enrichment.jsonl.gz` — one enriched document per line:

```json
{"doc_id":"<ad id>","doc_headline":"Sjuksköterska…","enriched_candidates":{
  "occupations":[{"concept_label":"Sjuksköterska","term":"sjuksköterska","prediction":0.91}],
  "competencies":[…],"traits":[…],"geos":[…]}}
```

`dist/manifest-<quarter>.json` — counts, date range, byte size, sha256.

## Scheduled releases

`.github/workflows/quarterly-release.yml` runs on the 1st of Jan/Apr/Jul/Oct,
rebuilds the window, packs the just-finished quarter and publishes a release
tagged `YYYYQn` with the gzip + enrichment + manifest assets. A manual run
(`workflow_dispatch`) accepts a quarter to re-cut.

## License / attribution

Code: MIT. Data: © Arbetsförmedlingen, published as open data by JobTech
Development (CC0 for the ads API). Keep the attribution when redistributing.
