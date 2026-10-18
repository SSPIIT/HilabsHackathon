#!/usr/bin/env python3
"""
Inspect the structure of a (large) in-network rate file by streaming it.

Prints:
  - top-level header fields
  - provider_references: count, keys used, network_name values, groups per reference
  - in_network: count, billing code types, negotiation_arrangement values,
    how rates point to providers (provider_references vs inline provider_groups),
    how many provider references each rate uses, negotiated_type / billing_class mix
  - which provider references are actually used by rates (and how often)
  - a few example items

Usage:
    python src/c5_inspect_rate_file.py --input data/raw/rate_files/<file>.json
    python src/c5_inspect_rate_file.py --input <file>.json --max-items 20000   # quicker, samples first N items
"""

import argparse
import collections
import json
import sys
from pathlib import Path

import ijson


def short(obj, n=600):
    s = json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + " ..."


def main():
    ap = argparse.ArgumentParser(description="Inspect a TiC in-network rate file")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--max-items", type=int, default=0,
                    help="stop after this many in_network items (0 = read all)")
    args = ap.parse_args()
    if not args.input.exists():
        sys.exit(f"input not found: {args.input}")
    size_mb = args.input.stat().st_size / 1e6
    print(f"file: {args.input.name}  ({size_mb:,.0f} MB)\n")

    # ---- header ----------------------------------------------------------------
    header = {}
    with open(args.input, "rb") as fh:
        for prefix, event, value in ijson.parse(fh):
            if prefix in ("reporting_entity_name", "reporting_entity_type", "last_updated_on",
                          "version", "plan_name", "plan_id", "plan_id_type", "plan_market_type"):
                header[prefix] = value
            if prefix in ("provider_references", "in_network") and event == "start_array":
                break
    print("== header ==")
    for k, v in header.items():
        print(f"  {k}: {v}")

    # ---- provider_references ---------------------------------------------------
    ref_keys = collections.Counter()
    net_names = collections.Counter()
    groups_per_ref = collections.Counter()
    npis_per_group = collections.Counter()
    n_refs = 0
    example_ref = None
    with open(args.input, "rb") as fh:
        for ref in ijson.items(fh, "provider_references.item"):
            n_refs += 1
            ref_keys.update(ref.keys())
            for n in ref.get("network_name") or []:
                net_names[n] += 1
            groups = ref.get("provider_groups") or []
            groups_per_ref[min(len(groups), 10)] += 1
            for g in groups:
                k = len(g.get("npi") or [])
                npis_per_group[1 if k == 1 else 5 if k <= 5 else 50 if k <= 50 else 51] += 1
            if example_ref is None:
                example_ref = ref
    print(f"\n== provider_references: {n_refs:,} ==")
    print(f"  keys used          : {dict(ref_keys)}")
    print(f"  network_name values: {dict(net_names.most_common(15)) or 'NONE'}")
    print(f"  groups per ref     : {dict(sorted(groups_per_ref.items()))}  (10 = 10+)")
    print(f"  NPIs per group     : 1:{npis_per_group[1]:,}  2-5:{npis_per_group[5]:,}  "
          f"6-50:{npis_per_group[50]:,}  51+:{npis_per_group[51]:,}")
    if example_ref:
        print(f"  example            : {short(example_ref)}")

    # ---- in_network ------------------------------------------------------------
    n_items = 0
    code_types = collections.Counter()
    arrangements = collections.Counter()
    rate_link = collections.Counter()
    refs_per_rate = collections.Counter()
    neg_types = collections.Counter()
    bill_class = collections.Counter()
    item_keys = collections.Counter()
    ref_usage = collections.Counter()
    inline_npis = set()
    examples = []
    with open(args.input, "rb") as fh:
        for item in ijson.items(fh, "in_network.item"):
            n_items += 1
            item_keys.update(item.keys())
            code_types[item.get("billing_code_type")] += 1
            arrangements[item.get("negotiation_arrangement")] += 1
            for r in item.get("negotiated_rates") or []:
                has_ref = "provider_references" in r
                has_inline = "provider_groups" in r
                rate_link["both" if has_ref and has_inline else "provider_references" if has_ref
                          else "inline provider_groups" if has_inline else "none"] += 1
                refs = r.get("provider_references") or []
                refs_per_rate[1 if len(refs) == 1 else 10 if len(refs) <= 10 else 100 if len(refs) <= 100 else 101] += 1
                ref_usage.update(refs)
                for g in r.get("provider_groups") or []:
                    inline_npis.update(g.get("npi") or [])
                for p in r.get("negotiated_prices") or []:
                    neg_types[p.get("negotiated_type")] += 1
                    bill_class[p.get("billing_class")] += 1
            if len(examples) < 2:
                ex = dict(item)
                ex["negotiated_rates"] = (item.get("negotiated_rates") or [])[:1]
                examples.append(ex)
            if n_items % 2000 == 0:
                print(f"  ... {n_items:,} in_network items read", end="\r")
            if args.max_items and n_items >= args.max_items:
                print(f"\n  (stopped after --max-items {args.max_items:,})")
                break

    print(f"\n== in_network: {n_items:,} items ==")
    print(f"  item keys              : {dict(item_keys)}")
    print(f"  billing_code_type      : {dict(code_types.most_common())}")
    print(f"  negotiation_arrangement: {dict(arrangements)}")
    print(f"  rates link providers by: {dict(rate_link)}")
    print(f"  provider refs per rate : 1:{refs_per_rate[1]:,}  2-10:{refs_per_rate[10]:,}  "
          f"11-100:{refs_per_rate[100]:,}  101+:{refs_per_rate[101]:,}")
    print(f"  negotiated_type        : {dict(neg_types.most_common())}")
    print(f"  billing_class          : {dict(bill_class.most_common())}")
    print(f"  provider refs defined  : {n_refs:,}   used by rates: {len(ref_usage):,}")
    if ref_usage:
        print(f"  most used refs         : {ref_usage.most_common(5)}")
    print(f"  NPIs in inline groups  : {len(inline_npis):,}")
    for i, ex in enumerate(examples, 1):
        print(f"\n  example item {i}: {short(ex, 900)}")


if __name__ == "__main__":
    main()
