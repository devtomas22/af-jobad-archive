#!/usr/bin/env python3
"""af-jobad-archive - rolling 12-month archive of Swedish job ads.

Downloads job ads from Arbetsformedlingen / JobTech Development
(historical ads API), enriches them with the JobAd Enrichments API,
prunes everything older than the rolling window and packs one gzip
asset per calendar quarter for GitHub Releases.

Standard library only. Python >= 3.9.

Typical run:

    ./af_jobs_archive.py plan                  # what would be fetched
    ./af_jobs_archive.py download --all        # fetch the 4 quarters
    ./af_jobs_archive.py pack --all            # dedupe, sort, gzip
    ./af_jobs_archive.py enrich --all          # JobAd Enrichments sidecar
    ./af_jobs_archive.py prune --all           # drop > 12 months old
    ./af_jobs_archive.py verify --all
    ./af_jobs_archive.py release --quarter 2026Q1
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"

HIST_SEARCH = "https://historical.api.jobtechdev.se/search"
ENRICH_URL = "https://jobad-enrichments-api.jobtechdev.se/enrichtextdocuments"
USER_AGENT = f"af-jobad-archive/{VERSION} (+https://github.com/devtomas22/af-jobad-archive)"

# Hard API limits (verified live 2026-11-22):
#   limit <= 100, offset <= 2000  =>  max 2100 ads reachable per query window
PAGE_LIMIT = 100
MAX_OFFSET = 2000
MAX_WINDOW_ADS = MAX_OFFSET + PAGE_LIMIT

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(ROOT, "data", "raw")
DIST_DIR = os.path.join(ROOT, "dist")
STATE_DIR = os.path.join(ROOT, "data", "state")

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------

def parse_date(s: str) -> dt.datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit(f"unparseable date: {s!r} (use YYYY-MM-DD)")


def iso(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def quarter_of(d: dt.datetime) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def quarter_bounds(q: str) -> tuple[dt.datetime, dt.datetime]:
    year, qn = int(q[:4]), int(q[-1])
    if qn not in (1, 2, 3, 4):
        raise SystemExit(f"bad quarter: {q}")
    start = dt.datetime(year, 3 * (qn - 1) + 1, 1)
    end = dt.datetime(year + (1 if qn == 4 else 0), 1 if qn == 4 else 3 * qn + 1, 1)
    return start, end


def default_quarters(end: dt.datetime, count: int = 4) -> list[str]:
    """The `count` most recent *complete* calendar quarters before `end`."""
    cur_start = dt.datetime(end.year, 3 * ((end.month - 1) // 3) + 1, 1)
    qs = []
    d = cur_start
    for _ in range(count):
        d = dt.datetime(d.year - (1 if d.month == 1 else 0), 12 if d.month == 1 else d.month - 3, 1)
        qs.append(quarter_of(d))
    return list(reversed(qs))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_json(url: str, params: dict | None = None, body: dict | None = None,
              retries: int = 6, timeout: int = 120) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 404, 422):  # not retryable
                raise
            time.sleep(min(30, 2 ** attempt) + 0.2 * attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(min(30, 2 ** attempt) + 0.2 * attempt)
    raise SystemExit(f"giving up on {url}: {last}")


def window_total(start: dt.datetime, end: dt.datetime) -> int:
    d = http_json(HIST_SEARCH, {"limit": 0,
                                "published-after": iso(start),
                                "published-before": iso(end)})
    return int(d["total"]["value"])


def _split(start: dt.datetime, end: dt.datetime, total: int, depth: int = 0):
    """Yield API-safe (start, end) windows: each reachable in <= 2100 ads."""
    if total <= MAX_WINDOW_ADS or depth > 12:
        yield start, end
        return
    mid = start + (end - start) / 2
    mid = mid.replace(microsecond=0)
    if mid <= start or mid >= end:
        yield start, end  # cannot split further; caller will page what it can
        return
    for a, b in ((start, mid), (mid, end)):
        yield from _split(a, b, window_total(a, b), depth + 1)


def iter_windows(start: dt.datetime, end: dt.datetime):
    """Adaptive 6-hour windows over [start, end)."""
    step = dt.timedelta(hours=6)
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        total = window_total(cur, nxt)
        yield from _split(cur, nxt, total)
        cur = nxt


def fetch_window(start: dt.datetime, end: dt.datetime) -> list[dict]:
    ads: dict[str, dict] = {}
    offset = 0
    while True:
        d = http_json(HIST_SEARCH, {
            "limit": PAGE_LIMIT,
            "offset": offset,
            "published-after": iso(start),
            "published-before": iso(end),
        })
        hits = d.get("hits") or []
        for h in hits:
            key = str(h.get("id") or h.get("original_id"))
            ads[key] = h
        total = int(d["total"]["value"])
        offset += PAGE_LIMIT
        if offset > min(total, MAX_OFFSET) or not hits:
            break
    return list(ads.values())


# --------------------------------------------------------------------------
# state / files
# --------------------------------------------------------------------------

def paths_for(quarter: str) -> dict:
    return {
        "raw": os.path.join(RAW_DIR, f"ads-{quarter}.jsonl.gz"),
        "packed": os.path.join(DIST_DIR, f"ads-{quarter}.jsonl.gz"),
        "enriched": os.path.join(DIST_DIR, f"ads-{quarter}-enrichment.jsonl.gz"),
        "manifest": os.path.join(DIST_DIR, f"manifest-{quarter}.json"),
        "state": os.path.join(STATE_DIR, f"{quarter}.json"),
    }


def load_state(quarter: str) -> dict:
    p = paths_for(quarter)["state"]
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"quarter": quarter, "windows": {}, "updated": None}


def save_state(quarter: str, state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    state["updated"] = dt.datetime.now().replace(microsecond=0).isoformat()
    tmp = paths_for(quarter)["state"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    os.replace(tmp, paths_for(quarter)["state"])


def append_ads(path: str, ads: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8", compresslevel=6) as f:
        for a in ads:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def read_jsonl(path: str):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def month_shift(d: dt.datetime, months: int) -> dt.datetime:
    y, m = divmod(d.year * 12 + (d.month - 1) + months, 12)
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return dt.datetime(y, m + 1, day, d.hour, d.minute, d.second)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_plan(args) -> None:
    end = parse_date(args.end)
    cutoff = month_shift(end, -12)
    quarters = args.quarters or default_quarters(end)
    log(f"window: {iso(cutoff)} .. {iso(end)}  (rolling 12 months)")
    grand = 0
    for q in quarters:
        qs, qe = quarter_bounds(q)
        a, b = max(qs, cutoff), min(qe, end)
        if a >= b:
            log(f"{q}: outside window")
            continue
        n = window_total(a, b)
        grand += n
        log(f"{q}: {iso(a)} .. {iso(b)}  {n:>7} ads  (~{n * 4310 / 1e6:.1f} MB raw, ~{n * 697 / 1e6:.0f} MB gz)")
    log(f"total {grand} ads")


def cmd_download(args) -> None:
    end = parse_date(args.end)
    cutoff = month_shift(end, -12)
    quarters = args.quarters or default_quarters(end)
    workers = max(1, args.workers)
    for q in quarters:
        qs, qe = quarter_bounds(q)
        a, b = max(qs, cutoff), min(qe, end)
        if a >= b:
            log(f"{q}: outside the rolling window, skipped")
            continue
        state = load_state(q)
        todo = []
        for wa, wb in iter_windows(a, b):
            key = f"{iso(wa)}|{iso(wb)}"
            if key not in state["windows"]:
                todo.append((key, wa, wb))
        log(f"{q}: {len(todo)} windows to fetch ({len(state['windows'])} already done)")
        if not todo:
            continue
        path = paths_for(q)["raw"]
        done = 0
        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_window, wa, wb): (key, wa, wb) for key, wa, wb in todo}
            for fut in concurrent.futures.as_completed(futs):
                key, wa, wb = futs[fut]
                try:
                    ads = fut.result()
                except SystemExit:
                    raise
                except Exception as e:  # keep going, window retried next run
                    log(f"  ! {key} failed: {e}")
                    continue
                append_ads(path, ads)
                state["windows"][key] = len(ads)
                done += 1
                if done % 25 == 0 or done == len(todo):
                    save_state(q, state)
                    rate = done / max(1e-6, time.time() - t0)
                    log(f"  {q}: {done}/{len(todo)} windows, {sum(state['windows'].values())} ads, {rate:.1f} win/s")
        save_state(q, state)
        log(f"{q}: downloaded {sum(state['windows'].values())} ads -> {os.path.relpath(path, ROOT)} "
            f"({os.path.getsize(path) / 1e6:.1f} MB gz)" if os.path.exists(path) else f"{q}: nothing fetched")


def cmd_pack(args) -> None:
    end = parse_date(args.end)
    cutoff = month_shift(end, -12)
    quarters = args.quarters or default_quarters(end)
    os.makedirs(DIST_DIR, exist_ok=True)
    for q in quarters:
        raw = paths_for(q)["raw"]
        if not os.path.exists(raw):
            log(f"{q}: no raw data, skipped")
            continue
        ads: dict[str, dict] = {}
        dropped_old = 0
        for ad in read_jsonl(raw):
            pub = str(ad.get("publication_date") or "")
            try:
                when = parse_date(pub)
            except SystemExit:
                when = None
            if when is not None and when < cutoff:
                dropped_old += 1
                continue
            key = str(ad.get("id") or ad.get("original_id"))
            prev = ads.get(key)
            if prev is None or str(ad.get("publication_date")) > str(prev.get("publication_date")):
                ads[key] = ad
        ordered = sorted(ads.values(), key=lambda a: (str(a.get("publication_date")), str(a.get("id"))))
        out = paths_for(q)["packed"]
        tmp = out + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
            for ad in ordered:
                f.write(json.dumps(ad, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, out)
        dates = [str(a.get("publication_date")) for a in ordered if a.get("publication_date")]
        manifest = {
            "quarter": q,
            "generated": dt.datetime.now().replace(microsecond=0).isoformat(),
            "ads": len(ordered),
            "dropped_older_than_window": dropped_old,
            "publication_date_from": min(dates) if dates else None,
            "publication_date_to": max(dates) if dates else None,
            "bytes_gz": os.path.getsize(out),
            "sha256": sha256(out),
        }
        with open(paths_for(q)["manifest"], "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, ensure_ascii=False)
        log(f"{q}: packed {len(ordered)} ads -> {os.path.relpath(out, ROOT)} "
            f"({manifest['bytes_gz'] / 1e6:.1f} MB)")


def cmd_enrich(args) -> None:
    quarters = args.quarters or default_quarters(parse_date(args.end))
    workers = max(1, args.workers)
    for q in quarters:
        packed = paths_for(q)["packed"]
        if not os.path.exists(packed):
            log(f"{q}: nothing packed, skipped")
            continue
        out = paths_for(q)["enriched"]
        done_ids: set[str] = set()
        if os.path.exists(out) and not args.force:
            for item in read_jsonl(out):
                if item.get("doc_id"):
                    done_ids.add(str(item["doc_id"]))
        tmp = out + ".tmp"
        if os.path.exists(out) and not args.force:
            shutil.copyfile(out, tmp)
        else:
            open(tmp, "wb").close()

        batches, batch = [], []
        for ad in read_jsonl(packed):
            if str(ad.get("id")) in done_ids:
                continue
            d = ad.get("description") or {}
            text = " ".join(x for x in (d.get("text"), d.get("needs"), d.get("requirements"),
                                        d.get("conditions"), d.get("company_information")) if x)
            if not (text or ad.get("headline")):
                continue
            batch.append({"doc_id": str(ad.get("id")), "doc_headline": ad.get("headline") or "",
                          "doc_text": text[:20000]})
            if len(batch) >= 100:
                batches.append(batch)
                batch = []
        if batch:
            batches.append(batch)
        log(f"{q}: {len(batches)} enrichment batches ({len(done_ids)} docs already done)")
        if not batches:
            if os.path.exists(tmp):
                os.replace(tmp, out)
            continue

        lock = threading.Lock()
        t0, counter = time.time(), [0]
        fh = gzip.open(tmp, "at", encoding="utf-8", compresslevel=9)

        def work(docs):
            body = {"include_terms_info": True, "include_sentences": False, "documents_input": docs}
            res = http_json(ENRICH_URL, body=body, timeout=300)
            items = res if isinstance(res, list) else res.get("result", [])
            with lock:
                for item in items:
                    fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                counter[0] += len(items)
                if counter[0] % 5000 < 100:
                    log(f"  {q}: {counter[0]} docs, {(time.time() - t0) and counter[0] / (time.time() - t0):.1f} docs/s")

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, batches))
        fh.close()
        os.replace(tmp, out)
        n_out = sum(1 for _ in read_jsonl(out))
        man = paths_for(q)["manifest"]
        if os.path.exists(man):
            with open(man, encoding="utf-8") as f:
                m = json.load(f)
            m["enrichment"] = {"docs": n_out, "bytes_gz": os.path.getsize(out), "sha256": sha256(out)}
            with open(man, "w", encoding="utf-8") as f:
                json.dump(m, f, indent=1, ensure_ascii=False)
        log(f"{q}: enriched {n_out} docs in {time.time() - t0:.0f}s -> {os.path.relpath(out, ROOT)} "
            f"({os.path.getsize(out) / 1e6:.1f} MB)")


def cmd_prune(args) -> None:
    end = parse_date(args.end)
    cutoff = month_shift(end, -12)
    removed = []
    for d in (RAW_DIR, DIST_DIR):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            q = None
            for token in name.replace(".jsonl.gz", "").split("-"):
                if len(token) == 6 and token[4] == "Q" and token[:4].isdigit():
                    q = token
            if not q:
                continue
            qs, qe = quarter_bounds(q)
            if qe <= cutoff:
                os.remove(os.path.join(d, name))
                removed.append(os.path.relpath(os.path.join(d, name), ROOT))
    for p in removed:
        log(f"pruned {p}")
    if not removed:
        log(f"nothing outside the window (cutoff {iso(cutoff)})")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_verify(args) -> None:
    quarters = args.quarters or default_quarters(parse_date(args.end))
    ok = True
    for q in quarters:
        man_path = paths_for(q)["manifest"]
        if not os.path.exists(man_path):
            log(f"{q}: no manifest")
            ok = False
            continue
        with open(man_path, encoding="utf-8") as f:
            m = json.load(f)
        p = paths_for(q)["packed"]
        if not os.path.exists(p):
            log(f"{q}: missing {p}")
            ok = False
            continue
        got = sha256(p)
        size_ok = os.path.getsize(p) == m["bytes_gz"]
        log(f"{q}: {m['ads']} ads, sha256 {'OK' if got == m['sha256'] else 'MISMATCH'}, size {'OK' if size_ok else 'MISMATCH'}")
        ok = ok and got == m["sha256"] and size_ok
    sys.exit(0 if ok else 1)


def cmd_measure(args) -> None:
    """Sample live ads and project storage for a 12-month window."""
    end = parse_date(args.end)
    start = month_shift(end, -12)
    total = window_total(start, end)
    step = dt.timedelta(days=7)
    samples, sizes = [], []
    cur = start
    t0 = time.time()
    while len(sizes) < args.sample and cur < end:
        d = http_json(HIST_SEARCH, {"limit": min(100, args.sample - len(sizes)), "offset": args.offset,
                                    "published-after": iso(cur),
                                    "published-before": iso(min(cur + step, end))})
        for h in d.get("hits") or []:
            sizes.append(len(json.dumps(h, ensure_ascii=False, separators=(",", ":")).encode()))
        cur += step
    raw_page = json.dumps(samples, ensure_ascii=False) if samples else ""
    mean = sum(sizes) / max(1, len(sizes))
    med = sorted(sizes)[len(sizes) // 2]
    gz_ratio = 0.1617  # measured: 69717/431224 on a 100-ad page, compresslevel 9
    out = {
        "window": [iso(start), iso(end)],
        "ads": total,
        "sample_ads": len(sizes),
        "bytes_per_ad_mean_raw": round(mean),
        "bytes_per_ad_median_raw": med,
        "raw_jsonl_bytes": round(total * mean),
        "gzip_bytes": round(total * mean * gz_ratio),
        "enrichment_bytes_per_ad_raw": 3169,
        "enrichment_gzip_bytes": round(total * 3169 * gz_ratio),
        "measured_in_s": round(time.time() - t0, 1),
    }
    print(json.dumps(out, indent=1))


def cmd_release(args) -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN not set")
    repo = args.repo or "devtomas22/af-jobad-archive"
    q = args.quarter
    tag = q
    assets = [p for p in (paths_for(q)["packed"], paths_for(q)["enriched"],
                          paths_for(q)["manifest"]) if os.path.exists(p)]
    if not assets:
        raise SystemExit(f"nothing to release for {q}")

    def api(method: str, path: str, payload=None, url=None):
        url = url or f"https://api.github.com/repos/{repo}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read()
            return json.loads(body) if body else {}

    manifest = {}
    if os.path.exists(paths_for(q)["manifest"]):
        with open(paths_for(q)["manifest"], encoding="utf-8") as f:
            manifest = json.load(f)
    try:
        rel = api("GET", f"/releases/tags/{tag}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        body = (f"Rolling 12-month archive, quarter **{q}**\n\n"
                f"- ads: **{manifest.get('ads', '?')}**\n"
                f"- published: {manifest.get('publication_date_from')} .. {manifest.get('publication_date_to')}\n"
                f"- assets: gzip JSONL (one ad per line) + JobAd Enrichments sidecar + manifest\n\n"
                f"Source: JobTech Development historical ads API + JobAd Enrichments API.\n"
                f"Generated by af_jobs_archive.py {VERSION}.")
        rel = api("POST", "/releases", {"tag_name": tag, "name": f"Job ads {q}",
                                        "body": body, "draft": False, "prerelease": False})
    for path in assets:
        name = os.path.basename(path)
        size = os.path.getsize(path)
        log(f"uploading {name} ({size / 1e6:.1f} MB)")
        with open(path, "rb") as f:
            req = urllib.request.Request(
                f"https://uploads.github.com/repos/{repo}/releases/{rel['id']}/assets?name={urllib.parse.quote(name)}",
                data=f.read(), method="POST",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream",
                         "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=900) as r:
                a = json.loads(r.read())
                log(f"  -> {a['name']} {a['size']} bytes state={a.get('state')}")
    log(f"release {q}: {rel['html_url']}")


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Rolling 12-month archive of Swedish job ads")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, default_all=True):
        sp.add_argument("--end", default=dt.date.today().isoformat(),
                        help="end of the rolling window (default: today)")
        sp.add_argument("--quarters", nargs="*", help="explicit quarters, e.g. 2025Q4 2026Q1")
        sp.add_argument("--all", action="store_true", help="all four quarters of the window")
        return sp

    sp = common(sub.add_parser("plan", help="show window sizes and projected storage"))
    sp.set_defaults(func=cmd_plan)

    sp = common(sub.add_parser("download", help="fetch ads into data/raw"))
    sp.add_argument("--workers", type=int, default=6)
    sp.set_defaults(func=cmd_download)

    sp = common(sub.add_parser("pack", help="dedupe, sort and gzip into dist/"))
    sp.set_defaults(func=cmd_pack)

    sp = common(sub.add_parser("enrich", help="run JobAd Enrichments over packed ads"))
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--workers", type=int, default=6)
    sp.set_defaults(func=cmd_enrich)

    sp = common(sub.add_parser("prune", help="delete quarters outside the window"))
    sp.set_defaults(func=cmd_prune)

    sp = common(sub.add_parser("verify", help="check manifests and checksums"))
    sp.set_defaults(func=cmd_verify)

    sp = common(sub.add_parser("measure", help="sample ads and project storage"))
    sp.add_argument("--sample", type=int, default=300)
    sp.add_argument("--offset", type=int, default=0)
    sp.set_defaults(func=cmd_measure)

    sp = sub.add_parser("release", help="publish one quarter to GitHub Releases")
    sp.add_argument("--quarter", required=True)
    sp.add_argument("--repo", default=os.environ.get("RELEASE_REPO", "devtomas22/af-jobad-archive"))
    sp.set_defaults(func=cmd_release, end=dt.date.today().isoformat(), quarters=None, all=False)

    args = p.parse_args(argv)
    if args.quarters is None:
        args.quarters = default_quarters(parse_date(args.end)) if args.cmd != "release" else None
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
