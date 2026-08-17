"""The typed analytic tool layer.

Each tool returns a uniform envelope:

    {
      "tool":           the tool name,
      "entity":         who this is about, where applicable,
      "data":           the figures,
      "caveats":        conditions the answer must carry, keyed to prompt rules,
      "not_computable": things the model must not attempt from this result,
      "vintage":        which year each block describes,
    }

The caveats are the point. A model that receives a peer median cannot receive it
without also receiving the flag saying the median is degenerate, because both
arrive in the same object. Free-form SQL over the same store would return the
median alone, and the answer would look grounded while breaking §8.

run_sql exists as an escape hatch for questions these tools do not cover. It is
read-only and every call is logged as a candidate for a new typed tool.
"""

import json
import os
import re
import sqlite3

from rules import (THRESHOLDS, TOPIC_CONCORDANCE, caveat, not_computable,
                   resolve_topic)

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "pf.sqlite")

# Entity types whose spending composition carries no information (§6).
SINGLE_CATEGORY_TYPES = {"School District"}


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.sql_log = []

    def rows(self, sql, *args):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def row(self, sql, *args):
        result = self.rows(sql, *args)
        return result[0] if result else None


def envelope(tool, entity=None, data=None, caveats=None, blocked=None, vintage=None):
    return {
        "tool": tool,
        "entity": entity,
        "data": data if data is not None else {},
        "caveats": caveats or [],
        "not_computable": blocked or [],
        "vintage": vintage or {},
    }


def _entity(store, pid6):
    return store.row(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county, "
        "host_county_pid6, is_dependent, website FROM entities WHERE pid6=?", pid6)


def _flags(store, pid6):
    return store.row("SELECT * FROM entity_flags WHERE pid6=?", pid6) or {}


def _oregon_caveats(entity, flags, store, pid6):
    """§13 applies only where jurisdiction-specific structure bears on the answer."""
    out = []
    gov_type = entity["gov_type_name"]
    if gov_type == "Municipal":
        health = store.row(
            "SELECT total FROM spending_by_service_area WHERE pid6=? "
            "AND service_area='Health & Human Services'", pid6)
        if not health or not health["total"]:
            out.append(caveat("oregon_city_health_expected"))
    elif gov_type == "County":
        out.append(caveat("oregon_county_delegated"))
    elif gov_type == "School District":
        out.append(caveat("oregon_school_state_funded"))
        name = (entity["common_name"] or entity["legal_name"]).lower()
        if "education service" in name or re.search(r"\besd\b", name):
            out.append(caveat("oregon_esd_no_peers"))
    name = (entity["common_name"] or entity["legal_name"]).lower()
    if "council of governments" in name or "councils of government" in name:
        out.append(caveat("oregon_cog_not_a_government"))
    return out


# ---------------------------------------------------------------- lookup

def find_entity(store, name, limit=8):
    """Resolve a name to entities. §14: distinguish not-in-data from not-existing."""
    pattern = f"%{name.strip().lower()}%"
    matches = store.rows(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county FROM entities "
        "WHERE lower(common_name) LIKE ? OR lower(legal_name) LIKE ? "
        "ORDER BY length(COALESCE(common_name, legal_name)) LIMIT ?",
        pattern, pattern, limit)

    caveats = []
    if len(matches) > 1:
        caveats.append({
            "code": "ambiguous_name", "rule": "§14",
            "guidance": "Several entities match. Ask which, or answer and state the "
                        "assumption. Overlapping jurisdictions are the norm, so a city and "
                        "a school district of the same name are different governments."})
    if not matches:
        caveats.append({
            "code": "entity_not_found", "rule": "§14",
            "guidance": "No entity in the data matches. Say it is not in the data, which is "
                        "not the same as saying it does not exist, and offer near matches."})
    return envelope("find_entity", data={"matches": matches, "count": len(matches)},
                    caveats=caveats)


def get_entity_profile(store, pid6):
    """The factual base, plus what the data can and cannot answer for this entity."""
    entity = _entity(store, pid6)
    if not entity:
        return envelope("get_entity_profile", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    availability = store.row("SELECT * FROM data_availability WHERE pid6=?", pid6)
    workforce = store.row("SELECT population, total_employees FROM workforce_profile WHERE pid6=?", pid6)
    flags = _flags(store, pid6)

    caveats = []
    if flags.get("no_population_denominator"):
        caveats.append(caveat("no_population_denominator"))
    if entity["gov_type_name"] in SINGLE_CATEGORY_TYPES:
        caveats.append(caveat("single_category_entity"))
    if entity["is_dependent"]:
        caveats.append({
            "code": "dependent_entity", "rule": "§6",
            "guidance": "This entity is dependent on a parent government. Its finances "
                        "interact with the parent's."})
    caveats += _oregon_caveats(entity, flags, store, pid6)

    blocked = []
    if flags.get("no_population_denominator"):
        blocked.append(not_computable("per_capita_without_population"))

    return envelope(
        "get_entity_profile", entity=entity,
        data={"population": (workforce or {}).get("population"),
              "total_employees": (workforce or {}).get("total_employees"),
              "available": {k: v for k, v in (availability or {}).items() if k != "pid6"}},
        caveats=caveats, blocked=blocked)


# ------------------------------------------------------------- financial

def get_financial_position(store, pid6):
    """§8: components before the label, and always name the driver."""
    entity = _entity(store, pid6)
    stability = store.row("SELECT * FROM fiscal_stability WHERE pid6=?", pid6)
    operating = store.row("SELECT * FROM operating_vs_capital WHERE pid6=?", pid6)
    volatility = store.row("SELECT * FROM revenue_volatility WHERE pid6=?", pid6)
    flags = _flags(store, pid6)

    if not stability:
        return envelope("get_financial_position", entity=entity, caveats=[{
            "code": "no_fiscal_stability", "rule": "§2",
            "guidance": "This entity has no fiscal stability record. Say so plainly."}])

    caveats, blocked = [], [not_computable("service_area_yoy")]

    if flags.get("debt_capped"):
        caveats.append(caveat("debt_capped"))
    if flags.get("prior_year_missing"):
        caveats.append(caveat("prior_year_missing"))
    if flags.get("peer_group_mismatch"):
        caveats.append(caveat("peer_group_mismatch"))
    if flags.get("peer_count_disagrees"):
        caveats.append(caveat("peer_count_disagrees"))
    if operating and (operating["capital_pct"] or 0) > THRESHOLDS["capital_active"]:
        caveats.append(caveat("capital_lumpy"))
    if entity["gov_type_name"] in ("Municipal", "County", "School District", "Special District"):
        caveats.append(caveat("oregon_property_tax_constrained"))

    # §8: name which component drives the composite.
    driver = None
    if stability["structural_balance_score"] is not None and stability["debt_to_revenue_score"] is not None:
        driver = ("debt_to_revenue" if stability["debt_to_revenue_score"] < stability["structural_balance_score"]
                  else "structural_balance")

    return envelope(
        "get_financial_position", entity=entity,
        data={
            "score": stability["score"],
            "rating_label": stability["rating_label"],
            "driver": driver,
            "components": {
                "structural_balance": {"value": stability["structural_balance"],
                                       "score": stability["structural_balance_score"], "weight": 0.6},
                "debt_to_revenue": {"value": stability["debt_to_revenue"],
                                    "score": stability["debt_to_revenue_score"], "weight": 0.4}},
            "prior_year_score": stability["prior_year_score"],
            "yoy_change": stability["yoy_change"],
            "peer_group": stability["peer_group"],
            "peer_count": stability["peer_count_observed"],
            "peer_median": stability["peer_median"],
            "operating_vs_capital": operating and {
                "operating": operating["operating_expenditure"],
                "capital": operating["capital_expenditure"],
                "total": operating["total_expenditure"],
                "capital_pct": operating["capital_pct"]},
            "revenue_volatility": volatility and {
                "coefficient_of_variation": volatility["coefficient_of_variation"],
                "years": volatility["num_yoy_pairs"],
                "swings_dominate_trend": bool(volatility["swings_dominate_trend"])},
        },
        caveats=caveats, blocked=blocked,
        vintage={"finance": stability["year"],
                 "operating_vs_capital": operating["year"] if operating else None})


def get_spending_breakdown(store, pid6):
    """§8: 'Other' above 20% means the breakdown is partial."""
    entity = _entity(store, pid6)
    areas = store.rows(
        "SELECT service_area, total, percentage, year FROM spending_by_service_area "
        "WHERE pid6=? ORDER BY total DESC", pid6)
    flags = _flags(store, pid6)

    if not areas:
        return envelope("get_spending_breakdown", entity=entity, caveats=[{
            "code": "no_breakdown", "rule": "§2",
            "guidance": "This entity has no spending breakdown in the data. Say so plainly. "
                        "Absence here is not a report of zero spending."}])

    caveats, blocked = [], [not_computable("service_area_yoy")]
    if flags.get("other_share_high"):
        caveats.append(caveat("other_share_high"))
    if flags.get("concentration_total"):
        caveats.append(caveat("concentration_total"))
    if entity["gov_type_name"] in SINGLE_CATEGORY_TYPES:
        caveats.append(caveat("single_category_entity"))
    caveats.append(caveat("absence_means_another_entity"))
    caveats.append(caveat("inputs_not_outcomes"))
    caveats += _oregon_caveats(entity, flags, store, pid6)

    # §8: the largest named category is not the top spending area when a larger
    # unclassified bucket sits beside it.
    named = [a for a in areas if a["service_area"] != "Other"]
    other = next((a for a in areas if a["service_area"] == "Other"), None)
    largest_is_other = bool(other and named and other["total"] > named[0]["total"])

    return envelope(
        "get_spending_breakdown", entity=entity,
        data={"service_areas": areas,
              "largest_named": named[0] if named else None,
              "other_exceeds_largest_named": largest_is_other},
        caveats=caveats, blocked=blocked,
        vintage={"finance": areas[0]["year"]})


def get_workforce(store, pid6):
    """§8: listed functions only, and shares are of listed functions."""
    entity = _entity(store, pid6)
    profile = store.row("SELECT * FROM workforce_profile WHERE pid6=?", pid6)
    areas = store.rows(
        "SELECT service_area, headcount, percentage FROM workforce_by_service_area "
        "WHERE pid6=? ORDER BY headcount DESC", pid6)
    functions = store.rows(
        "SELECT function_name, function_code, headcount, headcount_yoy, average_wage, "
        "pct_of_total, total_payroll FROM workforce_top_functions WHERE pid6=? "
        "ORDER BY headcount DESC", pid6)

    if not profile or not (areas or functions):
        return envelope("get_workforce", entity=entity, caveats=[{
            "code": "no_workforce", "rule": "§2",
            "guidance": "This entity has no workforce record. Say so plainly."}])

    flags = _flags(store, pid6)
    caveats = [caveat("workforce_partial"), caveat("vintage_split")]

    # A reported zero is not missing data, and it is not a workforce profile
    # either. §9: an entity reporting nothing here usually contracts the work out
    # or is served by another entity. Say that rather than describing the staff.
    if not profile["total_employees"] and not profile["total_annual_payroll"]:
        caveats.append({
            "code": "reported_zero_workforce", "rule": "§9",
            "guidance": "This entity reports zero employees and zero payroll. That is a "
                        "reported zero, not absent data. It usually means the work is "
                        "contracted out or another entity performs it. Do not describe a "
                        "workforce, and do not present this as a staffing decision."})

    if flags.get("no_population_denominator"):
        caveats.append(caveat("no_population_denominator"))
    if flags.get("personnel_share_unusual"):
        caveats.append({
            "code": "personnel_share_unusual", "rule": "§11",
            "guidance": "Personnel share of operating spending is outside the 35% to 65% "
                        "band, which is unusual and worth naming."})

    blocked = []
    if flags.get("no_population_denominator"):
        blocked.append(not_computable("per_capita_without_population"))

    return envelope(
        "get_workforce", entity=entity,
        data={"total_employees": profile["total_employees"],
              "population": profile["population"],
              "employees_per_1000": profile["employees_per_1000"],
              "average_annual_wage": profile["average_annual_wage"],
              "personnel_cost_pct": profile["personnel_cost_pct"],
              "full_time_ratio": profile["full_time_ratio"],
              "by_service_area": areas, "top_functions": functions},
        caveats=caveats, blocked=blocked, vintage={"workforce": profile["year"]})


# ------------------------------------------------------------ comparison

PEER_METRICS = {
    "capital_share": ("operating_vs_capital", "capital_pct", "peer_median",
                      "peer_group", "peer_count_stated", "peer_median_degenerate"),
    "fiscal_stability": ("fiscal_stability", "score", "peer_median",
                         "peer_group", "peer_count_observed", None),
}


def compare_to_peers(store, pid6, metric="capital_share"):
    """§8 and §14: a ratio, the peer group definition, the count.

    Where the peer statistic is degenerate, the distance is refused rather than
    computed, and the refusal is the payload.
    """
    if metric not in PEER_METRICS:
        return envelope("compare_to_peers", caveats=[{
            "code": "unknown_metric", "rule": "§4",
            "guidance": f"No peer benchmark exists for '{metric}'. Available: "
                        f"{', '.join(PEER_METRICS)}."}])

    table, value_col, median_col, group_col, count_col, degenerate_col = PEER_METRICS[metric]
    entity = _entity(store, pid6)
    record = store.row(f"SELECT * FROM {table} WHERE pid6=?", pid6)
    flags = _flags(store, pid6)

    if not record:
        return envelope("compare_to_peers", entity=entity, caveats=[{
            "code": "no_benchmark", "rule": "§2",
            "guidance": "This entity has no benchmark for that metric. Say so plainly."}])

    value, median = record[value_col], record[median_col]
    degenerate = bool(record[degenerate_col]) if degenerate_col else False
    caveats, computed_ratio = [], None

    if flags.get("peer_group_mismatch"):
        caveats.append(caveat("peer_group_mismatch"))
        degenerate = True  # the comparison is invalid regardless of the median
    if flags.get("peer_count_disagrees"):
        caveats.append(caveat("peer_count_disagrees"))

    if degenerate:
        caveats.append(caveat("peer_median_degenerate"))
    elif median and value is not None:
        computed_ratio = value / median
        if computed_ratio >= THRESHOLDS["peer_ratio_lead"] or computed_ratio <= THRESHOLDS["peer_ratio_low"]:
            caveats.append({
                "code": "peer_ratio_notable", "rule": "§11",
                "guidance": "This ratio is a lead candidate. State the expected baseline "
                            "before the deviation, so the reader can see the size of the gap."})

    if metric == "capital_share":
        caveats.append(caveat("capital_lumpy"))

    return envelope(
        "compare_to_peers", entity=entity,
        data={"metric": metric, "value": value, "peer_median": median,
              "ratio": computed_ratio,
              "ratio_refused": degenerate,
              "peer_group": record[group_col], "peer_count": record[count_col]},
        caveats=caveats,
        vintage={"finance": record["year"] if "year" in record.keys() else None})


def compare_entities(store, pid6_list, metric="fiscal_stability"):
    """§12: compare like with like, and say who is missing."""
    entities = [_entity(store, p) for p in pid6_list]
    found = [e for e in entities if e]
    missing = [p for p, e in zip(pid6_list, entities) if not e]
    types = {e["gov_type_name"] for e in found}

    caveats = []
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This set mixes {', '.join(sorted(types))}. A county and a city are "
                        "not comparable on health spending, because Oregon counties carry "
                        "state-delegated functions. Separate the types or decline."})
    if "County" in types and "Municipal" in types:
        caveats.append(caveat("oregon_county_delegated"))
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data. Note who is "
                        "missing before ranking."})
    caveats.append(caveat("inputs_not_outcomes"))

    table = "fiscal_stability" if metric == "fiscal_stability" else "operating_vs_capital"
    column = "score" if metric == "fiscal_stability" else "capital_pct"
    rows = []
    for entity in found:
        record = store.row(f"SELECT * FROM {table} WHERE pid6=?", entity["pid6"])
        rows.append({"pid6": entity["pid6"],
                     "name": entity["common_name"] or entity["legal_name"],
                     "gov_type": entity["gov_type_name"],
                     "value": record[column] if record else None,
                     "flags": {k: v for k, v in _flags(store, entity["pid6"]).items()
                               if v and k != "pid6"}})

    return envelope("compare_entities",
                    data={"metric": metric, "entities": rows, "missing": missing,
                          "basis": {"entity_types": sorted(types), "count": len(found)}},
                    caveats=caveats)


# -------------------------------------------------------------- salience

def find_salient(store, pid6):
    """§11: rank by divergence, not by size. An unremarkable entity says so."""
    entity = _entity(store, pid6)
    flags = _flags(store, pid6)
    stability = store.row("SELECT * FROM fiscal_stability WHERE pid6=?", pid6)
    operating = store.row("SELECT * FROM operating_vs_capital WHERE pid6=?", pid6)
    volatility = store.row("SELECT * FROM revenue_volatility WHERE pid6=?", pid6)
    workforce = store.row("SELECT * FROM workforce_profile WHERE pid6=?", pid6)

    candidates = []

    # Tier 1: divergence within the entity, two things moving in opposite directions.
    if stability and stability["structural_balance_score"] is not None:
        sb, dtr = stability["structural_balance_score"], stability["debt_to_revenue_score"]
        if sb is not None and dtr is not None and abs(sb - dtr) >= 50:
            candidates.append({
                "tier": 1, "kind": "internal_divergence",
                "finding": "structural balance and debt-to-revenue sit at opposite ends",
                "values": {"structural_balance_score": sb, "debt_to_revenue_score": dtr},
                "reading": "carrying capital obligations rather than failing operationally"
                           if sb > dtr else "operationally strained while carrying little debt"})

    areas = store.rows("SELECT service_area, headcount, percentage FROM "
                       "workforce_by_service_area WHERE pid6=? ORDER BY headcount DESC", pid6)

    # Tier 2: divergence from peers, only where the median is sound.
    if operating and operating["peer_median"] and not operating["peer_median_degenerate"]:
        value, median = operating["capital_pct"], operating["peer_median"]
        if value is not None and median:
            r = value / median
            if r >= THRESHOLDS["peer_ratio_mention"] or r <= THRESHOLDS["peer_ratio_low"]:
                candidates.append({
                    "tier": 2, "kind": "peer_divergence",
                    "finding": "capital share diverges from the peer median",
                    "values": {"value": value, "peer_median": median, "ratio": r},
                    "reading": "active capital period" if r > 1 else "no current capital cycle"})

    # Tier 3: change over time, at the entity total only (§8).
    if operating and operating["yoy_pct_change"] is not None:
        change = abs(operating["yoy_pct_change"])
        if change >= THRESHOLDS["yoy_name"]:
            candidates.append({
                "tier": 3, "kind": "total_change",
                "finding": "total expenditure moved outside the ordinary band",
                "values": {"yoy_pct_change": operating["yoy_pct_change"]},
                "reading": "possibly a capital event or one-time item; state as possibility"
                           if change >= THRESHOLDS["yoy_lead"] else "worth naming"})
    if volatility and volatility["swings_dominate_trend"]:
        candidates.append({
            "tier": 3, "kind": "revenue_volatility",
            "finding": "revenue swings dominate the trend",
            "values": {"coefficient_of_variation": volatility["coefficient_of_variation"]},
            "reading": "year-to-year variation is larger than the underlying direction"})

    # Tier 4: structural composition.
    if flags.get("concentration_total"):
        candidates.append({
            "tier": 4, "kind": "concentration",
            "finding": "a single service area exceeds 90% of spending",
            "values": {}, "reading": "composition carries no information for this entity"})
    if workforce and workforce["personnel_cost_pct"] is not None and flags.get("personnel_share_unusual"):
        candidates.append({
            "tier": 4, "kind": "personnel_share",
            "finding": "personnel share of operating spending is outside the usual band",
            "values": {"personnel_cost_pct": workforce["personnel_cost_pct"]},
            "reading": "unusual mix for this entity type"})
    if len(areas) >= 2:
        top, second = areas[0], areas[1]
        if (top["percentage"] or 0) - (second["percentage"] or 0) >= 40:
            candidates.append({
                "tier": 4, "kind": "workforce_concentration",
                "finding": "workforce is concentrated in one function",
                "values": {top["service_area"]: top["percentage"],
                           second["service_area"]: second["percentage"]},
                "reading": "name both figures; do not explain why"})

    candidates.sort(key=lambda c: c["tier"])

    caveats = [caveat("inputs_not_outcomes")]
    if flags.get("prior_year_missing"):
        caveats.append(caveat("prior_year_missing"))
    if flags.get("debt_capped"):
        caveats.append(caveat("debt_capped"))
    if flags.get("peer_median_degenerate"):
        caveats.append(caveat("peer_median_degenerate"))
    if flags.get("other_share_high"):
        caveats.append(caveat("other_share_high"))
    if not candidates:
        caveats.append({
            "code": "nothing_unusual", "rule": "§11",
            "guidance": "Nothing in this entity's profile clears an attention threshold. "
                        "Say the profile is close to typical for its peer group and stop. "
                        "Do not manufacture a finding."})

    return envelope("find_salient", entity=entity,
                    data={"candidates": candidates, "nothing_unusual": not candidates,
                          "lead": candidates[0] if candidates else None},
                    caveats=caveats,
                    blocked=[not_computable("service_area_yoy"),
                             not_computable("outcome_or_performance")])


# ------------------------------------------------------------- ecosystem

def list_ecosystem(store, county_pid6=None, county_name=None):
    """§9: when a question concerns a place, answer about the entities serving it."""
    if county_pid6 is None and county_name:
        match = store.row(
            "SELECT pid6 FROM entities WHERE gov_type_name='County' "
            "AND (lower(common_name) LIKE ? OR lower(legal_name) LIKE ?)",
            f"%{county_name.lower()}%", f"%{county_name.lower()}%")
        county_pid6 = match["pid6"] if match else None

    if not county_pid6:
        return envelope("list_ecosystem", caveats=[{
            "code": "county_not_found", "rule": "§14",
            "guidance": "That county is not in the data."}])

    county = _entity(store, county_pid6)
    members = store.rows(
        "SELECT e.pid6, e.legal_name, e.common_name, e.gov_type_name, e.is_dependent, "
        "a.has_geometry, a.has_offices FROM entities e "
        "JOIN data_availability a ON a.pid6=e.pid6 "
        "WHERE e.host_county_pid6=? ORDER BY e.gov_type_name, e.common_name", county_pid6)

    by_type = {}
    for member in members:
        by_type.setdefault(member["gov_type_name"], []).append(member)

    # §9 ranks measured fact above inference from names. Where a service extent
    # has been measured, it outranks host-county grouping, which files a district
    # under the county holding most of its assessed value and so misses every
    # district that serves this county from a neighbouring filing.
    try:
        also_serving = store.rows(
            "SELECT e.pid6, e.legal_name, e.common_name, e.gov_type_name, "
            "s.filed_county, s.basis FROM service_extent s "
            "JOIN entities e ON e.pid6 = s.pid6 "
            "WHERE s.serves_county = ? AND s.filed_county != s.serves_county "
            "ORDER BY e.gov_type_name, e.common_name",
            (county["host_county"] or "").upper())
    except sqlite3.OperationalError:
        also_serving = []   # the dissolve pilot has not been run

    caveats = [
        caveat("ecosystem_basis_host_county"),
        caveat("ecosystem_no_summing"),
        caveat("host_county_undercaptures"),
        caveat("absence_means_another_entity"),
        {"code": "responsibility_distributed", "rule": "§9",
         "guidance": "There is no single body accountable for a place's combined public "
                     "spending. If asked who is responsible for an outcome spanning "
                     "entities, say responsibility is distributed and name the bodies."},
    ]
    mappable = sum(1 for m in members if m["has_geometry"])
    if mappable < len(members):
        caveats.append({
            "code": "partial_geometry", "rule": "§9",
            "guidance": f"Only {mappable} of {len(members)} entities here have boundaries in "
                        "the data. A map of this ecosystem is necessarily partial; a list "
                        "is complete."})

    if also_serving:
        caveats.append({
            "code": "served_from_another_county", "rule": "§9",
            "guidance": f"{len(also_serving)} further governments operate in this county but "
                        "are filed under a neighbouring one, so they are absent from a "
                        "host-county list. They are included here on measured service "
                        "extent, which outranks inference from filing. Name them when the "
                        "question is who serves this place."})

    return envelope(
        "list_ecosystem", entity=county,
        data={"county": county["common_name"] or county["legal_name"],
              "total": len(members),
              "by_type": {k: len(v) for k, v in by_type.items()},
              "entities": members, "mappable": mappable,
              "also_serving_filed_elsewhere": also_serving},
        caveats=caveats,
        blocked=[not_computable("property_tax_summed")])


# ------------------------------------------------------- serving a place

# What each basis in serves_place is actually worth, strongest first. These are
# not three ways of saying the same thing and the interface must not flatten
# them: one is a record, one is an inference from an outline, one is a filing.
#
# The order matters twice over. It sorts the list, and it decides which row wins
# when a government arrives on more than one basis.
SERVING_BASIS = [
    ("self", "the place itself",
     "The city is a government, and for most residents it is the one they can name. "
     "It is listed with the rest because leaving it out makes the stack look like "
     "things happening to a place rather than including the place."),
    ("precinct_exact", "recorded per precinct",
     "The precinct file lists, for each polygon, which districts that polygon sits "
     "in. This is a record of the assignment, not an inference from it."),
    ("boundary_overlap", "the place falls inside its boundary",
     "The place sits inside the government's own outline. That establishes overlap, "
     "which for a school district is the same thing as service and for a district "
     "with an odd shape may not be."),
    ("register", "from the register",
     "The register names the county each government is filed under, and for a city "
     "inside a county that filing is the relationship."),
]
SERVING_ORDER = {basis: i for i, (basis, _, _) in enumerate(SERVING_BASIS)}
SERVING_MEANING = {basis: (short, long) for basis, short, long in SERVING_BASIS}

# Reading order, which is not evidence order. A reader scans this list by what
# kind of thing each government is — my town, my county, my schools, the rest —
# and sorting by how well established each row is instead put a community
# college ahead of Portland Public Schools because one sat in the precinct file
# and the other only in a boundary. The basis stays on every row; it just does
# not decide where the row goes.
SERVING_TYPE_ORDER = ["Municipal", "County", "School District",
                      "Special District", "State"]

# What a resident's stack really looks like, against what this data can show.
# The median address in Multnomah sits inside ten governments; the median place
# in this table resolves to two. The gap is the whole caveat, so it is stated as
# a number rather than as a hedge.
TYPICAL_STACK = 10


def availability(store, pid6):
    """Which blocks this government has the data for.

    The ETL manifest answers most of it. has_serving is computed here instead
    because serving is a relationship between two governments rather than a
    property of one, so it has no column in a per-entity table — and a town with
    no established stack must not be offered a stack.

    One definition, called by the follow-up chooser, the preset list and the
    static export alike. Three copies of this dict is how an interface ends up
    offering an answer the files behind it do not contain.
    """
    row = store.row("SELECT * FROM data_availability WHERE pid6=?", pid6) if pid6 else None
    available = dict(row) if row else {}
    try:
        served = store.row(
            "SELECT COUNT(*) AS n FROM serves_place WHERE place_pid6=?", pid6)
        available["has_serving"] = bool(served and served["n"])
    except sqlite3.OperationalError:
        available["has_serving"] = False   # serving ETL not run against this build
    return available


def governments_serving(store, pid6):
    """Which governments serve one place — the question a resident arrives with.

    Nobody has a government; they have a stack of them, and they can usually name
    two. A search box over 1,529 names asks the reader to already know the answer.
    This asks instead for the one thing they certainly know, which is where they
    live, and hands back the governments established to serve it.

    Established, not all. Every row says what it rests on, and the tool says how
    far short of a real stack the list falls, because a reader shown four
    governments will otherwise take four to be the number.
    """
    place = _entity(store, pid6)
    if not place:
        return envelope("governments_serving", caveats=[{
            "code": "not_found", "rule": "§14",
            "guidance": "That government is not in the data."}])

    try:
        rows = store.rows(
            "SELECT s.pid6, s.basis, s.relation, e.legal_name, e.common_name, "
            "       e.gov_type_name, a.has_spending_breakdown "
            "FROM serves_place s JOIN entities e ON e.pid6 = s.pid6 "
            "LEFT JOIN data_availability a ON a.pid6 = s.pid6 "
            "WHERE s.place_pid6 = ?", pid6)
    except sqlite3.OperationalError:
        rows = []   # the serving ETL has not been run against this build

    if not rows:
        # A place with no stack and a government that is not a place fail
        # differently, and telling them apart is the difference between "we
        # have not worked this out yet" and "that question does not apply".
        if place["gov_type_name"] != "Municipal":
            return envelope("governments_serving", entity=place, caveats=[{
                "code": "not_a_place", "rule": "§9",
                "guidance": "This asks which governments serve a city, so it is asked of "
                            "a city. A district or a county is one of the answers, not "
                            "the question."}])
        return envelope("governments_serving", entity=place, caveats=[{
            "code": "no_stack_established", "rule": "§9",
            "guidance": "No government has been established to serve this place beyond "
                        "itself. That is a gap in the boundary files, which cover school "
                        "districts and 19 special districts out of more than a thousand — "
                        "not a city that governs itself alone."}])

    # One row per government, keeping the strongest basis it arrived on. A
    # district that both sits in the precinct file and overlaps the outline is
    # recorded, and saying so twice would read as two governments.
    best = {}
    for row in rows:
        current = best.get(row["pid6"])
        if current is None or SERVING_ORDER.get(row["basis"], 9) < SERVING_ORDER.get(
                current["basis"], 9):
            best[row["pid6"]] = dict(row)

    # The town is one of the governments serving the town. It is not in the
    # table — a row saying a place serves itself would be noise there — but it
    # belongs in the answer, and putting it first is what makes the list read as
    # a stack the reader is inside rather than a list of outside bodies.
    best[pid6] = {"pid6": pid6, "basis": "self", "relation": "itself",
                  "legal_name": place["legal_name"],
                  "common_name": place["common_name"],
                  "gov_type_name": place["gov_type_name"],
                  "has_spending_breakdown": None}

    serving = sorted(best.values(), key=lambda r: (
        r["basis"] != "self",
        SERVING_TYPE_ORDER.index(r["gov_type_name"])
        if r["gov_type_name"] in SERVING_TYPE_ORDER else len(SERVING_TYPE_ORDER),
        (r["common_name"] or r["legal_name"] or "")))
    for row in serving:
        row["name"] = row["common_name"] or row["legal_name"]
        row["basis_label"] = SERVING_MEANING.get(row["basis"], (row["basis"], ""))[0]

    by_basis = {}
    for row in serving:
        by_basis[row["basis"]] = by_basis.get(row["basis"], 0) + 1

    caveats = [caveat("serving_stack_incomplete")]
    # Self is not evidence, so it does not count towards the bases disagreeing.
    # Otherwise every place in Oregon would carry a caveat about mixed evidence
    # on the strength of being itself.
    if len([b for b in by_basis if b != "self"]) > 1:
        caveats.append(caveat("serving_basis_differs"))
    caveats.append(caveat("ecosystem_no_summing"))
    caveats.append(caveat("absence_means_another_entity"))

    # Multnomah is the only county with a precinct file, so it is the only place
    # where the count approaches the truth. Everywhere else the shortfall is
    # large enough that leaving it implied would mislead.
    shortfall = TYPICAL_STACK - len(serving)
    if "precinct_exact" not in by_basis and shortfall > 0:
        caveats.append({
            "code": "stack_understated", "rule": "§9",
            "guidance": f"{len(serving)} governments are established here, against the "
                        f"roughly {TYPICAL_STACK} a typical Oregon address sits inside. The "
                        "missing ones are special districts — fire, water, transit, soil and "
                        "water — which have no boundary in this data outside Multnomah "
                        "County. Do not present this as the whole stack."})

    return envelope(
        "governments_serving", entity=place,
        data={"place": place["common_name"] or place["legal_name"],
              "county": place["host_county"],
              "serving": serving,
              "total": len(serving),
              "by_basis": by_basis,
              "typical_stack": TYPICAL_STACK,
              "basis_meaning": {b: long for b, _, long in SERVING_BASIS
                                if b in by_basis}},
        caveats=caveats,
        blocked=[not_computable("property_tax_summed")])


# ---------------------------------------------------------------- offices

# The OCD id already carries the fact that matters most about an elected seat
# and nothing was reading it. A seat filed under
#
#     ocd-division/country:us/state:or/place:adair_village/council_district:ward_1
#
# is elected by ward one alone. A seat filed under the bare jurisdiction with
# office_position "Position 3" is elected by everybody in the jurisdiction. That
# is the difference between "you vote on all seven of these" and "you vote on
# one of seven, the one covering your address", and it is the single thing about
# a governing body a reader can act on.
#
# board_distict is a misspelling present in the source data; it is matched here
# rather than corrected upstream so the parser stays true to what is filed.
SUBDIVISION = re.compile(
    r"/(board_district|board_distict|council_district|ward|zone|subdistrict):([^/]+)$")

# §7: a ballot structure describes how a seat is filled, never who holds it.
AT_LARGE = "at_large"          # whole jurisdiction votes, seats numbered by position
BY_DISTRICT = "by_district"    # each seat belongs to a ward, zone or board district
SINGLE = "single"              # one seat for the whole jurisdiction
MIXED = "mixed"                # a body that fills some seats each way


def _seat_order(label):
    """Sort 'Ward 2' before 'Ward 10', which a plain string sort will not."""
    match = re.search(r"(\d+)", label or "")
    return (re.sub(r"\d+", "", label or ""), int(match.group(1)) if match else 0)


def parse_ocd(ocd_id):
    """Split an OCD id into the jurisdiction and the sub-district inside it."""
    match = SUBDIVISION.search(ocd_id or "")
    if not match:
        return {"jurisdiction": ocd_id, "subdivision_type": None, "subdivision": None}
    kind = match.group(1).replace("board_distict", "board_district")
    return {"jurisdiction": ocd_id[:match.start()],
            "subdivision_type": kind,
            "subdivision": match.group(2)}


def district_label(subdivision_type, subdivision):
    """'Ward 1', 'Zone 3', 'District 4' — what the ballot would call it."""
    if not subdivision:
        return None
    name = subdivision.replace("_", " ").strip()
    # Sources write both "ward_1" and "district_1"; the prefix is already the
    # noun, so repeating the subdivision type would read "Ward ward 1".
    if re.match(r"^(ward|zone|district|position|subdistrict)\b", name, re.I):
        return name[:1].upper() + name[1:]
    noun = {"council_district": "District", "board_district": "Zone",
            "ward": "Ward", "zone": "Zone", "subdistrict": "Subdistrict"}.get(
                subdivision_type, "District")
    return f"{noun} {name}"


def ballot_structure(seats):
    """How a body's seats are filled, from the seats themselves.

    Returns one of the four constants above. A body is only called at-large when
    no seat carries a sub-district, because one districted seat in a body means a
    reader's ballot does not contain all of them.
    """
    districted = [s for s in seats if s.get("district")]
    if not districted:
        return SINGLE if len(seats) == 1 else AT_LARGE
    return BY_DISTRICT if len(districted) == len(seats) else MIXED



def get_offices(store, pid6):
    """§7: role, seat and holder are three different things, and holders are absent."""
    entity = _entity(store, pid6)
    rows = store.rows(
        "SELECT o.role, o.office_position, o.partisan_election, o.occupation_method, "
        "o.term_length, m.confidence, m.match_method, o.ocd_id FROM offices o "
        "JOIN office_entity_match m ON m.ocd_id=o.ocd_id WHERE m.pid6=?", pid6)

    if not rows:
        return envelope("get_offices", entity=entity, caveats=[
            caveat("office_coverage_uneven"),
            {"code": "no_offices", "rule": "§7",
             "guidance": "The data includes no office records for this entity. Say the data "
                         "does not include them, not that the entity has no governing body."}])

    # How a seat is filled and for how long is the part of this a reader can act
    # on, and it was being queried and then dropped. A seat you elect every two
    # years is a different fact about a government than one filled by appointment.
    by_role = {}
    for row in rows:
        by_role.setdefault(row["role"], []).append(row)

    caveats = [caveat("offices_have_no_holders"), caveat("office_coverage_uneven")]
    confidences = {r["confidence"] for r in rows}
    if confidences & {"low"}:
        caveats.append(caveat("office_match_low_confidence"))
    if any((r["office_position"] or "") for r in rows):
        caveats.append({
            "code": "seats_not_roles", "rule": "§7",
            "guidance": "Multi-member bodies have many seats of the same role. Never present "
                        "a seat as a role or imply the body has one member."})

    def _one(values):
        """The single value shared by every seat in a role, or None if they differ."""
        distinct = {v for v in values if v}
        return distinct.pop() if len(distinct) == 1 else None

    roles = []
    for role, seats in sorted(by_role.items()):
        parsed = []
        for seat in seats:
            parts = parse_ocd(seat["ocd_id"])
            parsed.append({
                "position": seat["office_position"],
                "district": district_label(parts["subdivision_type"],
                                           parts["subdivision"]),
                "district_type": parts["subdivision_type"],
                "ocd_id": seat["ocd_id"]})
        parsed.sort(key=lambda s: _seat_order(s["district"] or s["position"] or ""))
        structure = ballot_structure(parsed)
        roles.append({
            "role": role,
            "seats": parsed,
            "seat_count": len(seats),
            "ballot": structure,
            "districts": [s["district"] for s in parsed if s["district"]],
            "filled_by": _one(s["occupation_method"] for s in seats),
            "partisan": _one(s["partisan_election"] for s in seats),
            "term_length": _one(s["term_length"] for s in seats)})

    elected = sum(r["seat_count"] for r in roles if r["filled_by"] == "Election")
    appointed = sum(r["seat_count"] for r in roles if r["filled_by"] == "Appointment")
    if elected or appointed:
        caveats.append({
            "code": "how_seats_are_filled", "rule": "§7",
            "guidance": f"{elected} of these seats are filled by election and "
                        f"{appointed} by appointment. That distinction is what a reader "
                        "can act on, so state it. It describes the seat, not who holds "
                        "it; no holder names are in this data."})

    # The distinction a ballot actually makes. Reporting "7 seats" without it
    # lets a reader assume they vote on all seven when they may vote on one.
    districted = [r for r in roles if r["ballot"] in (BY_DISTRICT, MIXED)]
    at_large = [r for r in roles if r["ballot"] == AT_LARGE]
    if districted:
        caveats.append({
            "code": "seats_elected_by_district", "rule": "§7",
            "guidance": "Seats in "
                        + ", ".join(r["role"] for r in districted)
                        + " belong to a ward, zone or board district, so a voter fills "
                          "one of them rather than all of them. Say which seats are on "
                          "any one ballot; a seat count is not a ballot."})
    if at_large:
        caveats.append({
            "code": "seats_elected_at_large", "rule": "§7",
            "guidance": "Seats in " + ", ".join(r["role"] for r in at_large)
                        + " are numbered positions with no sub-district in the record, "
                          "so on the evidence here every voter in the jurisdiction "
                          "votes on each of them. The numbering is a ballot label, not "
                          "a geography."})

    return envelope(
        "get_offices", entity=entity,
        data={"roles": roles, "total_seats": len(rows),
              "elected_seats": elected, "appointed_seats": appointed,
              "districted_seats": sum(r["seat_count"] for r in districted),
              "at_large_seats": sum(r["seat_count"] for r in at_large),
              "match_confidence": sorted(confidences)},
        caveats=caveats)


# ----------------------------------------------------------------- topics

def map_topic_to_categories(store, topic):
    """§12: name the gap, offer the proxy, say how loose the fit is.

    The typed word is resolved through the alias index rather than by looking
    for a concordance key inside it. Nobody arrives with the concordance's
    vocabulary: the scope of work says unsheltered, or broadband, or deferred
    maintenance. A subject that fails to resolve never reaches the §12 gap
    sentence at all, and an empty result reads as "this data has nothing" when
    the truth is that it has a loose proxy and needs to say how loose.
    """
    key = topic.strip().lower()
    resolved = resolve_topic(key)

    caveats = [caveat("topic_gap"), caveat("topic_no_dollar_figure")]
    if "homeless" in key or "homelessness" in resolved:
        caveats.append(caveat("dp03_cannot_measure_homelessness"))

    if not resolved:
        return envelope(
            "map_topic_to_categories",
            data={"topic": topic, "matched": None, "also": [], "categories": [],
                  "fit": "none",
                  "note": "No category in this data corresponds to that topic."},
            caveats=caveats,
            blocked=[not_computable("issue_level_spending")])

    # The rest are named rather than dropped. "fire" is structural and wildland,
    # they sit on different categories with different fit, and picking one
    # silently is the §14 ambiguity failure with the ambiguity hidden.
    matched, also = resolved[0], resolved[1:]
    categories, fit, note = TOPIC_CONCORDANCE[matched]
    if also:
        caveats.append(caveat("topic_reads_as_several"))
    return envelope(
        "map_topic_to_categories",
        data={"topic": topic, "matched": matched, "also": also,
              "categories": categories, "fit": fit, "note": note},
        caveats=caveats,
        blocked=[not_computable("issue_level_spending")])


# ------------------------------------------------------------- escape hatch

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace)\b", re.I)


def run_sql(store, sql, limit=200):
    """Read-only escape hatch for questions the typed tools do not cover.

    Every call is logged. A repeated query here is a missing typed tool, and the
    log is the backlog. Results carry a standing caveat because rows retrieved
    this way arrive without the guardrails the typed tools attach.
    """
    if FORBIDDEN_SQL.search(sql) or ";" in sql.strip().rstrip(";"):
        return envelope("run_sql", caveats=[{
            "code": "write_rejected", "rule": "§4",
            "guidance": "Only a single read-only SELECT is permitted."}])
    if not sql.strip().lower().startswith(("select", "with")):
        return envelope("run_sql", caveats=[{
            "code": "not_a_select", "rule": "§4",
            "guidance": "Only a single read-only SELECT is permitted."}])

    store.sql_log.append(sql)
    try:
        rows = store.rows(f"SELECT * FROM ({sql.rstrip(';')}) LIMIT {int(limit)}")
    except sqlite3.Error as error:
        return envelope("run_sql", caveats=[{
            "code": "sql_error", "rule": "§4", "guidance": str(error)}])

    return envelope(
        "run_sql", data={"rows": rows, "count": len(rows)},
        caveats=[{
            "code": "raw_rows", "rule": "§4",
            "guidance": "These rows arrived without the guardrails the typed tools attach. "
                        "Check entity_flags for this entity before interpreting them, and "
                        "prefer a typed tool where one covers the question."}])


TOOLS = {
    "find_entity": find_entity,
    "get_entity_profile": get_entity_profile,
    "get_financial_position": get_financial_position,
    "get_spending_breakdown": get_spending_breakdown,
    "get_workforce": get_workforce,
    "compare_to_peers": compare_to_peers,
    "compare_entities": compare_entities,
    "find_salient": find_salient,
    "list_ecosystem": list_ecosystem,
    "governments_serving": governments_serving,
    "get_offices": get_offices,
    "map_topic_to_categories": map_topic_to_categories,
    "run_sql": run_sql,
}


# ------------------------------------------------------------- rendering

import format as fmt  # noqa: E402
import maps           # noqa: E402
import viz            # noqa: E402

# The model chooses a form and an entity. It supplies no title, no axis label,
# no colour and no number, so a caption cannot assert a relationship the data
# does not contain and a chart cannot disagree with the prose beside it.
CHART_FORMS = (
    "spending_composition", "service_area_functions", "peer_range",
    "finances_over_time", "workforce_composition",
)

# Which peer metric each entity type is most usefully placed against. Fiscal
# stability is deliberately absent: it is a composite whose two components move
# for opposite reasons, so a position on it tells a reader less than a position
# on any single measure underneath it.
DEFAULT_PEER_METRIC = "expenditure_per_capita"


def _named(entity):
    return entity["common_name"] or entity["legal_name"]


def render_chart(store, pid6, form):
    """Draw one chart for one entity, carrying the same refusals as the tools."""
    if form not in CHART_FORMS:
        return envelope("render_chart", caveats=[{
            "code": "unknown_form", "rule": "§4",
            "guidance": f"No chart form '{form}'. Available: {', '.join(CHART_FORMS)}."}])

    entity = _entity(store, pid6)
    if not entity:
        return envelope("render_chart", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    name = _named(entity)
    flags = _flags(store, pid6)
    svg = table = None
    caveats, blocked = [], []

    if form == "spending_composition":
        source = get_spending_breakdown(store, pid6)
        caveats, blocked = source["caveats"], source["not_computable"]
        areas = source["data"].get("service_areas") or []
        if not areas:
            svg = viz.refusal(f"{name}: spending by service area",
                              "This entity has no spending breakdown in the data. That is an "
                              "absence of data, not a report of zero spending.")
        else:
            note = None
            if flags.get("other_share_high"):
                note = ("Other exceeds 20% of spending. Other is a Census classification "
                        "artifact, so this breakdown is partial.")
            import explore
            drilled = explore.drill(store, pid6)["data"].get("areas") or []
            peer_by_area = {a["label"]: a for a in drilled}
            svg = viz.horizontal_bars(
                f"{name}: spending by service area",
                f"Census functional categories, {areas[0]['year']}",
                [{"label": a["service_area"], "value": a["total"],
                  "peer_median": None, "peer_degenerate": 1} for a in areas],
                note=note)
            table = [{"service area": a["service_area"], "amount": fmt.money(a["total"]),
                      "share": fmt.percent(a["percentage"]),
                      "peer median share": fmt.percent(
                          (peer_by_area.get(a["service_area"]) or {}).get("peer_median_share"))
                      or "not comparable"} for a in areas]

    elif form == "service_area_functions":
        import explore
        area = store.row("SELECT service_area FROM spending_by_service_area WHERE pid6=? "
                         "ORDER BY total DESC LIMIT 1", pid6)
        if not area:
            svg = viz.refusal(f"{name}: functions by service area",
                              "This entity has no spending breakdown in the data.")
        else:
            source = explore.drill(store, pid6, area["service_area"])
            caveats, blocked = source["caveats"], source["not_computable"]
            functions = source["data"].get("functions") or []
            if not functions:
                svg = viz.refusal(f"{name}: functions in {area['service_area']}",
                                  "No functions are reported inside this area.")
            else:
                svg = viz.horizontal_bars(
                    f"{name}: inside {area['service_area']}",
                    f"Functions within the largest service area, {source['data']['year']}",
                    [{"label": f["label"], "value": f["value"],
                      "peer_median": f["peer_median"],
                      "peer_degenerate": f["peer_degenerate"]} for f in functions],
                    note="Bars are operating plus capital, the basis the service-area "
                         "shares are drawn against.")
                table = [{"function": f["label"], "spending": fmt.money(f["value"]),
                          "share of entity": fmt.percent(f["share_of_entity"])}
                         for f in functions]

    elif form == "peer_range":
        import explore
        source = explore.compare_to_peer_group(store, pid6, DEFAULT_PEER_METRIC)
        caveats, blocked = source["caveats"], source["not_computable"]
        data = source["data"]
        if not data.get("peers"):
            svg = viz.refusal(f"{name}: against peers",
                              "No peer distribution covers this entity for that measure.")
        elif data["peers"]["degenerate"]:
            svg = viz.refusal(
                f"{name}: against peers",
                "Not drawn. Most of this peer group reports nothing on this measure, so "
                "the median is not a midpoint and a position against it would misstate "
                "where this entity sits.")
        else:
            stats = dict(data["peers"])
            stats["peer_group"] = data["peer_group"]
            svg = viz.peer_range(
                f"{name}: {data['label'].lower()} against peers",
                f"{data['peer_group']}, {stats['n']} entities",
                data["value"], stats, formatter=fmt.rate, label=name)
            table = [{"measure": "this entity", "value": data["formatted"]},
                     {"measure": "peer median", "value": fmt.rate(stats["median"])},
                     {"measure": "middle half",
                      "value": f"{fmt.rate(stats['p25'])} to {fmt.rate(stats['p75'])}"},
                     {"measure": "full range",
                      "value": f"{fmt.rate(stats['low'])} to {fmt.rate(stats['high'])}"}]

    elif form == "finances_over_time":
        rows = store.rows(
            "SELECT year, revenue, expenditure FROM financial_trends WHERE pid6=? "
            "ORDER BY year", pid6)
        blocked = [not_computable("service_area_yoy")]
        caveats = [caveat("inputs_not_outcomes"), caveat("two_expenditure_measures")]
        # The spending breakdown elsewhere in this interface is computed against
        # operating plus capital, which is not this number. Drawing that series
        # alongside says so with a line rather than only in a rule.
        basis = {r["year"]: r["total_expenditure"] for r in store.rows(
            "SELECT year, total_expenditure FROM service_area_totals WHERE pid6=? "
            "ORDER BY year", pid6)}
        if len(rows) < 2:
            svg = viz.refusal(f"{name}: revenue and spending over time",
                              "Fewer than two years are available, so there is no series to "
                              "draw. One year is a point, not a trend.")
        else:
            series = [
                {"label": "Revenue", "points": [(r["year"], r["revenue"]) for r in rows]},
                {"label": "Reported expenditure",
                 "points": [(r["year"], r["expenditure"]) for r in rows]}]
            if len({basis.get(r["year"]) for r in rows} - {None}) > 1:
                series.append({
                    "label": "Operating and capital",
                    "points": [(r["year"], basis.get(r["year"])) for r in rows]})
            svg = viz.time_series(
                f"{name}: revenue and spending over time",
                f"{rows[0]['year']} to {rows[-1]['year']}, entity totals on two "
                "expenditure measures",
                series)
            table = [{"year": r["year"], "revenue": fmt.money(r["revenue"]),
                      "reported expenditure": fmt.money(r["expenditure"]),
                      "operating and capital": (fmt.money(basis[r["year"]])
                                                if basis.get(r["year"]) else "—")}
                     for r in rows]

    elif form == "workforce_composition":
        source = get_workforce(store, pid6)
        caveats, blocked = source["caveats"], source["not_computable"]
        areas = source["data"].get("by_service_area") or []
        if not areas:
            svg = viz.refusal(f"{name}: workforce by service area",
                              "This entity has no workforce breakdown in the data.")
        else:
            svg = viz.horizontal_bars(
                f"{name}: workforce by service area",
                f"Headcount, {source['vintage'].get('workforce')}",
                [{"label": a["service_area"], "value": a["headcount"]} for a in areas],
                formatter=fmt.count,
                note="Listed functions only. Shares are of listed functions, not of all staff.")
            table = [{"service area": a["service_area"], "headcount": a["headcount"],
                      "share of listed": fmt.percent(a["percentage"])} for a in areas]

    return envelope("render_chart", entity=entity,
                    data={"form": form, "svg": svg, "table": table,
                          "refused": table is None},
                    caveats=caveats, blocked=blocked)


# -------------------------------------------------- multi-entity rendering

COMPARISON_FORMS = ("entities_over_time", "service_area_across_entities",
                    "per_capita_by_service_area", "per_capita_over_time",
                    # Two five-year forms, because the trend data is two panels.
                    # An annual window exists for about 380 governments; both ends
                    # of 2017 and 2022 exist for around 1,500, which is the only
                    # five-year comparison most of the corpus can answer.
                    "five_year_panel", "five_year_change")

# Two of these are totals of expenditure and they are not the same number, so
# neither may be called "total spending" alone. The label carries the basis.
MEASURE_LABELS = {"expenditure": "Reported expenditure",
                  "service_area_basis": "Operating and capital spending",
                  "revenue": "Total revenue",
                  "debt": "Total debt"}


def render_comparison(store, pid6_list, form="entities_over_time",
                      measure="expenditure", service_area=None, indexed=False):
    """Draw several governments on one axis.

    Two forms, and the difference between them is not stylistic. Entity totals
    run 2017 to 2023, so they are a line. Service-area spending is a single year
    per entity, so it is a bar and never a line: drawing one year as a trend
    would assert a direction the data does not contain (§8).
    """
    import explore

    if form not in COMPARISON_FORMS:
        return envelope("render_comparison", caveats=[{
            "code": "unknown_form", "rule": "§4",
            "guidance": f"No comparison form '{form}'. Available: {', '.join(COMPARISON_FORMS)}."}])

    if form in ("service_area_across_entities", "per_capita_by_service_area") \
            and not service_area:
        return envelope("render_comparison", caveats=[{
            "code": "service_area_required", "rule": "§10",
            "guidance": "This form compares within one service area and needs to be told "
                        "which. Without one every entity is 100% of its own spending."}])

    svg = table = None

    if form == "entities_over_time":
        source = explore.compare_entities_over_time(store, pid6_list, measure)
        data = source["data"]
        series = data.get("series") or []
        label = MEASURE_LABELS.get(measure, measure.title())
        if len(series) < 2:
            svg = viz.refusal(
                f"{label} over time",
                "Fewer than two entities have two or more years of data, so there is "
                "nothing to compare. One year is a point, not a trend.")
        else:
            drawn = series[:4]
            years = sorted({p[0] for s in drawn for p in s["points"]})
            note = None
            if len(series) > 4:
                folded = ", ".join(s["label"] for s in series[4:])
                note = f"Four lines drawn. Also requested: {folded}."
            svg = viz.multi_series(
                f"{label}: {len(drawn)} governments compared",
                f"{years[0]} to {years[-1]}, entity totals rather than any single category",
                drawn, note=note)
            table = [dict([("year", year)] + [
                (s["label"], fmt.money(dict(s["points"]).get(year)))
                for s in drawn]) for year in years]

    elif form == "per_capita_by_service_area":
        source = explore.per_capita_by_service_area(store, pid6_list, service_area)
        data = source["data"]
        rows = data.get("entities") or []
        unit = data.get("unit") or "resident"
        if data.get("mixed_denominators"):
            units = " and per ".join(u.rstrip("s") for u in data["mixed_denominators"])
            svg = viz.refusal(
                f"Spending on {service_area}, per unit",
                f"Not drawn. This set mixes governments measured per {units}. A school "
                "district's population is its enrollment, so dividing by it gives "
                "spending per student, and that cannot share an axis with spending per "
                "resident. Compare in total dollars, or split the set by type.")
        elif not rows:
            svg = viz.refusal(
                f"Spending on {service_area} per {unit}",
                f"None of these entities can be put on a per-{unit} basis for this "
                "area: either they report nothing here, or they have no population in "
                "the data. Neither is a report of zero spending.")
        else:
            years = data["years"]
            span = (str(years[0]) if len(years) == 1
                    else f"{min(years)} to {max(years)}, one year each")
            single_type = len({r["gov_type"] for r in rows}) == 1
            svg = viz.horizontal_bars(
                f"{service_area}: spending per {unit}",
                f"{fmt.plural(len(rows), 'government')}, {span}",
                [{"label": r["label"], "value": r["value"],
                  "peer_median": r["peer_median"] if single_type else None,
                  "peer_degenerate": 0 if (single_type and r["peer_median"]) else 1}
                 for r in rows],
                formatter=fmt.rate,
                note=("Tick marks the median for this government type."
                      if single_type and any(r["peer_median"] for r in rows) else
                      "Mixed government types, so no single peer median applies."))
            table = [{"government": r["label"], "type": r["gov_type"],
                      f"per {unit}": fmt.rate(r["value"]),
                      "total": fmt.money(r["total"]),
                      f"{unit}s": fmt.count(r["population"]),
                      "share of its own budget": fmt.percent(r["share"]),
                      "vs type median": (f"{r['ratio']:.2f}×" if r["ratio"]
                                         else "not comparable"),
                      "year": r["year"]} for r in rows]

    elif form == "five_year_panel":
        source = explore.trend_panel(store, pid6_list, measure)
        data = source["data"]
        series = data.get("series") or []
        label = MEASURE_LABELS.get(measure, measure.title())
        if len(series) < 2:
            svg = viz.refusal(
                f"{label}, {data['start']} to {data['end']}",
                f"Fewer than two of these governments report more than one year "
                f"inside {data['start']} to {data['end']}. Most Oregon governments "
                "file two years five years apart rather than annually, so a "
                "five-year annual panel exists for about 380 of them. Compare the "
                "change between 2017 and 2022 instead, which nearly all can answer.")
        else:
            drawn = series[:4]
            note = (f"{data['annual']} of these are measured every year. "
                    if data["annual"] else "")
            if data["interpolated"]:
                note += ("Dashed lines are drawn between observations that are years "
                         "apart; the years between were not filed.")
            if len(series) > 4:
                note += (" Four lines drawn: "
                         + ", ".join(s["label"] for s in series[4:]) + " also requested.")
            svg = viz.multi_series(
                f"{label}: {fmt.plural(len(drawn), 'government')} compared",
                f"{data['start']} to {data['end']}, entity totals in nominal dollars",
                drawn, note=note.strip() or None)
            table = []
            for year in range(data["start"], data["end"] + 1):
                row = {"year": year}
                for entry in drawn:
                    value = dict(entry["points"]).get(year)
                    row[entry["label"]] = fmt.money(value) if value is not None else "—"
                table.append(row)

    elif form == "five_year_change":
        source = explore.compare_change(store, pid6_list, measure)
        data = source["data"]
        rows = data.get("entities") or []
        label = MEASURE_LABELS.get(measure, measure.title())
        if not rows:
            svg = viz.refusal(
                f"Change in {label.lower()}, {data['start']} to {data['end']}",
                f"None of these governments reports both {data['start']} and "
                f"{data['end']}, so there are no two ends to measure between. That "
                "is an absence of a filing, not a change of zero.")
        else:
            svg = viz.diverging_bars(
                f"{label}: change over {data['span']} years",
                f"{data['start']} to {data['end']}, nominal dollars",
                [{"label": r["label"], "value": r["value"],
                  "peer_median": r["peer_median"]} for r in rows],
                formatter=lambda v: f"{v:+.0f}%",
                note="Two observations five years apart. Nothing here says whether "
                     "the change was steady, front-loaded or reversed in between, "
                     "and no price index is in this data, so these are nominal.")
            table = [{"government": r["label"], "type": r["gov_type"],
                      f"{data['start']}": fmt.money(r["first"]),
                      f"{data['end']}": fmt.money(r["last"]),
                      "change": f"{r['value']:+.0f}%",
                      "type median change": (f"{r['peer_median']:+.0f}%"
                                             if r["peer_median"] is not None
                                             else "not comparable"),
                      "years reported": r["observations"]} for r in rows]

    elif form == "per_capita_over_time":
        source = explore.per_capita_over_time(store, pid6_list, indexed=indexed)
        data = source["data"]
        series = data.get("series") or []
        baseline = data.get("baseline")
        unit = data.get("unit") or "resident"
        if data.get("mixed_denominators"):
            units = " and per ".join(u.rstrip("s") for u in data["mixed_denominators"])
            svg = viz.refusal(
                "Spending per unit over time",
                f"Not drawn. This set mixes governments measured per {units}, which are "
                "different measures and cannot share an axis. Compare entity totals, or "
                "split the set by type.")
        elif len(series) < 2:
            svg = viz.refusal(
                f"Spending per {unit} over time",
                f"Fewer than two entities can be drawn: an entity needs both a {unit} "
                "count in the data and two or more years of spending. One year is a "
                "point, not a trend.")
        else:
            drawn = series[:4]
            years = sorted({p[0] for s in drawn for p in s["points"]})
            note = (f"The {unit} count is one estimate per entity and is held constant, "
                    "so what moves here is spending.")
            if len(series) > 4:
                note += " Four lines drawn: " + ", ".join(s["label"] for s in series[4:]) \
                    + " also requested."
            svg = viz.multi_series(
                (f"Spending per {unit}, indexed" if indexed
                 else f"Spending per {unit}"),
                (f"{years[0]} = 100, {len(drawn)} governments compared" if indexed
                 else f"{years[0]} to {years[-1]}, entity totals divided by {unit}s"),
                drawn,
                formatter=(lambda v: f"{v:,.0f}") if indexed else fmt.rate,
                note=note, reference=baseline,
                baseline=100 if indexed else None)
            table = []
            for year in years:
                row = {"year": year}
                for entry in drawn:
                    value = dict(entry["points"]).get(year)
                    row[entry["label"]] = (f"{value:,.0f}" if indexed and value is not None
                                           else fmt.rate(value))
                if baseline:
                    value = dict(baseline["points"]).get(year)
                    row[baseline["label"]] = (f"{value:,.0f}" if indexed and value is not None
                                              else fmt.rate(value))
                table.append(row)

    else:
        source = explore.compare_service_area(store, pid6_list, service_area)
        data = source["data"]
        rows = data.get("entities") or []
        if not rows:
            svg = viz.refusal(
                f"Spending on {service_area}",
                "None of these entities reports spending in this area. That usually "
                "means another government holds the responsibility, not that the "
                "service is unfunded.")
        else:
            years = data["years"]
            span = (str(years[0]) if len(years) == 1
                    else f"{min(years)} to {max(years)}, one year each")
            svg = viz.horizontal_bars(
                f"Spending on {service_area}",
                f"{fmt.plural(len(rows), 'government')}, {span}",
                [{"label": r["label"], "value": r["value"],
                  "peer_median": None, "peer_degenerate": 1} for r in rows],
                note="A snapshot, not a trend. Service-area spending exists for one "
                     "year per entity, so none of these bars can be tracked over time.")
            table = [{"government": r["label"], "type": r["gov_type"],
                      "spending": fmt.money(r["value"]),
                      "share of its own budget": fmt.percent(r["share"]),
                      "peer median share": fmt.percent(r["peer_median_share"])
                      if not r["peer_degenerate"] else "not comparable",
                      "year": r["year"]} for r in rows]

    # A government the reader picked and the chart could not draw has to leave a
    # mark on the drawing, not only in the rules panel. Dropping it silently is
    # the defect this whole comparison path was rebuilt to prevent.
    # Two totals in this data are both called expenditure and disagree for three
    # quarters of the corpus. Whenever one is drawn, the rule that says so travels.
    if measure in ("expenditure", "service_area_basis") and form in (
            "five_year_panel", "five_year_change", "entities_over_time"):
        source = dict(source, caveats=source["caveats"]
                      + [caveat("two_expenditure_measures")])

    unchartable = source["data"].get("unchartable") or []
    if unchartable and svg:
        svg = viz.append_note(
            svg, f"Not drawn: {', '.join(unchartable)} — neither a spending breakdown "
                 "nor a year of totals is reported. That is an absence, not a zero.")

    return envelope(
        "render_comparison",
        data={"form": form, "measure": measure, "service_area": service_area,
              "indexed": bool(indexed), "unchartable": unchartable,
              "svg": svg, "table": table, "refused": table is None,
              "detail": source["data"]},
        caveats=source["caveats"], blocked=source["not_computable"])


# Each entry is the SQL that produces one value per pid6, plus how to format it
# and what to call it. Fiscal stability is deliberately absent: a composite of two
# components that move for opposite reasons tells a reader less, mapped, than any
# single measure underneath it, and a choropleth of it invites exactly the ranking
# §8 says the score cannot support.
MAP_METRICS = {
    "spending_per_resident": (
        "SELECT o.pid6 AS pid6, o.total_expenditure * 1.0 / w.population AS value "
        "FROM operating_vs_capital o JOIN workforce_profile w ON w.pid6 = o.pid6 "
        "WHERE w.population > 0 AND o.total_expenditure > 0",
        fmt.rate, "Spending per resident"),
    "total_spending": (
        "SELECT pid6, total_expenditure AS value FROM operating_vs_capital "
        "WHERE total_expenditure > 0",
        fmt.money, "Total expenditure"),
    "capital_share": (
        "SELECT pid6, capital_pct AS value FROM operating_vs_capital "
        "WHERE capital_pct IS NOT NULL",
        fmt.percent, "Capital share of spending"),
    "employees_per_1000": (
        "SELECT pid6, employees_per_1000 AS value FROM workforce_profile "
        "WHERE employees_per_1000 > 0",
        fmt.rate, "Employees per 1,000 residents"),
    "average_wage": (
        "SELECT pid6, average_annual_wage AS value FROM workforce_profile "
        "WHERE average_annual_wage > 0",
        fmt.rate, "Average annual wage"),
}

# Service-area metrics need the area named, so they are parameterised separately.
SERVICE_AREA_MAP_METRICS = {
    "service_area_share": (
        "SELECT pid6, percentage AS value FROM spending_by_service_area "
        "WHERE service_area = ? AND percentage IS NOT NULL",
        fmt.percent, "Share of spending on"),
    "service_area_per_resident": (
        "SELECT s.pid6 AS pid6, s.total * 1.0 / w.population AS value "
        "FROM spending_by_service_area s JOIN workforce_profile w ON w.pid6 = s.pid6 "
        "WHERE s.service_area = ? AND w.population > 0 AND s.total > 0",
        fmt.rate, "Spending per resident on"),
}

# One level below the service area: the named function inside it. Drawn on
# operating plus capital, which is the basis the service-area shares above are
# computed against, so the two maps stack instead of being two measures of
# loosely the same thing.
FUNCTION_MAP_METRICS = {
    "function_share": (
        "SELECT pid6, pct_of_entity_total AS value FROM financial_functions "
        "WHERE function_name = ? AND pct_of_entity_total IS NOT NULL",
        fmt.percent, "Share of spending on"),
    "function_per_resident": (
        "SELECT f.pid6 AS pid6, (COALESCE(f.operating_expenditures, 0) "
        "     + COALESCE(f.capital_expenditures, 0)) * 1.0 / w.population AS value "
        "FROM financial_functions f JOIN workforce_profile w ON w.pid6 = f.pid6 "
        "WHERE f.function_name = ? AND w.population > 0 "
        "  AND COALESCE(f.operating_expenditures, 0) "
        "    + COALESCE(f.capital_expenditures, 0) > 0",
        fmt.rate, "Spending per resident on"),
}

# What a coverage figure is measured over, per family. Both tables hold one year
# per entity, which is why the vintage spread is part of what gets measured.
COVERAGE_SOURCES = {
    "service_area": ("spending_by_service_area", "COALESCE(t.total, 0)", "service_area"),
    "function": ("financial_functions",
                 "COALESCE(t.operating_expenditures, 0) "
                 "+ COALESCE(t.capital_expenditures, 0)", "function_name"),
}


def map_coverage(store, kind, selector, layer):
    """How much of the thing being mapped this layer can actually show.

    Two different measures, because they answer two different objections.

    `layer_share` is how much of the money local governments report on this
    measure sits in entities drawable on this layer. It is the §15.5 question:
    a map that leaves out the governments doing most of the work is not a map of
    the work. Fire protection is the case that motivated this. 243 of Oregon's
    fire districts report it and one of them has a boundary in this data, so a
    place map of fire protection draws the 83 cities with their own department
    and leaves rural Oregon blank, which reads as an absence of fire spending
    where it is really an absence of drawable fire districts.

    `holders` names who the missing money belongs to, by government type, and
    distinguishes the two reasons it is missing. A municipality absent from a
    county map has a boundary and is on another layer; a fire district has none
    at all and cannot appear on any map this data can draw. Collapsing those two
    into "no boundary" was wrong in the first draft of this and would have told
    a reader Oregon's cities are unmapped.

    The state is counted separately rather than in the denominator: it is never
    drawable on a local boundary layer, and folding it in would make every map
    of a state-heavy function look like a coverage failure when the real point
    is that another level of government does the work.
    """
    table, money, column = COVERAGE_SOURCES[kind]
    rows = store.rows(
        f"SELECT e.gov_type_name AS gov_type, g.layer AS layer, "
        f"       COUNT(DISTINCT t.pid6) AS n, SUM({money}) AS amount, "
        f"       MIN(t.year) AS first_year, MAX(t.year) AS last_year "
        f"FROM {table} t JOIN entities e ON e.pid6 = t.pid6 "
        f"LEFT JOIN geo_entity g ON g.pid6 = t.pid6 "
        f"WHERE t.{column} = ? AND {money} > 0 "
        f"GROUP BY e.gov_type_name, g.layer", selector)

    local = [r for r in rows if r["gov_type"] != "State"]
    total = sum(r["amount"] or 0 for r in local)
    on_layer = sum(r["amount"] or 0 for r in local if r["layer"] == layer)

    holders = {}
    for row in local:
        if row["layer"] == layer:
            continue
        # The recovered special-district layer is 19 boundaries from the
        # Multnomah pilot, spanning fire, water, transit and a community
        # college. It locates a government; it is not a layer anything can be
        # drawn against. Counting it as somewhere else to draw would answer "so
        # map it there" with a map of nineteen unrelated districts.
        elsewhere = row["layer"] if row["layer"] != "special_district" else None
        key = (row["gov_type"], elsewhere)
        entry = holders.setdefault(key, {
            "gov_type": row["gov_type"], "entities": 0, "amount": 0.0,
            # No boundary anywhere this can draw, as against a boundary on a
            # layer other than the one being drawn.
            "unmapped": elsewhere is None,
            "layer": elsewhere})
        entry["entities"] += row["n"]
        entry["amount"] += row["amount"] or 0

    state = next((r for r in rows if r["gov_type"] == "State"), None)
    years = sorted({y for r in rows for y in (r["first_year"], r["last_year"]) if y})

    return {
        "layer_share": (on_layer / total) if total else 0.0,
        "on_layer": on_layer,
        "total": total,
        "holders": sorted(holders.values(), key=lambda h: h["amount"], reverse=True),
        "state_reports": bool(state),
        "state_amount": (state["amount"] if state else None),
        "years": years,
    }


def entity_layer(store, pid6):
    """Which boundary layer a government can be drawn on, and its geo_id.

    Counties and places join exactly, school districts by name at 156 of 223,
    and special districts have no boundaries at all. Returns None where the
    entity cannot be drawn, so the caller offers a list rather than a map.
    """
    row = store.row(
        "SELECT g.layer, g.geo_id, e.gov_type_name, e.host_county_pid6 "
        "FROM geo_entity g JOIN entities e ON e.pid6 = g.pid6 WHERE g.pid6 = ?", pid6)
    return dict(row) if row else None


def render_function_map(store, function, county=None, highlight_pid6=None):
    """Map one named function on the layer that does the work.

    Whoever does the work decides the layer, not the caller's habit. Sewerage is
    overwhelmingly municipal, and drawing it on counties would compare a city's
    sewers against county governments that mostly run none, which is the
    like-with-like error §12 names, arriving as a picture rather than a
    sentence. Police is a city and county function and reads either way.

    Layers are tried in order of how much of the function's reported spending
    they hold, and the first one that survives render_map's coverage gate is
    returned. Where none survives, the refusal from the layer holding most of
    the work is what comes back, because that is the one whose reason names the
    governments actually doing it: for fire protection, 243 districts with no
    boundary in this data. A refusal from a layer that was never the right one
    would blame the wrong absence.
    """
    ranked = sorted(
        ("county", "place", "school_district"),
        key=lambda candidate: map_coverage(store, "function", function,
                                           candidate)["layer_share"],
        reverse=True)

    first = None
    for candidate in ranked:
        # A place map is dots at state scale, so it is scoped where the caller
        # has a county to scope it to, exactly as the peer maps are.
        drawn = render_map(store, candidate, "function_per_resident",
                           county=(county if candidate == "place" else None),
                           function=function, highlight_pid6=highlight_pid6)
        if not drawn["data"].get("refused"):
            return drawn
        first = first or drawn
    return first


def render_map(store, layer="county", metric="spending_per_resident", county=None,
               service_area=None, function=None, highlight_pid6=None):
    """Choropleth over a boundary layer.

    Memoized on its arguments. The store is read-only, so a map drawn twice with
    the same arguments is the same map, and a whole-corpus export asks for the
    statewide one once per entity.

    Only counties and places join to geometry exactly. School districts join by
    name and cover 156 of 223. Special districts have no boundaries at all, so
    they are not offerable as a layer: two thirds of Oregon's governments cannot
    be drawn, and the honest form for them is a list.

    `service_area` and `function` are the two depths of the same drill: an area
    is twelve Census categories, a function is the named work inside one. Both
    select what is drawn. Neither selects any words on the picture, and nothing
    here takes a title, a caption or a colour, because a caption is where a map
    asserts the relationship §4 forbids and the way to prevent that is to have
    nowhere to put one.

    Whether the map is drawn at all is decided by measurement rather than by
    whoever called it. See the coverage gate below.
    """
    cache = getattr(store, "_map_cache", None)
    if cache is None:
        cache = store._map_cache = {}
    signature = (layer, metric, county, service_area, function, highlight_pid6)
    if signature in cache:
        return cache[signature]

    if layer not in maps.LAYERS:
        return envelope("render_map", caveats=[{
            "code": "unknown_layer", "rule": "§9",
            "guidance": f"No layer '{layer}'. Available: {', '.join(maps.LAYERS)}. "
                        "Special districts have no boundaries in this data and cannot "
                        "be mapped; list them instead."}])
    coverage_kind = coverage_selector = None
    if metric in SERVICE_AREA_MAP_METRICS:
        if not service_area:
            return envelope("render_map", caveats=[{
                "code": "service_area_required", "rule": "§9",
                "guidance": f"'{metric}' maps one service area and needs to be told which."}])
        inner, formatter, label = SERVICE_AREA_MAP_METRICS[metric]
        arguments = (service_area, layer)
        label = f"{label} {service_area}"
        function = None
        coverage_kind, coverage_selector = "service_area", service_area
    elif metric in FUNCTION_MAP_METRICS:
        if not function:
            return envelope("render_map", caveats=[{
                "code": "function_required", "rule": "§9",
                "guidance": f"'{metric}' maps one named function and needs to be told "
                            "which. Functions are the level below service areas; drill "
                            "to get their names for an entity, or call who_spends_on."}])
        known = store.row("SELECT service_area FROM financial_functions "
                          "WHERE function_name=? LIMIT 1", function)
        if not known:
            return envelope("render_map", caveats=[{
                "code": "no_such_function", "rule": "§2",
                "guidance": f"No government reports spending on '{function}', so there "
                            "is nothing to draw. Check the name against a drill result."}])
        inner, formatter, label = FUNCTION_MAP_METRICS[metric]
        arguments = (function, layer)
        label = f"{label} {function}"
        service_area = known["service_area"]
        coverage_kind, coverage_selector = "function", function
    elif metric in MAP_METRICS:
        inner, formatter, label = MAP_METRICS[metric]
        arguments = (layer,)
        service_area = function = None
    else:
        return envelope("render_map", caveats=[{
            "code": "unknown_metric", "rule": "§4",
            "guidance": f"No mappable metric '{metric}'. Available: "
                        f"{', '.join(list(MAP_METRICS) + list(SERVICE_AREA_MAP_METRICS) + list(FUNCTION_MAP_METRICS))}."}])

    rows = store.rows(
        f"SELECT g.geo_id, m.value AS value, e.common_name, e.legal_name, "
        f"f.peer_group_mismatch FROM geo_entity g "
        f"JOIN entities e ON e.pid6=g.pid6 "
        f"JOIN entity_flags f ON f.pid6=g.pid6 "
        f"LEFT JOIN ({inner}) m ON m.pid6=g.pid6 WHERE g.layer=?", *arguments)

    values = {r["geo_id"]: r["value"] for r in rows if r["value"] is not None
              and not r["peer_group_mismatch"]}

    # 378 Oregon cities at state scale are dots, and comparing dot areas compares
    # city land area, which carries no meaning here. Scoping to one county is
    # what makes a place map readable.
    only = extent_note = None
    if county:
        host = store.row(
            "SELECT pid6, common_name, legal_name FROM entities WHERE gov_type_name='County' "
            "AND (lower(common_name) LIKE ? OR lower(legal_name) LIKE ?)",
            f"%{county.lower()}%", f"%{county.lower()}%")
        if not host:
            return envelope("render_map", caveats=[{
                "code": "county_not_found", "rule": "§14",
                "guidance": f"No county matching '{county}' is in the data."}])
        only = {r["geo_id"] for r in store.rows(
            "SELECT g.geo_id FROM geo_entity g JOIN entities e ON e.pid6=g.pid6 "
            "WHERE g.layer=? AND e.host_county_pid6=?", layer, host["pid6"])}
        extent_note = f"{host['common_name'] or host['legal_name']} only"

    total = store.row("SELECT COUNT(*) AS n FROM entities WHERE gov_type_name IN "
                      "('County','Municipal','School District','Special District')")["n"]

    # ---- the coverage gate ------------------------------------------------
    #
    # §15.1: a map is drawn only where coverage is high enough that the picture
    # is not mostly absence. Measured on the extent actually drawn, so scoping a
    # place map to one county judges that county rather than the state.
    #
    # This is the whole reason the drill from service area to function needs a
    # gate at all. An area like Public Safety is reported by nearly every
    # general-purpose government, so the county map is dense and the colour
    # differences are differences in spending. One level down the functions
    # separate by who does the work: police is a city and county function and
    # draws at 34 of 36 counties, while fire protection is done by districts and
    # draws at one. The same map code, the same measure, and one of the two is a
    # picture of Oregon's fire spending while the other is a picture of which
    # governments happen to have a boundary in this file.
    collection, geo_key = maps.load_layer(layer)
    extent = {feature["properties"].get(geo_key)
              for feature in collection["features"] if feature["geometry"]}
    if only is not None:
        extent &= only
    drawn = len(extent & set(values))
    density = (drawn / len(extent)) if extent else 0.0

    coverage = (map_coverage(store, coverage_kind, coverage_selector, layer)
                if coverage_kind else None)

    caveats = [caveat("inputs_not_outcomes")]

    if coverage:
        missing = ", ".join(
            f"{fmt.count(h['entities'])} {h['gov_type']} "
            f"{'entity' if h['entities'] == 1 else 'entities'} holding "
            f"{fmt.money(h['amount'])} "
            + ("with no boundary in this data" if h["unmapped"]
               else f"drawn on the {h['layer'].replace('_', ' ')} layer instead")
            for h in coverage["holders"][:3])
        share_line = (
            f"{fmt.percent(100 * coverage['layer_share'])} of what Oregon's local "
            f"governments report spending on {coverage_selector} sits in entities "
            f"drawable on this layer.")
        if coverage["holders"]:
            share_line += f" The rest is {missing}."
        if coverage["state_reports"]:
            share_line += (f" The state reports {fmt.money(coverage['state_amount'])} on "
                           "this as well, on no local boundary at all.")
        caveats.append({
            "code": ("map_layer_holds_minority"
                     if coverage["layer_share"] < THRESHOLDS["map_layer_share_partial"]
                     else "map_layer_share"),
            "rule": "§15",
            "guidance": share_line + " Name the share when describing this map, and never "
                        "call it a picture of what Oregon spends on this."})
        # Both source tables hold one year per entity and the year differs
        # between entities, so a single map is several vintages at once. §8 says
        # figures from different moments are not one moment.
        if len(coverage["years"]) > 1:
            caveats.append({
                "code": "map_years_differ", "rule": "§8",
                "guidance": f"These figures span {coverage['years'][0]} to "
                            f"{coverage['years'][-1]}, one year per government rather than "
                            "one year across the map. Neighbouring boundaries can be two "
                            "years apart, so read this as a level and not as a moment."})


    if layer == "school_district":
        caveats.append({
            "code": "geometry_by_name", "rule": "§9",
            "guidance": "School district boundaries are matched by name, not by a shared "
                        "identifier, and cover 156 of 223 districts. Say the map is partial."})
    caveats.append({
        "code": "map_is_partial", "rule": "§9",
        "guidance": f"This layer covers one government type. Oregon has {total} local "
                    "governments across four types, and special districts, the largest "
                    "group, have no boundaries in this data. Never describe a single-layer "
                    "map as showing public spending in a place."})

    if layer == "place" and not county:
        caveats.append({
            "code": "place_map_unreadable_statewide", "rule": "§9",
            "guidance": "At state scale Oregon's cities render as dots, and their polygon "
                        "areas are land area rather than anything measured here. Scope the "
                        "map to a county, or use a list."})

    if coverage_selector:
        caveats.append({
            "code": "absence_is_not_zero", "rule": "§9",
            "guidance": f"A boundary with no value reports nothing on {coverage_selector}, "
                        "which is not a report of zero. It usually means another government "
                        "holds that responsibility here. The no-data colour is off the ramp "
                        "for exactly this reason; do not read it as low."})
    if metric in ("spending_per_resident", "service_area_per_resident",
                  "function_per_resident", "employees_per_1000"):
        caveats.append({
            "code": "population_is_one_estimate", "rule": "§8",
            "guidance": "The denominator is a single population estimate per entity, not a "
                        "series. This is a level at one moment and carries no rate of change."})

    thin = len(extent) < THRESHOLDS["map_min_polygons"]
    # A statewide place map is 378 cities rendered as specks, and what a reader
    # compares is their land area. The rule was already stated as a caveat and
    # the picture was drawn anyway, which is a caveat doing no work: drawn out,
    # sewerage across Oregon is a scatter of dots you cannot read a value from.
    # Every caller in this product already scopes place maps to a county, so
    # this refuses what none of them asks for and what §15.1 does not allow.
    unscoped_places = layer == "place" and not county
    if thin or unscoped_places or density < THRESHOLDS["map_density_minimum"]:
        # Refused rather than drawn, and the reason is drawn in its place: a
        # blank frame reads as a failure, a stated reason reads as a rule.
        # Which rule stopped it, so a caller can say why rather than guess. The
        # three refusals mean different things: mostly-absence is a fact about
        # who does the work, a thin extent is a fact about the extent, and
        # needing a county is only a fact about scale.
        #
        # Tested in that order, because the weakest reason is the one that reads
        # as most fixable. Ordered the other way, fire protection came back as
        # "scope it to a county" when the truth is that 243 of the districts
        # doing it have no boundary at any scale, and a reader who scoped it
        # would get an emptier map and the same silence about why.
        because = ("mostly_absence" if density < THRESHOLDS["map_density_minimum"]
                   else "thin_extent" if thin else "needs_a_county")
        if because == "needs_a_county":
            reason = ("Oregon's 378 cities render as specks at state scale, so this map "
                      "would compare land areas rather than the measure. Name a county to "
                      "scope it to, or read the list instead. The governments are here; "
                      "the state-wide picture of them is not a picture.")
        elif because == "thin_extent":
            # §15.5: a choropleth of two or three shapes is a chart of a couple
            # of numbers wearing a map's clothes, and the shapes carry land area
            # rather than anything measured. A scoped place map hits this in the
            # rural counties, where the extent is one or two incorporated cities.
            reason = (f"{len(extent)} boundaries fall inside this extent, which is too few "
                      "to read as a map: the shapes would carry land area rather than the "
                      "measure. A table of the same values says it without the picture "
                      "claiming a geography.")
        else:
            reason = (f"{drawn} of {len(extent)} boundaries on this layer report "
                      f"{coverage_selector or 'this measure'}, so most of this map would be "
                      "the no-data colour and the few that carry a value would read as the "
                      "pattern.")
            if coverage and coverage["holders"]:
                reason += f" The work is done by {missing}."
            reason += " Ask who spends on it instead, which lists them all."
        result = envelope(
            "render_map",
            data={"layer": layer, "metric": metric, "county": county,
                  "service_area": service_area, "function": function,
                  "refused": True, "refused_because": because,
                  "covered": drawn, "polygons": len(extent), "density": density,
                  "layer_share": coverage["layer_share"] if coverage else None,
                  "holders": coverage["holders"] if coverage else [],
                  "svg": viz.refusal(f"{label} by {layer.replace('_', ' ')}", reason),
                  "table": []},
            caveats=caveats + [{
                "code": "map_mostly_absence", "rule": "§15",
                "guidance": reason + " Do not describe this as a low-spending region, and "
                            "do not draw it on a different layer to get around the gap: "
                            "the governments are missing, not their figures."}])
        cache[signature] = result
        return result

    highlight = None
    if highlight_pid6:
        found = store.row(
            "SELECT geo_id FROM geo_entity WHERE pid6 = ? AND layer = ?",
            highlight_pid6, layer)
        highlight = found["geo_id"] if found else None
        if not highlight:
            caveats.append({
                "code": "highlight_unavailable", "rule": "§9",
                "guidance": "This government has no boundary on this layer, so it is not "
                            "outlined on the map. Say where it sits in words instead."})

    result = maps.choropleth(
        layer, values,
        f"{label} by {layer.replace('_', ' ')}",
        extent_note or "Oregon, one government type only",
        formatter=formatter, only=only, highlight=highlight)

    listed = sorted((r for r in rows if r["value"] is not None
                     and (only is None or r["geo_id"] in only)),
                    key=lambda r: r["value"], reverse=True)

    cache[signature] = envelope(
        "render_map",
        data={"layer": layer, "metric": metric, "county": county,
              "service_area": service_area, "function": function,
              "highlighted": bool(highlight), "refused": False,
              "svg": result["svg"],
              "covered": result["covered"], "polygons": result["polygons"],
              "density": density,
              "layer_share": coverage["layer_share"] if coverage else None,
              "holders": coverage["holders"] if coverage else [],
              "table": [{"name": r["common_name"] or r["legal_name"],
                         "value": formatter(r["value"])}
                        for r in listed][:60]},
        caveats=caveats)
    return cache[signature]


TOOLS["render_chart"] = render_chart
TOOLS["render_map"] = render_map
TOOLS["render_function_map"] = render_function_map
TOOLS["render_comparison"] = render_comparison


# The drill-down and peer tools live in explore, which imports this module, so
# they are bound here rather than defined here.
def _bind_explore():
    import explore
    for name in ("drill", "compare_to_peer_group", "compare_entities_over_time",
                 "compare_service_area", "per_capita_by_service_area",
                 "per_capita_over_time", "who_spends_on", "staffing",
                 "revenue_mix", "debt_load", "compare_change", "trend_panel",
                 "cost_per_head"):
        TOOLS[name] = getattr(explore, name)


_bind_explore()


def compose_report(store, kind="entity", pid6=None, county_name=None,
                   pid6_list=None, basis="absolute", service_area=None):
    """Plan a multi-section report. Sections come back in §5 precedence order."""
    import reports
    return reports.compose_report(store, kind=kind, pid6=pid6, county_name=county_name,
                                  pid6_list=pid6_list, basis=basis,
                                  service_area=service_area)


def compare_normalized(store, pid6_list, service_area=None, basis="absolute"):
    """Cross-entity comparison on a stated basis (§10)."""
    import reports
    return reports.compare_normalized(store, pid6_list, service_area, basis)


TOOLS["compose_report"] = compose_report
TOOLS["compare_normalized"] = compare_normalized


# ------------------------------------------------------ electoral districts

_DISTRICT_LAYER = {}


def _district_features(build=None):
    """The dissolved electoral districts, loaded once."""
    import maps
    root = build or maps.BUILD
    if root not in _DISTRICT_LAYER:
        path = os.path.join(root, "geo", "electoral_districts.geojson")
        try:
            with open(path) as handle:
                _DISTRICT_LAYER[root] = json.load(handle)["features"]
        except (OSError, ValueError):
            _DISTRICT_LAYER[root] = []
    return _DISTRICT_LAYER[root]


def _seat_noun(role):
    """What to call one holder of this role in a sentence.

    The Census role names are already noun phrases and several end in "District",
    so appending "districts" to them produces "community college district
    director districts". Trimming the leading jurisdiction words leaves the job.
    """
    text = re.sub(r"^(Community College|Education Service|Fire Protection|"
                  r"Water Supply Utility|Electric Power Utility|Parks and Recreation|"
                  r"Soil and Water Conservation|Law Enforcement|Library|Irrigation|"
                  r"Flood Control|Emergency Communications)\s+District\s+", "",
                  role or "")
    return (text or role or "seat").lower()


def render_office_map(store, pid6, role=None, seat=None):
    """Draw the ground that elects a seat on this government's governing body.

    Two sources, and which one applies is decided by how the seat is filled
    rather than by what happens to be available:

        at large    the electorate is the whole jurisdiction, so the government's
                    own boundary *is* the district. That covers 2,382 of the
                    seats tied to an entity, statewide, from layers already here.

        by district the seat answers to a ward, zone or board district inside the
                    jurisdiction, and only a source that records assignments per
                    polygon can recover it. The Multnomah precinct file is the
                    only one of those in this repository, so a districted body
                    outside that county is refused with the reason rather than
                    drawn as though it were at large — which would tell a reader
                    they elect every seat when they elect one.
    """
    import maps

    entity = _entity(store, pid6)
    if not entity:
        return envelope("render_office_map", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    offices = get_offices(store, pid6)
    roles = offices["data"].get("roles") or []
    if not roles:
        return envelope("render_office_map", entity=entity, caveats=offices["caveats"])

    chosen = next((r for r in roles if r["role"] == role), None) if role else None
    if chosen is None:
        # The body worth drawing is the one with seats to tell apart. A sheriff
        # is one seat over the whole county and a map of it says only where the
        # county is, which the reader already knows.
        chosen = max(roles, key=lambda r: (r["seat_count"], r["ballot"] == BY_DISTRICT))

    name = entity["common_name"] or entity["legal_name"]
    caveats = [caveat("offices_have_no_holders")]

    if chosen["ballot"] in (BY_DISTRICT, MIXED):
        wanted = {s["ocd_id"] for s in chosen["seats"] if s["district"]}
        by_ocd = {f["properties"]["ocd_id"]: f for f in _district_features()
                  if f["properties"].get("ocd_id")}
        features = []
        for entry in chosen["seats"]:
            match = by_ocd.get(entry["ocd_id"])
            if match:
                features.append({"label": (entry["district"] or "").split()[-1],
                                 "name": f"{chosen['role']}, {entry['district']}",
                                 "geometry": match["geometry"],
                                 "outline": match.get("outline")})
        if not features:
            caveats.append({
                "code": "no_district_geometry", "rule": "§9",
                "guidance": f"{name} fills {chosen['role']} seats by district, and no "
                            "boundary for those districts is in this data. District "
                            "assignments are recorded per polygon only for Multnomah "
                            "County here. Say the seats are districted and that the "
                            "map is missing; do not draw the whole jurisdiction, which "
                            "would say a reader elects every seat."})
            return envelope(
                "render_office_map", entity=entity,
                data={"role": chosen["role"], "ballot": chosen["ballot"],
                      "seat_count": chosen["seat_count"], "svg": None,
                      "districts": 0, "source": None},
                caveats=caveats)

        drawn = maps.districts(
            features,
            f"{name}: who elects each {_seat_noun(chosen['role'])}",
            f"{fmt.plural(chosen['seat_count'], 'seat')}, each elected by one district",
            note="Each shape elects one seat. A reader votes in the one covering "
                 "their address, not in all of them.",
            coverage_note=("Boundaries are dissolved from Multnomah precinct splits "
                           "and stop at the county line."
                           if len(features) < len(wanted) else None))
        caveats.append({
            "code": "district_boundaries_are_approximate", "rule": "§8",
            "guidance": "These edges are precinct edges recovered from a March 2024 "
                        "precinct-split file, not the district's own filed boundary. "
                        "They are close enough to show which district covers a "
                        "neighbourhood and not close enough to settle an address."})
        source = "multnomah_precinct_dissolve"
    else:
        placed = entity_layer(store, pid6)
        if not placed:
            caveats.append({
                "code": "no_boundary", "rule": "§9",
                "guidance": f"{name} has no boundary in this data, so the electorate "
                            "for these seats cannot be drawn. Special districts have "
                            "no boundaries here at all and school districts join by "
                            "name at 156 of 223. Name the seats instead of drawing "
                            "them; a missing outline is not a missing government."})
            return envelope(
                "render_office_map", entity=entity,
                data={"role": chosen["role"], "ballot": chosen["ballot"],
                      "seat_count": chosen["seat_count"], "svg": None,
                      "districts": 0, "source": None},
                caveats=caveats)

        collection, key = maps.load_layer(placed["layer"])
        feature = next((f for f in collection["features"]
                        if f["properties"].get(key) == placed["geo_id"]), None)
        if not feature or not feature.get("geometry"):
            caveats.append({
                "code": "no_boundary", "rule": "§9",
                "guidance": f"{name} has no boundary in this data, so the electorate "
                            "for these seats cannot be drawn. Special districts have "
                            "no boundaries here at all and school districts join by "
                            "name at 156 of 223. Name the seats instead of drawing "
                            "them; a missing outline is not a missing government."})
            return envelope(
                "render_office_map", entity=entity,
                data={"role": chosen["role"], "ballot": chosen["ballot"],
                      "seat_count": chosen["seat_count"], "svg": None,
                      "districts": 0, "source": None},
                caveats=caveats)

        # One shape, because one electorate. The seat count goes in the sentence
        # rather than into shapes, since drawing five identical outlines for five
        # at-large seats would invent five geographies that do not exist.
        drawn = maps.districts(
            [{"label": "", "name": name, "geometry": feature["geometry"]}],
            f"{name}: who elects the {_seat_noun(chosen['role'])}",
            f"{fmt.plural(chosen['seat_count'], 'seat')}, elected at large",
            note=f"One electorate. Every voter inside this boundary votes on all "
                 f"{fmt.plural(chosen['seat_count'], 'seat')}; the position numbers "
                 "are ballot labels, not places.")
        caveats.append({
            "code": "at_large_is_one_electorate", "rule": "§7",
            "guidance": f"These {chosen['seat_count']} seats are filled at large on the "
                        "evidence here, so the map is the jurisdiction and the numbered "
                        "positions are not geographies. Do not describe a position as "
                        "covering part of the place."})
        source = placed["layer"]

    # The office caveats travel with this because the map is about those seats,
    # but a code carried by both would be stated twice in one answer.
    seen = {c["code"] for c in caveats}
    caveats += [c for c in offices["caveats"] if c["code"] not in seen]

    return envelope(
        "render_office_map", entity=entity,
        data={"role": chosen["role"], "ballot": chosen["ballot"],
              "seat_count": chosen["seat_count"], "svg": drawn["svg"],
              "districts": drawn["districts"], "source": source,
              "districts_named": [f["label"] for f in
                                  (features if chosen["ballot"] in (BY_DISTRICT, MIXED)
                                   else [])]},
        caveats=caveats)


TOOLS["render_office_map"] = render_office_map


def render_locator_map(store, pid6):
    """Where in Oregon this government is, for every government.

    Three cases, and the difference between them is the point rather than an
    implementation detail:

        own boundary        counties, cities, 156 school districts and the 19
                            special districts the Multnomah pilot recovered.
                            The shape drawn is the government.

        measured extent     29 districts whose service area was established from
                            precinct assignments. Drawn as the counties it was
                            measured to operate in, which for several is not the
                            county it files under.

        filed county        everybody else, which is most of the corpus. Drawn as
                            the county the fiscal data files it under, and said
                            to be a filing — §13.11: published data assigns a
                            district to the county holding most of its assessed
                            value, so this locates the paperwork, not the service.

    A reader looking at a rural fire district still wants to know where in Oregon
    it is, and "no boundary in this data" answered that with nothing.
    """
    import maps

    entity = _entity(store, pid6)
    if not entity:
        return envelope("render_locator_map", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    name = entity["common_name"] or entity["legal_name"]
    counties, county_key = maps.load_layer("county")
    base = [f for f in counties["features"] if f["geometry"]]

    placed = entity_layer(store, pid6)
    if placed:
        # A county's own boundary is one of the base shapes, so it is picked out
        # of the same layer rather than treated as having no geometry.
        collection, key = maps.load_layer(placed["layer"])
        shape = next((f for f in collection["features"]
                      if f["properties"].get(key) == placed["geo_id"]
                      and f["geometry"]), None)
        if shape:
            recovered = placed["layer"] == "special_district"
            note = (f"{name} drawn inside Oregon. "
                    + ("The boundary is recovered from Multnomah precinct "
                       "assignments rather than filed by the district, so the edges "
                       "are precinct edges and stop at the county line."
                       if recovered else
                       "The boundary is the published one for this government type."))
            # Its own outline against a base of itself carries no information, so
            # the other counties stay as context and this one is picked out.
            base_shapes = ([f for f in base if f["properties"].get(county_key)
                            != placed["geo_id"]] if placed["layer"] == "county"
                           else base)
            drawn = maps.locator(base_shapes, [dict(shape, name=name)],
                                 f"Where {name} is",
                                 f"{entity['gov_type_name']}, drawn against Oregon's "
                                 "36 counties", note=note)
            return envelope(
                "render_locator_map", entity=entity,
                data={"basis": "own_boundary", "layer": placed["layer"],
                      "recovered": recovered, "svg": drawn["svg"],
                      "counties": [], "drawn": drawn["drawn"]},
                caveats=([{
                    "code": "boundary_is_recovered", "rule": "§8",
                    "guidance": "This boundary was reconstructed from precinct "
                                "assignments, not filed by the district. The edges "
                                "are precinct edges and stop at the Multnomah county "
                                "line, so it shows roughly where the district is and "
                                "does not settle an address."}] if recovered else []))

    # No boundary of its own. Fall back to the counties it touches, and say which
    # kind of touching this is.
    measured = [row["serves_county"] for row in store.rows(
        "SELECT DISTINCT serves_county FROM service_extent WHERE pid6=?", pid6)]
    filed = (entity["host_county"] or "").upper()
    wanted = {c.upper() for c in measured} | ({filed} if filed else set())
    if not wanted:
        return envelope(
            "render_locator_map", entity=entity,
            data={"basis": None, "svg": None, "counties": [], "drawn": 0},
            caveats=[{
                "code": "no_location", "rule": "§9",
                "guidance": f"{name} has neither a boundary nor a county in this data, "
                            "so it cannot be placed at all. Say that rather than "
                            "implying it is nowhere."}])

    shapes = [dict(f, name=f["properties"].get("NAME", ""))
              for f in base
              if (f["properties"].get("NAME") or "").upper() in wanted]
    if not shapes:
        return envelope(
            "render_locator_map", entity=entity,
            data={"basis": None, "svg": None, "counties": sorted(wanted), "drawn": 0},
            caveats=[{
                "code": "no_location", "rule": "§9",
                "guidance": f"The county named for {name} does not match a boundary "
                            "here, so it cannot be placed."}])

    if measured:
        note = (f"{name} has no boundary in this data. Highlighted are the counties it "
                "was measured to operate in, established from precinct assignments "
                "rather than from its filing. This is where it serves, not its edge.")
        basis = "measured_extent"
    else:
        note = (f"{name} has no boundary in this data. Highlighted is the county it "
                "files under, which published data assigns by where most of its "
                "assessed value sits — so this locates the filing and may be smaller "
                "or larger than where the district actually operates.")
        basis = "filed_county"

    drawn = maps.locator(
        base, shapes, f"Where {name} is filed" if basis == "filed_county"
        else f"Where {name} operates",
        f"{entity['gov_type_name']}, located by "
        + ("measured service extent" if measured else "county of filing"),
        note=note)

    caveats = [{
        "code": "located_not_bounded", "rule": "§9",
        "guidance": "This map places the government; it is not the government's "
                    "boundary. Never describe the highlighted county as the district's "
                    "extent, and never read area off it."}]
    if basis == "filed_county":
        caveats.append({
            "code": "host_county_is_a_filing", "rule": "§13",
            "guidance": "Published data files a special district under the county "
                        "holding most of its assessed value, so a district serving "
                        "several counties appears in one. The Multnomah pilot found "
                        "districts serving that county from a Washington County "
                        "filing. Do not treat the host county as the service area."})

    return envelope(
        "render_locator_map", entity=entity,
        data={"basis": basis, "layer": None, "recovered": False,
              "svg": drawn["svg"], "counties": sorted(wanted),
              "drawn": drawn["drawn"]},
        caveats=caveats)


TOOLS["render_locator_map"] = render_locator_map


def _bind_community():
    """Community context is its own module so it cannot see a fiscal figure.

    Bound here rather than imported at the top for the same reason explore is:
    community imports tools, and tools registers community's functions.
    """
    import community
    for name in ("community_context", "since_pandemic", "render_community_map"):
        TOOLS[name] = getattr(community, name)


_bind_community()


def _bind_brief():
    """The place-and-issue brief composes the tools above, so it binds last."""
    import brief
    TOOLS["place_topic_brief"] = brief.place_topic_brief


_bind_brief()
