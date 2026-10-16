#!/usr/bin/env python3
"""
C2 step 1 - Label plans by network words in the plan name.

Reads the cleaned C1 plan table and adds one column, ppo_status:
  accepted : name contains PPO and none of HMO / EPO / POS
  rejected : name contains HMO, EPO or POS and does not contain PPO
  pending  : everything else (no network word at all, or PPO together with HMO/EPO/POS)

Words are matched case-insensitively and as whole words, so "PPO" in
"FREEDOM-PPO Complete" counts, but letters inside a longer word do not.

Usage:
    python src/c2_name_keyword_status.py --input data/interim/c1_plans.csv --out data/interim/c2_name_status.csv
"""

import argparse
import re
from pathlib import Path

import pandas as pd

PPO = re.compile(r"\bPPO\b", re.I)
OTHER = re.compile(r"\b(?:HMO|EPO|POS)\b", re.I)


def status(name):
    has_ppo = bool(PPO.search(name))
    has_other = bool(OTHER.search(name))
    if has_ppo and not has_other:
        return "accepted"
    if has_other and not has_ppo:
        return "rejected"
    return "pending"


def main():
    ap = argparse.ArgumentParser(description="C2: accept/reject/pending from plan-name keywords.")
    ap.add_argument("--input", default="data/interim/c1_plans.csv")
    ap.add_argument("--out", default="data/interim/c2_name_status.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    print(f"read {args.input}: shape {df.shape}")

    df["ppo_status"] = df["plan_name"].map(status)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print("\nppo_status counts:")
    for k, v in df["ppo_status"].value_counts().reindex(["accepted", "rejected", "pending"],
                                                        fill_value=0).items():
        print(f"  {k:9s} {v}")
    both = df["plan_name"].str.contains(PPO) & df["plan_name"].str.contains(OTHER)
    print(f"  (of pending: {int(both.sum())} have PPO together with HMO/EPO/POS)")

    print(f"\nwrote {out}")
    print(f"final shape: ({df.shape[0]} rows, {df.shape[1]} columns)")


if __name__ == "__main__":
    main()