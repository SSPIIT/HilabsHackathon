#!/usr/bin/env python3
"""
C5 step 4 - Score candidate addresses and map each (NPI, TIN) to its practising address(es).

For every NPI, candidate addresses from all sources are standardized and grouped into
"sites" (same street number + street + ZIP; also merged when geocoded within 100 m).
Each site gets three component scores, each 0..1. NPPES is treated as one source among
the others - it is NOT used as a reference / benchmark address.

  A  Agreement     independent sources reporting this site (all NPPES address types count as
                   ONE source, since they come from the same record), combined as
                   1 - prod(1 - w_source). Every practice-address source has the same base
                   weight 0.60 (NPPES location / practice location, CMS Doctors & Clinicians,
                   CA facility listing, payer directory, any other source).
                   Mailing addresses (nppes_mailing) get 0.10 - a mailing address is not a
                   practice location. Freshness applies to every source that has a date:
                   x0.75 if the record is > 3 years old, x0.5 if > 7 years old (a record's
                   update date is not proof the address moved, so the penalty is moderate;
                   a moved address is caught by other sources disagreeing).
  V  Validity      1.0 Census exact match, 0.8 non-exact match, 0.7 tie, 0.4 no match;
                   if not geocoded: 0.7 if ZIP is a real CA ZIP and city agrees, 0.5 otherwise;
                   0 if the address is a PO box (mailing, not a practice location)
  T  TIN fit       1.0 if the TIN's organization NPI or >= 2 other NPIs of the same TIN
                   list this site, 0.5 if 1 other NPI does, else 0.
                   If the TIN has no other NPIs (solo practice / single-NPI organization)
                   T cannot be measured: it is left out and the score is renormalised.

  score = 0.60*A + 0.25*V + 0.15*T            (TIN has other NPIs)
  score = (0.60*A + 0.25*V) / 0.85            (TIN has no other NPIs)
  confidence: high >= 0.70, medium >= 0.50, low < 0.50

Output keeps, for every (NPI, TIN), up to --top-k sites with score >= --min-score
(ranked, rank 1 = primary). If none reaches the threshold, the single best site is
kept with confidence "low" so no provider silently disappears.

Inputs (from earlier steps, in --in-dir):
  c5_providers_clean.csv, c5_candidates.csv, c5_nppes_npi.csv (optional),
  c5_geocoded.csv (optional, from c5_census_geocode.py), ca_zip_reference.csv (optional)

Outputs:
  <out-dir>/c5_npi_tin_address.csv    FINAL: one row per (NPI, TIN) - provider info, then
                                      addr1_* (highest confidence), addr2_*, addr3_*
  <out-dir>/c5_npi_tin_address.xlsx   same, for Excel (NPI/TIN/ZIP kept as text)
  <in-dir>/c5_npi_tin_address_long.csv  same data, one row per kept address (used by
                                        c5_report.py and C6)
  <in-dir>/c5_address_scores.csv        every (NPI, TIN, site) with all component scores

Usage:
    python src/c5_score_addresses.py --in-dir data/interim --zip-ref data/reference/ca_zip_reference.csv --out-dir data/output
"""

import argparse
import collections
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c5_address_utils import canonical_site_map, haversine_km, normalize_address  # noqa: E402

BASE_W = 0.60                         # every practice-address source, NPPES included
SOURCE_W = {"nppes_mailing": 0.10}    # mailing address is not a practice location
WEIGHTS = {"A": 0.60, "V": 0.25, "T": 0.15}


def parse_date(value):
    """Accept 2014-07-07, 7/7/2014 (Excel) or 07-07-2014; return a date or None."""
    v = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def source_weight(source, source_date):
    w = SOURCE_W.get(source, BASE_W)
    d = parse_date(source_date)
    if d is None:
        return w                      # no / unreadable date: no freshness adjustment
    years = (date.today() - d).days / 365.25
    return w * (0.5 if years > 7 else 0.75 if years > 3 else 1.0)


def read_opt(path):
    p = Path(path)
    return pd.read_csv(p, dtype=str, keep_default_na=False) if p.exists() else None


PROVIDER_COLS = ["npi", "tin", "business_name", "n_name_variants", "entity_type"]
ADDR_COLS = ["address_1", "address_2", "city", "state", "zip", "census_address", "lat", "lon",
             "score", "confidence", "sources", "n_sources", "A_agreement", "V_validity",
             "T_tin_fit", "n_tin_npis_here", "validity_note", "flags", "n_tied_sites"]


def make_wide(final, top_k):
    """One row per (NPI, TIN): provider columns, n_addresses, then addr1_*, addr2_*, addr3_*."""
    base = final[final["rank"] == 1][PROVIDER_COLS + ["n_candidate_sites"]].copy()
    base["n_addresses"] = base.set_index(["npi", "tin"]).index.map(
        final.groupby(["npi", "tin"]).size()).values
    wide = base
    for k in range(1, top_k + 1):
        part = final[final["rank"] == k][["npi", "tin"] + ADDR_COLS]
        wide = wide.merge(part.rename(columns={c: f"addr{k}_{c}" for c in ADDR_COLS}),
                          on=["npi", "tin"], how="left")
    return wide


def write_final(wide, path):
    """CSV + (if openpyxl is installed) an .xlsx where NPI/TIN/ZIP stay text."""
    wide.to_csv(path, index=False)
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("  (pip install openpyxl to also get an .xlsx that keeps leading zeros)")
        return
    num = [c for c in wide.columns if c.endswith(("_lat", "_lon", "_score", "_A_agreement",
                                                  "_V_validity", "_T_tin_fit", "_n_sources"))]
    out = wide.copy()
    for c in num:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    try:
        with pd.ExcelWriter(path.with_suffix(".xlsx"), engine="openpyxl") as xw:
            out.to_excel(xw, index=False, sheet_name="npi_tin_address")
            ws = xw.sheets["npi_tin_address"]
            ws.freeze_panes = "C2"
            ws.auto_filter.ref = ws.dimensions
            for i, c in enumerate(out.columns, 1):
                col = ws.cell(row=1, column=i).column_letter
                ws.column_dimensions[col].width = min(max(len(c), 10) + 2, 40)
                if c not in num:
                    for cell in ws[col][1:]:
                        cell.number_format = "@"
        print(f"wrote {path.with_suffix('.xlsx')}")
    except PermissionError:
        print(f"  !! could not write {path.with_suffix('.xlsx')} - close it in Excel and re-run")


def main():
    ap = argparse.ArgumentParser(description="C5: score addresses and map NPI/TIN to addresses")
    ap.add_argument("--in-dir", default="data/interim")
    ap.add_argument("--geocoded", default=None, help="default: <in-dir>/c5_geocoded.csv")
    ap.add_argument("--zip-ref", default="data/reference/ca_zip_reference.csv")
    ap.add_argument("--out-dir", default="data/output")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--min-score", type=float, default=0.50)
    args = ap.parse_args()
    ind, outd = Path(args.in_dir), Path(args.out_dir)
    outd.mkdir(parents=True, exist_ok=True)

    prov = pd.read_csv(ind / "c5_providers_clean.csv", dtype=str, keep_default_na=False)
    cand = pd.read_csv(ind / "c5_candidates.csv", dtype=str, keep_default_na=False)
    nppes = read_opt(ind / "c5_nppes_npi.csv")
    geo = read_opt(args.geocoded or ind / "c5_geocoded.csv")
    zref = read_opt(args.zip_ref)
    print(f"providers {len(prov):,} pairs | candidates {len(cand):,} | "
          f"NPPES {'yes' if nppes is not None else 'no'} | geocoded {'yes' if geo is not None else 'no'} | "
          f"ZIP ref {'yes' if zref is not None else 'no'}")

    etype = dict(zip(nppes["npi"], nppes["entity_type"])) if nppes is not None else {}
    geo_by_key = {}
    if geo is not None and "norm_key" in geo.columns:
        for _, g in geo.iterrows():
            geo_by_key[g["norm_key"]] = (g.get("census_match", ""), g.get("census_match_type", ""),
                                         g.get("lat", ""), g.get("lon", ""), g.get("census_address", ""))
    zip_city = {}
    if zref is not None:
        for _, z in zref.iterrows():
            cities = {z["city"]} | {c.strip() for c in z.get("acceptable_cities", "").split("|") if c.strip()}
            zip_city[z["zip"]] = cities

    # ---- normalize every candidate ---------------------------------------------------
    recs = []
    for _, r in cand.iterrows():
        n = normalize_address(r["address_1"], r["address_2"], r["city"], r["state"], r["zip"])
        w = source_weight(r["source"], r["source_date"])
        g = geo_by_key.get(r["norm_key"], ("", "", "", "", ""))
        lat, lon = g[2], g[3]
        if not lat and r.get("src_lat", "") and r.get("src_lon", ""):
            lat, lon = r["src_lat"], r["src_lon"]          # source's own coordinates (e.g. Medi-Cal)
        recs.append({**r.to_dict(), "n": n, "w": w, "g_match": g[0], "g_type": g[1],
                     "lat": lat, "lon": lon, "census_address": g[4]})

    # same street written differently ("PK" vs "PKWY", truncated names) -> one site key
    canon = canonical_site_map(rec["n"]["site_key"] for rec in recs)
    merged = sum(1 for k, v in canon.items() if k != v)
    for rec in recs:
        rec["n"] = {**rec["n"], "site_key": canon.get(rec["n"]["site_key"], rec["n"]["site_key"])}
    print(f"  street spellings merged into one site key: {merged:,}")

    # ---- group candidates into sites per NPI -----------------------------------------
    sites = collections.defaultdict(list)          # npi -> list of site dicts
    for rec in recs:
        npi_sites = sites[rec["npi"]]
        placed = None
        for s in npi_sites:
            if s["site_key"] == rec["n"]["site_key"]:
                placed = s
                break
            d = haversine_km(s["lat"], s["lon"], rec["lat"], rec["lon"]) if s["lat"] and rec["lat"] else None
            if d is not None and d <= 0.1:
                placed = s
                break
        if placed is None:
            placed = {"site_key": rec["n"]["site_key"], "members": [], "lat": rec["lat"], "lon": rec["lon"]}
            npi_sites.append(placed)
        placed["members"].append(rec)
        if not placed["lat"] and rec["lat"]:
            placed["lat"], placed["lon"] = rec["lat"], rec["lon"]

    # sites of each NPI as keys, for the TIN-fit check
    npi_site_keys = {npi: {s["site_key"] for s in ss} for npi, ss in sites.items()}
    tin_npis = prov.groupby("tin")["npi"].apply(set).to_dict()

    # ---- score -----------------------------------------------------------------------
    rows = []
    for _, p in prov.iterrows():
        npi, tin = p["npi"], p["tin"]
        others = tin_npis.get(tin, set()) - {npi}
        for s in sites.get(npi, []):
            mem = s["members"]
            best = max(mem, key=lambda m: m["w"])              # representative address
            n = best["n"]
            by_src = {}                                  # detailed source labels (for reporting)
            by_family = {}                               # independent sources (for agreement)
            for m in mem:
                by_src[m["source"]] = max(by_src.get(m["source"], 0), m["w"])
                fam = "nppes" if m["source"].startswith("nppes") else m["source"]
                by_family[fam] = max(by_family.get(fam, 0), m["w"])
            prod = 1.0
            for w in by_family.values():                 # NPPES location + mailing = ONE source
                prod *= (1 - w)
            A = 1 - prod

            matches = [(m["g_match"], m["g_type"]) for m in mem if m["g_match"]]
            po_box = all(m["n"]["po_box"] for m in mem)
            if po_box:
                V, vnote = 0.0, "po_box"
            elif any(gm == "Match" and gt == "Exact" for gm, gt in matches):
                V, vnote = 1.0, "census_exact"
            elif any(gm == "Match" for gm, _ in matches):
                V, vnote = 0.8, "census_non_exact"
            elif any(gm == "Tie" for gm, _ in matches):
                V, vnote = 0.7, "census_tie"          # real address, several possible matches
            elif matches:
                V, vnote = 0.4, "census_no_match"
            elif zip_city:
                ok = n["zip5"] in zip_city and (not n["city"] or n["city"] in zip_city[n["zip5"]])
                V, vnote = (0.7, "zip_city_ok") if ok else (0.5, "zip_or_city_not_in_CA_ref")
            else:
                V, vnote = 0.5, "not_checked"

            org_hit = any(etype.get(o) == "2" and s["site_key"] in npi_site_keys.get(o, set()) for o in others)
            n_other = sum(1 for o in others if s["site_key"] in npi_site_keys.get(o, set()))
            T = 1.0 if org_hit or n_other >= 2 else 0.5 if n_other == 1 else 0.0
            t_applicable = bool(others)
            if t_applicable:
                score = WEIGHTS["A"] * A + WEIGHTS["V"] * V + WEIGHTS["T"] * T
            else:
                score = (WEIGHTS["A"] * A + WEIGHTS["V"] * V) / (WEIGHTS["A"] + WEIGHTS["V"])
            score = round(score, 4)
            conf = "high" if score >= 0.70 else "medium" if score >= 0.50 else "low"
            flags = []
            if po_box:
                flags.append("po_box")
            if n["state"] and n["state"] != "CA":
                flags.append("out_of_state")
            if set(by_src) <= {"nppes_location", "nppes_practice_location", "nppes_mailing"}:
                flags.append("nppes_only")

            rows.append({
                "npi": npi, "tin": tin, "business_name": p["business_name"],
                "n_name_variants": p.get("n_name_variants", ""),
                "entity_type": etype.get(npi, ""),
                "address_1": best["address_1"], "address_2": best["address_2"], "city": best["city"],
                "state": best["state"], "zip": best["zip"][:5],
                "census_address": next((m["census_address"] for m in mem if m["census_address"]), ""),
                "lat": s["lat"], "lon": s["lon"],
                "sources": " | ".join(sorted(by_src)), "n_sources": len(by_family),
                "A_agreement": round(A, 4), "V_validity": V, "validity_note": vnote,
                "T_tin_fit": T if t_applicable else "", "n_tin_npis_here": n_other,
                "score": score, "confidence": conf,
                "flags": " | ".join(flags),
            })

    scores = pd.DataFrame(rows)
    if scores.empty:
        sys.exit("no scored sites - check inputs")
    # ties (same score) are broken by: more NPIs of the same TIN at the site, more
    # independent sources, better Census validity, then address (so the order is fixed)
    scores = scores.sort_values(
        ["npi", "tin", "score", "n_tin_npis_here", "n_sources", "V_validity", "address_1"],
        ascending=[True, True, False, False, False, False, True])
    scores["rank"] = scores.groupby(["npi", "tin"]).cumcount() + 1
    # flag when sites with exactly the same score were left out by the top-k cut
    cut = scores[scores["rank"] == args.top_k + 1].set_index(["npi", "tin"])["score"]
    last = scores[(scores["rank"] == args.top_k) & (scores["score"] >= args.min_score)].set_index(["npi", "tin"])["score"]
    tied = set(last[last.index.isin(cut.index) & (last == cut.reindex(last.index))].index)
    n_tied = scores.groupby(["npi", "tin", "score"]).size()
    scores["n_candidate_sites"] = scores.groupby(["npi", "tin"])["rank"].transform("size")
    is_tied = [(a, b) in tied and sc == last.get((a, b)) for a, b, sc in
               zip(scores["npi"], scores["tin"], scores["score"])]
    scores["flags"] = [(f + " | " if f else "") + "tied_sites_cut" if t else f
                       for f, t in zip(scores["flags"], is_tied)]
    scores["n_tied_sites"] = [n_tied[(a, b, sc)] if t else "" for t, a, b, sc in
                              zip(is_tied, scores["npi"], scores["tin"], scores["score"])]
    scores.to_csv(ind / "c5_address_scores.csv", index=False)

    keep = scores[(scores["score"] >= args.min_score) & (scores["rank"] <= args.top_k)]
    fallback = scores[(scores["rank"] == 1) & ~scores.set_index(["npi", "tin"]).index.isin(
        keep.set_index(["npi", "tin"]).index)]
    final = pd.concat([keep, fallback]).sort_values(["npi", "tin", "rank"])
    final["rank"] = final.groupby(["npi", "tin"]).cumcount() + 1
    cols = ["npi", "tin", "business_name", "n_name_variants", "entity_type", "rank", "address_1", "address_2", "city", "state",
            "zip", "census_address", "lat", "lon", "score", "confidence", "sources", "n_sources",
            "A_agreement", "V_validity", "T_tin_fit", "n_tin_npis_here", "validity_note", "flags", "n_tied_sites"]
    final[cols].to_csv(ind / "c5_npi_tin_address_long.csv", index=False)
    wide = make_wide(final[cols + ["n_candidate_sites"]], args.top_k)
    write_final(wide, outd / "c5_npi_tin_address.csv")

    # ---- summary ----------------------------------------------------------------------
    pairs = prov[["npi", "tin"]].drop_duplicates()
    mapped = final[["npi", "tin"]].drop_duplicates()
    prim = final[final["rank"] == 1]
    print(f"\n(NPI, TIN) pairs           : {len(pairs):,}")
    print(f"  with >=1 address         : {len(mapped):,} ({len(mapped) / len(pairs):.1%})")
    print(f"  primary address confidence: {prim['confidence'].value_counts().to_dict()}")
    print(f"  addresses kept per pair  : {final.groupby(['npi', 'tin']).size().value_counts().sort_index().to_dict()}")
    print(f"  primary confirmed by >=2 sources: {(prim['n_sources'] >= 2).mean():.1%}")
    src = prim["sources"].str.split(" \\| ")
    print("  sources supporting the primary address:")
    for name, cnt in src.explode().value_counts().items():
        print(f"    {name:<26} {cnt:>6,} ({cnt / len(prim):.1%})")
    print(f"  primary NOT reported by NPPES: {(~prim['sources'].str.contains('nppes_location|nppes_practice_location')).mean():.1%}")
    print(f"  pairs where equally-scored sites were cut by top-{args.top_k}: {len(tied):,}")
    fl = final["flags"].str.split(" | ", regex=False).explode()
    fl = fl[fl != ""].value_counts().to_dict()
    print(f"  flags (all kept rows)    : {fl}  (rows with no flag: {int((final['flags'] == '').sum()):,})")
    if len(tied):
        nt = pd.to_numeric(final["n_tied_sites"], errors="coerce").dropna()
        print(f"  tied cuts: median {nt.median():.0f} equal sites, max {nt.max():.0f} "
              f"(multi-site organisations - see n_tied_sites / n_candidate_sites)")
    print(f"\nwrote {outd / 'c5_npi_tin_address.csv'}  ({len(wide):,} rows, one per NPI+TIN)  <- final")
    print(f"wrote {ind / 'c5_npi_tin_address_long.csv'}  ({len(final):,} rows, one per address)")
    print(f"wrote {ind / 'c5_address_scores.csv'}  ({len(scores):,} rows, all candidate sites)")


if __name__ == "__main__":
    main()