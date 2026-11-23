# Classification index (reverse lookup)

`tools/classify.py` builds `index/classify.sqlite` from `dist/`: a normalised
**label → ads** posting index plus an **ad → labels** lookup, so you can go from
a classification category back to the ads (and the other way round) without
reading 3 GB of JSONL. Built 2026-11-22 in 209 s, 785 MB for 522,663 ads and
14,037 labels. `index/` is gitignored — rebuild it with one command.

```bash
python3 tools/classify.py build                  # 3.5 min, 785 MB
python3 tools/classify.py categories             # what is indexed and how big
python3 tools/classify.py values --category occupation_group --top 20
python3 tools/classify.py ads --category occupation_field --label "Data/IT" --quarter 2026Q4
python3 tools/classify.py explain --id <ad id>   # every label that ad carries
python3 tools/classify.py concepts --kind competencies --min-pred 0.75 --top 20
```

## Categories (all indexed at ad level)

| category | kind | labels | ad-labels |
|---|---|---|---|
| occupation_field | taxonomy | 23 | 522,663 |
| occupation_group | taxonomy | 400 | 522,663 |
| occupation | taxonomy | 3,334 | 522,663 |
| employment_type | terms | 8 | 522,663 |
| salary_type | terms | 3 | 522,663 |
| duration | terms | 9 | 475,387 |
| working_hours_type | terms | 2 | 475,415 |
| region | geo | 21 | 511,562 |
| municipality | geo | 290 | 511,562 |
| country | geo | 53 | 522,663 |
| source_type | source | 4 | 522,663 |
| must_have.skills | must_have | 1,764 | 16,304 |
| must_have.languages | must_have | 91 | 69,563 |
| must_have.work_experiences | must_have | 2,455 | 108,488 |
| must_have.education | must_have | 25 | 12,150 |
| must_have.education_level | must_have | 6 | 12,150 |
| nice_to_have.* | nice_to_have | 2,443–2,974 | 10,561–100,384 |

`ad-labels` is the number of (ad, label) pairs, so a category with fewer than
522,663 pairs does not cover every ad — `working_hours_type` is absent on ~9 %
of ads, `region`/`municipality` on ~2 % (ads with no workplace address), and the
`must_have`/`nice_to_have` blocks only exist where the employer filled them in.
`country` has 53 labels because every ad names one, Sweden for 99.5 %.

Enrichment concepts are indexed as **aggregates** only (`concepts` table):
ads mentioning the concept, mentions, and the mean `prediction` weighted by
mentions. Current counts: competencies 36,920, occupations 9,924, geos 2,773,
traits 482. A posting-level index of 20k competencies × 522k ads would be
hundreds of millions of rows for little gain — join the sidecar JSONL when you
need per-ad evidence for a specific concept.

## Schema

```sql
category(id, name, kind, labels, ads)              -- the registry you query first
label(id, category_id, label, concept_id, ads)     -- one row per category+label
ad(id, quarter, publication_date, headline, employer, employer_orgnr,
   region, municipality, vacancies, webpage_url)
ad_label(label_id, ad_id, weight)                  -- POSTING LIST, WITHOUT ROWID
concept(kind, label, ads, mentions, pred_sum, mean_prediction)   -- enrichment rollup
meta(key, value)                                   -- build time, ad count, quarters
```

`ad_label` is the reverse index: primary key `(label_id, ad_id)` gives the
posting list in one range scan; the secondary index on `ad_id` gives `explain`.

## Worked example: how many IT ads?

Three defensible answers, depending on the definition (2026-11-22 cut):

| definition | ads | share |
|---|---|---|
| `occupation_field = Data/IT` (official taxonomy) | **30,389** | 5.8 % |
| + IT-role occupations filed under another field (IT-chef under Chefer, dataanalytiker under Försäljning, IT-jurist under Administration…) | **31,150** | 6.0 % |
| + ads whose enrichment occupations contain an IT role (text-level signal) | **37,483** | 7.2 % |

Per quarter of the narrow set: 2025Q4 3,855 · 2026Q1 8,817 · 2026Q2 8,383 ·
2026Q3 6,760 · 2026Q4 2,574. Top groups inside it: Mjukvaru- och
systemutvecklare 13,865, Systemanalytiker och IT-arkitekter 3,520,
Systemförvaltare 2,170, Drifttekniker IT 1,995, Supporttekniker IT 1,986.
Regions: Stockholm 12,043, Västra Götaland 4,819, Skåne 2,610,
Östergötland 2,531.

```sql
-- narrow: the field label is the taxonomy's own answer
SELECT l.ads FROM label l JOIN category c ON c.id = l.category_id
WHERE c.name = 'occupation_field' AND l.label = 'Data/IT';
```

```bash
# one label at a time from the CLI
python3 tools/classify.py ads --category occupation_field --label "Data/IT" --limit 10
```

The wider definitions need a join across `occupation` and `occupation_field`
(both live in `ad_label`); the enrichment-level count needs one streaming pass
over `dist/ads-*-enrichment.jsonl.gz`, since concepts are only aggregated in the
index.

## Caveats

* **Concept ids are the taxonomy's**, `concept_id` in `label` — the same label
  can exist in two categories (e.g. `Sjuksköterska, grundutbildad` is both an
  `occupation` and a `must_have.work_experiences` value); they are separate rows,
  keyed by category.
* **Enrichment `ads` == `mentions`** because the sidecar carries one entry per
  concept per document, not a term frequency.
* **Labels are as published**, not normalised: `Sjuksköterska, grundutbildad`
  (taxonomy) and `Sjuksköterska` (enrichment) are different strings. Fold case
  and diacritics yourself if you need fuzzy matching.
* **Rebuild after a re-cut.** The index holds ad ids; a re-packed quarter
  invalidates the postings for that quarter. `stats.json` covers volumes, this
  index covers membership — regenerate both together.
