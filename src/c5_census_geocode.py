#!/usr/bin/env python3
"""
C5 helper - Geocode addresses with the free US Census Geocoder (run on your laptop).

The Census batch service takes a CSV of up to 10,000 addresses per request and
returns, for each one: match status, match type, the standardized address, and
longitude/latitude. No API key is needed; it only needs internet access.

Input CSV (any extra columns are kept and copied to the output):
    id, street, city, state, zip         (column names can be changed with flags)

Output CSV = input columns +
    census_match          Match / No_Match / Tie
    census_match_type     Exact / Non_Exact
    census_address        standardized address returned by the Census
    lat, lon
    census_tigerline_id, census_side

Re-running is safe: ids already present in the output file are skipped, so if the
run stops halfway you just run the same command again.

Usage:
    pip install requests pandas
    # quick test with one address
    python src/c5_census_geocode.py --test "1006 NW Boulevard, Fresno, CA 93711"
    # batch
    python src/c5_census_geocode.py --input data/interim/c5_addresses_to_geocode.csv --out data/interim/c5_geocoded.csv
"""

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
ONELINE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
BENCHMARK = "Public_AR_Current"
OUT_COLS = ["census_match", "census_match_type", "census_address", "lat", "lon",
            "census_tigerline_id", "census_side"]


def test_one(address):
    r = requests.get(ONELINE_URL, params={"address": address, "benchmark": BENCHMARK,
                                          "format": "json"}, timeout=60)
    r.raise_for_status()
    matches = r.json()["result"]["addressMatches"]
    if not matches:
        print("No match")
        return
    for m in matches:
        c = m["coordinates"]
        print(f"{m['matchedAddress']}  ->  lat {c['y']}, lon {c['x']}")


def parse_response(text):
    """Census batch response: no header; columns
    id, input address, match, match type, matched address, "lon,lat", tigerline id, side"""
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        rid = row[0]
        rec = dict.fromkeys(OUT_COLS, "")
        rec["census_match"] = row[2] if len(row) > 2 else ""
        if len(row) > 3:
            rec["census_match_type"] = row[3]
        if len(row) > 4:
            rec["census_address"] = row[4]
        if len(row) > 5 and "," in row[5]:
            lon, lat = row[5].split(",", 1)
            rec["lon"], rec["lat"] = lon.strip(), lat.strip()
        if len(row) > 6:
            rec["census_tigerline_id"] = row[6]
        if len(row) > 7:
            rec["census_side"] = row[7]
        out[rid] = rec
    return out


def geocode_batch(chunk, retries=3):
    """chunk: DataFrame with columns id, street, city, state, zip."""
    buf = io.StringIO()
    chunk[["id", "street", "city", "state", "zip"]].to_csv(buf, index=False, header=False)
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(
                BATCH_URL,
                files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")},
                data={"benchmark": BENCHMARK},
                timeout=600,
            )
            r.raise_for_status()
            return parse_response(r.text)
        except requests.RequestException as e:
            print(f"    attempt {attempt} failed: {e}")
            time.sleep(10 * attempt)
    raise SystemExit("Census geocoder did not respond after retries - try again later or use a smaller --batch-size")


def main():
    ap = argparse.ArgumentParser(description="Batch geocode addresses with the US Census Geocoder")
    ap.add_argument("--test", help="geocode one address string and print the result")
    ap.add_argument("--input", type=Path)
    ap.add_argument("--out", type=Path, default=str(PATHS["interim"] / "c5_geocoded.csv"))
    ap.add_argument("--batch-size", type=int, default=1000, help="rows per request (max 10000)")
    ap.add_argument("--id-col", default="id")
    ap.add_argument("--street-col", default="street")
    ap.add_argument("--city-col", default="city")
    ap.add_argument("--state-col", default="state")
    ap.add_argument("--zip-col", default="zip")
    args = ap.parse_args()

    if args.test:
        test_one(args.test)
        return
    if not args.input:
        sys.exit("give --input <csv> (or --test \"address\")")

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    rename = {args.id_col: "id", args.street_col: "street", args.city_col: "city",
              args.state_col: "state", args.zip_col: "zip"}
    missing = [c for c in rename if c not in df.columns]
    if missing:
        sys.exit(f"missing columns {missing}; columns are {list(df.columns)}")
    work = df.rename(columns=rename)
    if work["id"].duplicated().any():
        sys.exit("ids must be unique")

    done = pd.DataFrame()
    if args.out.exists():
        done = pd.read_csv(args.out, dtype=str, keep_default_na=False)
        print(f"resuming: {len(done):,} rows already geocoded in {args.out}")
    if len(done) and "norm_key" in done.columns and "norm_key" in work.columns:
        # ids are renumbered every time c5_build_candidates.py runs, so match old results
        # by the normalised address (norm_key), not by id, and give them the new ids
        old = done.drop_duplicates("norm_key").set_index("norm_key")
        keep = work[work["norm_key"].isin(old.index)].copy()
        for col in OUT_COLS:
            keep[col] = keep["norm_key"].map(old[col]) if col in old.columns else ""
        print(f"  reusing {len(keep):,} already-geocoded addresses (matched by norm_key); "
              f"{len(done) - len(keep):,} old rows no longer needed")
        done = keep
        todo = work[~work["norm_key"].isin(old.index)]
    else:
        todo = work[~work["id"].isin(set(done.get("id", [])))]
    print(f"to geocode: {len(todo):,} of {len(work):,} addresses, batch size {args.batch_size}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(todo), args.batch_size):
        chunk = todo.iloc[start:start + args.batch_size]
        print(f"  batch {start // args.batch_size + 1}: rows {start + 1}-{start + len(chunk)} ...", flush=True)
        res = geocode_batch(chunk)
        out_chunk = chunk.copy()
        for col in OUT_COLS:
            out_chunk[col] = out_chunk["id"].map(lambda i: res.get(i, {}).get(col, ""))
        done = pd.concat([done, out_chunk], ignore_index=True)
        done.to_csv(args.out, index=False)          # save after every batch
        time.sleep(1)

    print("\nresults:")
    print(done["census_match"].replace("", "no response").value_counts().to_string())
    if "census_match_type" in done:
        print(done.loc[done["census_match"] == "Match", "census_match_type"].value_counts().to_string())
    print(f"wrote {args.out}  ({len(done):,} rows)")


if __name__ == "__main__":
    main()