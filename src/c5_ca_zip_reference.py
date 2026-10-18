#!/usr/bin/env python3
"""
C5 helper - Build an offline California ZIP-code reference table.

Uses the `zipcodes` package, which bundles USPS ZIP data (city, county, latitude,
longitude) inside the package, so no internet access or API is needed after install.

Output columns:
  zip, zip_type (STANDARD / PO BOX / UNIQUE), active, city, acceptable_cities,
  county, lat, lon

Use it to:
  - check an address's ZIP is a real, active California ZIP
  - check city and ZIP agree (city must be the ZIP's city or one of its acceptable cities)
  - get an approximate location (ZIP centre) for distance checks when an address
    cannot be geocoded at street level

Usage:
    pip install zipcodes
    python src/c5_ca_zip_reference.py --out data/reference/ca_zip_reference.csv
"""

import argparse
from pathlib import Path

import pandas as pd
import zipcodes

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Offline California ZIP reference table")
    ap.add_argument("--state", default="CA")
    ap.add_argument("--out", default=str(PATHS["derived_reference"] / "ca_zip_reference.csv"), type=Path)
    args = ap.parse_args()

    rows = []
    for z in zipcodes.filter_by(state=args.state.upper()):
        rows.append({
            "zip": z["zip_code"],
            "zip_type": z.get("zip_code_type", ""),
            "active": z.get("active", ""),
            "city": (z.get("city") or "").upper(),
            "acceptable_cities": " | ".join(c.upper() for c in z.get("acceptable_cities") or []),
            "county": z.get("county", ""),
            "lat": float(z["lat"]) if z.get("lat") else None,
            "lon": float(z["long"]) if z.get("long") else None,
        })
    df = pd.DataFrame(rows).sort_values("zip").reset_index(drop=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    print(f"{args.state.upper()} ZIP codes : {len(df):,}")
    print(f"  active         : {int(df['active'].astype(bool).sum()):,}")
    print(f"  by type        : {df['zip_type'].value_counts().to_dict()}")
    print(f"  counties       : {df['county'].nunique()}")
    print(f"  with lat/lon   : {int(df['lat'].notna().sum()):,}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
