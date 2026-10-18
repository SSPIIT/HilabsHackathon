#!/usr/bin/env python3
"""
c2_filter_california.py
-----------------------
Takes the cleaned C1 plan table (c1_plans.csv) and keeps only California plans.

Group plans are mostly identified by EIN, which carries no state, so the state
is decided per plan from independent signals, strongest first:

  1. HIOS-shaped plan_id (#####XX###) -> characters 6-7 are the state.
     Used even when plan_id_type says "ein" (Health Net labels 154 HIOS IDs as EIN).
  2. State code in the in-network rate-file name, e.g. "...healthnet-ca_in-network.json".
  3. Plan name starting with "CA " (e.g. "CA L PPO 30/500/4000/30%") or containing
     "California". "CalChoice ..." does not count.

A plan is California if the strongest available signal says CA. If two signals
disagree, the plan is flagged (state_conflict = True) and reported, never silently
kept or dropped.

Outputs (in --out-dir):
  c2_plans_ca.csv          California plans, with state / state_evidence / state_conflict
  c2_plans_excluded.csv    every other plan, with the state it was assigned (audit trail)
  c2_state_log.csv         counts per step, for the logic file

Usage
-----
    python src/c2_filter_california.py --input data/interim/c1_plans.csv --out-dir data/interim
    # column names are auto-detected; override if yours differ:
    python src/c2_filter_california.py --input c1_plans.csv --url-col in_network_file_url
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

TARGET = "CA"
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
HIOS_RE = re.compile(r"^\d{5}([A-Z]{2})\d{3}$")
URL_STATE_RE = re.compile(r"[-_]([a-z]{2})_in-network", re.I)
NAME_CA_RE = re.compile(r"^CA\b")          # case-sensitive on purpose: 'CA L ...' yes, 'CalChoice' no

# Candidate column names, in order of preference (auto-detection).
COLUMN_CANDIDATES = {
    "plan_id": ["plan_id"],
    "plan_name": ["plan_name"],
    "url": ["in_network_file_url", "in_network_url", "rate_file_url", "url_key", "location"],
}


def pick_column(df, role, override):
    if override:
        if override not in df.columns:
            sys.exit(f"--{role}-col '{override}' not in {list(df.columns)}")
        return override
    for cand in COLUMN_CANDIDATES[role]:
        if cand in df.columns:
            return cand
    sys.exit(f"could not find a {role} column; tried {COLUMN_CANDIDATES[role]}. "
             f"Pass --{role.replace('_', '-')}-col. Columns are: {list(df.columns)}")


def hios_state(plan_id):
    m = HIOS_RE.match(str(plan_id).strip().upper())
    return m.group(1) if m and m.group(1) in US_STATES else None


def url_states(url_field):
    """A cell may hold several URLs separated by '|' ; return the set of states found."""
    states = set()
    for url in str(url_field or "").split("|"):
        m = URL_STATE_RE.search(url)
        if m and m.group(1).upper() in US_STATES:
            states.add(m.group(1).upper())
    return states


def name_state(plan_name):
    name = re.sub(r"\s+", " ", str(plan_name or "")).strip()
    if NAME_CA_RE.match(name) or "california" in name.lower():
        return TARGET
    return None


def decide(row, id_col, name_col, url_col):
    h = hios_state(row[id_col])
    us = url_states(row[url_col])
    u = next(iter(us)) if len(us) == 1 else None
    n = name_state(row[name_col])

    evidence = []
    if h:
        evidence.append(f"hios_id={h}")
    if us:
        evidence.append("rate_file=" + "/".join(sorted(us)))
    if n:
        evidence.append(f"plan_name={n}")

    signals = {s for s in (h, n) if s} | us
    state = h or u or n or "UNKNOWN"          # priority: HIOS ID > rate-file name > plan name
    conflict = len(signals) > 1
    return pd.Series({"state": state,
                      "state_evidence": "; ".join(evidence) or "none",
                      "state_conflict": conflict})


def main():
    ap = argparse.ArgumentParser(description="Keep only California plans from c1_plans.csv")
    ap.add_argument("--input", required=True, type=Path, help="c1_plans.csv from c1_clean_index.py")
    ap.add_argument("--out-dir", default=str(PATHS["interim"]), type=Path)
    ap.add_argument("--plan-id-col")
    ap.add_argument("--plan-name-col")
    ap.add_argument("--url-col")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    id_col = pick_column(df, "plan_id", args.plan_id_col)
    name_col = pick_column(df, "plan_name", args.plan_name_col)
    url_col = pick_column(df, "url", args.url_col)
    print(f"read {len(df):,} rows from {args.input}")
    print(f"columns used: plan_id='{id_col}', plan_name='{name_col}', url='{url_col}'")

    df = pd.concat([df, df.apply(decide, axis=1, args=(id_col, name_col, url_col))], axis=1)

    ca = df[df["state"] == TARGET].copy()
    excluded = df[df["state"] != TARGET].copy()
    conflicts = df[df["state_conflict"]]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    ca.to_csv(args.out_dir / "c2_plans_ca.csv", index=False)
    excluded.to_csv(args.out_dir / "c2_plans_excluded.csv", index=False)

    log = [
        ("input rows", len(df)),
        ("state decided by HIOS ID", int(df["state_evidence"].str.contains("hios_id=").sum())),
        ("state decided by rate-file name (no HIOS ID)",
         int((~df["state_evidence"].str.contains("hios_id=") & df["state_evidence"].str.contains("rate_file=")).sum())),
        ("state decided by plan name only",
         int((df["state_evidence"] == "plan_name=CA").sum())),
        ("state unknown", int((df["state"] == "UNKNOWN").sum())),
        ("signals disagree (flagged)", len(conflicts)),
        ("California rows kept", len(ca)),
        ("rows excluded", len(excluded)),
    ]
    pd.DataFrame(log, columns=["step", "rows"]).to_csv(args.out_dir / "c2_state_log.csv", index=False)

    # ---- terminal summary ------------------------------------------------------
    print("\nState assigned to every row:")
    print(df["state"].value_counts().to_string())
    print("\nLog:")
    for step, n in log:
        print(f"  {step:<48} {n:>7,}")

    if len(conflicts):
        print("\n!! rows where signals disagree (check these):")
        print(conflicts[[id_col, name_col, "state", "state_evidence"]].head(20).to_string(index=False))

    print(f"\nCalifornia rows: {len(ca):,}")
    for col in ("brand", "issuer_name", "plan_market_type", "plan_id_type"):
        if col in ca.columns:
            print(f"  by {col}: {ca[col].value_counts().to_dict()}")
    print(f"  evidence: {ca['state_evidence'].value_counts().to_dict()}")
    print("  rate files:")
    for url, n in ca[url_col].value_counts().items():
        print(f"    {n:>6,}  {str(url).rsplit('/', 1)[-1]}")

    print(f"\nwrote {args.out_dir / 'c2_plans_ca.csv'}")
    print(f"wrote {args.out_dir / 'c2_plans_excluded.csv'}")
    print(f"wrote {args.out_dir / 'c2_state_log.csv'}")


if __name__ == "__main__":
    main()