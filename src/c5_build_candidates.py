#!/usr/bin/env python3
"""
C5 step 3 - Collect candidate addresses for every provider from all sources.

Inputs
  --providers   the NPI/TIN list extracted from the rate file. Accepts:
                  * CSV with columns npi, tin (e.g. c5_providers.csv), or
                  * JSON in any shape that contains objects with "npi" and "tin"
                    (list of {npi, tin}, provider_groups, provider_references ...)
  --nppes       c5_nppes_addresses.csv + c5_nppes_npi.csv from c5_nppes_lookup.py
  --dac         (optional) CMS Doctors and Clinicians national file (CSV, downloaded
                from data.cms.gov Provider Data Catalog); filtered to our NPIs
  --facility    (optional) CA Licensed & Certified Healthcare Facility Listing (CSV)
  --source      (optional, repeatable) name=path.csv[@date] for ANY NPI-keyed CSV; the NPI and
                address columns are auto-detected (Medicare billing file, Medi-Cal enrolled
                providers, Open Payments, payer directory export ...)
  --other       (optional, repeatable) name=path.csv for any extra source already in
                the standard columns: npi, address_1, address_2, city, state, zip
                (e.g. payer_directory=data/manual/healthnet_directory_sample.csv)

Outputs (in --out-dir)
  c5_providers_clean.csv        npi, tin, business_name (one row per NPI+TIN)
  c5_candidates.csv             npi, source, source_date, address_1, address_2, city, state, zip, norm_key
  c5_addresses_to_geocode.csv   one row per unique address, ready for c5_census_geocode.py
                                (columns id, street, city, state, zip, norm_key)

Simplest usage - put every source CSV in data/raw/sources and run:
    python src/c5_build_candidates.py --providers data/interim/c5_providers.csv --out-dir data/interim
  (all CSVs in --sources-dir are loaded automatically; source date = file's saved date)

Explicit usage:
    python src/c5_build_candidates.py --providers data/interim/c5_providers.csv \
        --nppes-dir data/interim --dac data/raw/sources/DAC_NationalDownloadableFile.csv \
        --facility data/raw/sources/ca_licensed_facilities.csv --out-dir data/interim
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c5_address_utils import normalize_address  # noqa: E402


# ---------------------------------------------------------------- providers
def _walk(obj, out):
    """Collect (npi, tin, business_name) from any JSON shape."""
    if isinstance(obj, dict):
        if "npi" in obj and "tin" in obj:
            tin = obj["tin"]
            name = ""
            if isinstance(tin, dict):
                name = tin.get("business_name") or ""
                tin = tin.get("value")
            npis = obj["npi"] if isinstance(obj["npi"], list) else [obj["npi"]]
            for n in npis:
                out.append((str(n), re.sub(r"\D", "", str(tin or "")), name))
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)


def load_providers(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        rows = []
        _walk(data, rows)
        df = pd.DataFrame(rows, columns=["npi", "tin", "business_name"])
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        if "business_name" not in df.columns:
            df["business_name"] = ""
        df = df[["npi", "tin", "business_name"]]
    df["npi"] = df["npi"].str.strip()
    df["tin"] = df["tin"].str.replace(r"\D", "", regex=True)
    df = df[df["npi"].str.fullmatch(r"\d{10}")]                      # drop NPI 0 / invalid
    # one readable organisation name per TIN: the most frequent name across all its
    # provider groups; every other name is kept in business_name_variants
    names = (df.assign(business_name=df["business_name"].str.split(r"\s*\|\s*"))
               .explode("business_name"))
    names = names[names["business_name"].str.strip() != ""]
    tin_name = (names.groupby(["tin", "business_name"]).size().reset_index(name="n")
                     .sort_values(["tin", "n"], ascending=[True, False])
                     .drop_duplicates("tin").set_index("tin")["business_name"])
    variants = (names.groupby(["npi", "tin"])["business_name"]
                     .agg(lambda s: " | ".join(sorted(set(s)))))
    df = df[["npi", "tin"]].drop_duplicates().reset_index(drop=True)
    df["business_name"] = df["tin"].map(tin_name).fillna("")
    df["business_name_variants"] = [variants.get((n, t), "") for n, t in zip(df["npi"], df["tin"])]
    df["n_name_variants"] = df["business_name_variants"].map(lambda v: len(v.split(" | ")) if v else 0)
    return df


# ---------------------------------------------------------------- generic loaders
def pick(cols, *cands):
    low = {c.lower(): c for c in cols}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def load_dac(path, npis, date):
    head = pd.read_csv(path, encoding_errors="replace", low_memory=False, nrows=0).columns
    m = {k: pick(head, *v) for k, v in {
        "npi": ["NPI", "npi"],
        "address_1": ["adr_ln_1", "Line 1 Street Address", "adrs_ln_1", "address_line_1"],
        "address_2": ["adr_ln_2", "Line 2 Street Address", "address_line_2"],
        "city": ["City/Town", "cty", "City", "citytown"],
        "state": ["State", "st"],
        "zip": ["ZIP Code", "zip", "Zip Code", "zip_code"],
        "org_name": ["Org_nm", "Facility Name", "facility_name"],
    }.items()}
    if not m["npi"] or not m["address_1"] or not m["zip"]:
        sys.exit(f"DAC file columns not recognised: {list(head)}")
    use = [c for c in m.values() if c]
    parts = []
    for chunk in pd.read_csv(path, encoding_errors="replace", low_memory=False, dtype=str, keep_default_na=False, usecols=use, chunksize=200_000):
        parts.append(chunk[chunk[m["npi"]].isin(npis)])
    df = pd.concat(parts) if parts else pd.DataFrame(columns=use)
    out = pd.DataFrame({k: df[v] if v else "" for k, v in m.items() if k != "org_name"})
    out["source"], out["source_date"] = "cms_doctors_clinicians", date
    return out


def load_facility(path, npis, date):
    df = pd.read_csv(path, encoding_errors="replace", low_memory=False, dtype=str, keep_default_na=False)
    m = {k: pick(df.columns, *v) for k, v in {
        "npi": ["NPI", "npi", "FAC_NPI", "FACILITY_NPI", "NPI_NUMBER"],
        "address_1": ["ADDRESS", "Address", "FACILITY_ADDRESS", "address_1", "DBA_ADDRESS1",
                      "ADDRESS1", "STREET_ADDRESS"],
        "city": ["CITY", "City", "DBA_CITY", "FACILITY_CITY"],
        "zip": ["ZIP", "ZIP_CODE", "Zip", "zip", "DBA_ZIP_CODE", "ZIP9", "FACILITY_ZIP"],
    }.items()}
    if not m["npi"] or not m["address_1"]:
        sys.exit("facility file columns not recognised (need an NPI and an address column).\n"
                 f"columns found: {list(df.columns)}")
    df[m["npi"]] = df[m["npi"]].str.replace(r"\.0$", "", regex=True).str.strip()
    df = df[df[m["npi"]].isin(npis)]
    out = pd.DataFrame({k: df[v] if v else "" for k, v in m.items()})
    out["address_2"], out["state"] = "", "CA"
    out["source"], out["source_date"] = "ca_facility_listing", date
    return out


GENERIC_COLS = {
    "npi": ["npi", "NPI", "Rndrng_NPI", "Covered_Recipient_NPI", "FAC_NPI", "FACILITY_NPI",
            "NPI_NUMBER", "NPI Number", "Provider_NPI", "PROVIDER_NPI", "Prvdr_NPI"],
    "address_1": ["address_1", "adr_ln_1", "Rndrng_Prvdr_St1", "Recipient_Primary_Business_Street_Address_Line1",
                  "ADDRESS", "Address", "Address1", "ADDRESS1", "ADDRESS_LINE_1", "Address Line 1",
                  "Street_Address", "STREET_ADDRESS", "Provider_Address", "PROVIDER_ADDRESS", "DBA_ADDRESS1",
                  "Service_Address", "SERVICE_ADDRESS", "Practice_Address"],
    "address_2": ["address_2", "adr_ln_2", "Rndrng_Prvdr_St2", "Recipient_Primary_Business_Street_Address_Line2",
                  "Address2", "ADDRESS2", "ADDRESS_LINE_2", "Address Line 2"],
    "city": ["city", "cty", "City/Town", "Rndrng_Prvdr_City", "Recipient_City", "CITY", "City",
             "Provider_City", "PROVIDER_CITY", "DBA_CITY", "Service_City"],
    "state": ["state", "st", "Rndrng_Prvdr_State_Abrvtn", "Recipient_State", "STATE", "State",
              "Provider_State", "PROVIDER_STATE", "Service_State"],
    "src_lat": ["Latitude", "latitude", "LATITUDE", "lat", "LAT"],
    "src_lon": ["Longitude", "longitude", "LONGITUDE", "lon", "LON", "lng"],
    "zip": ["zip", "ZIP Code", "Rndrng_Prvdr_Zip5", "Recipient_Zip_Code", "ZIP", "ZIP_CODE", "Zip",
            "Zip_Code", "Zip Code", "Provider_Zip", "PROVIDER_ZIP", "DBA_ZIP_CODE", "Service_Zip", "ZIP9"],
}


def fuzzy_pick(cols, field):
    """Fallback column match by keywords (e.g. 'Service Address Line 1', 'Prov_Addr_Zip').
    Mailing / billing / pay-to columns are avoided; service / practice / location preferred."""
    norm = {c: re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in cols}
    bad = ("mail", "billing", "payto", "remit", "owner", "corresp")
    want = {"npi": [("npi",)],
            "address_1": [("address", "1"), ("addr", "1"), ("address", "line1"), ("street",), ("address",), ("addr",)],
            "address_2": [("address", "2"), ("addr", "2")],
            "city": [("city",)], "state": [("state",)], "zip": [("zip",), ("postal",)]}[field]
    ok = [c for c in cols if not any(b in norm[c] for b in bad)]
    if field == "address_1":
        ok = [c for c in ok if not re.search(r"(line)?2$", norm[c])]
    if field == "state":
        ok = [c for c in ok if "statement" not in norm[c]]
    if field == "npi":
        ok = [c for c in ok if not any(x in norm[c] for x in ("count", "type", "name"))]
    for keys in want:
        hits = [c for c in ok if all(k in norm[c] for k in keys)]
        if hits:
            pref = [c for c in hits if any(p in norm[c] for p in ("serv", "pract", "loc", "prov"))]
            return (pref or hits)[0]
    return None


def load_generic(name, path, npis, date, strict=True):
    """Any CSV keyed by NPI: column names are auto-detected (see GENERIC_COLS).
    Large files are read in chunks and filtered to our NPIs."""
    head = pd.read_csv(path, encoding_errors="replace", low_memory=False, nrows=0).columns
    m = {k: pick(head, *v) for k, v in GENERIC_COLS.items()}
    for k in m:
        if not m[k] and k not in ("src_lat", "src_lon"):
            m[k] = fuzzy_pick(head, k)
    if not m["npi"] or not m["address_1"]:
        msg = f"[{name}] could not find an NPI and an address column.\ncolumns found: {list(head)}"
        if strict:
            sys.exit(msg)
        print("  SKIPPED " + msg)
        return None
    use = [c for c in m.values() if c]
    parts = []
    for chunk in pd.read_csv(path, encoding_errors="replace", low_memory=False, dtype=str, keep_default_na=False, usecols=use, chunksize=200_000):
        chunk[m["npi"]] = chunk[m["npi"]].str.replace(r"\.0$", "", regex=True).str.strip()
        parts.append(chunk[chunk[m["npi"]].isin(npis)])
    df = pd.concat(parts) if parts else pd.DataFrame(columns=use)
    out = pd.DataFrame({k: df[v].values if v else "" for k, v in m.items()})
    out["source"], out["source_date"] = name, date
    print(f"  {name:<20}: {len(out):,} addresses for {out['npi'].nunique():,} NPIs"
          f"   (columns used: {', '.join(f'{k}={v}' for k, v in m.items() if v)})")
    return out


def file_date(path):
    """Date the file was saved (download date) - used as source_date when none is given."""
    from datetime import datetime
    return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d")


def discover_sources(folder, npis):
    """Load every .csv in `folder`. Recognises the CMS Doctors & Clinicians file and the CA
    facility listing; any other CSV is loaded with auto-detected columns and named after
    its file. Files whose columns cannot be recognised are skipped with a message."""
    frames = []
    files = sorted(Path(folder).glob("*.csv"))
    print(f"  found {len(files)} csv file(s) in {Path(folder).resolve()}")
    if not files:
        print(f"  (no .csv files found in {folder})")
    for f in files:
        d = file_date(f)
        head = [c.lower() for c in pd.read_csv(f, encoding_errors="replace", low_memory=False, nrows=0).columns]
        name_l = f.stem.lower()
        try:
            if "adr_ln_1" in head and "npi" in head:
                fr = load_dac(f, npis, d)
                print(f"  Doctors & Clinicians: {len(fr):,} addresses for {fr['npi'].nunique():,} NPIs  [{f.name}]")
            elif "facilit" in name_l:
                fr = load_facility(f, npis, d)
                print(f"  CA facility listing : {len(fr):,} addresses for {fr['npi'].nunique():,} NPIs  [{f.name}]")
            else:
                fr = load_generic(name_l, f, npis, d, strict=False)
        except (SystemExit, Exception) as e:
            print(f"  SKIPPED {f.name}: {e}")
            fr = None
        if fr is not None and len(fr):
            frames.append(fr)
    return frames


def main():
    ap = argparse.ArgumentParser(description="C5: build candidate addresses from all sources")
    ap.add_argument("--providers", required=True)
    ap.add_argument("--nppes-dir", default="data/interim")
    ap.add_argument("--dac")
    ap.add_argument("--dac-date", default="", help="release date of the DAC file, YYYY-MM-DD")
    ap.add_argument("--facility")
    ap.add_argument("--facility-date", default="")
    ap.add_argument("--other", action="append", default=[], help="name=path.csv (standard columns)")
    ap.add_argument("--source", action="append", default=[],
                    help="name=path.csv[@YYYY-MM-DD]  any NPI-keyed CSV; columns auto-detected "
                         "(e.g. medicare_billing=data/raw/sources/MUP_PHY_by_provider.csv@2024-12-31)")
    ap.add_argument("--sources-dir", default="data/raw/sources",
                    help="folder scanned for source CSVs when no --dac/--facility/--source is given")
    ap.add_argument("--out-dir", default="data/interim")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prov = load_providers(args.providers)
    npis = set(prov["npi"])
    prov.to_csv(out_dir / "c5_providers_clean.csv", index=False)
    print(f"providers: {len(prov):,} (NPI, TIN) pairs, {len(npis):,} NPIs, {prov['tin'].nunique():,} TINs")

    frames = []
    nppes_addr = Path(args.nppes_dir) / "c5_nppes_addresses.csv"
    if nppes_addr.exists():
        a = pd.read_csv(nppes_addr, dtype=str, keep_default_na=False)
        a = a[a["npi"].isin(npis)].rename(columns={"address_source": "source",
                                                   "nppes_last_updated": "source_date"})
        frames.append(a[["npi", "source", "source_date", "address_1", "address_2", "city", "state", "zip"]])
        print(f"  NPPES               : {len(a):,} addresses")
    else:
        print(f"  NPPES               : not found ({nppes_addr}) - run c5_nppes_lookup.py first")
    explicit = bool(args.dac or args.facility or args.source or args.other)
    if not explicit:
        print(f"  scanning {args.sources_dir} for source files ...")
        frames.extend(discover_sources(args.sources_dir, npis))
    if args.dac:
        d = load_dac(args.dac, npis, args.dac_date)
        frames.append(d)
        print(f"  Doctors & Clinicians: {len(d):,} addresses for {d['npi'].nunique():,} NPIs")
    if args.facility:
        f = load_facility(args.facility, npis, args.facility_date)
        frames.append(f)
        print(f"  CA facility listing : {len(f):,} addresses for {f['npi'].nunique():,} NPIs")
    for spec in args.other:
        name, _, path = spec.partition("=")
        o = pd.read_csv(path, dtype=str, keep_default_na=False)
        for c in ("address_2", "state"):
            if c not in o.columns:
                o[c] = ""
        o = o[o["npi"].isin(npis)]
        o["source"] = name
        o["source_date"] = o["source_date"] if "source_date" in o.columns else ""
        frames.append(o[["npi", "source", "source_date", "address_1", "address_2", "city", "state", "zip"]])
        print(f"  {name:<20}: {len(o):,} addresses")

    for spec in args.source:
        name, _, rest = spec.partition("=")
        path, _, sdate = rest.partition("@")
        frames.append(load_generic(name.strip(), path.strip(), npis, sdate.strip() or file_date(path.strip())))

    if not frames:
        sys.exit("no address sources given")
    cand = pd.concat(frames, ignore_index=True).fillna("")
    cand = cand[cand["address_1"].str.strip() != ""]
    norm = cand.apply(lambda r: normalize_address(r["address_1"], r["address_2"], r["city"],
                                                  r["state"], r["zip"]), axis=1)
    cand["norm_key"] = norm.map(lambda n: n["full_key"])
    cand = cand.drop_duplicates(["npi", "source", "norm_key"]).reset_index(drop=True)
    cand.to_csv(out_dir / "c5_candidates.csv", index=False)

    uniq = cand.drop_duplicates("norm_key").reset_index(drop=True)
    geo = pd.DataFrame({
        "id": range(1, len(uniq) + 1),
        "street": (uniq["address_1"] + " " + uniq["address_2"]).str.strip(),
        "city": uniq["city"], "state": uniq["state"], "zip": uniq["zip"].str[:5],
        "norm_key": uniq["norm_key"],
    })
    geo.to_csv(out_dir / "c5_addresses_to_geocode.csv", index=False)

    print(f"\ncandidates: {len(cand):,} rows, {cand['npi'].nunique():,} NPIs with ≥1 address")
    print(f"  by source: {cand['source'].value_counts().to_dict()}")
    print(f"  NPIs with no address in any source: {len(npis - set(cand['npi'])):,}")
    print(f"unique addresses to geocode: {len(geo):,}")
    print(f"\nwrote {out_dir / 'c5_providers_clean.csv'}\nwrote {out_dir / 'c5_candidates.csv'}"
          f"\nwrote {out_dir / 'c5_addresses_to_geocode.csv'}")


if __name__ == "__main__":
    main()