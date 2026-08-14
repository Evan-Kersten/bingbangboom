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
import rules                  # noqa: E402
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
    "revenue_mix": "financial_interpretation",
    "community": "place_or_cross_entity",
    "debt": "financial_interpretation",
    "scale": "financial_interpretation",
    "spending": "financial_interpretation",
    "inside": "financial_interpretation",
    "function_split": "financial_interpretation",
    "unusual_areas": "what_stands_out",
    "vs_peers_area": "place_or_cross_entity",
    "vs_peers_time": "place_or_cross_entity",
    "workforce": "financial_interpretation",
    "trend": "financial_interpretation",
    "salient": "what_stands_out",
    "governance": "governance",
    "serving": "place_or_cross_entity",
    "ecosystem": "place_or_cross_entity",
    "map_area": "place_or_cross_entity",
    "map_community": "place_or_cross_entity",
    "limits": "factual_lookup",
    "report": "place_or_cross_entity",
}

# Fiscal stability is absent by design. It is a composite of two components that
# move for opposite reasons, so a reader learns less from a position on it than
# from a position on any single measure underneath it, and every question below
# is answerable without it.
PRESETS = [
    # (id, label, group, required availability key, handler)
    #
    # Ordered by what a reader standing outside the building would want to know
    # first, not by how the data is filed. The first group is money that came
    # from them and people they might meet; the abstractions come after.
    #
    # Labels name the stake rather than the table. "How is the workforce
    # distributed?" is a column heading. "How many teachers, officers or
    # firefighters, and how thinly spread?" is a question somebody has.
    ("revenue_mix", "Whose money is this — your tax bill, or the state's?",
     "Where it comes from", "has_revenue_sources", "revenue_mix"),
    ("scale", "Is this a lot to spend for a place this size?", "Where it comes from",
     "has_operating_vs_capital", "scale"),
    ("debt", "What does it owe, and against how much a year?", "Where it comes from",
     "has_financial_trends", "debt"),

    ("community", "What is this place like for the people who live here?",
     "Where it comes from", "has_dp03", "community"),

    ("workforce", "Who does it employ, and how thinly are they spread?",
     "Who does the work", "has_workforce", "workforce"),
    ("governance", "Who runs it, and do you elect them?", "Who does the work",
     "has_offices", "governance"),
    ("salient", "What stands out about this government?", "Who does the work",
     "has_operating_vs_capital", "salient"),

    ("spending", "Where does the money actually go?", "Follow the money",
     "has_spending_breakdown", "spending"),
    ("unusual_areas", "Which service areas is it unusual on?", "Follow the money",
     "has_spending_breakdown", "unusual_areas"),
    ("inside", "Take me inside its largest service area", "Follow the money",
     "has_financial_functions", "inside"),
    # Level three of the drill. The split is the §11 signal at this depth:
    # capital share says whether a government is mid-construction on the thing it
    # spends most on, which is a fact about the year rather than a preference.
    ("function_split", "Is its biggest function running costs or construction?",
     "Follow the money", "has_financial_functions", "function_split"),

    # The denominator is left out of these two labels on purpose: it is residents
    # for a city and students for a school district, and the answer states which.
    ("vs_peers_area", "How does its public safety spending compare, head for head?",
     "Against other governments", "has_spending_breakdown", "vs_peers_area"),
    ("vs_peers_time", "Has its spending outgrown its peers?",
     "Against other governments", "has_financial_trends", "vs_peers_time"),
    ("trend", "How have revenue and spending moved?", "Against other governments",
     "has_financial_trends", "trend"),

    # The first question a resident actually has, and the reason it leads the
    # group: everything above assumes you already knew which government to ask
    # about. This one starts from the only thing anybody knows for certain,
    # which is where they live.
    ("serving", "Which governments serve you here?", "Place", "has_serving", "serving"),
    ("ecosystem", "What else is filed under this county?", "Place", None, "ecosystem"),
    ("map_area", "Map public safety spending across Oregon", "Place", None, "map_area"),
    ("map_community", "Map how Oregon is doing, county by county", "Place", None,
     "map_community"),

    ("profile", "What kind of government is this?", "Reports", None, "profile"),
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
ENTITY_INDEPENDENT = {"map_area", "map_community"}

COMPARISON_FORMS = ("per_capita_by_service_area", "per_capita_over_time",
                    "service_area_across_entities", "entities_over_time",
                    "five_year_panel", "five_year_change")


def availability(pid6):
    """Which presets this government has the data for. Defined in the tool layer
    so the follow-up chooser and the static export agree with this list."""
    return T.availability(STORE, pid6)


def preset_shell():
    """The question list before a government is chosen, all of it disabled.

    Sent rather than hard-coded in the page. The browser used to carry its own
    copy of this list for static mode, which drifted from PRESETS the moment a
    question was added — the community question shipped and was never offered.
    """
    return [{"id": pid, "label": label, "group": group, "available": False}
            for pid, label, group, _, _ in PRESETS]


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
    unit = rules.denominator(anchor["gov_type_name"]) or "population"
    basis = (f"the {count} Oregon {anchor['gov_type_name']} entities closest in "
             f"{unit}")
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
                f"at {fmt.money(top['value'])}, operating plus capital.")
        if top.get("peer_median"):
            line += (f" The median {top['label']} budget among peers reporting it is "
                     f"{fmt.money(top['peer_median'])}.")
        return line
    if tool == "drill" and data.get("level") == "line_item":
        items = {i["label"]: i["value"] for i in data["items"]}
        line = (f"{data['function']} runs {fmt.money(items.get('Operating expenditure'))} "
                f"operating and {fmt.money(items.get('Capital expenditure'))} capital, "
                f"{fmt.money(data.get('total'))} together.")
        # §8: capital is lumpy, so the share is a position in a bond cycle rather
        # than a preference. Saying that here keeps the sentence from reading as
        # a characterisation of how this government chooses to spend.
        if data.get("capital_share"):
            line += (f" Capital is {fmt.percent(data['capital_share'])} of the function "
                     f"in {data['year']}, which is one year of a construction cycle "
                     "rather than a trend.")
        return line
    if tool == "get_spending_breakdown" and data.get("service_areas"):
        top = data["service_areas"][0]
        return (f"Largest reported area is {top['service_area']} at "
                f"{fmt.money(top['total'])}, {fmt.percent(top['percentage'])} of spending.")
    if tool == "get_workforce" and data.get("total_employees") is not None:
        # §9: a reported zero is a fact about how the work is arranged, not a
        # payroll of zero people to describe. "Average wage of None" is neither.
        if not data["total_employees"]:
            return ("This government reports no employees and no payroll. That is a "
                    "reported zero rather than missing data, and it usually means the "
                    "work is contracted out or another government performs it.")
        wage = data.get("average_annual_wage")
        return (f"{fmt.count(data['total_employees'])} employees on the payroll"
                + (f", at an average wage of {fmt.rate(wage)}" if wage else "") + ".")
    if tool == "staffing" and data.get("functions"):
        return _staffing_line(data)
    if tool == "community_context" and data.get("indicators"):
        return _community_line(data)
    if tool == "revenue_mix" and data.get("sources"):
        return _revenue_line(data)
    if tool == "debt_load" and data.get("ratio"):
        return _debt_line(data)
    if tool == "find_salient":
        if data.get("nothing_unusual"):
            return "Nothing in this profile clears an attention threshold."
        lead = data["lead"]
        return f"Most unusual: {lead['finding']}."
    if tool == "get_offices" and data.get("total_seats"):
        roles = len(data["roles"])
        line = (f"{data['total_seats']} seats recorded across "
                f"{roles} role{'' if roles == 1 else 's'}")
        elected, appointed = data.get("elected_seats"), data.get("appointed_seats")
        if elected:
            line += f", {elected} of them filled by election"
            if appointed:
                line += f" and {appointed} by appointment"
        terms = sorted({int(r["term_length"]) for r in data["roles"]
                        if (r.get("term_length") or "").isdigit()})
        if terms:
            line += (f". Terms run {terms[0]} years" if len(terms) == 1
                     else f". Terms run {terms[0]} to {terms[-1]} years")
        return line + ". No holder names are in this data."
    if tool == "list_ecosystem":
        return (f"{data['total']} governments are filed under {data['county']}, "
                f"of which {data['mappable']} have boundaries.")
    if tool == "governments_serving" and data.get("serving"):
        return _serving_line(data)
    if tool == "get_entity_profile":
        population = data.get("population")
        # "population 48,601" for a school district is a count of students. The
        # field has one name for every type; the sentence must not.
        unit = rules.denominator(entity["gov_type_name"])
        return (f"{entity['gov_type_name']}"
                + (f", {fmt.count(population)} {unit or 'people'}" if population else "")
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
        if detail.get("mixed_denominators"):
            units = " and per ".join(u.rstrip("s") for u in detail["mixed_denominators"])
            return (f"This set mixes governments measured per {units}. A school "
                    "district's population is its enrollment, so those are different "
                    "measures and cannot be ranked against each other. Compare in total "
                    "dollars, or split the set by type.")
        rows = detail.get("entities") or []
        if not rows:
            return ("None of these governments reports spending in that area. That "
                    "usually means another government holds the responsibility, not "
                    "that the service is unfunded.")
        formatter = fmt.rate if form == "per_capita_by_service_area" else fmt.money
        basis = (f"per {detail.get('unit') or 'resident'}"
                 if form == "per_capita_by_service_area"
                 else "in total dollars, which ranks by population")
        top, bottom = rows[0], rows[-1]
        # One row is not a comparison. Reading the first and last row off a
        # single row produced "Portland is highest at $709 and Portland lowest
        # at $709", which stages a race between a government and itself.
        if len(rows) == 1:
            line = (f"Only {top['label']} can be drawn here, at "
                    f"{formatter(top['value'])} on {detail['service_area']} {basis}. "
                    "One government is a figure, not a comparison.")
        else:
            line = (f"On {detail['service_area']} {basis}, {top['label']} is highest at "
                    f"{formatter(top['value'])} and {bottom['label']} lowest at "
                    f"{formatter(bottom['value'])}.")
            if bottom["value"]:
                line += f" That is a {top['value'] / bottom['value']:.1f}× spread."
        unchartable = detail.get("unchartable") or []
        if unchartable:
            one = len(unchartable) == 1
            line += (f" {', '.join(unchartable)} "
                     + ("reports" if one else "report")
                     + " neither a spending breakdown nor a year of totals, so there is "
                       "nothing to draw for " + ("it" if one else "them")
                     + " here; that is not a report of zero.")
        absent = detail.get("absent") or []
        if absent:
            line += (f" {len(absent)} of the set reports nothing here: "
                     f"{', '.join(absent)}.")
        excluded = detail.get("excluded_no_population") or []
        if excluded:
            line += (f" {len(excluded)} has no population in the data and cannot be "
                     f"put on this basis: {', '.join(excluded)}.")
        return line

    if form == "five_year_change":
        rows = detail.get("entities") or []
        if not rows:
            return (f"None of these governments reports both {detail['start']} and "
                    f"{detail['end']}, so there is nothing to measure between. That is "
                    "an absence of a filing, not a change of zero.")
        top, bottom = rows[0], rows[-1]
        span = detail["span"]
        if len(rows) == 1:
            line = (f"{top['label']} changed {top['value']:+.0f}% between "
                    f"{detail['start']} and {detail['end']}.")
        else:
            line = (f"Between {detail['start']} and {detail['end']}, {top['label']} "
                    f"changed most at {top['value']:+.0f}% and {bottom['label']} least "
                    f"at {bottom['value']:+.0f}%.")
        against = [r for r in rows if r["peer_median"] is not None]
        if against:
            ahead = [r for r in against if r["value"] > r["peer_median"]]
            line += (f" {len(ahead)} of {len(against)} outgrew the median for their own "
                     "government type over the same two years.")
        line += (f" This is a {span}-year change measured at two ends, not a trend: "
                 "nothing here says whether it was steady, and no price index is in "
                 "this data, so the figures are nominal.")
        return line

    if form == "five_year_panel":
        series = detail.get("series") or []
        if len(series) < 2:
            return (f"Fewer than two of these governments report more than one year "
                    f"between {detail['start']} and {detail['end']}. Most Oregon "
                    "governments file two years five years apart rather than annually, "
                    "so an annual panel covers about 380 of them. The change between "
                    "2017 and 2022 is the five-year comparison nearly all can answer.")
        ranked = sorted(((s["label"], s["change"]) for s in series
                         if s["change"] is not None),
                        key=lambda pair: pair[1], reverse=True)
        line = (f"Across {detail['start']} to {detail['end']}, {ranked[0][0]} grew "
                f"fastest at {ranked[0][1]:+.0f}% and {ranked[-1][0]} slowest at "
                f"{ranked[-1][1]:+.0f}%.")
        if detail.get("annual"):
            line += (f" {detail['annual']} of {len(series)} are measured every year in "
                     "this window.")
        if detail.get("interpolated"):
            line += (f" {detail['interpolated']} are not, and their lines are dashed "
                     "between observations because the years between were not filed.")
        # A government the reader picked and this window cannot draw has to be
        # named here, not only in the rules panel underneath.
        thin = detail.get("excluded_thin") or []
        if thin:
            line += (f" {', '.join(t['name'] for t in thin[:3])} "
                     + ("reports" if len(thin) == 1 else "report")
                     + f" fewer than two years between {detail['start']} and "
                       f"{detail['end']} and " + ("is" if len(thin) == 1 else "are")
                     + " not drawn; the five-year change reaches "
                     + ("it" if len(thin) == 1 else "them") + ".")
        return line

    if detail.get("mixed_denominators"):
        units = " and per ".join(u.rstrip("s") for u in detail["mixed_denominators"])
        return (f"This set mixes governments measured per {units}, which are different "
                "measures and cannot share an axis. Compare entity totals, or split the "
                "set by type.")
    series = detail.get("series") or []
    if len(series) < 2:
        unit = detail.get("unit") or "resident"
        return ("Fewer than two of these governments can be drawn over time. An entity "
                f"needs two or more years, and a per-{unit} line needs a {unit} count.")
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


def _named(entity):
    return entity["common_name"] or entity["legal_name"]


# A staffing ratio rests on one population estimate and one headcount, so a gap
# this size is inside the noise and is reported as no gap at all.
RATIO_FLAT_LOW, RATIO_FLAT_HIGH = 0.9, 1.1

# §11: name the gap only when it is one. A revenue share a few points off the
# median is how governments differ, not a finding about this one.
SHARE_GAP_NOTABLE = 8.0

# Past this much of a budget arriving from elsewhere, where the money comes from
# is a fact about the government's exposure and belongs in the answer.
TRANSFER_NOTABLE = 25.0

# Past this much of a budget landing in "Other Revenue", the split between money
# raised here and money sent here is not knowable from these lines.
UNCLASSIFIED_DOMINANT = 40.0


def _serving_line(data):
    """The stack, counted and named, with the shortfall in the same breath.

    The count and the caveat travel together on purpose. A reader told "four
    governments serve Bay City" and only later told the boundary files are thin
    has already filed the four away as the number.
    """
    # "1 municipal" is the register's word for it, not a reader's. The type
    # names are filing categories and this sentence is the one place in the
    # interface where the reader is being addressed about their own address.
    said = {"Municipal": "city", "County": "county", "School District": "school district",
            "Special District": "special district", "State": "state government"}
    kinds = {}
    for row in data["serving"]:
        kinds[row["gov_type_name"]] = kinds.get(row["gov_type_name"], 0) + 1
    listed = ", ".join(fmt.plural(n, said.get(kind, kind.lower()))
                       for kind, n in kinds.items())
    line = (f"{fmt.plural(data['total'], 'government')} established to serve "
            f"{data['place']}: {listed}.")
    if data["by_basis"].get("precinct_exact"):
        line += (f" {data['by_basis']['precinct_exact']} of them come from the precinct "
                 "file, which records the assignment rather than inferring it.")
    else:
        line += (f" A typical Oregon address sits inside about {data['typical_stack']} "
                 "governments. The ones missing here are special districts — fire, "
                 "water, transit — which have no boundary in this data outside "
                 "Multnomah County.")
    return line


def _community_line(data):
    """What the place is like, in the order a reader would ask it."""
    import community as C
    firm = [i for i in data["indicators"] if i["reliability"] == "firm"]
    if not firm:
        return ("Every community estimate for this place has a margin of error too "
                "wide to report as a figure. The survey covers it, but not closely "
                "enough to say what it found.")
    lead = firm[0]
    where = ("the county it sits in" if data["basis"] == "host_county"
             else data["place_name"])
    line = (f"In {where}, {lead['name'].lower()} is "
            f"{C.format_value(lead['estimate'], lead['unit'])}")
    rest = [i for i in firm[1:4]]
    if rest:
        line += ", with " + ", ".join(
            f"{i['name'].lower()} at {C.format_value(i['estimate'], i['unit'])}"
            for i in rest)
    line += f". American Community Survey, {data['vintage'] - 4} to {data['vintage']}."
    if data["basis"] == "host_county":
        line += (" This government is not a Census geography, so these describe its "
                 "host county rather than the area it serves.")
    if data["soft"]:
        line += (f" {fmt.plural(data['soft'], 'other estimate')} here "
                 + ("has" if data["soft"] == 1 else "have")
                 + " a margin too wide to report.")
    return line + (" These describe the population, not what the government achieved.")


def _pandemic_line(moved):
    """Only what cleared the margin, and never with a cause attached."""
    import community as C
    parts = []
    for detail in moved:
        first, last = detail["points"][0], detail["points"][1]
        direction = "higher" if detail["difference"] > 0 else "lower"
        parts.append(
            f"{detail['name'].lower()} is {direction} than in the 2019 release "
            f"({C.format_value(first['estimate'], detail['unit'])} to "
            f"{C.format_value(last['estimate'], detail['unit'])})")
    return ("Set against the pre-pandemic release, " + "; ".join(parts)
            + ". Both are five-year averages over windows that overlap, so this is "
              "two periods beside each other rather than a change measured at two "
              "moments — and nothing here says the government caused it or "
              "responded to it.")


def _revenue_line(data):
    """Where the money came from, led by the share a resident paid directly.

    Property tax leads when it is there, ahead of larger categories, because it
    is the only line on this list a reader recognises as their own money. The
    rest of the sentence is what a share cannot say on its own: whether this
    government raises its money or is handed it.
    """
    sources = {r["category"]: r for r in data["sources"]}
    top = data["sources"][0]
    own = sources.get("Property Tax")

    if own and own is not top:
        line = (f"{fmt.percent(own['percentage'])} comes from property tax, "
                f"{fmt.money(own['amount'])}, behind {top['category']} at "
                f"{fmt.percent(top['percentage'])}.")
    elif own:
        line = (f"{fmt.percent(own['percentage'])} of this revenue is property tax, "
                f"{fmt.money(own['amount'])} — the largest single source.")
    else:
        line = (f"The largest source is {top['category']} at "
                f"{fmt.percent(top['percentage'])}, {fmt.money(top['amount'])}. "
                "No property tax is reported here.")

    lead = own or top
    if lead.get("peer_median_share"):
        gap = lead["percentage"] - lead["peer_median_share"]
        if abs(gap) >= SHARE_GAP_NOTABLE:
            line += (f" That is {abs(gap):.0f} points "
                     f"{'above' if gap > 0 else 'below'} the median "
                     f"{fmt.percent(lead['peer_median_share'])} across "
                     f"{fmt.count(lead['peer_n'])} Oregon {data['gov_type']} entities.")

    transferred = data.get("transferred_pct") or 0
    own = data.get("own_source_pct") or 0
    # Property tax, fees and levies are raised here; aid is sent here; everything
    # else is neither. Where the unclassified remainder is the bulk of a budget,
    # saying "0% is raised inside the boundary" describes the classification
    # rather than the government, so the sentence names the remainder instead.
    unclassified = max(0.0, 100 - own - transferred)
    if transferred >= TRANSFER_NOTABLE:
        line += (f" {transferred:.0f}% of the total is transferred in from the state or "
                 "federal government rather than raised inside the boundary, so that "
                 "much of this budget turns on a decision made elsewhere.")
    elif unclassified >= UNCLASSIFIED_DOMINANT:
        line += (f" {unclassified:.0f}% of the total sits in unclassified categories, "
                 "so how much of this is raised inside the boundary cannot be read "
                 "off these lines.")
    else:
        line += f" {own:.0f}% is raised inside the boundary."
    return line


def _debt_line(data):
    """Debt against a year of revenue, which is the only form of it a reader can hold."""
    line = (f"{fmt.money(data['total_debt'])} outstanding at {data['year']}, against "
            f"{fmt.money(data['revenue'])} of revenue that year — "
            f"{data['ratio']:.2f} times what it takes in.")
    peers = data.get("peers")
    if peers and peers["median"]:
        line += (f" The median across {fmt.count(peers['n'])} Oregon {data['gov_type']} "
                 f"entities reporting both is {peers['median']:.2f} times.")
    line += (" This is a balance, not an annual shortfall: most of it is borrowed "
             "against assets meant to last decades.")
    return line


def data_units(result):
    """The plural noun this government's population is counted in."""
    return result["data"].get("units") or "people"


def _ballot_line(drawn):
    """One sentence about what a reader's ballot actually holds."""
    seats = fmt.plural(drawn["seat_count"], "seat")
    if drawn["ballot"] in ("by_district", "mixed"):
        named = drawn.get("districts_named") or []
        line = (f"{drawn['role']} is filled by district: {seats} across the "
                f"{fmt.plural(drawn['districts'], 'area')} below, and a voter fills "
                "the one covering their address rather than all of them.")
        if named and drawn["districts"] < drawn["seat_count"]:
            line += (f" {drawn['seat_count'] - drawn['districts']} of the districts "
                     "extend past Multnomah County, which is as far as the boundary "
                     "data goes, so they are not drawn.")
        return line
    return (f"{drawn['role']} is filled at large: one electorate, drawn below, and "
            f"every voter inside it votes on all {seats}. The position numbers on "
            "the ballot are labels, not places.")


def _locator_line(data, name):
    """One sentence saying what kind of placement this map is."""
    if data["basis"] == "own_boundary":
        return (f"Where {name} sits in Oregon."
                + (" The boundary is recovered from precinct assignments rather than "
                   "filed by the district, so it shows roughly where it is and stops "
                   "at the Multnomah county line."
                   if data.get("recovered") else ""))
    if data["basis"] == "measured_extent":
        return (f"{name} has no boundary in this data. Highlighted are the counties it "
                "was measured to operate in, which is where it serves rather than its "
                "edge.")
    return (f"{name} has no boundary in this data — special districts, two thirds of "
            "Oregon's governments, have none at all. Highlighted is the county it "
            "files under, which published data assigns by where most of its assessed "
            "value sits, so it locates the filing and not the service area.")


def _ratio(value):
    """One staff member per this many people, rounded to how well it is known."""
    if value is None:
        return None
    return f"{value:,.0f}" if value >= 10 else f"{value:.1f}"


def _staffing_line(data):
    """The staff split as a community would say it.

    A headcount is a fact about an organisation. The same figure against the
    population it serves is a fact about a place, and it is the one worth
    leading with: 688 firefighters means nothing without the city behind it.
    """
    rows = data["functions"]
    lead = max(rows, key=lambda r: r["headcount"])
    name = lead["role"] or lead["function_name"]
    line = (f"{fmt.count(lead['headcount'])} of them are {name}, "
            f"{fmt.percent(lead['pct_of_total'])} of the payroll.")

    if lead.get("per_staff"):
        line += f" That is one for every {_ratio(lead['per_staff'])} {data['units']}"
        peer = lead.get("peer_median_per_staff")
        if peer:
            # §11: a gap inside the band is not a finding, and calling a 1%
            # difference "thinner than most" invents one. Direction is only named
            # once the gap is large enough to survive the estimate underneath it.
            gap = lead["per_staff"] / peer
            where = ("in line with" if RATIO_FLAT_LOW <= gap <= RATIO_FLAT_HIGH
                     else ("thinner than" if gap > 1 else "thicker than"))
            line += (f", against a median of {_ratio(peer)} across "
                     f"{fmt.count(lead['peer_n'])} Oregon {data['gov_type']} entities, "
                     f"so this payroll is spread {where} most")
        line += "."

    others = [r for r in rows if r is not lead and r.get("role")]
    if others:
        line += " Also named: " + ", ".join(
            f"{fmt.count(r['headcount'])} {r['role']}" for r in others[:2]) + "."

    if data.get("covered_pct") and data["covered_pct"] < 95:
        line += (f" These named jobs are {data['covered_pct']:.0f}% of the payroll; "
                 "the rest is in functions this data does not break out.")
    return line


def _with_magnitude(match):
    """Attach the one figure that tells two same-named governments apart, named.

    Oregon has several Springfields and a great many Washingtons, and a row
    reading only "Municipal · Marion" does not distinguish them.

    The figure is labelled rather than left to be inferred, because the payload's
    population field is not the same quantity for every type: a school district
    reports its enrollment there, so 48,601 for Portland School District 1J is
    students and 641,162 for the City of Portland is residents. Showing both as a
    bare count invites a reader to compare them, and they are not comparable. A
    special district has no denominator at all, so it carries its budget instead.
    """
    row = STORE.row(
        "SELECT w.population, o.total_expenditure FROM entities e "
        "LEFT JOIN workforce_profile w ON w.pid6 = e.pid6 "
        "LEFT JOIN operating_vs_capital o ON o.pid6 = e.pid6 WHERE e.pid6 = ?",
        match["pid6"]) or {}
    unit = rules.denominator(match["gov_type_name"])
    population = row.get("population")
    spending = row.get("total_expenditure")

    if unit and population:
        figure, noun, kind = fmt.count(population), unit, unit
    elif spending:
        figure, noun, kind = fmt.money(spending), "budget", "budget"
    else:
        # §9. A row with nothing here has filed nothing, which is not a budget of
        # zero, and a blank cell would read as one.
        figure, noun, kind = "no budget filed", "", "absent"

    return {**match, "population": population, "total_expenditure": spending,
            "magnitude": f"{figure} {noun}".strip(), "magnitude_kind": kind,
            # Split so the interface can set the figure and its unit differently:
            # the number is what a reader scans, the noun is what stops them
            # comparing 48,601 students against 641,162 residents.
            "magnitude_figure": figure, "magnitude_unit": noun}


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
            # A school district's population field is enrollment, so the noun
            # follows the type rather than defaulting to residents.
            unit = rules.denominator((entity or {}).get("gov_type_name"))
            parts.append(f"serving {fmt.count(population)} {unit or 'people'}")
        if employees:
            parts.append(f"with {fmt.count(employees)} employees on the payroll")
        return ", ".join(parts) + "."

    if section_id == "scale":
        peers = data.get("peers") or {}
        if not peers:
            basis = rules.per_unit_label((entity or {}).get("gov_type_name")) or "per unit"
            return f"No peer distribution covers this government for spending {basis}."
        where = {"top quarter": "the top quarter", "bottom quarter": "the bottom quarter",
                 "middle half": "the middle half"}.get(data.get("quartile"), "the range")
        return (f"{name} spends {data['formatted']} {data.get('per_unit', 'per unit')}, "
                f"which places it in {where} of {fmt.count(peers['n'])} "
                f"{data['peer_group']} entities. The median is "
                f"{fmt.rate(peers['median'])} and the middle half runs "
                f"{fmt.rate(peers['p25'])} to {fmt.rate(peers['p75'])}.")

    if section_id == "where":
        return _locator_line(data, name)

    if section_id == "community":
        return _community_line(data)

    if section_id == "five_year_change":
        direction = "more" if data["change"] > 0 else "less"
        # The figures follow the years in the order the sentence names them, or a
        # reader maps the first number to the first year and reads the move backwards.
        line = (f"{name} spent {abs(data['change']):.0f}% {direction} in "
                f"{data['end']} than in {data['start']}: {fmt.money(data['first'])} "
                f"in {data['start']}, {fmt.money(data['last'])} in {data['end']}.")
        if data.get("peer_median") is not None:
            gap = data["change"] - data["peer_median"]
            where = ("about the same as" if abs(gap) < 5
                     else ("faster than" if gap > 0 else "slower than"))
            line += (f" The median across {fmt.count(data['peer_n'])} entities of its "
                     f"type moved {data['peer_median']:+.0f}% over the same two years, "
                     f"so this is {where} its peers.")
        if data.get("observations", 0) < (data["end"] - data["start"] + 1):
            line += (f" Only {fmt.plural(data['observations'], 'year')} inside that "
                     "window are filed, so this is a change between two ends and not "
                     "a path.")
        return line + " No price index is in this data, so the figures are nominal."

    if section_id == "five_year_panel":
        line = (f"{data['annual']} of these governments are measured in every year of "
                "the window.")
        if data.get("interpolated"):
            line += (f" {data['interpolated']} are not; the line between their "
                     "observations is drawn rather than measured, and is dashed for "
                     "that reason.")
        if data.get("excluded"):
            line += (f" {fmt.plural(len(data['excluded']), 'government')} report fewer "
                     "than two years here and are not drawn: "
                     + ", ".join(e["name"] for e in data["excluded"][:3]) + ".")
        return line

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
                f"{fmt.money(top['value'])}, operating plus capital.")
        if top.get("peer_median"):
            line += (f" The median {top['label']} budget among peers reporting it is "
                     f"{fmt.money(top['peer_median'])}.")
        return line

    if section_id == "workforce":
        employees = data.get("total_employees")
        if not employees:
            return ("This government reports no employees. That usually means the work is "
                    "contracted out or another entity performs it.")
        line = f"{fmt.count(employees)} employees"
        if data.get("employees_per_1000"):
            unit = rules.denominator((entity or {}).get("gov_type_name")) or "people"
            line += f", {fmt.rate(data['employees_per_1000'])} per 1,000 {unit}"
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


def _function_map_options(service_area):
    """The functions inside one service area, and what each can be drawn on.

    Ordered by what Oregon's local governments report spending on them, so the
    row a reader checks first is the one carrying the most money rather than the
    one that happens to map.
    """
    functions = [r["function_name"] for r in STORE.rows(
        "SELECT function_name, SUM(COALESCE(operating_expenditures, 0) "
        "     + COALESCE(capital_expenditures, 0)) AS spend "
        "FROM financial_functions WHERE service_area = ? "
        "GROUP BY function_name ORDER BY spend DESC", service_area)]
    rows = []
    for name in functions:
        drawn = T.render_function_map(STORE, name)
        data = drawn["data"]
        holders = data.get("holders") or []
        if data.get("refused") and data.get("refused_because") == "needs_a_county":
            # Mappable, but city by city, so it needs an extent a reader can
            # see. Calling this "no map" would be wrong in the direction that
            # matters: the picture exists, it just cannot be drawn statewide.
            drawable = f"{data['layer']}, county by county"
            why = "cities are specks at state scale, so this one needs a county"
        elif data.get("refused"):
            drawable = "no map"
            why = f"only {data['covered']} of {data['polygons']} boundaries report it"
            # Name who does the work only where they actually hold the money. A
            # function can fail the density gate while nearly all of it sits on
            # the layer, and blaming districts for that would be a story about
            # the wrong absence.
            share = data.get("layer_share")
            if holders and share is not None and share < rules.THRESHOLDS[
                    "map_layer_share_partial"]:
                why += (f", and {fmt.percent(100 * (1 - share))} of it is "
                        f"{holders[0]['gov_type'].lower()} work")
        else:
            drawable = data["layer"].replace("_", " ")
            why = (f"{fmt.percent(100 * data['layer_share'])} of the reported spending "
                   "sits on that layer")
        rows.append({"function": name, "drawn on": drawable, "why": why})
    return rows


def _map_blocks(result, intro):
    """A map result as blocks: the picture where it was drawn, the reason where
    it was not.

    A refused map is not a missing map. render_map returns the reason drawn in
    the frame, and dropping that leaves the reader with a list and no hint that
    a map was considered and declined, which is exactly the fact worth knowing:
    fire protection has no county map because fire districts have no boundary,
    not because nobody looked. The block carries no coverage figure because
    nothing was drawn, which also keeps it clear of the §15.1 floor check that
    guards real choropleths.
    """
    data = result.get("data") or {}
    if not data.get("svg"):
        return []
    if data.get("refused"):
        subject = data.get("function") or data.get("service_area") or "this measure"
        layer = (data.get("layer") or "").replace("_", " ")
        if data.get("refused_because") == "needs_a_county":
            line = (f"{subject} is mostly {layer} work, and Oregon's cities are specks at "
                    "state scale, so it maps one county at a time rather than statewide.")
        else:
            line = (f"{subject} cannot be drawn on a {layer} map: {data.get('covered')} of "
                    f"{data.get('polygons')} boundaries there report it.")
            unmapped = [h for h in (data.get("holders") or []) if h["unmapped"]]
            if unmapped:
                line += (" It is done by " + ", ".join(
                    f"{fmt.count(h['entities'])} {h['gov_type'].lower()} entities"
                    for h in unmapped[:2]) + ", which have no boundary in this data.")
        return [{"kind": "text", "text": line},
                {"kind": "map", "svg": data["svg"], "refused": True}]
    polygons = data.get("polygons") or 0
    coverage = (data.get("covered", 0) / polygons) if polygons else None
    if (coverage or 0) < B.MAP_COVERAGE_FLOOR:
        return []
    return [{"kind": "text", "text": intro},
            {"kind": "map", "svg": data["svg"], "coverage": coverage}]


def answer_function(function_name):
    """Who does this? The answer a name search cannot give.

    Ranked by spending, with the per-resident figure alongside, and the statewide
    map of the service area it belongs to. Every row is a government a reader can
    then open or add to a comparison, which is the point: a door that leads to
    real entities rather than to another empty search box.
    """
    import explore
    result = explore.who_spends_on(STORE, function_name)
    results = [result]
    data = result["data"]
    if not data.get("entities"):
        return {"blocks": [{"kind": "answer", "text":
                            f"No government reports spending on {function_name}."}],
                "question_type": "issue_or_topic", "violations": [], "trace": [result["tool"]]}

    top = data["entities"][0]
    blocks = [{"kind": "text", "text": (
        f"{fmt.count(data['total_governments'])} Oregon governments report spending on "
        f"{function_name}, across {data['counties']} counties. The largest is "
        f"{top['name']} at {fmt.money(top['spending'])}"
        + (f", or {fmt.rate(top['per_unit'])} per {top['unit']}"
           if top["per_unit"] and top["unit"] else "")
        + f". This is a ranking by size; the per-unit column reorders it. "
        f"{function_name} sits inside {data['service_area']}.")}]

    # The per-unit figure carries its unit in the cell, not in the heading: this
    # list mixes cities counted in residents with school districts counted in
    # students, and one heading over both would be a basis change nobody stated.
    blocks.append({"kind": "table", "rows": [
        {"government": row["name"], "type": row["gov_type"],
         "county": (row["host_county"] or "").title(),
         "spending": fmt.money(row["spending"]),
         "per resident or student": (
             f"{fmt.rate(row['per_unit'])} per {row['unit']}"
             if row["per_unit"] and row["unit"] else "no denominator"),
         "year": row["year"]}
        for row in data["entities"]]})

    # The function itself mapped across the counties, which is the question a
    # reader of this list has next: not only who spends on it, but where. It is
    # also the question that cannot always be answered. Where the governments
    # doing the work have no boundary, the map refuses and says which they are,
    # and the list above is the honest form of the same question.
    function_map = T.render_function_map(STORE, function_name)
    results.append(function_map)
    layer = (function_map["data"].get("layer") or "").replace("_", " ")
    blocks.extend(_map_blocks(
        function_map,
        f"Spending on {function_name} per resident, by {layer}, the layer holding "
        "most of it. The list above is several government types; the map is one."))

    composed = [{"kind": "answer",
                 "text": " ".join(b["text"] for b in blocks if b["kind"] == "text")}]
    composed += [b for b in blocks if b["kind"] in ("table", "map")]
    composed.append(B.limits(results))
    ordered = B.compose("place_or_cross_entity", composed)
    return {"blocks": ordered, "question_type": "place_or_cross_entity",
            "violations": B.validate("place_or_cross_entity",
                                     [b for b in ordered if b["kind"]
                                      in B.BLOCK_ORDER["place_or_cross_entity"]]),
            "trace": [r["tool"] for r in results]}


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
        # The peer strip says where this government sits in its distribution;
        # the map says where it sits in Oregon, on the same measure. A reader
        # arrives knowing which government is theirs and wanting both.
        placed = T.entity_layer(STORE, pid6)
        coverage = None
        # A choropleth needs a layer of peers to shade. The recovered special
        # district layer is 19 boundaries spanning fire, water, transit and
        # community colleges — not a peer group, and shading them on one measure
        # would invite a comparison none of them are in. They go to the locator.
        if placed and placed["layer"] == "special_district":
            placed = None
        if placed:
            scoped = (entity["host_county"] if placed["layer"] == "place" else None)
            area_map = T.render_map(
                STORE, placed["layer"], "spending_per_resident",
                county=scoped, highlight_pid6=pid6)
            results.append(area_map)
            data = area_map["data"]
            polygons = data.get("polygons") or 0
            coverage = (data.get("covered", 0) / polygons) if polygons else None
            # §15.1: a map is offered only where the picture is not mostly
            # absence. Below the floor the list is the honest form.
            if data.get("svg") and (coverage or 0) >= B.MAP_COVERAGE_FLOOR:
                blocks.append({"kind": "text", "text": (
                    f"The same measure across {'this county' if scoped else 'Oregon'}, "
                    f"with {_named(entity)} outlined. "
                    + ("Cities render as dots at state scale, so this is scoped to "
                       f"{(scoped or '').title()} County."
                       if scoped else
                       "This is one government type only; the others spend here too."))})
                blocks.append({"kind": "map", "svg": data["svg"], "coverage": coverage})
        # A choropleth needs a layer of peers to shade. A government without one
        # still has a place, and "cannot be put on a map" answered the reader's
        # actual question — where is this — with nothing.
        if not placed or not (coverage or 0) >= B.MAP_COVERAGE_FLOOR:
            located = T.render_locator_map(STORE, pid6)
            results.append(located)
            if located["data"].get("svg"):
                blocks.append({"kind": "text", "text": _locator_line(located["data"],
                                                                    _named(entity))})
                blocks.append({"kind": "map", "svg": located["data"]["svg"],
                               "coverage": 1.0})
            else:
                blocks.append({"kind": "text", "text": (
                    f"{_named(entity)} has neither a boundary nor a county in this "
                    "data, so it cannot be placed at all.")})
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
                         "GROUP BY service_area ORDER BY SUM(COALESCE(operating_expenditures, 0) "
                         "  + COALESCE(capital_expenditures, 0)) DESC "
                         "LIMIT 1", pid6)
        add(explore.drill(STORE, pid6, area["service_area"]) if area
            else T.envelope("drill"), "service_area_functions")
    elif preset_id == "function_split":
        top = STORE.row(
            "SELECT service_area, function_name FROM financial_functions WHERE pid6=? "
            "ORDER BY COALESCE(operating_expenditures, 0) "
            "       + COALESCE(capital_expenditures, 0) DESC LIMIT 1", pid6)
        if top:
            result = explore.drill(STORE, pid6, top["service_area"], top["function_name"])
            # No table. The split is two numbers and the answer sentence states
            # both, so a table of them restates the sentence, and §15.2 gives a
            # financial interpretation no table block to put it in.
            add(result)
            # Where the same function is done elsewhere in Oregon, the map for it
            # is the next question a reader has, and it is the one place the
            # coverage gate is visible: some functions can be drawn and some
            # cannot, and which is which is a fact about who does the work.
            function_map = T.render_function_map(
                STORE, top["function_name"],
                county=(entity or {}).get("host_county"), highlight_pid6=pid6)
            results.append(function_map)
            data = function_map["data"]
            layer = (data.get("layer") or "").replace("_", " ")
            scoped = data.get("county")
            blocks.extend(_map_blocks(
                function_map,
                f"{top['function_name']} per resident across "
                f"{(scoped or '').title() + ' County' if scoped else 'Oregon'}, by "
                f"{layer}, the layer holding most of this function, with "
                f"{_named(entity)} outlined. Other types report it too and are "
                "not drawn."))
        else:
            add(T.envelope("drill", caveats=[{
                "code": "no_functions", "rule": "§2",
                "guidance": "This entity reports no functions, so there is nothing to "
                            "split. That is an absence of detail, not a report of "
                            "zero spending."}]))
    elif preset_id == "vs_peers_area":
        group, basis = peer_set(pid6)
        per_unit = rules.per_unit_label(
            (entity or {}).get("gov_type_name")) or "per unit"
        if len(group) < 2:
            blocks.append({"kind": "text", "text": (
                "This entity has no denominator in the data, so a head-for-head "
                "comparison cannot be built for it. Special districts serve an "
                "extent, not a countable membership.")})
            results.append(T.envelope("per_capita_by_service_area", caveats=[{
                "code": "no_population_denominator", "rule": "§10",
                "guidance": "No denominator, so no per-unit basis."}]))
        else:
            blocks.append({"kind": "text", "text": (
                f"Compared against {basis}, on spending {per_unit} rather than "
                "total dollars: totals rank these by size, which the reader "
                "already knows.")})
            add_comparison(T.render_comparison(
                STORE, group, "per_capita_by_service_area", service_area=DEFAULT_AREA))
    elif preset_id == "vs_peers_time":
        group, basis = peer_set(pid6)
        if len(group) < 2:
            blocks.append({"kind": "text", "text": (
                "This entity has no denominator in the data, so it cannot be put "
                "on a per-unit axis.")})
            results.append(T.envelope("per_capita_over_time", caveats=[{
                "code": "no_population_denominator", "rule": "§10",
                "guidance": "No denominator, so no per-unit basis."}]))
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
            # The drill from the area to the functions inside it, said as a
            # table because the answer is which of them can be drawn at all.
            # Public Safety maps cleanly at the area level and separates one
            # level down: police is city and county work, fire is district work,
            # and a reader who only ever sees the area map would take the two to
            # be equally mappable. Naming the layer each one lands on, or why it
            # lands on none, is the honest form of "go deeper".
            inside = _function_map_options(DEFAULT_AREA)
            if inside:
                blocks.append({"kind": "text", "text": (
                    f"One level down, {DEFAULT_AREA} splits into functions that are not "
                    "done by the same kinds of government, so they do not all map. Where "
                    "the governments doing the work have no boundary in this data, the "
                    "map is refused rather than drawn over the gap.")})
                blocks.append({"kind": "table", "rows": inside})
    elif preset_id == "workforce":
        add(T.get_workforce(STORE, pid6), "workforce_composition")
        # The split into named jobs is what turns a payroll into a picture of a
        # place, so it follows the composition chart rather than replacing it.
        staff = T.TOOLS["staffing"](STORE, pid6)
        if staff["data"].get("functions"):
            add(staff)
            blocks.append({"kind": "table", "rows": [
                {"job": r["role"] or r["function_name"],
                 "staff": fmt.count(r["headcount"]),
                 "share of payroll": fmt.percent(r["pct_of_total"]),
                 f"{data_units(staff)} per staff member": _ratio(r["per_staff"]) or "no denominator",
                 "type median": _ratio(r["peer_median_per_staff"]) or "not comparable",
                 "average wage": fmt.rate(r["average_wage"])}
                for r in staff["data"]["functions"]]})
    elif preset_id == "trend":
        add(T.get_entity_profile(STORE, pid6), "finances_over_time")
    elif preset_id == "community":
        # Deliberately its own answer rather than a panel bolted onto a spending
        # one. Putting a budget and a poverty rate in a single block is where a
        # reader starts reading one as the other's cause, and §4 forbids exactly
        # that. Adjacent, in the same thread, is as close as they get.
        result = T.TOOLS["community_context"](STORE, pid6)
        add(result)
        data = result["data"]
        if data.get("indicators"):
            import community as C
            blocks.append({"kind": "table", "rows": [
                {"measure": i["name"],
                 "figure": C.format_value(i["estimate"], i["unit"]),
                 "give or take": (C.format_value(i["moe"], i["unit"])
                                  if i["moe"] is not None else "not stated"),
                 "can it carry weight": {
                     "firm": "yes", "soft": "no — margin too wide",
                     "unstated": "no margin given", "absent": "no estimate"
                 }[i["reliability"]]}
                for i in data["indicators"]]})

            # The pre-pandemic comparison, on a rate rather than a dollar figure,
            # and only where the two releases differ by more than the survey's
            # own margin allows.
            moved = []
            for code, name, unit, _ in C.INDICATORS:
                if unit == "dollars":
                    continue
                shift = T.TOOLS["since_pandemic"](STORE, pid6, code)
                detail = shift["data"]
                if detail.get("separated_from_margin"):
                    moved.append(detail)
                    results.append(shift)
            if moved:
                blocks.append({"kind": "text", "text": _pandemic_line(moved)})
            else:
                blocks.append({"kind": "text", "text": (
                    "Against the 2019 release, none of these has moved by more than "
                    "the survey's own margin of error allows. That is not a finding "
                    "of no change; it is the survey being unable to separate the two "
                    "estimates.")})

    elif preset_id == "revenue_mix":
        result = T.TOOLS["revenue_mix"](STORE, pid6)
        add(result)
        data = result["data"]
        if data.get("sources"):
            blocks.append({"kind": "table", "rows": [
                {"source": r["category"],
                 "share": fmt.percent(r["percentage"]),
                 "amount": fmt.money(r["amount"]),
                 "type median share": (fmt.percent(r["peer_median_share"])
                                       if r["peer_median_share"] else "not comparable"),
                 "what it is": r["meaning"] or "unclassified"}
                for r in data["sources"]]})
    elif preset_id == "debt":
        result = T.TOOLS["debt_load"](STORE, pid6)
        add(result)
        series = result["data"].get("series") or []
        if len(series) > 1:
            blocks.append({"kind": "table", "rows": [
                {"year": year, "outstanding debt": fmt.money(value)}
                for year, value in series]})
    elif preset_id == "salient":
        add(T.find_salient(STORE, pid6))
    elif preset_id == "governance":
        add(T.get_offices(STORE, pid6))
        result = results[0]
        # The seat count says a body has seven members. Only the boundary says
        # which of the seven is the reader's, and that is the actionable half.
        seat_map = T.render_office_map(STORE, pid6)
        results.append(seat_map)
        drawn = seat_map["data"]
        if drawn.get("svg"):
            blocks.append({"kind": "text", "text": _ballot_line(drawn)})
            blocks.append({"kind": "map", "svg": drawn["svg"],
                           "coverage": 1.0 if drawn["districts"] else None})
        elif drawn.get("ballot") in ("by_district", "mixed"):
            blocks.append({"kind": "text", "text": (
                f"These {fmt.plural(drawn['seat_count'], 'seat')} are filled by "
                "district, so a voter fills one of them rather than all. No boundary "
                "for those districts is in this data — district assignments are "
                "recorded per polygon for Multnomah County only — so the map is "
                "missing rather than drawn as one electorate.")})

        if result["data"].get("roles"):
            blocks.append({"kind": "table", "rows": [
                {"role": r["role"], "seats": r["seat_count"],
                 "filled by": (r["filled_by"] or "not recorded").lower(),
                 "term": (f"{r['term_length']} years"
                          if (r.get("term_length") or "").isdigit() else "not recorded"),
                 # "N/A" is what the source writes for a seat nobody votes on.
                 # Printed as-is it reads like a gap in the data rather than the
                 # consequence of the column beside it.
                 "ballot": ("no ballot — appointed"
                            if r["filled_by"] == "Appointment"
                            else (r["partisan"] or "not recorded"))}
                for r in result["data"]["roles"]]})
    elif preset_id == "serving":
        result = T.TOOLS["governments_serving"](STORE, pid6)
        add(result)
        data = result["data"]
        if data.get("serving"):
            blocks.append({"kind": "table", "rows": [
                {"government": row["name"],
                 "type": row["gov_type_name"],
                 "established by": row["basis_label"]}
                for row in data["serving"]]})
            # The locator is the picture of the question. A reader who asked
            # where they live gets to see where that is before they are handed
            # a list of bodies with claims on it.
            located = T.render_locator_map(STORE, pid6)
            results.append(located)
            if located["data"].get("svg"):
                blocks.append({"kind": "map", "svg": located["data"]["svg"],
                               "coverage": None})
    elif preset_id == "map_community":
        import community as C
        # Two grains in one answer, because the second is the argument for the
        # first. Poverty maps at county level and refuses at place level, and a
        # reader shown only the county map would reasonably ask why it is not
        # finer. The refusal answers that in the survey's own terms.
        blocks.append({"kind": "text", "text": (
            "How Oregon's counties are doing, from the American Community Survey. "
            "This describes populations, not governments: no county on this map is "
            "being credited or blamed for where it sits, and this is deliberately "
            "not drawn on the same axis as a spending map.")})
        for code in ("DP03_0128P", "DP03_0062"):
            drawn = T.TOOLS["render_community_map"](
                STORE, code=code, vintage=C.VINTAGES[0], geo_level="county")
            results.append(drawn)
            data = drawn["data"]
            if data.get("svg"):
                polygons = data.get("polygons") or 0
                blocks.append({"kind": "text", "text": (
                    f"{data['name']}, {data['vintage'] - 4} to {data['vintage']}. "
                    f"{data['shown']} counties shown"
                    + (f", {data['withheld']} withheld because the margin of error is "
                       "wider than the survey can place on a scale."
                       if data["withheld"] else ", all with usable margins."))})
                blocks.append({"kind": "map", "svg": data["svg"],
                               "coverage": (data.get("covered", 0) / polygons)
                               if polygons else None})
        # The same indicator one grain finer, which the tool refuses. Shown
        # rather than hidden: a limit a reader can see is a limit they can trust.
        finer = T.TOOLS["render_community_map"](
            STORE, code="DP03_0128P", vintage=C.VINTAGES[0], geo_level="place")
        results.append(finer)
        thin = next((c for c in finer["caveats"] if c["code"] == "too_thin_to_map"), None)
        if thin:
            blocks.append({"kind": "text", "text": (
                "The same measure town by town is refused. " + thin["guidance"])})
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
            composed.append(B.figure(
                data["function"], fmt.money(data.get("total")),
                marks=marks, year=data.get("year"),
                basis="operating plus capital"))
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
                               "presets": preset_shell(),
                               "shared": sorted(ENTITY_INDEPENDENT),
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

        if parsed.path == "/api/browse":
            import browse_data
            return self._json(browse_data.build(STORE))

        if parsed.path == "/api/service_areas":
            # Ordered by how many governments report them, so the picker opens on
            # the areas a comparison will actually find data in.
            return self._json({"areas": [r["service_area"] for r in STORE.rows(
                "SELECT service_area, COUNT(*) AS n FROM spending_by_service_area "
                "WHERE total > 0 GROUP BY service_area ORDER BY n DESC")]})

        if parsed.path == "/api/presets":
            pid6 = (query.get("pid6") or [None])[0]
            available = availability(pid6) if pid6 else {}
            # Carrying the magnitude means a government reached through a door
            # rather than through the search box arrives with the same labelled
            # figure beside its name — students for a district, residents for a
            # city — instead of a bare type and county.
            entity = _with_magnitude(T._entity(STORE, pid6)) if pid6 else None
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

        if parsed.path == "/api/function":
            name = (payload.get("name") or "").strip()
            if not name:
                return self._json({"error": "name required"}, 400)
            return self._json(answer_function(name))

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
