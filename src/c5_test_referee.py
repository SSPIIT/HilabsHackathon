#!/usr/bin/env python3
"""
C5 test - "referee" check: when two sources disagree on a provider's address, which one
does a third, independent source side with?

For every NPI and every pair of sources (X, Y):
  X and Y DISAGREE  = no site of X is the same site as any site of Y
                      (same number|street|ZIP5, or geocoded within 100 m)
  for each referee R (a third source that has this NPI):
      sides with X  = R matches X and not Y
      sides with Y  = R matches Y and not X
      both          = R matches both (provider has several sites)
      neither       = R matches neither (R has yet another address)

If, e.g., the referee sides with Doctors & Clinicians far more often than with NPPES,
that is evidence from our own data (not just from the Medicare rules) that the
DAC / PECOS address is more reliable and deserves a higher source weight.

NPPES here = practice location + extra practice locations (mailing addresses excluded).

Usage:
    python src/c5_test_referee.py --in-dir data/interim
    (prints the table, writes data/interim/c5_referee_cases.csv)
"""

import argparse
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c5_address_utils import canonical_site_map, haversine_km, normalize_address  # noqa: E402

SHORT = {"nppes": "NPPES", "cms_doctors_clinicians": "DAC (PECOS)",
         "medi-cal-ffs-provider-listing": "Medi-Cal", "ca_facility_listing": "CA facility"}


def main():
    ap = argparse.ArgumentParser(description="C5 referee test between address sources")
    ap.add_argument("--in-dir", default="data/interim")
    ap.add_argument("--min-cases", type=int, default=5, help="hide rows with fewer cases")
    args = ap.parse_args()
    ind = Path(args.in_dir)

    cand = pd.read_csv(ind / "c5_candidates.csv", dtype=str, keep_default_na=False)
    cand = cand[cand["source"] != "nppes_mailing"].copy()
    cand["family"] = cand["source"].where(~cand["source"].str.startswith("nppes"), "nppes")
    cand["site"] = [normalize_address(a, b, c, d, z)["site_key"] for a, b, c, d, z in
                    zip(cand["address_1"], cand["address_2"], cand["city"], cand["state"], cand["zip"])]
    latlon = {}
    gp = ind / "c5_geocoded.csv"
    if gp.exists():
        g = pd.read_csv(gp, dtype=str, keep_default_na=False)
        latlon = {k: (a, o) for k, a, o in zip(g["norm_key"], g["lat"], g["lon"]) if a and o}
    else:
        print("(no c5_geocoded.csv - comparing by address key only)")
    if "src_lat" in cand.columns:                       # source's own coordinates as fallback
        for k, a, o in zip(cand["norm_key"], cand["src_lat"], cand["src_lon"]):
            if a and o and k not in latlon:
                latlon[k] = (a, o)
    canon = canonical_site_map(cand["site"])
    cand["site"] = cand["site"].map(lambda k: canon.get(k, k))

    # npi -> family -> list of (site_key, latlon or None, printable address)
    data = {}
    for r in cand.itertuples():
        data.setdefault(r.npi, {}).setdefault(r.family, []).append(
            (r.site, latlon.get(r.norm_key),
             f"{r.address_1} {r.address_2}, {r.city} {r.zip[:5]}".replace("  ", " ")))

    def same(a, b):
        if a[0] == b[0]:
            return True
        if a[1] and b[1]:
            d = haversine_km(*a[1], *b[1])
            return d is not None and d <= 0.1
        return False

    def match(xs, ys):
        return any(same(x, y) for x in xs for y in ys)

    families = sorted(set(cand["family"]))
    counts, cases = {}, []
    for npi, fam in data.items():
        for x, y in itertools.combinations(sorted(fam), 2):
            if match(fam[x], fam[y]):
                continue                                  # X and Y agree - nothing to referee
            for r in sorted(set(fam) - {x, y}):
                mx, my = match(fam[r], fam[x]), match(fam[r], fam[y])
                verdict = ("both" if mx and my else f"sides with {x}" if mx
                           else f"sides with {y}" if my else "neither")
                key = (x, y, r)
                c = counts.setdefault(key, {"x": 0, "y": 0, "both": 0, "neither": 0})
                c["both" if mx and my else "x" if mx else "y" if my else "neither"] += 1
                cases.append({"npi": npi, "source_x": x, "source_y": y, "referee": r, "verdict": verdict,
                              "x_addresses": " || ".join(sorted({a[2] for a in fam[x]})),
                              "y_addresses": " || ".join(sorted({a[2] for a in fam[y]})),
                              "referee_addresses": " || ".join(sorted({a[2] for a in fam[r]}))})

    print(f"sources: {', '.join(SHORT.get(f, f) for f in families)}\n")
    print("When X and Y disagree, which one does the referee side with?")
    print("(decided = referee matches exactly one of X / Y)\n")
    hdr = f"{'X':<12} {'Y':<12} {'referee':<12} {'cases':>6} {'sides X':>9} {'sides Y':>9} {'both':>6} {'neither':>8}   X wins when decided"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for (x, y, r), c in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        if n < args.min_cases:
            continue
        dec = c["x"] + c["y"]
        win = f"{c['x'] / dec:.0%} of {dec}" if dec else "-"
        print(f"{SHORT.get(x, x):<12} {SHORT.get(y, y):<12} {SHORT.get(r, r):<12} {n:>6} "
              f"{c['x']:>9} {c['y']:>9} {c['both']:>6} {c['neither']:>8}   {win}")
        rows.append({"x": x, "y": y, "referee": r, "cases": n, **c})

    # one-line answer for the DAC question
    print("\nNPPES vs DAC, all referees combined:")
    tot = {"x": 0, "y": 0, "both": 0, "neither": 0}
    for (x, y, r), c in counts.items():
        if {x, y} == {"nppes", "cms_doctors_clinicians"}:
            dac_is_x = x == "cms_doctors_clinicians"
            tot["x"] += c["x"] if dac_is_x else c["y"]       # x = DAC wins
            tot["y"] += c["y"] if dac_is_x else c["x"]       # y = NPPES wins
            tot["both"] += c["both"]
            tot["neither"] += c["neither"]
    dec = tot["x"] + tot["y"]
    if dec:
        print(f"  referee sides with DAC  : {tot['x']:,}")
        print(f"  referee sides with NPPES: {tot['y']:,}")
        print(f"  both / neither          : {tot['both']:,} / {tot['neither']:,}")
        print(f"  -> DAC is right in {tot['x'] / dec:.0%} of the {dec:,} decided disagreements")
    else:
        print("  no decided cases (DAC and NPPES rarely disagree where a third source exists)")

    out = ind / "c5_referee_cases.csv"
    pd.DataFrame(cases).to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(cases):,} referee cases, one row per NPI x pair x referee)")


if __name__ == "__main__":
    main()