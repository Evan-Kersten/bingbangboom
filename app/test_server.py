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
    # group, so a reader exploring one government never saw one.
    for name, expect_map in (("COUNTY OF BAKER", True), ("CITY OF PORTLAND", True),
                             ("NEW BRIDGE WATER SUPPLY DISTRICT", False)):
        row = store.row("SELECT pid6 FROM entities WHERE legal_name=?", name)
        result = S.answer_preset("scale", row["pid6"])
        has_map = any(b["kind"] == "map" for b in result["blocks"])
        check(f"{name.title()}: map {'drawn' if expect_map else 'withheld'}",
              has_map is expect_map)
        check(f"{name.title()}: still conforms to §15", result["violations"] == [],
              str(result["violations"]))
    answer = " ".join(b.get("text", "") for b in
                      S.answer_preset("scale", store.row(
                          "SELECT pid6 FROM entities WHERE legal_name="
                          "'NEW BRIDGE WATER SUPPLY DISTRICT'")["pid6"])["blocks"])
    check("and an entity with no boundary is told why, not left blank",
          "no boundary in this data" in answer, answer[:100])

    print("\nthe drill-down reaches the line item")
    levels = [S.answer_preset(preset, baker) for preset in
              ("spending", "inside", "purchasable")]
    check("all three levels answer", all(r["blocks"] for r in levels))
    check("the second level names functions inside one area",
          any("largest function is" in b.get("text", "") for b in levels[1]["blocks"]))
    check("the third names the vendor-addressable remainder",
          any("addressable" in b.get("text", "") for b in levels[2]["blocks"]))
    check("and calls it a derivation rather than a reported line",
          any("derivation" in b.get("text", "") for b in levels[2]["blocks"]))

    print("\nrefusals survive into the interface")
    result = S.answer_preset("scale", new_bridge)
    text = " ".join(b.get("text", "") for b in result["blocks"])
    check("an entity outside every peer distribution is not placed in one",
          "no peer distribution" in text.lower(), text[:110])
    check("and the refusal is drawn rather than dropped",
          any(b["kind"] == "chart" and "no peer distribution" in b["svg"].lower()
              for b in result["blocks"]))

    print("\nthe comparison tray is grounded in the tool layer")
    portland = pid("CITY OF PORTLAND")
    group, basis = S.peer_set(portland)
    check("a peer set is drawn from one government type", len(group) >= 2)
    check("and its basis is stated for the answer to repeat",
          bool(basis) and "population" in basis, str(basis))
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
