"""The conditions of the place a government serves.

This is deliberately adjacent to the fiscal data and never joined to it. A city
that spends $3,582 per resident sits in a place with a median household income;
both are facts, and the interface puts them side by side and stops. It does not
correlate them, rank places by the pair, or order the presentation so that one
reads as the cause of the other. §4 is explicit that this data measures inputs —
what governments spend and who they employ — and DP03 measures a population, so
a line drawn between them would be an assertion neither source supports.

Three things about ACS that decide what may be drawn:

Each vintage is in its own dollars. The variable labels say so outright: the
2019 release is "in 2019 inflation-adjusted dollars", the 2023 release in 2023
dollars. A median income series across the three is therefore not a real-terms
series, and the change between two of them is not a real change.

The windows overlap. A 5-year release covers the five years ending in its name,
so 2019 covers 2015-2019 and 2023 covers 2019-2023: they share a year, and 2024
shares four with 2023. Change between vintages is not period-over-period.

And the estimates carry margins of error that are, at place level, frequently
larger than the differences a map would invite a reader to see. For median
household income the median margin is 22% of the estimate and one place in eight
has a margin wider than half its value. A choropleth of 240 towns on that is
substantially a picture of sampling noise, so every figure here comes with its
margin and a flag saying whether it can carry weight.
"""

import math

import format as fmt
import tools as T
from rules import caveat, not_computable

# Vintages present, newest first. 2019 is the pre-pandemic reference and is
# named as such rather than treated as simply the oldest.
VINTAGES = (2024, 2023, 2019)
PRE_PANDEMIC = 2019

# The indicators worth showing, with the plain name first. Percent variables use
# the P suffix: the bare code holds a count and is empty at place level, which is
# why an earlier pass found "no data" for unemployment in every Oregon town.
#
#   code, plain name, unit, whether more is worse
INDICATORS = [
    ("DP03_0062", "Median household income", "dollars", False),
    ("DP03_0088", "Income per person", "dollars", False),
    ("DP03_0128P", "People below the poverty level", "percent", True),
    ("DP03_0009P", "Unemployment rate", "percent", True),
    ("DP03_0099P", "People without health insurance", "percent", True),
    ("DP03_0025", "Average commute to work", "minutes", True),
]
BY_CODE = {code: (name, unit, worse) for code, name, unit, worse in INDICATORS}

# A margin this wide against the estimate means the figure cannot carry the
# weight a map or a comparison would put on it. Not a hard error — the estimate
# is still the best available — but it is reported as soft and never binned.
MARGIN_UNRELIABLE = 0.30

# Dollar figures are in the dollars of their own vintage, so these two are the
# ones a cross-vintage comparison must refuse outright rather than caveat.
DOLLAR_UNITS = {"dollars"}

# Below this share of geographies surviving the margin test, a choropleth is not
# a thin map, it is a different picture entirely. Measured statewide at place
# level, the six indicators split hard:
#
#     income per person       76% usable        commute            72%
#     median household income 65%               poverty            13%
#     uninsured                9%               unemployment        5%
#
# An unemployment map of Oregon's towns would colour twenty of four hundred and
# twenty and leave the rest grey, and a reader would read the twenty as the
# pattern. Counties survive everything at 67% and up, so the refusal names them
# as the grain that works rather than leaving the reader with nothing.
MAP_USABLE_FLOOR = 0.5


def _geo_id_for(store, pid6):
    """The DP03 geography this government's own boundary corresponds to.

    Counties and cities are Census geographies and join directly. A school or
    special district is not, so it has no DP03 record of its own and is placed
    through the county it sits in, which is stated rather than implied.
    """
    row = store.row(
        "SELECT g.layer, g.geo_id, e.gov_type_name, e.host_county_pid6 "
        "FROM geo_entity g JOIN entities e ON e.pid6 = g.pid6 WHERE g.pid6=?", pid6)
    if row and row["layer"] == "county":
        return row["geo_id"], "county", "own"
    if row and row["layer"] == "place":
        return row["geo_id"], "place", "own"

    entity = T._entity(store, pid6)
    host = (entity or {}).get("host_county_pid6")
    if host:
        county = store.row(
            "SELECT geo_id FROM geo_entity WHERE pid6=? AND layer='county'", host)
        if county:
            return county["geo_id"], "county", "host_county"
    return None, None, None


def reliability(estimate, moe):
    """Whether a figure can carry the weight a comparison would put on it."""
    if estimate is None:
        return "absent"
    if moe is None:
        return "unstated"
    if not estimate:
        return "unstated"
    return "soft" if abs(moe / estimate) > MARGIN_UNRELIABLE else "firm"


def indicators(store, geo_id, geo_level, vintage):
    """Every tracked indicator for one geography in one release."""
    out = []
    for code, name, unit, worse in INDICATORS:
        row = store.row(
            "SELECT estimate, moe, geo_name FROM dp03 "
            "WHERE geo_id=? AND geo_level=? AND vintage=? AND variable=?",
            geo_id, geo_level, vintage, code)
        if not row:
            continue
        out.append({
            "code": code, "name": name, "unit": unit, "higher_is_worse": worse,
            "estimate": row["estimate"], "moe": row["moe"],
            "reliability": reliability(row["estimate"], row["moe"]),
            "geo_name": row["geo_name"],
        })
    return out


def format_value(value, unit):
    if value is None:
        return None
    if unit == "dollars":
        return fmt.money(value) if value >= 1000 else fmt.rate(value)
    if unit == "percent":
        return fmt.percent(value)
    if unit == "minutes":
        return f"{value:.0f} min"
    return fmt.count(value)


def community_context(store, pid6, vintage=VINTAGES[0]):
    """The place a government serves, described on its own terms.

    Returns the indicators beside nothing else. The caller places this next to
    the government's figures; this function never sees them, which is the
    simplest way to guarantee it cannot correlate them.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("community_context", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    geo_id, geo_level, basis = _geo_id_for(store, pid6)
    if not geo_id:
        return T.envelope("community_context", entity=entity, caveats=[{
            "code": "no_community_geography", "rule": "§9",
            "guidance": "This government has no Census geography and no host county "
                        "with one, so the place it serves cannot be described. Say the "
                        "context is missing rather than describing the state instead."}])

    rows = indicators(store, geo_id, geo_level, vintage)
    if not rows:
        return T.envelope("community_context", entity=entity, caveats=[{
            "code": "no_community_data", "rule": "§9",
            "guidance": f"No {vintage} community estimates cover this geography."}])

    name = entity["common_name"] or entity["legal_name"]
    soft = [r["name"] for r in rows if r["reliability"] == "soft"]

    caveats = [
        {"code": "conditions_are_not_outcomes", "rule": "§4",
         "guidance": "These describe the population of a place, not the performance of "
                     "the government that serves it. Report them beside the fiscal "
                     "figures and never as a result of them: nothing in either source "
                     "says a budget produced a poverty rate or that a poverty rate "
                     "produced a budget. Do not order the sentence so that one reads "
                     "as the cause of the other."},
        {"code": "survey_not_census", "rule": "§8",
         "guidance": f"These are American Community Survey five-year estimates for the "
                     f"{vintage} release, which describes {vintage - 4} to {vintage} "
                     "rather than a single year. They are a survey with a sample, not a "
                     "count."},
    ]
    if basis == "host_county":
        caveats.append({
            "code": "context_is_the_county", "rule": "§12",
            "guidance": f"{name} is not a Census geography, so the conditions shown are "
                        "for the county it sits in, not for the area it serves. A "
                        "district covering part of a county, or several, is described "
                        "here by the whole county. Say so before any figure."})
    if soft:
        caveats.append({
            "code": "margin_too_wide", "rule": "§8",
            "guidance": f"{len(soft)} of these estimates have a margin of error wider "
                        f"than {MARGIN_UNRELIABLE:.0%} of the estimate: "
                        f"{', '.join(soft[:4])}. Report them with the margin or not at "
                        "all, and never rank a place on one. A small town's estimate "
                        "can move by half its own value and still be the same sample."})

    return T.envelope(
        "community_context", entity=entity,
        data={"geo_id": geo_id, "geo_level": geo_level, "basis": basis,
              "vintage": vintage, "place_name": rows[0]["geo_name"],
              "indicators": rows,
              "firm": sum(1 for r in rows if r["reliability"] == "firm"),
              "soft": len(soft)},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")],
        vintage={"community": vintage})


def since_pandemic(store, pid6, code="DP03_0128P", vintage=VINTAGES[0]):
    """One indicator at the pre-pandemic release and the latest one.

    Two releases, named, never a line between them. The windows overlap and each
    is a five-year average, so what this shows is two overlapping periods and not
    a change measured at two moments. Dollar figures are refused outright: each
    release is in its own dollars and the difference between them is part
    inflation and part everything else, with nothing here to separate the two.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("since_pandemic", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    name, unit, worse = BY_CODE.get(code, (code, "count", False))
    if unit in DOLLAR_UNITS:
        return T.envelope("since_pandemic", entity=entity, caveats=[{
            "code": "vintage_dollars", "rule": "§10",
            "guidance": f"{name} is reported in each release's own dollars — the 2019 "
                        "figure in 2019 dollars, the latest in its own — so the "
                        "difference between them is part inflation and part change, and "
                        "no price index in this data can separate the two. Compare a "
                        "share or a rate instead, or report each release separately and "
                        "say which dollars each is in."}])

    geo_id, geo_level, basis = _geo_id_for(store, pid6)
    if not geo_id:
        return T.envelope("since_pandemic", entity=entity, caveats=[{
            "code": "no_community_geography", "rule": "§9",
            "guidance": "This government has no Census geography to describe."}])

    points = []
    for release in (PRE_PANDEMIC, vintage):
        row = store.row(
            "SELECT estimate, moe, geo_name FROM dp03 "
            "WHERE geo_id=? AND geo_level=? AND vintage=? AND variable=?",
            geo_id, geo_level, release, code)
        if row and row["estimate"] is not None:
            points.append({
                "vintage": release, "covers": f"{release - 4} to {release}",
                "estimate": row["estimate"], "moe": row["moe"],
                "reliability": reliability(row["estimate"], row["moe"])})

    if len(points) < 2:
        return T.envelope("since_pandemic", entity=entity, caveats=[{
            "code": "no_comparison_release", "rule": "§9",
            "guidance": f"{name} is not reported in both the {PRE_PANDEMIC} and "
                        f"{vintage} releases for this geography, so there is nothing to "
                        "set beside anything. That is an absence, not no change."}])

    first, last = points[0], points[1]
    difference = last["estimate"] - first["estimate"]

    # Census's own significance test for two ACS estimates, not a guess at one.
    # A margin of error at 90% confidence is 1.645 standard errors, so the
    # difference of two independent estimates is significant when it exceeds the
    # root sum of squares of their margins. Adding the margins instead — which
    # looks safer — is the wrong test and about 40% too strict at equal margins:
    # it hides Portland's poverty rate falling from 13.7% to 12.7%, which by the
    # documented method is a real difference.
    #
    # ACS General Handbook 2020, ch. 7, "Testing statistical significance".
    span = math.hypot(first["moe"] or 0, last["moe"] or 0)
    separated = abs(difference) > span

    caveats = [
        {"code": "overlapping_windows", "rule": "§8",
         "guidance": f"The {PRE_PANDEMIC} release covers {PRE_PANDEMIC - 4} to "
                     f"{PRE_PANDEMIC} and the {vintage} release covers {vintage - 4} to "
                     f"{vintage}. Those windows overlap, and each is a five-year "
                     "average, so this is two overlapping periods set beside each other "
                     "and not a change measured at two moments. Do not call it a trend "
                     "or attach a year to it."},
        {"code": "conditions_are_not_outcomes", "rule": "§4",
         "guidance": "A change in a population is not a result produced by the "
                     "government that serves it. Nothing here attributes one to the "
                     "other, and the answer must not either."},
    ]
    if not separated:
        caveats.append({
            "code": "within_the_margin", "rule": "§8",
            "guidance": f"The difference is {abs(difference):.1f}, inside the "
                        f"{span:.1f} the two margins of error allow. By the Census "
                        "significance test these estimates have not been shown to "
                        "differ at all. Say the survey cannot separate them rather than "
                        "reporting a change, and never state a direction."})
    if basis == "host_county":
        caveats.append({
            "code": "context_is_the_county", "rule": "§12",
            "guidance": "This government is not a Census geography, so these are county "
                        "figures standing in for the area it serves. Say so."})

    return T.envelope(
        "since_pandemic", entity=entity,
        data={"code": code, "name": name, "unit": unit, "higher_is_worse": worse,
              "geo_level": geo_level, "basis": basis, "points": points,
              "difference": difference, "separated_from_margin": separated,
              "margin_span": span},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")])


def map_values(store, code, vintage, geo_level, only=None):
    """Estimates keyed by geography, with the unreliable ones withheld.

    A soft estimate is not drawn. That is the whole argument for this function
    existing: binning a figure whose margin is wider than a third of itself puts
    a town in a colour band the survey cannot place it in, and a reader compares
    the bands. Withheld, it falls to the no-data fill, which the choropleth
    already renders as an absence rather than as a low value.
    """
    values, withheld = {}, []
    for row in store.rows(
            "SELECT geo_id, geo_name, estimate, moe FROM dp03 "
            "WHERE variable=? AND vintage=? AND geo_level=?", code, vintage, geo_level):
        if only is not None and row["geo_id"] not in only:
            continue
        state = reliability(row["estimate"], row["moe"])
        if state == "firm":
            values[row["geo_id"]] = row["estimate"]
        elif state != "absent":
            withheld.append(row["geo_name"])
    return values, withheld


def render_community_map(store, code="DP03_0128P", vintage=VINTAGES[0],
                         geo_level="county", county=None):
    """A condition of Oregon's places, mapped, with the survey's limits enforced.

    Never drawn beside a fiscal map on one axis and never with a fiscal figure in
    the same legend. This says what a place is like; a spending map says what a
    government spends. Putting them in one picture is the causal claim §4 forbids,
    and keeping them in separate pictures is how the interface declines to make it.
    """
    import maps

    name, unit, worse = BY_CODE.get(code, (code, "percent", True))
    if geo_level not in ("county", "place"):
        return T.envelope("render_community_map", caveats=[{
            "code": "unknown_level", "rule": "§4",
            "guidance": "Community estimates exist for counties and places only. There "
                        "are no tract-level rows in this data, so a finer map cannot be "
                        "drawn however fine the boundaries are."}])

    only = None
    scope = "Oregon"
    if geo_level == "place" and county:
        # host_county is the county's plain name on the entity itself. Joining
        # through the county entity and matching its common_name instead asks
        # for "Multnomah" against a row that reads "Multnomah County", and
        # returns nothing at all rather than failing.
        rows = store.rows(
            "SELECT g.geo_id FROM geo_entity g JOIN entities e ON e.pid6 = g.pid6 "
            "WHERE g.layer='place' AND UPPER(e.host_county)=?", county.upper())
        only = {r["geo_id"] for r in rows}
        scope = f"{county.title()} County"

    values, withheld = map_values(store, code, vintage, geo_level, only)
    total = len(values) + len(withheld)
    usable = (len(values) / total) if total else 0
    if not values:
        return T.envelope("render_community_map", caveats=[{
            "code": "nothing_reliable", "rule": "§8",
            "guidance": f"No {geo_level} estimate of {name} in the {vintage} release "
                        "has a margin narrow enough to place on a scale. The map is "
                        "withheld rather than drawn out of figures the survey cannot "
                        "separate."}])
    if usable < MAP_USABLE_FLOOR:
        return T.envelope(
            "render_community_map",
            data={"code": code, "name": name, "geo_level": geo_level, "svg": None,
                  "shown": len(values), "withheld": len(withheld), "usable": usable},
            caveats=[{
                "code": "too_thin_to_map", "rule": "§8",
                "guidance": f"Only {len(values)} of {total} {geo_level}s have a margin "
                            f"of error narrow enough to place {name} on a scale — "
                            f"{usable:.0%}. A map of that is a map of the few places "
                            "the survey happens to measure precisely, which a reader "
                            "would read as the pattern. Refused. The same indicator at "
                            "county level is reliable and is the grain to use."}],
            blocked=[not_computable("outcome_or_performance")])

    formatter = (fmt.percent if unit == "percent"
                 else (fmt.money if unit == "dollars" else fmt.count))
    note = (f"{len(values)} of {len(values) + len(withheld)} shown. "
            f"{len(withheld)} withheld: the margin of error is wider than "
            f"{MARGIN_UNRELIABLE:.0%} of the estimate, so the survey cannot place them "
            "on this scale."
            if withheld else
            f"{len(values)} geographies, all with margins inside "
            f"{MARGIN_UNRELIABLE:.0%} of the estimate.")

    drawn = maps.choropleth(
        geo_level, values, f"{name}, {scope}",
        f"American Community Survey {vintage} release, covering {vintage - 4} to {vintage}",
        formatter=formatter, only=only, note=note, bins=maps.SURVEY_BINS)

    caveats = [
        {"code": "conditions_are_not_outcomes", "rule": "§4",
         "guidance": "This maps a population, not a government's performance. Do not "
                     "place it beside a spending map as cause and effect, and do not "
                     "describe a government as responsible for the pattern."},
        {"code": "survey_not_census", "rule": "§8",
         "guidance": f"Five-year estimates covering {vintage - 4} to {vintage}, not a "
                     "count and not a single year."},
    ]
    if withheld:
        caveats.append({
            "code": "withheld_for_margin", "rule": "§8",
            "guidance": f"{len(withheld)} geographies are drawn as no data because their "
                        "margin of error is too wide to bin, not because they are "
                        "missing: " + ", ".join(withheld[:4]) + ". Say withheld, not "
                        "absent, and never read the gap as a low value."})
    if unit == "dollars":
        caveats.append({
            "code": "vintage_dollars", "rule": "§10",
            "guidance": f"These are {vintage} dollars. A map from another release is in "
                        "that release's dollars, so the two are not comparable and no "
                        "price index here can make them so."})

    return T.envelope(
        "render_community_map",
        data={"code": code, "name": name, "unit": unit, "vintage": vintage,
              "geo_level": geo_level, "scope": scope, "svg": drawn["svg"],
              "shown": len(values), "withheld": len(withheld),
              "withheld_names": withheld,
              "covered": drawn["covered"], "polygons": drawn["polygons"]},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")],
        vintage={"community": vintage})
