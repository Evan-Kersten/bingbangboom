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
from rules import (TOPIC_ALIASES, TOPIC_CONCORDANCE, TOPIC_GROUPS, caveat,
                   not_computable)

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
    """What to ask for next, and who holds it.

    A scoping pass ends by naming what would have to be collected, and that is
    what this is: the categories are a Census classification, so the programme
    names, the mandates and the year-over-year explanations all sit in
    documents this data does not contain. Pointing at the body that holds each
    one is the difference between a limit and a next step. §12's "note who is
    missing" and §9's "responsibility is distributed" both end here rather than
    in a caveat nobody reads.
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
            # What the typed word was read as, and what else it could have been.
            # A reader who typed "fire" and got structural fire needs to see
            # both, or the answer silently decides which subject they meant.
            "matched": mapped["data"].get("matched"),
            "also": mapped["data"].get("also") or [],
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


def topic_index():
    """The subject list and the words that reach it, for the page's own matcher.

    Sent at runtime for the reason the preset list is: the browser matching a
    typed subject against its own copy of the vocabulary is the same drift that
    shipped a question the exported interface never offered. The page filters
    this rather than defining it, so an alias added to the concordance is
    reachable in the box without a second edit.
    """
    # Fit rides with the list rather than waiting for the answer. "Is this
    # measurable at all" is the first question in a prototyping session, and a
    # reader who can see that technology is a weak proxy before clicking has
    # been told the most useful thing this data has to say about it.
    grouped = [{"name": name, "topics": [t for t in names if t in TOPIC_CONCORDANCE]}
               for name, names in TOPIC_GROUPS]
    placed = {t for group in grouped for t in group["topics"]}
    missing = [t for t in topics() if t not in placed]
    if missing:
        grouped.append({"name": "Other", "topics": missing})
    return {"topics": topics(),
            "groups": grouped,
            "aliases": {alias: list(names)
                        for alias, names in sorted(TOPIC_ALIASES.items())},
            "fit": {name: TOPIC_CONCORDANCE[name][1] for name in TOPIC_CONCORDANCE}}


# How loosely the proxy fits, said in proportion to how much it matters. A good
# fit needs six words; a weak one is the most important thing on the page and
# gets a sentence of its own. The old opening spent three sentences on this
# regardless, so every brief in the build began the same way and the reader
# learned to skip the first forty words.
# Below this share of a government's own budget, a body is not "organised
# around" a subject however far ahead of the rest of its stack it sits. Set
# where it is because the ratio test alone passed Deschutes County at 5.9% on
# housing, which is a county doing a normal amount of a normal thing.
LEAD_SHARE_FLOOR = 10.0

FIT_OPENER = {
    "good": "which fit it closely",
    "moderate": "which contain it along with a good deal else",
    "weak": "which are a loose stand-in: much of the work on this sits outside "
            "them, and much of what is inside them is something else",
    "none": "and none of them corresponds to it",
}


def _shares(data):
    """The stack ranked by how much of its own budget each body puts here.

    Share, never dollars. A stack ranked by spending is a stack ranked by which
    government is biggest, which is almost always the county and says nothing
    about who holds the subject; the share of a body's own budget is the closest
    this data comes to a statement about emphasis, and it is the number that
    separates a government organised around a subject from one that touches it.
    """
    ranked = [(row, sum(r.get("percentage") or 0 for r in row["reports"]))
              for row in data["stack"] if row.get("reports")]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


def narrative(data):
    """The brief's opening, as three sentences that are actually about a place.

    Every brief in this build used to begin with the same paragraph about Census
    categories with the nouns swapped, and the first sentence that differed
    between Estacada and Portland was forty words down. A reader who has opened
    two of them has learnt to skip the top of the third, which is a problem
    because the top of the third is where §5 puts the scope limit.

    So the shape here is: a claim, then the gap that qualifies it, then the
    finding underneath it, then whatever is strange about this particular place.
    The claim carries no figure at all — §12 forbids a topic and an amount in one
    breath, and a headline is exactly where a reader would carry one away — so it
    is a statement about *who*, which is what this data can support and what
    somebody scoping a subject actually needs.

    Returns a dict rather than a string because the parts are rendered at
    different weights and the caller decides which survive.
    """
    place, stack = data["place"], data["stack"]
    ranked = _shares(data)
    silent = [row for row in stack if not row.get("reports")]
    elsewhere = data.get("elsewhere") or []
    topic = data["topic"]

    # ---- nobody in the stack reports anything -------------------------------
    if not ranked and elsewhere:
        return {
            "headline": f"Whoever does this work in {place} files somewhere else.",
            "finding": (
                f"Not one of the {fmt.count(len(stack))} governments established to "
                f"serve {place} reports anything in these categories. "
                f"{fmt.count(len(elsewhere))} special "
                f"{_plural(len(elsewhere), 'district')} filed under this county "
                f"{_verb(len(elsewhere))}, and not one of those carries a boundary "
                "anywhere in this data."),
            "texture": (
                "That combination — the responsibility outside the place and the "
                "bodies holding it unmappable — is the ordinary case in rural "
                f"Oregon, not a gap in the filing. It means the answer to who does "
                f"{topic} here is a phone call, and this data can tell you which "
                "number to try."),
        }
    if not ranked:
        return {
            "headline": f"Nobody serving {place} files this under a name you can look up.",
            "finding": (
                f"All {fmt.count(len(stack))} governments established to serve "
                f"{place} report nothing in these categories."),
            "texture": (
                "A row of zeroes here is almost never a place where nothing "
                "happens. It is a place where the work sits with a body that "
                "files under a different county, a nonprofit under contract, or "
                "a state agency — none of which appear in this data at all."),
        }

    top, top_share = ranked[0]
    runner, runner_share = (ranked[1] if len(ranked) > 1 else (None, 0.0))

    # More bodies reporting from outside the stack than inside it. This is the
    # most useful thing that can be true of a brief and it was previously buried
    # under whichever pair happened to rank first: eight rural fire districts
    # filed under Clackamas are the answer to wildfire around Estacada, and the
    # city and the county both reporting something is the distraction.
    if len(elsewhere) > len(ranked):
        # Named by what they are and by the Census category they file under,
        # never by the topic. "Eight fire districts do this" would assert that
        # fire districts do wildfire work, which is the §12 slide from category
        # to subject, and the headline sits above the sentence that explains the
        # difference — so it has to stand on its own without it.
        dominant = _dominant(elsewhere)
        if dominant:
            count, kind, category = dominant
            headline = (f"{fmt.count(count)} {kind} file {category} spending near "
                        f"{place}. Not one is on {place}'s own list.")
        else:
            headline = f"The bodies doing most of this near {place} are not on its list."
        return {
            "headline": headline,
            # The headline has already counted the outside bodies. This turns to
            # the list itself, which is the part a reader can act on, rather
            # than restating the same number with different nouns.
            "finding": (
                f"On {place}'s own list, {fmt.count(len(ranked))} of the "
                f"{fmt.count(len(stack))} governments established to serve it "
                f"{'reports' if len(ranked) == 1 else 'report'} anything in these "
                f"categories, and {top['name']} leads them at "
                f"{fmt.percent(top_share)} of its own budget. That list is short "
                "because the boundary files are, not because the place is."),
            "texture": _texture(data, top, runner, silent, elsewhere),
        }

    # ---- one body carries it and the obvious candidate does not -------------
    # The interesting case, and the common one: somebody scoping a subject in a
    # city assumes the city holds it, and in Oregon the county usually does.
    if runner is not None and top_share >= 2 * (runner_share or 0):
        # A ratio is not the same as a presence. Deschutes County at 5.9% against
        # Bend at 2.2% clears the same 2x test as Multnomah at 47% against
        # Portland at 8.4%, and calling the first one a government built around
        # housing would be false in the way a reader could not check. The
        # threshold is a floor on the leader's own budget, not on the gap.
        substantial = top_share >= LEAD_SHARE_FLOOR
        both_here = top["relation"] == "county" and runner["relation"] == "itself"
        if substantial:
            headline = (f"On paper this belongs to {top['name']}, not to {place}."
                        if both_here
                        else f"This is {top['name']}'s work, not {runner['name']}'s.")
            closing = ("Two governments serving the same streets, and only one of "
                       "them is built around this."
                       if both_here else
                       "One of these is organised around this and the other is not.")
        else:
            headline = f"Nobody serving {place} is built around this."
            closing = ("Neither of those is a government organised around this "
                       "subject. The larger share is still small, and a reader "
                       "looking for the body that leads on it will not find one "
                       "in this stack.")
        return {
            "headline": headline,
            "finding": (
                f"{top['name']} puts {fmt.percent(top_share)} of its own budget into "
                f"these categories. {runner['name']} puts {fmt.percent(runner_share)} "
                f"of its. {closing}"),
            "texture": _texture(data, top, runner, silent, elsewhere),
        }

    # ---- exactly one body reports ------------------------------------------
    # Ahead of the mostly-silent branch on purpose. "Most of them have nothing
    # to do with this" is true of Burns and buries the fact worth having, which
    # is that Harney County puts 39% of its budget here and is the only body
    # that reports at all.
    if runner is None:
        return {
            "headline": f"{top['name']} is the only body here that reports this.",
            "finding": (
                f"Of the {fmt.count(len(stack))} governments established to serve "
                f"{place}, {top['name']} is the one that reports anything in these "
                f"categories, at {fmt.percent(top_share)} of its own budget. The "
                f"other {fmt.count(len(silent))} report nothing."
                if silent else
                f"{top['name']} is the only government established to serve {place} "
                f"in this data, and it puts {fmt.percent(top_share)} of its own "
                "budget into these categories."),
            "texture": _texture(data, top, runner, silent, elsewhere),
        }

    # ---- most of the stack is silent ---------------------------------------
    if len(silent) >= 2 * len(ranked):
        return {
            "headline": f"Most of the governments serving {place} have nothing to do "
                        "with this.",
            "finding": (
                f"{fmt.count(len(ranked))} of the {fmt.count(len(stack))} bodies "
                f"established to serve {place} "
                f"{'reports' if len(ranked) == 1 else 'report'} anything in these "
                "categories; "
                f"the other {fmt.count(len(silent))} report nothing. "
                f"{top['name']} carries the largest share of its own budget here, at "
                f"{fmt.percent(top_share)}."),
            "texture": _texture(data, top, runner, silent, elsewhere),
        }

    # ---- shared, and shared unevenly ---------------------------------------
    if runner is not None:
        return {
            "headline": f"Two governments split this in {place}, and not evenly.",
            "finding": (
                f"{top['name']} puts {fmt.percent(top_share)} of its own budget into "
                f"these categories and {runner['name']} {fmt.percent(runner_share)} "
                "of its. Neither is a lead agency and both have a claim, which is "
                "the arrangement that produces two answers to the same question."),
            "texture": _texture(data, top, runner, silent, elsewhere),
        }

    return {
        "headline": f"One government carries this in {place}.",
        "finding": (
            f"{top['name']} is the only body established to serve {place} that "
            f"reports anything in these categories, at {fmt.percent(top_share)} of "
            "its own budget."),
        "texture": _texture(data, top, runner, silent, elsewhere),
    }


def _texture(data, top, runner, silent, elsewhere):
    """The one thing about this place that is not true of the next place.

    Ranked by what changes a reader's next move, and every branch is a fact this
    data measured rather than a flourish. Where nothing here is unusual it
    returns None and the brief is three sentences shorter, which is the correct
    outcome: padding an answer to seem thorough is the failure §3 names.
    """
    place = data["place"]

    # A district filing under the county and unplaceable is the most consequential
    # thing that can be true of a stack, because it is money in scope that cannot
    # be attributed and cannot be ruled out either.
    if elsewhere:
        names = ", ".join(row["name"] for row in elsewhere[:2])
        more = (f" and {fmt.count(len(elsewhere) - 2)} others"
                if len(elsewhere) > 2 else "")
        one = len(elsewhere) == 1
        return (f"{names}{more} also {'reports' if one else 'report'} in these "
                f"categories and {'is' if one else 'are'} filed under this county "
                f"rather than against {place}. Whether that money lands here is not "
                "something this data can settle either way — the "
                f"{'district has' if one else 'districts have'} no boundary in it, "
                "and host-county filing follows assessed value rather than service "
                "area.")

    # A body that is in the stack and reports nothing is a signal, not a hole:
    # it is usually the clearest evidence available that somebody else holds the
    # responsibility, and readers consistently read it as absence of effort.
    if silent:
        kinds = sorted({row["gov_type_name"] for row in silent})
        who = (silent[0]["name"] if len(silent) == 1
               else f"{fmt.count(len(silent))} of them, all {kinds[0].lower()}s,"
               if len(kinds) == 1 else f"{fmt.count(len(silent))} of them")
        return (f"{who} report nothing here at all. In this data that is nearly "
                "always a statement about which government holds the "
                "responsibility, not about how much of it there is.")

    # §13.3, and only where it applies. Oregon's delegation covers health,
    # corrections and veterans' services; attaching it to a county's broadband
    # or transport share was the rule firing on the government type rather than
    # on the categories, which is a true sentence about the wrong number.
    if (runner is not None and top["relation"] == "county"
            and set(data["categories"]) & DELEGATED_CATEGORIES):
        return (f"{top['name']} is a county, and Oregon counties run health, "
                "corrections and veterans' services for the state rather than by "
                "local choice (§13.3). A large share of its budget in these "
                "categories is partly a mandate showing up as a number, and the "
                "budget message is where the two are separated.")
    return None


# The service areas Oregon delegates to its counties. §13.3 attaches to these
# and to nothing else.
DELEGATED_CATEGORIES = {"Health & Human Services", "Public Safety"}


# What a special district's name says it is. Oregon names these bodies after the
# work — Rural Fire Protection District, Soil and Water Conservation District —
# and that word is the most useful thing a headline can carry: "eight fire
# districts do this around Estacada" is a finding, and "the bodies doing most of
# this are not on its list" is a sentence that fits every place in the state.
# Ordered so a longer phrase wins over a word it contains.
DISTRICT_KINDS = (
    ("soil and water conservation", "soil and water districts"),
    # Rural Fire Protection District and Fire District are one family here. Kept
    # apart, a stack holding both split the count and neither reached a
    # majority, so the headline fell back to the generic sentence for exactly
    # the case the naming exists to serve.
    ("rural fire protection", "fire districts"),
    ("fire protection", "fire districts"),
    ("park and recreation", "parks districts"),
    ("people's utility", "public utility districts"),
    ("urban renewal", "urban renewal agencies"),
    ("housing authority", "housing authorities"),
    ("water control", "water control districts"),
    ("vector control", "vector control districts"),
    ("ambulance", "ambulance districts"),
    ("cemetery", "cemetery districts"),
    ("irrigation", "irrigation districts"),
    ("hospital", "hospital districts"),
    ("library", "library districts"),
    ("sanitary", "sanitary districts"),
    ("transit", "transit districts"),
    ("health", "health districts"),
    ("school", "school districts"),
    ("water", "water districts"),
    ("sewer", "sewer districts"),
    ("port of", "ports"),
    ("fire", "fire districts"),
    ("road", "road districts"),
    ("drainage", "drainage districts"),
)


def _dominant(rows):
    """The largest group of these bodies that shares both a kind and a category.

    Grouped on the pair, not on either half. Adrian's eight off-stack bodies are
    six irrigation districts filing Environment & Natural Resources and one fire
    district filing Public Safety; taking the commonest kind and the brief's
    first category independently produced "eight irrigation districts file
    Public Safety spending", which is a true-sounding sentence that seven of its
    eight subjects contradict.

    Requires a clear majority of everything off the stack rather than a
    plurality, because a headline that describes less than half its own subject
    is worse than the general sentence it replaces.
    """
    if not rows:
        return None
    groups = {}
    for row in rows:
        name = (row.get("name") or "").lower()
        kind = next((label for needle, label in DISTRICT_KINDS if needle in name), None)
        if not kind or not row.get("service_area"):
            continue
        groups.setdefault((kind, row["service_area"]), set()).add(row.get("name"))
    if not groups:
        return None
    (kind, area), members = max(groups.items(), key=lambda pair: len(pair[1]))
    distinct = len({row.get("name") for row in rows})
    return (len(members), kind, area) if len(members) > distinct / 2 else None


def _district_kind(rows):
    """The kind alone, where every row shares one. Kept for callers that have no
    category to pair it with; `_dominant` is what the headline uses."""
    found = _dominant([{**row, "service_area": "x"} for row in rows])
    return found[1] if found else None


def _plural(count, word):
    return word if count == 1 else word + "s"


def _verb(count):
    return "does" if count == 1 else "do"


def lead(data):
    """The finding sentence on its own, for callers that want one line."""
    return narrative(data)["finding"]
