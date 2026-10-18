#!/usr/bin/env python3
"""
C1 (part 2) - Standardise the raw index table, then remove duplicate rows.

Input : raw table from c1_parse_index.py (one row per plan x rate file)
Output: c1_plans.csv          cleaned table, duplicates removed
        c1_cleaning_log.csv   what each step changed / removed

Step 1 - standardise every column (nothing is removed here)
  S1  all text columns   : strip leading/trailing spaces, collapse repeated spaces,
                           remove invisible characters (non-breaking space, zero-width)
  S2  plan_id            : UPPER-CASE, remove spaces, hyphens and dots
                           (e.g. "71-0601534" -> "710601534", "67138ca070" -> "67138CA070")
  S3  plan_id_type       : lower-case ("HIOS" -> "hios", "EIN" -> "ein")
  S4  plan_market_type   : lower-case ("Group" -> "group")
  S5  brand              : blank -> "Unknown"
  S6  index_last_updated_on : written as YYYY-MM-DD when it is a valid date
  S7  plan_name          : build a comparison key in lower case. The name itself keeps
                           its original capitalisation (only spaces are fixed in S1).

Step 2 - remove duplicates
  D1  A row is removed only if ALL FIVE C1 columns match another row:
        brand, plan_name (case-insensitive), plan_id, plan_market_type,
        in_network_file_url (compared exactly, after S1 whitespace trimming)
      The first row is kept. It records how many times it appeared
      (n_occurrences) and every index file it came from.

Usage:
    python src/c1_clean_index.py --input data/interim/c1_index_raw.csv --out-dir data/interim
"""

import argparse
from pathlib import Path
import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

INVISIBLE = r"[ ​‌‍﻿]"
DEDUP_KEY = ["brand", "plan_name_key", "plan_id", "plan_market_type", "in_network_file_url"]
OUTPUT_COLS = ["brand", "plan_name", "plan_id", "plan_market_type", "in_network_file_url",
               "plan_id_type", "issuer_name", "plan_sponsor_name", "reporting_entity_name",
               "reporting_entity_type", "index_last_updated_on", "in_network_file_description",
               "allowed_amount_file_url", "index_file", "n_occurrences"]


def main():
    ap = argparse.ArgumentParser(description="C1: standardise + deduplicate the raw index table.")
    ap.add_argument("--input", default=str(PATHS["interim"] / "c1_index_raw.csv"))
    ap.add_argument("--out-dir", default=str(PATHS["interim"]))
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)   # keep IDs as text
    print(f"read {args.input}: shape {df.shape}")
    log = []

    def record(step, what, changed):
        log.append({"step": step, "what": what, "rows_affected": int(changed), "rows_after": len(df)})
        print(f"  {step:3s} {what:62s} rows affected: {int(changed)}")

    # ---------------- Step 1: standardise ----------------
    text_cols = [c for c in df.columns if c != "block_id"]
    before = df[text_cols].copy()
    for c in text_cols:
        df[c] = (df[c].str.replace(INVISIBLE, " ", regex=True)
                      .str.replace(r"\s+", " ", regex=True)
                      .str.strip())
    record("S1", "whitespace / invisible characters fixed", (before != df[text_cols]).any(axis=1).sum())

    old = df["plan_id"].copy()
    df["plan_id"] = df["plan_id"].str.upper().str.replace(r"[\s\-.]", "", regex=True)
    record("S2", "plan_id upper-cased, spaces/hyphens/dots removed", (old != df["plan_id"]).sum())

    for step, col in (("S3", "plan_id_type"), ("S4", "plan_market_type")):
        old = df[col].copy()
        df[col] = df[col].str.lower()
        record(step, f"{col} lower-cased", (old != df[col]).sum())

    blank = df["brand"] == ""
    df.loc[blank, "brand"] = "Unknown"
    record("S5", "blank brand set to Unknown", blank.sum())

    old = df["index_last_updated_on"].copy()
    parsed = pd.to_datetime(df["index_last_updated_on"], errors="coerce")
    df.loc[parsed.notna(), "index_last_updated_on"] = parsed[parsed.notna()].dt.strftime("%Y-%m-%d")
    record("S6", "index_last_updated_on written as YYYY-MM-DD", (old != df["index_last_updated_on"]).sum())

    df["plan_name_key"] = df["plan_name"].str.lower()
    record("S7", "plan-name comparison key built (lower case)", 0)

    # ---------------- Step 2: remove duplicates ----------------
    n_before = len(df)
    df["n_occurrences"] = df.groupby(DEDUP_KEY)["plan_id"].transform("size")
    df["index_file"] = df.groupby(DEDUP_KEY)["index_file"].transform(
        lambda s: "|".join(sorted(set(s))))
    df = df.drop_duplicates(subset=DEDUP_KEY, keep="first")
    record("D1", "rows removed: all 5 columns matched another row", n_before - len(df))

    df = df[OUTPUT_COLS].sort_values(["brand", "plan_market_type", "plan_id", "plan_name"])
    df.to_csv(out_dir / "c1_plans.csv", index=False)
    pd.DataFrame(log).to_csv(out_dir / "c1_cleaning_log.csv", index=False)

    print(f"\nwrote {out_dir / 'c1_plans.csv'}")
    print(f"final shape: ({df.shape[0]} rows, {df.shape[1]} columns)")
    print(f"unique rate files in cleaned table: {df['in_network_file_url'].nunique()}")


if __name__ == "__main__":
    main()