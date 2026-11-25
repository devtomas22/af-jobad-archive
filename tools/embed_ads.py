#!/usr/bin/env python3
"""Embed a slice of the archive with a local Ollama embedding model.

    python3 tools/embed_ads.py --model granite-embedding:278m \
        --category occupation_field --label "Data/IT"

Writes one .npy matrix + ids file per shard under embeddings/<model-slug>/, so a
killed run resumes at the next shard and never re-embeds what is already done.
No third-party deps beyond numpy; the vectors are float32 and the shard size is
capped (default 5000 ads ≈ 15 MB at 768 dims).

Scope: `--category/--label` selects via index/classify.sqlite (build it first with
tools/classify.py build); without them every ad in dist/ is embedded.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
INDEX = os.path.join(ROOT, "index", "classify.sqlite")
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def text_for(ad: dict, max_chars: int) -> str:
    d = ad.get("description") or {}
    txt = " ".join(x for x in (ad.get("headline"), d.get("text"), d.get("needs"),
                               d.get("requirements"), d.get("conditions"),
                               d.get("company_information")) if x)
    return txt[:max_chars] if max_chars else txt


def select_ids(category: str | None, label: str | None) -> set[str] | None:
    if not category:
        return None
    if not os.path.exists(INDEX):
        raise SystemExit(f"{category} filter needs {INDEX} — run tools/classify.py build")
    con = sqlite3.connect(f"file:{INDEX}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT al.ad_id FROM ad_label al JOIN label l ON l.id = al.label_id "
        "JOIN category c ON c.id = l.category_id WHERE c.name = ? AND l.label = ?",
        (category, label)).fetchall()
    if not rows:
        raise SystemExit(f"no ads for {category}={label!r}")
    return {r[0] for r in rows}


def embed(model: str, docs: list[str], ctx: int, retries: int = 4) -> list[list[float]]:
    body = json.dumps({"model": model, "input": docs, "options": {"num_ctx": ctx}}).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{OLLAMA}/api/embed", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=1800) as r:
                return json.load(r)["embeddings"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise SystemExit(f"embedding failed after {retries} tries: {last}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="granite-embedding:278m")
    p.add_argument("--category")
    p.add_argument("--label")
    p.add_argument("--dist", default=DIST)
    p.add_argument("--out", default=os.path.join(ROOT, "embeddings"))
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--shard", type=int, default=5000, help="ads per .npy shard")
    p.add_argument("--max-chars", type=int, default=0, help="0 = whole text")
    p.add_argument("--ctx", type=int, default=2048)
    p.add_argument("--limit", type=int, default=0, help="0 = all selected ads")
    p.add_argument("--since", help="only ads published on/after this date (YYYY-MM-DD)")
    args = p.parse_args(argv[1:] if argv else None)

    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", args.model)
    out_dir = os.path.join(args.out, slug)
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(out_dir, "state.jsonl")   # one line per finished shard
    done: set[str] = set()
    for name in os.listdir(out_dir):
        if name.endswith(".ids.json"):
            done |= set(json.load(open(os.path.join(out_dir, name))))
    shard_n = len([n for n in os.listdir(out_dir) if n.endswith(".npy")])
    ids_filter = select_ids(args.category, args.label)
    wanted = len(ids_filter) if ids_filter is not None else None
    print(f"{args.model}: {wanted if wanted is not None else 'all'} ads selected, "
          f"{len(done):,} already embedded, {shard_n} shards in {os.path.relpath(out_dir, ROOT)}",
          flush=True)

    buf_ids: list[str] = []
    buf_txt: list[str] = []
    dims = 0
    t0 = time.time()
    done_now = 0
    shard = shard_n

    def flush(force=False):
        nonlocal buf_ids, buf_txt, shard, dims, done_now
        while buf_ids and (len(buf_ids) >= args.shard or (force and buf_ids)):
            take = min(args.shard, len(buf_ids))
            chunk_ids, chunk_txt = buf_ids[:take], buf_txt[:take]
            vecs: list[list[float]] = []
            i = 0
            while i < len(chunk_txt):
                part = chunk_txt[i:i + args.batch]
                vecs.extend(embed(args.model, part, args.ctx))
                i += len(part)
                rate = (i + done_now) / max(time.time() - t0, 1e-9)
                if i % (args.batch * 8) == 0:
                    print(f"  {done_now + i:,}/{wanted or '?'}  {rate:.2f} docs/s", flush=True)
            mat = np.asarray(vecs, dtype=np.float32)
            dims = mat.shape[1]
            np.save(os.path.join(out_dir, f"part-{shard:05d}.npy"), mat)
            json.dump(chunk_ids, open(os.path.join(out_dir, f"part-{shard:05d}.ids.json"), "w"))
            with open(state_path, "a") as f:
                f.write(json.dumps({"shard": shard, "ads": len(chunk_ids), "dims": dims,
                                    "model": args.model, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
            shard += 1
            done_now += len(chunk_ids)
            buf_ids, buf_txt = buf_ids[take:], buf_txt[take:]

    for q in sorted({n[len("ads-"):-len(".jsonl.gz")] for n in os.listdir(args.dist)
                     if n.startswith("ads-") and n.endswith(".jsonl.gz") and "enrichment" not in n}):
        with gzip.open(os.path.join(args.dist, f"ads-{q}.jsonl.gz"), "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                ad = json.loads(line)
                aid = str(ad.get("id"))
                if ids_filter is not None and aid not in ids_filter:
                    continue
                if args.since and str(ad.get("publication_date") or "")[:10] < args.since:
                    continue
                if aid in done:
                    continue
                if args.limit and done_now + len(buf_ids) >= args.limit:
                    break
                buf_ids.append(aid)
                buf_txt.append(text_for(ad, args.max_chars))
                if len(buf_ids) >= args.shard:
                    flush()
                    if args.limit and done_now >= args.limit:
                        break
            if args.limit and done_now >= args.limit:
                break
    flush(force=True)

    total = done_now + len(done)
    elapsed = time.time() - t0
    print(f"done: {total:,} ads embedded, {dims or '?'} dims, {shard} shards, "
          f"{elapsed/60:.1f} min this run ({done_now/max(elapsed,1e-9):.2f} docs/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
