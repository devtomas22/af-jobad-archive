#!/usr/bin/env python3
"""Deep checks on a packed quarter: counts, keys, order, window, enrichment join.

    python3 tools/verify_pack.py 2025Q4 2026Q1 2026Q2 2026Q3

`af_jobs_archive.py verify` is the checksum gate (manifest vs file). This is the
content gate: everything that would still be wrong after the hashes match.
Exit code 0 only when every check passes.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, ROOT)

from af_jobs_archive import quarter_bounds  # noqa: E402  single source of truth




def records(path: str):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def check(q: str, window_start: dt.datetime | None = None) -> list[str]:
    fails: list[str] = []
    ads_path = os.path.join(DIST, f"ads-{q}.jsonl.gz")
    man_path = os.path.join(DIST, f"manifest-{q}.json")
    if not os.path.exists(ads_path) or not os.path.exists(man_path):
        return [f"{q}: missing ads file or manifest"]
    man = json.load(open(man_path, encoding="utf-8"))
    q_start, q_end = quarter_bounds(q)
    lo = max(q_start, window_start) if window_start else q_start

    ids: set[str] = set()
    n = dup_ids = bad_date = unsorted = before_window = 0
    empty_headline = 0
    prev = ""
    for ad in records(ads_path):
        n += 1
        aid = str(ad.get("id") or "")
        if aid in ids:
            dup_ids += 1
        ids.add(aid)
        pub = str(ad.get("publication_date") or "")
        try:
            when = dt.datetime.fromisoformat(pub)
        except ValueError:
            bad_date += 1
            continue
        if when < lo:
            before_window += 1
        if pub < prev:
            unsorted += 1
        prev = pub
        if not (ad.get("headline") or "").strip():
            empty_headline += 1

    if n != man["ads"]:
        fails.append(f"{q}: {n} lines but manifest says {man['ads']}")
    if dup_ids:
        fails.append(f"{q}: {dup_ids} duplicate ids")
    if bad_date:
        fails.append(f"{q}: {bad_date} unparseable publication_date")
    if unsorted:
        fails.append(f"{q}: {unsorted} records out of publication_date order")
    if before_window:
        fails.append(f"{q}: {before_window} ads published before {lo.date()}")
    if empty_headline:
        fails.append(f"{q}: {empty_headline} ads without headline")

    enriched_n = e_orphans = e_dups = 0
    e_path = os.path.join(DIST, f"ads-{q}-enrichment.jsonl.gz")
    if os.path.exists(e_path):
        seen: set[str] = set()
        for doc in records(e_path):
            enriched_n += 1
            did = str(doc.get("doc_id") or "")
            if did in seen:
                e_dups += 1
            seen.add(did)
            if did not in ids:
                e_orphans += 1
        if e_dups:
            fails.append(f"{q}: {e_dups} duplicate enrichment doc_ids")
        if e_orphans:
            fails.append(f"{q}: {e_orphans} enrichment docs without a matching ad")

    print(f"{q}: ads={n} unique_ids={len(ids)} span={man['publication_date_from']}.."
          f"{man['publication_date_to']} gz={os.path.getsize(ads_path) / 1e6:.1f}MB "
          f"enriched={enriched_n} cover={100 * enriched_n / max(1, n):.1f}%")
    for f in fails:
        print("  FAIL", f)
    return fails


def main(argv: list[str]) -> int:
    quarters = argv[1:]
    if not quarters:
        print(__doc__)
        return 2
    # the archive is rolling: the oldest quarter is clipped by the 12-month window
    today = dt.date.today()
    window_start = dt.datetime(today.year - 1, today.month, today.day)
    fails: list[str] = []
    for q in quarters:
        fails += check(q, window_start if q == min(quarters) else None)
    print("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECKS FAILED")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
