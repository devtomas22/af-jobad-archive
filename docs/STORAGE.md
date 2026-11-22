# Storage sizing

## Question

How much disk does the last 12 months of Swedish job ads take, delivered by the
JobTech Development APIs (ads + JobAd Enrichments)?

## Method

1. `GET /search?limit=0&published-after=…&published-before=…` gives the exact
   number of ads in the window (no download).
2. `measure` samples real ads (200 spread over the window, one request per week,
   different offsets), serializes each with `json.dumps(..., separators=(",",":"),
   ensure_ascii=False)` and takes mean/median bytes.
3. The gzip factor was measured by compressing real 100-ad pages at
   `compresslevel=9` (69,717 B for 431,224 B raw = **0.162**) and then corrected
   against the first full quarter pack (`bytes_gz / (ads × mean_raw)`).
4. Enrichment output was measured from real `/enrichtextdocuments` responses
   (10 docs, 32,301 B ⇒ 3,169 B/doc).

## Numbers (window 2025-11-22 … 2026-11-22)

```
ads in window                522,872
mean raw per ad              5,114 B   (median 5,137, p90 ~5,900)
raw JSONL                   2.67 GB
gzip JSONL                  0.43 GB (0.16) … 0.69 GB (0.26)

enrichment per ad            3,169 B
enrichment raw              1.66 GB
enrichment gzip             0.27 … 0.43 GB

TOTAL (ads + enrichment), gzipped     ~0.7 – 1.1 GB
TOTAL raw JSON, uncompressed          ~4.3 GB
```

Observed on the real 2025Q4 pack: 54,676 ads → see `dist/manifest-2025Q4.json`.

## Scalability

| History kept | ads (approx) | raw | gzip |
|---|---|---|---|
| 1 year | 0.5 M | 2.7 GB | 0.4–0.7 GB |
| 3 years | 1.5 M | 8 GB | 1.3–2 GB |
| 10 years | 5 M | 27 GB | 4.5–7 GB |

GitHub Releases allow 2 GB per asset, so a year compressed is ~4–8×
under the limit; a decade needs splitting per quarter (already the case).

## What dominates

`description.text` + `description.text_formatted` are ~40 % of an ad,
`workplace_address` + `occupation*` + `must_have`/`nice_to_have` ~35 %,
the rest (employer contacts, dates, urls, labels) ~25 %. If you only need
searchable structured fields, dropping `text_formatted` and
`application_details` cuts raw size by ~30 % and gzip by ~15 %.
