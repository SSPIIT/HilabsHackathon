#!/usr/bin/env python3
"""
C2 step 1 - Label plans by network words in the plan name.

Reads the cleaned C1 plan table and adds two columns:

  ppo_status
    accepted : name contains PPO (with or without HMO / EPO / POS also present)
    rejected : name contains HMO, EPO or POS and does not contain PPO
    pending  : no network word at all

  ppo_status_reason
    why the plan got that status, listing the words actually found, e.g.
      "found PPO; no HMO/EPO/POS"
      "found HMO; no PPO"
      "both present: found PPO and EPO"
      "no network word (PPO/HMO/EPO/POS) found"

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


def found_words(pattern, name):
    """Distinct matched words, upper-cased, in the order they appear."""
    seen = []
    for m in pattern.findall(name):
        w = m.upper()
        if w not in seen:
            seen.append(w)
    return seen


def status_and_reason(name):
    ppo_words = found_words(PPO, name)
    other_words = found_words(OTHER, name)
    has_ppo, has_other = bool(ppo_words), bool(other_words)

    if has_ppo and not has_other:
        return "accepted", "found PPO; no HMO/EPO/POS"
    if has_other and not has_ppo:
        return "rejected", f"found {', '.join(other_words)}; no PPO"
    if has_ppo and has_other:
        return "accepted", f"both present: found PPO and {', '.join(other_words)}"
    return "pending", "no network word (PPO/HMO/EPO/POS) found"


def main():
    ap = argparse.ArgumentParser(description="C2: accept/reject/pending from plan-name keywords.")
    ap.add_argument("--input", default="data/interim/c1_plans.csv")
    ap.add_argument("--out", default="data/interim/c2_name_status.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    print(f"read {args.input}: shape {df.shape}")

    results = df["plan_name"].map(status_and_reason)
    df["ppo_status"] = results.str[0]
    df["ppo_status_reason"] = results.str[1]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print("\nppo_status counts:")
    for k, v in df["ppo_status"].value_counts().reindex(["accepted", "rejected", "pending"],
                                                        fill_value=0).items():
        print(f"  {k:9s} {v}")
    both = df["plan_name"].str.contains(PPO) & df["plan_name"].str.contains(OTHER)
    print(f"  (of accepted: {int(both.sum())} have PPO together with HMO/EPO/POS)")

    print("\nppo_status_reason counts:")
    counts = df.groupby(["ppo_status", "ppo_status_reason"]).size()
    order = {"accepted": 0, "rejected": 1, "pending": 2}
    for (st, reason), v in sorted(counts.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
        print(f"  {st:9s} {v:>6}  {reason}")

    print(f"\nwrote {out}")
    print(f"final shape: ({df.shape[0]} rows, {df.shape[1]} columns)")


if __name__ == "__main__":
    main()