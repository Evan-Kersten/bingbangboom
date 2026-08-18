"""Drill-down, peer context and cross-entity comparison.

Three levels, which is as deep as the data goes and no deeper:

    service area   twelve Census functional categories, with the entity's share
                   and its peer group's distribution
    function       thirty-six functions inside those areas, each against the
                   median for that named function among governments doing it
    line item      how one function's total splits between operating and capital

Every level is on one basis, operating plus capital, which is the basis the
service-area shares are computed against. The source also carries a
vendor-addressable derivation per function; it is not built and not shown,
because it is an estimate sitting in columns named like the reported ones, and
a reader who compares the two is comparing a derivation to a filing.

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
from rules import caveat, denominator, not_computable, per_unit_label

# §11: a ratio this far from the median is worth naming.
NOTABLE_RATIO_HIGH = 1.5
NOTABLE_RATIO_LOW = 0.5

# Two of these are per-capita, and "capita" is not the same unit for every
# government type, so their labels are built from the type rather than fixed.
METRIC_LABELS = {
    "total_expenditure": ("Total spending", fmt.money),
    "total_revenue": ("Total revenue", fmt.money),
    "expenditure_per_capita": ("Spending {per_unit}", fmt.rate),
    "capital_share": ("Capital share of spending", fmt.percent),
    "employees_per_1000": ("Employees per 1,000 {units}", fmt.rate),
    "average_wage": ("Average annual wage", fmt.rate),
    "personnel_share": ("Personnel share of operating", fmt.percent),
    "service_area_share": ("Share of spending", fmt.percent),
    "service_area_total": ("Spending", fmt.money),
}


def metric_label(metric, gov_type):
    """The label for a metric, with the denominator named where it has one."""
    template, formatter = METRIC_LABELS.get(metric, (metric, fmt.money))
    return template.format(per_unit=per_unit_label(gov_type) or "per unit",
                           units=denominator(gov_type) or "units"), formatter


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
    label, formatter = metric_label(metric, entity["gov_type_name"])

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
              "peer_group": f"Oregon {entity['gov_type_name']}",
              "per_unit": per_unit_label(entity["gov_type_name"]),
              "unit": denominator(entity["gov_type_name"])},
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

        operating = row["operating_expenditures"] or 0
        capital = row["capital_expenditures"] or 0
        items = [
            {"label": "Operating expenditure", "value": row["operating_expenditures"],
             "note": "day to day running costs"},
            {"label": "Capital expenditure", "value": row["capital_expenditures"],
             "note": "construction and equipment, lumpy by nature"},
        ]
        total = operating + capital
        # Capital share is the signal at this level, and it is the one figure
        # here a reader will misread as a policy preference. §8 says one year is
        # a point in a bond cycle, so the caveat travels with the split itself.
        return T.envelope(
            "drill", entity=entity,
            data={"level": "line_item", "service_area": row["service_area"],
                  "function": function_name, "year": row["year"],
                  "share_of_entity": row["share_of_total"],
                  "total": total,
                  # Percentages in this tree are 0 to 100, the way the source
                  # files carry them and the way fmt.percent reads them.
                  "capital_share": (100 * capital / total) if total else None,
                  "items": items},
            caveats=caveats + [caveat("capital_lumpy")],
            blocked=blocked, vintage={"finance": row["year"]})

    # ---- level 2: functions inside one service area ---------------------
    if service_area:
        rows = store.rows(
            "SELECT * FROM financial_functions WHERE pid6=? AND service_area=? "
            "ORDER BY COALESCE(operating_expenditures, 0) "
            "       + COALESCE(capital_expenditures, 0) DESC", pid6, service_area)
        if not rows:
            return T.envelope("drill", entity=entity, caveats=[{
                "code": "no_functions", "rule": "§2",
                "guidance": f"No functions are reported for {service_area}. That is an "
                            "absence of detail, not a report of zero spending."}])

        # The median is computed per function and on the same measure as the bar.
        # A single median for the whole area would compare Police Protection to
        # the typical function rather than to other Police Protection budgets.
        medians = _cached(store, ("fn_median", gov_type),
                          lambda: _function_medians(store, gov_type))
        functions = []
        for row in rows:
            stats = medians.get(row["function_name"]) or {"median": None, "n": 0}
            functions.append({
                "label": row["function_name"],
                "value": (row["operating_expenditures"] or 0) + (row["capital_expenditures"] or 0),
                "share_of_entity": row["share_of_total"],
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
            # The entity's own year. Every government is shown at whichever year
            # it last reported, so two entities in one answer can be two years
            # apart and the year has to ride on the row rather than sit in a
            # header that only describes the first one.
            "year": area["year"],
            "value": area["total"],
            "share": area["percentage"],
            "peer_median_share": stats["median"] if stats else None,
            "peer_degenerate": stats["degenerate"] if stats else 0,
            "peer_n": stats["n"] if stats else None,
            "expandable": area["service_area"] in has_functions,
        })

    # Every row above carries its own year. The peer median beside it does not
    # have one: the pool is whichever year each peer last reported, so it can
    # blend two or three vintages. Disclosing the entity's year is necessary and
    # is not sufficient, because the number it is being compared against is the
    # one that has no single year.
    if any(r["peer_median_share"] is not None for r in rows):
        caveats.append(caveat("peer_pool_mixed_vintage"))
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

    found, unchartable, missing = partition(store, pid6_list)
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
    if unchartable:
        caveats.append(unchartable_caveat(unchartable))
    if len(series) > 4:
        caveats.append({
            "code": "series_capped", "rule": "§15",
            "guidance": "Past four lines the legend stops carrying identity. Only the "
                        "first four are drawn; name the rest or split the comparison."})

    return T.envelope(
        "compare_entities_over_time",
        data={"measure": measure, "series": series, "excluded": thin, "missing": missing,
              "entity_types": sorted(types), "count": len(series),
              "unchartable": [e["common_name"] or e["legal_name"] for e in unchartable]},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"), not_computable("outcome_or_performance")])


def compare_service_area(store, pid6_list, service_area):
    """Several governments within one service area, at the year each reports.

    A bar rather than a line, because there is one year of service-area data per
    entity. Entities may report different years, which is stated rather than
    aligned away.
    """
    found, unchartable, missing = partition(store, pid6_list)
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
    if unchartable:
        caveats.append(unchartable_caveat(unchartable))

    return T.envelope(
        "compare_service_area",
        data={"service_area": service_area, "entities": rows, "absent": absent,
              "missing": missing, "years": sorted(years),
              "unchartable": [e["common_name"] or e["legal_name"] for e in unchartable],
              "entity_types": sorted(types), "count": len(rows)},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy")])


# -------------------------------------------------------- per resident

# Peer distributions on a per-resident basis are computed here rather than read
# from peer_stats, because the denominator has to match the bars being drawn.
# The quantile is the same linear interpolation etl/peers.py uses; it is repeated
# rather than imported because the ETL is not on the agent's path at runtime.
DEGENERATE_RATIO = 0.05


def chartable(store, pid6):
    """Whether any of the four comparison forms has anything to draw for this.

    Fifteen governments report neither a spending breakdown nor a year of
    totals. They are real, they are searchable, and until this existed the
    browser dropped them from a comparison without a word — so a reader who
    picked one got an answer about the other government alone. §9: reporting
    nothing is not the same as not existing, and both are different again from
    spending zero.
    """
    return bool(
        store.row("SELECT 1 AS x FROM spending_by_service_area "
                  "WHERE pid6=? AND total > 0 LIMIT 1", pid6)
        or store.row("SELECT 1 AS x FROM financial_trends WHERE pid6=? LIMIT 1", pid6))


def partition(store, pid6_list):
    """Split a picked set into drawable, present-but-undrawable, and unknown."""
    found, unchartable, missing = [], [], []
    for pid6 in pid6_list:
        entity = T._entity(store, pid6)
        if not entity:
            missing.append(pid6)
        elif not _cached(store, ("chartable", pid6), lambda p=pid6: chartable(store, p)):
            unchartable.append(entity)
        else:
            found.append(entity)
    return found, unchartable, missing


def unchartable_caveat(unchartable):
    names = [e["common_name"] or e["legal_name"] for e in unchartable]
    return {
        "code": "nothing_to_compare", "rule": "§9",
        "guidance": f"{len(names)} of the governments picked report neither a spending "
                    "breakdown nor a year of totals, so there is nothing to put on this "
                    f"axis for them: {', '.join(names[:4])}. They are in the data and "
                    "were left out of the drawing, which is not the same as spending "
                    "nothing. Name them."}


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
    found, unchartable, missing = partition(store, pid6_list)
    types = {e["gov_type_name"] for e in found}

    # A school district's population field is enrollment, so dividing by it gives
    # spending per student. Ranking that against spending per resident puts two
    # different measures in one column under one heading, which is the silent
    # basis change §10 forbids. Refused, with what can be drawn instead.
    units = {denominator(e["gov_type_name"]) for e in found
             if denominator(e["gov_type_name"])}
    if len(units) > 1:
        return T.envelope(
            "per_capita_by_service_area",
            data={"service_area": service_area, "basis": "per_resident",
                  "entities": [], "peers": {}, "excluded_no_population": [],
                  "absent": [], "missing": missing, "years": [],
                  "entity_types": sorted(types), "count": 0,
                  "mixed_denominators": sorted(units),
                  "alternatives": ["the same service area in total dollars",
                                   "either group on its own denominator"]},
            caveats=[{
                "code": "mixed_denominators", "rule": "§10",
                "guidance": "This set mixes governments measured per "
                            + " and per ".join(sorted(u.rstrip("s") for u in units))
                            + ". Those are different measures, not one measure across "
                              "different governments, so they cannot share an axis. "
                              "Compare in total dollars, or split the set by type."}],
            blocked=[not_computable("service_area_yoy")])

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
         "guidance": "Every figure here is spending divided by the entity's "
                     + (sorted(units)[0] if units else "population")
                     + ". Say the basis in the answer; a per-unit figure compared "
                       "against an absolute one is not a comparison."},
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
    if unchartable:
        caveats.append(unchartable_caveat(unchartable))

    return T.envelope(
        "per_capita_by_service_area",
        data={"service_area": service_area, "basis": "per_resident",
              "unit": (sorted(units)[0].rstrip("s") if units else "resident"),
              "entities": rows, "peers": peers, "excluded_no_population": no_population,
              "absent": absent, "missing": missing, "years": sorted(years),
              "unchartable": [e["common_name"] or e["legal_name"] for e in unchartable],
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
    found, unchartable, missing = partition(store, pid6_list)
    types = {e["gov_type_name"] for e in found}

    units = {denominator(e["gov_type_name"]) for e in found
             if denominator(e["gov_type_name"])}
    if len(units) > 1:
        return T.envelope(
            "per_capita_over_time",
            data={"basis": "per_resident", "indexed": bool(indexed),
                  "series": [], "baseline": None, "excluded_no_population": [],
                  "excluded_thin": [], "missing": missing,
                  "entity_types": sorted(types), "count": 0,
                  "mixed_denominators": sorted(units),
                  "alternatives": ["entity totals over time",
                                   "either group on its own denominator"]},
            caveats=[{
                "code": "mixed_denominators", "rule": "§10",
                "guidance": "This set mixes governments measured per "
                            + " and per ".join(sorted(u.rstrip("s") for u in units))
                            + ". Those are different measures and cannot share an axis. "
                              "Compare entity totals, or split the set by type."}],
            blocked=[not_computable("service_area_yoy")])

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
    if unchartable:
        caveats.append(unchartable_caveat(unchartable))

    return T.envelope(
        "per_capita_over_time",
        data={"basis": "indexed_per_resident" if indexed else "per_resident",
              "unit": (sorted(units)[0].rstrip("s") if units else "resident"),
              "indexed": bool(indexed), "series": series, "baseline": baseline,
              "excluded_no_population": no_population, "excluded_thin": thin,
              "missing": missing, "entity_types": sorted(types), "count": len(series),
              "unchartable": [e["common_name"] or e["legal_name"] for e in unchartable]},
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
        "     + COALESCE(f.capital_expenditures, 0) AS spending, w.population "
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

    # The unit rides on the row rather than on the column, because this list is
    # mixed by design: a school district and a fire district appear in the same
    # ranking and their population fields count different things. One column
    # heading over both would be the silent basis change §10 forbids.
    for row in rows:
        row["per_unit"] = (row["spending"] / row["population"]
                           if row["population"] else None)
        row["unit"] = denominator(row["gov_type"], plural=False)

    types = sorted({r["gov_type"] for r in rows})
    counties = {(r["host_county"] or "").title() for r in rows if r["host_county"]}
    service_area = rows[0]["service_area"]
    years = sorted({r["year"] for r in rows if r["year"]})

    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "ranked_by_size", "rule": "§10",
         "guidance": "This list is ranked by what each government spends, which ranks "
                     "by government size. Say so. The per-unit column reorders it and "
                     "is the fairer comparison where a denominator exists, but that "
                     "denominator is residents for a city or county and students for a "
                     "school district, so each figure carries its own unit and the "
                     "column is not one ranking."},
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
            "guidance": f"{no_population} of these have no denominator in the data, so no "
                        "per-unit figure is shown for them. That is an absence of a "
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


# ------------------------------------------------------ staffing in ratios

# A headcount is a number about an organisation. A ratio against the population
# it serves is a number about a community: one firefighter per 1,140 residents
# says something a reader can picture, and 688 firefighters does not. The
# function split is what makes this possible, since the payroll is broken out
# into named jobs rather than left as one total.
#
# The unit follows the government type, so a school district's teachers are
# counted per student and a city's officers per resident. Ratios are only
# built where a denominator exists; a special district gets the split and the
# wages without a ratio, rather than a ratio against a population it has none of.
STAFF_ROLES = {
    "Education - Elementary and Secondary Instructional": "teaching and instructional staff",
    "Education - Higher Education Instructional": "instructional staff",
    "Police Protection - Persons with Power of Arrest": "sworn police officers",
    "Fire Protection - Firefighters": "firefighters",
    "Libraries": "library staff",
    "Parks and Recreation": "parks and recreation staff",
    "Corrections": "corrections staff",
    "Health": "health staff",
    "Water Supply": "water supply staff",
    "Highways": "road and highway staff",
    "Sewerage": "sewerage staff",
    "Transit": "transit staff",
}


def staff_role(function_name):
    """What a reader would call the people in this function."""
    return STAFF_ROLES.get(function_name)


def _staff_ratio_stats(store, gov_type, function_name):
    """Median people-per-staff-member across one type, for one function.

    Built over entities that have both the function and a denominator, because a
    median taken over a pool that includes entities missing either side is a
    median of a different quantity (§8).
    """
    ratios = sorted(
        row["population"] / row["headcount"]
        for row in store.rows(
            "SELECT f.headcount, w.population FROM workforce_top_functions f "
            "JOIN entities e ON e.pid6 = f.pid6 "
            "JOIN workforce_profile w ON w.pid6 = f.pid6 "
            "WHERE e.gov_type_name = ? AND f.function_name = ? "
            "  AND f.headcount > 0 AND w.population > 0",
            gov_type, function_name))
    if len(ratios) < 4:
        return None
    return {"n": len(ratios), "median": quantile(ratios, 0.5),
            "p25": quantile(ratios, 0.25), "p75": quantile(ratios, 0.75),
            "degenerate": 1 if quantile(ratios, 0.25) == quantile(ratios, 0.75) else 0}


def staffing(store, pid6):
    """Who a government employs, split by job, and how thinly that is spread.

    The listed functions do not cover every employee, so shares are stated as
    shares of the entity total rather than of the listed rows (§8): the payload
    gives pct_of_total against the whole payroll, and the gap between the listed
    rows and the total is named rather than left for a reader to discover by
    adding the column up.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("staffing", caveats=[{
            "code": "unknown_entity", "rule": "§2",
            "guidance": f"No entity {pid6} in the data."}])

    profile = store.row("SELECT * FROM workforce_profile WHERE pid6=?", pid6)
    functions = store.rows(
        "SELECT function_name, year, headcount, headcount_yoy, average_wage, "
        "pct_of_total, total_payroll FROM workforce_top_functions "
        "WHERE pid6=? AND headcount > 0 ORDER BY headcount DESC", pid6)

    if not profile or not functions:
        return T.envelope("staffing", entity=entity, caveats=[{
            "code": "no_staff_split", "rule": "§9",
            "guidance": "This government's payroll is not broken out into named jobs. "
                        "That is an absence of detail, not a report of no staff."}])

    gov_type = entity["gov_type_name"]
    unit_one = denominator(gov_type, plural=False)
    unit_many = denominator(gov_type)
    population = profile["population"]
    total = profile["total_employees"]

    rows = []
    for record in functions:
        row = dict(record)
        row["role"] = staff_role(record["function_name"])
        row["per_staff"] = (population / record["headcount"]
                            if population and record["headcount"] else None)
        stats = (_cached(store, ("staff_ratio", gov_type, record["function_name"]),
                         lambda f=record["function_name"]:
                         _staff_ratio_stats(store, gov_type, f))
                 if population else None)
        row["peer_median_per_staff"] = (
            stats["median"] if stats and not stats["degenerate"] else None)
        row["peer_n"] = stats["n"] if stats else None
        rows.append(row)

    listed = sum(r["headcount"] for r in rows)
    covered = (listed / total * 100) if total else None

    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "headcount_is_not_service", "rule": "§4",
         "guidance": "A staffing ratio is a count of people employed against a count of "
                     "people served. It is not a measure of coverage, response time or "
                     "quality, and a thinner ratio is not a worse service."},
        {"code": "workforce_partial", "rule": "§8",
         "guidance": f"The named jobs cover {fmt.count(listed)} of "
                     f"{fmt.count(total)} employees"
                     + (f" ({covered:.0f}%)" if covered else "")
                     + ". Shares are of the whole payroll, but the rows do not add to "
                       "it; the remainder is staff in functions this payload does not "
                       "break out."},
    ]
    if population:
        caveats.append({
            "code": "ratio_denominator", "rule": "§10",
            "guidance": f"Ratios here are {unit_many} per staff member, on a single "
                        f"{unit_one} estimate held constant. Say the unit: a school "
                        "district's is students and a city's is residents."})
    else:
        caveats.append({
            "code": "no_population_denominator", "rule": "§10",
            "guidance": "This government has no denominator in the data, so the split is "
                        "reported without ratios rather than against a population it "
                        "does not have."})
    if any(r["role"] is None for r in rows):
        caveats.append({
            "code": "census_job_labels", "rule": "§12",
            "guidance": "Some rows carry the Census function label rather than a plain "
                        "job name. Quote the label; do not invent a job title for it."})

    return T.envelope(
        "staffing", entity=entity,
        data={"total_employees": total, "population": population,
              "unit": unit_one, "units": unit_many, "gov_type": gov_type,
              "functions": rows, "listed": listed, "covered_pct": covered,
              "average_annual_wage": profile["average_annual_wage"]},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")],
        vintage={"workforce": profile["year"]})


# --------------------------------------------------------- whose money it is

# The question a resident actually has about a budget is not where it goes but
# where it came from, because one of the answers is their own tax bill. Every
# entity in the data has a revenue split and nothing was using it.
#
# Reported as shares with the dollars alongside. A share is what makes two
# governments comparable and a dollar figure is what makes one concrete, and
# §10 wants the basis said either way.
REVENUE_MEANING = {
    "Property Tax": "levied on property inside its boundary",
    "Charges & Fees": "charged to the people who use the service",
    "State Aid": "transferred from the state",
    "Federal Aid": "transferred from the federal government",
    "Sales Tax": "a local levy; Oregon has no statewide sales tax",
    "Income Tax": "a local levy; most Oregon governments do not have one",
    "Other Taxes": "lodging, franchise and similar levies",
    "Other Revenue": "interest, sales of property, and everything unclassified",
}

# Money the government raises from inside its own boundary, as against money
# handed to it. §12: the distinction is structural, not a judgement.
OWN_SOURCE = {"Property Tax", "Charges & Fees", "Sales Tax", "Income Tax", "Other Taxes"}
TRANSFERS = {"State Aid", "Federal Aid"}


def _revenue_share_stats(store, gov_type, category):
    shares = sorted(
        row["percentage"] for row in store.rows(
            "SELECT r.percentage FROM revenue_sources r "
            "JOIN entities e ON e.pid6 = r.pid6 "
            "WHERE e.gov_type_name = ? AND r.category = ? AND r.percentage IS NOT NULL",
            gov_type, category))
    if len(shares) < 4:
        return None
    p25, p75 = quantile(shares, 0.25), quantile(shares, 0.75)
    return {"n": len(shares), "median": quantile(shares, 0.5),
            "p25": p25, "p75": p75, "degenerate": 1 if p25 == p75 else 0}


def revenue_mix(store, pid6):
    """Where a government's money comes from, and how much of it is the reader's.

    Two structural facts ride on this and both are stated: the split between
    money raised inside the boundary and money transferred in, because a
    government funded by transfers is exposed to a decision made elsewhere; and
    the peer share for each category, because 28% property tax means nothing
    until you know what a typical Oregon county of that kind reports.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("revenue_mix", caveats=[{
            "code": "unknown_entity", "rule": "§2",
            "guidance": f"No entity {pid6} in the data."}])

    rows = store.rows(
        "SELECT category, amount, percentage, year FROM revenue_sources "
        "WHERE pid6=? AND COALESCE(amount, 0) > 0 ORDER BY percentage DESC", pid6)
    if not rows:
        return T.envelope("revenue_mix", entity=entity, caveats=[{
            "code": "no_revenue_split", "rule": "§9",
            "guidance": "This government's revenue is not broken into sources here. That "
                        "is an absence of detail, not a report of no revenue."}])

    gov_type = entity["gov_type_name"]
    for row in rows:
        row["meaning"] = REVENUE_MEANING.get(row["category"])
        row["own_source"] = 1 if row["category"] in OWN_SOURCE else 0
        stats = _cached(store, ("rev_share", gov_type, row["category"]),
                        lambda c=row["category"]: _revenue_share_stats(store, gov_type, c))
        row["peer_median_share"] = (
            stats["median"] if stats and not stats["degenerate"] else None)
        row["peer_n"] = stats["n"] if stats else None

    total = sum(r["amount"] for r in rows)
    own = sum(r["amount"] for r in rows if r["category"] in OWN_SOURCE)
    transferred = sum(r["amount"] for r in rows if r["category"] in TRANSFERS)
    years = sorted({r["year"] for r in rows if r["year"]})

    caveats = [
        {"code": "revenue_not_spending", "rule": "§10",
         "guidance": "These are revenue shares, not spending. A government's largest "
                     "revenue source and its largest spending area are different "
                     "questions and must not be run together in one sentence."},
        {"code": "absent_category_is_not_zero", "rule": "§9",
         "guidance": "A category missing here usually means this government does not "
                     "levy or receive it, not that it collected nothing it expected. "
                     "Oregon has no statewide sales tax, so a sales or income tax line "
                     "is a local levy and most governments have none."},
        {"code": "transfer_exposure", "rule": "§12",
         "guidance": f"{transferred / total * 100:.0f}% of this revenue is transferred "
                     "in from the state or federal government rather than raised inside "
                     "the boundary. Say so where it is large: that share is exposed to a "
                     "decision made somewhere else. It is a structural fact, not a "
                     "weakness."},
    ]
    if len(years) > 1:
        caveats.append({
            "code": "years_differ", "rule": "§8",
            "guidance": f"These lines span {years[0]} to {years[-1]}. Say so; they are "
                        "not all the same moment."})
    if gov_type == "School District":
        caveats.append(caveat("oregon_school_state_funded"))

    return T.envelope(
        "revenue_mix", entity=entity,
        data={"sources": rows, "total": total, "own_source": own,
              "own_source_pct": own / total * 100 if total else None,
              "transferred": transferred,
              "transferred_pct": transferred / total * 100 if total else None,
              "gov_type": gov_type, "years": years,
              "largest": rows[0]["category"]},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")],
        vintage={"revenue": years[-1] if years else None})


def debt_load(store, pid6):
    """What a government owes, set against what it takes in each year.

    Debt alone ranks by government size. Against annual revenue it becomes a
    number a reader can hold: 1.75 times revenue means the whole year's income
    would not clear it. Reported at the latest year both figures exist, because
    a debt from one year over a revenue from another is not a ratio.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("debt_load", caveats=[{
            "code": "unknown_entity", "rule": "§2",
            "guidance": f"No entity {pid6} in the data."}])

    row = store.row(
        "SELECT year, total_debt, revenue FROM financial_trends "
        "WHERE pid6=? AND total_debt > 0 AND revenue > 0 ORDER BY year DESC LIMIT 1", pid6)
    if not row:
        return T.envelope("debt_load", entity=entity, caveats=[{
            "code": "no_debt_reported", "rule": "§9",
            "guidance": "This government reports no outstanding debt in the data. That "
                        "may mean it carries none, or that debt is not reported here. "
                        "Do not present it as a government that has paid everything off."}])

    series = store.rows(
        "SELECT year, total_debt FROM financial_trends WHERE pid6=? AND total_debt > 0 "
        "ORDER BY year", pid6)
    gov_type = entity["gov_type_name"]
    ratios = sorted(
        r["total_debt"] / r["revenue"] for r in store.rows(
            "SELECT f.total_debt, f.revenue FROM financial_trends f "
            "JOIN entities e ON e.pid6 = f.pid6 "
            "WHERE e.gov_type_name = ? AND f.total_debt > 0 AND f.revenue > 0 "
            "  AND f.year = ?", gov_type, row["year"]))
    peers = ({"n": len(ratios), "median": quantile(ratios, 0.5),
              "p25": quantile(ratios, 0.25), "p75": quantile(ratios, 0.75)}
             if len(ratios) >= 4 else None)

    ratio = row["total_debt"] / row["revenue"]
    flags = T._flags(store, pid6)
    caveats = [
        {"code": "debt_is_not_a_deficit", "rule": "§8",
         "guidance": "Outstanding debt is a balance, not an annual shortfall. Most of it "
                     "is borrowing against capital assets a government intends to hold "
                     "for decades. Do not describe it as overspending."},
        {"code": "debt_over_revenue_basis", "rule": "§10",
         "guidance": f"The ratio is debt at {row['year']} over revenue in the same year. "
                     "Say the basis; against spending or against assessed value it would "
                     "be a different number."},
    ]
    if peers:
        caveats.append({
            "code": "peer_group_stated", "rule": "§8",
            "guidance": f"The peer group is {peers['n']} Oregon {gov_type} entities "
                        f"reporting both figures in {row['year']}. State the group and "
                        "the count; a median without its pool is not a benchmark."})
    else:
        caveats.append({
            "code": "no_peer_distribution", "rule": "§8",
            "guidance": "Too few peers report both debt and revenue in this year for a "
                        "median. Report the ratio without a comparison."})
    if flags.get("debt_capped"):
        caveats.append(caveat("debt_capped"))

    return T.envelope(
        "debt_load", entity=entity,
        data={"year": row["year"], "total_debt": row["total_debt"],
              "revenue": row["revenue"], "ratio": ratio, "peers": peers,
              "gov_type": gov_type,
              "series": [(r["year"], r["total_debt"]) for r in series]},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")],
        vintage={"finance": row["year"]})


# ------------------------------------------------------------ five-year panel

# The trend data is not one panel, it is two, and drawing them together without
# saying so is the worst thing this module could do.
#
#     1,005 entities   two observations, 2017 and 2022, and nothing between
#       313 entities   every year from 2017 to 2023
#        67 entities   2017 and then 2019 to 2023
#
# A straight line between 2017 and 2022 looks exactly like a line measured seven
# times. Next to each other on one axis, the reader has no way to tell which is
# which, and will read a path through five years nobody reported. §8 already says
# one year is a point rather than a trend; two points five years apart is a
# change rather than a trend, and that is the distinction drawn here.
ANNUAL = "annual"           # measured every year in the window
ENDPOINTS = "endpoints"     # measured at the ends, with a gap between
PARTIAL = "partial"         # some years, with holes
SINGLE = "single"           # one observation, which is not a series

# The window where a genuine annual panel exists. 2019 to 2023 is five years and
# 381 entities report every one of them; widening it to 2017 buys two more years
# and loses two thirds of the entities that could fill them.
PANEL_START, PANEL_END = 2019, 2023

# The window every entity can answer, because it is the two years almost all of
# them filed. Five years apart, so it is a five-year change and never a trend.
CHANGE_START, CHANGE_END = 2017, 2022

MEASURE_COLUMNS = {"expenditure": "expenditure", "revenue": "revenue",
                   "debt": "total_debt"}

# The fourth measure does not live in financial_trends. It is the total the
# service-area shares are computed against — operating plus capital — and it is
# a different series from financial_trends.expenditure for 1,111 of 1,479
# entities. Kept separate at every level rather than folded in, because folding
# it in is exactly how one came to stand in for the other.
SERVICE_AREA_BASIS = "service_area_basis"


def coverage_of(years, start, end):
    """What kind of series these observations are, inside a window."""
    inside = sorted(y for y in years if start <= y <= end)
    if not inside:
        return None, []
    if len(inside) == 1:
        return SINGLE, inside
    if len(inside) == (inside[-1] - inside[0] + 1) and len(inside) >= 3:
        return ANNUAL, inside
    if len(inside) == 2:
        return ENDPOINTS, inside
    return PARTIAL, inside


def measure_series(store, pid6, measure, start, end):
    """The year/value pairs for one government on one measure, inside a window.

    Routes the service-area basis to its own table. Everything else comes from
    financial_trends, and the two are never mixed in one series.
    """
    if measure == SERVICE_AREA_BASIS:
        return [(r["year"], r["total_expenditure"]) for r in store.rows(
            "SELECT year, total_expenditure FROM service_area_totals "
            "WHERE pid6=? AND year BETWEEN ? AND ? AND total_expenditure IS NOT NULL "
            "ORDER BY year", pid6, start, end)]
    column = MEASURE_COLUMNS[measure]
    return [(r["year"], r["value"]) for r in store.rows(
        f"SELECT year, {column} AS value FROM financial_trends "
        f"WHERE pid6=? AND year BETWEEN ? AND ? AND {column} IS NOT NULL "
        "ORDER BY year", pid6, start, end)]


def trend_panel(store, pid6_list, measure="expenditure",
                start=PANEL_START, end=PANEL_END):
    """Several governments over a stated window, each labelled by how densely
    it is actually measured.

    The window is stated rather than inferred from what happens to be present,
    because a chart whose x-axis is decided by whichever entity reported longest
    is a chart about that entity's filing habits.
    """
    if measure != SERVICE_AREA_BASIS and measure not in MEASURE_COLUMNS:
        return T.envelope("trend_panel", caveats=[{
            "code": "unknown_measure", "rule": "§4",
            "guidance": f"Measure must be one of "
                        f"{', '.join(list(MEASURE_COLUMNS) + [SERVICE_AREA_BASIS])}."}])

    found, unchartable, missing = partition(store, pid6_list)
    series, thin = [], []
    for entity in found:
        points = measure_series(store, entity["pid6"], measure, start, end)
        name = entity["common_name"] or entity["legal_name"]
        kind, years = coverage_of([p[0] for p in points], start, end)
        if kind in (None, SINGLE):
            thin.append({"name": name, "years": len(points)})
            continue
        first, last = points[0][1], points[-1][1]
        series.append({
            "pid6": entity["pid6"], "label": name,
            "gov_type": entity["gov_type_name"],
            "points": points, "coverage": kind, "years": years,
            "observations": len(points),
            "first": first, "last": last,
            "change": (last / first - 1) * 100 if first else None,
            # The renderer dashes these, because the segment between the two
            # observations is drawn and was never measured.
            "interpolated": kind in (ENDPOINTS, PARTIAL),
        })

    covered = {s["coverage"] for s in series}
    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "window_is_stated", "rule": "§8",
         "guidance": f"The window is {start} to {end} and was chosen, not inferred "
                     "from whichever entity reported longest. Say the window; a "
                     "different one produces a different and equally true picture."},
        {"code": "totals_not_categories", "rule": "§8",
         "guidance": "These are entity totals. Service-area spending exists for one "
                     "year per entity, so no line here is a category."},
    ]
    if ENDPOINTS in covered or PARTIAL in covered:
        sparse = [s["label"] for s in series if s["interpolated"]]
        caveats.append({
            "code": "series_not_annual", "rule": "§8",
            "guidance": f"{len(sparse)} of these are not annual series: "
                        f"{', '.join(sparse[:4])} report only some years in this "
                        "window, and the line between their observations is drawn, "
                        "not measured. They are dashed for that reason. Do not "
                        "describe a year nobody filed, and do not read a rate of "
                        "change off a dashed segment."})
    if ANNUAL in covered and len(covered) > 1:
        caveats.append({
            "code": "mixed_density", "rule": "§8",
            "guidance": "This chart mixes annual series with series measured at two "
                        "ends. They are not equally precise and the difference is "
                        "not visible in the shape of a line. Say which are which."})
    if thin:
        caveats.append({
            "code": "entities_excluded", "rule": "§12",
            "guidance": f"{len(thin)} entities report fewer than two years inside this "
                        f"window and are not drawn: "
                        f"{', '.join(t['name'] for t in thin[:4])}. Name them."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data."})
    if unchartable:
        caveats.append(unchartable_caveat(unchartable))

    series.sort(key=lambda s: s["last"] or 0, reverse=True)
    return T.envelope(
        "trend_panel",
        data={"measure": measure, "start": start, "end": end,
              "series": series, "excluded_thin": thin, "missing": missing,
              "annual": sum(1 for s in series if s["coverage"] == ANNUAL),
              "interpolated": sum(1 for s in series if s["interpolated"]),
              "count": len(series),
              "unchartable": [e["common_name"] or e["legal_name"] for e in unchartable]},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"),
                 not_computable("outcome_or_performance")])


def compare_change(store, pid6_list, measure="expenditure",
                   start=CHANGE_START, end=CHANGE_END):
    """What happened between two years, across several governments.

    The five-year comparison almost every government can answer. An annual panel
    exists for 381 entities; both ends of 2017 to 2022 exist for around 1,500,
    because those are the two years nearly everybody filed.

    Deliberately bars and never a line. The data between the endpoints does not
    exist for most of this set, and a line would draw a path through it. A bar
    says "this much more, over five years", which is what two observations
    support and all they support.
    """
    if measure != SERVICE_AREA_BASIS and measure not in MEASURE_COLUMNS:
        return T.envelope("compare_change", caveats=[{
            "code": "unknown_measure", "rule": "§4",
            "guidance": f"Measure must be one of "
                        f"{', '.join(list(MEASURE_COLUMNS) + [SERVICE_AREA_BASIS])}."}])
    column = MEASURE_COLUMNS.get(measure)

    found, unchartable, missing = partition(store, pid6_list)
    rows, incomplete = [], []
    for entity in found:
        name = entity["common_name"] or entity["legal_name"]
        span = measure_series(store, entity["pid6"], measure, start, end)
        ends = dict(span)
        if start not in ends or end not in ends or not ends[start]:
            incomplete.append(name)
            continue
        observed = len(span)
        rows.append({
            "pid6": entity["pid6"], "label": name,
            "gov_type": entity["gov_type_name"],
            "first": ends[start], "last": ends[end],
            "value": (ends[end] / ends[start] - 1) * 100,
            "difference": ends[end] - ends[start],
            "observations": observed,
            "annual": observed == (end - start + 1)})

    rows.sort(key=lambda r: r["value"], reverse=True)

    # The peer median change, over the same two years and the same type, so the
    # band a reader compares against is built the way the bars are.
    peers = {}
    source = ("service_area_totals" if measure == SERVICE_AREA_BASIS
              else "financial_trends")
    value = "total_expenditure" if measure == SERVICE_AREA_BASIS else column
    for gov_type in sorted({r["gov_type"] for r in rows}):
        changes = sorted(
            (r["e"] / r["s"] - 1) * 100 for r in store.rows(
                f"SELECT a.{value} AS s, b.{value} AS e FROM {source} a "
                f"JOIN {source} b ON b.pid6 = a.pid6 AND b.year = ? "
                "JOIN entities x ON x.pid6 = a.pid6 "
                f"WHERE a.year = ? AND x.gov_type_name = ? AND a.{value} > 0 "
                f"AND b.{value} IS NOT NULL", end, start, gov_type))
        if len(changes) >= 4:
            peers[gov_type] = {"n": len(changes), "median": quantile(changes, 0.5),
                               "p25": quantile(changes, 0.25),
                               "p75": quantile(changes, 0.75)}
    for row in rows:
        stats = peers.get(row["gov_type"])
        row["peer_median"] = stats["median"] if stats else None
        row["peer_n"] = stats["n"] if stats else None

    caveats = [
        caveat("inputs_not_outcomes"),
        {"code": "change_not_trend", "rule": "§8",
         "guidance": f"This is the change between {start} and {end}, which is two "
                     "observations five years apart and not a trend. Nothing here "
                     "says whether the change was steady, front-loaded or reversed "
                     "in between. Do not describe a direction over the middle years, "
                     "and do not call it a growth rate per year."},
        {"code": "no_price_index", "rule": "§8",
         "guidance": "No price index exists in this data, so these changes are in "
                     "nominal dollars. A government that grew is not necessarily "
                     "buying more than it did; say the figures are not inflation "
                     "adjusted."},
    ]
    if peers:
        group = ", ".join(f"{stats['n']} Oregon {gov_type}"
                          for gov_type, stats in sorted(peers.items()))
        caveats.append({
            "code": "peer_group_stated", "rule": "§8",
            "guidance": f"The peer medians are over {group} entities reporting both "
                        "years. State the group and the count; a median without its "
                        "pool is not a benchmark."})
    if any(not r["annual"] for r in rows):
        caveats.append({
            "code": "endpoints_only", "rule": "§8",
            "guidance": "Most of these governments report only these two years, so "
                        "the middle of the window is not in the data at all. That is "
                        "why this is drawn as a change and not as a line."})
    if incomplete:
        caveats.append({
            "code": "entities_excluded", "rule": "§12",
            "guidance": f"{len(incomplete)} entities do not report both {start} and "
                        f"{end} and are not shown: {', '.join(incomplete[:4])}. That "
                        "is an absence of a filing, not a change of zero."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data."})
    if unchartable:
        caveats.append(unchartable_caveat(unchartable))

    return T.envelope(
        "compare_change",
        data={"measure": measure, "start": start, "end": end, "span": end - start,
              "entities": rows, "peers": peers, "excluded": incomplete,
              "missing": missing, "count": len(rows),
              "unchartable": [e["common_name"] or e["legal_name"] for e in unchartable]},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"),
                 not_computable("outcome_or_performance")])


# ------------------------------------------------- money against people

def cost_per_head(store, pid6, function_name):
    """What a government spends on one function against who it employs in it.

    The only measure in this product that crosses the finance and workforce
    taxonomies, and the crosswalk it rests on is many to many in both
    directions. Fire Protection money covers firefighters and other fire staff;
    six public welfare financial functions land on one employment function. So
    both sides are summed across the bridge before dividing. A row-to-row join
    would double count on one side and drop money on the other, and the answer
    looks equally plausible either way, which is what makes it dangerous.

    Refused rather than estimated where the entity's listed employment functions
    do not include this work. §8: the source reports top functions only, so an
    absent headcount is a truncated list rather than a government doing the job
    with nobody, and dividing by what happens to be listed would flatter every
    government whose staff fell below the cut. That is 59 of the 327 entities
    reporting fire protection.
    """
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("cost_per_head", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    money = store.row(
        "SELECT SUM(COALESCE(operating_expenditures, 0) "
        "     + COALESCE(capital_expenditures, 0)) AS spend, MIN(year) AS year "
        "FROM financial_functions WHERE pid6=? AND function_name=?", pid6, function_name)
    if not money or not money["spend"]:
        return T.envelope("cost_per_head", entity=entity, caveats=[{
            "code": "function_not_reported", "rule": "§2",
            "guidance": f"{entity['common_name'] or entity['legal_name']} reports no "
                        f"spending on {function_name}. That is an absence of a report, "
                        "not a report of zero."}])

    # Every employment function this financial function maps to, summed.
    staff = store.row(
        "SELECT SUM(w.headcount) AS heads, SUM(w.total_payroll) AS payroll, "
        "       COUNT(*) AS listed, MIN(w.year) AS year "
        "FROM workforce_top_functions w "
        "WHERE w.pid6=? AND w.function_name IN ("
        "  SELECT employment_function FROM function_bridge WHERE financial_function=?)",
        pid6, function_name)

    bridged = [r["employment_function"] for r in store.rows(
        "SELECT employment_function FROM function_bridge WHERE financial_function=?",
        function_name)]
    caveats = [caveat("bridge_is_many_to_many"), caveat("cost_per_head_is_not_a_salary"),
               caveat("headcount_from_listed_functions"), caveat("inputs_not_outcomes")]

    if not bridged:
        return T.envelope("cost_per_head", entity=entity, caveats=caveats + [{
            "code": "no_bridge_for_function", "rule": "§8",
            "guidance": f"No employment function is crosswalked to {function_name}, so "
                        "there is no headcount to divide by. The spending figure stands "
                        "on its own."}],
            data={"function": function_name, "spend": money["spend"], "heads": None})

    if not staff or not staff["heads"]:
        return T.envelope(
            "cost_per_head", entity=entity,
            data={"function": function_name, "spend": money["spend"], "heads": None,
                  "per_head": None, "bridged_to": bridged, "refused": True},
            caveats=caveats + [{
                "code": "no_listed_headcount", "rule": "§8",
                "guidance": f"This government reports spending on {function_name} and "
                            f"none of {', '.join(bridged)} appears among its listed "
                            "employment functions, which the source reports as a top-N. "
                            "Per-head is refused rather than divided by a headcount that "
                            "is missing rather than zero."}],
            blocked=[not_computable("outcome_or_performance")])

    per_head = money["spend"] / staff["heads"]
    return T.envelope(
        "cost_per_head", entity=entity,
        data={"function": function_name, "spend": money["spend"],
              "heads": staff["heads"], "per_head": per_head,
              "payroll": staff["payroll"], "bridged_to": bridged,
              "listed_functions": staff["listed"], "refused": False},
        caveats=caveats,
        blocked=[not_computable("outcome_or_performance")],
        vintage={"finance": money["year"], "workforce": staff["year"]})


def inside_service_area(store, service_area):
    """What a service area is made of, across every government that reports it.

    `drill` answers this for one government: here is what Bend puts inside
    Public Safety. This answers it for the state, and the two are different
    questions. One government's split is a budget. Oregon's split is a picture
    of who delivers the service, and the shape of it is not what a reader
    expects: fire protection is reported by 327 governments and police
    protection by 187, and yet police is the larger number. Fire is district
    work, spread thin across hundreds of small bodies; policing is concentrated
    in cities and counties. That single comparison is the argument for having
    this view at all, and nothing in the product produced it before.

    Never a share of the service area. §9 forbids summing across governments,
    and a percentage here would need exactly that sum as its denominator: the
    same dollar appears twice wherever one government pays another to do the
    work. Each function's own total is real; the whole they would make is not.
    """
    rows = store.rows(
        "SELECT f.function_name AS name, "
        "       COUNT(DISTINCT f.pid6) AS governments, "
        "       SUM(COALESCE(f.operating_expenditures, 0) "
        "         + COALESCE(f.capital_expenditures, 0)) AS total, "
        "       SUM(COALESCE(f.capital_expenditures, 0)) AS capital, "
        "       MIN(f.year) AS first_year, MAX(f.year) AS last_year "
        "FROM financial_functions f WHERE f.service_area = ? "
        "GROUP BY f.function_name HAVING total > 0 ORDER BY total DESC",
        service_area)
    if not rows:
        return T.envelope("inside_service_area", caveats=[{
            "code": "no_such_service_area", "rule": "§2",
            "guidance": f"No function in this data is filed under {service_area}. "
                        "Call get_spending_breakdown for the areas that exist."}])

    functions = []
    for row in rows:
        # Which government types carry it, in order of how much they carry.
        # This is the finding for most areas: the same function is a city job in
        # one place and a district's whole reason for existing in another.
        types = store.rows(
            "SELECT e.gov_type_name AS type, COUNT(*) AS n, "
            "       SUM(COALESCE(f.operating_expenditures, 0) "
            "         + COALESCE(f.capital_expenditures, 0)) AS total "
            "FROM financial_functions f JOIN entities e ON e.pid6 = f.pid6 "
            "WHERE f.function_name = ? AND COALESCE(f.operating_expenditures, 0) "
            "    + COALESCE(f.capital_expenditures, 0) > 0 "
            "GROUP BY e.gov_type_name ORDER BY total DESC", row["name"])
        functions.append({
            "name": row["name"],
            "governments": row["governments"],
            "total": row["total"],
            "capital_share": (100.0 * row["capital"] / row["total"]
                              if row["total"] else None),
            "years": sorted({row["first_year"], row["last_year"]} - {None}),
            "held_by": [{"type": t["type"], "governments": t["n"], "total": t["total"]}
                        for t in types],
        })

    years = sorted({y for f in functions for y in f["years"]})
    caveats = [caveat("inputs_not_outcomes"), caveat("two_expenditure_measures"),
               {"code": "functions_do_not_sum", "rule": "§9",
                "guidance": "These function totals are not parts of one number and must "
                            "not be added. A city paying a district to do the work "
                            "reports the payment and the district reports the spending, "
                            "so the same dollar is in both rows, and no share of the "
                            "service area can be computed from them."}]
    if len(years) > 1:
        caveats.append({
            "code": "years_differ", "rule": "§8",
            "guidance": f"These rows span {years[0]} to {years[-1]}, one year per "
                        "government rather than one year across the state. Read the "
                        "totals as a level and not as a moment."})
    return T.envelope(
        "inside_service_area",
        data={"service_area": service_area, "functions": functions,
              "governments": store.row(
                  "SELECT COUNT(DISTINCT pid6) AS n FROM financial_functions "
                  "WHERE service_area = ?", service_area)["n"],
              "years": years},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy")])
