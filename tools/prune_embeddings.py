#!/usr/bin/env python3
"""Drop embedding rows for ads outside the retention window.

    python3 tools/prune_embeddings.py --dir embeddings/granite-embedding_30m --days 30

Reads publication dates from dist/, keeps only vectors whose ad is still inside
the window, rewrites the affected shards, deletes shards that become empty, and
rebuilds index.npy / index.ids.json / manifest.json. Prints what it dropped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")


def pubdates(dist: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(dist, "ads-*.jsonl.gz"))):
        if "enrichment" in path:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                ad = json.loads(line)
                out[str(ad.get("id"))] = str(ad.get("publication_date") or "")[:10]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--since", help="explicit window start YYYY-MM-DD (overrides --days)")
    ap.add_argument("--dist", default=DIST)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv[1:] if argv else None)

    d = args.dir if os.path.isabs(args.dir) else os.path.join(ROOT, args.dir)
    cutoff = args.since or (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    dates = pubdates(args.dist)
    keep = {aid for aid, day in dates.items() if day >= cutoff}

    kept_rows = dropped = 0
    for shard in sorted(glob.glob(os.path.join(d, "part-*.npy"))):
        ids = json.load(open(shard.replace(".npy", ".ids.json")))
        X = np.load(shard)
        mask = np.array([aid in keep and dates.get(aid, "") >= cutoff for aid in ids])
        kept_rows += int(mask.sum())
        dropped += int((~mask).sum())
        if mask.all():
            continue
        if args.dry_run:
            continue
        if mask.any():
            np.save(shard, X[mask])
            json.dump([a for a, m in zip(ids, mask) if m], open(shard.replace(".npy", ".ids.json"), "w"))
        else:
            for p in (shard, shard.replace(".npy", ".ids.json")):
                os.remove(p)

    if args.dry_run:
        print(f"[dry-run] keep {kept_rows:,} rows, drop {dropped:,} (cutoff {cutoff})")
        return 0

    mats, ids = [], []
    for shard in sorted(glob.glob(os.path.join(d, "part-*.npy"))):
        mats.append(np.load(shard))
        ids.extend(json.load(open(shard.replace(".npy", ".ids.json"))))
    if not mats:
        print(f"window {cutoff}: no vectors left, shards removed")
        return 0
    X = np.vstack(mats).astype(np.float32, copy=False)
    np.save(os.path.join(d, "index.npy"), X)
    json.dump(ids, open(os.path.join(d, "index.ids.json"), "w"))
    man_path = os.path.join(d, "manifest.json")
    man = json.load(open(man_path)) if os.path.exists(man_path) else {}
    man.update({"ads": int(X.shape[0]), "dims": int(X.shape[1]),
                "sha256_matrix": hashlib.sha256(X.tobytes()).hexdigest(),
                "bytes": int(X.nbytes), "window_since": cutoff,
                "pruned_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    json.dump(man, open(man_path, "w"), indent=1)
    print(f"kept {X.shape[0]:,} vectors ({X.nbytes/1e6:.1f} MB), dropped {dropped:,} older than {cutoff}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
