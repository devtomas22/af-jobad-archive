# Release set — 2026-11-22

The archive as cut on **2026-11-22** (UTC): every quarter that intersects the
rolling 12-month window `[2025-11-22, 2026-11-22)`. Built locally with
`af_jobs_archive.py`; nothing here was hand-edited.

```
ads     522,663 unique ads  (3.05 GB raw JSONL, 702.7 MB gzip, 5,832 B/ad mean)
sidecar 522,663 enrichment docs (296.1 MB gzip, ~0.57 kB/doc)
total   ~1.0 GB gzipped for 12 months, in five GitHub-release-sized assets
```

## Per quarter

`dist/ads-<q>.jsonl.gz` — one ad per line, compact JSON, sorted by
`publication_date` then `id`. `dist/ads-<q>-enrichment.jsonl.gz` — one enriched
document per line, keyed by `doc_id` = ad `id`.

| Quarter | ads | span (publication_date) | raw JSON | ads .gz | enrichment .gz |
|---|---|---|---|---|---|
| 2025Q4 | 54,435 | 2025-11-22 .. 2025-12-31 | 324.8 MB | 75.4 MB | 32.1 MB |
| 2026Q1 | 152,404 | 2026-01-01 .. 2026-03-31 | 876.7 MB | 203.6 MB | 84.5 MB |
| 2026Q2 | 149,565 | 2026-04-01 .. 2026-06-30 | 863.1 MB | 198.2 MB | 83.4 MB |
| 2026Q3 | 121,594 | 2026-07-01 .. 2026-09-30 | 715.6 MB | 164.5 MB | 69.8 MB |
| 2026Q4 | 44,665 | 2026-10-01 .. 2026-11-19 | 268.0 MB | 61.1 MB | 26.4 MB |
| **total** | **522,663** | 2025-11-22 .. 2026-11-19 | **3.05 GB** | **702.7 MB** | **296.1 MB** |

2026Q4 is the **partial** current quarter (cut at download time); the other four
are complete calendar quarters. 2025Q4 is clipped to the window start, 2025-11-22.

## Checksums

```
2025Q4 ads  cc3b60f017b2784fcbc1a374f032bcffdfc662bded50922c775cd529e2f075e4  75,413,884 B
2025Q4 enr  f6bf84feb83a6a4da43c8cfe10d601261f0d2c01ef98f584a7690af1cbdd44ae  32,052,458 B
2026Q1 ads  ed315b5fad3de29a7938d2fd65a7003a187c8e1bfd8fd31fe321ede7d5605251 203,570,871 B
2026Q1 enr  4e381faf63fc47ba87bcdf186b871c9834b90ed969b77f949857549a2a7cb29c  84,485,912 B
2026Q2 ads  51be1864b30f44e8e8db76145ead0dc18f2ebadd9f9059eb365d7223fead175f 198,154,020 B
2026Q2 enr  ffa599d19c999d9656c8b7db5ee82851fecb9a4140e072853c9e8e072ab4e8ab  83,387,191 B
2026Q3 ads  546453e1ad747f0f285ec3f6b423191314ba5e410c6fbdeebe3c140742ba2770 164,467,416 B
2026Q3 enr  ffece0d9858ffd6b049b48c6c1db9938b9a07e8bdcea0d21760ae786ed700af9  69,775,546 B
2026Q4 ads  e6f6312066f870a4bc9aaa132c885c2d1d73c5abe2b1603e6a2cc4596521d6a7  61,126,109 B
2026Q4 enr  231e44b981fab41366ae952b28cc875fbecc5ffbf289c6676891555e99269179  26,379,047 B
```

Each quarter's `dist/manifest-<q>.json` carries the same numbers plus
`dropped_older_than_window` and `dropped_after_quarter_end`.

## Published

All five are live as GitHub releases (tag = quarter, assets = ads + sidecar +
manifest); each asset's `sha256` digest on GitHub was checked against the local
manifest after upload:

* https://github.com/devtomas22/af-jobad-archive/releases/tag/2025Q4
* https://github.com/devtomas22/af-jobad-archive/releases/tag/2026Q1
* https://github.com/devtomas22/af-jobad-archive/releases/tag/2026Q2
* https://github.com/devtomas22/af-jobad-archive/releases/tag/2026Q3
* https://github.com/devtomas22/af-jobad-archive/releases/tag/2026Q4

```bash
export GITHUB_TOKEN=$(gh auth token)
python3 af_jobs_archive.py release --quarter 2026Q4
```

Releasing a quarter that already exists is not idempotent: GitHub rejects a
second asset with the same name (422), so delete the release first when re-cutting.

## Verification (run 2026-11-22, both gates green)

`python3 af_jobs_archive.py verify --quarters 2025Q4 2026Q1 2026Q2 2026Q3 2026Q4`
— sha256 and byte size of every ads file and sidecar against its manifest: 10/10 OK.

`python3 tools/verify_pack.py 2025Q4 2026Q1 2026Q2 2026Q3 2026Q4` — content gate:

| Quarter | lines | unique ids | duplicate ids | out-of-order | before window | enrichment coverage |
|---|---|---|---|---|---|---|
| 2025Q4 | 54,435 | 54,435 | 0 | 0 | 0 | 100.0 % |
| 2026Q1 | 152,404 | 152,404 | 0 | 0 | 0 | 100.0 % |
| 2026Q2 | 149,565 | 149,565 | 0 | 0 | 0 | 100.0 % |
| 2026Q3 | 121,594 | 121,594 | 0 | 0 | 0 | 100.0 % |
| 2026Q4 | 44,665 | 44,665 | 0 | 0 | 0 | 100.0 % |

Also checked in a one-off pass: the same id never appears in two quarters
(522,663 rows, 522,663 distinct ids across the set), and every enrichment
`doc_id` has a matching ad.

## How it was built

```bash
python3 af_jobs_archive.py download --quarters 2025Q4 2026Q1 2026Q2 2026Q3 2026Q4 --workers 16
python3 af_jobs_archive.py pack     --quarters 2025Q4 2026Q1 2026Q2 2026Q3 2026Q4
python3 af_jobs_archive.py enrich   --quarters 2025Q4 2026Q1 2026Q2 2026Q3 2026Q4
```

Timings on 4 vCPU / 8 GB: download 5–20 min per quarter (the first quarter of a
run also pays for the 6-hour window-total scan), pack ~2.5 min per 150k-ad
quarter (`gzip -9` is the bottleneck), enrich ~250 docs/s ⇒ ~35 min for the
whole year, ~2 min for the 45k-ad partial quarter.

## Caveats

* **Rolling window.** 2025Q4 starts at 2025-11-22, not 2025-10-01, and 2026Q4
  stops at the last ad the API had published at cut time (2026-11-19 — the
  historical API trails real time by a couple of days). A later run re-cuts
  both ends.
* **Counts drift.** The live API reported 522,872 ads for the same window when
  `measure` ran earlier the same day; after dedupe and the boundary rule below,
  522,663 rows remain. Treat the manifest numbers, not the API's, as the truth
  for this release.
* **Quarter-boundary ads.** `published-before` is inclusive at second
  granularity, so an ad published exactly at `2026-01-01T00:00:00` is returned
  by both the Q4 and the Q1 window. `pack` assigns each ad by its own
  `publication_date` (`dropped_after_quarter_end` in the manifest counts the
  rows that belonged to the next quarter: 167 / 92 / 49 / 20 / 0).
* **Dedupe rule.** Duplicate ids inside a quarter keep the record with the
  latest `publication_date` (republished ads).
* **Enrichment is a model output**, not a fact: `prediction` is the API's own
  0..1 demand score, produced by JobTech's JobAd Enrichments model. The sidecar
  has 100 % coverage of the ads in this cut but is not re-run for ads already
  in it on a later enrich pass.
* **Not committed.** `dist/` is gitignored (≈1 GB); the data ships as GitHub
  release assets, the repo carries the code, the manifests and this document.
