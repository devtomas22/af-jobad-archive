#!/usr/bin/env python3
"""Query a downloaded quarter without unpacking it.

    ./tools/read_archive.py dist/ads-2026Q1.jsonl.gz --q sjukskoterska --limit 5
    ./tools/read_archive.py dist/ads-2026Q1.jsonl.gz --stats
"""
import argparse, gzip, json, re, sys


def matches(ad, pattern):
    blob = " ".join([
        ad.get("headline") or "",
        (ad.get("description") or {}).get("text") or "",
        (ad.get("employer") or {}).get("name") or "",
        (ad.get("workplace_address") or {}).get("municipality") or "",
        " ".join(o.get("label", "") for o in (ad.get("occupation") or [])),
    ]).lower()
    return bool(re.search(pattern, blob, re.I))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--q", help="regex over headline/text/employer/city/occupation")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    n = 0
    fields = {}
    with gzip.open(a.path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            ad = json.loads(line)
            n += 1
            if a.stats:
                d = ad.get("publication_date", "")[:7]
                fields[d] = fields.get(d, 0) + 1
                continue
            if a.q and matches(ad, a.q) and len(fields) < a.limit:
                fields[ad.get("id")] = 1
                print(json.dumps({"id": ad.get("id"), "headline": ad.get("headline"),
                                  "employer": (ad.get("employer") or {}).get("name"),
                                  "city": (ad.get("workplace_address") or {}).get("municipality"),
                                  "published": ad.get("publication_date"),
                                  "url": ad.get("webpage_url")}, ensure_ascii=False))
    if a.stats:
        for k in sorted(fields):
            print(k, fields[k])
        print("total", n)


if __name__ == "__main__":
    main()
