"""The brief: one place, one issue, and who would have to act.

This is the answer to the question people actually arrive with. Nobody opens a
budget tool wanting a capital share. They arrive because they are working on
housing in Milwaukie, or fire risk in Estacada, and the first thing they need is
not a figure at all: it is the list of bodies with a claim on that place and
that subject, and an honest account of what this data can and cannot say about
them.

Section 12 is the reason this module exists and the reason it is shaped the way
it is. Users ask about issues; the data records Census functional categories;
the two do not correspond. §12 forbids stating or implying a dollar figure for
homelessness, wildfire, mental health or any other issue-level topic, and it
prescribes the move in order: name the gap, offer the proxy and say how loose
the fit is, then give the real figures for the real categories, clearly
labelled. It also says a bare refusal is worse than a labelled approximation.

So the gap statement is not a footnote here. It is section one, before any
government is named and long before any dollar figure, and the fit rating rides
next to every number downstream of it.

§5's precedence gives the rest of the order: scope limits first, ecosystem
membership second, proxy disclosure third, figures last. The sections below are
in exactly that order, and `sections()` returns them that way rather than
leaving the caller to sort.

**What this module will not do.** It will not total the governments in the
stack. §9: intergovernmental transfers mean the same dollar appears in two
entities' expenditures, so a sum across a place is a number with no referent,
and the fact that a reader would like one is not evidence that it exists. It
will not put a condition measure and a spending figure in the same row, because
that is the §4 causation error in table form. And it will not rank the bodies by
spending, because the largest budget in a stack is usually the county and that
says nothing about who holds the responsibility being asked about.
"""

import format as fmt
import tools as T
from rules import TOPIC_CONCORDANCE, caveat, not_computable

# How loose the fit is, in a reader's words rather than a rating scale. §12
# gives the ratings; this says what each one means for the answer that follows,
# because "moderate" alone tells a reader nothing about how far to trust it.
FIT_MEANING = {
    "good": "The category lines up with the topic closely enough to reason from, "
            "though it still contains more than the topic does.",
    "moderate": "The category contains the topic and a good deal besides. Treat "
                "the figures as an upper bound on the subject, not a measure of it.",
    "weak": "The category is a loose stand-in. Much of the work on this topic sits "
            "outside it, and much of what is inside the category is something else.",
    "none": "No category in this data corresponds to this topic. Nothing below is a "
            "measure of it.",
}


def _reported(store, pid6, categories):
    """What one government reports in the proxy categories, per category.

    Per category and never summed, because two categories standing in for one
    topic overlap in unknown proportion, and adding them would invent a
    precision the concordance explicitly denies.
    """
    if not categories:
        return []
    marks = ",".join("?" for _ in categories)
    return [dict(r) for r in store.rows(
        f"SELECT service_area, total, percentage, year "
        f"FROM spending_by_service_area WHERE pid6=? AND service_area IN ({marks}) "
        f"AND total > 0 ORDER BY total DESC", pid6, *categories)]


def _questions(store, place, stack, categories):
    """What to ask, and who to ask it of.

    The output of this product is usually a conversation rather than a figure.
    A facilitator standing in front of a council does not need another number;
    they need the question that the number cannot answer, pointed at the body
    that can. §12's "note who is missing" and §9's "responsibility is
    distributed" both end here rather than in a caveat nobody reads aloud.
    """
    asks = []
    silent = [row for row in stack if not row.get("reports")]
    if silent:
        names = ", ".join(row["name"] for row in silent[:3])
        asks.append(f"Ask {names} which body handles this here. Reporting nothing in "
                    "these categories usually means another government holds the "
                    "responsibility, not that nobody does.")
    if categories:
        asks.append(f"Ask what sits inside {categories[0]} in this year's budget. The "
                    "category is a Census classification, so the programme names are "
                    "in the budget document and not in this data.")
    county = next((r for r in stack if r["gov_type_name"] == "County"), None)
    if county:
        asks.append(f"Ask {county['name']} what it delivers here under state mandate "
                    "rather than local choice. Oregon counties run health, corrections "
                    "and veterans' services for the state, so those budgets are not "
                    "county priorities (§13.3).")
    asks.append("Ask for the budget message. Oregon local budget law requires one for "
                "essentially every government here, and it explains year-over-year "
                "changes that this data can only show (§13.9).")
    return asks


def place_topic_brief(store, pid6, topic):
    """One place, one issue: who has a claim, and what can honestly be said.

    Returns the sections in §5 precedence order. The caller renders them; it
    does not get to reorder them, because the order is the rule.
    """
    place = T._entity(store, pid6)
    if not place:
        return T.envelope("place_topic_brief", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "That place is not in the data."}])

    # ---- 1. the gap, before anything else (§12, §5 precedence) -----------
    mapped = T.map_topic_to_categories(store, topic)
    categories = mapped["data"]["categories"]
    fit = mapped["data"]["fit"]
    caveats = list(mapped["caveats"])
    blocked = list(mapped["not_computable"])

    # ---- 2. ecosystem membership (§9) ------------------------------------
    serving = T.TOOLS["governments_serving"](store, pid6)
    stack = list(serving["data"].get("serving") or [])
    for code in serving["caveats"]:
        if code["code"] not in {c["code"] for c in caveats}:
            caveats.append(code)

    # ---- 3 and 4. proxy disclosure, then figures -------------------------
    for row in stack:
        row["reports"] = _reported(store, row["pid6"], categories)

    # A government outside the stack may still do the work here. §9 is explicit
    # that absence from the table is not absence from the ecosystem, and for
    # exactly the topics people ask about — fire, transit, water — the body
    # doing it is a district with no boundary and so no way into the stack.
    elsewhere = []
    if categories:
        host = place["host_county_pid6"]
        known = {row["pid6"] for row in stack}
        for row in store.rows(
                "SELECT e.pid6, COALESCE(e.common_name, e.legal_name) AS name, "
                "       e.gov_type_name, s.service_area, s.total "
                "FROM spending_by_service_area s JOIN entities e ON e.pid6 = s.pid6 "
                "WHERE e.host_county_pid6 = ? AND s.service_area IN ("
                + ",".join("?" for _ in categories) + ") AND s.total > 0 "
                "  AND e.gov_type_name = 'Special District' "
                "ORDER BY s.total DESC LIMIT 8", host, *categories):
            if row["pid6"] not in known:
                elsewhere.append(dict(row))
        if elsewhere:
            caveats.append({
                "code": "districts_filed_not_placed", "rule": "§9",
                "guidance": f"{len(elsewhere)} special districts filed under this county "
                            "report spending in these categories and have no boundary in "
                            "this data, so they cannot be tied to this place or ruled out "
                            "of it. They are listed as filed under the county, which is "
                            "where the money sits and not necessarily where the service "
                            "goes (§13.11)."})

    # ---- conditions, kept apart from every figure above (§4, §10) --------
    conditions = T.TOOLS["community_context"](store, pid6)
    indicators = conditions["data"].get("indicators") or []
    if indicators:
        caveats.append({
            "code": "conditions_are_not_a_record", "rule": "§4",
            "guidance": "These describe the population the governments above operate in. "
                        "They are not those governments' results and were not produced by "
                        "the spending beside them. Never place the two on one axis, and "
                        "never say one explains the other."})

    reporting = [row for row in stack if row["reports"]]
    return T.envelope(
        "place_topic_brief",
        entity=place,
        data={
            "place": place["common_name"] or place["legal_name"],
            "topic": topic,
            "categories": categories,
            "fit": fit,
            "fit_meaning": FIT_MEANING.get(fit, FIT_MEANING["none"]),
            "concordance_note": mapped["data"].get("note"),
            "stack": stack,
            "reporting": len(reporting),
            "silent": len(stack) - len(reporting),
            "elsewhere": elsewhere,
            "conditions": indicators,
            "conditions_basis": conditions["data"].get("basis"),
            "questions": _questions(store, place, stack, categories),
        },
        caveats=caveats,
        blocked=blocked + [not_computable("outcome_or_performance")],
        vintage={"finance": next((r["reports"][0]["year"] for r in reporting), None)})


def topics():
    """The issues this can be asked about, in the reader's words.

    Sorted rather than ranked. A list ordered by fit would put the topics the
    data handles well at the top, which is the opposite of useful: the ones it
    handles badly are the ones a reader most needs to be warned about before
    they ask.
    """
    return sorted(TOPIC_CONCORDANCE)
