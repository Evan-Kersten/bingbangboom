"""Drill-down, peer context and cross-entity comparison.

Three levels, which is as deep as the data goes and no deeper:

    service area   twelve Census functional categories, with the entity's share
                   and its peer group's distribution
    function       thirty-six functions inside those areas, each with the peer
                   distribution for the same area
    line item      how one function's total decomposes: operating, capital,
                   personnel, amounts excluded, and the vendor-addressable
                   remainder

Peer context comes from peer_stats, computed over the entity set rather than
shipped in the payload, so every level can say where a figure sits among its
peers instead of only reporting the figure.

One thing this module refuses to do. Service-area spending exists for a single
year per entity; entity totals span 2017 to 2023. So several entities can be
compared over time on their totals, and compared to each other within a service
area at the reported year, but a service area cannot be tracked over time for
anyone. That is §8's rule and the refusal is returned as a result rather than
quietly substituted with something adjacent.
"""

import format as fmt
import tools as T
from rules import caveat, not_computable

# §11: a ratio this far from the median is worth naming.
NOTABLE_RATIO_HIGH = 1.5
NOTABLE_RATIO_LOW = 0.5

METRIC_LABELS = {
    "total_expenditure": ("Total spending", fmt.money),
    "total_revenue": ("Total revenue", fmt.money),
    "expenditure_per_capita": ("Spending per resident", fmt.rate),
    "capital_share": ("Capital share of spending", fmt.percent),
    "employees_per_1000": ("Employees per 1,000 residents", fmt.rate),
    "average_wage": ("Average annual wage", fmt.rate),
    "personnel_share": ("Personnel share of operating", fmt.percent),
    "service_area_share": ("Share of spending", fmt.percent),
    "service_area_total": ("Spending", fmt.money),
}


def peer_stats(store, gov_type, metric, service_area=None):
    if service_area:
        return store.row(
            "SELECT * FROM peer_stats WHERE peer_group=? AND metric=? AND service_area=?",
            gov_type, metric, service_area)
    return store.row(
        "SELECT * FROM peer_stats WHERE peer_group=? AND metric=? AND service_area IS NULL",
        gov_type, metric)


def entity_metric(store, pid6, metric, service_area=None):
    """The entity's own value for a metric peer_stats covers."""
    if metric == "service_area_share":
        row = store.row("SELECT percentage AS v FROM spending_by_service_area "
                        "WHERE pid6=? AND service_area=?", pid6, service_area)
    elif metric == "service_area_total":
        row = store.row("SELECT total AS v FROM spending_by_service_area "
                        "WHERE pid6=? AND service_area=?", pid6, service_area)
    elif metric in ("total_expenditure", "capital_share"):
        column = "total_expenditure" if metric == "total_expenditure" else "capital_pct"
        row = store.row(f"SELECT {column} AS v FROM operating_vs_capital WHERE pid6=?", pid6)
    elif metric == "total_revenue":
        row = store.row("SELECT revenue AS v FROM financial_trends WHERE pid6=? "
                        "ORDER BY year DESC LIMIT 1", pid6)
    elif metric == "expenditure_per_capita":
        row = store.row("SELECT o.total_expenditure * 1.0 / w.population AS v "
                        "FROM operating_vs_capital o JOIN workforce_profile w ON w.pid6=o.pid6 "
                        "WHERE o.pid6=? AND w.population > 0", pid6)
    else:
        column = {"employees_per_1000": "employees_per_1000",
                  "average_wage": "average_annual_wage",
                  "personnel_share": "personnel_cost_pct"}.get(metric)
        row = store.row(f"SELECT {column} AS v FROM workforce_profile WHERE pid6=?", pid6) \
            if column else None
    return (row or {}).get("v")


def compare_to_peer_group(store, pid6, metric, service_area=None):
    """Where one entity sits in its peer distribution (§8, §11).

    Returns the value, the distribution and a ratio, or the refusal where the
    distribution is degenerate. The peer group and its count travel with every
    result because §8 requires both to be stated.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("compare_to_peer_group", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    stats = peer_stats(store, entity["gov_type_name"], metric, service_area)
    value = entity_metric(store, pid6, metric, service_area)
    label, formatter = METRIC_LABELS.get(metric, (metric, fmt.money))

    if not stats or value is None:
        return T.envelope("compare_to_peer_group", entity=entity, caveats=[{
            "code": "no_peer_distribution", "rule": "§8",
            "guidance": f"No peer distribution exists for {label.lower()} among "
                        f"{entity['gov_type_name']} entities. Report the figure "
                        "without a comparison."}],
            data={"metric": metric, "value": value, "label": label})

    caveats = [{
        "code": "peer_group_stated", "rule": "§8",
        "guidance": f"The peer group is {stats['n']} Oregon "
                    f"{entity['gov_type_name']} entities. State the group and the "
                    "count alongside any comparison; a median without its pool is "
                    "not a benchmark."}]

    ratio = None
    if stats["degenerate"]:
        caveats.append(caveat("peer_median_degenerate"))
    elif stats["median"]:
        ratio = value / stats["median"]
        if ratio >= NOTABLE_RATIO_HIGH or ratio <= NOTABLE_RATIO_LOW:
            caveats.append({
                "code": "peer_ratio_notable", "rule": "§11",
                "guidance": "State the expected baseline before the deviation, so the "
                            "reader sees the size of the gap rather than an adjective."})

    # §13.3: peers are within a type, and the reason is structural.
    if entity["gov_type_name"] in ("County", "Municipal"):
        caveats.append(caveat("oregon_county_delegated"))

    quartile = None
    if value is not None:
        if value <= stats["p25"]:
            quartile = "bottom quarter"
        elif value >= stats["p75"]:
            quartile = "top quarter"
        else:
            quartile = "middle half"

    return T.envelope(
        "compare_to_peer_group", entity=entity,
        data={"metric": metric, "label": label, "service_area": service_area,
              "value": value, "formatted": formatter(value),
              "ratio": ratio, "ratio_refused": bool(stats["degenerate"]),
              "quartile": quartile,
              "peers": {k: stats[k] for k in
                        ("n", "low", "p25", "median", "p75", "high", "degenerate")},
              "peer_group": f"Oregon {entity['gov_type_name']}"},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy")])


# ------------------------------------------------------------- drill-down

def _function_medians(store, gov_type):
    """Median operating-plus-capital spending per named function, by type.

    Taken over entities that report the function, so it says what a government
    that does this spends, not what a typical government spends. A pool under
    three is not a median and returns None, which suppresses the tick.
    """
    buckets = {}
    for record in store.rows(
            "SELECT f.function_name AS name, "
            "       COALESCE(f.operating_expenditures, 0) "
            "     + COALESCE(f.capital_expenditures, 0) AS v "
            "FROM financial_functions f JOIN entities e ON e.pid6 = f.pid6 "
            "WHERE e.gov_type_name = ? AND COALESCE(f.operating_expenditures, 0) "
            "  + COALESCE(f.capital_expenditures, 0) > 0", gov_type):
        buckets.setdefault(record["name"], []).append(record["v"])

    out = {}
    for name, values in buckets.items():
        values.sort()
        if len(values) < 3:
            out[name] = {"median": None, "n": len(values)}
            continue
        median, p75 = quantile(values, 0.5), quantile(values, 0.75)
        degenerate = not median or (p75 and median / p75 < DEGENERATE_RATIO)
        out[name] = {"median": None if degenerate else median, "n": len(values)}
    return out


def drill(store, pid6, service_area=None, function_name=None):
    """One level of the breakdown, deepening as arguments are supplied."""
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("drill", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    gov_type = entity["gov_type_name"]
    flags = T._flags(store, pid6)
    caveats = [caveat("inputs_not_outcomes")]
    blocked = [not_computable("service_area_yoy")]

    # ---- level 3: how one function's total decomposes -------------------
    if function_name:
        row = store.row(
            "SELECT * FROM financial_functions WHERE pid6=? AND function_name=?",
            pid6, function_name)
        if not row:
            return T.envelope("drill", entity=entity, caveats=[{
                "code": "no_function", "rule": "§2",
                "guidance": f"{function_name} is not reported for this entity."}])

        items = [
            {"label": "Operating expenditure", "value": row["operating_expenditures"],
             "note": "day to day running costs"},
            {"label": "Capital expenditure", "value": row["capital_expenditures"],
             "note": "construction and equipment, lumpy by nature"},
            {"label": "Personnel, adjusted", "value": row["personnel_costs_adjusted"],
             "note": "wages and benefits, removed from the addressable figure"},
            {"label": "Excluded amounts", "value": row["excluded_amounts"],
             "note": "transfers and debt service, not purchasable"},
            {"label": "Vendor addressable", "value": row["total_function_pae"],
             "note": "what remains after personnel and exclusions"},
        ]
        return T.envelope(
            "drill", entity=entity,
            data={"level": "line_item", "service_area": row["service_area"],
                  "function": function_name, "year": row["year"],
                  "share_of_entity": row["pct_of_entity_total"], "items": items},
            caveats=caveats + [{
                "code": "pae_is_an_estimate", "rule": "§4",
                "guidance": "The vendor-addressable figure is a derivation, not a reported "
                            "line. It is operating and capital spending less personnel and "
                            "less amounts that cannot be purchased. Call it an estimate."}],
            blocked=blocked, vintage={"finance": row["year"]})

    # ---- level 2: functions inside one service area ---------------------
    if service_area:
        rows = store.rows(
            "SELECT * FROM financial_functions WHERE pid6=? AND service_area=? "
            "ORDER BY total_function_pae DESC", pid6, service_area)
        if not rows:
            return T.envelope("drill", entity=entity, caveats=[{
                "code": "no_functions", "rule": "§2",
                "guidance": f"No functions are reported for {service_area}. That is an "
                            "absence of detail, not a report of zero spending."}])

        # The median is computed per function and on the same measure as the bar.
        # A single median for the whole area would compare Police Protection to
        # the typical function rather than to other Police Protection budgets, and
        # the area median is built from vendor-addressable totals, which are a
        # different quantity from the operating-plus-capital figure shown.
        medians = _cached(store, ("fn_median", gov_type),
                          lambda: _function_medians(store, gov_type))
        functions = []
        for row in rows:
            stats = medians.get(row["function_name"]) or {"median": None, "n": 0}
            functions.append({
                "label": row["function_name"],
                "value": (row["operating_expenditures"] or 0) + (row["capital_expenditures"] or 0),
                "addressable": row["total_function_pae"],
                "share_of_entity": row["pct_of_entity_total"],
                "peer_median": stats["median"],
                "peer_n": stats["n"],
                "peer_degenerate": int(stats["median"] is None),
            })

        compared = [f for f in functions if f["peer_median"]]
        if compared:
            caveats.append({
                "code": "function_median_is_per_function", "rule": "§8",
                "guidance": "Each tick is the median for that named function among Oregon "
                            f"{gov_type} entities that report it, on the same "
                            "operating-plus-capital basis as the bar. It is not the median "
                            "for the service area, and it is taken only over entities "
                            "reporting the function, so it says what a government that does "
                            "this spends, not what a typical government spends."})

        return T.envelope(
            "drill", entity=entity,
            data={"level": "function", "service_area": service_area,
                  "year": rows[0]["year"], "functions": functions,
                  "peer_group": f"Oregon {gov_type}"},
            caveats=caveats, blocked=blocked, vintage={"finance": rows[0]["year"]})

    # ---- level 1: service areas ------------------------------------------
    areas = store.rows(
        "SELECT service_area, total, percentage, year FROM spending_by_service_area "
        "WHERE pid6=? ORDER BY total DESC", pid6)
    if not areas:
        return T.envelope("drill", entity=entity, caveats=[{
            "code": "no_breakdown", "rule": "§2",
            "guidance": "This entity has no spending breakdown. That is an absence of "
                        "data, not a report of zero."}])

    has_functions = {r["service_area"] for r in store.rows(
        "SELECT DISTINCT service_area FROM financial_functions WHERE pid6=?", pid6)}

    rows = []
    for area in areas:
        stats = peer_stats(store, gov_type, "service_area_share", area["service_area"])
        rows.append({
            "label": area["service_area"],
            "value": area["total"],
            "share": area["percentage"],
            "peer_median_share": stats["median"] if stats else None,
            "peer_degenerate": stats["degenerate"] if stats else 0,
            "peer_n": stats["n"] if stats else None,
            "expandable": area["service_area"] in has_functions,
        })

    if flags.get("other_share_high"):
        caveats.append(caveat("other_share_high"))
    if gov_type in T.SINGLE_CATEGORY_TYPES:
        caveats.append(caveat("single_category_entity"))
    caveats.append(caveat("absence_means_another_entity"))
    caveats.append({
        "code": "peer_median_on_each_row", "rule": "§8",
        "guidance": f"Each row carries the median share for Oregon {gov_type} entities. "
                    "Compare against it rather than against the entity's own largest row."})

    return T.envelope(
        "drill", entity=entity,
        data={"level": "service_area", "year": areas[0]["year"], "areas": rows,
              "peer_group": f"Oregon {gov_type}"},
        caveats=caveats, blocked=blocked, vintage={"finance": areas[0]["year"]})


# --------------------------------------------------- cross-entity compare

def compare_entities_over_time(store, pid6_list, measure="expenditure"):
    """Several governments on one measure across years (§12).

    Entity totals only. Service-area spending is a single year per entity, so a
    service area cannot be tracked over time for anyone, and this returns totals
    with that stated rather than implying the series is by category.
    """
    column = {"expenditure": "expenditure", "revenue": "revenue",
              "debt": "total_debt"}.get(measure)
    if not column:
        return T.envelope("compare_entities_over_time", caveats=[{
            "code": "unknown_measure", "rule": "§4",
            "guidance": "Measure must be expenditure, revenue or debt."}])

    entities = [T._entity(store, p) for p in pid6_list]
    found = [e for e in entities if e]
    missing = [p for p, e in zip(pid6_list, entities) if not e]
    types = {e["gov_type_name"] for e in found}

    series, thin = [], []
    for entity in found:
        points = [(r["year"], r[column]) for r in store.rows(
            f"SELECT year, {column} FROM financial_trends WHERE pid6=? ORDER BY year",
            entity["pid6"]) if r[column] is not None]
        if len(points) < 2:
            thin.append(entity["common_name"] or entity["legal_name"])
            continue
        series.append({"pid6": entity["pid6"],
                       "label": entity["common_name"] or entity["legal_name"],
                       "points": points})

    caveats = [caveat("inputs_not_outcomes"), {
        "code": "totals_not_categories", "rule": "§8",
        "guidance": "These are entity totals. Service-area spending exists for a single "
                    "year per entity, so no line here is a category and no category can "
                    "be tracked over time."}]
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This set mixes {', '.join(sorted(types))}. Say so: an Oregon "
                        "county carries state-delegated functions a city does not, so "
                        "their totals are not like for like."})
    if thin:
        caveats.append({
            "code": "entities_excluded", "rule": "§12",
            "guidance": f"{len(thin)} entities have fewer than two years and are not "
                        f"drawn: {', '.join(thin[:4])}. Name them."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data."})
    if len(series) > 4:
        caveats.append({
            "code": "series_capped", "rule": "§15",
            "guidance": "Past four lines the legend stops carrying identity. Only the "
                        "first four are drawn; name the rest or split the comparison."})

    return T.envelope(
        "compare_entities_over_time",
        data={"measure": measure, "series": series, "excluded": thin, "missing": missing,
              "entity_types": sorted(types), "count": len(series)},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"), not_computable("outcome_or_performance")])


def compare_service_area(store, pid6_list, service_area):
    """Several governments within one service area, at the year each reports.

    A bar rather than a line, because there is one year of service-area data per
    entity. Entities may report different years, which is stated rather than
    aligned away.
    """
    entities = [T._entity(store, p) for p in pid6_list]
    found = [e for e in entities if e]
    missing = [p for p, e in zip(pid6_list, entities) if not e]
    types = {e["gov_type_name"] for e in found}

    rows, absent, years = [], [], set()
    for entity in found:
        record = store.row(
            "SELECT total, percentage, year FROM spending_by_service_area "
            "WHERE pid6=? AND service_area=?", entity["pid6"], service_area)
        if not record or record["total"] is None:
            absent.append(entity["common_name"] or entity["legal_name"])
            continue
        years.add(record["year"])
        stats = peer_stats(store, entity["gov_type_name"], "service_area_share", service_area)
        rows.append({
            "label": entity["common_name"] or entity["legal_name"],
            "gov_type": entity["gov_type_name"],
            "value": record["total"], "share": record["percentage"], "year": record["year"],
            "peer_median_share": stats["median"] if stats else None,
            "peer_degenerate": stats["degenerate"] if stats else 0,
        })
    rows.sort(key=lambda r: r["value"], reverse=True)

    caveats = [caveat("inputs_not_outcomes"), {
        "code": "one_year_per_entity", "rule": "§8",
        "guidance": "Service-area spending is a single year per entity, so this is a "
                    "snapshot and not a trend. Do not describe any of it as rising or "
                    "falling."}]
    if len(years) > 1:
        caveats.append({
            "code": "years_differ", "rule": "§8",
            "guidance": f"These entities report different years ({', '.join(map(str, sorted(years)))}). "
                        "Say so; they are not the same moment."})
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This set mixes {', '.join(sorted(types))}, which are not "
                        "comparable on delegated functions."})
    if absent:
        caveats.append({
            "code": "absence_is_not_zero", "rule": "§9",
            "guidance": f"{len(absent)} entities report nothing in this area: "
                        f"{', '.join(absent[:4])}. That usually means another government "
                        "holds the responsibility, not that the service is unfunded."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data."})

    return T.envelope(
        "compare_service_area",
        data={"service_area": service_area, "entities": rows, "absent": absent,
              "missing": missing, "years": sorted(years),
              "entity_types": sorted(types), "count": len(rows)},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy")])


# -------------------------------------------------------- per resident

# Peer distributions on a per-resident basis are computed here rather than read
# from peer_stats, because the denominator has to match the bars being drawn.
# The quantile is the same linear interpolation etl/peers.py uses; it is repeated
# rather than imported because the ETL is not on the agent's path at runtime.
DEGENERATE_RATIO = 0.05


def _cached(store, key, build):
    """Memoize a whole-type scan on the store.

    A per-resident peer distribution is a scan of every entity of a government
    type, and it is identical for every entity of that type. Recomputing it per
    call is what turned a forty-second static export into an hour-long one. The
    store is opened read-only, so a value cannot go stale inside its lifetime.
    """
    cache = getattr(store, "_explore_cache", None)
    if cache is None:
        cache = store._explore_cache = {}
    if key not in cache:
        cache[key] = build()
    return cache[key]


def quantile(ordered, fraction):
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _population(store, pid6):
    row = store.row("SELECT population, year FROM workforce_profile WHERE pid6=? "
                    "AND population > 0", pid6)
    return (row["population"], row["year"]) if row else (None, None)


def _per_capita_area_stats(store, gov_type, service_area):
    values = sorted(
        record["v"] for record in store.rows(
            "SELECT s.total * 1.0 / w.population AS v "
            "FROM spending_by_service_area s "
            "JOIN workforce_profile w ON w.pid6 = s.pid6 "
            "JOIN entities e ON e.pid6 = s.pid6 "
            "WHERE s.service_area = ? AND e.gov_type_name = ? "
            "AND w.population > 0 AND s.total > 0", service_area, gov_type))
    if len(values) < 3:
        return None
    median, p75 = quantile(values, 0.5), quantile(values, 0.75)
    return {"n": len(values), "low": values[0], "p25": quantile(values, 0.25),
            "median": median, "p75": p75, "high": values[-1],
            "degenerate": bool(median <= 0 or (p75 and median / p75 < DEGENERATE_RATIO))}


def _per_capita_baseline(store, gov_type):
    """Median per-resident spending by year, over a balanced panel.

    Balanced on purpose: a median taken over a pool that changes size year to
    year moves when its membership moves, and that would read as a trend.
    """
    panel = {}
    for record in store.rows(
            "SELECT f.pid6, f.year, f.expenditure * 1.0 / w.population AS v "
            "FROM financial_trends f "
            "JOIN workforce_profile w ON w.pid6 = f.pid6 "
            "JOIN entities e ON e.pid6 = f.pid6 "
            "WHERE e.gov_type_name = ? AND w.population > 0 AND f.expenditure > 0",
            gov_type):
        panel.setdefault(record["pid6"], {})[record["year"]] = record["v"]
    all_years = sorted({y for v in panel.values() for y in v})
    balanced = [v for v in panel.values() if len(v) == len(all_years)]
    if len(balanced) < 5:
        return None
    return {"label": f"{gov_type} median", "gov_type": gov_type, "n": len(balanced),
            "points": [(year, quantile(sorted(v[year] for v in balanced), 0.5))
                       for year in all_years]}


def per_capita_by_service_area(store, pid6_list, service_area):
    """Per-resident spending on one service area, across selected governments.

    This is the comparison absolute dollars cannot make. Portland spends more on
    public safety than Sherman County by two orders of magnitude and that fact is
    only a statement about population; per resident, the ranking is a different
    one and it means something.

    Two denominators sit under this and both are stated. Population is a single
    estimate per entity, not a series, and service-area spending is a single year
    per entity, so this is a snapshot on both axes.
    """
    entities = [T._entity(store, p) for p in pid6_list]
    found = [e for e in entities if e]
    missing = [p for p, e in zip(pid6_list, entities) if not e]
    types = {e["gov_type_name"] for e in found}

    rows, no_population, absent, years = [], [], [], set()
    for entity in found:
        name = entity["common_name"] or entity["legal_name"]
        population, pop_year = _population(store, entity["pid6"])
        if not population:
            no_population.append(name)
            continue
        record = store.row(
            "SELECT total, percentage, year FROM spending_by_service_area "
            "WHERE pid6=? AND service_area=?", entity["pid6"], service_area)
        if not record or record["total"] is None:
            absent.append(name)
            continue
        years.add(record["year"])
        rows.append({
            "pid6": entity["pid6"], "label": name,
            "gov_type": entity["gov_type_name"],
            "value": record["total"] / population,
            "total": record["total"], "share": record["percentage"],
            "population": population, "population_year": pop_year,
            "year": record["year"],
        })
    rows.sort(key=lambda r: r["value"], reverse=True)

    # Peer context per government type, computed over the same denominator so the
    # band a reader compares against is built the way the bars are.
    peers = {}
    for gov_type in sorted({r["gov_type"] for r in rows}):
        stats = _cached(store, ("pc_area", gov_type, service_area),
                        lambda t=gov_type: _per_capita_area_stats(store, t, service_area))
        if stats:
            peers[gov_type] = stats
    for row in rows:
        stats = peers.get(row["gov_type"])
        row["peer_median"] = stats["median"] if stats and not stats["degenerate"] else None
        row["ratio"] = (row["value"] / stats["median"]
                        if stats and not stats["degenerate"] and stats["median"] else None)

    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "per_resident_basis", "rule": "§10",
         "guidance": "Every figure here is spending divided by the entity's population. "
                     "Say the basis in the answer; a per-resident figure compared against "
                     "an absolute one is not a comparison."},
        {"code": "population_is_one_estimate", "rule": "§8",
         "guidance": "Population is a single estimate per entity rather than a series, so "
                     "these are levels at one moment and no rate of change can be read "
                     "from them."},
        {"code": "one_year_per_entity", "rule": "§8",
         "guidance": "Service-area spending exists for one year per entity, so this is a "
                     "snapshot. Do not describe any bar as rising or falling."},
    ]
    if len(years) > 1:
        caveats.append({
            "code": "years_differ", "rule": "§8",
            "guidance": f"These entities report different years ({', '.join(map(str, sorted(years)))}). "
                        "Say so; they are not the same moment."})
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This set mixes {', '.join(sorted(types))}. Per resident makes the "
                        "sizes comparable but not the responsibilities: an Oregon county "
                        "carries state-delegated functions a city does not, so a county "
                        "spending more per resident on health is structure, not priority."})
        caveats.append(caveat("oregon_county_delegated"))
    if no_population:
        caveats.append({
            "code": "no_population_denominator", "rule": "§10",
            "guidance": f"{len(no_population)} entities have no population and are excluded "
                        f"rather than shown on another basis: {', '.join(no_population[:4])}. "
                        "Name them. Special districts and school districts serve areas that "
                        "are not a resident count."})
    if absent:
        caveats.append({
            "code": "absence_is_not_zero", "rule": "§9",
            "guidance": f"{len(absent)} entities report nothing in this area: "
                        f"{', '.join(absent[:4])}. That usually means another government "
                        "holds the responsibility, not that the service is unfunded."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data."})

    return T.envelope(
        "per_capita_by_service_area",
        data={"service_area": service_area, "basis": "per_resident",
              "entities": rows, "peers": peers, "excluded_no_population": no_population,
              "absent": absent, "missing": missing, "years": sorted(years),
              "entity_types": sorted(types), "count": len(rows)},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy")])


def per_capita_over_time(store, pid6_list, indexed=False, reference=True):
    """Per-resident spending across years, with a peer-median baseline.

    Honest about its denominator. Population is one estimate per entity, so it is
    held constant across the series: the movement in every line is movement in
    spending, not in spending per person. Where that distinction matters the
    caveat says so rather than the axis label implying otherwise.

    The dashed baseline is the peer median, computed over a balanced panel —
    entities present in every year — because a median taken over a pool that
    changes size year to year moves when its membership moves, and that would
    read as a trend.
    """
    entities = [T._entity(store, p) for p in pid6_list]
    found = [e for e in entities if e]
    missing = [p for p, e in zip(pid6_list, entities) if not e]
    types = {e["gov_type_name"] for e in found}

    series, no_population, thin = [], [], []
    for entity in found:
        name = entity["common_name"] or entity["legal_name"]
        population, pop_year = _population(store, entity["pid6"])
        if not population:
            no_population.append(name)
            continue
        points = [(r["year"], r["expenditure"] / population) for r in store.rows(
            "SELECT year, expenditure FROM financial_trends WHERE pid6=? "
            "AND expenditure IS NOT NULL ORDER BY year", entity["pid6"])]
        if len(points) < 2:
            thin.append(name)
            continue
        series.append({"pid6": entity["pid6"], "label": name,
                       "gov_type": entity["gov_type_name"],
                       "population": population, "population_year": pop_year,
                       "points": points})

    # The baseline, over one type only: a median across mixed types is a median
    # of two different jobs.
    baseline = None
    if reference and len({s["gov_type"] for s in series}) == 1 and series:
        gov_type = series[0]["gov_type"]
        cached = _cached(store, ("pc_baseline", gov_type),
                         lambda: _per_capita_baseline(store, gov_type))
        # Copied because indexing rewrites the points in place.
        baseline = dict(cached, points=list(cached["points"])) if cached else None

    if indexed and series:
        def rebase(points):
            base = points[0][1] or None
            return [(y, (v / base) * 100 if base else None) for y, v in points]
        for entry in series:
            entry["points"] = rebase(entry["points"])
        if baseline:
            baseline["points"] = rebase(baseline["points"])

    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "population_held_constant", "rule": "§8",
         "guidance": "Population is a single estimate per entity, not a series, so the "
                     "denominator is held constant across every year. What moves in these "
                     "lines is spending. Do not say spending per person rose or fell as "
                     "though the population had been tracked; say spending rose against a "
                     "fixed population estimate."},
        {"code": "totals_not_categories", "rule": "§8",
         "guidance": "These are entity totals. Service-area spending exists for one year "
                     "per entity, so no line here is a category and no category can be "
                     "tracked over time."},
    ]
    if indexed:
        caveats.append({
            "code": "indexed_hides_level", "rule": "§10",
            "guidance": "Every line starts at 100, so this shows growth and deliberately "
                        "hides level. An entity that doubled from a low base outruns one "
                        "that grew slightly from a high one. State the starting dollar "
                        "figures alongside, or the chart ranks the wrong thing."})
    if baseline:
        caveats.append({
            "code": "baseline_is_peers_not_inflation", "rule": "§8",
            "guidance": f"The dashed line is the median across {baseline['n']} Oregon "
                        f"{baseline['gov_type']} entities reporting in every year. It is a "
                        "peer baseline, not an inflation adjustment: no price index is in "
                        "this data, so none of these figures is in real terms and a line "
                        "rising above the baseline has outgrown its peers, not inflation."})
    elif reference:
        caveats.append({
            "code": "no_baseline_drawn", "rule": "§8",
            "guidance": "No peer baseline is drawn, because this set spans more than one "
                        "government type or too few peers report in every year. Compare the "
                        "lines to each other and say there is no baseline."})
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This set mixes {', '.join(sorted(types))}, which carry different "
                        "responsibilities. Per resident makes the sizes comparable, not "
                        "the jobs."})
    if no_population:
        caveats.append({
            "code": "no_population_denominator", "rule": "§10",
            "guidance": f"{len(no_population)} entities have no population and are excluded "
                        f"rather than drawn on another basis: {', '.join(no_population[:4])}."})
    if thin:
        caveats.append({
            "code": "entities_excluded", "rule": "§12",
            "guidance": f"{len(thin)} entities have fewer than two years and are not drawn: "
                        f"{', '.join(thin[:4])}. Name them."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data."})

    return T.envelope(
        "per_capita_over_time",
        data={"basis": "indexed_per_resident" if indexed else "per_resident",
              "indexed": bool(indexed), "series": series, "baseline": baseline,
              "excluded_no_population": no_population, "excluded_thin": thin,
              "missing": missing, "entity_types": sorted(types), "count": len(series)},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"),
                 not_computable("outcome_or_performance")])


def who_spends_on(store, function_name, limit=12):
    """Which governments report spending on one named function.

    The discovery question, and the one a name search cannot answer: somebody
    who wants fire protection cannot get there from a list of 1,529 names,
    because the districts that provide it are exactly the ones nobody can name.

    Ranked by spending rather than per resident. Most of the governments doing a
    given function are special districts with no population in the data, and
    ranking by a figure two thirds of the list cannot have would quietly drop
    them. The per-resident figure rides alongside where it exists.
    """
    rows = store.rows(
        "SELECT f.pid6, COALESCE(e.common_name, e.legal_name) AS name, "
        "       e.gov_type_name AS gov_type, e.host_county, f.service_area, f.year, "
        "       COALESCE(f.operating_expenditures, 0) "
        "     + COALESCE(f.capital_expenditures, 0) AS spending, "
        "       f.total_function_pae AS addressable, w.population "
        "FROM financial_functions f "
        "JOIN entities e ON e.pid6 = f.pid6 "
        "LEFT JOIN workforce_profile w ON w.pid6 = f.pid6 AND w.population > 0 "
        "WHERE f.function_name = ? AND COALESCE(f.operating_expenditures, 0) "
        "    + COALESCE(f.capital_expenditures, 0) > 0 "
        "ORDER BY spending DESC", function_name)

    if not rows:
        return T.envelope("who_spends_on", caveats=[{
            "code": "no_such_function", "rule": "§2",
            "guidance": f"No government reports spending on '{function_name}'."}])

    for row in rows:
        row["per_resident"] = (row["spending"] / row["population"]
                               if row["population"] else None)

    types = sorted({r["gov_type"] for r in rows})
    counties = {(r["host_county"] or "").title() for r in rows if r["host_county"]}
    service_area = rows[0]["service_area"]
    years = sorted({r["year"] for r in rows if r["year"]})

    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "ranked_by_size", "rule": "§10",
         "guidance": "This list is ranked by what each government spends, which ranks "
                     "by government size. Say so. The per-resident column reorders it "
                     "and is the fairer comparison where a population exists."},
        {"code": "absence_is_not_zero", "rule": "§9",
         "guidance": f"{len(rows)} governments report this function. A government absent "
                     "from the list is not one that spends nothing: it is one where "
                     "another body does the work, or where the function is not reported "
                     "separately."},
    ]
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This list mixes {', '.join(types)}. A city fire department and "
                        "a rural fire protection district both appear here and are not "
                        "peers; compare within a type."})
    if len(years) > 1:
        caveats.append({
            "code": "years_differ", "rule": "§8",
            "guidance": f"These figures span {years[0]} to {years[-1]}, one year per "
                        "entity. Say so; they are not the same moment."})
    no_population = sum(1 for r in rows if not r["population"])
    if no_population:
        caveats.append({
            "code": "no_population_denominator", "rule": "§10",
            "guidance": f"{no_population} of these have no population in the data, so no "
                        "per-resident figure is shown for them. That is an absence of a "
                        "denominator, not a small one."})

    return T.envelope(
        "who_spends_on",
        data={"function": function_name, "service_area": service_area,
              "total_governments": len(rows), "entities": rows[:limit],
              "entity_types": types, "counties": len(counties),
              "years": years, "shown": min(limit, len(rows))},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"),
                 not_computable("outcome_or_performance")])


def service_area_over_time_refusal(service_area):
    """The one comparison this data cannot support, returned as a result.

    Asking for a service area over time is a reasonable question and the honest
    answer is a refusal with the reason, not a silently substituted total.
    """
    return T.envelope(
        "compare_service_area_over_time",
        data={"service_area": service_area, "refused": True,
              "alternatives": ["entity totals over time, which span 2017 to 2023",
                               "this service area across entities at the reported year"]},
        caveats=[{
            "code": "no_service_area_series", "rule": "§8",
            "guidance": f"Spending on {service_area} exists for one year per entity, so it "
                        "cannot be tracked over time for any government. Offer entity "
                        "totals over time, or this area compared across entities, and say "
                        "which was substituted."}],
        blocked=[not_computable("service_area_yoy")])
