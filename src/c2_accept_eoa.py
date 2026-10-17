#!/usr/bin/env python3
"""
C2 step 2 - Accept Health Net Elect Open Access (EOA) plans as PPO.

EOA is a hybrid product with two benefit tiers: an HMO tier (PCP-coordinated care)
and a PPO tier, where members self-refer to PPO-network specialists statewide
without a referral. Because EOA members use the PPO network, EOA plans are
counted as part of the PPO scope.

Reads the output of c2_name_keyword_status.py and, for every plan whose name
contains the word EOA (whole word, case-insensitive):
    ppo_status        -> accepted
    ppo_status_reason -> EOA
All other rows are left unchanged.

Usage:
    python src/c2_accept_eoa.py --input data/interim/c2_name_status.csv --out data/interim/c2_name_status_eoa.csv
"""

import argparse
import re
from pathlib import Path

import pandas as pd

EOA = re.compile(r"\bEOA\b", re.I)


def main():
    ap = argparse.ArgumentParser(description="C2: mark EOA plans as accepted (PPO tier).")
    ap.add_argument("--input", default="data/interim/c2_name_status.csv")
    ap.add_argument("--out", default="data/interim/c2_name_status_eoa.csv")
    ap.add_argument("--reason", default="EOA", help="text written to ppo_status_reason")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    print(f"read {args.input}: shape {df.shape}")
    for col in ("plan_name", "ppo_status", "ppo_status_reason"):
        if col not in df.columns:
            raise SystemExit(f"column '{col}' missing - run c2_name_keyword_status.py first")

    is_eoa = df["plan_name"].str.contains(EOA)
    print(f"\nEOA plans found: {int(is_eoa.sum())}")
    if is_eoa.any():
        print("their status before this step:")
        before = df.loc[is_eoa].groupby(["ppo_status", "ppo_status_reason"]).size()
        for (st, reason), v in before.items():
            print(f"  {st:9s} {v:>6}  {reason}")

    df.loc[is_eoa, "ppo_status"] = "accepted"
    df.loc[is_eoa, "ppo_status_reason"] = args.reason

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print("\nppo_status counts after:")
    for k, v in df["ppo_status"].value_counts().reindex(["accepted", "rejected", "pending"],
                                                        fill_value=0).items():
        print(f"  {k:9s} {v}")

    print("\nppo_status_reason counts after:")
    counts = df.groupby(["ppo_status", "ppo_status_reason"]).size()
    order = {"accepted": 0, "rejected": 1, "pending": 2}
    for (st, reason), v in sorted(counts.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        print(f"  {st:9s} {v:>6}  {reason}")

    print(f"\nwrote {out}")
    print(f"final shape: ({df.shape[0]} rows, {df.shape[1]} columns)")


if __name__ == "__main__":
    main()