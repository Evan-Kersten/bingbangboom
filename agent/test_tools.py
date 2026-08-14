#!/usr/bin/env python3
"""Tests for the tool layer.

Asserts that each tool attaches the caveats the system prompt requires, on the
specific entities the prompt describes. A tool that returns the right number
without the right caveat is a failure here, because that is exactly the case
where the model produces a grounded-looking answer that breaks a rule.

    python3 agent/test_tools.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import format as fmt
import tools as T

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


def codes(result):
    return {c["code"] for c in result["caveats"]}


def blocked_codes(result):
    return {c["code"] for c in result["not_computable"]}


def pid_for(store, legal_name):
    row = store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)
    assert row, f"fixture entity missing: {legal_name}"
    return row["pid6"]


def main():
    store = T.Store()

    sumpter = pid_for(store, "CITY OF SUMPTER")
    baker_county = pid_for(store, "COUNTY OF BAKER")
    portland = pid_for(store, "CITY OF PORTLAND")
    burnt_river = pid_for(store, "BURNT RIVER SCH DIST 30J")
    new_bridge = pid_for(store, "NEW BRIDGE WATER SUPPLY DISTRICT")
    oregon = pid_for(store, "STATE OF OREGON")

    print("\nformatting (§4)")
    check("2.04B rounds to $2.0B", fmt.money(2_040_000_000) == "$2.0B", fmt.money(2_040_000_000))
    check("700M formats without decimals", fmt.money(700_000_000) == "$700M", fmt.money(700_000_000))
    check("843000 rounds to $840K", fmt.money(843_000) == "$840K", fmt.money(843_000))
    check("410000 formats as $410K", fmt.money(410_000) == "$410K", fmt.money(410_000))
    check("5000 stays legible as $5K", fmt.money(5_000) == "$5K", fmt.money(5_000))
    check("15.2% rounds to 15%", fmt.percent(15.2) == "15%", fmt.percent(15.2))
    check("2.72% keeps its decimal", fmt.percent(2.72) == "2.7%", fmt.percent(2.72))
    check("em dash is caught", any(v["kind"] == "dash" for v in fmt.check_style("a — b")))
    check("banned word is caught",
          any(v["detail"] == "robust" for v in fmt.check_style("a robust result")))
    check("banned word inside another word is not caught",
          not fmt.check_style("the targetable set is opportunistic") or
          all(v["detail"] not in ("target", "opportunity") for v in fmt.check_style("targetable")))
    check("unrounded figure is caught",
          any(v["kind"] == "unrounded_figure" for v in fmt.check_style("it spent $1,234,567")))

    print("\nget_financial_position (§8)")
    result = T.get_financial_position(store, sumpter)
    check("Sumpter carries the debt cap caveat", "debt_capped" in codes(result))
    check("Sumpter's driver is named as debt", result["data"]["driver"] == "debt_to_revenue",
          str(result["data"]["driver"]))
    check("financial position always blocks service-area YoY",
          "service_area_yoy" in blocked_codes(result))

    result = T.get_financial_position(store, baker_county)
    check("Baker County carries prior_year_missing", "prior_year_missing" in codes(result))
    check("Baker County reports no yoy_change", result["data"]["yoy_change"] is None)
    check("Oregon county delegation caveat is attached",
          "oregon_county_delegated" in codes(result) or
          "oregon_property_tax_constrained" in codes(result))

    print("\ncompare_to_peers (§8)")
    result = T.compare_to_peers(store, new_bridge, "capital_share")
    check("special district capital ratio is refused", result["data"]["ratio_refused"] is True)
    check("refusal carries the degenerate-median caveat",
          "peer_median_degenerate" in codes(result))
    check("no ratio is computed when refused", result["data"]["ratio"] is None)
    check("peer group and count are always returned",
          result["data"]["peer_group"] and result["data"]["peer_count"])

    result = T.compare_to_peers(store, oregon, "capital_share")
    check("state comparison is refused on peer group mismatch",
          "peer_group_mismatch" in codes(result) and result["data"]["ratio"] is None)

    result = T.compare_to_peers(store, portland, "fiscal_stability")
    check("a sound comparison does compute a ratio", result["data"]["ratio"] is not None,
          str(result["data"]))

    print("\nget_spending_breakdown (§8)")
    result = T.get_spending_breakdown(store, baker_county)
    check("breakdown always blocks service-area YoY",
          "service_area_yoy" in blocked_codes(result))
    check("breakdown carries the inputs-not-outcomes rule",
          "inputs_not_outcomes" in codes(result))
    check("breakdown carries the absence rule", "absence_means_another_entity" in codes(result))

    high_other = store.row(
        "SELECT pid6 FROM entity_flags WHERE other_share_high=1 AND pid6 IN "
        "(SELECT pid6 FROM spending_by_service_area)")
    result = T.get_spending_breakdown(store, high_other["pid6"])
    check("an entity with Other above 20% is told the breakdown is partial",
          "other_share_high" in codes(result))

    result = T.get_spending_breakdown(store, burnt_river)
    check("a school district is told composition carries no information",
          "single_category_entity" in codes(result))

    print("\nget_workforce (§8)")
    result = T.get_workforce(store, baker_county)
    check("workforce is always marked partial", "workforce_partial" in codes(result))
    check("workforce and finance vintages are separated", "vintage_split" in codes(result))

    # New Bridge reports zero employees and zero payroll. That is a real reported
    # zero, and must not be written up as a workforce profile.
    result = T.get_workforce(store, new_bridge)
    check("a reported zero workforce is distinguished from absent data",
          "reported_zero_workforce" in codes(result))
    check("and an entity with real staff is not flagged that way",
          "reported_zero_workforce" not in codes(T.get_workforce(store, baker_county)))

    print("\nrevenue_mix answers whose money it is (§9, §10, §12)")
    import explore
    schools = store.row("SELECT pid6 FROM entities WHERE legal_name='PORTLAND SCH DIST 1J'")["pid6"]
    result = explore.revenue_mix(store, baker_county)
    data = result["data"]
    sources = {r["category"]: r for r in data["sources"]}
    check("the split names property tax separately", "Property Tax" in sources,
          str(list(sources)))
    check("each source carries the median share for its type",
          sources["Property Tax"]["peer_median_share"] is not None)
    # Baker County takes more than half its revenue from other governments, which
    # is a structural fact about a rural county and the reason this preset exists.
    check("money raised here is separated from money sent here",
          data["transferred_pct"] > 50, str(data["transferred_pct"]))
    check("shares of revenue are never confused with shares of spending",
          "revenue_not_spending" in codes(result))
    check("a missing category is not reported as a collection of zero",
          "absent_category_is_not_zero" in codes(result))
    check("and exposure to a decision made elsewhere is stated",
          "transfer_exposure" in codes(result))

    result = explore.revenue_mix(store, schools)
    check("an Oregon school district carries the state-funding rule",
          "oregon_school_state_funded" in codes(result))

    print("\ndebt is reported against a year of revenue (§8, §10)")
    result = explore.debt_load(store, schools)
    data = result["data"]
    check("debt and revenue come from the same year",
          data["ratio"] and data["total_debt"] and data["revenue"])
    check("the ratio is debt over that year's revenue",
          abs(data["ratio"] - data["total_debt"] / data["revenue"]) < 1e-9)
    check("with the median across the same type in the same year",
          data["peers"] and data["peers"]["n"] > 4)
    check("a balance is never presented as an annual shortfall",
          "debt_is_not_a_deficit" in codes(result))
    check("and the basis of the ratio is stated",
          "debt_over_revenue_basis" in codes(result))

    # §9 again: nothing reported is not the same as nothing owed.
    silent = store.row(
        "SELECT pid6 FROM entities WHERE pid6 NOT IN "
        "(SELECT pid6 FROM financial_trends WHERE total_debt > 0) LIMIT 1")["pid6"]
    result = explore.debt_load(store, silent)
    check("a government reporting no debt is not called debt-free",
          "no_debt_reported" in codes(result))

    print("\nstaffing splits the payroll into jobs (§4, §8)")
    import explore
    schools = store.row("SELECT pid6 FROM entities WHERE legal_name='PORTLAND SCH DIST 1J'")["pid6"]
    result = explore.staffing(store, schools)
    data = result["data"]
    jobs = {r["role"] or r["function_name"]: r for r in data["functions"]}
    check("the split names instructional staff separately from the rest",
          "teaching and instructional staff" in jobs, str(list(jobs)))
    teaching = jobs["teaching and instructional staff"]
    check("with a headcount below the payroll total",
          0 < teaching["headcount"] < data["total_employees"])
    # The ratio is the point: 5,710 teachers is a fact about an organisation,
    # one per 8.5 students is a fact about a community.
    check("and a ratio against the students it serves",
          teaching["per_staff"] and 4 < teaching["per_staff"] < 40, str(teaching["per_staff"]))
    check("the unit follows the government type", data["units"] == "students")
    check("the ratio carries the median for the same job across the same type",
          teaching["peer_median_per_staff"] and teaching["peer_n"] > 4)
    check("a headcount is never presented as a service level",
          "headcount_is_not_service" in codes(result))
    check("and the payroll the named jobs do not reach is declared",
          "workforce_partial" in codes(result))

    result = explore.staffing(store, portland)
    jobs = {r["role"]: r for r in result["data"]["functions"] if r["role"]}
    check("a city's split names sworn officers and firefighters",
          "sworn police officers" in jobs and "firefighters" in jobs, str(list(jobs)))
    check("counted per resident, not per student", result["data"]["units"] == "residents")
    check("the named jobs are a minority of a city payroll and say so",
          result["data"]["covered_pct"] < 50, str(result["data"]["covered_pct"]))

    # No denominator means no ratio, rather than a ratio against a population
    # this government does not have.
    result = explore.staffing(store, new_bridge)
    check("a government with no staff split says so rather than inventing one",
          "no_staff_split" in codes(result) or not result["data"].get("functions"))

    print("\nper-capita refusal (§6)")
    no_pop = store.row("SELECT pid6 FROM entity_flags WHERE no_population_denominator=1")
    result = T.get_entity_profile(store, no_pop["pid6"])
    check("an entity with no population blocks per-capita",
          "per_capita_without_population" in blocked_codes(result))
    check("and carries the denominator caveat", "no_population_denominator" in codes(result))

    print("\nfind_salient (§11)")
    result = T.find_salient(store, sumpter)
    check("Sumpter's lead finding is internal divergence",
          result["data"]["lead"] and result["data"]["lead"]["tier"] == 1,
          str(result["data"]["lead"]))
    check("the reading names capital obligations rather than failure",
          "capital obligations" in (result["data"]["lead"] or {}).get("reading", ""))
    check("salience blocks outcome claims", "outcome_or_performance" in blocked_codes(result))

    quiet = None
    for row in store.rows("SELECT pid6 FROM entities LIMIT 400"):
        candidate = T.find_salient(store, row["pid6"])
        if candidate["data"]["nothing_unusual"]:
            quiet = candidate
            break
    check("an unremarkable entity is allowed to have no finding", quiet is not None)
    check("and is told not to manufacture one",
          quiet is not None and "nothing_unusual" in codes(quiet))

    print("\nlist_ecosystem (§9)")
    result = T.list_ecosystem(store, county_name="Multnomah")
    check("ecosystem states its assembly basis",
          "ecosystem_basis_host_county" in codes(result))
    check("ecosystem forbids naive summing", "ecosystem_no_summing" in codes(result))
    check("ecosystem warns that host county under-captures",
          "host_county_undercaptures" in codes(result))
    check("ecosystem blocks summing property tax",
          "property_tax_summed" in blocked_codes(result))
    check("ecosystem reports partial map coverage honestly",
          "partial_geometry" in codes(result) and
          result["data"]["mappable"] < result["data"]["total"],
          f"{result['data']['mappable']}/{result['data']['total']}")
    check("ecosystem returns multiple government types",
          len(result["data"]["by_type"]) >= 3, str(result["data"]["by_type"]))

    print("\nget_offices (§7)")
    result = T.get_offices(store, baker_county)
    check("offices disclose that holders are absent",
          "offices_have_no_holders" in codes(result))
    check("offices warn that coverage is uneven", "office_coverage_uneven" in codes(result))

    low = store.row("SELECT pid6 FROM office_entity_match WHERE confidence='low' AND pid6 IS NOT NULL")
    result = T.get_offices(store, low["pid6"])
    check("a low-confidence match is never presented as established",
          "office_match_low_confidence" in codes(result))

    print("\nmap_topic_to_categories (§12)")
    result = T.map_topic_to_categories(store, "homelessness")
    check("homelessness refuses a dollar figure",
          "issue_level_spending" in blocked_codes(result))
    check("homelessness names the gap", "topic_gap" in codes(result))
    check("homelessness fit is labeled weak", result["data"]["fit"] == "weak")
    check("homelessness notes DP03 cannot measure it",
          "dp03_cannot_measure_homelessness" in codes(result))
    check("but a proxy is still offered rather than a bare refusal",
          len(result["data"]["categories"]) > 0)

    result = T.map_topic_to_categories(store, "public safety")
    check("public safety is labeled a good fit", result["data"]["fit"] == "good")

    result = T.map_topic_to_categories(store, "unicycle licensing")
    check("an unmapped topic still refuses a figure",
          "issue_level_spending" in blocked_codes(result) and result["data"]["fit"] == "none")

    print("\ncompare_entities (§12)")
    result = T.compare_entities(store, [portland, baker_county])
    check("mixing a city and a county is flagged", "mixed_entity_types" in codes(result))
    check("and the Oregon delegation rule fires", "oregon_county_delegated" in codes(result))
    result = T.compare_entities(store, [portland, "000000"])
    check("missing entities are named", "entities_missing" in codes(result))

    print("\nrun_sql escape hatch")
    result = T.run_sql(store, "SELECT COUNT(*) AS n FROM entities")
    check("a read-only select runs", result["data"]["rows"][0]["n"] == 1529)
    check("raw rows carry a standing caveat", "raw_rows" in codes(result))
    check("writes are rejected",
          "write_rejected" in codes(T.run_sql(store, "DELETE FROM entities")))
    check("stacked statements are rejected",
          "write_rejected" in codes(T.run_sql(store, "SELECT 1; DROP TABLE entities")))
    check("pragma is rejected", "write_rejected" in codes(T.run_sql(store, "PRAGMA table_info(entities)")))
    check("non-selects are rejected", "not_a_select" in codes(T.run_sql(store, "table entities")))
    check("calls are logged as missing-tool candidates", len(store.sql_log) >= 1)

    print("\nenvelope shape")
    # Every tool returns the same envelope, whatever its arguments. The ones
    # that do not take a bare pid6 are called with their own minimum here rather
    # than skipped, so no tool escapes the shape check.
    extra_args = {
        "find_entity": {"name": "Baker"},
        "list_ecosystem": {"county_name": "Baker"},
        "map_topic_to_categories": {"topic": "housing"},
        "compare_entities": {"pid6_list": [baker_county]},
        "run_sql": {"sql": "SELECT 1 AS n"},
        "render_chart": {"pid6": baker_county, "form": "spending_composition"},
        "render_map": {"layer": "county"},
        "compare_normalized": {"pid6_list": [portland, baker_county]},
        "compose_report": {"kind": "entity", "pid6": baker_county},
        "compare_to_peer_group": {"pid6": baker_county, "metric": "expenditure_per_capita"},
        "compare_entities_over_time": {"pid6_list": [portland, baker_county]},
        "compare_service_area": {"pid6_list": [portland, baker_county],
                                 "service_area": "Public Safety"},
        "per_capita_by_service_area": {"pid6_list": [portland, baker_county],
                                       "service_area": "Public Safety"},
        "per_capita_over_time": {"pid6_list": [portland, baker_county]},
        "render_comparison": {"pid6_list": [portland, baker_county],
                              "form": "entities_over_time"},
        "who_spends_on": {"function_name": "Fire Protection"},
    }
    for name, tool in T.TOOLS.items():
        args = extra_args.get(name, {"pid6": baker_county})
        result = tool(store, **args)
        ok = all(k in result for k in
                 ("tool", "entity", "data", "caveats", "not_computable", "vintage"))
        check(f"{name} returns the full envelope", ok)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
