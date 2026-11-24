#!/usr/bin/env python3
"""Merge embed_ads.py shards into one matrix + id list + manifest.

    python3 tools/merge_embeddings.py --dir embeddings/granite-embedding_30m

Writes index.npy (float32, rows in shard order), index.ids.json (same order) and
manifest.json (model, dims, count, sha256 of the matrix payload, source). The
shards stay on disk; the merged file is what you load with numpy.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    args = ap.parse_args(argv[1:] if argv else None)

    d = args.dir if os.path.isabs(args.dir) else os.path.join(ROOT, args.dir)
    shards = sorted(glob.glob(os.path.join(d, "part-*.npy")))
    if not shards:
        raise SystemExit(f"no shards in {d}")
    mats, ids = [], []
    for f in shards:
        mats.append(np.load(f))
        ids.extend(json.load(open(f.replace(".npy", ".ids.json"))))
    X = np.vstack(mats).astype(np.float32, copy=False)
    if X.shape[0] != len(ids):
        raise SystemExit(f"{X.shape[0]} rows vs {len(ids)} ids in {d}")
    if len(set(ids)) != len(ids):
        raise SystemExit(f"duplicate ids in {d}")

    np.save(os.path.join(d, "index.npy"), X)
    json.dump(ids, open(os.path.join(d, "index.ids.json"), "w"))

    state = [json.loads(line) for line in open(os.path.join(d, "state.jsonl")) if line.strip()]
    manifest = {
        "model": state[0].get("model") if state else None,
        "dims": int(X.shape[1]),
        "ads": int(X.shape[0]),
        "shards": len(shards),
        "dtype": "float32",
        "normalised": True,
        "sha256_matrix": hashlib.sha256(X.tobytes()).hexdigest(),
        "bytes": int(X.nbytes),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "rows are L2-normalised; cosine similarity = dot product",
    }
    json.dump(manifest, open(os.path.join(d, "manifest.json"), "w"), indent=1)
    print(f"{manifest['ads']:,} x {manifest['dims']} ({X.nbytes/1e6:.1f} MB) from {len(shards)} shards\n"
          f"sha256 {manifest['sha256_matrix'][:16]}… -> {os.path.relpath(d, ROOT)}/index.npy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
