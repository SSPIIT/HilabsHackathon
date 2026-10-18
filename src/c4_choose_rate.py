#!/usr/bin/env python3
"""
C4 step 2 - CHOOSE: one rate per provider and service, with the reason it was kept.

Input : data/interim/c4_rates_extracted.csv   (c4_extract_rates.py: every price, nothing dropped)
Output: data/interim/c4_rates.csv              (one row per provider + service)

Rules, in order of application:
  S1 One service       npi, tin, code type, code, code_detail, modifiers, billing class,
                       place-of-service group, price setting, arrangement; prices for
                       different services are all kept, choosing only happens within one
  S2 RC with HCPCS     revenue code + the HCPCS code in additional_information is the
                       service (RC 636 + J1885); the item name is ignored
  S3 RC without HCPCS  the revenue code alone is the service; item names are ignored
                       (Health Net repeats the same price list under every name)
  S4 Same-meaning      modifiers that mean the same thing are grouped before comparing:
     modifiers         95, GT, GQ, 93, FQ (all telehealth) -> TELEHEALTH; so 90833-95 and
                       90833-95|GT are one service. The original values are kept in
                       modifiers_in_file. Modifiers that change the service (26, TC, 25, 59...)
                       stay separate.
  R1 Duplicates        same provider group + same price counts once
  R2 Fake prices       $0, negative or >= $999,999 removed, if a real price exists
  R3 Expired           expiry before the file date removed, if a current price exists
  R4 Rate type         keep only the best type: negotiated > fee schedule > derived >
                       per diem > percentage
  R5 Most groups       keep the price listed by the most provider groups (a provider in
                       several groups with different contracts, e.g. 99213 at $277 in 16
                       groups vs $119.71 in 8 -> $277)
  R6 Tie               if prices share the top count, keep the middle one (lower when two)
kept_rule: single, duplicates, fake_removed, expired_removed, rate_type, most_groups, tie_middle
flag:      conflict_within_group (one group itself lists >1 current price), fake_only,
           expired_only, percent_not_dollars

Usage:
    python src/c4_choose_rate.py --file-date 2026-10-06
"""

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import PATHS  # noqa: E402

PLACEHOLDER_MAX = 999_999
TELEHEALTH_MODIFIERS = ["95", "GT", "GQ", "93", "FQ"]     # all mean "delivered by telehealth"
SERVICE = ("npi, tin, billing_code_type, billing_code, code_detail, modifiers, billing_class, "
           "pos_group, price_setting, negotiation_arrangement")

SQL = """
-- every price row, with the service columns filled
CREATE OR REPLACE TABLE j AS
SELECT * REPLACE (
    -- S4: modifiers that mean the same thing are one value (telehealth: 95, GT, GQ, 93, FQ)
    array_to_string(list_sort(list_distinct(list_transform(string_split(upper(modifiers), '|'),
        m -> CASE WHEN trim(m) IN ({tele}) THEN 'TELEHEALTH' ELSE trim(m) END))), '|') AS modifiers),
  modifiers AS modifiers_in_file,
  CASE WHEN upper(billing_code_type) = 'RC'
       THEN coalesce(trim(additional_information), '') ELSE '' END AS code_detail,
  TRY_CAST(negotiated_rate AS DOUBLE) AS rate
FROM read_csv('{src}', header=true, all_varchar=true, nullstr='__none__');

CREATE OR REPLACE TABLE mods AS
SELECT {svc}, string_agg(DISTINCT nullif(modifiers_in_file, ''), ' ; ') AS modifiers_in_file
FROM j GROUP BY ALL;

-- R1: one row per service + provider group + price (+ type, expiry)
CREATE OR REPLACE TABLE d AS
SELECT {svc}, provider_group_id, rate, negotiated_type, expiration_date,
       any_value(tin_type) AS tin_type,
       any_value(CASE WHEN upper(billing_code_type) = 'RC' THEN '' ELSE name END) AS description,
       count(*) AS n_rows,
       (rate IS NULL OR rate <= 0 OR rate >= {ph}) AS is_fake,
       (expiration_date <> '' AND expiration_date < '{fdate}' AND expiration_date NOT LIKE '9999%') AS is_expired,
       CASE negotiated_type WHEN 'negotiated' THEN 1 WHEN 'fee schedule' THEN 2 WHEN 'derived' THEN 3
            WHEN 'per diem' THEN 4 WHEN 'percentage' THEN 5 ELSE 6 END AS type_rank
FROM j GROUP BY ALL;

CREATE OR REPLACE TABLE s0 AS
SELECT {svc}, any_value(tin_type) AS tin_type, any_value(description) AS description,
       sum(n_rows)::BIGINT AS n_rates_found, count(DISTINCT provider_group_id) AS n_groups,
       count(DISTINCT rate) AS a1, min(rate) AS min_rate, max(rate) AS max_rate,
       bool_or(NOT is_fake) AS any_real
FROM d GROUP BY ALL;

-- R2: fake prices out (if a real one exists)
CREATE OR REPLACE TABLE d2 AS
SELECT d.* FROM d JOIN s0 USING ({svc}) WHERE NOT (d.is_fake AND s0.any_real);
CREATE OR REPLACE TABLE s2 AS
SELECT {svc}, count(DISTINCT rate) AS a2, bool_or(NOT is_expired) AS any_current FROM d2 GROUP BY ALL;

-- R3: expired out (if a current one exists)
CREATE OR REPLACE TABLE d3 AS
SELECT d2.* FROM d2 JOIN s2 USING ({svc}) WHERE NOT (d2.is_expired AND s2.any_current);
CREATE OR REPLACE TABLE s3 AS
SELECT {svc}, count(DISTINCT rate) AS a3, min(type_rank) AS best_type FROM d3 GROUP BY ALL;

-- R4: best rate type only
CREATE OR REPLACE TABLE d4 AS
SELECT d3.* FROM d3 JOIN s3 USING ({svc}) WHERE d3.type_rank = s3.best_type;

-- conflict inside one provider group (after R2-R4)
CREATE OR REPLACE TABLE cg AS
SELECT {svc}, bool_or(n > 1) AS conflict_within_group
FROM (SELECT {svc}, provider_group_id, count(DISTINCT rate) AS n FROM d4 GROUP BY ALL) GROUP BY ALL;

-- R5: how many provider groups list each remaining price
CREATE OR REPLACE TABLE p AS
SELECT {svc}, rate, any_value(negotiated_type) AS negotiated_type, min(expiration_date) AS expiration_date,
       count(DISTINCT provider_group_id) AS groups_listing, bool_and(is_fake) AS fake, bool_and(is_expired) AS expired
FROM d4 GROUP BY ALL;
CREATE OR REPLACE TABLE top AS
SELECT {svc}, count(DISTINCT rate) AS a4, max(groups_listing) AS top_groups FROM p GROUP BY ALL;
CREATE OR REPLACE TABLE winners AS                 -- prices with the top count
SELECT p.* FROM p JOIN top USING ({svc}) WHERE p.groups_listing = top.top_groups;

-- R6: tie -> middle (lower middle) of the tied prices
CREATE OR REPLACE TABLE kept AS
SELECT {svc}, count(*) AS n_tied, quantile_disc(rate, 0.5) AS negotiated_rate,
       any_value(groups_listing) AS groups_for_kept_rate
FROM winners GROUP BY ALL;
CREATE OR REPLACE TABLE kept2 AS
SELECT kept.*, p.negotiated_type, p.expiration_date, p.fake, p.expired
FROM kept JOIN p USING ({svc}) WHERE p.rate IS NOT DISTINCT FROM kept.negotiated_rate;

CREATE OR REPLACE TABLE final AS
SELECT k.npi, k.tin, s0.tin_type,
       k.billing_code_type, k.billing_code, k.code_detail, s0.description, k.modifiers, k.billing_class,
       k.pos_group, k.price_setting, k.negotiation_arrangement,
       coalesce(mods.modifiers_in_file, '') AS modifiers_in_file,
       k.negotiated_rate, k.negotiated_type, k.expiration_date,
       CASE
         WHEN s0.n_rates_found = 1 THEN 'single'
         WHEN s0.a1 = 1           THEN 'duplicates'
         WHEN s2.a2 = 1           THEN 'fake_removed'
         WHEN s3.a3 = 1           THEN 'expired_removed'
         WHEN top.a4 = 1          THEN 'rate_type'
         WHEN k.n_tied = 1        THEN 'most_groups'
         ELSE 'tie_middle'
       END AS kept_rule,
       s0.n_rates_found, s0.n_groups, k.groups_for_kept_rate, s0.a1 AS n_distinct_amounts,
       s0.min_rate, s0.max_rate,
       concat_ws(' | ',
         CASE WHEN cg.conflict_within_group THEN 'conflict_within_group' END,
         CASE WHEN k.fake THEN 'fake_only' END,
         CASE WHEN k.expired THEN 'expired_only' END,
         CASE WHEN k.negotiated_type = 'percentage' THEN 'percent_not_dollars' END) AS flag
FROM kept2 k
JOIN s0 USING ({svc}) JOIN s2 USING ({svc}) JOIN s3 USING ({svc})
JOIN top USING ({svc}) JOIN cg USING ({svc}) JOIN mods USING ({svc});
"""


def main():
    ap = argparse.ArgumentParser(description="C4 step 2: one rate per provider and service")
    ap.add_argument("--input", default=str(PATHS["interim"] / "c4_rates_extracted.csv"), type=Path)
    ap.add_argument("--out", default=str(PATHS["interim"] / "c4_rates.csv"), type=Path)
    ap.add_argument("--file-date", default="", help="rate file last_updated_on (YYYY-MM-DD), used by R3")
    args = ap.parse_args()
    if not args.input.exists():
        sys.exit(f"{args.input} not found - run c4_extract_rates.py first")
    try:
        import duckdb
    except ImportError:
        sys.exit("pip install duckdb")
    fdate = (args.file_date or date.today().isoformat())[:10]

    tmp = args.out.parent / "_c4_tmp"
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{tmp.as_posix()}'")
    print("choosing one rate per provider and service ...")
    con.execute(SQL.format(src=args.input.as_posix(), svc=SERVICE, ph=PLACEHOLDER_MAX,
                           fdate=fdate, tele=", ".join(f"'{m}'" for m in TELEHEALTH_MODIFIERS)))
    con.execute(f"COPY (SELECT * FROM final ORDER BY {SERVICE}) TO '{args.out.as_posix()}' (HEADER, DELIMITER ',')")

    q = lambda s: con.execute(s).fetchall()
    rows = q("SELECT count(*) FROM j")[0][0]
    fin, npis, pairs, codes = q("SELECT count(*), count(DISTINCT npi), count(DISTINCT (npi, tin)), "
                                "count(DISTINCT (billing_code_type, billing_code)) FROM final")[0]
    print(f"\nextracted price rows      : {rows:,}")
    print(f"provider + service rows   : {fin:,}   ({npis:,} NPIs, {pairs:,} NPI+TIN, {codes:,} codes)")
    print("\nwhich rule decided the kept rate:")
    for r, n in q("SELECT kept_rule, count(*) FROM final GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {r:<18} {n:>10,} ({n / fin:.1%})")
    print("\nflags:")
    for r, n in q("SELECT f, count(*) FROM (SELECT unnest(string_split(flag, ' | ')) AS f FROM final "
                  "WHERE flag <> '') GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {r:<24} {n:>10,}")
    print("\nrows by code type:")
    for r, n in q("SELECT billing_code_type, count(*) FROM final GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {r:<8} {n:>10,}")
    print("\nlargest spreads still decided by R5/R6:")
    for r in q("SELECT npi, billing_code_type, billing_code, code_detail, pos_group, n_distinct_amounts, "
               "min_rate, negotiated_rate, max_rate, kept_rule FROM final "
               "WHERE kept_rule IN ('most_groups', 'tie_middle') "
               "ORDER BY max_rate / nullif(min_rate, 0) DESC NULLS LAST LIMIT 8"):
        print(f"  NPI {r[0]} {r[1]} {r[2]} {r[3]} {r[4]}: {r[5]} amounts {r[6]:,.2f}..{r[8]:,.2f} "
              f"-> kept {r[7]:,.2f} ({r[9]})")
    con.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()