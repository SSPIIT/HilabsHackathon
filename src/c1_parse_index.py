#!/usr/bin/env python3
"""
C1 - Parse TiC index files into one raw table.

Reads every index file (.json or .json.gz) in the input folder(s), streams each
one with ijson, and writes one row per (plan, in-network rate file).
No cleaning or deduplication is done here; that happens in a later step.

Usage:
    python src/c1_parse_index.py --input data/raw/index_files --out data/interim/c1_index_raw.csv
    python src/c1_parse_index.py --input a.json b.json.gz --out out.csv --workers 4

Requires: ijson
"""

import argparse
import csv
import gzip
import os
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import ijson

COLUMNS = [
    "index_file",
    "brand",
    "reporting_entity_name",
    "reporting_entity_type",
    "index_last_updated_on",
    "block_id",
    "plan_name",
    "plan_id",
    "plan_id_type",
    "plan_market_type",
    "issuer_name",
    "plan_sponsor_name",
    "in_network_file_description",
    "in_network_file_url",
    "allowed_amount_file_url",
]

HEADER_COLUMNS = ("brand", "reporting_entity_name", "reporting_entity_type", "index_last_updated_on")
RAW_COLUMNS = [c for c in COLUMNS if c not in HEADER_COLUMNS]   # known while streaming blocks

HEADER_KEYS = ("reporting_entity_name", "reporting_entity_type", "last_updated_on")

BRAND_KEYWORDS = {
    "ambetter": "Ambetter",
    "healthnet": "Health Net",
    "health net": "Health Net",
    "health-net": "Health Net",
    "health_net": "Health Net",
    "qualchoice": "QualChoice",
    "fidelis": "Fidelis",
}


def detect_brand(*texts):
    blob = " ".join(t for t in texts if t).lower()
    for kw, brand in BRAND_KEYWORDS.items():
        if kw in blob:
            return brand
    return ""


def open_maybe_gz(path):
    return gzip.open(path, "rb") if path.name.endswith(".gz") else open(path, "rb")


def block_rows(block, block_id, index_file):
    """Turn one reporting_structure block into plan x in-network-file rows."""
    plans = block.get("reporting_plans") or []
    files = block.get("in_network_files") or [{}]       # keep plans that list no file
    aa = block.get("allowed_amount_file")
    aa_url = aa.get("location", "") if isinstance(aa, dict) else ""

    for p in plans:
        for f in files:
            yield {
                "index_file": index_file,
                "block_id": block_id,
                "plan_name": p.get("plan_name", ""),
                "plan_id": p.get("plan_id", ""),
                "plan_id_type": p.get("plan_id_type", ""),
                "plan_market_type": p.get("plan_market_type", ""),
                "issuer_name": p.get("issuer_name", ""),
                "plan_sponsor_name": p.get("plan_sponsor_name", ""),
                "in_network_file_description": f.get("description", ""),
                "in_network_file_url": f.get("location", ""),
                "allowed_amount_file_url": aa_url,
            }


def parse_one(path, part_dir):
    """
    Stream one index file and write its rows to a part CSV.
    Only one block is held in memory at a time. Header fields can appear
    before or after reporting_structure, so they are filled in at the end.
    """
    path = Path(path)
    print(f"parsing  {path.name} ...", flush=True)
    header = {}
    n_blocks = n_rows = 0
    tmp_part = Path(part_dir) / f"{path.name}.rows.tmp"

    with open_maybe_gz(path) as fin, open(tmp_part, "w", newline="", encoding="utf-8") as fout:
        w = csv.DictWriter(fout, fieldnames=RAW_COLUMNS)
        builder = None
        for prefix, event, value in ijson.parse(fin):
            if prefix in HEADER_KEYS and event in ("string", "number"):
                header[prefix] = str(value)
                continue
            if prefix == "reporting_structure.item" and event == "start_map":
                builder = ijson.ObjectBuilder()
            if builder is not None:
                builder.event(event, value)
                if prefix == "reporting_structure.item" and event == "end_map":
                    n_blocks += 1
                    for row in block_rows(builder.value, n_blocks, path.name):
                        w.writerow(row)
                        n_rows += 1
                    builder = None

    # second pass over the small part file to add header-level columns
    part = Path(part_dir) / f"{path.name}.csv"
    with open(tmp_part, newline="", encoding="utf-8") as fin, \
         open(part, "w", newline="", encoding="utf-8") as fout:
        w = csv.DictWriter(fout, fieldnames=COLUMNS)
        for row in csv.DictReader(fin, fieldnames=RAW_COLUMNS):
            row["reporting_entity_name"] = header.get("reporting_entity_name", "")
            row["reporting_entity_type"] = header.get("reporting_entity_type", "")
            row["index_last_updated_on"] = header.get("last_updated_on", "")
            row["brand"] = detect_brand(path.name, row["issuer_name"], row["in_network_file_url"])
            w.writerow(row)
    tmp_part.unlink()
    return path.name, n_blocks, n_rows, str(part)


def collect_inputs(inputs):
    files = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.name.endswith((".json", ".json.gz")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"warning: {item} not found, skipped", file=sys.stderr)
    return files


def main():
    ap = argparse.ArgumentParser(description="C1: parse TiC index files into one raw table.")
    ap.add_argument("--input", nargs="+", required=True, help="index files and/or folders")
    ap.add_argument("--out", default="data/interim/c1_index_raw.csv", help="output CSV path")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                    help="parallel processes (one index file per process)")
    args = ap.parse_args()

    files = collect_inputs(args.input)
    if not files:
        sys.exit("no index files found")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as part_dir:
        results = []
        total_rows = 0
        with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(files)))) as ex:
            futures = {ex.submit(parse_one, f, part_dir): f for f in files}
            for fut in as_completed(futures):
                try:
                    name, n_blocks, n_rows, part = fut.result()
                except Exception as e:                       # one bad file shouldn't stop the run
                    print(f"FAILED {futures[fut].name}: {e}", file=sys.stderr)
                    continue
                results.append((name, part))
                total_rows += n_rows
                print(f"done     {name}: {n_blocks} blocks, +{n_rows} rows "
                      f"(running total {total_rows})", flush=True)

        # concatenate parts in input order into one table
        with open(out, "w", newline="", encoding="utf-8") as fout:
            fout.write(",".join(COLUMNS) + "\n")
            for _, part in sorted(results):
                with open(part, encoding="utf-8") as fin:
                    shutil.copyfileobj(fin, fout)

    print(f"\nwrote {out}  ({len(results)}/{len(files)} index files parsed)")
    print(f"final shape: ({total_rows} rows, {len(COLUMNS)} columns)")

    # count unique in-network rate files by streaming the output once
    urls = set()
    with open(out, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["in_network_file_url"]:
                urls.add(row["in_network_file_url"])
    print(f"unique in-network rate files: {len(urls)}")


if __name__ == "__main__":
    main()