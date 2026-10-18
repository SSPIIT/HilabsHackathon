#!/usr/bin/env python3
"""
C6 - Build the provider rate file: one row per NPI x TIN x billing code (service) x address.

Inputs (data/output):
  c4_rates.csv              one kept rate per NPI + TIN + service (C4)
  c5_npi_tin_address.csv    up to 3 ranked addresses per NPI + TIN (C5, wide: addr1_/addr2_/addr3_)

How it is built:
  1. C5 wide file -> one row per (NPI, TIN, address rank)
  2. TINs are compared as 9-digit strings (leading zeros restored: 50528655 -> 050528655),
     because Excel / some sources drop them
  3. every C4 rate row is joined to every address of its (NPI, TIN):
        rate rows x addresses  (a doctor with 7 services at 2 addresses = 14 rows)
  4. rows that cannot be placed:
        NPI 0 / not 10 digits   -> excluded (a placeholder, not a provider), counted
        NPI + TIN with no address in C5 -> kept, address blank, address_confidence "unmapped"
        NPIs listed in DEACTIVATED -> excluded

Output: data/output/c6_provider_rate_file.csv - one row per NPI, TIN, Address, Billing Code:
  NPI, TIN, Address, Billing Code, Specialty, Rate
    Address      = "street, city, CA zip" of each C5 address (up to 3 per NPI + TIN)
    Billing Code = the code; revenue codes add the HCPCS item ("636 J1885"); a modifier is
                   appended ("99213-25")
    Specialty    = NPPES primary taxonomy (from the C5 NPPES lookup)
    Rate         = the C4 kept rate
  If a code still has several variants for one NPI/TIN/address (setting, billing class,
  arrangement), one is kept: V1 fee-for-service first, V2 office (non-facility) > facility >
  institutional, V3 professional first, V4 lowest rate.

Usage:
    python src/c6_build_rate_file.py
    python src/c6_build_rate_file.py --min-confidence high     # only high-confidence addresses
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

DEACTIVATED = {"1760410195"}
# NUCC taxonomy names for codes NPPES returned without a description
TAXONOMY_NAMES = {
    "2086S0129X": "Surgery, Vascular Surgery",
    "208VP0000X": "Pain Medicine, Pain Medicine",
    "213ES0131X": "Podiatrist, Foot Surgery",
    "2085N0700X": "Radiology, Neuroradiology",
    "2085R0001X": "Radiology, Radiation Oncology",
    "2084N0600X": "Psychiatry & Neurology, Clinical Neurophysiology",
}
# specialty for NPIs mapped by hand in C5 (not returned by NPPES), from the manual lookup
MANUAL_SPECIALTY = {
    "1053427351": "Durable Medical Equipment & Medical Supplies",   # Apria Healthcare
    "1437195260": "Clinical Medical Laboratory",                    # Quest Diagnostics
    "1215962204": "Clinic/Center",                                  # Planned Parenthood
    "1932154879": "Radiology",                                      # Bay Imaging Consultants
}          # confirmed deactivated (manual check in C5)
ADDR_FIELDS = ["address_1", "address_2", "city", "state", "zip", "lat", "lon",
               "score", "confidence", "sources", "flags"]
RENAME_ADDR = {"score": "address_score", "confidence": "address_confidence",
               "sources": "address_sources", "flags": "address_flags"}
SERVICE = ["billing_code_type", "billing_code", "code_detail", "description", "modifiers",
           "billing_class", "pos_group", "price_setting", "negotiation_arrangement"]
RATE = {"negotiated_rate": "negotiated_rate", "negotiated_type": "negotiated_type",
        "expiration_date": "expiration_date", "kept_rule": "rate_kept_rule", "flag": "rate_flag",
        "n_distinct_amounts": "rate_n_distinct_amounts", "min_rate": "rate_min", "max_rate": "rate_max"}
CONF_ORDER = {"high": 3, "medium": 2, "low": 1}


def tin9(s):
    d = re.sub(r"\D", "", str(s or ""))
    return d.zfill(9) if d else ""


def main():
    ap = argparse.ArgumentParser(description="C6: provider rate file at NPI x TIN x code x address level")
    ap.add_argument("--rates", default=str(PATHS["output"] / "c4_rates.csv"), type=Path)
    ap.add_argument("--addresses", default=str(PATHS["output"] / "c5_npi_tin_address.csv"), type=Path)
    ap.add_argument("--nppes", default=str(PATHS["interim"] / "c3_nppes_npi.csv"), type=Path,
                    help="NPPES lookup from C5 - gives the specialty (primary taxonomy)")
    ap.add_argument("--out", default=str(PATHS["output"] / "c6_provider_rate_file.csv"), type=Path)
    ap.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low",
                    help="keep only addresses at or above this confidence (default: all)")
    args = ap.parse_args()
    for p in (args.rates, args.addresses):
        if not p.exists():
            sys.exit(f"{p} not found")

    rates = pd.read_csv(args.rates, dtype=str, keep_default_na=False)
    wide = pd.read_csv(args.addresses, dtype=str, keep_default_na=False)
    print(f"C4 rates     : {len(rates):,} rows")
    print(f"C5 addresses : {len(wide):,} (NPI, TIN) rows")

    # ---- 1. C5 wide -> long --------------------------------------------------------
    parts = []
    for k in (1, 2, 3):
        cols = {f"addr{k}_{f}": f for f in ADDR_FIELDS if f"addr{k}_{f}" in wide.columns}
        if not cols:
            continue
        part = wide[["npi", "tin", "business_name", "entity_type"] + list(cols)].rename(columns=cols)
        part = part[part["address_1"].str.strip() != ""]
        part["address_rank"] = k
        parts.append(part)
    addr = pd.concat(parts, ignore_index=True).rename(columns=RENAME_ADDR)
    addr["tin"] = addr["tin"].map(tin9)
    keep_rank = CONF_ORDER[args.min_confidence]
    addr = addr[addr["address_confidence"].map(lambda c: CONF_ORDER.get(c, 0)) >= keep_rank]

    # ---- 2. clean the rate side ----------------------------------------------------
    rates["tin"] = rates["tin"].map(tin9)
    bad_npi = ~rates["npi"].str.fullmatch(r"\d{10}") | (rates["npi"] == "0000000000")
    n_bad, tins_bad = int(bad_npi.sum()), rates.loc[bad_npi, "tin"].nunique()
    deact = rates["npi"].isin(DEACTIVATED)
    n_deact = int(deact.sum())
    rates = rates[~bad_npi & ~deact]

    # ---- 3. join every rate to every address of its NPI + TIN -----------------------
    keep_rate = ["npi", "tin"] + SERVICE + list(RATE)
    r = rates[[c for c in keep_rate if c in rates.columns]].rename(columns=RATE)
    out = r.merge(addr, on=["npi", "tin"], how="left")
    unm = out["address_1"].isna()
    out.loc[unm, "address_confidence"] = "unmapped"
    out["address_rank"] = out["address_rank"].map(lambda x: "" if pd.isna(x) else str(int(x)))
    out = out.fillna("")

    order = (["npi", "tin", "business_name", "entity_type"] + SERVICE + list(RATE.values()) +
             ["address_rank", "address_1", "address_2", "city", "state", "zip", "lat", "lon",
              "address_score", "address_confidence", "address_sources", "address_flags"])
    out = out[[c for c in order if c in out.columns]]
    out = out.sort_values(["npi", "tin", "billing_code_type", "billing_code", "code_detail", "address_rank"])

    # ---- 4. final format: NPI, TIN, Address, Billing Code, Specialty, Rate ------------
    spec = {}
    if not args.nppes.exists() and (PATHS["interim"] / "c5_nppes_npi.csv").exists():
        args.nppes = PATHS["interim"] / "c5_nppes_npi.csv"        # older name
    if args.nppes.exists():
        n = pd.read_csv(args.nppes, dtype=str, keep_default_na=False)
        # NPPES sometimes returns the taxonomy code without its description: fill the name
        # from other NPPES rows with the same code, then from the NUCC names below
        code_name = {c: d for c, d in zip(n["primary_taxonomy_code"], n["primary_taxonomy_desc"]) if c and d}
        code_name = {**TAXONOMY_NAMES, **code_name}
        for npi, code, desc in zip(n["npi"], n["primary_taxonomy_code"], n["primary_taxonomy_desc"]):
            spec[npi] = desc or code_name.get(code, "")
    else:
        print(f"  ! {args.nppes} not found - Specialty left blank")
    for npi, sp in MANUAL_SPECIALTY.items():          # hand-mapped NPIs not in NPPES
        if not spec.get(npi):
            spec[npi] = sp

    # Billing Code: the code, + the HCPCS for revenue codes (636 J1885), + modifier (99213-25)
    def code_label(r):
        c = r["billing_code"]
        if r["code_detail"]:
            c = f"{c} {r['code_detail']}"
        if r["modifiers"]:
            c = f"{c}-{r['modifiers'].replace('|', '-')}"
        return c
    out["Billing Code"] = out.apply(code_label, axis=1)

    def addr_label(r):
        if not r["address_1"]:
            return ""
        street = " ".join(x for x in (r["address_1"].strip(), r["address_2"].strip()) if x)
        st_zip = f"{r['state']} {str(r['zip'])[:5]}".strip()
        return ", ".join(x for x in (street, r["city"].strip(), st_zip) if x)
    out["Address"] = out.apply(addr_label, axis=1)

    # One row per NPI, TIN, Address, Billing Code: when a code still has several variants
    # (setting / billing class / arrangement), keep one by a fixed rule:
    #   V1 fee-for-service before bundle / capitation
    #   V2 setting: non_facility (office) > facility > institutional > mixed > unspecified
    #   V3 professional before institutional
    #   V4 lowest rate
    out["_v1"] = (out["negotiation_arrangement"] != "ffs").astype(int)
    out["_v2"] = out["pos_group"].map({"non_facility": 0, "facility": 1, "institutional": 2,
                                       "mixed": 3}).fillna(4)
    out["_v3"] = (out["billing_class"] != "professional").astype(int)
    out["_rate"] = pd.to_numeric(out["negotiated_rate"], errors="coerce")
    key = ["npi", "tin", "Address", "Billing Code"]
    n_before = len(out)
    out = out.sort_values(key + ["_v1", "_v2", "_v3", "_rate"])
    n_variants = int(out.duplicated(key).sum())
    final = out.drop_duplicates(key)

    final = pd.DataFrame({
        "NPI": final["npi"], "TIN": final["tin"], "Address": final["Address"],
        "Billing Code": final["Billing Code"], "Specialty": final["npi"].map(spec).fillna(""),
        "Rate": final["negotiated_rate"],
    }).sort_values(["NPI", "TIN", "Billing Code", "Address"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        final.to_csv(args.out, index=False)
    except PermissionError:
        sys.exit(f"cannot write {args.out} - close it in Excel and re-run")

    # ---- summary ---------------------------------------------------------------------
    print(f"\nexcluded: {n_bad:,} rate rows with NPI 0 / invalid NPI ({tins_bad:,} TINs) - placeholder, not a provider")
    print(f"excluded: {n_deact:,} rate rows for deactivated NPIs {sorted(DEACTIVATED)}")
    print(f"code variants collapsed by V1-V4 : {n_variants:,} of {n_before:,} rows")
    print(f"\nC6 rows (NPI x TIN x Address x Billing Code): {len(final):,}")
    print(f"  NPIs {final['NPI'].nunique():,}, NPI+TIN {len(final[['NPI', 'TIN']].drop_duplicates()):,}, "
          f"billing codes {final['Billing Code'].nunique():,}")
    print(f"  rows without an address        : {int((final['Address'] == '').sum()):,}")
    print(f"  rows without a specialty       : {int((final['Specialty'] == '').sum()):,}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()