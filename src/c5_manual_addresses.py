#!/usr/bin/env python3
"""
C5 - add the addresses found by hand for the NPIs no source could map.

The automatic pipeline left 8 (NPI, TIN) pairs without an address (not found in NPPES or
any other source). They were looked up manually; this script writes them into the C5
outputs with score 1.0 / confidence high, validity_note "manual", flag "manual_lookup",
and sources = "manual: <where it was found>".
A deactivated NPI is REMOVED from the C5 outputs (it can no longer bill).

Run it AFTER c5_score_addresses.py (scoring rewrites the files). Safe to re-run.

Updates:
  data/output/c5_npi_tin_address.csv   (+ .xlsx)   final wide file
  data/interim/c5_npi_tin_address_long.csv         long file used by the report and C6

Usage:
    python src/c5_manual_addresses.py
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402
from c5_score_addresses import write_final  # noqa: E402

# npi, tin, business name, address_1, address_2, city, zip, where it was found
MANUAL = [
    ("1053427351", "330057155", "APRIA HEALTHCARE LLC", "410 LONGFELLOW CT", "#A", "LIVERMORE", "94551",
     "manual: Medicare Advantage National DME Provider Directory"),
    ("1437195260", "382084239", "QUEST DIAGNOSTICS INC", "7600 TYRONE AVE", "", "VAN NUYS", "91405",
     "manual: Quest Diagnostics national reference laboratory directory"),
    ("1342464355", "562684458", "HEALTH NET DIRECT FFS COMMERCIAL", "4191 EAST COMMERCE WAY", "", "SACRAMENTO", "95834",
     "manual: Health Net Direct Provider Directory"),
    ("1932154879", "942965646", "LARVIE, M.D.", "2125 OAK GROVE RD", "SUITE 200", "WALNUT CREEK", "94598",
     "manual: https://corporate.ai/entity/fl/f23000004429/bay-imaging-consultants-medical-group-inc"),
    ("1174823165", "953536649", "YAHYA, M.D., INC.", "1334 W COVINA BLVD", "SUITE 202", "SAN DIMAS", "91773",
     "manual: US News Health profiles"),
    ("1750469318", "953942116", "SGHIATTI, M.D.", "6316 HOLMES AVE", "", "LOS ANGELES", "90001",
     "manual: https://npiregistry.cms.hhs.gov/provider-view/1558731943"),
    ("1215962204", "956111785", "PLANNED PARENTHOOD OF THE", "1075 CAMINO DEL RIO SOUTH", "", "SAN DIEGO", "92108",
     "manual: https://npiregistry.cms.hhs.gov/provider-view/1215962204"),
]
# NPIs confirmed deactivated: removed from C5
DEACTIVATED = [
    ("1760410195", "943370791", "manual: https://orthopedic.io/podiatry-clinic/vale-road-podiatry-san-pablo-ca/"),
]


def address_fields(npi, tin, name, a1, a2, city, zip5, source):
    return {"npi": npi, "tin": tin, "business_name": name, "address_1": a1, "address_2": a2,
            "city": city, "state": "CA", "zip": zip5, "census_address": "", "lat": "", "lon": "",
            "score": 1.0, "confidence": "high", "sources": source, "n_sources": 1,
            "A_agreement": "", "V_validity": "", "T_tin_fit": "", "n_tin_npis_here": "",
            "validity_note": "manual", "flags": "manual_lookup", "n_tied_sites": ""}


def main():
    ap = argparse.ArgumentParser(description="C5: add manually found addresses")
    ap.add_argument("--final", default=str(PATHS["output"] / "c5_npi_tin_address.csv"), type=Path)
    ap.add_argument("--long", default=str(PATHS["interim"] / "c5_npi_tin_address_long.csv"), type=Path)
    ap.add_argument("--providers", default=str(PATHS["interim"] / "c5_providers_clean.csv"), type=Path)
    args = ap.parse_args()
    for p in (args.final, args.long):
        if not p.exists():
            sys.exit(f"{p} not found - run c5_score_addresses.py first")

    wide = pd.read_csv(args.final, dtype=str, keep_default_na=False)
    long = pd.read_csv(args.long, dtype=str, keep_default_na=False)
    prov = (pd.read_csv(args.providers, dtype=str, keep_default_na=False)
            if args.providers.exists() else pd.DataFrame(columns=["npi", "tin"]))

    touched = {m[0] for m in MANUAL} | {d[0] for d in DEACTIVATED}
    before_w, before_l = len(wide), len(long)
    wide = wide[~wide["npi"].isin(touched)]                 # re-runnable: drop old versions
    long = long[~long["npi"].isin(touched)]

    new_w, new_l = [], []
    for m in MANUAL:
        npi, tin = m[0], m[1]
        known = prov[prov["npi"] == npi]
        if len(known) and tin not in set(known["tin"]):
            print(f"  ! NPI {npi}: TIN {tin} not in the rate file (file has {sorted(set(known['tin']))})")
        f = address_fields(*m)
        long_row = {**f, "n_name_variants": "1", "entity_type": "", "rank": "1"}
        new_l.append({c: long_row.get(c, "") for c in long.columns})
        wide_row = {"npi": npi, "tin": tin, "business_name": f["business_name"], "n_name_variants": "1",
                    "entity_type": "", "n_candidate_sites": "0", "n_addresses": "1"}
        for k, v in f.items():
            if k not in ("npi", "tin", "business_name"):
                wide_row[f"addr1_{k}"] = v
        new_w.append({c: wide_row.get(c, "") for c in wide.columns})

    wide = pd.concat([wide, pd.DataFrame(new_w)], ignore_index=True).sort_values(["npi", "tin"])
    long = pd.concat([long, pd.DataFrame(new_l)], ignore_index=True).sort_values(["npi", "tin", "rank"])
    long.to_csv(args.long, index=False)
    write_final(wide, args.final)

    print(f"added {len(MANUAL)} manual addresses, removed {len(DEACTIVATED)} deactivated NPI(s)")
    for d in DEACTIVATED:
        print(f"  removed NPI {d[0]} TIN {d[1]} (deactivated; {d[2]})")
    print(f"final file : {before_w:,} -> {len(wide):,} rows   ({args.final})")
    print(f"long file  : {before_l:,} -> {len(long):,} rows   ({args.long})")


if __name__ == "__main__":
    main()