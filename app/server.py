#!/usr/bin/env python3
"""Local server for the Public Foundry chat UI.

    python3 app/server.py            then open http://localhost:8000

Standard library only, no build step, no dependencies.

Runs in one of two modes, and says which in the interface rather than pretending.

    grounded   No model. Presets map directly to tool calls, so every figure,
               chart and rule is real; what is missing is the prose that reads
               them back. This is the whole product minus the sentence.

    full       ANTHROPIC_API_KEY is set and the anthropic package is installed.
               Free-text questions go through the orchestrator and the model
               writes the answer against the same tool results.

The mode boundary is deliberate. Everything except the wording is deterministic
and testable, so a wrong answer in grounded mode is a bug in the tool layer, and
a wrong answer only in full mode is a model adherence problem. That is the same
delivery-versus-adherence split the eval harness reports.
"""

import http.server
import json
import os
import socketserver
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))

import blocks as B            # noqa: E402
import format as fmt          # noqa: E402
import reports as R           # noqa: E402
import tools as T             # noqa: E402
import viz                    # noqa: E402

STORE = T.Store()

# Appendix A, gated by the availability manifest the ETL computed. A preset is
# only offered when the block it needs is present, which is what the prompt
# already asks for and what stops the interface promising an answer the data
# cannot give.
# preset id -> §5 question type, which §15.2 turns into a block order
QUESTION_TYPE = {
    "profile": "factual_lookup",
    "scale": "financial_interpretation",
    "spending": "financial_interpretation",
    "inside": "financial_interpretation",
    "purchasable": "financial_interpretation",
    "unusual_areas": "what_stands_out",
    "vs_peers_area": "place_or_cross_entity",
    "vs_peers_time": "place_or_cross_entity",
    "workforce": "financial_interpretation",
    "trend": "financial_interpretation",
    "salient": "what_stands_out",
    "governance": "governance",
    "ecosystem": "place_or_cross_entity",
    "map_area": "place_or_cross_entity",
    "limits": "factual_lookup",
    "report": "place_or_cross_entity",
}

# Fiscal stability is absent by design. It is a composite of two components that
# move for opposite reasons, so a reader learns less from a position on it than
# from a position on any single measure underneath it, and every question below
# is answerable without it.
PRESETS = [
    # (id, label, group, required availability key, handler)
    ("profile", "What kind of government is this?", "Orientation", None, "profile"),
    ("scale", "Is this budget large or small for its type?", "Orientation",
     "has_operating_vs_capital", "scale"),
    ("spending", "Where does the money actually go?", "Orientation",
     "has_spending_breakdown", "spending"),

    ("unusual_areas", "Which service areas is it unusual on?", "Follow the money",
     "has_spending_breakdown", "unusual_areas"),
    ("inside", "Take me inside its largest service area", "Follow the money",
     "has_financial_functions", "inside"),
    ("purchasable", "What in its biggest function is actually purchasable?",
     "Follow the money", "has_financial_functions", "purchasable"),

    ("vs_peers_area", "How does its public safety spending compare per resident?",
     "Against other governments", "has_spending_breakdown", "vs_peers_area"),
    ("vs_peers_time", "Has spending per resident outgrown its peers?",
     "Against other governments", "has_financial_trends", "vs_peers_time"),
    ("trend", "How have revenue and spending moved?", "Against other governments",
     "has_financial_trends", "trend"),

    ("workforce", "How is the workforce distributed?", "Workforce",
     "has_workforce", "workforce"),
    ("salient", "What stands out about this government?", "Workforce",
     "has_operating_vs_capital", "salient"),

    ("ecosystem", "Which governments serve this place?", "Place", None, "ecosystem"),
    ("map_area", "Map public safety spending across Oregon", "Place", None, "map_area"),
    ("governance", "Who governs this entity?", "Place", "has_offices", "governance"),

    ("report", "Give me the full report", "Reports", None, "report"),
    ("limits", "What can this data not tell me?", "Reports", None, "limits"),
]

# The service area the two comparison presets open on. Public safety is the one
# category almost every general-purpose government reports, which is what makes
# it a defensible default rather than a favourite.
DEFAULT_AREA = "Public Safety"

# Presets whose answer does not depend on the selected government. A statewide
# map is about Oregon, not about whichever government happens to be selected, so
# it is rendered once rather than once per entity. That one change is half the
# weight of the static build.
ENTITY_INDEPENDENT = {"map_area"}

COMPARISON_FORMS = ("per_capita_by_service_area", "per_capita_over_time",
                    "service_area_across_entities", "entities_over_time")


def peer_set(pid6, count=3):
    """The entity plus its nearest peers by population, within its own type.

    Nearest by population rather than largest, because a comparison against the
    four biggest governments in Oregon is a comparison against Portland however
    the question was phrased. §13.3 keeps the set inside one government type.
    """
    anchor = STORE.row(
        "SELECT e.pid6, e.gov_type_name, w.population FROM entities e "
        "JOIN workforce_profile w ON w.pid6 = e.pid6 WHERE e.pid6=? AND w.population > 0",
        pid6)
    if not anchor:
        return [pid6], None
    neighbours = STORE.rows(
        "SELECT e.pid6 FROM entities e JOIN workforce_profile w ON w.pid6 = e.pid6 "
        "WHERE e.gov_type_name = ? AND e.pid6 != ? AND w.population > 0 "
        "ORDER BY ABS(w.population - ?) LIMIT ?",
        anchor["gov_type_name"], pid6, anchor["population"], count)
    basis = (f"the {count} Oregon {anchor['gov_type_name']} entities closest in "
             f"population")
    return [pid6] + [n["pid6"] for n in neighbours], basis


def model_client():
    """Return an orchestrator with a model attached, or None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import orchestrator as O
        model = os.environ.get("PF_MODEL", "claude-opus-5")
        return O.Orchestrator(store=STORE, client=O.anthropic_client(model))
    except Exception:
        return None


AGENT = model_client()
MODE = "full" if AGENT else "grounded"


# ------------------------------------------------------------- handlers

def _headline(entity, result):
    """One sentence of real numbers, assembled from the tool result.

    Deliberately templated rather than generated. In grounded mode the interface
    says a model wrote nothing, so this must read as a readout, not as prose.
    """
    data = result["data"]
    name = entity["common_name"] or entity["legal_name"]
    tool = result["tool"]

    if tool == "compare_to_peer_group" and data.get("peers"):
        peers = data["peers"]
        where = {"top quarter": "the top quarter of", "bottom quarter": "the bottom quarter of",
                 "middle half": "the middle half of"}.get(data.get("quartile"), "")
        return (f"{data['label']} is {data['formatted']}, which sits in {where} "
                f"{fmt.count(peers['n'])} {data['peer_group']} entities. The median is "
                f"{fmt.rate(peers['median'])} and the middle half runs "
                f"{fmt.rate(peers['p25'])} to {fmt.rate(peers['p75'])}.")
    if tool == "drill" and data.get("level") == "function":
        top = data["functions"][0]
        line = (f"Inside {data['service_area']}, the largest function is {top['label']} "
                f"at {fmt.money(top['value'])}, of which {fmt.money(top['addressable'])} "
                "is vendor addressable.")
        if top.get("peer_median"):
            line += (f" The median {top['label']} budget among peers reporting it is "
                     f"{fmt.money(top['peer_median'])}.")
        return line
    if tool == "drill" and data.get("level") == "line_item":
        items = {i["label"]: i["value"] for i in data["items"]}
        return (f"{data['function']} runs {fmt.money(items.get('Operating expenditure'))} "
                f"operating and {fmt.money(items.get('Capital expenditure'))} capital. "
                f"After {fmt.money(items.get('Personnel, adjusted'))} of personnel and "
                f"{fmt.money(items.get('Excluded amounts'))} that cannot be purchased, "
                f"{fmt.money(items.get('Vendor addressable'))} is addressable. That last "
                "figure is a derivation, not a reported line.")
    if tool == "get_spending_breakdown" and data.get("service_areas"):
        top = data["service_areas"][0]
        return (f"Largest reported area is {top['service_area']} at "
                f"{fmt.money(top['total'])}, {fmt.percent(top['percentage'])} of spending.")
    if tool == "get_workforce" and data.get("total_employees") is not None:
        return (f"{fmt.count(data['total_employees'])} employees, average wage "
                f"{fmt.money(data.get('average_annual_wage'))}.")
    if tool == "find_salient":
        if data.get("nothing_unusual"):
            return "Nothing in this profile clears an attention threshold."
        lead = data["lead"]
        return f"Most unusual: {lead['finding']}."
    if tool == "get_offices" and data.get("total_seats"):
        return (f"{data['total_seats']} seats recorded across "
                f"{len(data['roles'])} roles. No holder names are in this data.")
    if tool == "list_ecosystem":
        return (f"{data['total']} governments are filed under {data['county']}, "
                f"of which {data['mappable']} have boundaries.")
    if tool == "get_entity_profile":
        population = data.get("population")
        return (f"{entity['gov_type_name']}"
                + (f", population {fmt.count(population)}" if population else "")
                + f", host county {(entity['host_county'] or '').title()}.")
    return None


def _compare_headline(result):
    """One sentence over a multi-entity chart: the range, and who is at the ends.

    A comparison with no sentence leaves the reader to find the finding, and the
    finding here is almost always the spread rather than any single bar.
    """
    data = result["data"]
    detail = data.get("detail") or {}
    form = data["form"]

    if form in ("per_capita_by_service_area", "service_area_across_entities"):
        rows = detail.get("entities") or []
        if not rows:
            return ("None of these governments reports spending in that area. That "
                    "usually means another government holds the responsibility, not "
                    "that the service is unfunded.")
        formatter = fmt.rate if form == "per_capita_by_service_area" else fmt.money
        basis = ("per resident" if form == "per_capita_by_service_area"
                 else "in total dollars, which ranks by population")
        top, bottom = rows[0], rows[-1]
        line = (f"On {detail['service_area']} {basis}, {top['label']} is highest at "
                f"{formatter(top['value'])} and {bottom['label']} lowest at "
                f"{formatter(bottom['value'])}.")
        if len(rows) > 1 and bottom["value"]:
            line += f" That is a {top['value'] / bottom['value']:.1f}× spread."
        absent = detail.get("absent") or []
        if absent:
            line += (f" {len(absent)} of the set reports nothing here: "
                     f"{', '.join(absent)}.")
        excluded = detail.get("excluded_no_population") or []
        if excluded:
            line += (f" {len(excluded)} has no population in the data and cannot be "
                     f"put on this basis: {', '.join(excluded)}.")
        return line

    series = detail.get("series") or []
    if len(series) < 2:
        return ("Fewer than two of these governments can be drawn over time. An entity "
                "needs two or more years, and a per-resident line needs a population.")
    growth = sorted(
        ((s["label"], s["points"][-1][1] / s["points"][0][1])
         for s in series if s["points"][0][1]), key=lambda pair: pair[1], reverse=True)
    years = f"{series[0]['points'][0][0]} and {series[0]['points'][-1][0]}"
    line = (f"Between {years}, {growth[0][0]} grew fastest at {growth[0][1]:.2f}× and "
            f"{growth[-1][0]} slowest at {growth[-1][1]:.2f}×.")
    baseline = detail.get("baseline")
    if baseline and baseline["points"][0][1]:
        pace = baseline["points"][-1][1] / baseline["points"][0][1]
        ahead = [name for name, ratio in growth if ratio > pace]
        line += (f" The {baseline['gov_type']} median grew {pace:.2f}× over the same "
                 f"years, so {len(ahead)} of {len(growth)} outgrew their peers.")
    if detail.get("indexed"):
        line += (" These lines show growth and deliberately hide level: a government "
                 "that doubled from a low base outruns one that grew slightly from a "
                 "high one.")
    return line


def _with_magnitude(match):
    """Attach the one figure that tells two same-named governments apart.

    Oregon has several Springfields and a great many Washingtons, and a row
    reading only "Municipal · Marion" does not distinguish them. Population where
    there is one, total spending otherwise, because a school district serves an
    enrollment rather than a resident count.
    """
    row = STORE.row(
        "SELECT w.population, o.total_expenditure FROM entities e "
        "LEFT JOIN workforce_profile w ON w.pid6 = e.pid6 "
        "LEFT JOIN operating_vs_capital o ON o.pid6 = e.pid6 WHERE e.pid6 = ?",
        match["pid6"]) or {}
    return {**match,
            "population": row.get("population"),
            "total_expenditure": row.get("total_expenditure")}


def _section_readout(section, entity=None):
    """One or two sentences of real figures for a report section.

    A report plan is written for a model to fill in, so an unwritten section
    renders as its own brief: "To write here… Budget 60 words." That is the
    right scaffold for the model path and the wrong thing entirely to show a
    reader, who did not ask for the instructions. In grounded mode there is no
    model, so the readouts are assembled here from the same figures, the same
    way every other preset answers without one.

    Templated rather than generated, deliberately. The interface says no model
    wrote this, so it has to read as a readout and not as prose.
    """
    data = section.get("data") or {}
    section_id = section["id"]
    name = (entity["common_name"] or entity["legal_name"]) if entity else "this place"

    if section_id == "scope":
        absent = data.get("absent") or []
        if not absent:
            return ("Every block this report draws on is present for this government. "
                    "The data still measures what is spent and who is employed, and "
                    "contains no performance measures, service levels or results.")
        return (f"{len(absent)} of the blocks this report can draw on are missing here: "
                f"{', '.join(absent)}. That is an absence of data, not a report of zero. "
                "Nothing here measures results either.")

    if section_id == "identity":
        population = data.get("population")
        employees = data.get("total_employees")
        # The Census type names are labels, not English. "Portland is a
        # Municipal" is not a sentence a reader should have to parse.
        plain = {"Municipal": "a city", "County": "a county",
                 "School District": "a school district",
                 "Special District": "a special district",
                 "State": "a state government"}.get(
                     (entity or {}).get("gov_type_name"), "a government")
        parts = [f"{name} is {plain}"]
        if population:
            parts.append(f"serving {fmt.count(population)} residents")
        if employees:
            parts.append(f"with {fmt.count(employees)} employees on the payroll")
        return ", ".join(parts) + "."

    if section_id == "scale":
        peers = data.get("peers") or {}
        if not peers:
            return "No peer distribution covers this government for spending per resident."
        where = {"top quarter": "the top quarter", "bottom quarter": "the bottom quarter",
                 "middle half": "the middle half"}.get(data.get("quartile"), "the range")
        return (f"{name} spends {data['formatted']} per resident, which places it in "
                f"{where} of {fmt.count(peers['n'])} {data['peer_group']} entities. The "
                f"median is {fmt.rate(peers['median'])} and the middle half runs "
                f"{fmt.rate(peers['p25'])} to {fmt.rate(peers['p75'])}.")

    if section_id == "spending":
        areas = data.get("service_areas") or []
        if not areas:
            return "This government has no spending breakdown in the data."
        top = areas[0]
        line = (f"The largest reported area is {top['service_area']} at "
                f"{fmt.money(top['total'])}, {fmt.percent(top['percentage'])} of spending.")
        if data.get("other_exceeds_largest_named"):
            line += (" An unclassified bucket is larger still, so this breakdown is "
                     "partial and no category here is the biggest.")
        return line

    if section_id == "inside_largest":
        functions = data.get("functions") or []
        if not functions:
            return "No functions are reported inside this area."
        top = functions[0]
        line = (f"Inside {data['service_area']}, the largest function is {top['label']} at "
                f"{fmt.money(top['value'])}, of which {fmt.money(top['addressable'])} is "
                "vendor addressable.")
        if top.get("peer_median"):
            line += (f" The median {top['label']} budget among peers reporting it is "
                     f"{fmt.money(top['peer_median'])}.")
        line += (" The addressable figure is a derivation, not a reported line: operating "
                 "and capital less personnel and less amounts that cannot be purchased.")
        return line

    if section_id == "workforce":
        employees = data.get("total_employees")
        if not employees:
            return ("This government reports no employees. That usually means the work is "
                    "contracted out or another entity performs it.")
        line = f"{fmt.count(employees)} employees"
        if data.get("employees_per_1000"):
            line += f", {fmt.rate(data['employees_per_1000'])} per 1,000 residents"
        if data.get("average_annual_wage"):
            line += f", at an average wage of {fmt.rate(data['average_annual_wage'])}"
        areas = data.get("by_service_area") or []
        if areas:
            line += (f". The largest group works in {areas[0]['service_area']}, "
                     f"{fmt.percent(areas[0]['percentage'])} of listed staff")
        return line + ". Shares are of listed functions, not of all staff."

    if section_id == "trend":
        rows = section.get("table") or []
        if len(rows) < 2:
            return "Fewer than two years are available, so there is no trend to read."
        return (f"Revenue and spending are shown from {rows[0]['year']} to "
                f"{rows[-1]['year']}, at entity totals. No change here can be attributed "
                "to any single service area.")

    if section_id == "salience":
        if data.get("nothing_unusual"):
            return ("Nothing in this profile clears an attention threshold. The government "
                    "is close to typical for its peer group.")
        lead = data.get("lead") or {}
        return (f"The most structurally unusual thing here is that {lead.get('finding')}. "
                f"Read that as {lead.get('reading')}.")

    if section_id == "governance":
        seats = data.get("total_seats")
        if not seats:
            return ("The data includes no office records for this government. That is the "
                    "data's limit, not a statement that it has no governing body.")
        roles = data.get("roles") or []
        return (f"{seats} seats are recorded across {len(roles)} roles. The data carries no "
                "holder names, so who sits in them is not something this can answer.")

    if section_id == "membership":
        return (f"{data.get('total')} governments are filed under this county. They are "
                "listed by type below. Their budgets cannot be summed: overlapping "
                "jurisdictions tax the same property, so a total would double-count.")

    if section_id == "cross_boundary":
        return (f"{data.get('count')} further governments operate here but are filed under "
                "a neighbouring county, so a host-county list misses them. Their finances "
                "are recorded against that other county.")

    if section_id == "geography":
        return (f"{data.get('mappable')} of {data.get('total')} governments here have "
                "boundaries in the data, so a map of this place is necessarily partial. "
                "The list is the complete answer.")

    if section_id == "responsibility":
        return ("No single body is accountable for this place's combined public spending. "
                "Responsibility is distributed across the governments named above.")

    return None


def _gap(points):
    """A gap in percentage points. Below ten, the decimal is the difference
    between two rows; above it, the decimal is noise."""
    if abs(points) >= 10:
        return f"{points:+.0f} pts"
    return f"{points:+.1f} pts"


def answer_preset(preset_id, pid6=None, county=None):
    """Run a preset. Returns blocks the UI renders in order."""
    if preset_id in ENTITY_INDEPENDENT:
        pid6 = None
    entity = T._entity(STORE, pid6) if pid6 else None
    blocks, results = [], []

    def add(result, chart_form=None):
        results.append(result)
        headline = _headline(entity, result) if entity else None
        if not headline:
            # A tool with nothing to return says why. Leading with that sentence
            # is better than dropping the reader straight into a refusal drawing.
            absence = next((c for c in result["caveats"]
                            if c["code"].startswith("no_")), None)
            if absence:
                headline = absence["guidance"]
        if headline:
            blocks.append({"kind": "text", "text": headline})
        if chart_form and entity:
            chart = T.render_chart(STORE, pid6, chart_form)
            results.append(chart)
            if chart["data"].get("svg"):
                blocks.append({"kind": "chart", "svg": chart["data"]["svg"],
                               "table": chart["data"].get("table")})

    def add_comparison(result, extra_blocks=True):
        """A multi-entity chart, which carries its own table rather than a headline."""
        results.append(result)
        data = result["data"]
        if data.get("svg"):
            blocks.append({"kind": "chart", "svg": data["svg"],
                           "table": data.get("table")})
        elif extra_blocks:
            blocks.append({"kind": "text",
                           "text": "That comparison cannot be drawn from this data."})

    import explore

    if preset_id == "profile":
        add(T.get_entity_profile(STORE, pid6))
    elif preset_id == "scale":
        add(explore.compare_to_peer_group(STORE, pid6, "expenditure_per_capita"),
            "peer_range")
    elif preset_id == "spending":
        add(T.get_spending_breakdown(STORE, pid6), "spending_composition")
    elif preset_id == "unusual_areas":
        result = explore.drill(STORE, pid6)
        results.append(result)
        areas = result["data"].get("areas") or []
        # §11 ranks by divergence from the peer, not by size. The largest line in
        # a budget is almost always the same category for every government of a
        # type, so ranking by size answers a question nobody asked.
        rated = [a for a in areas
                 if a.get("peer_median_share") and not a.get("peer_degenerate")]
        for area in rated:
            area["gap"] = (area["share"] or 0) - area["peer_median_share"]
        rated.sort(key=lambda a: abs(a["gap"]), reverse=True)
        if rated:
            lead = rated[0]
            direction = "more" if lead["gap"] > 0 else "less"
            blocks.append({"kind": "text", "text": (
                f"The largest divergence from peers is {lead['label']}: "
                f"{fmt.percent(lead['share'])} of spending against a median of "
                f"{fmt.percent(lead['peer_median_share'])} across "
                f"{fmt.count(lead['peer_n'])} peers, which is "
                f"{abs(lead['gap']):.0f} points {direction}.")})
            blocks.append({"kind": "table", "rows": [
                {"service area": a["label"], "share here": fmt.percent(a["share"]),
                 "peer median": fmt.percent(a["peer_median_share"]),
                 "gap": _gap(a["gap"]), "amount": fmt.money(a["value"])}
                for a in rated]})
        else:
            blocks.append({"kind": "text", "text": (
                "No service area here has a sound peer median to diverge from. Most "
                "of this peer group reports nothing in most areas, so a median is "
                "not a midpoint and a gap against it would not mean anything.")})
    elif preset_id == "inside":
        area = STORE.row("SELECT service_area FROM financial_functions WHERE pid6=? "
                         "GROUP BY service_area ORDER BY SUM(total_function_pae) DESC "
                         "LIMIT 1", pid6)
        add(explore.drill(STORE, pid6, area["service_area"]) if area
            else T.envelope("drill"), "service_area_functions")
    elif preset_id == "purchasable":
        top = STORE.row("SELECT service_area, function_name FROM financial_functions "
                        "WHERE pid6=? ORDER BY total_function_pae DESC LIMIT 1", pid6)
        if top:
            result = explore.drill(STORE, pid6, top["service_area"], top["function_name"])
            add(result)
            items = result["data"].get("items") or []
            if items:
                blocks.append({"kind": "table", "rows": [
                    {"component": i["label"], "amount": fmt.money(i["value"]),
                     "what it is": i["note"]} for i in items]})
        else:
            add(T.envelope("drill", caveats=[{
                "code": "no_functions", "rule": "§2",
                "guidance": "This entity reports no functions, so there is nothing to "
                            "decompose. That is an absence of detail, not a report of "
                            "zero spending."}]))
    elif preset_id == "vs_peers_area":
        group, basis = peer_set(pid6)
        if len(group) < 2:
            blocks.append({"kind": "text", "text": (
                "This entity has no population in the data, so a per-resident "
                "comparison cannot be built for it. Special districts and school "
                "districts serve areas that are not a resident count.")})
            results.append(T.envelope("per_capita_by_service_area", caveats=[{
                "code": "no_population_denominator", "rule": "§10",
                "guidance": "No population, so no per-resident basis."}]))
        else:
            blocks.append({"kind": "text", "text": (
                f"Compared against {basis}, on spending per resident rather than "
                "total dollars: totals rank these by population, which the reader "
                "already knows.")})
            add_comparison(T.render_comparison(
                STORE, group, "per_capita_by_service_area", service_area=DEFAULT_AREA))
    elif preset_id == "vs_peers_time":
        group, basis = peer_set(pid6)
        if len(group) < 2:
            blocks.append({"kind": "text", "text": (
                "This entity has no population in the data, so it cannot be put on "
                "a per-resident axis.")})
            results.append(T.envelope("per_capita_over_time", caveats=[{
                "code": "no_population_denominator", "rule": "§10",
                "guidance": "No population, so no per-resident basis."}]))
        else:
            blocks.append({"kind": "text", "text": (
                f"Every line starts at 100 in the first year, against {basis}, with "
                "the dashed line showing the median for this government type. There "
                "is no price index in this data, so that baseline is peers and not "
                "inflation.")})
            add_comparison(T.render_comparison(
                STORE, group, "per_capita_over_time", indexed=True))
    elif preset_id == "map_area":
        area_map = T.render_map(STORE, "county", "service_area_per_resident",
                                service_area=DEFAULT_AREA)
        results.append(area_map)
        data = area_map["data"]
        if data.get("svg"):
            blocks.append({"kind": "text", "text": (
                f"County spending on {DEFAULT_AREA} per resident, across all 36 Oregon "
                "counties. This is county government only: cities, school districts "
                "and special districts spend here too and are not on this map.")})
            polygons = data.get("polygons") or 0
            blocks.append({"kind": "map", "svg": data["svg"],
                           "coverage": (data.get("covered", 0) / polygons)
                           if polygons else None})
            blocks.append({"kind": "table", "rows": [
                {"county": r["name"], "per resident": r["value"]}
                for r in (data.get("table") or [])[:10]]})
    elif preset_id == "workforce":
        add(T.get_workforce(STORE, pid6), "workforce_composition")
    elif preset_id == "trend":
        add(T.get_entity_profile(STORE, pid6), "finances_over_time")
    elif preset_id == "salient":
        add(T.find_salient(STORE, pid6))
    elif preset_id == "governance":
        add(T.get_offices(STORE, pid6))
        result = results[0]
        if result["data"].get("roles"):
            blocks.append({"kind": "table",
                           "rows": [{"role": r["role"], "seats": r["seat_count"]}
                                    for r in result["data"]["roles"]]})
    elif preset_id == "ecosystem":
        name = county or (entity["host_county"] if entity else None)
        result = T.list_ecosystem(STORE, county_name=name)
        results.append(result)
        data = result["data"]
        if data:
            blocks.append({"kind": "text",
                           "text": f"{data['total']} governments are filed under "
                                   f"{data['county']}, of which {data['mappable']} have "
                                   f"boundaries in this data."})
            blocks.append({"kind": "table",
                           "rows": [{"type": k, "count": v}
                                    for k, v in sorted(data["by_type"].items())]})
            if data.get("also_serving_filed_elsewhere"):
                blocks.append({
                    "kind": "text",
                    "text": f"{len(data['also_serving_filed_elsewhere'])} more operate here "
                            "but are filed under a neighbouring county, so a host-county "
                            "list misses them:"})
                blocks.append({"kind": "table", "rows": [
                    {"government": e["common_name"] or e["legal_name"],
                     "type": e["gov_type_name"],
                     "filed under": (e["filed_county"] or "").title()}
                    for e in data["also_serving_filed_elsewhere"]]})
            county_map = T.render_map(STORE, "place", "spending_per_resident",
                                      county=(data["county"] or "").split()[0])
            results.append(county_map)
            map_data = county_map["data"]
            if map_data.get("svg"):
                polygons = map_data.get("polygons") or 0
                blocks.append({
                    "kind": "map", "svg": map_data["svg"],
                    "coverage": (map_data.get("covered", 0) / polygons) if polygons else None})
    elif preset_id == "limits":
        result = T.get_entity_profile(STORE, pid6) if pid6 else T.envelope("scope")
        results.append(result)
        available = result["data"].get("available") or {}
        absent = [k.replace("has_", "").replace("_", " ")
                  for k, v in available.items() if not v]
        blocks.append({"kind": "text",
                       "text": "This data measures what governments spend and who they "
                               "employ. It contains no performance measures, service "
                               "levels or results."})
        if absent:
            blocks.append({"kind": "list", "items": [f"No {a}" for a in absent]})
    elif preset_id == "report":
        kind = "entity" if pid6 else "place"
        plan = R.compose_report(STORE, kind=kind, pid6=pid6,
                                county_name=county or (entity["host_county"] if entity else None))
        results.append(plan)
        if plan["data"]:
            sections = plan["data"]["sections"]
            # Without a model there is nobody to write the sections, and a plan
            # rendered unwritten shows a reader its own authoring brief. The
            # readouts stand in, assembled from the figures each section already
            # carries.
            prose = {}
            for section in sections:
                written = _section_readout(section, entity)
                if written:
                    prose[section["id"]] = written
            blocks.append({"kind": "text", "text": (
                f"A {len(sections)}-section profile follows. Every figure in it is read "
                "out of the data rather than written up, and each section carries the "
                "rules bound to it.")})
            blocks.append({"kind": "report", "html": R.render_report(plan, prose),
                           "sections": len(sections),
                           "budget": plan["data"]["total_word_budget"]})
    else:
        return {"blocks": [{"kind": "text", "text": f"No preset '{preset_id}'."}],
                "rules": [], "trace": []}

    # ---- assemble into the §15 vocabulary --------------------------------
    question_type = QUESTION_TYPE.get(preset_id, "factual_lookup")
    flags = T._flags(STORE, pid6) if pid6 else {}

    # §15.1: one answer block. Several readouts join rather than stacking as
    # separate answers, because §15.2 places exactly one.
    sentences = [b["text"] for b in blocks if b["kind"] == "text"]
    if not sentences:
        sentences = ["The data has nothing to show for that here. That is an "
                     "absence of data, not a report of zero."]

    vintages = {}
    for result in results:
        vintages.update(result.get("vintage") or {})

    composed = [
        B.interpretation(STORE, entity, vintages),
        {"kind": "answer", "text": " ".join(sentences)},
    ]

    # §15.1: a figure block where the answer is a quantity, marked per §15.3.
    marks = B.marks_for(flags)
    for result in results:
        data = result.get("data") or {}
        if result["tool"] == "compare_to_peer_group" and data.get("peers"):
            composed.append(B.figure(
                data["label"], data["formatted"], marks=marks,
                context=f"peer median {fmt.rate(data['peers']['median'])} across "
                        f"{fmt.count(data['peers']['n'])} {data['peer_group']} entities"))
        elif result["tool"] == "drill" and data.get("level") == "line_item":
            addressable = next((i["value"] for i in data["items"]
                                if i["label"] == "Vendor addressable"), None)
            composed.append(B.figure(
                f"Vendor addressable, {data['function']}", fmt.money(addressable),
                marks=marks, year=data.get("year"),
                context="a derivation, not a reported line"))
        elif result["tool"] == "get_spending_breakdown" and data.get("service_areas"):
            top = data["service_areas"][0]
            composed.append(B.figure(
                f"Largest reported area, {top['service_area']}",
                fmt.money(top["total"]), marks=marks,
                year=(result.get("vintage") or {}).get("finance")))

    composed += [b for b in blocks if b["kind"] in ("chart", "map", "table", "report", "list")]
    composed.append(B.limits(results))
    # Never offer back the question just asked.
    asked = {label for pid, label, _, _, _ in PRESETS if pid == preset_id}
    composed.append(B.next_questions(STORE, pid6, asked=asked))

    ordered = B.compose(question_type, composed)
    violations = B.validate(question_type, [b for b in ordered
                                            if b["kind"] in B.BLOCK_ORDER[question_type]])

    return {"blocks": ordered,
            "question_type": question_type,
            "violations": violations,
            "trace": [r["tool"] for r in results]}


# ------------------------------------------------------------------ http

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/mode":
            return self._json({"mode": MODE,
                               "entities": STORE.row(
                                   "SELECT COUNT(*) AS n FROM entities")["n"],
                               "css": viz.css(".pf-viz") + R.REPORT_CSS})

        if parsed.path == "/api/search":
            term = (query.get("q") or [""])[0].strip()
            if len(term) < 2:
                return self._json({"matches": []})
            result = T.find_entity(STORE, term, limit=12)
            return self._json({"matches": [_with_magnitude(m)
                                           for m in result["data"]["matches"]],
                               "ambiguous": any(c["code"] == "ambiguous_name"
                                                for c in result["caveats"])})

        if parsed.path == "/api/service_areas":
            # Ordered by how many governments report them, so the picker opens on
            # the areas a comparison will actually find data in.
            return self._json({"areas": [r["service_area"] for r in STORE.rows(
                "SELECT service_area, COUNT(*) AS n FROM spending_by_service_area "
                "WHERE total > 0 GROUP BY service_area ORDER BY n DESC")]})

        if parsed.path == "/api/presets":
            pid6 = (query.get("pid6") or [None])[0]
            available = {}
            if pid6:
                row = STORE.row("SELECT * FROM data_availability WHERE pid6=?", pid6)
                available = dict(row) if row else {}
            entity = T._entity(STORE, pid6) if pid6 else None
            return self._json({
                "entity": entity,
                "presets": [
                    {"id": pid, "label": label, "group": group,
                     "available": (needs is None or bool(available.get(needs)))}
                    for pid, label, group, needs, _ in PRESETS]})

        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or "{}")
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/preset":
            return self._json(answer_preset(payload.get("id"), payload.get("pid6"),
                                            payload.get("county")))

        if parsed.path == "/api/compare":
            # The reader picks the governments and the measure; every label,
            # number and caveat still comes from the tool layer. What is
            # selectable is the question, not the answer.
            ids = [str(p) for p in (payload.get("pid6_list") or []) if p]
            form = payload.get("form") or "per_capita_by_service_area"
            if form not in COMPARISON_FORMS:
                return self._json({"error": f"no form '{form}'"}, 400)
            if len(ids) < 2:
                return self._json({"blocks": [{"kind": "text", "text":
                                   "Pick at least two governments to compare."}],
                                   "rules": [], "trace": []})
            result = T.render_comparison(
                STORE, ids, form,
                service_area=payload.get("service_area") or DEFAULT_AREA,
                indexed=bool(payload.get("indexed")))
            data = result["data"]
            blocks = [{"kind": "answer", "text": _compare_headline(result)}]
            if data.get("svg"):
                blocks.append({"kind": "chart", "svg": data["svg"],
                               "table": data.get("table")})
            blocks.append(B.limits([result]))
            return self._json({"blocks": [b for b in blocks if b],
                               "trace": [result["tool"]],
                               "refused": data.get("refused", False)})

        if parsed.path == "/api/ask":
            question = (payload.get("question") or "").strip()
            if not AGENT:
                return self._json({
                    "blocks": [{"kind": "text",
                                "text": "Free-text questions need a model. Set "
                                        "ANTHROPIC_API_KEY and install the anthropic "
                                        "package, then restart. Every preset works now, "
                                        "and the figures, charts and rules they return are "
                                        "the same ones the model would be given."}],
                    "rules": [], "trace": []})
            answer, trace = AGENT.answer(question)
            return self._json({
                "blocks": [{"kind": "text", "text": answer}],
                "rules": [], "trace": [c["tool"] for c in trace["calls"]],
                "question_type": trace["question_type"],
                "delivered": sorted(set(trace["caveats_delivered"]))})

        return self._json({"error": "unknown endpoint"}, 404)


def main():
    port = int(os.environ.get("PORT", 8000))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), Handler) as server:
        print(f"Public Foundry, {MODE} mode, http://localhost:{port}")
        if MODE == "grounded":
            print("No ANTHROPIC_API_KEY, so presets work and free text does not.")
        server.serve_forever()


if __name__ == "__main__":
    main()
