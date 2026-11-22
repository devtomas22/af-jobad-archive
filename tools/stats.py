#!/usr/bin/env python3
"""Aggregate the packed quarters into stats.json.

    python3 tools/stats.py [--dist dist] [--out stats.json]

One pass over dist/ads-<q>.jsonl.gz plus the manifests. Every number is counted
from the release files, none is estimated; the manifests supply the sizes and
hashes so `verify`/`stats` can never disagree about what was measured.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")


def quarters_in(dist: str) -> list[str]:
    qs = []
    for name in sorted(os.listdir(dist)):
        if name.startswith("ads-") and name.endswith(".jsonl.gz") and "enrichment" not in name:
            qs.append(name[len("ads-"):-len(".jsonl.gz")])
    return qs


def label_of(obj) -> str | None:
    if isinstance(obj, dict):
        return obj.get("label")
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=DIST)
    ap.add_argument("--out", default=os.path.join(ROOT, "stats.json"))
    args = ap.parse_args(argv[1:])

    quarters = quarters_in(args.dist)
    if not quarters:
        raise SystemExit(f"no packed quarters in {args.dist}")

    by_month = collections.Counter()
    by_source = collections.Counter()
    by_field = collections.Counter()
    by_group = collections.Counter()
    by_region = collections.Counter()
    by_country = collections.Counter()
    by_employer = collections.Counter()
    org_numbers: set[str] = set()
    vacancies = 0
    with_deadline = 0
    experience_required = 0
    full_time = 0
    total_ads = 0
    q_rows = []

    for q in quarters:
        path = os.path.join(args.dist, f"ads-{q}.jsonl.gz")
        man_path = os.path.join(args.dist, f"manifest-{q}.json")
        man = json.load(open(man_path, encoding="utf-8")) if os.path.exists(man_path) else {}
        n = raw_bytes = pos_q = 0
        first = last = None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                ad = json.loads(line)
                n += 1
                raw_bytes += len(line.encode())
                pub = str(ad.get("publication_date") or "")
                if pub:
                    first = pub if first is None or pub < first else first
                    last = pub if last is None or pub > last else last
                    by_month[pub[:7]] += 1
                by_source[ad.get("source_type") or "unknown"] += 1
                fld = label_of(ad.get("occupation_field")) or "unknown"
                by_field[fld] += 1
                grp = label_of(ad.get("occupation_group")) or "unknown"
                by_group[grp] += 1
                addr = ad.get("workplace_address") or {}
                by_region[addr.get("region") or "unknown"] += 1
                by_country[addr.get("country") or "unknown"] += 1
                emp = ad.get("employer") or {}
                name = emp.get("name")
                if name:
                    by_employer[name] += 1
                if emp.get("organization_number"):
                    org_numbers.add(str(emp["organization_number"]))
                try:
                    pos = int(ad.get("number_of_vacancies") or 1)
                except (TypeError, ValueError):
                    pos = 1
                vacancies += pos
                pos_q += pos
                if ad.get("application_deadline"):
                    with_deadline += 1
                if ad.get("experience_required") is True:
                    experience_required += 1
                if (label_of(ad.get("working_hours_type")) or "").lower().startswith("heltid"):
                    full_time += 1

        enrich_path = os.path.join(args.dist, f"ads-{q}-enrichment.jsonl.gz")
        e_size = os.path.getsize(enrich_path) if os.path.exists(enrich_path) else 0
        q_rows.append({
            "quarter": q,
            "ads": n,
            "publication_date_from": first,
            "publication_date_to": last,
            "vacancies": pos_q,
            "raw_json_bytes": raw_bytes,
            "ads_gz_bytes": os.path.getsize(path),
            "ads_sha256": man.get("sha256"),
            "enrichment_docs": (man.get("enrichment") or {}).get("docs"),
            "enrichment_gz_bytes": e_size or man.get("enrichment", {}).get("bytes_gz"),
            "enrichment_sha256": (man.get("enrichment") or {}).get("sha256"),
        })
        total_ads += n

    stats = {
        "generated": dt.datetime.now().replace(microsecond=0).isoformat(),
        "dataset": "af-jobad-archive",
        "license": "Ads © Arbetsförmedlingen via JobTech Development (CC0); code MIT",
        "window": {
            "start": min((r["publication_date_from"] for r in q_rows if r["publication_date_from"]), default=None),
            "end": max((r["publication_date_to"] for r in q_rows if r["publication_date_to"]), default=None),
        },
        "totals": {
            "ads": total_ads,
            "positions": vacancies,
            "quarters": len(q_rows),
            "raw_json_bytes": sum(r["raw_json_bytes"] for r in q_rows),
            "ads_gz_bytes": sum(r["ads_gz_bytes"] for r in q_rows),
            "enrichment_gz_bytes": sum(r["enrichment_gz_bytes"] or 0 for r in q_rows),
            "gzip_bytes_total": sum(r["ads_gz_bytes"] for r in q_rows) + sum(r["enrichment_gz_bytes"] or 0 for r in q_rows),
            "mean_raw_bytes_per_ad": round(sum(r["raw_json_bytes"] for r in q_rows) / max(1, total_ads), 1),
            "employer_names": len(by_employer),
            "employer_org_numbers": len(org_numbers),
            "ads_with_application_deadline": with_deadline,
            "ads_experience_required": experience_required,
            "ads_full_time": full_time,
        },
        "by_quarter": q_rows,
        "by_month": dict(sorted(by_month.items())),
        "by_source_type": dict(by_source.most_common()),
        "by_occupation_field": dict(by_field.most_common()),
        "by_region": dict(by_region.most_common()),
        "by_country": dict(by_country.most_common()),
        "top_occupation_groups": [{"label": k, "ads": v} for k, v in by_group.most_common(20)],
        "top_employers": [{"name": k, "ads": v} for k, v in by_employer.most_common(20)],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1, ensure_ascii=False, sort_keys=False)
        f.write("\n")

    t = stats["totals"]
    print(f"{t['ads']:,} ads / {t['positions']:,} positions in {t['quarters']} quarters "
          f"({stats['window']['start'][:10]} .. {stats['window']['end'][:10]})")
    print(f"raw {t['raw_json_bytes']/1e9:.2f} GB -> gzip {t['gzip_bytes_total']/1e6:.1f} MB "
          f"(ads {t['ads_gz_bytes']/1e6:.1f} + sidecar {t['enrichment_gz_bytes']/1e6:.1f})")
    print(f"employers {t['employer_names']:,} names / {t['employer_org_numbers']:,} org numbers; "
          f"full-time {100*t['ads_full_time']/max(1,t['ads']):.0f}%, "
          f"experience required {100*t['ads_experience_required']/max(1,t['ads']):.0f}%")
    print(f"wrote {os.path.relpath(args.out, ROOT)} "
          f"({os.path.getsize(args.out)} B, sha256 {hashlib.sha256(open(args.out,'rb').read()).hexdigest()[:16]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
