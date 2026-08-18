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
    # This set is allowed to grow, but only with parameters that select data.
    # service_area and function are the two depths of the drill; a title or a
    # caption would be the model writing on the picture, which is the thing the
    # signature exists to prevent.
    map_signature = set(inspect.signature(T.render_map).parameters)
    check("render_map takes no title either",
          map_signature == {"store", "layer", "metric", "county", "service_area",
                            "function", "highlight_pid6"},
          str(map_signature))
    for name in ("render_map", "render_function_map"):
        parameters = set(inspect.signature(getattr(T, name)).parameters)
        check(f"and every {name} parameter names data, never wording",
              not (parameters & {"title", "subtitle", "caption", "note", "label",
                                 "colour", "color"}),
              str(parameters))

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

    print("\nconverging end labels terminate")
    # Four cities whose lines finish within a few points of each other put four
    # end labels inside one line height. The nudge that separates them used to
    # re-test the gap it had just created, and (a + 15) - a is not exactly 15 in
    # binary floating point, so it could set the same value forever and hang the
    # request. A time limit is the only assertion that catches a hang.
    import signal, time
    years = list(range(2017, 2024))
    converging = [
        {"label": name, "points": list(zip(years, values))}
        for name, values in [
            ("Portland", [100.0, 107.72, 115.64, 122.85, 128.03, 135.97, 142.78]),
            ("Salem", [100.0, 102.51, 107.13, 109.37, 111.58, 114.52, 128.48]),
            ("Eugene", [100.0, 102.46, 106.43, 113.16, 117.32, 122.34, 131.68]),
            ("Gresham", [100.0, 108.44, 112.16, 99.51, 106.44, 111.24, 120.87])]]
    reference = {"label": "Municipal median",
                 "points": list(zip(years, [100.0, 98.93, 101.7, 114.54, 119.2,
                                            118.71, 124.29]))}
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(10)
    try:
        drawn = viz.multi_series("t", "s", converging, formatter=lambda v: f"{v:,.0f}",
                                 note="n", reference=reference, baseline=100)
        hung = False
    except TimeoutError:
        drawn, hung = None, True
    finally:
        signal.alarm(0)
    check("four converging end labels do not hang the renderer", not hung)
    check("and all four are still drawn",
          bool(drawn) and all(name in drawn for name in
                              ("Portland", "Salem", "Eugene", "Gresham")))

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

    # §4: a survey estimate and a spending figure must not be readable as one
    # picture. The caveat says so; this is the drawing saying so too.
    check("the survey ramp shares no colour with the spending ramp",
          not ({viz.TOKENS[b][0] for b in maps.SURVEY_BINS}
               & {viz.TOKENS[b][0] for b in maps.BINS}))
    check("and has the same number of steps, so it is read as ordinal",
          len(maps.SURVEY_BINS) == len(maps.BINS))
    survey = T.TOOLS["render_community_map"](store, code="DP03_0128P",
                                             geo_level="county")
    spending = data["svg"]
    check("a community map is drawn on the survey ramp",
          viz.TOKENS["survey-5"][0] in survey["data"]["svg"] or
          "survey-5" in survey["data"]["svg"])
    check("and carries no colour from the spending ramp",
          not any(viz.TOKENS[b][0] in survey["data"]["svg"] for b in maps.BINS),
          "a spending bin colour appears in a survey map")
    check("while the spending map keeps its own",
          any(viz.TOKENS[b][0] in spending for b in maps.BINS))
    check("a fully covered map does not advertise a no-data class",
          "no data" not in data["svg"])
    check("and the legend carries the values, not just 'lower' and 'higher'",
          "lower" not in data["svg"])

    gapped = T.render_map(store, "school_district", "total_spending")
    check("a map with gaps names the no-data class and counts them",
          "no data (" in gapped["data"]["svg"])

    outlined = T.render_map(store, "county", "spending_per_resident",
                            highlight_pid6=store.row(
                                "SELECT pid6 FROM entities WHERE legal_name="
                                "'COUNTY OF MULTNOMAH'")["pid6"])
    check("a government can be outlined on its own layer",
          outlined["data"]["highlighted"] is True)
    check("and the outline is a stroke, so the entity keeps its place on the ramp",
          'stroke-width="1.8"' in outlined["data"]["svg"]
          and outlined["data"]["svg"].count('stroke-width="1.8"') == 1)
    check("and it is cased, so it reads against the darkest bin and the lightest",
          outlined["data"]["svg"].count('stroke-width="4"') == 1)
    absent = T.render_map(store, "county", "spending_per_resident",
                          highlight_pid6=store.row(
                              "SELECT pid6 FROM entities WHERE legal_name="
                              "'CITY OF PORTLAND'")["pid6"])
    check("an entity absent from the layer is not outlined, and the map says so",
          absent["data"]["highlighted"] is False
          and "highlight_unavailable" in codes(absent))

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

    # The Multnomah pilot recovered 19 special district boundaries, so the layer
    # exists — but 19 districts spanning fire, water, transit and community
    # colleges are not a peer group, and a choropleth over them would invite a
    # comparison none of them are in. Locating one is the honest form.
    recovered = T.render_map(store, "special_district", "spending_per_resident")
    check("the recovered special district layer is drawable at all",
          "unknown_layer" not in codes(recovered), str(sorted(codes(recovered))))
    check("but it falls under the coverage floor rather than posing as a peer map",
          (recovered["data"].get("covered") or 0) < (recovered["data"].get("polygons") or 1),
          f"{recovered['data'].get('covered')}/{recovered['data'].get('polygons')}")
    located = T.render_locator_map(
        store, store.row("SELECT pid6 FROM entities WHERE legal_name="
                         "'TUALATIN VALLEY FIRE AND RESCUE DISTRICT'")["pid6"])
    check("and one of them can be placed in Oregon on its own boundary",
          located["data"]["basis"] == "own_boundary" and located["data"]["svg"])
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

    print("\na framed map does not carry the state it cropped out")
    # The tolerance in _path is in output pixels, so scoping a point map to one
    # county magnifies the whole state by about thirty and the simplification
    # that removed most of Oregon's coastline at state scale removes nothing.
    # Every scale answer in the static build carried 96 KB of thirty-five
    # counties drawn off-canvas, and the file went from 111 KB to 29 KB.
    import tools as _T
    store_ = _T.Store()
    framed = _T.render_point_map(store_, metric="total_spending", county="Multnomah")
    check("a county-framed point map is a fraction of the size it was",
          len(framed["data"]["svg"]) < 60000, str(len(framed["data"]["svg"])))
    # What is dropped is only what is outside the frame. The neighbours that
    # remain are the context the map is read against.
    check("and the counties still in frame are still drawn",
          framed["data"]["svg"].count("<path") >= 3,
          str(framed["data"]["svg"].count("<path")))
    check("a ring wholly outside the frame is dropped",
          maps._path({"coordinates": [[[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]]]},
                     lambda x, y: (x - 900, y), clip=(680, 400)) == "")
    check("and one inside it is kept",
          maps._path({"coordinates": [[[(0, 0), (60, 0), (60, 60), (0, 60), (0, 0)]]]},
                     lambda x, y: (x, y), clip=(680, 400)) != "")

    print("\na quantity cut into named pieces")
    split = viz.split_bar(
        "A government: running costs against construction", "Reported expenditure, 2023",
        [{"label": "Operating", "value": 900}, {"label": "Capital", "value": 100}],
        headline="10%", headline_label="went into capital",
        headline_share=10.0, peer=25.0, peer_label="peer median")
    check("the pieces are drawn in proportion to the whole", split.count("<path") >= 2)
    check("and each piece states its money and its share",
          "$900" in split and "90%" in split, split[:200])
    # The two tracks share a zero; a tick inside the composition bar would land
    # in whichever segment happened to be under it and be read against that.
    check("the peer comparison is a second track, not a mark inside the bar",
          "Capital share against the peer group" in split and "peer median" in split)
    check("a piece worth nothing is not drawn as a piece",
          viz.split_bar("t", None, [{"label": "a", "value": 5},
                                    {"label": "b", "value": 0}]).count("<rect") == 1)
    check("and nothing at all returns nothing rather than an empty frame",
          viz.split_bar("t", None, [{"label": "a", "value": 0}]) is None)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
