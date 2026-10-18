#!/usr/bin/env python3
"""
C5 report - the numbers to present for C5 (run after c5_score_addresses.py).

1. Coverage        (NPI, TIN) pairs mapped, primary-address confidence
2. Sources         how many NPIs each source covers
3. NPPES check     for NPIs that ALSO have a non-NPPES address (Doctors & Clinicians,
                   CA facility listing, payer directory ...): does NPPES's practice
                   location point to the same site (same number+street+ZIP, or geocoded
                   within 100 m)?  -> the evidence for "NPPES is stale"
4. By record age   the same agreement rate split by how old the NPPES record is
5. Validity        Census result for the primary addresses
6. Examples        a few NPIs where NPPES disagrees with the other sources

Usage:
    python src/c5_report.py --in-dir data/interim --out-dir data/output
    (writes data/output/c5_report.txt and data/interim/c5_nppes_disagreements.csv)
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402
from c5_address_utils import canonical_site_map, haversine_km, normalize_address  # noqa: E402
from c5_score_addresses import parse_date  # noqa: E402

NPPES = {"nppes_location", "nppes_practice_location", "nppes_mailing"}


def main():
    ap = argparse.ArgumentParser(description="C5 summary report")
    ap.add_argument("--in-dir", default=str(PATHS["interim"]))
    ap.add_argument("--out-dir", default=str(PATHS["output"]))
    args = ap.parse_args()
    ind, outd = Path(args.in_dir), Path(args.out_dir)

    prov = pd.read_csv(ind / "c5_providers_clean.csv", dtype=str, keep_default_na=False)
    cand = pd.read_csv(ind / "c5_candidates.csv", dtype=str, keep_default_na=False)
    npi_info = pd.read_csv(ind / "c3_nppes_npi.csv", dtype=str, keep_default_na=False)
    final = pd.read_csv(ind / "c5_npi_tin_address_long.csv", dtype=str, keep_default_na=False)
    geo_path = ind / "c5_geocoded.csv"
    geo = pd.read_csv(geo_path, dtype=str, keep_default_na=False) if geo_path.exists() else None
    latlon = {}
    if geo is not None:
        latlon = {k: (a, o) for k, a, o in zip(geo["norm_key"], geo["lat"], geo["lon"]) if a}
    if "src_lat" in cand.columns:                       # source's own coordinates as fallback
        for k, a, o in zip(cand["norm_key"], cand["src_lat"], cand["src_lon"]):
            if a and o and k not in latlon:
                latlon[k] = (a, o)

    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    # 1 coverage --------------------------------------------------------------------
    pairs = prov[["npi", "tin"]].drop_duplicates()
    prim = final[final["rank"] == "1"]
    out("== 1. Coverage ==")
    out(f"(NPI, TIN) pairs                 : {len(pairs):,}")
    out(f"  mapped to >= 1 address         : {len(prim):,} ({len(prim) / len(pairs):.1%})")
    for c in ("high", "medium", "low"):
        n = int((prim["confidence"] == c).sum())
        out(f"  primary confidence {c:<7}     : {n:,} ({n / len(pairs):.1%})")
    out(f"  pairs with 2-3 addresses kept  : {int((final.groupby(['npi', 'tin']).size() > 1).sum()):,}")
    out(f"  NPIs not found in any source   : {prov['npi'].nunique() - cand['npi'].nunique():,}")

    # 2 sources ---------------------------------------------------------------------
    out("\n== 2. Source coverage (NPIs with >= 1 address from the source) ==")
    fam = cand.assign(family=cand["source"].where(~cand["source"].isin(NPPES), "nppes"))
    total_npi = prov["npi"].nunique()
    for s, n in fam.groupby("family")["npi"].nunique().sort_values(ascending=False).items():
        out(f"  {s:<26} {n:>6,} NPIs ({n / total_npi:.1%})")

    # 3 NPPES vs other sources ------------------------------------------------------
    cand["site"] = [normalize_address(a, b, c, d, z)["site_key"]
                    for a, b, c, d, z in zip(cand["address_1"], cand["address_2"], cand["city"],
                                             cand["state"], cand["zip"])]
    canon = canonical_site_map(cand["site"])
    cand["site"] = cand["site"].map(lambda k: canon.get(k, k))
    loc = cand[cand["source"] == "nppes_location"]
    other = cand[~cand["source"].isin(NPPES)]
    rows = []
    for npi, og in other.groupby("npi"):
        lg = loc[loc["npi"] == npi]
        if lg.empty:
            continue
        l = lg.iloc[0]
        agree = l["site"] in set(og["site"])
        if not agree and l["norm_key"] in latlon:
            la, lo = latlon[l["norm_key"]]
            for k in og["norm_key"]:
                if k in latlon:
                    d = haversine_km(la, lo, *latlon[k])
                    if d is not None and d <= 0.1:
                        agree = True
                        break
        rows.append({"npi": npi, "agree": agree,
                     "nppes_address": f"{l['address_1']} {l['address_2']}, {l['city']} {l['zip'][:5]}".replace("  ", " "),
                     "other_addresses": " || ".join(sorted({f"{r.address_1} {r.address_2}, {r.city} {r.zip[:5]} [{r.source}]"
                                                            .replace("  ", " ") for r in og.itertuples()})),
                     "other_sources": " | ".join(sorted(set(og["source"])))})
    chk = pd.DataFrame(rows)
    out("\n== 3. Does NPPES agree with the other sources? ==")
    if chk.empty:
        out("  no NPI has both an NPPES location and another source")
    else:
        n_dis = int((~chk["agree"]).sum())
        out(f"  NPIs with NPPES + another source : {len(chk):,}")
        out(f"  NPPES location agrees           : {int(chk['agree'].sum()):,} ({chk['agree'].mean():.1%})")
        out(f"  NPPES location DIFFERS          : {n_dis:,} ({n_dis / len(chk):.1%})")
        for s in sorted(set(other["source"])):
            sub = chk[chk["other_sources"].str.split(" | ", regex=False).apply(lambda x: s in x)]
            if len(sub):
                out(f"    vs {s:<30}: differs for {(~sub['agree']).mean():.1%} of {len(sub):,} NPIs")

        # 4 by age
        age = dict(zip(npi_info["npi"], npi_info["last_updated"]))
        def bucket(npi):
            d = parse_date(age.get(npi, ""))
            if d is None:
                return "unknown"
            y = (date.today() - d).days / 365.25
            return "0-3 yrs" if y <= 3 else "3-7 yrs" if y <= 7 else "7+ yrs"
        chk["age"] = chk["npi"].map(bucket)
        out("\n== 4. NPPES disagreement by age of the NPPES record ==")
        for b in ("0-3 yrs", "3-7 yrs", "7+ yrs", "unknown"):
            sub = chk[chk["age"] == b]
            if len(sub):
                out(f"  updated {b:<8}: {len(sub):>5,} NPIs, NPPES differs for {(~sub['agree']).mean():.1%}")
        chk[~chk["agree"]].to_csv(ind / "c5_nppes_disagreements.csv", index=False)

    # 5 validity --------------------------------------------------------------------
    out("\n== 5. Census validation of primary addresses ==")
    for k, n in prim["validity_note"].value_counts().items():
        out(f"  {k:<26} {n:>6,} ({n / len(prim):.1%})")
    out(f"  flagged out_of_state       {int(prim['flags'].str.contains('out_of_state').sum()):>6,}")

    # 6 examples --------------------------------------------------------------------
    if not chk.empty and (~chk["agree"]).any():
        out("\n== 6. Examples where NPPES differs from other sources ==")
        for r in chk[~chk["agree"]].head(5).itertuples():
            out(f"  NPI {r.npi}\n    NPPES : {r.nppes_address}\n    other : {r.other_addresses[:160]}")

    (outd / "c5_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {outd / 'c5_report.txt'}")
    if not chk.empty:
        print(f"wrote {ind / 'c5_nppes_disagreements.csv'}")


if __name__ == "__main__":
    main()