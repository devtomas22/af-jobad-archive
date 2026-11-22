# API notes (verified live 2026-11-22)

## Historical ads search

```
GET https://historical.api.jobtechdev.se/search
    ?limit=100          # max 100 (limit=0 returns counts only)
    &offset=0           # max 2000  -> hard ceiling of 2100 ads per query
    &published-after=2026-01-01T00:00:00
    &published-before=2026-04-01T00:00:00
```

Response envelope: `{"total": {"value": N}, "positions": M, "hits": [...],
"stats": [], "freetext_concepts": {}}`. `total.value` = number of ads,
`positions` = number of vacancies.

Observed volumes: 522,872 ads published 2025-11-22..2026-11-22; 43,758 ads
currently open (that is what the *jobsearch* API serves); a busy weekday has
~2,900 ads, a quiet weekend day ~230.

Ad fields (`hits[]`): `id`, `external_id`, `original_id`, `headline`,
`webpage_url`, `logo_url`, `application_deadline`, `number_of_vacancies`,
`description{text,text_formatted,company_information,needs,requirements,conditions}`,
`employment_type`, `salary_type`, `salary_description`, `duration`,
`working_hours_type`, `scope_of_work`, `access`, `access_to_own_car`,
`driving_license`, `driving_license_required`, `experience_required`,
`employer{phone_number,email,url,organization_number,name,workplace}`,
`workplace_address{...}`, `occupation`, `occupation_group`,
`occupation_field`, `must_have{skills,languages,work_experiences,education,education_level}`,
`nice_to_have{...}`, `publication_date`, `last_publication_date`, `removed`,
`removed_date`, `source_type`, `timestamp`, `application_details`,
`application_contacts`, `label`.

Mean serialized size (compact JSON): **5,114 B/ad**; median 5,137 B;
p90 ≈ 5,900 B; min ≈ 3,300 B; max ≈ 9,000 B.

Errors come back with HTTP 400 and a JSON body
`{"trackingId": "...", "cause": {"code": "400", "message": "..."}}` — the
message for an out-of-range page is explicit (`200 is not less or equal to 100`).

There is also `https://historical.api.jobtechdev.se/stats` and a Swagger UI at
the API root (`/swagger.json`), useful for re-checking field names.

## JobAd Enrichments

```
POST https://jobad-enrichments-api.jobtechdev.se/enrichtextdocuments
Content-Type: application/json

{"include_terms_info": true,
 "include_sentences": false,
 "documents_input": [{"doc_id": "123", "doc_headline": "…", "doc_text": "…"}]}
```

Max **100 documents per request**. Response is a JSON array (one object per
document) with `doc_id`, `doc_headline` and `enriched_candidates`
(`occupations`, `competencies`, `traits`, `geos`), each candidate carrying
`concept_label`, `term`, `term_misspelled` and `prediction` (0 = not demanded,
1 = demanded).

Measured output: **~3,169 B per ad** with `include_terms_info=true`,
`include_sentences=false`.

Older paths (`/enrich`, `/v1/enrich`) are 404 on the current deployment
(API 1.5.7); `/enrichtextdocuments` and `/enrichtextdocumentsbinary` are the
live endpoints. There is no API key requirement in practice, but send a
descriptive `User-Agent`.

## Politeness

* 6–8 concurrent connections are enough to fetch ~500k ads in well under an
  hour; do not exceed ~10.
* Exponential backoff on 5xx/timeouts; 400/404/422 are not retried.
* Each 100-ad page is ~500–600 kB.
