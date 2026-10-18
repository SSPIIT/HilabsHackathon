#!/usr/bin/env python3
"""
C2 final export - PPO plans in scope, with only the C1 columns.

Reads the final C2 table (after keyword status, EOA and manual overrides) and keeps:
  - ppo_status == "accepted"
  - plan_market_type == "group"   (scope: commercial group; use --market all to keep every market)

Outputs only the columns asked for in C1:
    brand, plan_name, plan_id, plan_market_type, in_network_file_url
and removes duplicate rows (plan name compared after trimming spaces, case-insensitive).

Usage:
    python src/c2_export_ppo_plans.py --input data/interim/c2_name_status_final.csv --out data/output/c2_ppo_plans_ca.csv
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

COLS = ["brand", "plan_name", "plan_id", "plan_market_type", "in_network_file_url"]


def main():
    ap = argparse.ArgumentParser(description="Export accepted PPO plans with the C1 columns.")
    ap.add_argument("--input", default="data/interim/c2_name_status_final.csv")
    ap.add_argument("--out", default="data/output/c2_ppo_plans_ca.csv")
    ap.add_argument("--market", default="group", help='"group", "individual" or "all"')
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    print(f"read {args.input}: shape {df.shape}")
    missing = [c for c in COLS + ["ppo_status"] if c not in df.columns]
    if missing:
        sys.exit(f"missing columns {missing}; columns are {list(df.columns)}")

    ppo = df[df["ppo_status"].str.lower() == "accepted"]
    print(f"accepted (PPO) rows      : {len(ppo):,}")
    print(f"  by market              : {ppo['plan_market_type'].value_counts().to_dict()}")

    if args.market != "all":
        dropped = ppo[ppo["plan_market_type"].str.lower() != args.market]
        ppo = ppo[ppo["plan_market_type"].str.lower() == args.market]
        print(f"kept market '{args.market}'    : {len(ppo):,}  (dropped {len(dropped):,} other-market PPO rows"
              + (f", e.g. {dropped['brand'].value_counts().to_dict()})" if len(dropped) else ")"))

    out_df = ppo[COLS].copy()
    out_df["plan_name"] = out_df["plan_name"].map(lambda s: re.sub(r"\s+", " ", s).strip())
    key = out_df.assign(_n=out_df["plan_name"].str.lower())[["brand", "_n", "plan_id",
                                                              "plan_market_type", "in_network_file_url"]]
    before = len(out_df)
    out_df = out_df[~key.duplicated()].sort_values(["brand", "plan_name", "plan_id"]).reset_index(drop=True)
    print(f"duplicates removed       : {before - len(out_df):,}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out, index=False)

    print(f"\nPPO plans written        : {len(out_df):,}")
    print(f"  unique plan IDs        : {out_df['plan_id'].nunique():,}")
    print(f"  by brand               : {out_df['brand'].value_counts().to_dict()}")
    print("  rate files:")
    for url, n in out_df["in_network_file_url"].value_counts().items():
        print(f"    {n:>6,}  {url.rsplit('/', 1)[-1]}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()