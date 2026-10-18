#!/usr/bin/env python3
"""
C4 step 1 - EXTRACT: read the PPO rate file and write every negotiated price, per provider.

Nothing is chosen or dropped here - one row per
    NPI x TIN x billing code x negotiated price x provider group
exactly as the file states it. Choosing one rate per provider and code is step 2
(c4_choose_rate.py).

How the file is read (streamed with ijson, never loaded whole):
  1. provider_references: provider_group_id -> the NPIs and TIN in that group (kept in memory)
  2. in_network: each billing code -> negotiated_rates -> negotiated_prices; every price is
     written once for every NPI of every provider group it points to
     (inline provider_groups inside a rate are handled too)

Output: data/interim/c4_rates_extracted.csv, columns
  npi, tin, tin_type, provider_group_id,
  billing_code_type, billing_code_type_version, billing_code, name, negotiation_arrangement,
  negotiated_type, negotiated_rate, expiration_date, billing_class,
  service_codes (place-of-service codes, | separated), pos_group (derived from them:
  non_facility / facility / mixed / institutional / unspecified), price_setting (the
  price's own "setting" field when the file gives one, e.g. outpatient), modifiers,
  additional_information, n_bundled_codes

Usage:
    python src/c4_extract_rates.py --input data/in-network-files/<file>.json
    python src/c4_extract_rates.py --input <file>.json --limit-items 200      # quick test
    python src/c4_extract_rates.py --input <file>.json --peek 636 --peek-npi 1184722779
        (prints how one billing code is stored in the file, writes nothing)
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import ijson

# CMS place-of-service codes billed as facility; all others = non-facility
FACILITY_POS = {"19", "21", "22", "23", "24", "26", "31", "34", "41", "42", "51", "52",
                "53", "56", "61"}
COLS = ["npi", "tin", "tin_type", "provider_group_id",
        "billing_code_type", "billing_code_type_version", "billing_code", "name",
        "negotiation_arrangement", "negotiated_type", "negotiated_rate", "expiration_date",
        "billing_class", "service_codes", "pos_group", "price_setting", "modifiers", "additional_information",
        "n_bundled_codes"]


def setting_of(codes, billing_class):
    if codes:
        fac = codes & FACILITY_POS
        if fac and fac == codes:
            return "facility"
        return "non_facility" if not fac else "mixed"
    return "institutional" if billing_class == "institutional" else "unspecified"


def group_members(g):
    tin = g.get("tin") or {}
    tv = re.sub(r"\D", "", str(tin.get("value") or ""))
    tt = str(tin.get("type", "")).lower()
    return [(str(n), tv, tt) for n in g.get("npi") or []]


def read_groups(path):
    groups = {}
    with open(path, "rb") as fh:
        for ref in ijson.items(fh, "provider_references.item"):
            gid = str(ref.get("provider_group_id", ""))
            members = groups.setdefault(gid, [])
            for g in ref.get("provider_groups") or []:
                members.extend(group_members(g))
    return groups


def peek(path, code, npi=""):
    """Print the first 2 in_network items with this billing code, compactly."""
    import json
    groups = {}
    if npi:
        with open(path, "rb") as fh:
            for ref in ijson.items(fh, "provider_references.item"):
                for g in ref.get("provider_groups") or []:
                    if npi in {str(x) for x in g.get("npi") or []}:
                        groups[str(ref.get("provider_group_id"))] = (g.get("tin") or {}).get("value")
        print(f"NPI {npi} is in provider groups: {groups}\n")
    shown = 0
    with open(path, "rb") as fh:
        for item in ijson.items(fh, "in_network.item", use_float=True):
            if str(item.get("billing_code", "")).strip() != code:
                continue
            shown += 1
            head = {k: v for k, v in item.items() if k != "negotiated_rates"}
            for k in ("bundled_codes", "covered_services"):
                if k in head:
                    head[k] = head[k][:5]
            print(f"--- item {shown}: " + json.dumps(head, default=str)[:800])
            nrs = item.get("negotiated_rates") or []
            print(f"    negotiated_rates: {len(nrs)}")
            for i, nr in enumerate(nrs):
                refs = [str(r) for r in nr.get("provider_references") or []]
                if npi and not set(refs) & set(groups):
                    continue
                prices = nr.get("negotiated_prices") or []
                print(f"    [{i}] provider_references {refs[:10]}{' ...' if len(refs) > 10 else ''} "
                      f"({len(refs)} refs), {len(prices)} prices")
                for p in prices[:6]:
                    print("        " + json.dumps(p, default=str)[:300])
            if shown >= 2:
                break
    if not shown:
        print(f"no item with billing_code {code}")



def main():
    ap = argparse.ArgumentParser(description="C4 step 1: extract every negotiated price per provider")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--out", default="data/interim/c4_rates_extracted.csv", type=Path)
    ap.add_argument("--limit-items", type=int, default=0, help="only the first N billing codes (test)")
    ap.add_argument("--peek", default="", help="print how one billing code is stored (e.g. 636), then stop")
    ap.add_argument("--peek-npi", default="", help="with --peek: only rate blocks whose groups hold this NPI")
    args = ap.parse_args()
    if not args.input.exists():
        sys.exit(f"input not found: {args.input}")
    if args.peek:
        peek(args.input, args.peek, args.peek_npi)
        return

    print(f"file {args.input.name}  ({args.input.stat().st_size / 1e6:,.0f} MB)")
    groups = read_groups(args.input)
    print(f"provider_references: {len(groups):,} groups, "
          f"{sum(len(m) for m in groups.values()):,} group x NPI entries")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    n_items = n_rows = n_missing_ref = inline = 0
    t0 = time.time()
    with open(args.input, "rb") as fh, open(args.out, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(COLS)
        for item in ijson.items(fh, "in_network.item", use_float=True):
            n_items += 1
            code = [str(item.get("billing_code_type", "")), str(item.get("billing_code_type_version", "")),
                    str(item.get("billing_code", "")).strip(), str(item.get("name", "")),
                    str(item.get("negotiation_arrangement", "")).lower()]
            nb = len(item.get("bundled_codes") or []) + len(item.get("covered_services") or [])
            for nr in item.get("negotiated_rates") or []:
                targets = []                                   # (group id, members)
                for r in nr.get("provider_references") or []:
                    m = groups.get(str(r))
                    if m is None:
                        n_missing_ref += 1
                        continue
                    targets.append((str(r), m))
                for g in nr.get("provider_groups") or []:
                    inline += 1
                    targets.append((f"inline_{inline}", group_members(g)))
                for p in nr.get("negotiated_prices") or []:
                    sc = {str(c).strip() for c in p.get("service_code") or [] if str(c).strip()}
                    mods = sorted({str(m).strip().upper() for m in p.get("billing_code_modifier") or [] if str(m).strip()})
                    bc = str(p.get("billing_class", "")).lower()
                    price = [str(p.get("negotiated_type", "")).lower(), p.get("negotiated_rate", ""),
                             str(p.get("expiration_date", "")), bc, "|".join(sorted(sc)), setting_of(sc, bc),
                             str(p.get("setting", "") or "").lower(), "|".join(mods), str(p.get("additional_information", "") or ""), nb]
                    for gid, members in targets:
                        for npi, tin, tt in members:
                            w.writerow([npi, tin, tt, gid] + code + price)
                            n_rows += 1
            if n_items % 200 == 0:
                print(f"  {n_items:,} codes, {n_rows:,} rows  ({time.time() - t0:,.0f} s)", flush=True)
            if args.limit_items and n_items >= args.limit_items:
                break

    print(f"\nbilling codes read          : {n_items:,}")
    print(f"rows written (NPI x price)  : {n_rows:,}")
    if n_missing_ref:
        print(f"rate references to unknown provider groups: {n_missing_ref:,} (skipped)")
    if inline:
        print(f"inline provider groups      : {inline:,}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()