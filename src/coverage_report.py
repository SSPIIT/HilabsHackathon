#!/usr/bin/env python3
"""
Coverage and linkage report - reads the existing outputs, changes nothing.

Part 1  Funnel C1 -> C6: how many plans, files, providers, rates and addresses survive each
        step, and why anything is dropped (nothing should disappear silently).
Part 2  Linkage: share of NPIs and TINs linked to an address, and to a VERIFIED address.

VERIFIED address = either
    (a) confirmed by >= 2 independent source families (NPPES, Medi-Cal, Doctors & Clinicians,
        CA facility listing ... ; NPPES location + mailing count as one) AND found by the
        Census geocoder (exact or non-exact match), or
    (b) found by manual lookup with a cited source (validity_note = manual).
An NPI / pair counts as verified if ANY of its kept addresses (up to 3) is verified;
"primary" figures use only the rank-1 address.

TIN linkage is reported two ways: TINs where at least one NPI is verified, and TINs where
every NPI is verified.

Usage:
    python src/coverage_report.py
    (writes data/output/coverage_report.txt)
"""

import argparse
import re
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

VERIFIED_CENSUS = {"census_exact", "census_non_exact"}
DEACTIVATED = {"1760410195"}          # confirmed deactivated in C5: not a linkable provider


def load(path):
    p = Path(path)
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False, low_memory=False)


def tin9(s):
    d = re.sub(r"\D", "", str(s or ""))
    return d.zfill(9) if d else ""


def main():
    ap = argparse.ArgumentParser(description="Coverage and linkage report")
    ap.add_argument("--interim", default=str(PATHS["interim"]), type=Path)
    ap.add_argument("--output", default=str(PATHS["output"]), type=Path)
    args = ap.parse_args()
    I, O = args.interim, args.output
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    def pct(a, b):
        return f"{a:,} / {b:,} ({a / b:.1%})" if b else f"{a:,} / 0"

    # ======================= Part 1: funnel =========================================
    out("== Part 1. Funnel: in-scope data from index to final file ==")
    c1 = load(I / "c1_plans.csv")
    c2ca = load(I / "c2_plans_ca.csv")
    c2st = load(I / "c2_name_status_final.csv")
    ppo = load(O / "c2_ppo_plans_ca.csv")
    if ppo is None:
        ppo = load(I / "c2_ppo_plans_ca.csv")
    if c1 is not None:
        out(f"C1  Centene index rows (all brands)          : {len(c1):,}")
    if c2ca is not None:
        out(f"C2  California rows                          : {len(c2ca):,}")
    if c2st is not None and "ppo_status" in c2st.columns:
        vc = c2st["ppo_status"].value_counts().to_dict()
        out(f"C2  PPO decision on CA rows                  : {vc}")
    if ppo is not None:
        url_col = next((c for c in ppo.columns if "url" in c.lower()), None)
        id_col = next((c for c in ppo.columns if c.lower() == "plan_id"), None)
        out(f"C2  PPO group plans (rows)                   : {len(ppo):,}"
            + (f", {ppo[id_col].nunique():,} plan IDs" if id_col else ""))
        if url_col:
            urls = ppo[url_col].str.replace(r"\?.*$", "", regex=True)
            out(f"C2  distinct rate files those plans point to : {urls.nunique():,}")
            for u, n in urls.value_counts().head(5).items():
                out(f"      {n:>4} rows -> {u.split('/')[-1]}")

    raw = load(I / "c3_providers.csv")
    prov = load(I / "c5_providers_clean.csv")
    if raw is not None:
        raw["tin"] = raw["tin"].map(tin9)
        out(f"C3  (NPI, TIN) pairs in the rate file        : {len(raw[['npi', 'tin']].drop_duplicates()):,} "
            f"({raw['npi'].nunique():,} NPIs, {raw['tin'].nunique():,} TINs)")
        if "npi_valid" in raw.columns:
            bad = raw[raw["npi_valid"].str.lower() != "true"]
            out(f"      of which NPI fails the check digit / is a placeholder: {bad['npi'].nunique():,} "
                f"{sorted(bad['npi'].unique())[:5]}")

    ext = load(I / "c4_rates_extracted.csv") if (I / "c4_rates_extracted.csv").exists() else None
    if ext is not None:
        out(f"C4  extracted price rows                     : {len(ext):,} "
            f"({ext['npi'].nunique():,} NPIs, {ext['billing_code'].nunique():,} codes)")
        del ext
    c4 = load(O / "c4_rates.csv")
    if c4 is None:
        c4 = load(I / "c4_rates.csv")
    if c4 is not None:
        c4["tin"] = c4["tin"].map(tin9)
        out(f"C4  provider + service rates (one per key)   : {len(c4):,} "
            f"({c4['npi'].nunique():,} NPIs, {len(c4[['npi', 'tin']].drop_duplicates()):,} NPI+TIN)")
        bad = c4[~c4["npi"].str.fullmatch(r"\d{10}")]
        if len(bad):
            out(f"      rates with placeholder NPI (e.g. 0)    : {len(bad):,} rows, {bad['tin'].nunique():,} TINs "
                "-> cannot be placed at NPI level")

    long = load(I / "c5_npi_tin_address_long.csv")
    if long is not None:
        long["tin"] = long["tin"].map(tin9)
        out(f"C5  (NPI, TIN) pairs with >= 1 address       : {len(long[['npi', 'tin']].drop_duplicates()):,} "
            f"({len(long):,} addresses kept)")
    c6 = load(O / "c6_provider_rate_file.csv")
    if c6 is not None:
        out(f"C6  final rows (NPI x TIN x address x code)  : {len(c6):,} "
            f"({c6['NPI'].nunique():,} NPIs, {len(c6[['NPI', 'TIN']].drop_duplicates()):,} NPI+TIN, "
            f"{c6['Billing Code'].nunique():,} codes)")
        out(f"      rows with an address / specialty       : {pct(int((c6['Address'] != '').sum()), len(c6))} / "
            f"{pct(int((c6['Specialty'] != '').sum()), len(c6))}")

    # pairs lost between the rate file and the final file
    if raw is not None and c6 is not None:
        start = raw[["npi", "tin"]].drop_duplicates()
        end = c6[["NPI", "TIN"]].drop_duplicates().rename(columns={"NPI": "npi", "TIN": "tin"})
        end["tin"] = end["tin"].map(tin9)
        lost = start.merge(end, on=["npi", "tin"], how="left", indicator=True)
        lost = lost[lost["_merge"] == "left_only"]
        out(f"\n(NPI, TIN) pairs in the rate file but not in C6: {len(lost):,}")
        for r in lost.head(12).itertuples():
            why = ("placeholder NPI" if not re.fullmatch(r"\d{10}", r.npi)
                   else "deactivated NPI" if r.npi in DEACTIVATED
                   else "no rate kept / check")
            out(f"  NPI {r.npi:<12} TIN {r.tin}  -> {why}")

    # ======================= Part 2: linkage ========================================
    out("\n== Part 2. Linkage to a (verified) address ==")
    if long is None:
        out("c5_npi_tin_address_long.csv not found - run C5 first")
    else:
        nppes = load(I / "c3_nppes_npi.csv")
        if nppes is None:
            nppes = load(I / "c5_nppes_npi.csv")
        etype = dict(zip(nppes["npi"], nppes["entity_type"])) if nppes is not None else {}

        n_src = pd.to_numeric(long["n_sources"], errors="coerce").fillna(0)
        long["verified"] = (((n_src >= 2) & long["validity_note"].isin(VERIFIED_CENSUS))
                            | (long["validity_note"] == "manual"))
        long["high"] = long["confidence"] == "high"
        long["primary"] = long["rank"].astype(str).str.replace(".0", "", regex=False) == "1"

        # universe = every valid (NPI, TIN) in the rate file (or C5 providers if C3 file missing)
        base = raw if raw is not None else prov
        uni = base[["npi", "tin"]].drop_duplicates()
        uni["tin"] = uni["tin"].map(tin9)
        uni = uni[uni["npi"].str.fullmatch(r"\d{10}") & ~uni["npi"].isin(DEACTIVATED)]
        out(f"universe: {len(uni):,} (NPI, TIN) pairs with a real, active NPI "
            f"(placeholder NPIs and {len(DEACTIVATED)} deactivated NPI excluded)")
        uni["etype"] = uni["npi"].map(etype).fillna("").replace({"1": "Type 1 (individual)",
                                                                  "2": "Type 2 (organization)",
                                                                  "": "unknown"})

        pair = long.groupby(["npi", "tin"]).agg(
            any_addr=("address_1", lambda s: (s != "").any()),
            any_verified=("verified", "any"), any_high=("high", "any"),
            prim_verified=("verified", lambda s: bool(s[long.loc[s.index, "primary"]].any())),
        ).reset_index()
        u = uni.merge(pair, on=["npi", "tin"], how="left").fillna(False)

        out("\nVERIFIED = >= 2 independent sources + Census match, or manual lookup with a cited source\n")
        out(f"{'level':<28} {'with address':>22} {'verified (any addr)':>24} {'verified (primary)':>22}")

        def row(label, df, key):
            g = df.groupby(key).agg(a=("any_addr", "any"), v=("any_verified", "any"),
                                    p=("prim_verified", "any"))
            n = len(g)
            out(f"{label:<28} {pct(int(g['a'].sum()), n):>22} {pct(int(g['v'].sum()), n):>24} "
                f"{pct(int(g['p'].sum()), n):>22}")

        row("NPI + TIN pairs", u, ["npi", "tin"])
        row("NPIs (all)", u, "npi")
        for et in ("Type 1 (individual)", "Type 2 (organization)", "unknown"):
            sub = u[u["etype"] == et]
            if len(sub):
                row(f"  NPIs {et}", sub, "npi")
        row("TINs (>= 1 NPI)", u, "tin")
        tin_all = u.groupby("tin").agg(v=("any_verified", "all"), a=("any_addr", "all"))
        out(f"{'TINs (every NPI)':<28} {pct(int(tin_all['a'].sum()), len(tin_all)):>22} "
            f"{pct(int(tin_all['v'].sum()), len(tin_all)):>24}")

        out("\nwhy addresses are not verified (kept addresses):")
        nv = long[~long["verified"]]
        reason = pd.Series("other", index=nv.index)
        nsrc = pd.to_numeric(nv["n_sources"], errors="coerce").fillna(0)
        reason[nsrc < 2] = "single source"
        reason[(nsrc >= 2) & ~nv["validity_note"].isin(VERIFIED_CENSUS)] = "2+ sources, Census no match / tie"
        for r, n in reason.value_counts().items():
            out(f"  {r:<36} {n:>7,} ({n / len(long):.1%} of kept addresses)")
        single = nv[nsrc < 2]
        if len(single):
            out("  single-source addresses by source:")
            for s, n in single["sources"].str.split(" | ", regex=False).str[0].value_counts().head(6).items():
                out(f"    {s:<34} {n:>7,}")

    path = O / "coverage_report.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()