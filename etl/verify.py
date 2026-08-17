#!/usr/bin/env python3
"""Verify the built store.

Asserts referential integrity, join coverage, and that the guardrail flags fire
on the entities they are supposed to fire on. Exits non-zero on any failure so
this can gate a build.

    python3 etl/verify.py [--db build/pf.sqlite]
"""

import argparse
import os
import sqlite3
import sys

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "pf.sqlite"))
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]

    print("\nreferential integrity")
    child_tables = [
        "fiscal_stability", "operating_vs_capital", "revenue_volatility",
        "revenue_volatility_yoy", "spending_by_service_area", "workforce_profile",
        "workforce_by_service_area", "workforce_top_functions", "financial_functions",
        "financial_trends", "revenue_sources", "entity_flags", "data_availability",
        "geo_entity",
    ]
    for table in child_tables:
        orphans = one(
            f"SELECT COUNT(*) FROM {table} t LEFT JOIN entities e ON e.pid6=t.pid6 "
            "WHERE e.pid6 IS NULL")
        check(f"{table} has no orphan pid6", orphans == 0, f"{orphans} orphans")

    check("entity count is 1529", one("SELECT COUNT(*) FROM entities") == 1529)
    check("every entity has a flag row",
          one("SELECT COUNT(*) FROM entities") == one("SELECT COUNT(*) FROM entity_flags"))
    check("every entity has an availability row",
          one("SELECT COUNT(*) FROM entities") == one("SELECT COUNT(*) FROM data_availability"))
    check("pid6 is unique", one("SELECT COUNT(*) FROM (SELECT pid6 FROM entities GROUP BY pid6 HAVING COUNT(*)>1)") == 0)

    print("\nguardrail flags fire on the right entities")

    # §8: Sumpter scores 60 because debt-to-revenue is 99.43 and scores zero,
    # not because it is operationally weak. Structural balance is at the top of
    # its range. This is the case the prompt describes, so it is pinned.
    sumpter = conn.execute(
        "SELECT f.score, f.structural_balance_score, f.debt_to_revenue, g.debt_capped "
        "FROM fiscal_stability f JOIN entity_flags g ON g.pid6=f.pid6 "
        "JOIN entities e ON e.pid6=f.pid6 WHERE e.legal_name='CITY OF SUMPTER'").fetchone()
    check("Sumpter is flagged debt_capped", sumpter and sumpter[3] == 1)
    check("Sumpter scores 60 with structural balance at 100",
          sumpter and abs(sumpter[0] - 60) < 0.01 and abs(sumpter[1] - 100) < 0.01,
          f"got {sumpter}")

    # The priorYearScore artifact: Baker County has no prior year, so both the
    # prior score and the year-over-year change must be NULL, not 0 and 97.
    baker = conn.execute(
        "SELECT f.prior_year_score, f.yoy_change, g.prior_year_missing "
        "FROM fiscal_stability f JOIN entity_flags g ON g.pid6=f.pid6 "
        "JOIN entities e ON e.pid6=f.pid6 WHERE e.legal_name='COUNTY OF BAKER'").fetchone()
    check("Baker County prior year is NULL, not 0", baker and baker[0] is None)
    check("Baker County yoy_change is NULL, not 97", baker and baker[1] is None)
    check("Baker County flagged prior_year_missing", baker and baker[2] == 1)

    leaked = one("SELECT COUNT(*) FROM fiscal_stability WHERE prior_year_score=0 AND score>0")
    check("no entity still reports a 0 prior year against a positive score", leaked == 0,
          f"{leaked} rows")

    # §8: the special district capital-share median is 2.72, which is not a
    # midpoint. Every entity in that pool must carry the degenerate flag.
    degenerate = one(
        "SELECT COUNT(*) FROM operating_vs_capital o JOIN entities e ON e.pid6=o.pid6 "
        "WHERE e.gov_type_name='Special District' AND o.peer_median_degenerate=0")
    check("every special district carries the degenerate-median flag", degenerate == 0,
          f"{degenerate} missing")

    # The State of Oregon is benchmarked against school districts.
    mismatch = conn.execute(
        "SELECT e.legal_name, o.peer_group, o.peer_count_stated FROM operating_vs_capital o "
        "JOIN entity_flags g ON g.pid6=o.pid6 JOIN entities e ON e.pid6=o.pid6 "
        "WHERE g.peer_group_mismatch=1").fetchall()
    check("the state record is flagged peer_group_mismatch",
          any(r[0] == "STATE OF OREGON" for r in mismatch), f"{mismatch}")

    print("\nnull is distinguished from zero")
    # An entity whose spending block is absent has no rows and is marked absent,
    # rather than looking identical to an entity that reported zeros.
    inconsistent = one("""
        SELECT COUNT(*) FROM data_availability a
        WHERE a.has_spending_breakdown = 1
          AND NOT EXISTS (SELECT 1 FROM spending_by_service_area s WHERE s.pid6=a.pid6)""")
    check("has_spending_breakdown implies rows exist", inconsistent == 0, f"{inconsistent}")

    reverse = one("""
        SELECT COUNT(*) FROM (SELECT DISTINCT pid6 FROM spending_by_service_area) s
        JOIN data_availability a ON a.pid6=s.pid6 WHERE a.has_spending_breakdown = 0""")
    check("rows existing implies has_spending_breakdown", reverse == 0, f"{reverse}")

    zero_reported = one("SELECT COUNT(*) FROM operating_vs_capital WHERE total_expenditure=0")
    check("real reported zeros are preserved, not nulled", zero_reported > 0,
          f"{zero_reported} entities report zero total expenditure")

    print("\nper-capita is refused where there is no denominator")
    bad_denominator = one("""
        SELECT COUNT(*) FROM workforce_profile w JOIN entity_flags g ON g.pid6=w.pid6
        WHERE g.no_population_denominator=1 AND w.population > 0""")
    check("no_population_denominator never set where population exists", bad_denominator == 0,
          f"{bad_denominator}")

    print("\noffice crosswalk")
    total = one("SELECT COUNT(*) FROM office_entity_match WHERE match_method != 'not_a_government'")
    resolved = one("SELECT COUNT(*) FROM office_entity_match WHERE pid6 IS NOT NULL")
    check("at least 90% of government divisions resolve", resolved / total >= 0.90,
          f"{resolved}/{total} = {resolved / total:.1%}")
    check("every resolved match carries a confidence",
          one("SELECT COUNT(*) FROM office_entity_match WHERE pid6 IS NOT NULL "
              "AND confidence NOT IN ('high','medium','low')") == 0)
    check("no unmatched row claims a confidence",
          one("SELECT COUNT(*) FROM office_entity_match WHERE pid6 IS NULL "
              "AND confidence != 'none'") == 0)
    check("legislative and judicial districts are not treated as governments",
          one("SELECT COUNT(*) FROM office_entity_match WHERE match_method='not_a_government'") > 0)
    check("offices carry no holder names",
          "holder" not in " ".join(r[1] for r in conn.execute("PRAGMA table_info(offices)")).lower())

    print("\ngeometry coverage")
    check("all 36 counties are mappable",
          one("SELECT COUNT(*) FROM geo_entity WHERE layer='county'") == 36)
    check("all 240 places are mappable",
          one("SELECT COUNT(*) FROM geo_entity WHERE layer='place'") == 240)
    # Special districts have no published boundaries anywhere. Nineteen were
    # reconstructed from Multnomah precinct assignments, and the point of this
    # check is that the number stays small and stays labelled: a recovered
    # boundary must never be filed as though the Census had published it.
    # The recovered layer and the Census government type do not describe the same
    # set: five of the reconstructed districts are community colleges and
    # education service districts, which the register files as School District.
    # Both counts are checked rather than forced to agree.
    reconstructed = one("SELECT COUNT(*) FROM geo_entity WHERE layer='special_district'")
    check("special district geometry exists only where it was reconstructed",
          0 < reconstructed < 40, str(reconstructed))
    check("and every reconstructed boundary says how it was made",
          one("SELECT COUNT(*) FROM geo_entity WHERE layer='special_district' "
              "AND match_method='multnomah_precinct_dissolve'") == reconstructed)
    check("some of them are filed under another government type, which is recorded",
          one("SELECT COUNT(*) FROM geo_entity g JOIN entities e ON e.pid6=g.pid6 "
              "WHERE g.layer='special_district' "
              "AND e.gov_type_name != 'Special District'") > 0)
    check("with the other 1,010 still carrying no boundary at all",
          one("SELECT COUNT(*) FROM entities e WHERE e.gov_type_name='Special District' "
              "AND e.pid6 NOT IN (SELECT pid6 FROM geo_entity)") > 1000)
    school = one("SELECT COUNT(*) FROM geo_entity WHERE layer='school_district'")
    check("school district geometry is partial and marked below high confidence",
          0 < school < 223 and one("SELECT COUNT(*) FROM geo_entity WHERE "
                                   "layer='school_district' AND confidence='high'") == 0,
          f"{school}/223 matched")

    print("\nDP03")
    check("three vintages loaded",
          one("SELECT COUNT(DISTINCT vintage) FROM dp03") == 3)
    check("county DP03 joins to county entities",
          one("SELECT COUNT(DISTINCT d.geo_id) FROM dp03 d JOIN entities e ON e.geoid=d.geo_id "
              "WHERE e.gov_type_name='County'") == 36)
    check("estimates and margins are separate columns",
          one("SELECT COUNT(*) FROM dp03 WHERE moe IS NOT NULL") > 0)

    print("\nnot computable by construction")
    # §8: an individual service area has one year and no prior-year figure, so
    # there must be no per-area time series in the store to query by mistake.
    for table in ("spending_by_service_area", "workforce_by_service_area", "financial_functions"):
        years = one(f"SELECT COUNT(*) FROM (SELECT pid6 FROM {table} "
                    "GROUP BY pid6 HAVING COUNT(DISTINCT year) > 1)")
        check(f"{table} holds a single year per entity, so no per-area YoY exists",
              years == 0, f"{years} entities have multiple years")

    print("\nbut the total those areas sum to does move")
    # The counterpart, and the reason the rule above used to be read too widely:
    # the breakdown is a snapshot inside a total that has a history, and that
    # history was discarded at load time until it was noticed.
    multi = one("SELECT COUNT(*) FROM (SELECT pid6 FROM service_area_totals "
                "GROUP BY pid6 HAVING COUNT(DISTINCT year) > 1)")
    check("service_area_totals carries more than one year for most entities",
          multi > 1000, f"only {multi} entities have a history")
    seven = one("SELECT COUNT(*) FROM (SELECT pid6 FROM service_area_totals "
                "GROUP BY pid6 HAVING COUNT(DISTINCT year) >= 7)")
    check("and a full seven years for several hundred", seven > 300,
          f"only {seven} entities reach seven years")

    # The two measures must stay distinguishable. If these ever agreed
    # everywhere the separation would be pointless; they disagree for three
    # quarters of the corpus, which is why one may never stand in for the other.
    differ = one("""
        SELECT COUNT(DISTINCT s.pid6) FROM service_area_totals s
        JOIN financial_trends f ON f.pid6 = s.pid6 AND f.year = s.year
        WHERE f.expenditure IS NOT NULL
          AND ABS(f.expenditure - s.total_expenditure) > 1""")
    check("the two expenditure measures are genuinely different series",
          differ > 500, f"only {differ} entities disagree, so the split may be moot")
    matched = one("""
        SELECT COUNT(*) FROM service_area_totals s
        JOIN operating_vs_capital o ON o.pid6 = s.pid6 AND o.year = s.year
        WHERE ABS(o.total_expenditure - s.total_expenditure) > 1""")
    check("and the service-area basis is operating plus capital, as its shares assume",
          matched < 10, f"{matched} rows disagree with operating_vs_capital")

    print("\nthe one join between money and people")
    bridge = one("SELECT COUNT(*) FROM function_bridge")
    check("the finance-to-workforce crosswalk is built", bridge > 30, f"{bridge} rows")
    # Many to many in both directions. A schema that assumed one-to-one would
    # let a row-to-row join through, and the per-head figure it produced would
    # look plausible whichever way it was wrong.
    fan_out = one("""SELECT COUNT(*) FROM (SELECT financial_function FROM function_bridge
                     GROUP BY financial_function HAVING COUNT(*) > 1)""")
    fan_in = one("""SELECT COUNT(*) FROM (SELECT employment_function FROM function_bridge
                    GROUP BY employment_function HAVING COUNT(*) > 1)""")
    check("and it fans out: one financial function to several employment ones",
          fan_out >= 3, f"{fan_out} financial functions map to more than one")
    check("and fans in: several financial functions to one employment one",
          fan_in >= 3, f"{fan_in} employment functions receive more than one")
    both = one("""SELECT COUNT(*) FROM function_bridge b
                  WHERE EXISTS (SELECT 1 FROM financial_functions f
                                WHERE f.function_name = b.financial_function)
                    AND EXISTS (SELECT 1 FROM workforce_top_functions w
                                WHERE w.function_name = b.employment_function)""")
    check("and both sides of it have live per-entity data", both >= 25,
          f"only {both} bridge rows have data on both sides")

    print("\ncoverage and recency trade against each other")
    # The Census of Governments is a full census in years ending 2 and 7 and a
    # sample in between. The vintage mix is that design, not a data problem.
    census_year = one("SELECT COUNT(DISTINCT pid6) FROM financial_functions WHERE year=2022")
    sample_year = one("SELECT COUNT(DISTINCT pid6) FROM financial_functions WHERE year=2023")
    check("the census year reaches more governments than the sample year",
          census_year > sample_year, f"2022 {census_year}, 2023 {sample_year}")
    big = one("""SELECT ROUND(AVG(w.population)) FROM workforce_profile w
                 WHERE w.population > 0 AND w.pid6 IN
                   (SELECT pid6 FROM financial_functions WHERE year=2023)""")
    small = one("""SELECT ROUND(AVG(w.population)) FROM workforce_profile w
                   WHERE w.population > 0 AND w.pid6 IN
                     (SELECT pid6 FROM financial_functions WHERE year=2022)""")
    check("and the sample year skews to the larger governments", big > small * 2,
          f"2023 mean population {big}, 2022 mean {small}")

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
