#!/usr/bin/env python3
"""Smoke tests for the app's API, without a browser or a running server.

Calls the handlers directly. What matters here is that a preset returns real
figures and the rules that bind them, and that the interface never offers a
question the data cannot answer.

    python3 app/test_server.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server as S
from server import R

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
    store = S.STORE

    def pid(legal_name):
        return store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)["pid6"]

    sumpter = pid("CITY OF SUMPTER")
    baker = pid("COUNTY OF BAKER")
    new_bridge = pid("NEW BRIDGE WATER SUPPLY DISTRICT")

    print("\nevery preset answers without raising")
    for preset_id, label, _, _, _ in S.PRESETS:
        result = S.answer_preset(preset_id, baker)
        check(f"{preset_id} returns blocks", bool(result["blocks"]), label)
        check(f"{preset_id} conforms to §15", result["violations"] == [],
              str(result["violations"]))

    print("\nno preset is built on the fiscal stability score")
    # §8: a composite whose two components move for opposite reasons tells a
    # reader less than any single measure underneath it, so no preset opens on
    # it and no preset label names it.
    check("no preset label mentions the score",
          not any("stability" in label.lower() for _, label, _, _, _ in S.PRESETS),
          str([label for _, label, _, _, _ in S.PRESETS if "stability" in label.lower()]))
    check("and no preset is gated on having one",
          not any(needs == "has_fiscal_stability" for _, _, _, needs, _ in S.PRESETS))
    traces = {tool for preset_id, _, _, _, _ in S.PRESETS
              for tool in S.answer_preset(preset_id, baker)["trace"]}
    check("and no preset reaches the fiscal stability tool",
          "get_financial_position" not in traces, str(sorted(traces)))

    print("\npresets carry their rules and their trace")
    result = S.answer_preset("scale", sumpter)
    limits = next((b for b in result["blocks"] if b["kind"] == "limits"), {"rules": []})
    codes = {r["code"] for r in limits["rules"]}
    check("the peer group and its count reach the interface",
          "peer_group_stated" in codes, str(sorted(codes)))
    check("a never-rule is marked as one",
          any(r["kind"] == "never" for r in limits["rules"]))
    check("the tool trace is reported", "compare_to_peer_group" in result["trace"])
    check("a chart is returned", any(b["kind"] == "chart" for b in result["blocks"]))

    figure = next((b for b in result["blocks"] if b["kind"] == "figure"), None)
    check("the figure carries the comparison it is read against",
          bool(figure and figure.get("context") and "median" in figure["context"]),
          str(figure and figure.get("context")))
    check("and the answer states where in the distribution it sits",
          any("quarter" in b.get("text", "") or "middle half" in b.get("text", "")
              for b in result["blocks"]))
    check("the response conforms to §15", result["violations"] == [],
          str(result["violations"]))
    check("it opens with an interpretation block",
          result["blocks"][0]["kind"] == "interpretation")
    check("and closes by offering only answerable follow-ups",
          result["blocks"][-1]["kind"] == "next")

    print("\na government is placed on a map, not only described")
    # Maps used to appear in two place-scoped presets only, both in the last
    # group, so a reader exploring one government never saw one. A district with
    # no boundary of its own used to get nothing at all; it now gets the county
    # it files under, which is a placement rather than an extent.
    for name in ("COUNTY OF BAKER", "CITY OF PORTLAND",
                 "NEW BRIDGE WATER SUPPLY DISTRICT"):
        row = store.row("SELECT pid6 FROM entities WHERE legal_name=?", name)
        result = S.answer_preset("scale", row["pid6"])
        check(f"{name.title()}: drawn on a map",
              any(b["kind"] == "map" for b in result["blocks"]))
        check(f"{name.title()}: still conforms to §15", result["violations"] == [],
              str(result["violations"]))
    answer = " ".join(b.get("text", "") for b in
                      S.answer_preset("scale", store.row(
                          "SELECT pid6 FROM entities WHERE legal_name="
                          "'NEW BRIDGE WATER SUPPLY DISTRICT'")["pid6"])["blocks"])
    check("and an entity with no boundary of its own is told so, on the map",
          "no boundary in this data" in answer, answer[:120])

    print("\na reader who cannot name a government can still find one")
    # Search over 1,529 names only finds what somebody already knew existed,
    # which excludes the districts that actually do the work.
    import browse_data
    doors = browse_data.build(store)
    check("all 36 counties are offered as a way in", len(doors["counties"]) == 36)
    check("every government type is offered with its count",
          len(doors["types"]) == 4 and all(t["count"] > 0 for t in doors["types"]))
    check("and the named things governments do are offered too",
          len(doors["functions"]) >= 30,
          str(len(doors["functions"])))
    check("each door states how many governments are behind it",
          all(f["governments"] > 0 for f in doors["functions"]))

    fire = S.answer_function("Fire Protection")
    text = " ".join(b.get("text", "") for b in fire["blocks"])
    table = next((b for b in fire["blocks"] if b["kind"] == "table"), None)
    check("asking who does a thing returns governments", bool(table and table["rows"]))
    check("including ones a reader could not have named",
          any(r["type"] == "Special District" for r in table["rows"]),
          str([r["type"] for r in table["rows"]][:4]))
    check("the count of everyone doing it is stated, not just the top rows",
          "327" in text, text[:80])
    check("the ranking says it ranks by size", "ranking by size" in text)
    check("a government with no denominator says so rather than showing a zero",
          any(r["per resident or student"] == "no denominator" for r in table["rows"]))
    # The list is mixed by design, so the unit rides on each figure. A city's row
    # says per resident and a school district's says per student, and neither
    # sits under a heading that claims the other.
    check("and every figure in that column carries the unit it is per",
          all(r["per resident or student"] == "no denominator"
              or r["per resident or student"].endswith((" per resident", " per student"))
              for r in table["rows"]),
          str([r["per resident or student"] for r in table["rows"]][:4]))
    check("and the answer places the service on a map",
          any(b["kind"] == "map" for b in fire["blocks"]))
    check("it conforms to §15", fire["violations"] == [], str(fire["violations"]))

    missing = S.answer_function("Nothing Like This")
    check("an unknown function is refused rather than invented",
          "No government reports" in missing["blocks"][0]["text"])

    print("\na per-capita figure names the unit it is per")
    # The payload calls one field population for every type, and it is enrollment
    # for a school district: Portland School District 1J reports 48,601 against a
    # city of 641,162, with 7,608 staff. Labelling that "per resident" is wrong.
    import rules
    check("a city is measured per resident", rules.per_unit_label("Municipal") == "per resident")
    check("a school district is measured per student",
          rules.per_unit_label("School District") == "per student")
    check("a special district has no denominator at all",
          rules.per_unit_label("Special District") is None)

    college = store.row("SELECT pid6 FROM entities WHERE gov_type_name='School District' "
                        "AND legal_name LIKE '%PORTLAND%' LIMIT 1")["pid6"]
    district_answer = " ".join(b.get("text", "") for b in
                               S.answer_preset("scale", college)["blocks"])
    check("and the answer for one says student, not resident",
          "per student" in district_answer and "per resident" not in district_answer,
          district_answer[:110])

    print("\nthe presets open on who would have to act")
    # Ordered for somebody preparing for a meeting, not for somebody who already
    # reads municipal finance. Appendix A: keep the serving question prominent,
    # because it is the one this platform answers better than the alternatives
    # and most users do not know the ecosystem exists until they see it.
    first_group = S.PRESETS[0][2]
    check("the first group is who has a claim on the place",
          first_group == "Who is at the table", first_group)
    check("and it opens on which governments serve here",
          S.PRESETS[0][0] == "serving", S.PRESETS[0][0])
    # Appendix A again: the limits group stays visible and phrased as real
    # questions. Losing it in a regroup is the easy mistake.
    groups = [group for _, _, group, _, _ in S.PRESETS]
    check("and the limits group survives the grouping",
          "What this cannot settle" in groups, str(sorted(set(groups))))
    check("every group is named for a job rather than a table",
          not ({"Where it comes from", "Follow the money", "Reports"} & set(groups)),
          str(sorted(set(groups))))

    revenue = S.answer_preset("revenue_mix", baker)
    text = " ".join(b.get("text", "") for b in revenue["blocks"])
    check("the revenue answer names property tax in the reader's terms",
          "property tax" in text.lower(), text[:120])
    check("and says how much of the budget is decided elsewhere",
          "transferred in" in text, text[:200])
    check("it conforms to §15", revenue["violations"] == [], str(revenue["violations"]))
    table = next(b for b in revenue["blocks"] if b["kind"] == "table")
    check("every revenue line says what it is",
          all(r["what it is"] for r in table["rows"]))

    debt = S.answer_preset("debt", baker)
    text = " ".join(b.get("text", "") for b in debt["blocks"])
    check("debt is expressed against a year of revenue",
          "times what it takes in" in text, text[:160])
    check("and is never called a shortfall",
          "balance, not an annual shortfall" in text)
    check("it conforms to §15", debt["violations"] == [], str(debt["violations"]))

    print("\nthe payroll is split into jobs a community would name")
    staff = S.answer_preset("workforce", pid("CITY OF PORTLAND"))
    text = " ".join(b.get("text", "") for b in staff["blocks"])
    check("a city's answer names sworn officers, not a headcount alone",
          "sworn police officers" in text, text[:200])
    check("and gives the ratio against the people served",
          "one for every" in text and "residents" in text, text[:240])
    school_staff = " ".join(b.get("text", "") for b in
                            S.answer_preset("workforce", college)["blocks"])
    check("a school district's ratio is per student, not per resident",
          "students" in school_staff and "one for every" in school_staff,
          school_staff[:200])

    # §9: a government that reports zero staff has arranged the work differently.
    # "average wage of None" is not a description of that.
    zero = S.answer_preset("workforce", new_bridge)
    text = " ".join(b.get("text", "") for b in zero["blocks"])
    check("a reported zero payroll is explained rather than printed",
          "reported zero" in text and "None" not in text, text[:180])

    print("\ngovernance says how a seat is filled, not just that it exists")
    gov = S.answer_preset("governance", baker)
    text = " ".join(b.get("text", "") for b in gov["blocks"])
    check("the answer counts the seats a reader can vote on",
          "filled by election" in text, text[:160])
    check("and states the term length", "Terms run" in text, text[:160])
    check("holders are still absent and said to be",
          "No holder names" in text)
    rows = next(b for b in gov["blocks"] if b["kind"] == "table")["rows"]
    check("each role says how it is filled and for how long",
          all(r["filled by"] and r["term"] for r in rows))

    print("\ngovernance draws the ground a seat answers to")
    multnomah = pid("COUNTY OF MULTNOMAH")
    gov = S.answer_preset("governance", multnomah)
    text = " ".join(b.get("text", "") for b in gov["blocks"])
    check("a districted body says a voter fills one seat, not all",
          "covering their address rather than all of them" in text, text[-200:])
    check("and the districts are drawn",
          any(b["kind"] == "map" for b in gov["blocks"]))
    check("it conforms to §15", gov["violations"] == [], str(gov["violations"]))

    at_large = " ".join(b.get("text", "") for b in
                        S.answer_preset("governance", baker)["blocks"])
    check("an at-large body says every voter votes on all of them",
          "every voter inside it votes on all" in at_large, at_large[-200:])
    check("and its ballot numbers are called labels, not places",
          "labels, not places" in at_large)

    # §9 again: no boundary is a stated absence, not a jurisdiction drawn as if
    # it were one electorate.
    schools = " ".join(b.get("text", "") for b in
                       S.answer_preset("governance", college)["blocks"])
    check("a districted body with no boundary says the map is missing",
          "map is missing rather than drawn as one electorate" in schools
          or "filled by district" in schools, schools[-220:])

    print("\nno government is left off the map")
    # The interface used to say a special district "cannot be put on a map",
    # which is two thirds of Oregon's governments answered with nothing.
    import random
    everyone = store.rows("SELECT pid6, gov_type_name FROM entities")
    random.seed(11)
    sample = random.sample(everyone, 60)
    without = [r for r in sample
               if not any(b["kind"] == "map"
                          for b in S.answer_preset("scale", r["pid6"])["blocks"])]
    check("a sample across all four types is placed, every one",
          not without, str([r["gov_type_name"] for r in without][:4]))

    district = " ".join(b.get("text", "") for b in
                        S.answer_preset("scale", new_bridge)["blocks"])
    check("a district with no boundary is placed by its filing",
          "files under" in district, district[-200:])
    check("and the filing is never called a service area",
          "locates the filing" in district or "not the service area" in district,
          district[-200:])

    entity_report = R.compose_report(store, kind="entity", pid6=new_bridge)
    ids = [s["id"] for s in entity_report["data"]["sections"]]
    check("the report says where the government is", "where" in ids, str(ids))

    print("\nthe community sits beside the government, never inside it")
    community = S.answer_preset("community", pid("CITY OF PORTLAND"))
    text = " ".join(b.get("text", "") for b in community["blocks"])
    check("the place is described in its own terms",
          "American Community Survey" in text, text[:140])
    check("and never as something the government achieved",
          "not what the government" in text or "not what the governme" in text,
          text[-160:])
    check("it conforms to §15", community["violations"] == [],
          str(community["violations"]))
    rows = next(b for b in community["blocks"] if b["kind"] == "table")["rows"]
    check("every figure shows what it can carry",
          all(r["can it carry weight"] for r in rows))

    # §8: where the two releases cannot be separated, the answer says the survey
    # cannot tell rather than reporting a change of zero.
    quiet = " ".join(b.get("text", "") for b in
                     S.answer_preset("community", pid("CITY OF SUMPTER"))["blocks"])
    check("a place the survey cannot separate is told so, not called unchanged",
          "unable to separate" in quiet or "margin of error allows" in quiet,
          quiet[-200:])

    report = R.compose_report(store, kind="entity", pid6=baker)
    ids = [x["id"] for x in report["data"]["sections"]]
    check("the entity report carries a community section", "community" in ids, str(ids))
    check("and places it after the fiscal ones, not among them",
          ids.index("community") > ids.index("spending"), str(ids))

    print("\nthe front door starts from a place, not from a name")
    portland_city = pid("CITY OF PORTLAND")
    serving = S.answer_preset("serving", portland_city)
    text = " ".join(b.get("text", "") for b in serving["blocks"])
    check("the answer counts the stack", "governments established to serve" in text,
          text[:160])
    check("it conforms to §15", serving["violations"] == [], str(serving["violations"]))
    rows = next(b for b in serving["blocks"] if b["kind"] == "table")["rows"]
    check("the city is listed among the governments serving it",
          rows[0]["government"] == "Portland", str(rows[:1]))
    check("and every row carries the evidence behind it",
          all(r["established by"] for r in rows))
    check("the reader is shown where the place is",
          any(b["kind"] == "map" for b in serving["blocks"]))
    limits = next(b for b in serving["blocks"] if b["kind"] == "limits")
    check("the stack is never presented as the whole stack",
          "serving_stack_incomplete" in {r["code"] for r in limits["rules"]},
          str([r["code"] for r in limits["rules"]]))

    # §9 again, at the interface: a town with no established stack must not be
    # offered the question. The manifest column is computed rather than stored,
    # which is exactly the kind of key that goes missing from one caller.
    with_stack = S.availability(portland_city)
    without = S.availability(new_bridge)
    check("a town with a stack is offered the question", with_stack["has_serving"])
    check("a district with none is not", not without["has_serving"])
    offered = {p["id"] for p in S.preset_shell()}
    check("the question list the page is sent covers every preset",
          offered == {p[0] for p in S.PRESETS}, str(offered ^ {p[0] for p in S.PRESETS}))

    print("\nthe survey maps a population and refuses the grain it cannot carry")
    conditions = S.answer_preset("map_community")
    text = " ".join(b.get("text", "") for b in conditions["blocks"])
    check("it conforms to §15", conditions["violations"] == [],
          str(conditions["violations"]))
    check("two county maps are drawn",
          sum(1 for b in conditions["blocks"] if b["kind"] == "map") == 2)
    check("the map is not offered as a government's record",
          "not governments" in text, text[:200])
    check("and the finer grain is refused in the survey's own terms",
          "refused" in text.lower() and "margin of error" in text, text[-260:])
    limits = next(b for b in conditions["blocks"] if b["kind"] == "limits")
    check("a condition is never read as an outcome",
          "conditions_are_not_outcomes" in {r["code"] for r in limits["rules"]},
          str([r["code"] for r in limits["rules"]]))

    print("\nfive years of data reaches the comparison and the report")
    keizer = pid("CITY OF KEIZER")
    portland = pid("CITY OF PORTLAND")
    change = S.T.render_comparison(store, [portland, keizer], "five_year_change")
    text = S._compare_headline(change)
    check("a five-year change compares governments the annual panel cannot",
          "Keizer" in text and "Portland" in text, text[:120])
    check("and it refuses to be called a trend",
          "not a trend" in text, text[-160:])
    check("the figures are declared nominal", "nominal" in text)

    panel = S.T.render_comparison(store, [portland, pid("CITY OF SALEM")],
                                  "five_year_panel")
    check("an annual panel says how many are measured every year",
          "measured every year" in S._compare_headline(panel),
          S._compare_headline(panel)[:160])

    # §8: a line between two observations five years apart is drawn, not
    # measured, and must not look like a line measured seven times.
    mixed = S.T.render_comparison(store, [portland, keizer], "five_year_panel")
    codes_here = {c["code"] for c in mixed["caveats"]}
    check("a sparse series never sits unmarked beside an annual one",
          "series_not_annual" in codes_here or "entities_excluded" in codes_here,
          str(sorted(codes_here)))

    report = R.compose_report(store, kind="comparison",
                              pid6_list=[portland, keizer, pid("CITY OF MEDFORD")],
                              basis="absolute")
    ids = [s["id"] for s in report["data"]["sections"]]
    check("the comparison report carries a five-year section",
          "five_year_change" in ids, str(ids))
    check("and a year-by-year section where the years exist",
          "five_year_panel" in ids, str(ids))

    entity_report = R.compose_report(store, kind="entity", pid6=keizer)
    section = next((s for s in entity_report["data"]["sections"]
                    if s["id"] == "five_year_change"), None)
    check("an entity report places its own five years against its type",
          section is not None)
    readout = S._section_readout(section, S.T._entity(store, keizer))
    check("and the readout names both ends in the order it says them",
          "2017" in readout and "2022" in readout and "nominal" in readout,
          readout[:200])

    print("\na search row says what its number counts")
    rows = [S._with_magnitude(store.row(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county "
        "FROM entities WHERE legal_name=?", name))
        for name in ("CITY OF PORTLAND", "COUNTY OF BAKER")]
    check("cities and counties count residents",
          all("residents" in r["magnitude"] for r in rows), str([r["magnitude"] for r in rows]))
    school_row = S._with_magnitude(store.row(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county "
        "FROM entities WHERE pid6=?", college))
    check("a school district counts students", "students" in school_row["magnitude"],
          school_row["magnitude"])
    district = S._with_magnitude(store.row(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county FROM entities "
        "WHERE gov_type_name='Special District' AND pid6 IN "
        "(SELECT pid6 FROM operating_vs_capital WHERE total_expenditure > 0) LIMIT 1"))
    check("and a special district shows its budget instead",
          "budget" in district["magnitude"], district["magnitude"])
    # §9: a district that filed nothing has filed nothing. A blank cell in that
    # column would read as a budget of zero.
    silent = S._with_magnitude(store.row(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county FROM entities "
        "WHERE gov_type_name='Special District' AND pid6 NOT IN "
        "(SELECT pid6 FROM operating_vs_capital WHERE total_expenditure > 0) LIMIT 1"))
    check("a district that filed nothing says so rather than going blank",
          silent["magnitude"] == "no budget filed", silent["magnitude"])
    # The figure and the noun travel apart so the interface can set them apart.
    check("every row hands over its figure and its unit separately",
          all(r["magnitude"] == f"{r['magnitude_figure']} {r['magnitude_unit']}".strip()
              for r in rows + [school_row, district, silent]))

    print("\ntwo denominators cannot share an axis")
    import explore
    mixed = explore.per_capita_by_service_area(
        store, [pid("CITY OF PORTLAND"), college], S.DEFAULT_AREA)
    check("a city and a school district are refused, not ranked together",
          "mixed_denominators" in {c["code"] for c in mixed["caveats"]})
    check("and the refusal offers what can be drawn instead",
          len(mixed["data"]["alternatives"]) == 2)
    drawn = S.T.render_comparison(store, [pid("CITY OF PORTLAND"), college],
                                  "per_capita_by_service_area",
                                  service_area=S.DEFAULT_AREA)
    check("the chart draws the reason rather than an empty frame",
          "per student" in drawn["data"]["svg"])

    print("\nthe drill-down reaches the line item")
    levels = [S.answer_preset(preset, baker) for preset in
              ("spending", "inside", "function_split")]
    check("all three levels answer", all(r["blocks"] for r in levels))
    check("the second level names functions inside one area",
          any("largest function is" in b.get("text", "") for b in levels[1]["blocks"]))
    check("the third splits one function into operating and capital",
          any("operating and" in b.get("text", "") and "capital" in b.get("text", "")
              for b in levels[2]["blocks"]))
    # §8: one year of capital is a position in a bond cycle. The sentence has to
    # say so, because a share this large reads as a preference otherwise.
    check("and says a capital share is a cycle rather than a trend",
          any("rather than a trend" in b.get("text", "") for b in levels[2]["blocks"]))
    # Every level is operating plus capital now, so the figures nest. A share
    # taken off one basis beside a total on another was the reason the
    # vendor-addressable derivation came out.
    figures = [b for b in levels[2]["blocks"] if b["kind"] == "figure"]
    check("the figure states the basis it is on",
          bool(figures) and figures[0].get("basis") == "operating plus capital",
          str(figures[:1]))

    print("\nrefusals survive into the interface")
    result = S.answer_preset("scale", new_bridge)
    text = " ".join(b.get("text", "") for b in result["blocks"])
    check("an entity outside every peer distribution is not placed in one",
          "no peer distribution" in text.lower(), text[:110])
    check("and the refusal is drawn rather than dropped",
          any(b["kind"] == "chart" and "no peer distribution" in b["svg"].lower()
              for b in result["blocks"]))

    print("\nthe brief opens on the gap, not on a figure (§12, §5)")
    estacada = pid("CITY OF ESTACADA")
    brief = S.answer_brief(estacada, "wildfire")
    kinds = [b["kind"] for b in brief["blocks"]]
    check("a brief is composed as an issue answer", brief["question_type"] == "issue_or_topic")
    check("and its blocks come back in §15.2 order", brief["violations"] == [],
          str(brief["violations"]))
    answer = next(b["text"] for b in brief["blocks"] if b["kind"] == "answer")
    # §5 precedence: scope limits first. The sentence has to say the data does
    # not record the subject before it names a single government, because a
    # reader who sees the bodies first has already started attributing.
    check("the first sentence says the data does not record the subject",
          answer.index("Census functional categories") < answer.index("governments"),
          answer[:120])
    check("and no dollar figure appears in it at all",
          "$" not in answer, answer)
    check("the stack is named in the answer with a count",
          "established to serve" in answer)

    # The margin column exists so nobody quotes a figure the survey cannot
    # support. Estacada is small enough that most of its estimates are soft.
    conditions = [b for b in brief["blocks"] if b["kind"] == "table"
                  and b["rows"] and "safe to quote" in b["rows"][0]]
    check("conditions carry a verdict on whether they can be quoted",
          bool(conditions) and any(r["safe to quote"].startswith("no")
                                   for r in conditions[0]["rows"]),
          str(conditions[:1])[:160])
    asks = [b for b in brief["blocks"] if b["kind"] == "table"
            and b["rows"] and "what to ask for next" in b["rows"][0]]
    check("and the brief ends with what to ask, not with a number", bool(asks))

    # §15.1: never offer a question whose data is absent. The subject list is
    # sent from one definition, the way the preset list is.
    import brief as BR
    check("the subject list has exactly one definition",
          set(BR.topics()) == set(rules.TOPIC_CONCORDANCE))

    print("\nthe comparison tray is grounded in the tool layer")
    portland = pid("CITY OF PORTLAND")
    group, basis = S.peer_set(portland)
    check("a peer set is drawn from one government type", len(group) >= 2)
    # "closest in residents", not "closest in population": the peer set for a
    # school district is closest in enrollment, and the sentence has to say which.
    check("and its basis is stated for the answer to repeat",
          bool(basis) and "residents" in basis, str(basis))
    school_group, school_basis = S.peer_set(college)
    check("a school district's peers are nearest in students, and say so",
          bool(school_basis) and "students" in school_basis, str(school_basis))
    comparison = S.T.render_comparison(store, group, "per_capita_by_service_area",
                                       service_area=S.DEFAULT_AREA)
    headline = S._compare_headline(comparison)
    check("a comparison headline names both ends of the range",
          "highest at" in headline and "lowest at" in headline, headline[:110])
    check("and the per-resident basis travels as a rule",
          "per_resident_basis" in {c["code"] for c in comparison["caveats"]})

    over_time = S.T.render_comparison(store, group, "per_capita_over_time", indexed=True)
    codes = {c["code"] for c in over_time["caveats"]}
    check("an indexed series says the baseline is peers, not inflation",
          "baseline_is_peers_not_inflation" in codes, str(sorted(codes)))
    check("and says the denominator is held constant",
          "population_held_constant" in codes)

    print("\na service area cannot be tracked over time, and says so")
    import explore
    refusal = explore.service_area_over_time_refusal(S.DEFAULT_AREA)
    check("the refusal is the result, not an empty chart",
          refusal["data"]["refused"] is True)
    check("and it offers what can be drawn instead",
          len(refusal["data"]["alternatives"]) == 2)

    print("\nthe ecosystem answer names who is filed elsewhere")
    result = S.answer_preset("ecosystem", None, county="Multnomah")
    text = " ".join(b.get("text", "") for b in result["blocks"])
    check("it reports the count", "governments are filed under" in text)
    check("it names the cross-boundary set", "filed under a neighbouring county" in text)
    check("and includes a map block, not a chart",
          any(b["kind"] == "map" for b in result["blocks"]))
    check("the map declares its coverage so §15 can judge it",
          any(b["kind"] == "map" and b.get("coverage") is not None
              for b in result["blocks"]))

    print("\nreports render into the thread")
    result = S.answer_preset("report", baker)
    report = next((b for b in result["blocks"] if b["kind"] == "report"), None)
    check("a report block is returned", report is not None)
    check("with several sections", report and report["sections"] >= 5)
    check("and a stated word budget", report and report["budget"] > 0)

    print("\npresets are gated by the availability manifest")
    row = store.row("SELECT * FROM data_availability WHERE pid6=?", new_bridge)
    gated = [(label, bool(dict(row).get(needs)))
             for _, label, _, needs, _ in S.PRESETS if needs]
    check("at least one preset is withheld for a sparse entity",
          any(not available for _, available in gated), str(gated))
    check("and spending is the one withheld here",
          not dict(row)["has_spending_breakdown"])

    print("\nan entity with nothing to show says so")
    result = S.answer_preset("spending", new_bridge)
    text = " ".join(b.get("text", "") for b in result["blocks"])
    check("absence is not reported as zero",
          "absence of data" in text or "no spending breakdown" in text.lower(), text[:90])

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
