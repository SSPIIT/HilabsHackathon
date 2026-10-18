#!/usr/bin/env python3
"""
test.py - Summarise every rate file listed in a download log.

Input: the download log CSV with columns  url, file, bytes, status
For each row with status "downloaded" it:
  1. finds the file in --rate-dir and checks its size matches the logged bytes
  2. streams the file (ijson, so 700 MB+ files are fine) and reports:
       header fields (entity, type, last_updated_on, version)
       provider_references: count, network names
       unique NPIs and TINs (from provider_references and any inline provider_groups)
       invalid / placeholder NPIs (Luhn check, NPI 0)
       in_network: number of billing codes, code types, negotiation arrangements,
                   number of price rows
Then prints a combined total of unique NPIs across all files.

Usage:
    python src/test.py --log data/raw/rate_files/download_log.csv --rate-dir data/raw/rate_files
"""

import argparse
import collections
import re
import sys
from pathlib import Path

import ijson
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402


def luhn_ok(npi):
    s = str(npi)
    if not re.fullmatch(r"\d{10}", s):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def summarise(path):
    s = {"header": {}, "refs": 0, "networks": collections.Counter(), "npis": set(), "tins": set(),
         "codes": 0, "code_types": collections.Counter(), "arrangements": collections.Counter(),
         "price_rows": 0, "inline_groups": 0}

    with open(path, "rb") as fh:                      # header
        for prefix, event, value in ijson.parse(fh):
            if prefix in ("reporting_entity_name", "reporting_entity_type", "last_updated_on", "version"):
                s["header"][prefix] = value
            if prefix in ("provider_references", "in_network") and event == "start_array":
                break

    def add_group(g):
        tin = re.sub(r"\D", "", str((g.get("tin") or {}).get("value", "")))
        if tin:
            s["tins"].add(tin)
        for n in g.get("npi") or []:
            s["npis"].add(str(n))

    with open(path, "rb") as fh:                      # provider references
        for ref in ijson.items(fh, "provider_references.item"):
            s["refs"] += 1
            for n in ref.get("network_name") or []:
                s["networks"][n] += 1
            for g in ref.get("provider_groups") or []:
                add_group(g)

    with open(path, "rb") as fh:                      # in_network
        for item in ijson.items(fh, "in_network.item"):
            s["codes"] += 1
            s["code_types"][item.get("billing_code_type")] += 1
            s["arrangements"][item.get("negotiation_arrangement")] += 1
            for r in item.get("negotiated_rates") or []:
                s["price_rows"] += len(r.get("negotiated_prices") or [])
                for g in r.get("provider_groups") or []:
                    s["inline_groups"] += 1
                    add_group(g)
            if s["codes"] % 5000 == 0:
                print(f"    ... {s['codes']:,} billing codes read", end="\r", flush=True)
    return s


def main():
    ap = argparse.ArgumentParser(description="Summarise rate files from a download log")
    ap.add_argument("--log", required=True, type=Path, help="download log CSV (url,file,bytes,status)")
    ap.add_argument("--rate-dir", default=str(PATHS["rate_files"]), type=Path)
    args = ap.parse_args()

    log = pd.read_csv(args.log, dtype=str, keep_default_na=False)
    need = {"file", "bytes", "status"}
    if not need.issubset(log.columns):
        sys.exit(f"log must have columns {sorted(need)}; has {list(log.columns)}")
    print(f"log: {args.log}  ({len(log)} row(s))\n")

    all_npis = set()
    for _, row in log.iterrows():
        name = row["file"]
        print("=" * 80)
        print(name)
        print("=" * 80)
        if row["status"].strip().lower() != "downloaded":
            print(f"  skipped: status = {row['status']}\n")
            continue
        path = args.rate_dir / name
        if not path.exists():
            print(f"  MISSING: {path} not found\n")
            continue
        size = path.stat().st_size
        logged = int(row["bytes"]) if row["bytes"].isdigit() else None
        ok = "OK" if logged == size else f"MISMATCH (log {logged:,})" if logged else "no size in log"
        print(f"  size            : {size:,} bytes  [{ok}]")

        s = summarise(path)
        h = s["header"]
        print(" " * 60, end="\r")
        print(f"  entity          : {h.get('reporting_entity_name')} ({h.get('reporting_entity_type')})")
        print(f"  last_updated_on : {h.get('last_updated_on')}   version: {h.get('version')}")
        print(f"  provider refs   : {s['refs']:,}   inline provider groups: {s['inline_groups']:,}")
        print(f"  network names   : {dict(s['networks'].most_common(10)) or 'none'}")
        invalid = [n for n in s["npis"] if not luhn_ok(n)]
        print(f"  UNIQUE NPIs     : {len(s['npis']):,}   (invalid/placeholder: {len(invalid)}"
              + (f", e.g. {invalid[:5]}" if invalid else "") + ")")
        print(f"  unique TINs     : {len(s['tins']):,}")
        print(f"  billing codes   : {s['codes']:,}   price rows: {s['price_rows']:,}")
        print(f"  code types      : {dict(s['code_types'].most_common())}")
        print(f"  arrangements    : {dict(s['arrangements'])}\n")
        all_npis |= s["npis"]

    print("=" * 80)
    print(f"TOTAL unique NPIs across all files: {len(all_npis):,}")


if __name__ == "__main__":
    main()
