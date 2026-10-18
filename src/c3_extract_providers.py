#!/usr/bin/env python3
"""
C5 step 1 - Extract the unique (NPI, TIN) pairs from an in-network rate file.

These pairs are the input to address mapping: for each one we later look up
where that provider practises under that TIN.

The file is STREAMED with ijson, so multi-GB rate files do not need to fit in
memory. Provider groups are read from:
  - the top-level `provider_references` section (most files, incl. Centene), and
  - optionally, provider groups written inline inside `in_network` rates
    (--scan-inline; slower, because the whole file is read).

Output columns (one row per unique NPI + TIN):
  npi, tin, tin_type, business_name, network_names,
  n_provider_groups   number of provider groups the pair appears in
  npi_valid           NPI passes the official Luhn check (10 digits, prefix 80840)
  npi_placeholder     NPI is 0 / not 10 digits (placeholder values seen in real files)
  source_file

Usage:
    pip install ijson pandas
    python src/c5_extract_providers.py --input data/raw/rate_files/2026-09-30_..._healthnet-ca_in-network.json --out data/interim/c3_providers.csv
"""

import argparse
import collections
import re
import sys
from pathlib import Path

import ijson
import pandas as pd


def luhn_npi_ok(npi):
    """NPI check digit: Luhn over '80840' + first 9 digits."""
    s = str(npi)
    if not re.fullmatch(r"\d{10}", s):
        return False
    digits = "80840" + s
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def norm_tin(value):
    return re.sub(r"\D", "", str(value or ""))


class Collector:
    def __init__(self):
        self.pairs = {}               # (npi, tin) -> dict
        self.groups_seen = 0
        self.remote_refs = 0          # provider_references that only give a URL
        self.empty_groups = 0

    def add_group(self, group, network_names):
        self.groups_seen += 1
        tin_obj = group.get("tin") or {}
        tin = norm_tin(tin_obj.get("value"))
        tin_type = str(tin_obj.get("type", "")).lower()
        name = re.sub(r"\s+", " ", str(tin_obj.get("business_name") or "")).strip()
        npis = group.get("npi") or []
        if not npis:
            self.empty_groups += 1
        for npi in npis:
            key = (str(npi), tin)
            rec = self.pairs.get(key)
            if rec is None:
                rec = {"npi": str(npi), "tin": tin, "tin_type": tin_type,
                       "names": set(), "networks": set(), "n_provider_groups": 0}
                self.pairs[key] = rec
            if name:
                rec["names"].add(name)
            rec["networks"].update(network_names)
            rec["n_provider_groups"] += 1


def scan_provider_references(path, col):
    with open(path, "rb") as fh:
        for ref in ijson.items(fh, "provider_references.item"):
            networks = [str(n) for n in (ref.get("network_name") or [])]
            groups = ref.get("provider_groups")
            if groups is None and ref.get("location"):
                col.remote_refs += 1
                continue
            for g in groups or []:
                col.add_group(g, networks)


def scan_inline(path, col):
    with open(path, "rb") as fh:
        for rate in ijson.items(fh, "in_network.item.negotiated_rates.item"):
            for g in rate.get("provider_groups") or []:
                col.add_group(g, [])


def main():
    ap = argparse.ArgumentParser(description="C5: unique NPI/TIN pairs from a rate file")
    ap.add_argument("--input", required=True, type=Path, help="in-network rate file (.json)")
    ap.add_argument("--out", default="data/interim/c3_providers.csv", type=Path)
    ap.add_argument("--scan-inline", action="store_true",
                    help="also read provider_groups written inline in in_network (slower)")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"input not found: {args.input}")

    col = Collector()
    print(f"streaming provider_references from {args.input.name} ...")
    scan_provider_references(args.input, col)
    if args.scan_inline:
        print("streaming inline provider_groups from in_network ...")
        scan_inline(args.input, col)

    rows = []
    for rec in col.pairs.values():
        rows.append({
            "npi": rec["npi"],
            "tin": rec["tin"],
            "tin_type": rec["tin_type"],
            "business_name": " | ".join(sorted(rec["names"])),
            "network_names": " | ".join(sorted(rec["networks"])),
            "n_provider_groups": rec["n_provider_groups"],
            "npi_valid": luhn_npi_ok(rec["npi"]),
            "npi_placeholder": not re.fullmatch(r"\d{10}", rec["npi"]),
            "source_file": args.input.name,
        })
    df = pd.DataFrame(rows).sort_values(["tin", "npi"]).reset_index(drop=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    # ---- summary ---------------------------------------------------------------
    print(f"\nprovider groups read     : {col.groups_seen:,}")
    print(f"  with no NPIs           : {col.empty_groups:,}")
    print(f"  remote references (URL only, not followed): {col.remote_refs:,}")
    print(f"unique (NPI, TIN) pairs  : {len(df):,}")
    print(f"unique NPIs              : {df['npi'].nunique():,}")
    print(f"unique TINs              : {df['tin'].nunique():,}")
    print(f"TIN types                : {df['tin_type'].value_counts().to_dict()}")
    print(f"placeholder NPIs (e.g. 0): {int(df['npi_placeholder'].sum()):,} pairs")
    print(f"invalid NPIs (Luhn fail) : {int((~df['npi_valid']).sum()):,} pairs")

    tins_per_npi = df.groupby("npi")["tin"].nunique()
    dist = collections.Counter(min(v, 5) for v in tins_per_npi)
    print("TINs per NPI             : " +
          ", ".join(f"{k if k < 5 else '5+'}: {v:,}" for k, v in sorted(dist.items())))
    if df["network_names"].str.len().gt(0).any():
        print("network names            :")
        for n, c in df["network_names"].value_counts().head(10).items():
            print(f"   {c:>8,}  {n}")

    print(f"\nwrote {args.out}  ({len(df):,} rows)")


if __name__ == "__main__":
    main()
