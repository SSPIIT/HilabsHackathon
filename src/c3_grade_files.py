#!/usr/bin/env python3
"""
C3 - Grade in-network rate files (quality and completeness).

Takes the download log written by c3_download_in_network_files.py (columns url, file, bytes,
status), finds each file on disk, streams it, and grades it with the framework in the C3 doc:

  Step 0  schema check        6 required top-level fields + optional plan fields (CMS schema)
  Step 1  gates (fail = F)    G1 readable + schema, G2 <=1% undefined provider references,
                              G3 last_updated_on <= 120 days old
  Step 2  precision           share of price rows passing P1-P8 (a row failing several = 1)
            P1 rate <= $1 (dollar types)        P5 code shape does not match its type
                                                   (HCPCS accepts Level I = CPT and Level II)
            P2 >10x or <1/10 of the median for  P6 invalid / placeholder NPI in the row's
               code+class+modifier (CPT/HCPCS,      provider groups (CMS Luhn, 80840 prefix)
               groups of 5+)                    P7 MS-DRG or place of service 21 marked outpatient
            P3 exact duplicate                  P8 expiration_date before the grading date
            P4 same code+provider+POS+modifier+class+additional_information, different amount
  Revenue codes missing the leading zero ("110" for 0110) are padded before P5 and reported
  separately as a format warning (rc_unpadded_codes), since the code is recoverable.
  Step 3  recall              mean of R1 code coverage (CMS 70 shoppable services),
                              R2 provider coverage (needs --ca-npis), R3 field fill rate,
                              R4 structural integrity (broken links)
  Step 4  freshness           1.0 up to 35 days, linear to 0 at 120 days
  Step 5  score               F0.5 = 1.25PR/(0.25P+R); score = 100 x F0.5 x freshness;
                              F1 = 2PR/(P+R) is reported alongside
  Step 6  sensitivity         outlier 5x/20x, near-zero $0/$5, grace 30/45 days, without P7

Definitions used in this code
  price row  = one negotiated_price object (one code, one negotiated_rates entry)
  provider   = the row's provider_references ids, or its inline provider_groups (NPIs + TINs)
  P3/P4/P2 are evaluated within each in_network item (one billing code). If a payer splits one
  code over several items, duplicates/conflicts across those items are not detected.

Outputs (in --out-dir, default data/output):
  c3_file_grades.csv     one row per file: gates, P1-P8 counts, precision overall and per
                         attribute, R1-R4, recall, freshness, F0.5, F1, score, grade, top reasons
  c3_sensitivity.csv     score and grade under each alternative threshold
  c3_plan_grades.csv     only with --plans: each plan gets the grade of its rate file

Usage:
    python src/c3_grade_files.py --log data/in-network-files/download_log.csv
    python src/c3_grade_files.py --log data/in-network-files/download_log.csv \
        --ca-npis data/interim/nppes_ca_npis.csv --plans data/interim/c2_name_status.csv

Requires: ijson, pandas
"""

import argparse
import collections
import datetime as dt
import gzip
import re
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import ijson
import pandas as pd

# --------------------------------------------------------------------------- #
# Settings (defaults = the C3 doc; sensitivity variants change one at a time)
# --------------------------------------------------------------------------- #

REQUIRED_TOP = {"reporting_entity_name": "string", "reporting_entity_type": "string",
                "in_network": "array", "provider_references": "array",
                "last_updated_on": "string", "version": "string"}
OPTIONAL_TOP = ("issuer_name", "plan_name", "plan_sponsor_name", "plan_id_type", "plan_id",
                "plan_market_type")
G2_MAX_UNDEFINED = 0.01
G3_MAX_DAYS = 120
FRESH_GRACE = 35
NEAR_ZERO = 1.0
OUTLIER_X = 10.0
MIN_GROUP = 5
DOLLAR_TYPES = {"negotiated", "derived", "fee schedule"}
EVERGREEN = "9999-12-31"

VARIANTS = {   # name: (near_zero, outlier_x, use_p7, grace_days)
    "default":        (1.0, 10.0, True, 35),
    "outlier_5x":     (1.0, 5.0, True, 35),
    "outlier_20x":    (1.0, 20.0, True, 35),
    "near_zero_0":    (0.0, 10.0, True, 35),
    "near_zero_5":    (5.0, 10.0, True, 35),
    "grace_30_days":  (1.0, 10.0, True, 30),
    "grace_45_days":  (1.0, 10.0, True, 45),
    "without_P7":     (1.0, 10.0, False, 35),
}

CODE_SHAPE = {   # billing_code_type -> valid shape of billing_code
    "CPT": re.compile(r"^\d{4}[0-9FTU]$"),          # Cat I, II (F), III (T), PLA (U)
    "HCPCS": re.compile(r"^(\d{4}[0-9FTU]|[A-V]\d{4})$"),   # Level I (= CPT) or Level II
    "RC": re.compile(r"^\d{4}$"),                   # revenue code with leading zero
    "MS-DRG": re.compile(r"^\d{3}$"),
    "APC": re.compile(r"^\d{4}$"),
    "NDC": re.compile(r"^(\d{11}|\d{4,5}-\d{3,4}-\d{1,2})$"),
}
CHECKS = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8")
CHECK_ATTR = {"P1": "negotiated_rate", "P2": "negotiated_rate", "P3": "row (duplicate)",
              "P4": "negotiated_rate", "P5": "billing_code", "P6": "npi",
              "P7": "setting", "P8": "expiration_date"}
CHECK_NAME = {"P1": "zero/near-zero rate", "P2": "outlier rate", "P3": "exact duplicate",
              "P4": "conflicting rate", "P5": "invalid code type", "P6": "invalid/placeholder NPI",
              "P7": "impossible setting", "P8": "expired price"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def luhn_ok(npi):
    """CMS NPI check digit: Luhn mod 10 computed with the 80840 prefix."""
    s = str(npi)
    if not re.fullmatch(r"\d{10}", s):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + s)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iso_date(s):
    try:
        return dt.date.fromisoformat(str(s)) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s)) else None
    except ValueError:
        return None


def open_file(path):
    return gzip.open(path, "rb") if path.name.endswith(".gz") else open(path, "rb")


def filled(v):
    return v is not None and not (isinstance(v, str) and v.strip() == "") and v != []


def freshness(age_days, grace):
    if age_days is None:
        return 0.0
    return max(0.0, min(1.0, (G3_MAX_DAYS - age_days) / (G3_MAX_DAYS - grace)))


def f_beta(p, r, beta):
    b2 = beta * beta
    return 0.0 if p + r == 0 else (1 + b2) * p * r / (b2 * p + r)


def grade_of(score, gates_ok):
    if not gates_ok or score < 60:
        return "F"
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D"


def load_shoppable(path):
    """Read the CMS 70 shoppable services CSV (columns: code_type, codes; '|' = alternatives)."""
    if not path.exists():
        sys.exit(f"shoppable services file not found: {path}  (pass --shoppable <path>)")
    df = pd.read_csv(path, dtype=str)
    return [(r.code_type, set(r.codes.split("|"))) for r in df.itertuples()]


# --------------------------------------------------------------------------- #
# Pass 1: provider_references (they can sit before or after in_network)
# --------------------------------------------------------------------------- #

def scan_provider_refs(path, st):
    refs = {}
    with open_file(path) as fh:
        for ref in ijson.items(fh, "provider_references.item", use_float=True):
            gid = ref.get("provider_group_id")
            st["groups_defined"] += 1
            for n in ref.get("network_name") or []:
                st["networks"][n] += 1
            if ref.get("location") and not ref.get("provider_groups"):
                refs[gid] = {"bad_npi": False, "remote": True}
                st["remote_refs"] += 1
                continue
            bad = False
            for g in ref.get("provider_groups") or []:
                bad |= add_group(g, st)
            refs[gid] = {"bad_npi": bad, "remote": False}
    return refs


def add_group(g, st):
    """Count fill for one provider group, collect NPIs/TINs. Returns True if it has a bad NPI."""
    npis = g.get("npi") or []
    tin = g.get("tin") or {}
    st["fill_req"] += 3
    st["fill_ok"] += filled(npis) + filled(tin.get("value")) + filled(tin.get("type"))
    if filled(tin.get("value")):
        st["tins"].add(re.sub(r"\D", "", str(tin["value"])) or str(tin["value"]))
    bad = False
    for n in npis:
        s = str(n)
        st["npis"].add(s)
        if not luhn_ok(s):
            bad = True
            st["bad_npis"].add(s)
    return bad


# --------------------------------------------------------------------------- #
# Pass 2: header + in_network (one item = one billing code, built one at a time)
# --------------------------------------------------------------------------- #

def grade_rows_of_item(item, refs, st, grade_date, evergreen_missing):
    code = str(item.get("billing_code", "")).strip()
    ctype = str(item.get("billing_code_type", "")).strip().upper()
    st["codes"] += 1
    st["code_types"][ctype] += 1
    st["arrangements"][item.get("negotiation_arrangement")] += 1
    for k in ("billing_code", "billing_code_type", "billing_code_type_version", "name",
              "negotiation_arrangement"):
        st["fill_req"] += 1
        st["fill_ok"] += filled(item.get(k))

    if ctype == "RC" and code.isdigit() and len(code) < 4:
        st["rc_unpadded_codes"] += 1               # e.g. "110" for 0110: repairable, warn only
        code = code.zfill(4)
    shape = CODE_SHAPE.get(ctype)
    p5 = bool(code) and not code.upper().startswith("CSTM-") and shape is not None and not shape.match(code)
    rates = item.get("negotiated_rates") or []
    if not rates:
        st["codes_without_rates"] += 1
        return
    st["covered"].add((ctype, code))

    rows = []
    for nr in rates:
        st["rate_entries"] += 1
        ids = nr.get("provider_references") or []
        groups = nr.get("provider_groups") or []
        st["fill_req"] += 1
        st["fill_ok"] += bool(ids or groups)
        if not ids and not groups:
            st["rates_without_providers"] += 1
        bad = False
        for gid in ids:
            st["ref_uses"] += 1
            st["groups_used"].add(gid)
            info = refs.get(gid)
            if info is None:
                st["undefined_uses"] += 1
            else:
                bad |= info["bad_npi"]
        for g in groups:
            st["inline_groups"] += 1
            bad |= add_group(g, st)
        if ids:
            prov = ("r", tuple(sorted(map(str, ids))))
        else:
            prov = ("g", tuple(sorted((str(sorted(map(str, g.get("npi") or []))),
                                       str((g.get("tin") or {}).get("value", ""))) for g in groups)))

        for p in nr.get("negotiated_prices") or []:
            ntype = str(p.get("negotiated_type", "")).strip().lower()
            rate = p.get("negotiated_rate")
            exp = p.get("expiration_date")
            bclass = str(p.get("billing_class", "")).strip().lower()
            setting = str(p.get("setting", "")).strip().lower()
            pos = tuple(sorted(map(str, p.get("service_code") or [])))
            mods = tuple(sorted(map(str, p.get("billing_code_modifier") or [])))
            # R3 fill
            exp_ok = iso_date(exp) is not None and not (evergreen_missing and str(exp) == EVERGREEN)
            req = [filled(ntype), isinstance(rate, (int, float)), exp_ok, filled(bclass), filled(setting)]
            if bclass == "professional":
                req.append(bool(pos))
            st["fill_req"] += len(req)
            st["fill_ok"] += sum(req)
            st["rate_types"][ntype] += 1
            d = iso_date(exp)
            rows.append({
                "prov": prov, "ntype": ntype, "rate": rate if isinstance(rate, (int, float)) else None,
                "bclass": bclass, "pos": pos, "mods": mods,
                "dup_key": (prov, ntype, rate, str(exp), bclass, setting, pos, mods,
                            str(p.get("additional_information", ""))),
                "p4_key": (prov, pos, mods, bclass, str(p.get("additional_information", ""))),
                "p5": p5, "p6": bad,
                "p7": setting == "outpatient" and (ctype == "MS-DRG" or "21" in pos),
                "p8": d is not None and str(exp) != EVERGREEN and d < grade_date,
            })

    # P3 exact duplicates (first occurrence is clean), P4 conflicts (all rows in the group)
    seen, conflict_sets = set(), collections.defaultdict(set)
    for r in rows:
        r["p3"] = r["dup_key"] in seen
        seen.add(r["dup_key"])
        conflict_sets[r["p4_key"]].add(r["rate"])
    # P2 medians per code + class + modifier (dollar types, CPT/HCPCS only)
    med = {}
    if ctype in ("CPT", "HCPCS"):
        groups = collections.defaultdict(list)
        for r in rows:
            if r["ntype"] in DOLLAR_TYPES and r["rate"] is not None and r["rate"] > 0:
                groups[(r["bclass"], r["mods"])].append(r["rate"])
        med = {k: statistics.median(v) for k, v in groups.items() if len(v) >= MIN_GROUP}

    for r in rows:
        r["p4"] = len(conflict_sets[r["p4_key"]]) > 1
        dollar = r["ntype"] in DOLLAR_TYPES and r["rate"] is not None
        m = med.get((r["bclass"], r["mods"])) if dollar else None
        ratio = (r["rate"] / m) if (m and r["rate"] and r["rate"] > 0) else None
        base = r["p3"] or r["p4"] or r["p5"] or r["p6"] or r["p8"]
        st["rows"] += 1
        # default per-check counts
        flags = {"P1": dollar and r["rate"] <= NEAR_ZERO,
                 "P2": ratio is not None and (ratio > OUTLIER_X or ratio < 1 / OUTLIER_X),
                 "P3": r["p3"], "P4": r["p4"], "P5": r["p5"], "P6": r["p6"], "P7": r["p7"], "P8": r["p8"]}
        for c, v in flags.items():
            if v:
                st["check_rows"][c] += 1
        attrs = {CHECK_ATTR[c] for c, v in flags.items() if v}
        for a in attrs:
            st["attr_rows"][a] += 1
        # each sensitivity variant
        for name, (nz, ox, use7, _g) in VARIANTS.items():
            p1 = dollar and r["rate"] <= nz
            p2 = ratio is not None and (ratio > ox or ratio < 1 / ox)
            if base or p1 or p2 or (use7 and r["p7"]):
                st["unclean"][name] += 1


def new_stats():
    return {"header": {}, "top_types": {}, "networks": collections.Counter(), "npis": set(),
            "bad_npis": set(), "tins": set(), "groups_defined": 0, "remote_refs": 0,
            "groups_used": set(), "ref_uses": 0, "undefined_uses": 0, "inline_groups": 0,
            "codes": 0, "codes_without_rates": 0, "rate_entries": 0, "rates_without_providers": 0,
            "code_types": collections.Counter(), "arrangements": collections.Counter(),
            "rate_types": collections.Counter(), "covered": set(), "rows": 0,
            "check_rows": collections.Counter(), "attr_rows": collections.Counter(),
            "unclean": collections.Counter(), "rc_unpadded_codes": 0, "fill_req": 0, "fill_ok": 0, "parse_error": ""}


def scan_file(path, grade_date, evergreen_missing):
    st = new_stats()
    try:
        refs = scan_provider_refs(path, st)
        builder = None
        top_events = {"string": "string", "number": "number", "start_array": "array",
                      "start_map": "object", "boolean": "boolean", "null": "null"}
        with open_file(path) as fh:
            for prefix, event, value in ijson.parse(fh, use_float=True):
                if builder is None:
                    if "." not in prefix and prefix and event in top_events and prefix not in st["top_types"]:
                        st["top_types"][prefix] = top_events[event]
                        if event in ("string", "number"):
                            st["header"][prefix] = value
                    if prefix == "in_network.item" and event == "start_map":
                        builder = ijson.ObjectBuilder()
                if builder is not None:
                    builder.event(event, value)
                    if prefix == "in_network.item" and event == "end_map":
                        grade_rows_of_item(builder.value, refs, st, grade_date, evergreen_missing)
                        builder = None
                        if st["codes"] % 1000 == 0:
                            print(f"    ... {st['codes']:,} codes, {st['rows']:,} price rows", end="\r", flush=True)
        st["defined_ids"] = set(refs)
    except (ijson.JSONError, ValueError, OSError) as e:
        st["parse_error"] = f"{type(e).__name__}: {e}"
        st.setdefault("defined_ids", set())
    print(" " * 70, end="\r")
    return st


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def step0(st):
    problems = []
    for k, t in REQUIRED_TOP.items():
        got = st["top_types"].get(k)
        if got is None:
            problems.append(f"missing {k}")
        elif got != t:
            problems.append(f"{k} is {got}, expected {t}")
        elif t == "string" and not str(st["header"].get(k, "")).strip():
            problems.append(f"{k} is empty")
    if "last_updated_on" in st["header"] and iso_date(st["header"]["last_updated_on"]) is None:
        problems.append("last_updated_on is not YYYY-MM-DD")
    h = st["header"]
    if "plan_id_type" in h and str(h["plan_id_type"]).lower() not in ("ein", "hios"):
        problems.append("plan_id_type not ein/hios")
    if "plan_market_type" in h and str(h["plan_market_type"]).lower() not in ("group", "individual"):
        problems.append("plan_market_type not group/individual")
    if "plan_id" in h and not re.fullmatch(r"\d{9}|\d{5}|\d{5}[A-Z]{2}\d{3}", str(h["plan_id"])):
        problems.append("plan_id not a 9-digit EIN or 5/10-character HIOS id")
    return problems


def score_file(st, grade_date, shoppable, ca_npis):
    out = {}
    s0 = step0(st)
    upd = iso_date(st["header"].get("last_updated_on"))
    age = (grade_date - upd).days if upd else None
    undefined_share = st["undefined_uses"] / st["ref_uses"] if st["ref_uses"] else 0.0
    g1 = not st["parse_error"] and not s0
    g2 = undefined_share <= G2_MAX_UNDEFINED
    g3 = age is not None and age <= G3_MAX_DAYS
    out.update(step0_problems="; ".join(s0) or "none", parse_error=st["parse_error"] or "none",
               G1_readable=g1, G2_linked=g2, G3_current=g3, undefined_ref_share=round(undefined_share, 4),
               last_updated_on=st["header"].get("last_updated_on"), age_days=age)

    # precision
    rows = st["rows"]
    prec = 1 - st["unclean"]["default"] / rows if rows else 0.0
    out["price_rows"] = rows
    for c in CHECKS:
        out[f"{c}_{CHECK_NAME[c].replace('/', '_').replace(' ', '_')}"] = st["check_rows"][c]
    out["rows_flagged"] = st["unclean"]["default"]
    out["precision"] = round(prec, 4)
    for a in sorted(set(CHECK_ATTR.values())):
        out[f"precision_{a.split(' ')[0]}"] = round(1 - st["attr_rows"][a] / rows, 4) if rows else None
    dollar_rows = sum(v for k, v in st["rate_types"].items() if k in DOLLAR_TYPES)
    out["share_percentage_rates"] = round(st["rate_types"]["percentage"] / rows, 4) if rows else None
    out["share_per_diem_rates"] = round(st["rate_types"]["per diem"] / rows, 4) if rows else None
    out["share_dollar_rates"] = round(dollar_rows / rows, 4) if rows else None

    # recall
    cov = st["covered"]
    all_types = {t for t, _ in cov}
    def has(ctype, codes):
        if any(t == "CSTM-ALL" for t in all_types):
            return True
        labels = ("CPT", "HCPCS") if ctype == "CPT" else ("MS-DRG",)
        return any((lab, c) in cov or (lab, "CSTM-00") in cov for lab in labels for c in codes)
    pro = [has(t, c) for t, c in shoppable if t == "CPT"]
    drg = [has(t, c) for t, c in shoppable if t == "MS-DRG"]
    r1 = (sum(pro) + sum(drg)) / (len(pro) + len(drg))
    r2 = len(st["npis"] & ca_npis) / len(ca_npis) if ca_npis else None
    r3 = st["fill_ok"] / st["fill_req"] if st["fill_req"] else 0.0
    broken = [st["undefined_uses"] / st["ref_uses"] if st["ref_uses"] else 0.0,
              st["codes_without_rates"] / st["codes"] if st["codes"] else 0.0,
              st["rates_without_providers"] / st["rate_entries"] if st["rate_entries"] else 0.0,
              len(st["defined_ids"] - st["groups_used"]) / len(st["defined_ids"]) if st["defined_ids"] else 0.0]
    r4 = 1 - sum(broken) / len(broken)
    comps = [x for x in (r1, r2, r3, r4) if x is not None]
    recall = sum(comps) / len(comps)
    out.update(R1_code_coverage=round(r1, 4), R1_professional=f"{sum(pro)}/{len(pro)}",
               R1_drg=f"{sum(drg)}/{len(drg)}",
               R2_provider_coverage=round(r2, 4) if r2 is not None else "not computed (no --ca-npis)",
               R3_fill_rate=round(r3, 4), R4_structural_integrity=round(r4, 4),
               R4_undefined_refs=st["undefined_uses"], R4_codes_without_rates=st["codes_without_rates"],
               R4_rates_without_providers=st["rates_without_providers"],
               R4_unused_provider_groups=len(st["defined_ids"] - st["groups_used"]),
               recall=round(recall, 4), recall_components_used=len(comps))

    fresh = freshness(age, FRESH_GRACE)
    f05, f1 = f_beta(prec, recall, 0.5), f_beta(prec, recall, 1.0)
    gates_ok = g1 and g2 and g3
    score = 100 * f05 * fresh
    out.update(freshness=round(fresh, 4), F0_5=round(f05, 4), F1=round(f1, 4),
               score=round(score, 1), grade=grade_of(score, gates_ok))

    # top 3 reasons for lost points
    losses = [(st["check_rows"][c] / rows if rows else 0, f"{c} {CHECK_NAME[c]}: {st['check_rows'][c]:,} rows")
              for c in CHECKS]
    for name, v in (("R1 code coverage", r1), ("R2 provider coverage", r2), ("R3 fill rate", r3),
                    ("R4 structural integrity", r4)):
        if v is not None:
            losses.append((1 - v, f"{name}: {v:.1%}"))
    losses.append((1 - fresh, f"freshness: {fresh:.2f} ({age} days old)"))
    if not gates_ok:
        failed = [n for n, ok in (("G1 readable/schema", g1), ("G2 linked", g2), ("G3 current", g3)) if not ok]
        losses.append((9, "failed gate: " + ", ".join(failed)))
    out["top_reasons"] = " | ".join(t for v, t in sorted(losses, reverse=True)[:3] if v > 0) or "none"

    # sensitivity
    sens = []
    for name, (_nz, _ox, _p7, grace) in VARIANTS.items():
        p = 1 - st["unclean"][name] / rows if rows else 0.0
        fr = freshness(age, grace)
        sc = 100 * f_beta(p, recall, 0.5) * fr
        sens.append({"variant": name, "precision": round(p, 4), "freshness": round(fr, 4),
                     "score": round(sc, 1), "grade": grade_of(sc, gates_ok)})
    return out, sens


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="C3: grade in-network rate files listed in a download log")
    ap.add_argument("--log", required=True, type=Path, help="download_log.csv (url,file,bytes,status)")
    ap.add_argument("--rate-dir", type=Path, default=None, help="folder with the files (default: the log's folder)")
    ap.add_argument("--out-dir", type=Path, default=Path("data/output"))
    ap.add_argument("--shoppable", type=Path, default=Path("data/raw/reference/cms_70_shoppable_services.csv"))
    ap.add_argument("--ca-npis", type=Path, default=None,
                    help="CSV with an 'npi' column: active California NPIs from NPPES (for R2)")
    ap.add_argument("--plans", type=Path, default=None,
                    help="plan CSV with in_network_file_url; writes c3_plan_grades.csv")
    ap.add_argument("--grade-date", default=dt.date.today().isoformat(), help="YYYY-MM-DD (default today)")
    ap.add_argument("--evergreen-valid", action="store_true",
                    help="count expiration_date 9999-12-31 as filled in R3 (default: counted as missing)")
    args = ap.parse_args()

    grade_date = dt.date.fromisoformat(args.grade_date)
    rate_dir = args.rate_dir or args.log.parent
    shoppable = load_shoppable(args.shoppable)
    ca_npis = set()
    if args.ca_npis:
        ca_npis = set(pd.read_csv(args.ca_npis, dtype=str, usecols=["npi"])["npi"].str.strip())
    log = pd.read_csv(args.log, dtype=str, keep_default_na=False)
    if not {"file", "status"}.issubset(log.columns):
        sys.exit(f"log must have columns file, status; has {list(log.columns)}")
    print(f"log: {args.log} ({len(log)} rows) | files in: {rate_dir} | grading date: {grade_date}\n")

    grades, sens_rows = [], []
    for _, row in log.iterrows():
        name = row["file"]
        print("=" * 80 + f"\n{name}\n" + "=" * 80)
        if not row["status"].strip().lower().startswith(("downloaded", "skipped")):
            print(f"  not graded: status = {row['status']}\n")
            continue
        path = rate_dir / name
        if not path.exists():
            print(f"  MISSING: {path} (check --rate-dir)\n")
            continue
        t0 = time.time()
        st = scan_file(path, grade_date, evergreen_missing=not args.evergreen_valid)
        res, sens = score_file(st, grade_date, shoppable, ca_npis)
        res = {"file": name, "url": row.get("url", ""), "bytes": path.stat().st_size,
               "reporting_entity": st["header"].get("reporting_entity_name"),
               "version": st["header"].get("version"), "codes": st["codes"],
               "unique_npis": len(st["npis"]), "invalid_npis": len(st["bad_npis"]),
               "unique_tins": len(st["tins"]), "provider_groups": st["groups_defined"],
               "remote_provider_refs": st["remote_refs"],
               "rc_unpadded_codes": st["rc_unpadded_codes"],
               "network_names": "|".join(n for n, _ in st["networks"].most_common(10)), **res}
        grades.append(res)
        sens_rows += [{"file": name, **s} for s in sens]

        print(f"  read in {time.time() - t0:,.0f} s | {st['codes']:,} codes | {res['price_rows']:,} price rows | "
              f"{res['unique_npis']:,} NPIs | {res['unique_tins']:,} TINs")
        print(f"  gates      : G1 {res['G1_readable']}  G2 {res['G2_linked']}  G3 {res['G3_current']}"
              f"   (schema: {res['step0_problems']})")
        print(f"  precision  : {res['precision']:.3f}  ({res['rows_flagged']:,} rows flagged)")
        for c in CHECKS:
            print(f"      {c} {CHECK_NAME[c]:<24} {st['check_rows'][c]:>12,}")
        if st["rc_unpadded_codes"]:
            print(f"  warning    : {st['rc_unpadded_codes']:,} revenue codes missing the leading zero "
                  f"(padded, not counted in P5)")
        print(f"  recall     : {res['recall']:.3f}  R1 {res['R1_code_coverage']} ({res['R1_professional']} pro, "
              f"{res['R1_drg']} DRG)  R2 {res['R2_provider_coverage']}  R3 {res['R3_fill_rate']}  "
              f"R4 {res['R4_structural_integrity']}")
        print(f"  freshness  : {res['freshness']} ({res['age_days']} days)")
        print(f"  F0.5 {res['F0_5']:.3f} | F1 {res['F1']:.3f} | SCORE {res['score']} | GRADE {res['grade']}")
        print(f"  top reasons: {res['top_reasons']}")
        flips = {s['variant']: s['grade'] for s in sens if s['grade'] != res['grade']}
        print(f"  sensitivity: {'grade stable across all variants' if not flips else 'grade changes: ' + str(flips)}\n")

    if not grades:
        sys.exit("no files graded")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(grades).to_csv(args.out_dir / "c3_file_grades.csv", index=False)
    pd.DataFrame(sens_rows).to_csv(args.out_dir / "c3_sensitivity.csv", index=False)
    print(f"wrote {args.out_dir / 'c3_file_grades.csv'} and {args.out_dir / 'c3_sensitivity.csv'}")

    if args.plans:
        plans = pd.read_csv(args.plans, dtype=str, keep_default_na=False)
        plans["file"] = plans["in_network_file_url"].map(lambda u: unquote(Path(urlsplit(u).path).name))
        g = pd.DataFrame(grades)[["file", "score", "grade"]]
        merged = plans.merge(g, on="file", how="left").drop(columns="file")
        merged.to_csv(args.out_dir / "c3_plan_grades.csv", index=False)
        print(f"wrote {args.out_dir / 'c3_plan_grades.csv'} ({merged['grade'].notna().sum()} plans graded)")


if __name__ == "__main__":
    main()