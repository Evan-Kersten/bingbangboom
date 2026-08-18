#!/usr/bin/env python3
"""Tests for report composition and normalized comparison.

The ordering assertions matter most. §5 fixes the precedence: scope limits
first, ecosystem membership second, figures last. A report that opens with
figures has told the reader the opposite of what the prompt requires, and the
order is the one part of that a renderer can guarantee.

    python3 agent/test_reports.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reports as R
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


def main():
    store = T.Store()

    def pid(legal_name):
        row = store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)
        assert row, f"fixture missing: {legal_name}"
        return row["pid6"]

    baker = pid("COUNTY OF BAKER")
    portland = pid("CITY OF PORTLAND")
    salem = pid("CITY OF SALEM")
    new_bridge = pid("NEW BRIDGE WATER SUPPLY DISTRICT")
    schools = pid("PORTLAND SCH DIST 1J")

    print("\nsection order follows §5 precedence")
    for kind, kwargs in (("entity", {"pid6": baker}),
                         ("place", {"county_name": "Multnomah"}),
                         ("comparison", {"pid6_list": [portland, salem]})):
        plan = R.compose_report(store, kind=kind, **kwargs)
        ids = [s["id"] for s in plan["data"]["sections"]]
        check(f"{kind} report opens with scope limits", ids[0] == "scope", str(ids))
        check(f"{kind} report puts no chart before the scope section",
              plan["data"]["sections"][0].get("chart") is None)

    plan = R.compose_report(store, kind="place", county_name="Multnomah")
    ids = [s["id"] for s in plan["data"]["sections"]]
    check("a place report names its members before drawing anything",
          ids.index("membership") < ids.index("geography"), str(ids))

    print("\nevery section carries a budget and a purpose")
    plan = R.compose_report(store, kind="entity", pid6=baker)
    for section in plan["data"]["sections"]:
        check(f"{section['id']} has a purpose and a word budget",
              bool(section["purpose"]) and section["word_budget"] > 0)
    check("the whole report has a stated budget",
          plan["data"]["total_word_budget"] ==
          sum(s["word_budget"] for s in plan["data"]["sections"]))

    print("\nrules travel with the section they bind")
    sections = {s["id"]: s for s in plan["data"]["sections"]}
    check("the scope section forbids outcome claims",
          "outcome_or_performance" in
          {c["code"] for c in sections["scope"]["not_computable"]})
    check("the spending section forbids service-area year over year",
          "service_area_yoy" in
          {c["code"] for c in sections["spending"]["not_computable"]})
    check("the governance section discloses that holders are absent",
          "offices_have_no_holders" in
          {c["code"] for c in sections["governance"]["caveats"]})

    print("\nsections with nothing to say are dropped, not padded")
    sparse = R.compose_report(store, kind="entity", pid6=new_bridge)
    sparse_ids = [s["id"] for s in sparse["data"]["sections"]]
    check("an entity with no workforce breakdown gets no workforce section",
          "workforce" not in sparse_ids, str(sparse_ids))
    check("but still gets a scope section naming what is absent",
          bool(sparse["data"]["sections"][0]["data"]["absent"]))
    check("a sparse report is shorter than a full one",
          sparse["data"]["total_word_budget"] < plan["data"]["total_word_budget"])

    print("\nnormalized comparison (§10)")
    # Share of own spending needs something to be a share of, or every entity
    # is 100% of itself and the chart shows five identical bars.
    check("share of own spending without a service area is refused",
          "basis_needs_a_service_area" in
          codes(R.compare_normalized(store, [portland, salem], basis="share_of_spending")))

    result = R.compare_normalized(store, [portland, salem], "Public Safety",
                                  basis="share_of_spending")
    check("the basis is stated in the caveats", "comparison_basis" in codes(result))
    check("and the shares are not all identical",
          len({round(e["value"], 3) for e in result["data"]["entities"]}) > 1,
          str([e["value"] for e in result["data"]["entities"]]))
    check("rows are ordered by value",
          [r["value"] for r in result["data"]["entities"]] ==
          sorted((r["value"] for r in result["data"]["entities"]), reverse=True))
    check("higher is never implied to be better", "inputs_not_outcomes" in codes(result))

    result = R.compare_normalized(store, [portland, salem], basis="absolute")
    check("an absolute ranking says it ranks by size",
          "absolute_ranks_by_size" in codes(result))

    result = R.compare_normalized(store, [portland, new_bridge], basis="per_resident")
    excluded = {e["name"] for e in result["data"]["excluded"]}
    check("an entity with no population is excluded from a per-resident basis",
          any("New Bridge" in name for name in excluded), str(excluded))
    check("and the exclusion is declared", "entities_excluded" in codes(result))
    check("the basis is never silently downgraded for the rest",
          result["data"]["basis"] == "per_resident")
    check("and the basis names the unit rather than assuming residents",
          result["data"]["basis_label"] == "spending per resident",
          result["data"]["basis_label"])

    # A school district's population field is enrollment, so this set would rank
    # dollars per student against dollars per resident in one column.
    result = R.compare_normalized(store, [portland, schools], basis="per_resident")
    check("a city and a school district cannot share a per-unit column",
          "mixed_denominators" in codes(result))
    guidance = " ".join(c["guidance"] for c in result["caveats"])
    check("and the refusal names both units",
          "per resident" in guidance and "per student" in guidance, guidance)

    result = R.compare_normalized(store, [schools, portland], basis="absolute")
    check("but total dollars still compares them", bool(result["data"]["entities"]))

    result = R.compare_normalized(store, [schools], basis="per_resident")
    check("a school district on its own is measured per student",
          result["data"]["basis_label"] == "spending per student",
          result["data"]["basis_label"])
    check("and every row carries that unit",
          all(r["unit"] == "dollars per student" for r in result["data"]["entities"]),
          str([r["unit"] for r in result["data"]["entities"]]))

    result = R.compare_normalized(store, [portland, baker], basis="absolute")
    check("mixing a city and a county is flagged", "mixed_entity_types" in codes(result))
    check("and the Oregon delegation rule fires", "oregon_county_delegated" in codes(result))

    result = R.compare_normalized(store, [portland, "000000"], basis="absolute")
    check("entities absent from the data are named",
          "entities_missing" in codes(result) and result["data"]["missing"] == ["000000"])

    result = R.compare_normalized(store, [portland], basis="nonsense")
    check("an unknown basis is refused", "unknown_basis" in codes(result))

    print("\nthe cross-boundary section appears only where measured")
    multnomah = R.compose_report(store, kind="place", county_name="Multnomah")
    check("Multnomah has a cross-boundary section",
          "cross_boundary" in [s["id"] for s in multnomah["data"]["sections"]])
    baker_place = R.compose_report(store, kind="place", county_name="Baker")
    check("a county with no measured extent has none",
          "cross_boundary" not in [s["id"] for s in baker_place["data"]["sections"]])

    print("\nrendering")
    html = R.render_report(plan)
    check("an unwritten report renders its purposes, not blank sections",
          html.count("To write here") == len(plan["data"]["sections"]))
    check("charts are embedded", "<svg" in html)
    check("what a section cannot tell you is rendered",
          "What this cannot tell you" in html)

    written = R.render_report(plan, prose={"scope": "The data covers inputs, not results."})
    check("supplied prose replaces the purpose for that section",
          "The data covers inputs, not results." in written and
          written.count("To write here") == len(plan["data"]["sections"]) - 1)

    check("an unknown report kind is refused",
          "unknown_report_kind" in codes(R.compose_report(store, kind="nonsense")))
    check("an unknown entity is refused",
          "entity_not_found" in codes(R.compose_report(store, kind="entity", pid6="000000")))

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
