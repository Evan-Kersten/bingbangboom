"""The atlas: every measure that can be put on a map, and whether it should be.

This is the prototyping surface. Somebody testing whether a spatial relationship
is worth committing to needs to try a layer, see it, and throw it away, and the
expensive mistake at that stage is not a slow tool. It is committing to a layer
that looked convincing in a working session and turns out, three months into the
build, to have been mostly absence with a few marks in it.

So every measure here is drawn through a gate and every result says which of two
things happened, in the same shape:

    drawn      the picture, plus what share of the extent carries a value
    refused    the reason, drawn in the frame, plus what would have to change

A conventional mapping tool draws whatever it is asked for. That is the right
default for a tool whose job is exploration over data somebody else has already
vetted. It is the wrong default when the question under test is whether the data
supports the layer at all, which is exactly the question in a discovery phase.

**One registry, two ramps, and a refusal between them.** Fiscal measures and
survey measures both appear here, because an analyst wants them in one place.
They are drawn on different colour ramps and they can never be paired. §4: a
government's spending and the conditions of the people it serves are aggregate
patterns observed in the same geography, and two maps side by side is the most
persuasive way to assert a causal claim without writing a sentence anybody could
challenge. `compare` refuses that pairing by measure kind rather than by asking
the caller to be careful.

The registry is derived from the metric tables in `tools` and the indicator list
in `community` rather than restating them. A second copy of a list is a list that
goes stale, and the drift would be invisible here: a measure missing from the
atlas is a measure nobody can reach, which looks like a product decision.
"""

import community as C
import format as fmt
import maps
import tools as T

# What a measure needs told to it beyond a layer. Service areas and functions
# select rows rather than columns, so they are arguments, not separate measures.
SELECTOR = {"service_area": "service_area", "function": "function"}

# Layers a survey measure may be drawn on. There are no tract-level DP03 rows in
# any of the three source files, so a finer grain is unavailable however fine the
# boundaries in the repository are.
# A survey measure describes a geography and a fiscal one describes a
# government, and that is why these two lists differ rather than one being a
# subset of the other. A poverty rate is a property of the place polygon: it was
# measured over that ground and drawing it as a shape is correct. A government's
# spending is a property of a body that has an office and, two thirds of the
# time, no boundary at all, so shading ground for it was always the compromise.
SURVEY_LAYERS = ("county", "place")

# "government" is a point layer, not a boundary one. It exists because 1,029 of
# Oregon's special districts have no polygon anywhere in this data and are
# therefore absent from every choropleth in this registry: a fiscal map of
# Oregon's local governments that silently omits two thirds of them is the
# mostly-absence failure this module was built to catch, committed by the tool
# doing the catching. A pin says where a government's office is and nothing
# else, which is far less than a boundary and far more than leaving it out.
#
# Survey measures are not offered on it. DP03 describes a geography and a pin is
# a government; drawing a poverty rate on a government's office would attach a
# population's conditions to a body that did not produce them, which is the §4
# pairing the whole module refuses elsewhere.
# Counties, and then pins. Places and school districts were choropleth layers
# and should not have been: 378 cities at state scale compare land areas rather
# than the measure, which is why every statewide place map refused, and school
# districts join by name at 156 of 223 so a third of them were simply missing.
# Both are governments with an address, and a pin says where each one is without
# claiming to say how far it reaches. Counties keep the shading because a county
# genuinely is the ground it covers and all 36 join exactly.
FISCAL_LAYERS = ("county", "government")


# Every layer some measure can be drawn on, in the order they narrow. Derived
# rather than listed for the same reason `measures` is derived: the interface is
# sent this at runtime, and a hand-written copy of it in the page is how a layer
# gets added to the registry and never becomes selectable. `place` is here on
# the strength of the survey measures alone; a fiscal measure asked for it is
# refused by `draw` and shown as "not on this layer" in the catalogue, which is
# the account somebody deciding whether to build a city layer actually wants.
LAYER_ORDER = ("county", "place", "school_district", "government")


def layers():
    live = set(FISCAL_LAYERS) | set(SURVEY_LAYERS)
    return [name for name in LAYER_ORDER if name in live]


def measures():
    """Everything mappable, with what it needs and where it can be drawn.

    Derived, never listed. `tools.MAP_METRICS` and friends are the definition of
    a fiscal measure and `community.INDICATORS` of a survey one; this reads them
    so that adding a metric there makes it reachable here without a second edit
    anybody could forget.
    """
    out = []
    for metric, (_, _, label) in T.MAP_METRICS.items():
        out.append({"id": metric, "label": label, "kind": "fiscal",
                    "needs": None, "layers": list(FISCAL_LAYERS),
                    "source": "Census of Governments finance, per entity"})
    for metric, (_, _, label) in T.SERVICE_AREA_MAP_METRICS.items():
        out.append({"id": metric, "label": label, "kind": "fiscal",
                    "needs": "service_area", "layers": list(FISCAL_LAYERS),
                    "source": "Census of Governments, spending by service area"})
    for metric, (_, _, label) in T.FUNCTION_MAP_METRICS.items():
        out.append({"id": metric, "label": label, "kind": "fiscal",
                    "needs": "function", "layers": list(FISCAL_LAYERS),
                    "source": "Census of Governments, functions inside a service area"})
    for code, name, unit, _ in C.INDICATORS:
        out.append({"id": code, "label": name, "kind": "survey", "unit": unit,
                    "needs": None, "layers": list(SURVEY_LAYERS),
                    "source": f"American Community Survey DP03, {C.VINTAGES[0]} release"})
    return out


BY_ID = None


def _lookup(measure_id):
    global BY_ID
    if BY_ID is None:
        BY_ID = {m["id"]: m for m in measures()}
    return BY_ID.get(measure_id)


def _labelled(measure, selectors):
    """The measure's label with its selector in it.

    "Share of spending on" is a column heading; "Share of spending on Fire
    Protection" is what the panel is of. A catalogue listing two rows both
    called "Share of spending on" is a catalogue nobody can read.
    """
    if measure["needs"] and selectors.get(measure["needs"]):
        return f"{measure['label']} {selectors[measure['needs']]}"
    return measure["label"]


def _panel(measure, result, label=None):
    """One result in the shape every panel is read in, whichever path drew it.

    The two renderers report coverage differently because they refuse for
    different reasons: a fiscal map refuses when too few boundaries report the
    measure, a survey map when too few estimates have a margin narrow enough to
    bin. Both are coverage, and flattening them to one shape is what lets a
    reader compare two panels without learning two vocabularies.
    """
    data = result.get("data") or {}
    label = label or measure["label"]
    if measure["kind"] == "survey":
        shown, withheld = data.get("shown"), data.get("withheld")
        total = (shown or 0) + (withheld or 0)
        drawn = bool(data.get("svg"))
        return {
            "measure": measure["id"], "label": label, "kind": "survey",
            "drawn": drawn, "svg": data.get("svg"),
            "covered": shown, "total": total or None,
            "coverage": (shown / total) if total else None,
            # A survey figure is withheld, not missing: the geography exists and
            # was measured, and the estimate is simply too loose to place.
            "withheld": withheld,
            "reason": None if drawn else _first_reason(result),
            "source": measure["source"],
        }
    polygons = data.get("polygons") or 0
    drawn = bool(data.get("svg")) and not data.get("refused")
    return {
        "measure": measure["id"], "label": label, "kind": "fiscal",
        "drawn": drawn, "svg": data.get("svg"),
        "covered": data.get("covered"), "total": polygons or None,
        "coverage": data.get("density"),
        # How much of the money for this measure sits on the drawn layer at all,
        # which is a different question from how much of the layer is coloured.
        "layer_share": data.get("layer_share"),
        "refused_because": data.get("refused_because"),
        "reason": None if drawn else _first_reason(result),
        "source": measure["source"],
    }


def _first_reason(result):
    """The caveat that explains a refusal, in the reader's words."""
    ranked = ("map_mostly_absence", "too_thin_to_map", "nothing_reliable",
              "unknown_level", "no_such_function", "function_required",
              "service_area_required")
    by_code = {c["code"]: c["guidance"] for c in result.get("caveats", [])}
    for code in ranked:
        if code in by_code:
            return by_code[code]
    return next(iter(by_code.values()), "This measure could not be drawn.")


def draw(store, measure_id, layer="county", county=None, **selectors):
    """One measure on one layer, drawn or refused, in the atlas shape."""
    measure = _lookup(measure_id)
    if not measure:
        return T.envelope("atlas_draw", caveats=[{
            "code": "unknown_measure", "rule": "§4",
            "guidance": f"No measure '{measure_id}'. Call measures() for the list."}])
    if layer not in measure["layers"]:
        return T.envelope("atlas_draw", caveats=[{
            "code": "layer_unavailable", "rule": "§9",
            "guidance": f"{measure['label']} cannot be drawn on the {layer} layer. "
                        f"Available: {', '.join(measure['layers'])}."
                        + (" There are no tract-level rows in this survey data, so a "
                           "finer grain is unavailable however fine the boundaries are."
                           if measure["kind"] == "survey" else "")}])

    if layer == "government":
        result = T.render_point_map(store, metric=measure_id, county=county,
                                    gov_type=selectors.get("gov_type"),
                                    service_area=selectors.get("service_area"),
                                    function=selectors.get("function"))
        data = result["data"]
        panel = {
            "measure": measure_id, "label": _labelled(measure, selectors),
            "kind": "fiscal", "drawn": bool(data.get("drawn")),
            "svg": data.get("svg"), "covered": data.get("placed"),
            "total": data.get("total"), "coverage": data.get("coverage"),
            "layer_share": None, "refused_because": None if data.get("drawn") else "unplaced",
            "reason": None if data.get("drawn") else data.get("reason"),
            "source": measure["source"] + ", placed by address",
        }
        return T.envelope("atlas_draw",
                          data={"panel": panel, "layer": layer, "county": county,
                                "selectors": selectors},
                          caveats=result.get("caveats", []),
                          blocked=result.get("not_computable", []))

    if measure["kind"] == "survey":
        result = C.render_community_map(store, code=measure_id, geo_level=layer,
                                        county=county)
    else:
        result = T.render_map(store, layer=layer, metric=measure_id, county=county,
                              service_area=selectors.get("service_area"),
                              function=selectors.get("function"))
    panel = _panel(measure, result, _labelled(measure, selectors))
    return T.envelope("atlas_draw", data={"panel": panel, "layer": layer,
                                          "county": county, "selectors": selectors},
                      caveats=result.get("caveats", []),
                      blocked=result.get("not_computable", []))


def compare(store, measure_ids, layer="county", county=None, **selectors):
    """Two measures side by side, which is the only honest form for two measures.

    §15.4 forbids two measures on one plot, because two scales on one chart
    invent a correlation. Two panels are the sanctioned form and this draws them
    with their own scales and their own legends.

    What it will not do is pair a spending measure with a condition measure. §4
    forbids asserting that spending explains conditions or conditions explain
    spending, and a reader shown those two maps together will draw that line
    whatever the caption says; it is the most persuasive way to make a causal
    claim without writing a sentence anybody could argue with. The refusal is by
    measure kind rather than by a warning, because a warning is advice and this
    needs to be a wall.
    """
    if len(measure_ids) != 2:
        return T.envelope("atlas_compare", caveats=[{
            "code": "two_panels", "rule": "§15",
            "guidance": "A comparison is two measures. One is a map; three is a wall "
                        "of pictures nobody reads."}])
    found = [_lookup(m) for m in measure_ids]
    if not all(found):
        missing = [m for m, f in zip(measure_ids, found) if not f]
        return T.envelope("atlas_compare", caveats=[{
            "code": "unknown_measure", "rule": "§4",
            "guidance": f"No measure {' or '.join(missing)}."}])

    kinds = {m["kind"] for m in found}
    if len(kinds) > 1:
        spending = next(m for m in found if m["kind"] == "fiscal")
        survey = next(m for m in found if m["kind"] == "survey")
        return T.envelope(
            "atlas_compare",
            data={"refused": True, "panels": []},
            caveats=[{
                "code": "no_investment_against_conditions", "rule": "§4",
                "guidance": f"{spending['label']} is what a government spends and "
                            f"{survey['label']} is a condition of the people who live "
                            "there. Putting them side by side asserts that one explains "
                            "the other, which this data cannot support: they are "
                            "aggregate patterns observed in the same geography, filed "
                            "per entity and published per geography, with nothing "
                            "joining them. Draw either one alone. If the question is "
                            "whether investment matches need, §10 says aggregate every "
                            "government serving the place first, and say on what basis "
                            "you normalised."}],
            blocked=[T.not_computable("outcome_or_performance")])

    panels, caveats, seen = [], [], set()
    for measure in found:
        result = draw(store, measure["id"], layer=layer, county=county, **selectors)
        panels.append(result["data"]["panel"] if result["data"] else None)
        for caveat in result["caveats"]:
            if caveat["code"] not in seen:
                seen.add(caveat["code"])
                caveats.append(caveat)

    # Two panels drawn from the same table at different vintages is the one
    # comparison where a shared scale would be right; two different measures is
    # not, and this never shares one. Said out loud because a reader assumes a
    # shared scale unless told otherwise.
    if all(p and p["drawn"] for p in panels):
        caveats.append({
            "code": "panels_have_separate_scales", "rule": "§15",
            "guidance": "Each panel is binned over its own values, so a colour in one "
                        "does not mean the same amount as the same colour in the other. "
                        "Compare the shape of each pattern, never the shades between "
                        "them."})
    return T.envelope("atlas_compare",
                      data={"refused": False, "panels": panels, "layer": layer,
                            "county": county},
                      caveats=caveats)


def catalogue(store, layer="county", county=None, **selectors):
    """Every measure on one layer, with what it would draw and what it would not.

    This is the artifact a discovery phase is trying to produce: not a map, but
    the account of which layers are worth building. It says, per measure, how
    much of the extent carries a value, how much of the subject sits on the layer
    at all, and where a refusal would fire, so a decision to commit to a layer is
    made against measured coverage rather than against a demo that happened to
    use a well-covered example.
    """
    rows = []
    for measure in measures():
        if layer not in measure["layers"]:
            rows.append({"id": measure["id"], "measure": measure["label"],
                         "kind": measure["kind"],
                         "verdict": "not on this layer", "coverage": None,
                         "detail": f"drawn on {', '.join(measure['layers'])}",
                         "source": measure["source"]})
            continue
        if measure["needs"] and not selectors.get(measure["needs"]):
            rows.append({"id": measure["id"], "measure": measure["label"],
                         "kind": measure["kind"],
                         "verdict": "needs a selector", "coverage": None,
                         "detail": f"name a {measure['needs'].replace('_', ' ')}",
                         "source": measure["source"]})
            continue
        panel = draw(store, measure["id"], layer=layer, county=county,
                     **selectors)["data"]["panel"]
        rows.append({
            # The id rides with the row so the interface can put a verdict on
            # the control that selects the measure, rather than only in a table
            # somebody reaches after choosing one.
            "id": measure["id"],
            "measure": panel["label"], "kind": measure["kind"],
            "verdict": "drawn" if panel["drawn"] else "refused",
            "coverage": panel["coverage"],
            "detail": (f"{panel['covered']} of {panel['total']} carry a value"
                       if panel["total"] else "nothing to draw")
            + (f", {fmt.percent(100 * panel['layer_share'])} of the subject is on "
               "this layer" if panel.get("layer_share") is not None else "")
            + ("" if panel["drawn"]
               else f" — {(panel['reason'] or 'not drawn')[:140]}"),
            "source": panel["source"],
        })
    drawn = sum(1 for r in rows if r["verdict"] == "drawn")
    return T.envelope(
        "atlas_catalogue",
        data={"layer": layer, "county": county, "rows": rows,
              "drawn": drawn, "considered": len(rows)},
        caveats=[{
            "code": "catalogue_is_a_snapshot", "rule": "§8",
            "guidance": f"{drawn} of {len(rows)} measures can be drawn on the "
                        f"{layer.replace('_', ' ')} layer today. Coverage is a property "
                        "of this build of the data, so a refreshed source can change a "
                        "verdict here; record the date beside any layer decision this "
                        "informs."}])


T.TOOLS["atlas_measures"] = lambda store: T.envelope(
    "atlas_measures", data={"measures": measures()})
T.TOOLS["atlas_draw"] = draw
T.TOOLS["atlas_compare"] = compare
T.TOOLS["atlas_catalogue"] = catalogue
