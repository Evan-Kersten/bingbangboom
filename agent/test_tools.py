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

    print("\noffices say how a seat is filled, not just that it exists (§7)")
    # The OCD id already carried this and nothing read it. A seat filed under a
    # sub-district is elected by that ward alone; a seat filed under the bare
    # jurisdiction with a numbered position is elected by everybody.
    parts = T.parse_ocd("ocd-division/country:us/state:or/place:x/council_district:ward_1")
    check("a sub-district is parsed off the jurisdiction",
          parts["subdivision"] == "ward_1" and parts["jurisdiction"].endswith("place:x"))
    check("and reads as a ballot would name it",
          T.district_label("council_district", "ward_1") == "Ward 1")
    # board_distict is misspelled in the source; the parser matches what is filed.
    check("the source's misspelled sub-district is still parsed",
          T.parse_ocd("ocd-division/x/board_distict:zone_2")["subdivision"] == "zone_2")
    check("a bare jurisdiction has no sub-district",
          T.parse_ocd("ocd-division/country:us/state:or/county:baker")["subdivision"] is None)

    schools = store.row("SELECT pid6 FROM entities WHERE legal_name='PORTLAND SCH DIST 1J'")["pid6"]
    data = T.get_offices(store, schools)["data"]
    board = next(r for r in data["roles"] if r["role"] == "School Board Member")
    check("a board elected by zone is marked as such", board["ballot"] == T.BY_DISTRICT,
          board["ballot"])
    check("and every zone is named", len(board["districts"]) == board["seat_count"])
    check("zones sort 2 before 10, not lexically",
          board["districts"] == sorted(board["districts"], key=T._seat_order))
    check("the reader is told a ballot holds one of them, not all",
          "seats_elected_by_district" in codes(T.get_offices(store, schools)))

    result = T.get_offices(store, baker_county)
    commissioners = next(r for r in result["data"]["roles"]
                         if r["role"] == "County Commissioner")
    check("a board elected at large is marked differently",
          commissioners["ballot"] == T.AT_LARGE, commissioners["ballot"])
    check("and the numbering is called a ballot label, not a geography",
          "seats_elected_at_large" in codes(result))
    check("a lone office is neither", next(
        r for r in result["data"]["roles"] if r["role"] == "County Sheriff"
    )["ballot"] == T.SINGLE)
    check("holders are still absent and the rule still says so",
          "offices_have_no_holders" in codes(result))

    print("\nevery government type gets a map (§9, §13)")
    # Special districts are two thirds of Oregon's governments and 1,010 have no
    # boundary anywhere here. "Cannot be put on a map" answered "where is this"
    # with nothing.
    tvfr = store.row("SELECT pid6 FROM entities WHERE legal_name="
                     "'TUALATIN VALLEY FIRE AND RESCUE DISTRICT'")["pid6"]
    result = T.render_locator_map(store, tvfr)
    check("a recovered special district is drawn as itself",
          result["data"]["basis"] == "own_boundary", str(result["data"]["basis"]))
    check("and the recovered boundary declares that it is reconstructed",
          "boundary_is_recovered" in codes(result))

    result = T.render_locator_map(store, new_bridge)
    check("a district with no boundary is still placed",
          result["data"]["basis"] == "filed_county" and result["data"]["svg"],
          str(result["data"]["basis"]))
    check("as a filing, never as an extent",
          "located_not_bounded" in codes(result))
    check("and §13's host-county rule travels with it",
          "host_county_is_a_filing" in codes(result))

    for name in ("CITY OF PORTLAND", "COUNTY OF BAKER", "PORTLAND SCH DIST 1J"):
        pid = store.row("SELECT pid6 FROM entities WHERE legal_name=?", name)["pid6"]
        drawn = T.render_locator_map(store, pid)["data"]
        check(f"{name.title()} is drawn as its own boundary",
              drawn["basis"] == "own_boundary" and drawn["svg"], str(drawn["basis"]))

    print("\nthe atlas vets a layer before anybody commits to it (§4, §15)")
    import atlas as A
    catalogue = {m["id"] for m in A.measures()}
    # Derived from the metric tables, never restated. A measure added to tools
    # or community and missing here is a measure nobody can reach, which looks
    # like a product decision rather than the drift it is.
    check("every mappable measure is in the atlas",
          catalogue >= set(T.MAP_METRICS) | set(T.SERVICE_AREA_MAP_METRICS)
          | set(T.FUNCTION_MAP_METRICS),
          str(sorted(catalogue)))
    import community as C
    check("and so is every survey indicator",
          catalogue >= {code for code, *_ in C.INDICATORS})

    # §4. Two maps side by side is the most persuasive way to assert that one
    # explains the other without writing a sentence anybody could argue with,
    # and spending against poverty is the pairing this domain reaches for first.
    paired = A.compare(store, ["spending_per_resident", "DP03_0128P"], "county")
    check("spending is never drawn beside a condition",
          paired["data"]["refused"] is True
          and "no_investment_against_conditions" in codes(paired))
    check("and the refusal carries no picture at all",
          not paired["data"]["panels"], str(paired["data"]["panels"])[:80])
    check("and it says what to do instead",
          "aggregate every" in next(c["guidance"] for c in paired["caveats"]
                                    if c["code"] == "no_investment_against_conditions"))

    both = A.compare(store, ["DP03_0128P", "DP03_0009P"], "county")
    check("two measures of the same kind do draw",
          all(p["drawn"] for p in both["data"]["panels"]))
    # A shared scale across two panels would say a colour means the same amount
    # in both, which is exactly the false comparison §15.4 is about.
    check("and the panels say they are on separate scales",
          "panels_have_separate_scales" in codes(both))

    # There are no tract-level DP03 rows in any of the three source files, so a
    # survey measure on a school district layer is not a coverage problem.
    off_layer = A.draw(store, "DP03_0128P", "school_district")
    check("a survey measure is refused on a layer it has no rows for",
          "layer_unavailable" in codes(off_layer))

    listing = A.catalogue(store, "county", service_area="Public Safety",
                          function="Fire Protection")
    verdicts = {row["measure"]: row["verdict"] for row in listing["data"]["rows"]}
    check("the catalogue reports a verdict for every measure",
          len(verdicts) == len(A.measures()), str(len(verdicts)))
    check("and fire protection is refused in it rather than quietly drawn",
          verdicts.get("Share of spending on Fire Protection") == "refused",
          str(verdicts))
    check("while police protection is drawn",
          verdicts.get("Share of spending on Police Protection") is None
          or verdicts.get("Spending per resident on Fire Protection") == "refused")
    check("and the catalogue dates itself, because coverage is a property of the build",
          "catalogue_is_a_snapshot" in codes(listing))

    print("\nthe brief answers a place and an issue together (§12, §9)")
    estacada = pid_for(store, "CITY OF ESTACADA")
    fire = T.TOOLS["place_topic_brief"](store, estacada, "wildfire")
    data = fire["data"]
    # §12: the gap is not a footnote. Nothing downstream is allowed to read as a
    # measure of the topic, so the fit rides on the result rather than in prose.
    check("the brief states how loosely the categories fit",
          data["fit"] == "weak" and data["fit_meaning"], str(data["fit"]))
    check("and forbids an issue-level dollar figure outright",
          "issue_level_spending" in blocked_codes(fire))
    check("the topic gap caveat leads the rules",
          fire["caveats"][0]["code"] == "topic_gap", str(fire["caveats"][0]["code"]))
    # §9: the same dollar appears in two entities' expenditures, so a total
    # across a stack is a number with no referent. The absence of the key is
    # the guarantee; a renderer cannot print what was never computed.
    check("no total is computed across the stack",
          not any(k in data for k in ("total", "combined", "sum")), str(list(data)))
    # The point of the whole thing: fire is district work, the districts have no
    # boundary, and a stack that quietly omitted them would say the city and the
    # county are all there is.
    check("districts that do the work but cannot be placed are still surfaced",
          any("Fire" in row["name"] for row in data["elsewhere"]),
          str([r["name"] for r in data["elsewhere"]][:3]))
    check("and are marked as a filing rather than a location",
          "districts_filed_not_placed" in codes(fire))
    check("the stack itself is never presented as complete",
          "serving_stack_incomplete" in codes(fire))

    # A topic the concordance does not cover is answered, not refused: §12 says
    # a bare refusal is less useful than a labelled approximation, and the stack
    # is still the honest half of the answer.
    unknown = T.TOOLS["place_topic_brief"](store, estacada, "broadband")
    check("an uncovered topic returns the gap and still names the governments",
          unknown["data"]["fit"] == "none" and not unknown["data"]["categories"]
          and unknown["data"]["stack"], str(unknown["data"]["fit"]))

    # §4: conditions are not the government's record, and a figure whose margin
    # swamps it must not be quotable.
    check("conditions arrive with a reliability verdict on each figure",
          all("reliability" in i for i in data["conditions"]))
    check("and are marked as not being anybody's results",
          "conditions_are_not_a_record" in codes(fire))
    wide = [i for i in data["conditions"] if i["reliability"] == "soft"]
    check("a small town's estimates are mostly too wide to quote", bool(wide),
          str([(i["name"], i["moe"]) for i in data["conditions"]]))

    # Asked of a district, "which governments serve you" is the question
    # answering itself, and the brief inherits that refusal rather than
    # inventing a stack.
    district = T.TOOLS["place_topic_brief"](store, new_bridge, "water")
    check("asked of a district rather than a town, the stack is refused",
          "not_a_place" in codes(district), str(codes(district)))

    print("\nthe drill from service area to function reaches the map (§15.1)")
    # The case this gate exists for. Fire protection is done by 243 special
    # districts and 83 cities; one of those districts has a boundary anywhere in
    # this data. Drawn on places it colours the cities with their own department
    # and leaves rural Oregon in the no-data neutral, which reads as an absence
    # of fire spending where it is an absence of drawable fire districts.
    fire = T.render_function_map(store, "Fire Protection")
    check("fire protection is refused on every layer",
          fire["data"]["refused"] is True, str(fire["data"]["layer"]))
    check("and the refusal is drawn rather than left blank",
          "fire protection" in fire["data"]["svg"].lower())
    check("and it names the districts doing the work",
          any(h["gov_type"] == "Special District" and h["unmapped"]
              for h in fire["data"]["holders"]), str(fire["data"]["holders"]))
    check("and says so as a rule, not only as a picture",
          "map_mostly_absence" in codes(fire))
    # The sibling function inside the same service area draws, which is the
    # whole point: the refusal is a fact about who does the work rather than a
    # blanket rule about mapping functions.
    police = T.render_function_map(store, "Police Protection")
    check("police protection, in the same service area, draws",
          police["data"]["refused"] is False and police["data"]["layer"] == "county",
          str(police["data"]["layer"]))
    check("and still states what share of the work its layer holds",
          "map_layer_holds_minority" in codes(police))

    # Whoever does the work picks the layer. Sewerage is municipal, so counties
    # would compare a city against governments that mostly run no sewers.
    sewerage = T.render_function_map(store, "Sewerage")
    check("a municipal function is drawn on places, not counties",
          sewerage["data"]["layer"] == "place", str(sewerage["data"]["layer"]))
    schools = T.render_function_map(store, "Elementary & Secondary Education")
    check("and a school function on school districts",
          schools["data"]["layer"] == "school_district", str(schools["data"]["layer"]))

    coverage = T.map_coverage(store, "function", "Fire Protection", "place")
    check("coverage counts the money, not the entities",
          0.4 < coverage["layer_share"] < 0.6, str(coverage["layer_share"]))
    check("the state is held out of the local denominator",
          coverage["total"] > 0 and not any(
              h["gov_type"] == "State" for h in coverage["holders"]))
    # A city missing from a county map has a boundary and is on another layer.
    # A fire district has none at all. Telling a reader Oregon's cities are
    # unmapped would be false, and the first draft of this said exactly that.
    county_view = T.map_coverage(store, "function", "Fire Protection", "county")
    kinds = {(h["gov_type"], h["unmapped"]) for h in county_view["holders"]}
    check("a government on another layer is not called unmapped",
          ("Municipal", False) in kinds, str(kinds))
    check("and one with no boundary anywhere is",
          ("Special District", True) in kinds, str(kinds))
    # The recovered special-district layer is 19 unrelated boundaries. Offering
    # it as somewhere else to draw invites a map of nineteen districts.
    check("the recovered layer is never offered as an alternative",
          all(h["layer"] != "special_district" for h in county_view["holders"]),
          str(county_view["holders"]))

    print("\nthe gate is measured, and the measure is one number (§15.1)")
    import rules as R
    import blocks as B
    check("the tool and the block validator share one floor",
          B.MAP_COVERAGE_FLOOR == R.THRESHOLDS["map_density_minimum"])
    thin = T.render_map(store, "place", "function_per_resident",
                        function="Sewerage", county="Gilliam")
    check("an extent of a couple of polygons is refused as too thin to read",
          thin["data"]["refused"] is True
          and thin["data"]["polygons"] < R.THRESHOLDS["map_min_polygons"],
          str(thin["data"]["polygons"]))
    # A statewide place map is 378 specks and the reader compares land areas.
    # Every caller scopes place maps; this is what happens when one does not.
    statewide = T.render_map(store, "place", "function_per_resident", function="Sewerage")
    check("a statewide place map is refused as unreadable",
          statewide["data"]["refused_because"] == "needs_a_county",
          str(statewide["data"].get("refused_because")))
    scoped = T.render_map(store, "place", "function_per_resident",
                          function="Sewerage", county="Multnomah")
    check("and the same map scoped to a county draws",
          scoped["data"]["refused"] is False, str(scoped["data"]["polygons"]))
    # The reasons are ranked, strongest first. Fire protection is a place-layer
    # function and unscoped, so the weak reason applies to it too, and reporting
    # that one would send a reader off to scope a map that will still be empty.
    check("mostly-absence outranks needing a county",
          fire["data"]["refused_because"] == "mostly_absence",
          str(fire["data"]["refused_because"]))

    missing = T.render_map(store, "county", "function_per_resident")
    check("a function metric without a function says which argument is missing",
          "function_required" in codes(missing))
    unknown = T.render_map(store, "county", "function_per_resident",
                           function="Sanitation Marketing")
    check("and an invented function name is refused rather than drawn empty",
          "no_such_function" in codes(unknown))

    # The precinct source is state plane feet and every other layer is degrees.
    # Mixed without conversion, Oregon shrinks to a dot and the district lands
    # off frame — which is what happened the first time this was drawn.
    import json as _json
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "build", "geo",
            "special_district.geojson")) as fh:
        recovered = _json.load(fh)["features"]
    corner = recovered[0]["geometry"]["coordinates"][0][0][0]
    check("recovered boundaries are stored in longitude and latitude",
          -125 < corner[0] < -116 and 41 < corner[1] < 47, str(corner))

    print("\na seat is drawn on the ground that elects it (§7, §9)")
    multnomah = store.row("SELECT pid6 FROM entities WHERE legal_name='COUNTY OF MULTNOMAH'")["pid6"]
    result = T.render_office_map(store, multnomah)
    data = result["data"]
    check("a districted body is drawn as its districts", data["districts"] == 4,
          str(data["districts"]))
    check("from the dissolved precinct source",
          data["source"] == "multnomah_precinct_dissolve", str(data["source"]))
    check("and the edges are declared approximate",
          "district_boundaries_are_approximate" in codes(result))

    # Baker County elects its commissioners at large, so the electorate is the
    # county. Drawing three shapes there would invent three geographies.
    result = T.render_office_map(store, baker_county)
    data = result["data"]
    check("an at-large body is drawn as one electorate", data["districts"] == 1,
          str(data["districts"]))
    check("from a boundary layer already here", data["source"] == "county",
          str(data["source"]))
    check("and the numbering is not presented as geography",
          "at_large_is_one_electorate" in codes(result))

    # §9: a districted body outside Multnomah has no district geometry, and
    # drawing the whole jurisdiction would tell a reader they elect every seat.
    schools = store.row("SELECT pid6 FROM entities WHERE legal_name='PORTLAND SCH DIST 1J'")["pid6"]
    result = T.render_office_map(store, schools)
    check("a districted body with no boundaries is refused, not approximated",
          result["data"]["svg"] is None and "no_district_geometry" in codes(result))

    check("the role name is not doubled into the title",
          T._seat_noun("Community College District Director") == "director")

    print("\nthe place beside the government, never joined to it (§4, §8)")
    import community as C
    sumpter = store.row("SELECT pid6 FROM entities WHERE legal_name='CITY OF SUMPTER'")["pid6"]

    result = C.community_context(store, portland)
    data = result["data"]
    check("a city gets its own Census geography", data["basis"] == "own")
    check("with every tracked indicator", len(data["indicators"]) >= 5)
    check("and each estimate carries its margin",
          all(i["moe"] is not None for i in data["indicators"]))
    check("conditions are never presented as the government's results",
          "conditions_are_not_outcomes" in codes(result))
    check("and the survey is not called a count", "survey_not_census" in codes(result))

    # A school district is not a Census geography. Standing its host county in
    # for it is defensible; doing so silently is not.
    schools = store.row("SELECT pid6 FROM entities WHERE legal_name='PORTLAND SCH DIST 1J'")["pid6"]
    result = C.community_context(store, schools)
    check("a district falls back to its host county and says so",
          result["data"]["basis"] == "host_county"
          and "context_is_the_county" in codes(result))

    # Sumpter's unemployment estimate is 20% give or take 30 points. Reporting
    # that as a figure is the failure this flag exists to prevent.
    result = C.community_context(store, sumpter)
    check("an estimate whose margin swamps it is marked unreliable",
          any(i["reliability"] == "soft" for i in result["data"]["indicators"]))
    check("and the answer is told to report the margin or nothing",
          "margin_too_wide" in codes(result))

    print("\ntwo ACS releases are compared by the Census test, not by eye (§8)")
    # Portland's poverty rate moved 1.0 points against margins of 0.4 and 0.6.
    # Summing those says no; the root sum of squares — which is the documented
    # test — says yes. The wrong arithmetic hides a real finding.
    moved = C.since_pandemic(store, portland, "DP03_0128P")
    check("a difference outside the margins is reported as real",
          moved["data"]["separated_from_margin"] is True,
          str(moved["data"].get("margin_span")))
    check("and the overlapping windows are declared",
          "overlapping_windows" in codes(moved))

    held = C.since_pandemic(store, sumpter, "DP03_0128P")
    check("a difference inside the margins is not called a change",
          held["data"]["separated_from_margin"] is False)
    check("and the answer is forbidden a direction",
          "within_the_margin" in codes(held))

    dollars = C.since_pandemic(store, portland, "DP03_0062")
    check("comparing dollar figures across releases is refused outright",
          "vintage_dollars" in codes(dollars) and not dollars["data"].get("points"))

    print("\na map the survey cannot support is refused, not drawn thin (§8)")
    good = C.render_community_map(store, "DP03_0128P", geo_level="county")
    check("counties carry a poverty map", bool(good["data"].get("svg")))
    check("with the withheld geographies named, not silently grey",
          "withheld_for_margin" in codes(good) or good["data"]["withheld"] == 0)

    # 20 of 420 Oregon towns have an unemployment estimate tight enough to bin.
    thin = C.render_community_map(store, "DP03_0009P", geo_level="place")
    check("a place-level unemployment map is refused",
          thin["data"].get("svg") is None and "too_thin_to_map" in codes(thin))
    check("and the refusal points at the grain that works",
          "county level" in " ".join(c["guidance"] for c in thin["caveats"]))

    print("\ntwo totals are both called expenditure and are not the same (§10)")
    import explore
    corvallis = store.row("SELECT pid6 FROM entities WHERE legal_name='CITY OF CORVALLIS'")["pid6"]
    reported = dict(explore.measure_series(store, corvallis, "expenditure", 2017, 2023))
    basis = dict(explore.measure_series(store, corvallis, explore.SERVICE_AREA_BASIS,
                                        2017, 2023))
    check("both measures have a series for the same entity",
          len(reported) > 4 and len(basis) > 4)
    check("and they are genuinely different numbers",
          any(abs(reported[y] - basis[y]) > 1 for y in reported if y in basis),
          f"{reported} vs {basis}")

    # The service-area shares are computed against the second one, so the second
    # one is what the breakdown's total must agree with.
    row = store.row("SELECT total_expenditure FROM operating_vs_capital WHERE pid6=?",
                    corvallis)
    latest = max(basis)
    check("the service-area basis is operating plus capital",
          abs(basis[latest] - row["total_expenditure"]) < 1,
          f"{basis[latest]} vs {row['total_expenditure']}")

    # §8 still holds for an individual area: one year, no prior-year figure.
    areas = store.rows("SELECT COUNT(DISTINCT year) y FROM spending_by_service_area "
                       "WHERE pid6=? GROUP BY service_area", corvallis)
    check("an individual service area still has no year-over-year",
          all(a["y"] == 1 for a in areas))

    drawn = T.render_comparison(store, [corvallis, portland], "five_year_change",
                                measure=explore.SERVICE_AREA_BASIS)
    check("a chart on either measure carries the rule that names it",
          "two_expenditure_measures" in codes(drawn))
    check("and the two measures are labelled apart",
          T.MEASURE_LABELS["expenditure"] != T.MEASURE_LABELS["service_area_basis"])

    chart = T.render_chart(store, corvallis, "finances_over_time")
    check("the trend chart draws both measures rather than picking one",
          any("operating and capital" in r for r in (chart["data"]["table"] or [{}])[0]),
          str(list((chart["data"]["table"] or [{}])[0])))

    print("\nfive years is two panels, not one (§8)")
    # 1,005 governments filed 2017 and 2022 and nothing between; 313 filed every
    # year. Drawn as lines on one axis those are indistinguishable, and a reader
    # reads a path through five years nobody reported.
    import explore
    keizer = store.row("SELECT pid6 FROM entities WHERE legal_name='CITY OF KEIZER'")["pid6"]
    result = explore.trend_panel(store, [portland, keizer])
    by_label = {s["label"]: s for s in result["data"]["series"]}
    check("an annual filer is classified as annual",
          by_label["Portland"]["coverage"] == explore.ANNUAL,
          by_label["Portland"]["coverage"])
    check("and a two-endpoint filer never reaches the annual panel",
          "Keizer" not in by_label, str(list(by_label)))
    check("the window is stated rather than inferred",
          "window_is_stated" in codes(result))
    check("and it is the window the data supports",
          (result["data"]["start"], result["data"]["end"]) == (2019, 2023))

    # coverage_of is the classifier both engines share.
    check("consecutive years are annual",
          explore.coverage_of([2019, 2020, 2021, 2022, 2023], 2019, 2023)[0]
          == explore.ANNUAL)
    check("two years with a gap are endpoints",
          explore.coverage_of([2019, 2023], 2019, 2023)[0] == explore.ENDPOINTS)
    check("one year is not a series at all",
          explore.coverage_of([2021], 2019, 2023)[0] == explore.SINGLE)

    result = explore.compare_change(store, [portland, keizer])
    rows = {r["label"]: r for r in result["data"]["entities"]}
    check("the change form reaches a government the panel cannot",
          "Keizer" in rows and "Portland" in rows, str(list(rows)))
    check("each row says how many years inside the window it filed",
          rows["Keizer"]["observations"] == 2 and rows["Portland"]["observations"] > 2)
    check("a change is never presented as a trend",
          "change_not_trend" in codes(result))
    check("and the figures are declared nominal",
          "no_price_index" in codes(result))
    check("with the median change for the same type alongside",
          rows["Portland"]["peer_median"] is not None
          and rows["Portland"]["peer_n"] > 4)

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

    print("\ngovernments_serving (§9)")
    result = T.governments_serving(store, portland)
    names = [r["name"] for r in result["data"]["serving"]]
    check("the stack names governments beyond the city itself",
          len(names) > 3, str(len(names)))
    check("the city is in its own stack",
          result["data"]["serving"][0]["basis"] == "self", str(names[:1]))
    check("and it leads, because a reader is inside the stack rather than beside it",
          result["data"]["serving"][0]["gov_type_name"] == "Municipal")
    check("every row says what established it",
          all(r["basis_label"] for r in result["data"]["serving"]))
    check("a stack is never presented as complete",
          "serving_stack_incomplete" in codes(result))
    check("mixed evidence is disclosed as mixed",
          "serving_basis_differs" in codes(result))
    check("summing across the stack is blocked",
          "property_tax_summed" in blocked_codes(result))
    # Portland is inside Multnomah, the only county with a precinct file. Its
    # count is close to true, so the shortfall caveat must stay off — a warning
    # that fires everywhere warns about nothing.
    check("a county with a precinct file is not warned about a short stack",
          "stack_understated" not in codes(result))
    check("Multnomah rows rest on the precinct record",
          result["data"]["by_basis"].get("precinct_exact", 0) >= 5,
          str(result["data"]["by_basis"]))

    outside = store.row(
        "SELECT s.place_pid6 FROM serves_place s JOIN entities e ON e.pid6=s.place_pid6 "
        "WHERE e.host_county != 'MULTNOMAH' LIMIT 1")
    result = T.governments_serving(store, outside["place_pid6"])
    check("a town with no precinct file is told the stack is understated",
          "stack_understated" in codes(result))

    result = T.governments_serving(store, baker_county)
    check("a county is not asked which governments serve it",
          "not_a_place" in codes(result))
    check("and that refusal returns no stack", not result["data"].get("serving"))

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
        "render_function_map": {"function": "Police Protection"},
        "place_topic_brief": {"pid6": portland, "topic": "housing"},
        "atlas_measures": {},
        "atlas_draw": {"measure_id": "spending_per_resident"},
        "atlas_compare": {"measure_ids": ["DP03_0128P", "DP03_0009P"]},
        "atlas_catalogue": {},
        "compare_change": {"pid6_list": [portland, baker_county]},
        "render_community_map": {"code": "DP03_0128P", "geo_level": "county"},
        "trend_panel": {"pid6_list": [portland, baker_county]},
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
