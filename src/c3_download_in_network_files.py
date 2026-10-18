#!/usr/bin/env python3
"""
Download every unique in-network rate file listed in a CSV.

Reads the column `in_network_file_url` from the input CSV, takes the unique non-empty
URLs, and downloads each file into data/in-network-files/ (or --out-dir).

Built for very large files (hundreds of MB to tens of GB):
  * streamed to disk in chunks, never held in memory
  * written to <name>.part first and renamed only when complete
  * resumes an interrupted download (HTTP Range) instead of starting over
  * skips files that are already fully downloaded
  * retries on network errors
A log of every URL (file name, size, status) is written to <out-dir>/download_log.csv.

Usage:
    python src/c3_download_in_network_files.py --input data/interim/c1_plans.csv
    python src/c3_download_in_network_files.py --input data/interim/c2_name_status.csv --match healthnet
    python src/c3_download_in_network_files.py --input data/interim/c1_plans.csv --dry-run

Requires: pandas, requests
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

URL_COL = "in_network_file_url"
CHUNK = 8 * 1024 * 1024          # 8 MB per write
HEADERS = {"User-Agent": "Mozilla/5.0 (TiC hackathon downloader)"}


def file_name(url):
    """File name from the URL path, e.g. .../2026-09-30_..._in-network.json"""
    name = unquote(Path(urlsplit(url).path).name)
    return name or "unnamed_file"


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def remote_size(session, url, timeout):
    """Content-Length from a HEAD request, or None if the server doesn't say."""
    try:
        r = session.head(url, allow_redirects=True, timeout=timeout)
        if r.ok and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
    except requests.RequestException:
        pass
    return None


def download(url, out_dir, args):
    session = requests.Session()
    session.headers.update(HEADERS)
    dest = out_dir / file_name(url)
    part = dest.with_name(dest.name + ".part")
    size = remote_size(session, url, args.timeout)

    # already complete?
    if dest.exists() and (size is None or dest.stat().st_size == size):
        return {"url": url, "file": dest.name, "bytes": dest.stat().st_size, "status": "skipped (already downloaded)"}

    for attempt in range(1, args.retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=args.timeout) as r:
                if r.status_code == 416:                       # .part already holds the whole file
                    part.replace(dest)
                    return {"url": url, "file": dest.name, "bytes": dest.stat().st_size, "status": "downloaded"}
                if r.status_code not in (200, 206):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                if r.status_code == 200 and have:              # server ignored Range: start over
                    have = 0
                mode = "ab" if r.status_code == 206 else "wb"
                done, last = have, time.time()
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        f.write(chunk)
                        done += len(chunk)
                        if time.time() - last > 15:            # progress every 15 s
                            total = f" / {human(size)}" if size else ""
                            print(f"   ... {dest.name}: {human(done)}{total}", flush=True)
                            last = time.time()
            if size is not None and part.stat().st_size != size:
                raise IOError(f"incomplete: {part.stat().st_size} of {size} bytes")
            part.replace(dest)
            return {"url": url, "file": dest.name, "bytes": dest.stat().st_size, "status": "downloaded"}
        except (requests.RequestException, IOError) as e:
            print(f"   retry {attempt}/{args.retries} {dest.name}: {e}", flush=True)
            time.sleep(min(60, 5 * attempt))
    return {"url": url, "file": dest.name, "bytes": part.stat().st_size if part.exists() else 0,
            "status": "FAILED (partial .part kept; re-run to resume)"}


def main():
    ap = argparse.ArgumentParser(description="Download unique in-network rate files listed in a CSV.")
    ap.add_argument("--input", required=True, help="CSV with an in_network_file_url column")
    ap.add_argument("--out-dir", default=str(PATHS["in_network_files"]))
    ap.add_argument("--match", default="", help="only URLs containing this text (case-insensitive)")
    ap.add_argument("--workers", type=int, default=2, help="files downloaded at the same time")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=120, help="seconds without data before retrying")
    ap.add_argument("--dry-run", action="store_true", help="list the files and sizes, download nothing")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False, usecols=lambda c: c == URL_COL)
    if URL_COL not in df.columns:
        sys.exit(f"column '{URL_COL}' not found in {args.input}")
    urls = sorted({u.strip() for u in df[URL_COL] if u.strip()})
    if args.match:
        urls = [u for u in urls if args.match.lower() in u.lower()]
    print(f"read {args.input}: {len(df)} rows -> {len(urls)} unique in-network file URLs")
    if not urls:
        return

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        s = requests.Session()
        s.headers.update(HEADERS)
        total = 0
        for u in urls:
            n = remote_size(s, u, args.timeout)
            total += n or 0
            print(f"  {file_name(u)}  {human(n) if n else 'size unknown'}")
        print(f"total (known sizes): {human(total)}")
        return

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(download, u, out_dir, args): u for u in urls}
        for i, f in enumerate(as_completed(futs), 1):
            res = f.result()
            results.append(res)
            print(f"[{i}/{len(urls)}] {res['status']:<28} {res['file']}  ({human(res['bytes'])})", flush=True)

    log = out_dir / "download_log.csv"
    with open(log, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["url", "file", "bytes", "status"])
        w.writeheader()
        w.writerows(sorted(results, key=lambda r: r["file"]))

    failed = [r for r in results if r["status"].startswith("FAILED")]
    print(f"\ndone: {len(results) - len(failed)} ok, {len(failed)} failed -> {out_dir}")
    print(f"log: {log}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()