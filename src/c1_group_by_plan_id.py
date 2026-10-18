#!/usr/bin/env python3
"""
Group the raw parsed index table (output of c1_parse_index.py) by plan_id.

Writes two files:
  <out>_rows.csv     every raw row, sorted so rows with the same plan_id sit together,
                     plus plan_id_group_size and plan_id_row_no columns
  <out>_summary.csv  one row per plan_id: how many rows, distinct names, files, etc.

No cleaning is done: plan_id is used exactly as it appears in the raw file.

Usage:
    python src/c1_group_by_plan_id.py --input data/interim/c1_index_raw.csv --out data/interim/c1_by_plan_id
"""

import argparse
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402


def join_unique(s):
    return " | ".join(sorted({str(x) for x in s if str(x)}))


def main():
    ap = argparse.ArgumentParser(description="Group raw index rows by plan_id.")
    ap.add_argument("--input", default=str(PATHS["interim"] / "c1_index_raw.csv"))
    ap.add_argument("--out", default=str(PATHS["interim"] / "c1_by_plan_id"),
                    help="output path prefix (no extension)")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    print(f"read {args.input}: shape {df.shape}")

    # 1. all rows, grouped together by plan_id
    df["plan_id_group_size"] = df.groupby("plan_id")["plan_id"].transform("size")
    rows = df.sort_values(["plan_id", "plan_name", "index_file"], kind="stable")
    rows["plan_id_row_no"] = rows.groupby("plan_id").cumcount() + 1
    lead = ["plan_id", "plan_id_group_size", "plan_id_row_no"]
    rows = rows[lead + [c for c in rows.columns if c not in lead]]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows_path = out.with_name(out.name + "_rows.csv")
    rows.to_csv(rows_path, index=False)

    # 2. one summary row per plan_id
    summary = (df.groupby("plan_id")
                 .agg(n_rows=("plan_id", "size"),
                      n_plan_names=("plan_name", "nunique"),
                      plan_names=("plan_name", join_unique),
                      plan_id_types=("plan_id_type", join_unique),
                      market_types=("plan_market_type", join_unique),
                      brands=("brand", join_unique),
                      issuer_names=("issuer_name", join_unique),
                      plan_sponsor_names=("plan_sponsor_name", join_unique),
                      n_rate_files=("in_network_file_url", "nunique"),
                      rate_files=("in_network_file_url",
                                  lambda s: join_unique(u.rsplit("/", 1)[-1] for u in s)),
                      index_files=("index_file", join_unique))
                 .reset_index()
                 .sort_values(["n_rows", "plan_id"], ascending=[False, True]))
    summary_path = out.with_name(out.name + "_summary.csv")
    summary.to_csv(summary_path, index=False)

    print(f"wrote {rows_path}: shape {rows.shape}")
    print(f"wrote {summary_path}: shape {summary.shape}  ({summary.shape[0]} unique plan_ids)")
    print(f"plan_ids with more than one plan name: {(summary['n_plan_names'] > 1).sum()}")
    print(f"plan_ids pointing to more than one rate file: {(summary['n_rate_files'] > 1).sum()}")


if __name__ == "__main__":
    main()