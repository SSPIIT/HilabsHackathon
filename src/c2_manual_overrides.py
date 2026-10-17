#!/usr/bin/env python3
"""
C2 step 3 - Apply manual review decisions to plans the keyword rules could not settle.

Reads the output of c2_accept_eoa.py and, for each plan name listed in OVERRIDES
below, sets ppo_status and ppo_status_reason to the reviewed values. Plan names
are compared after trimming and collapsing whitespace, case-insensitively, so
"CA L CUSPPO 15/250/20% RAP   " matches "CA L CUSPPO 15/250/20% RAP".

A column `ppo_status_source` is added:
    rule    - status came from the keyword / EOA rules
    manual  - status was set here by manual review

Every override must match at least one row; unmatched names are reported.

Usage:
    python src/c2_manual_overrides.py --input data/interim/c2_name_status_eoa.csv --out data/interim/c2_name_status_final.csv
"""

import argparse
import re
from pathlib import Path

import pandas as pd

# plan_name -> (ppo_status, ppo_status_reason)   (manual review decisions)
OVERRIDES = {
    "CA L CUSPPO 15/250/20% RAP":               ("accepted", "PPO keyword is present"),
    "CA L SAL Mex 5/0/1500":                    ("rejected", "SAL is HMO type"),
    "CA L&T CUS SAL CEMENT MASONS CA H&W GF+":  ("rejected", "SAL is HMO type"),
}


def norm(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def main():
    ap = argparse.ArgumentParser(description="C2: apply manual PPO status overrides.")
    ap.add_argument("--input", default="data/interim/c2_name_status_eoa.csv")
    ap.add_argument("--out", default="data/interim/c2_name_status_final.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    print(f"read {args.input}: shape {df.shape}")
    for col in ("plan_name", "ppo_status", "ppo_status_reason"):
        if col not in df.columns:
            raise SystemExit(f"column '{col}' missing - run the earlier C2 steps first")

    if "ppo_status_source" not in df.columns:
        df["ppo_status_source"] = "rule"

    keys = df["plan_name"].map(norm)
    print("\nmanual overrides:")
    unmatched = []
    for name, (status, reason) in OVERRIDES.items():
        hit = keys == norm(name)
        n = int(hit.sum())
        if n == 0:
            unmatched.append(name)
            print(f"  NOT FOUND  {name}")
            continue
        before = df.loc[hit, "ppo_status"].value_counts().to_dict()
        df.loc[hit, "ppo_status"] = status
        df.loc[hit, "ppo_status_reason"] = reason
        df.loc[hit, "ppo_status_source"] = "manual"
        print(f"  {n:>3} row(s)  {name:<42} {before} -> {status} ({reason})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print("\nppo_status counts after:")
    for k, v in df["ppo_status"].value_counts().reindex(["accepted", "rejected", "pending"],
                                                        fill_value=0).items():
        print(f"  {k:9s} {v}")

    print("\nppo_status_reason counts after:")
    counts = df.groupby(["ppo_status", "ppo_status_source", "ppo_status_reason"]).size()
    order = {"accepted": 0, "rejected": 1, "pending": 2}
    for (st, src, reason), v in sorted(counts.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        print(f"  {st:9s} {src:6s} {v:>6}  {reason}")

    left = df[df["ppo_status"] == "pending"]
    if len(left):
        print(f"\nstill pending ({len(left)}):")
        for n in left["plan_name"].head(20):
            print(f"  {n}")
    if unmatched:
        print(f"\n!! {len(unmatched)} override name(s) matched no row - check spelling")

    print(f"\nwrote {out}")
    print(f"final shape: ({df.shape[0]} rows, {df.shape[1]} columns)")


if __name__ == "__main__":
    main()