#!/usr/bin/env python3
"""Tests for the render layer.

Two things are being checked. That the SVG is structurally sound, which a
renderer can get wrong silently. And that a chart carries the same refusals as
the prose, which is the part that matters: a bar drawn against a degenerate peer
median is as wrong as the sentence would be, and reads as measurement.

    python3 agent/test_viz.py
"""

import inspect
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import format as fmt
import maps
import tools as T
import viz

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
    sumpter = pid("CITY OF SUMPTER")
    portland = pid("CITY OF PORTLAND")
    new_bridge = pid("NEW BRIDGE WATER SUPPLY DISTRICT")

    print("\nthe model cannot supply words or colour")
    signature = inspect.signature(T.render_chart).parameters
    for forbidden in ("title", "label", "subtitle", "colour", "color", "caption"):
        check(f"render_chart takes no '{forbidden}' parameter", forbidden not in signature)
    check("render_chart takes only a store, an entity and a form",
          set(signature) == {"store", "pid6", "form"}, str(set(signature)))
    map_signature = set(inspect.signature(T.render_map).parameters)
    check("render_map takes no title either",
          map_signature == {"store", "layer", "metric", "county", "service_area"},
          str(map_signature))

    print("\nSVG is well formed")
    for form in T.CHART_FORMS:
        svg = T.render_chart(store, baker, form)["data"]["svg"]
        try:
            ET.fromstring(svg)
            ok = True
        except ET.ParseError as error:
            ok, form = False, f"{form}: {error}"
        check(f"{form} parses as XML", ok)

    svg = T.render_chart(store, baker, "spending_composition")["data"]["svg"]
    check("charts carry an aria-label", 'role="img"' in svg and "aria-label" in svg)
    check("marks carry a title for hover", svg.count("<title>") >= 5)
    check("colours are tokens with a light fallback",
          "var(--pf-s1, #1B7A4C)" in svg, "no CSS variable in fill")
    check("no raw hex outside a var() fallback",
          not re.search(r'fill="#', svg), "hex used directly")

    print("\ntheme tokens")
    block = viz.css(".pf-viz")
    check("light values sit on the bare selector", "--pf-surface: #FBFDFC;" in block)
    check("dark values are declared under prefers-color-scheme",
          "prefers-color-scheme: dark" in block and "--pf-surface: #0E1512;" in block)
    check("and again under an explicit data-theme stamp",
          ':root[data-theme="dark"]' in block)
    check("the light stamp beats an OS dark preference",
          ':root:where(:not([data-theme="light"]))' in block)
    check("every token has both modes",
          all(len(v) == 2 and all(v) for v in viz.TOKENS.values()))

    print("\ncharts carry the tools' refusals")
    result = T.render_chart(store, new_bridge, "peer_range")
    check("an entity with no peer distribution is not placed in one",
          result["data"]["refused"] is True)
    check("and the refusal says why on the chart itself",
          "no peer distribution" in result["data"]["svg"].lower())

    result = T.render_chart(store, portland, "peer_range")
    check("a sound distribution is drawn", result["data"]["refused"] is False)
    check("and states the peer group and count",
          "entities" in result["data"]["svg"] and "Oregon Municipal" in result["data"]["svg"])
    check("and shows the middle half rather than the median alone",
          "middle half" in str(result["data"]["table"]))

    # A function median taken over two entities is not a median. The drill-down
    # suppresses the tick rather than drawing a comparison against a pair.
    inside = T.render_chart(store, baker, "service_area_functions")
    check("the drill-down into a service area draws",
          inside["data"]["refused"] is False)
    import explore
    functions = explore.drill(store, baker, "Public Safety")["data"]["functions"]
    thin = [f for f in functions if f["peer_n"] < 3]
    check("a function almost no peer reports gets no median tick",
          all(f["peer_median"] is None for f in thin), f"{len(thin)} thin functions")
    check("and the ones that are drawn are per function, not per service area",
          len({f["peer_median"] for f in functions if f["peer_median"]}) ==
          len([f for f in functions if f["peer_median"]]))

    high_other = store.row(
        "SELECT pid6 FROM entity_flags WHERE other_share_high=1 AND pid6 IN "
        "(SELECT pid6 FROM spending_by_service_area)")["pid6"]
    result = T.render_chart(store, high_other, "spending_composition")
    check("a partial breakdown is annotated on the chart",
          "partial" in result["data"]["svg"].lower())

    # 87 entities have fewer than two years of trend data. One year is a point,
    # and a line drawn through a single point invents a direction.
    sparse = store.row(
        "SELECT e.pid6 FROM entities e LEFT JOIN financial_trends t ON t.pid6=e.pid6 "
        "GROUP BY e.pid6 HAVING COUNT(t.year) < 2")["pid6"]
    result = T.render_chart(store, sparse, "finances_over_time")
    check("fewer than two years is refused, not drawn as a point",
          result["data"]["refused"] is True)
    check("and the refusal says why",
          "point, not a trend" in result["data"]["svg"])

    print("\nseveral governments on one axis")
    cities = [portland, store.row(
        "SELECT pid6 FROM entities WHERE legal_name='CITY OF BEND'")["pid6"]]

    result = T.render_comparison(store, cities, "per_capita_by_service_area",
                                 service_area="Public Safety")
    check("per-resident bars draw for a service area",
          result["data"]["refused"] is False)
    check("and the per-resident basis is stated as a caveat",
          "per_resident_basis" in codes(result))
    check("and the population caveat travels, since the denominator is one estimate",
          "population_is_one_estimate" in codes(result))

    result = T.render_comparison(store, cities, "per_capita_over_time", indexed=True)
    check("an indexed series draws", result["data"]["refused"] is False)
    check("and says the index hides level",
          "indexed_hides_level" in codes(result))
    check("and says the baseline is peers rather than inflation",
          "baseline_is_peers_not_inflation" in codes(result))
    check("and every line starts at 100",
          all(row[label] == "100" for row in result["data"]["table"][:1]
              for label in row if label != "year"))

    result = T.render_comparison(store, cities, "service_area_across_entities",
                                 service_area="Public Safety")
    check("a service area across entities is a snapshot, never a trend",
          "one_year_per_entity" in codes(result))
    check("and the chart says so on its face",
          "not a trend" in result["data"]["svg"].lower())

    result = T.render_comparison(store, cities, "per_capita_by_service_area")
    check("a service-area comparison without an area is refused",
          "service_area_required" in codes(result))

    refusal = explore.service_area_over_time_refusal("Public Safety")
    check("a service area over time is refused outright",
          refusal["data"]["refused"] is True)
    check("and the refusal offers what can be drawn instead",
          len(refusal["data"]["alternatives"]) == 2)

    print("\nevery chart has a table twin")
    for form in T.CHART_FORMS:
        result = T.render_chart(store, baker, form)
        data = result["data"]
        check(f"{form} returns a table unless it refused",
              (data["table"] is not None) or data["refused"])

    print("\nfigures match the prose formatter")
    svg = T.render_chart(store, baker, "spending_composition")["data"]["svg"]
    total = store.row("SELECT total FROM spending_by_service_area WHERE pid6=? "
                      "ORDER BY total DESC LIMIT 1", baker)["total"]
    check("the largest bar is labelled with format.money",
          fmt.money(total) in svg, fmt.money(total))
    check("no unrounded figure appears in a chart",
          not re.search(r">\$\d{1,3}(,\d{3}){2,}<", svg))

    print("\nmaps")
    result = T.render_map(store, "county", "spending_per_resident")
    data = result["data"]
    check("all 36 counties are drawn with a value", data["covered"] == 36)
    check("the map states it covers one government type",
          "map_is_partial" in codes(result))
    check("no data has a fill distinct from every bin",
          viz.TOKENS["nodata"][0] not in [viz.TOKENS[b][0] for b in maps.BINS])
    check("a fully covered map does not advertise a no-data class",
          "no data" not in data["svg"])
    check("and the legend carries the values, not just 'lower' and 'higher'",
          "lower" not in data["svg"])

    gapped = T.render_map(store, "school_district", "total_spending")
    check("a map with gaps names the no-data class and counts them",
          "no data (" in gapped["data"]["svg"])

    area_map = T.render_map(store, "county", "service_area_per_resident",
                            service_area="Public Safety")
    check("a service area can be mapped", area_map["data"]["covered"] > 0)
    check("and a blank boundary is declared an absence, not a zero",
          "absence_is_not_zero" in codes(area_map))
    check("a service-area map without an area is refused",
          "service_area_required" in codes(
              T.render_map(store, "county", "service_area_per_resident")))
    try:
        ET.fromstring(data["svg"])
        ok = True
    except ET.ParseError as error:
        ok = str(error)
    check("the map parses as XML", ok is True, str(ok))

    result = T.render_map(store, "school_district", "total_spending")
    check("school district geometry is declared as name matched",
          "geometry_by_name" in codes(result))
    check("and is partial", result["data"]["covered"] < result["data"]["polygons"])

    result = T.render_map(store, "place", "spending_per_resident")
    check("a statewide place map warns that it is unreadable",
          "place_map_unreadable_statewide" in codes(result))

    result = T.render_map(store, "place", "spending_per_resident", county="Multnomah")
    check("scoping a place map to a county drops the readability warning",
          "place_map_unreadable_statewide" not in codes(result))
    check("and draws far fewer polygons",
          0 < result["data"]["polygons"] < 60, str(result["data"]["polygons"]))

    check("special districts cannot be mapped at all",
          "unknown_layer" in codes(T.render_map(store, "special_district")))
    check("and the refusal says to list them instead",
          "list them" in " ".join(c["guidance"] for c in
                                  T.render_map(store, "special_district")["caveats"]))
    check("an unknown county is refused",
          "county_not_found" in codes(T.render_map(store, "place", county="Atlantis")))

    print("\nbins")
    check("quantile breaks split a skewed distribution",
          len(maps.quantile_bins([1, 1, 1, 2, 3, 4, 90, 200])) == 4)
    check("a near-constant column collapses rather than faking spread",
          len(maps.quantile_bins([5, 5, 5])) <= 1)
    check("values below the first break land in bin 0", maps.bin_index(0, [10, 20]) == 0)
    check("values above the last break land in the top bin",
          maps.bin_index(99, [10, 20]) == 2)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
