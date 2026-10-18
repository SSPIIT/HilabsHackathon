#!/usr/bin/env python3
"""
Diagnose the P4 (conflicting rate) and P5 (invalid code type) checks on one rate file.

P4: groups price rows by the P4 key (code + provider references + place of service +
    modifier + billing class). For groups with more than one amount it counts which other
    fields differ between the rows (setting, negotiated_type, expiration_date,
    additional_information, same or different negotiated_rates entry) and prints examples.
P5: tallies billing_code_type x code shape, with examples.

Usage:
    python src/c3_inspect_conflicts.py --file data/in-network-files/<name>_in-network.json
    python src/c3_inspect_conflicts.py --file ... --examples 5 --max-codes 300
"""

import argparse
import collections
import gzip
import json
import re
from pathlib import Path

import ijson

FIELDS = ("setting", "negotiated_type", "expiration_date", "additional_information")


def shape(code):
    c = str(code)
    if re.fullmatch(r"\d{5}", c):
        return "5 digits (CPT Cat I)"
    if re.fullmatch(r"\d{4}[FTU]", c):
        return "4 digits + F/T/U (CPT Cat II/III/PLA)"
    if re.fullmatch(r"[A-V]\d{4}", c):
        return "letter + 4 digits (HCPCS Level II)"
    if re.fullmatch(r"\d{4}", c):
        return "4 digits"
    if re.fullmatch(r"\d{3}", c):
        return "3 digits"
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--max-codes", type=int, default=0, help="stop after this many codes (0 = all)")
    args = ap.parse_args()

    opener = gzip.open if args.file.name.endswith(".gz") else open
    shapes = collections.Counter()
    shape_examples = collections.defaultdict(list)
    differs = collections.Counter()
    n_groups = n_conf_groups = n_conf_rows = 0
    examples = []

    with opener(args.file, "rb") as fh:
        for i, item in enumerate(ijson.items(fh, "in_network.item", use_float=True), 1):
            ctype = str(item.get("billing_code_type"))
            code = str(item.get("billing_code"))
            sh = shape(code)
            shapes[(ctype, sh)] += 1
            if len(shape_examples[(ctype, sh)]) < 5:
                shape_examples[(ctype, sh)].append(code)

            groups = collections.defaultdict(list)
            for e, nr in enumerate(item.get("negotiated_rates") or []):
                prov = tuple(sorted(map(str, nr.get("provider_references") or [])))
                for p in nr.get("negotiated_prices") or []:
                    key = (prov, tuple(sorted(map(str, p.get("service_code") or []))),
                           tuple(sorted(map(str, p.get("billing_code_modifier") or []))),
                           str(p.get("billing_class", "")).lower())
                    groups[key].append((e, p))
            for key, rows in groups.items():
                n_groups += 1
                if len({r[1].get("negotiated_rate") for r in rows}) < 2:
                    continue
                n_conf_groups += 1
                n_conf_rows += len(rows)
                diff = [f for f in FIELDS if len({str(r[1].get(f)) for r in rows}) > 1]
                if len({r[0] for r in rows}) > 1:
                    diff.append("different negotiated_rates entries")
                else:
                    diff.append("same negotiated_rates entry")
                differs[" + ".join(diff)] += 1
                if len(examples) < args.examples:
                    examples.append({"billing_code_type": ctype, "billing_code": code,
                                     "provider_references": list(key[0])[:10],
                                     "service_code": list(key[1]), "modifier": list(key[2]),
                                     "billing_class": key[3],
                                     "prices": [r[1] for r in rows[:6]], "rows_in_group": len(rows)})
            if i % 200 == 0:
                print(f"  ... {i:,} codes", end="\r", flush=True)
            if args.max_codes and i >= args.max_codes:
                break

    print(" " * 40)
    print("P5 - billing_code_type x code shape (codes):")
    for (ct, sh), n in shapes.most_common():
        print(f"  {ct:<8} {sh:<40} {n:>6}   e.g. {shape_examples[(ct, sh)]}")

    print(f"\nP4 - price groups: {n_groups:,} | groups with 2+ amounts: {n_conf_groups:,} "
          f"| rows in them: {n_conf_rows:,}")
    print("What differs between rows inside a conflicting group (groups):")
    for d, n in differs.most_common(15):
        print(f"  {n:>10,}  {d}")
    print("\nExamples:")
    for ex in examples:
        print(json.dumps(ex, indent=1, default=str)[:3000])
        print("-" * 60)


if __name__ == "__main__":
    main()