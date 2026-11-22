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

## Storage, measured (2026-11-22)

Measured with `af_jobs_archive.py measure` against the live API over the
window `2025-11-22 .. 2026-11-22`:

| Metric | Value |
|---|---|
| ads in the last 12 months | **522,872** |
| mean raw JSON per ad | **5,114 B** (median 5,137, p90 ≈ 5,900) |
| **raw JSONL, 1 year** | **≈ 2.67 GB** |
| **gzip -9 JSONL, 1 year** | **≈ 0.43–0.69 GB** (0.16–0.26 of raw) |
| JobAd Enrichments sidecar, raw | ≈ 3.2 kB/ad → ≈ 1.66 GB |
| enrichment sidecar, gzip | ≈ 0.27–0.45 GB |
| **everything, gzipped, 1 year** | **≈ 0.7–1.2 GB ≈ 1 GB** |

Per quarter (4 releases):

| Quarter | ads | raw | gzip asset |
|---|---|---|---|
| 2025Q4 (from 2025-11-22) | 54,676 | 0.24 GB | ~0.04–0.08 GB |
| 2026Q1 | 152,226 | 0.66 GB | ~0.11–0.22 GB |
| 2026Q2 | 149,408 | 0.64 GB | ~0.10–0.21 GB |
| 2026Q3 | 121,854 | 0.53 GB | ~0.09–0.17 GB |
| **total** | **478,164** | **2.07 GB** | **~0.34–0.68 GB** |

Rules of thumb: **ads × 5 kB** for raw JSON, **÷ 4.5** for gzip, and the same
again for the enrichment sidecar. Ten years of history would be ≈ 5M ads ≈
25 GB raw / ≈ 5 GB gzipped.

Ground truth from the first full pack: 54,676-ad quarter → check
`dist/manifest-*.json` (`bytes_gz`, `sha256`) after a run.

## Quick start

```bash
python3 af_jobs_archive.py plan                 # window + projected size
python3 af_jobs_archive.py download --all       # fetch 4 quarters (resumable)
python3 af_jobs_archive.py pack --all           # dedupe, sort, gzip -> dist/
python3 af_jobs_archive.py enrich --all         # JobAd Enrichments sidecar
python3 af_jobs_archive.py prune --all          # drop > 12 months old
python3 af_jobs_archive.py verify --all         # checksums vs manifests
python3 af_jobs_archive.py release --quarter 2026Q1
```

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
* `published-after` / `published-before` are half-open (`>=..<`, second
  granularity); windows are chained without gaps.
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
