#!/usr/bin/env python3
"""
C3 step - Look up every provider NPI from the rate file in the NPPES NPI Registry API.

Only the NPIs in c3_providers.csv (all providers in the Health Net CA rate file) are
looked up - no full NPPES download. The API is free and needs no key; run this on a
machine with internet access.
Used by C3 (R2 provider validity) and C5 (NPPES candidate addresses), so it runs in C3.

Outputs
  c3_nppes_npi.csv        one row per NPI:
      npi, found, entity_type (1 individual / 2 organization), name, status,
      enumeration_date, last_updated, primary_taxonomy_code, primary_taxonomy_desc,
      n_addresses
  c3_nppes_addresses.csv  one row per NPI x address (the candidate addresses for C5):
      npi, address_source (nppes_location / nppes_mailing / nppes_practice_location),
      address_1, address_2, city, state, zip, phone, nppes_last_updated

Re-running is safe: NPIs already in c3_nppes_npi.csv are skipped (resume after a stop).

Usage:
    pip install requests pandas
    python src/c3_nppes_lookup.py --input data/interim/c3_providers.csv --out-dir data/interim
"""

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

API = "https://npiregistry.cms.hhs.gov/api/"


def safe_save(df, path):
    """Save a CSV; if the file is open in Excel (locked), wait and retry instead of crashing."""
    for attempt in range(60):
        try:
            df.to_csv(path, index=False)
            return
        except PermissionError:
            if attempt == 0:
                print(f"\n  !! cannot write {path} - is it open in Excel? Close it; retrying every 10 s ...")
            time.sleep(10)
    raise SystemExit(f"could not write {path} after 10 minutes - close it and re-run (progress is kept)")


def fetch(npi, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(API, params={"version": "2.1", "number": npi}, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries:
                print(f"    {npi}: failed after {retries} tries ({e})")
                return None
            time.sleep(3 * attempt)


def parse(npi, data):
    """Return (npi_row, [address_rows]) from one API response."""
    row = {"npi": npi, "found": False, "entity_type": "", "name": "", "status": "",
           "enumeration_date": "", "last_updated": "", "primary_taxonomy_code": "",
           "primary_taxonomy_desc": "", "n_addresses": 0}
    addrs = []
    results = (data or {}).get("results") or []
    if not results:
        return row, addrs
    res = results[0]
    basic = res.get("basic") or {}
    etype = res.get("enumeration_type", "")
    row.update({
        "found": True,
        "entity_type": "1" if etype == "NPI-1" else "2" if etype == "NPI-2" else etype,
        "name": basic.get("organization_name")
                or " ".join(x for x in (basic.get("first_name"), basic.get("middle_name"),
                                        basic.get("last_name")) if x),
        "status": basic.get("status", ""),
        "enumeration_date": basic.get("enumeration_date", ""),
        "last_updated": basic.get("last_updated", ""),
    })
    taxes = res.get("taxonomies") or []
    prim = next((t for t in taxes if t.get("primary")), taxes[0] if taxes else {})
    row["primary_taxonomy_code"] = prim.get("code", "")
    row["primary_taxonomy_desc"] = prim.get("desc", "")

    def add(a, source):
        addrs.append({
            "npi": npi,
            "address_source": source,
            "address_1": (a.get("address_1") or "").strip(),
            "address_2": (a.get("address_2") or "").strip(),
            "city": (a.get("city") or "").strip(),
            "state": (a.get("state") or "").strip(),
            "zip": re.sub(r"\D", "", a.get("postal_code") or "")[:9],
            "phone": a.get("telephone_number", ""),
            "nppes_last_updated": row["last_updated"],
        })

    for a in res.get("addresses") or []:
        purpose = (a.get("address_purpose") or "").upper()
        add(a, "nppes_location" if purpose == "LOCATION" else "nppes_mailing")
    for a in res.get("practiceLocations") or []:
        add(a, "nppes_practice_location")
    row["n_addresses"] = len(addrs)
    return row, addrs


def main():
    ap = argparse.ArgumentParser(description="NPPES API lookup for the rate-file NPIs")
    ap.add_argument("--input", default="data/interim/c3_providers.csv", type=Path)
    ap.add_argument("--out-dir", default="data/interim", type=Path)
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between requests")
    ap.add_argument("--limit", type=int, default=0, help="only look up the first N NPIs (for testing)")
    args = ap.parse_args()

    prov = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    if "npi" not in prov.columns:
        sys.exit(f"'npi' column missing in {args.input}")
    npis = sorted({n for n in prov["npi"] if re.fullmatch(r"\d{10}", n)})
    skipped = prov["npi"].nunique() - len(npis)
    print(f"{len(npis):,} valid unique NPIs to look up ({skipped} placeholder/invalid skipped)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    npi_path = args.out_dir / "c3_nppes_npi.csv"
    addr_path = args.out_dir / "c3_nppes_addresses.csv"
    done_rows = pd.read_csv(npi_path, dtype=str, keep_default_na=False) if npi_path.exists() else pd.DataFrame()
    done_addr = pd.read_csv(addr_path, dtype=str, keep_default_na=False) if addr_path.exists() else pd.DataFrame()
    done = set(done_rows.get("npi", []))
    todo = [n for n in npis if n not in done]
    if args.limit:
        todo = todo[:args.limit]
    if done:
        print(f"resuming: {len(done):,} already done")
    print(f"looking up {len(todo):,} NPIs (about {len(todo) * (args.delay + 0.3) / 60:.0f} min)")

    new_rows, new_addr = [], []
    for i, npi in enumerate(todo, 1):
        row, addrs = parse(npi, fetch(npi))
        new_rows.append(row)
        new_addr.extend(addrs)
        if i % 100 == 0 or i == len(todo):
            done_rows = pd.concat([done_rows, pd.DataFrame(new_rows)], ignore_index=True)
            done_addr = pd.concat([done_addr, pd.DataFrame(new_addr)], ignore_index=True)
            safe_save(done_rows, npi_path)
            safe_save(done_addr, addr_path)
            new_rows, new_addr = [], []
            print(f"  {i:,}/{len(todo):,} done (saved)", flush=True)
        time.sleep(args.delay)

    r = done_rows
    print("\nsummary:")
    print(f"  NPIs looked up       : {len(r):,}")
    print(f"  found in NPPES       : {int((r['found'].astype(str) == 'True').sum()):,}")
    print(f"  entity type          : {r['entity_type'].value_counts().to_dict()}")
    print(f"  status               : {r['status'].value_counts().to_dict()}")
    if len(done_addr):
        print(f"  addresses            : {len(done_addr):,}  by source {done_addr['address_source'].value_counts().to_dict()}")
        loc = done_addr[done_addr["address_source"] == "nppes_location"]
        print(f"  practice location in CA: {int((loc['state'] == 'CA').sum()):,} of {len(loc):,}")
    print(f"  top specialties      : {r['primary_taxonomy_desc'].value_counts().head(5).to_dict()}")
    print(f"\nwrote {npi_path}\nwrote {addr_path}")


if __name__ == "__main__":
    main()