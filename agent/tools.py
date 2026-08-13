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

import os
import re
import sqlite3

from rules import (THRESHOLDS, TOPIC_CONCORDANCE, caveat, not_computable)

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


# ---------------------------------------------------------------- offices

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

    by_role = {}
    for row in rows:
        by_role.setdefault(row["role"], []).append(row["office_position"])

    caveats = [caveat("offices_have_no_holders"), caveat("office_coverage_uneven")]
    confidences = {r["confidence"] for r in rows}
    if confidences & {"low"}:
        caveats.append(caveat("office_match_low_confidence"))
    if any((r["office_position"] or "") for r in rows):
        caveats.append({
            "code": "seats_not_roles", "rule": "§7",
            "guidance": "Multi-member bodies have many seats of the same role. Never present "
                        "a seat as a role or imply the body has one member."})

    return envelope(
        "get_offices", entity=entity,
        data={"roles": [{"role": role, "seats": seats, "seat_count": len(seats)}
                        for role, seats in sorted(by_role.items())],
              "total_seats": len(rows),
              "match_confidence": sorted(confidences)},
        caveats=caveats)


# ----------------------------------------------------------------- topics

def map_topic_to_categories(store, topic):
    """§12: name the gap, offer the proxy, say how loose the fit is."""
    key = topic.strip().lower()
    entry = next((v for k, v in TOPIC_CONCORDANCE.items() if k in key), None)

    caveats = [caveat("topic_gap"), caveat("topic_no_dollar_figure")]
    if "homeless" in key:
        caveats.append(caveat("dp03_cannot_measure_homelessness"))

    if not entry:
        return envelope(
            "map_topic_to_categories",
            data={"topic": topic, "categories": [], "fit": "none",
                  "note": "No category in this data corresponds to that topic."},
            caveats=caveats,
            blocked=[not_computable("issue_level_spending")])

    categories, fit, note = entry
    return envelope(
        "map_topic_to_categories",
        data={"topic": topic, "categories": categories, "fit": fit, "note": note},
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
                    note="Bars are operating plus capital. Vendor addressable is smaller.")
                table = [{"function": f["label"], "spending": fmt.money(f["value"]),
                          "vendor addressable": fmt.money(f["addressable"]),
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
        caveats = [caveat("inputs_not_outcomes")]
        if len(rows) < 2:
            svg = viz.refusal(f"{name}: revenue and spending over time",
                              "Fewer than two years are available, so there is no series to "
                              "draw. One year is a point, not a trend.")
        else:
            svg = viz.time_series(
                f"{name}: revenue and spending over time",
                f"{rows[0]['year']} to {rows[-1]['year']}, entity totals",
                [{"label": "Revenue", "points": [(r["year"], r["revenue"]) for r in rows]},
                 {"label": "Spending", "points": [(r["year"], r["expenditure"]) for r in rows]}])
            table = [{"year": r["year"], "revenue": fmt.money(r["revenue"]),
                      "spending": fmt.money(r["expenditure"])} for r in rows]

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
                    "per_capita_by_service_area", "per_capita_over_time")

MEASURE_LABELS = {"expenditure": "Total spending", "revenue": "Total revenue",
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
        if not rows:
            svg = viz.refusal(
                f"Spending on {service_area} per resident",
                "None of these entities can be put on a per-resident basis for this "
                "area: either they report nothing here, or they have no population in "
                "the data. Neither is a report of zero spending.")
        else:
            years = data["years"]
            span = (str(years[0]) if len(years) == 1
                    else f"{min(years)} to {max(years)}, one year each")
            single_type = len({r["gov_type"] for r in rows}) == 1
            svg = viz.horizontal_bars(
                f"{service_area}: spending per resident",
                f"{len(rows)} governments, {span}",
                [{"label": r["label"], "value": r["value"],
                  "peer_median": r["peer_median"] if single_type else None,
                  "peer_degenerate": 0 if (single_type and r["peer_median"]) else 1}
                 for r in rows],
                formatter=fmt.rate,
                note=("Tick marks the median for this government type."
                      if single_type and any(r["peer_median"] for r in rows) else
                      "Mixed government types, so no single peer median applies."))
            table = [{"government": r["label"], "type": r["gov_type"],
                      "per resident": fmt.rate(r["value"]),
                      "total": fmt.money(r["total"]),
                      "population": fmt.count(r["population"]),
                      "share of its own budget": fmt.percent(r["share"]),
                      "vs type median": (f"{r['ratio']:.2f}×" if r["ratio"]
                                         else "not comparable"),
                      "year": r["year"]} for r in rows]

    elif form == "per_capita_over_time":
        source = explore.per_capita_over_time(store, pid6_list, indexed=indexed)
        data = source["data"]
        series = data.get("series") or []
        baseline = data.get("baseline")
        if len(series) < 2:
            svg = viz.refusal(
                "Spending per resident over time",
                "Fewer than two entities can be drawn: an entity needs both a population "
                "in the data and two or more years of spending. One year is a point, "
                "not a trend.")
        else:
            drawn = series[:4]
            years = sorted({p[0] for s in drawn for p in s["points"]})
            note = ("Population is one estimate per entity and is held constant, so what "
                    "moves here is spending.")
            if len(series) > 4:
                note += " Four lines drawn: " + ", ".join(s["label"] for s in series[4:]) \
                    + " also requested."
            svg = viz.multi_series(
                ("Spending per resident, indexed" if indexed
                 else "Spending per resident"),
                (f"{years[0]} = 100, {len(drawn)} governments compared" if indexed
                 else f"{years[0]} to {years[-1]}, entity totals divided by population"),
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
                f"{len(rows)} governments, {span}",
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

    return envelope(
        "render_comparison",
        data={"form": form, "measure": measure, "service_area": service_area,
              "indexed": bool(indexed),
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


def render_map(store, layer="county", metric="spending_per_resident", county=None,
               service_area=None):
    """Choropleth over a boundary layer.

    Memoized on its arguments. The store is read-only, so a map drawn twice with
    the same arguments is the same map, and a whole-corpus export asks for the
    statewide one once per entity.

    Only counties and places join to geometry exactly. School districts join by
    name and cover 156 of 223. Special districts have no boundaries at all, so
    they are not offerable as a layer: two thirds of Oregon's governments cannot
    be drawn, and the honest form for them is a list.
    """
    cache = getattr(store, "_map_cache", None)
    if cache is None:
        cache = store._map_cache = {}
    signature = (layer, metric, county, service_area)
    if signature in cache:
        return cache[signature]

    if layer not in maps.LAYERS:
        return envelope("render_map", caveats=[{
            "code": "unknown_layer", "rule": "§9",
            "guidance": f"No layer '{layer}'. Available: {', '.join(maps.LAYERS)}. "
                        "Special districts have no boundaries in this data and cannot "
                        "be mapped; list them instead."}])
    if metric in SERVICE_AREA_MAP_METRICS:
        if not service_area:
            return envelope("render_map", caveats=[{
                "code": "service_area_required", "rule": "§9",
                "guidance": f"'{metric}' maps one service area and needs to be told which."}])
        inner, formatter, label = SERVICE_AREA_MAP_METRICS[metric]
        arguments = (service_area, layer)
        label = f"{label} {service_area}"
    elif metric in MAP_METRICS:
        inner, formatter, label = MAP_METRICS[metric]
        arguments = (layer,)
        service_area = None
    else:
        return envelope("render_map", caveats=[{
            "code": "unknown_metric", "rule": "§4",
            "guidance": f"No mappable metric '{metric}'. Available: "
                        f"{', '.join(list(MAP_METRICS) + list(SERVICE_AREA_MAP_METRICS))}."}])

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

    caveats = [caveat("inputs_not_outcomes")]
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

    if service_area:
        caveats.append({
            "code": "absence_is_not_zero", "rule": "§9",
            "guidance": f"A boundary with no value reports nothing on {service_area}, which "
                        "is not a report of zero. It usually means another government holds "
                        "that responsibility here. The no-data colour is off the ramp for "
                        "exactly this reason; do not read it as low."})
    if metric in ("spending_per_resident", "service_area_per_resident",
                  "employees_per_1000"):
        caveats.append({
            "code": "population_is_one_estimate", "rule": "§8",
            "guidance": "The denominator is a single population estimate per entity, not a "
                        "series. This is a level at one moment and carries no rate of change."})

    result = maps.choropleth(
        layer, values,
        f"{label} by {layer.replace('_', ' ')}",
        extent_note or "Oregon, one government type only",
        formatter=formatter, only=only)

    listed = sorted((r for r in rows if r["value"] is not None
                     and (only is None or r["geo_id"] in only)),
                    key=lambda r: r["value"], reverse=True)

    cache[signature] = envelope(
        "render_map",
        data={"layer": layer, "metric": metric, "county": county,
              "service_area": service_area, "svg": result["svg"],
              "covered": result["covered"], "polygons": result["polygons"],
              "table": [{"name": r["common_name"] or r["legal_name"],
                         "value": formatter(r["value"])}
                        for r in listed][:60]},
        caveats=caveats)
    return cache[signature]


TOOLS["render_chart"] = render_chart
TOOLS["render_map"] = render_map
TOOLS["render_comparison"] = render_comparison


# The drill-down and peer tools live in explore, which imports this module, so
# they are bound here rather than defined here.
def _bind_explore():
    import explore
    for name in ("drill", "compare_to_peer_group", "compare_entities_over_time",
                 "compare_service_area", "per_capita_by_service_area",
                 "per_capita_over_time"):
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
