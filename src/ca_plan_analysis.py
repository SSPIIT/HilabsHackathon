#!/usr/bin/env python3
"""
ca_plan_analysis.py
-------------------
Prints, on the terminal, the California analysis of the cleaned C1 plan table
(c1_plans.csv), per brand (Ambetter, Health Net, ...):

  1. Per rate file: how many plans point to it and which state(s) its plans resolve to.
  2. Per plan, the state decided from independent signals (same logic as
     c2_filter_california.py, imported from it so the two never disagree):
       HIOS-shaped plan ID  >  state code in the rate-file name  >  plan name starting "CA "
  3. For the California plans: markets, ID types, HIOS IDs mislabelled as EIN,
     rate files, and a network-type breakdown (PPO / HMO / EPO / POS / EOA / UNKNOWN)
     read from the plan name, with examples.

Usage
-----
    python src/ca_plan_analysis.py                                   # default input
    python src/ca_plan_analysis.py --input data/interim/c1_plans.csv --examples 5
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

LINE = "=" * 88

# ---------------------------------------------------------------------------
# State detection. Keep these rules identical to c2_filter_california.py so the
# analysis and the filter always agree.
# ---------------------------------------------------------------------------
TARGET = "CA"
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
HIOS_RE = re.compile(r"^\d{5}([A-Z]{2})\d{3}$")
URL_STATE_RE = re.compile(r"[-_]([a-z]{2})_in-network", re.I)
NAME_CA_RE = re.compile(r"^CA\b")          # 'CA L ...' yes, 'CalChoice' no

COLUMN_CANDIDATES = {
    "plan_id": ["plan_id"],
    "plan_name": ["plan_name"],
    "url": ["in_network_file_url", "in_network_url", "rate_file_url", "url_key", "location"],
}


def pick_column(df, role, override):
    if override:
        if override not in df.columns:
            sys.exit(f"column '{override}' not in {list(df.columns)}")
        return override
    for cand in COLUMN_CANDIDATES[role]:
        if cand in df.columns:
            return cand
    sys.exit(f"could not find a {role} column; tried {COLUMN_CANDIDATES[role]}. "
             f"Columns are: {list(df.columns)}")


def hios_state(plan_id):
    m = HIOS_RE.match(str(plan_id).strip().upper())
    return m.group(1) if m and m.group(1) in US_STATES else None


def url_states(url_field):
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
    state = h or u or n or "UNKNOWN"          # HIOS ID > rate-file name > plan name
    return pd.Series({"state": state,
                      "state_evidence": "; ".join(evidence) or "none",
                      "state_conflict": len(signals) > 1})


def network_type(plan_name):
    """Network type from the plan name. 'PPO' is matched as a substring so 'CUSPPO'
    is caught; the others as whole words ('FWP-EPO' counts as EPO)."""
    upper = re.sub(r"\s+", " ", str(plan_name or "")).strip().upper()
    found = ["PPO"] if "PPO" in upper else []
    for token in ("EPO", "HMO", "POS", "EOA"):
        if re.search(rf"(\b|-){token}\b", upper):
            found.append(token)
    return "+".join(found) if found else "UNKNOWN"


def short(url):
    return str(url).replace("\\", "/").rsplit("/", 1)[-1] or "(none)"


def brand_column(df):
    for col in ("brand", "issuer_name", "index_file"):
        if col in df.columns:
            return col
    df["brand"] = "all"
    return "brand"


def analyse_brand(name, g, id_col, name_col, url_col, n_examples):
    print(f"\n{LINE}\n{name}\n{LINE}")
    print(f"plan rows        : {len(g):,}")
    for col in ("plan_market_type", "plan_id_type"):
        if col in g.columns:
            print(f"{col:<17}: {g[col].value_counts().to_dict()}")

    # 1. per rate file
    print("\n-- Rate files ----------------------------------------------------------------")
    for url, fg in g.groupby(url_col, sort=False):
        states = fg["state"].value_counts().to_dict()
        print(f"{len(fg):>6,} plans | states {states}")
        print(f"         {short(url)}")

    # 2. state decision
    print("\n-- State of each plan (HIOS ID > rate-file name > plan name) ------------------")
    print(g["state"].value_counts().to_dict())
    conflicts = g[g["state_conflict"]]
    print(f"plans where signals disagree: {len(conflicts)}")
    for _, r in conflicts.head(n_examples).iterrows():
        print(f"   {r[id_col]:<12} {str(r[name_col])[:50]:<50} {r['state_evidence']}")

    ca = g[g["state"] == TARGET]
    print(f"\n-- {TARGET} plans: {len(ca):,} of {len(g):,} " + "-" * 50)
    if ca.empty:
        return {"brand": name, "rows": len(g), "ca": 0, "ppo": 0}

    print(f"evidence used    : {ca['state_evidence'].value_counts().to_dict()}")
    for col in ("plan_market_type", "plan_id_type", "issuer_name"):
        if col in ca.columns:
            print(f"{col:<17}: {ca[col].value_counts().to_dict()}")
    print(f"unique plan IDs  : {ca[id_col].nunique():,}   "
          f"unique plan names: {ca[name_col].str.lower().nunique():,}")
    print("rate files       :")
    for url, n in ca[url_col].value_counts().items():
        print(f"   {n:>6,} plans -> {short(url)}")

    if "plan_id_type" in ca.columns:
        mis = ca[(ca["plan_id_type"].str.lower() == "ein") & ca[id_col].map(lambda x: hios_state(x) is not None)]
        if len(mis):
            print(f"\n!! {len(mis)} IDs labelled 'ein' are HIOS-shaped "
                  f"(e.g. {', '.join(sorted(mis[id_col].unique())[:4])})")

    # 3. network types
    print(f"\n-- Network type from plan name ({TARGET}) " + "-" * 40)
    ca = ca.assign(network_type=ca[name_col].map(network_type))
    for net, ng in sorted(ca.groupby("network_type"), key=lambda kv: -len(kv[1])):
        print(f"{net:<10} {len(ng):>5,} rows | {ng[id_col].nunique():>4,} unique IDs")
        for ex in sorted(ng[name_col].unique())[:n_examples]:
            print(f"             e.g. {ex}")

    ppo = ca[ca["network_type"].str.contains("PPO")]
    clean = (ca["network_type"] == "PPO").sum()
    print(f"\nPPO candidates (name contains 'PPO'): {len(ppo):,} rows "
          f"(clean 'PPO' only: {clean:,}; mixed with other types: {len(ppo) - clean:,})")
    if "plan_market_type" in ppo.columns:
        print(f"PPO markets: {ppo['plan_market_type'].value_counts().to_dict()}")
    return {"brand": name, "rows": len(g), "ca": len(ca), "ppo": len(ppo)}


def main():
    ap = argparse.ArgumentParser(description="California analysis of c1_plans.csv")
    ap.add_argument("--input", type=Path, default=Path("data/interim/c1_plans.csv"))
    ap.add_argument("--examples", type=int, default=3, help="example plan names per group")
    ap.add_argument("--plan-id-col")
    ap.add_argument("--plan-name-col")
    ap.add_argument("--url-col")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"input not found: {args.input}  (run c1_clean_index.py first, or pass --input)")

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    id_col = pick_column(df, "plan_id", args.plan_id_col)
    name_col = pick_column(df, "plan_name", args.plan_name_col)
    url_col = pick_column(df, "url", args.url_col)
    print(f"read {len(df):,} rows from {args.input}")
    print(f"columns used: plan_id='{id_col}', plan_name='{name_col}', url='{url_col}'")

    df = pd.concat([df, df.apply(decide, axis=1, args=(id_col, name_col, url_col))], axis=1)
    bcol = brand_column(df)

    summaries = [analyse_brand(b, g, id_col, name_col, url_col, args.examples)
                 for b, g in df.groupby(bcol, sort=False)]

    print(f"\n{LINE}\nSUMMARY ({TARGET}, grouped by '{bcol}')\n{LINE}")
    for s in summaries:
        print(f"{str(s['brand'])[:40]:<40} rows {s['rows']:>6,} | {TARGET} {s['ca']:>6,} | PPO by name {s['ppo']:>6,}")
    print(f"{'TOTAL':<40} rows {len(df):>6,} | {TARGET} {sum(s['ca'] for s in summaries):>6,} | "
          f"PPO by name {sum(s['ppo'] for s in summaries):>6,}")


if __name__ == "__main__":
    main()