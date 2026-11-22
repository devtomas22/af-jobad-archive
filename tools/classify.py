#!/usr/bin/env python3
"""Classification index over the packed archive: category+label -> ads, ad -> labels.

    python3 tools/classify.py build                     # -> index/classify.sqlite
    python3 tools/classify.py categories                # what is indexed, how many labels
    python3 tools/classify.py values --category occupation_group --top 20
    python3 tools/classify.py ads --category occupation_field --label "Data/IT" --limit 5
    python3 tools/classify.py ads --category must_have.languages --label Svenska --quarter 2026Q1
    python3 tools/classify.py explain --id 5f1c...      # every label an ad carries
    python3 tools/classify.py concepts --kind competencies --min-pred 0.7 --top 20

The posting table is normalised (label ids, ad ids), so 500k ads stay a few
hundred MB. Enrichment concepts are indexed as *aggregates* (ads, mentions,
mean prediction per concept) — a posting-level index of 20k competencies ×
500k ads is not worth the space; use `concepts` for demand questions and the
sidecar JSONL when you need per-ad evidence.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
DB = os.path.join(ROOT, "index", "classify.sqlite")

# (category, kind, extractor) — every extractor takes an ad dict and returns
# [(label, concept_id)].
SINGLE = [
    ("occupation_field", "taxonomy", "occupation_field"),
    ("occupation_group", "taxonomy", "occupation_group"),
    ("occupation", "taxonomy", "occupation"),
    ("working_hours_type", "terms", "working_hours_type"),
    ("employment_type", "terms", "employment_type"),
    ("duration", "terms", "duration"),
    ("salary_type", "terms", "salary_type"),
    ("region", "geo", None),
    ("municipality", "geo", None),
    ("country", "geo", None),
    ("source_type", "source", None),
]
REQUIREMENTS = [
    ("skills", "skills"), ("languages", "languages"),
    ("work_experiences", "work_experiences"), ("education", "education"),
    ("education_level", "education_level"),
]


def label_of(obj) -> str | None:
    if isinstance(obj, dict):
        return obj.get("label")
    if isinstance(obj, str):
        return obj or None
    return None


def concept_of(obj) -> str | None:
    return obj.get("concept_id") if isinstance(obj, dict) else None


def ad_labels(ad: dict):
    """Yield (category, label, concept_id) for one ad."""
    for cat, _kind, field in SINGLE:
        if field is None:
            addr = ad.get("workplace_address") or {}
            lab = label_of(addr.get(cat))
            yield cat, lab, None
            continue
        obj = ad.get(field)
        yield cat, label_of(obj), concept_of(obj)
    for side in ("must_have", "nice_to_have"):
        block = ad.get(side) or {}
        for field, cat in REQUIREMENTS:
            for item in block.get(field) or []:
                yield f"{side}.{cat}", label_of(item), concept_of(item)


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE category(id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT,
                      labels INT DEFAULT 0, ads INT DEFAULT 0);
CREATE TABLE label(id INTEGER PRIMARY KEY, category_id INT, label TEXT, concept_id TEXT,
                   ads INT DEFAULT 0, UNIQUE(category_id, label));
CREATE TABLE ad(id TEXT PRIMARY KEY, quarter TEXT, publication_date TEXT, headline TEXT,
                employer TEXT, employer_orgnr TEXT, region TEXT, municipality TEXT,
                vacancies INT, webpage_url TEXT);
CREATE TABLE ad_label(label_id INT, ad_id TEXT, weight REAL, PRIMARY KEY(label_id, ad_id))
  WITHOUT ROWID;
CREATE INDEX ad_label_ad ON ad_label(ad_id);
CREATE TABLE concept(kind TEXT, label TEXT, ads INT, mentions INT, pred_sum REAL,
                     mean_prediction REAL, PRIMARY KEY(kind, label));
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("PRAGMA cache_size=-200000")
    return con


def quarters_in(dist: str) -> list[str]:
    return sorted({n[len("ads-"):-len(".jsonl.gz")] for n in os.listdir(dist)
                   if n.startswith("ads-") and n.endswith(".jsonl.gz") and "enrichment" not in n})


def cmd_build(args) -> None:
    dist = args.dist
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    con = connect(DB)
    con.executescript(SCHEMA)
    cur = con.cursor()

    cat_ids: dict[str, int] = {}
    label_ids: dict[tuple[str, str], int] = {}
    label_counts: collections.Counter = collections.Counter()
    cat_ads: collections.Counter = collections.Counter()
    ad_rows = []
    link_rows = []

    def cat_id(name: str, kind: str) -> int:
        if name not in cat_ids:
            cur.execute("INSERT INTO category(name, kind) VALUES (?,?)", (name, kind))
            cat_ids[name] = int(cur.lastrowid or 0)
        return cat_ids[name]

    def label_id(cat: str, kind: str, lab: str, concept: str | None) -> int:
        cid = cat_id(cat, kind)
        key = (cat, lab)
        if key not in label_ids:
            cur.execute("INSERT INTO label(category_id, label, concept_id) VALUES (?,?,?)",
                        (cid, lab, concept))
            label_ids[key] = int(cur.lastrowid or 0)
        return label_ids[key]

    kinds = {c: k for c, k, _ in SINGLE}
    for side in ("must_have", "nice_to_have"):
        for _f, c in REQUIREMENTS:
            kinds[f"{side}.{c}"] = side

    t0 = time.time()
    total = 0
    for q in quarters_in(dist):
        with gzip.open(os.path.join(dist, f"ads-{q}.jsonl.gz"), "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                ad = json.loads(line)
                aid = str(ad.get("id") or "")
                addr = ad.get("workplace_address") or {}
                emp = ad.get("employer") or {}
                ad_rows.append((aid, q, str(ad.get("publication_date") or ""),
                                (ad.get("headline") or "")[:300], emp.get("name"),
                                emp.get("organization_number"), addr.get("region"),
                                addr.get("municipality"), ad.get("number_of_vacancies"),
                                ad.get("webpage_url")))
                seen: set[tuple[str, str]] = set()
                for cat, lab, concept in ad_labels(ad):
                    if not lab:
                        continue
                    lab = str(lab)
                    if (cat, lab) in seen:
                        continue
                    seen.add((cat, lab))
                    lid = label_id(cat, kinds.get(cat, "other"), lab, concept)
                    label_counts[(cat, lab)] += 1
                    cat_ads[cat] += 1
                    link_rows.append((lid, aid, 1.0))
                total += 1
                if len(ad_rows) >= 20000:
                    cur.executemany("INSERT INTO ad VALUES (?,?,?,?,?,?,?,?,?,?)", ad_rows)
                    cur.executemany("INSERT OR IGNORE INTO ad_label VALUES (?,?,?)", link_rows)
                    ad_rows, link_rows = [], []
        print(f"  {q}: {total:,} ads indexed", flush=True)
    if ad_rows:
        cur.executemany("INSERT INTO ad VALUES (?,?,?,?,?,?,?,?,?,?)", ad_rows)
        cur.executemany("INSERT OR IGNORE INTO ad_label VALUES (?,?,?)", link_rows)
    con.commit()

    # per-label totals, then per-category rollups
    print("  aggregating labels…", flush=True)
    cur.execute("CREATE TEMP TABLE lc(label_id INTEGER PRIMARY KEY, n INT)")
    cur.executemany("INSERT INTO lc VALUES (?,?)",
                    [((label_ids[(c, l)], n)) for (c, l), n in label_counts.items()])
    cur.execute("UPDATE label SET ads = COALESCE((SELECT n FROM lc WHERE lc.label_id = label.id), 0)")
    cur.execute("UPDATE category SET ads = COALESCE((SELECT SUM(ads) FROM label WHERE label.category_id = category.id), 0), "
                "labels = COALESCE((SELECT COUNT(*) FROM label WHERE label.category_id = category.id), 0)")
    con.commit()

    # enrichment concepts: aggregates only
    for q in quarters_in(dist):
        ep = os.path.join(dist, f"ads-{q}-enrichment.jsonl.gz")
        if not os.path.exists(ep):
            continue
        agg: dict[tuple[str, str], list] = {}
        with gzip.open(ep, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                doc = json.loads(line)
                for kind, items in (doc.get("enriched_candidates") or {}).items():
                    for it in items:
                        lab = it.get("concept_label") or it.get("term")
                        if not lab:
                            continue
                        slot = agg.setdefault((kind, str(lab)), [0, 0, 0.0])
                        slot[0] += 1                        # ads mentioning it
                        slot[1] += 1                        # mentions
                        slot[2] += float(it.get("prediction") or 0.0)
        cur.executemany(
            "INSERT INTO concept(kind, label, ads, mentions, pred_sum) VALUES (?,?,?,?,?) "
            "ON CONFLICT(kind, label) DO UPDATE SET ads = ads + excluded.ads, "
            "mentions = mentions + excluded.mentions, pred_sum = pred_sum + excluded.pred_sum",
            [(k, lab, v[0], v[1], v[2]) for (k, lab), v in agg.items()])
        con.commit()
        print(f"  {q}: {len(agg):,} enrichment concepts", flush=True)

    # mean demand score is weighted by mentions, not an average of quarter means
    cur.execute("UPDATE concept SET mean_prediction = pred_sum / mentions WHERE mentions > 0")
    cur.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built", time.strftime("%Y-%m-%dT%H:%M:%S")),
        ("ads", str(total)),
        ("quarters", ",".join(quarters_in(dist))),
        ("build_seconds", f"{time.time() - t0:.0f}"),
    ])
    con.commit()
    cur.execute("ANALYZE")
    con.commit()
    size = os.path.getsize(DB)
    con.close()
    print(f"index: {total:,} ads, {len(label_counts):,} labels, "
          f"{len(label_ids):,} label ids -> {os.path.relpath(DB, ROOT)} ({size/1e6:.1f} MB) "
          f"in {time.time() - t0:.0f}s")


def open_db() -> sqlite3.Connection:
    if not os.path.exists(DB):
        raise SystemExit(f"no index at {DB} — run: python3 tools/classify.py build")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def cmd_categories(args) -> None:
    con = open_db()
    rows = con.execute(
        "SELECT c.name, c.kind, c.labels, c.ads FROM category c ORDER BY c.kind, c.ads DESC").fetchall()
    print(f"{'category':32s} {'kind':12s} {'labels':>7s} {'ad-labels':>10s}")
    for r in rows:
        print(f"{r['name']:32s} {r['kind']:12s} {r['labels']:7,d} {r['ads']:10,d}")
    print("\nenrichment concepts (aggregate only):")
    for r in con.execute("SELECT kind, COUNT(*) n, SUM(mentions) m FROM concept GROUP BY kind ORDER BY n DESC"):
        print(f"  {r['kind']:14s} {r['n']:7,d} concepts, {r['m']:,} mentions")
    meta = dict(con.execute("SELECT key, value FROM meta"))
    print(f"\nbuilt {meta.get('built')} over {meta.get('ads')} ads ({meta.get('build_seconds')}s)")


def cmd_values(args) -> None:
    con = open_db()
    rows = con.execute(
        "SELECT l.label, l.concept_id, l.ads FROM label l JOIN category c ON c.id = l.category_id "
        "WHERE c.name = ? ORDER BY l.ads DESC LIMIT ?", (args.category, args.top)).fetchall()
    if not rows:
        raise SystemExit(f"no category {args.category!r} — see: categories")
    for r in rows:
        print(f"{r['ads']:8,d}  {r['label']}  [{r['concept_id'] or '-'}]")


def cmd_ads(args) -> None:
    con = open_db()
    where = ["c.name = ?", "l.label = ?"]
    params: list = [args.category, args.label]
    if args.quarter:
        where.append("a.quarter = ?")
        params.append(args.quarter)
    sql = ("SELECT a.id, a.quarter, a.publication_date, a.employer, a.headline, a.webpage_url "
           "FROM ad_label al JOIN label l ON l.id = al.label_id JOIN category c ON c.id = l.category_id "
           "JOIN ad a ON a.id = al.ad_id WHERE " + " AND ".join(where) +
           " ORDER BY a.publication_date DESC LIMIT ?")
    rows = con.execute(sql, (*params, args.limit)).fetchall()
    total = con.execute("SELECT l.ads FROM label l JOIN category c ON c.id = l.category_id "
                        "WHERE c.name = ? AND l.label = ?", (args.category, args.label)).fetchone()
    print(f"{args.category} = {args.label!r}: {total['ads'] if total else 0:,} ads "
          f"(showing {len(rows)})")
    for r in rows:
        print(f"{r['publication_date'][:10]}  {r['quarter']}  {r['id']}  {r['employer']}  "
              f"{(r['headline'] or '')[:70]}")


def cmd_explain(args) -> None:
    con = open_db()
    ad = con.execute("SELECT * FROM ad WHERE id = ?", (args.id,)).fetchone()
    if not ad:
        raise SystemExit(f"no ad {args.id}")
    print(f"{ad['id']}  {ad['publication_date']}  {ad['employer']}  {ad['headline'][:80]}")
    print(f"{ad['webpage_url']}")
    rows = con.execute(
        "SELECT c.name, c.kind, l.label FROM ad_label al JOIN label l ON l.id = al.label_id "
        "JOIN category c ON c.id = l.category_id WHERE al.ad_id = ? ORDER BY c.kind, c.name, l.label",
        (args.id,)).fetchall()
    last = None
    for r in rows:
        if r["name"] != last:
            print(f"\n  [{r['kind']}] {r['name']}")
            last = r["name"]
        print(f"    - {r['label']}")


def cmd_concepts(args) -> None:
    con = open_db()
    rows = con.execute(
        "SELECT label, ads, mentions, mean_prediction FROM concept WHERE kind = ? AND mean_prediction >= ? "
        "ORDER BY ads DESC LIMIT ?", (args.kind, args.min_pred, args.top)).fetchall()
    for r in rows:
        print(f"{r['ads']:8,d} ads  {r['mentions']:9,d} mentions  pred {r['mean_prediction']:.2f}  {r['label']}")


def main(argv=None) -> int:
    argv = argv or sys.argv
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build index/classify.sqlite from dist/")
    b.add_argument("--dist", default=DIST)
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("categories", help="list indexed categories and their label counts")
    c.set_defaults(func=cmd_categories)

    v = sub.add_parser("values", help="top labels in one category")
    v.add_argument("--category", required=True)
    v.add_argument("--top", type=int, default=25)
    v.set_defaults(func=cmd_values)

    a = sub.add_parser("ads", help="ads carrying category=label")
    a.add_argument("--category", required=True)
    a.add_argument("--label", required=True)
    a.add_argument("--quarter")
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=cmd_ads)

    e = sub.add_parser("explain", help="every indexed label for one ad id")
    e.add_argument("--id", required=True)
    e.set_defaults(func=cmd_explain)

    k = sub.add_parser("concepts", help="top enrichment concepts by ads")
    k.add_argument("--kind", required=True,
                   choices=["occupations", "competencies", "traits", "geos"])
    k.add_argument("--min-pred", type=float, default=0.0)
    k.add_argument("--top", type=int, default=20)
    k.set_defaults(func=cmd_concepts)

    args = p.parse_args(argv[1:])
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
