# Storage sizing

## Question

How much disk does the last 12 months of Swedish job ads take, delivered by the
JobTech Development APIs (ads + JobAd Enrichments)?

## Answer (measured on a full cut, 2026-11-22)

```
ads in window                522,872   (live API count for 2025-11-22..2026-11-22)
ads packed                   522,663   (after dedupe + quarter-boundary rule)
mean raw per ad              5,832 B   compact JSON, one ad per line
raw JSONL                    3.05 GB
gzip -9 JSONL                702.7 MB  (4.34x, 0.23 of raw)

enrichment per ad            567 B     gzip, one enriched doc per line
enrichment gzip              296.1 MB
raw enrichment               ~1.6 GB   (~3.2 kB/doc before compression)

TOTAL (ads + enrichment), gzipped    ~1.0 GB
TOTAL raw JSON, uncompressed         ~4.6 GB
```

Per-quarter numbers and sha256s: [`DATASET.md`](DATASET.md).

## Method

1. `GET /search?limit=0&published-after=…&published-before=…` gives the exact
   number of ads in the window (no download).
2. `measure` samples real ads (200 spread over the window, one request per week,
   different offsets) and serializes each with `json.dumps(..., separators=(",",":"),
   ensure_ascii=False)` to get mean/median bytes. The sample said 5,114 B/ad; the
   full pack says 5,832 B/ad, so a 200-ad sample under-estimates by ~12 % — use
   the packed figure when planning.
3. The gzip factor is not a guess any more: it is `sum(bytes_gz) / sum(raw)` over
   the five released quarters (4.31–4.38 per quarter).
4. Enrichment output was measured from real `/enrichtextdocuments` responses and
   then confirmed over 522,663 documents (0.55–0.59 kB/doc gzipped).

## Scalability

| History kept | ads (approx) | raw | gzip (ads + sidecar) |
|---|---|---|---|
| 1 year | 0.52 M | 3.0 GB | ~1.0 GB |
| 3 years | 1.6 M | 9 GB | ~3 GB |
| 10 years | 5 M | 29 GB | ~9.5 GB |

GitHub Releases allow 2 GB per asset, so a year compressed is 3–30× under the
limit; a decade needs splitting per quarter (already the case) or per half-year.

## What dominates

`description.text` + `description.text_formatted` are ~40 % of an ad,
`workplace_address` + `occupation*` + `must_have`/`nice_to_have` ~35 %,
the rest (employer contacts, dates, urls, labels) ~25 %. If you only need
searchable structured fields, dropping `text_formatted` and
`application_details` cuts raw size by ~30 % and gzip by ~15 %.

## Throughput (4 vCPU / 8 GB, direct connection)

```
download   16 workers, 6-hour windows halved when total > 2100     5–20 min per quarter
pack       gzip -9, dedupe, sort, manifest                         ~2.5 min per 150k ads
enrich     16 workers, 100 docs per request, ~250 docs/s           ~35 min for 522k docs
```

The enrichment API is the long pole. The ads API is fast but flaky from some
networks (502/504 on deep pages), which is what `AF_PROXY` exists for.
