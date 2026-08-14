"""The payload that lets a pre-rendered build compare any set a reader picks.

The static export used to answer a comparison by looking up the first selected
government's pre-rendered peer set, which meant picking Portland and Medford
returned Portland against Eugene, Salem and Gresham. The chart was correct about
four governments nobody had asked about. §8 forbids exactly that substitution in
prose, and a picture asserts it harder than a sentence does.

Every combination of four governments across twelve service areas is far more
pages than a static build can carry, so the fix is not more pages. It is to ship
the figures and let the browser assemble the comparison the reader actually
asked for. Compact and rounded, that is about 400 KB, or 90 KB over the wire.

What is shipped and what is computed are split deliberately:

    shipped     every peer distribution and every baseline series, computed here
                by the same code the server uses, so no statistic is ever
                calculated twice in two languages

    computed    per-entity arithmetic (a total divided by a population) and the
                chart geometry, which are duplicated in comparison.js and held
                to the Python output by app/test_parity.py

The palette and the layout constants travel with the data rather than being
written out again in the client, so a colour or a bar height changes in one
place.
"""

import tools as T
import viz
from rules import DENOMINATOR


def _explore():
    # Imported lazily: explore imports tools, and tools binds explore's
    # functions at module load, so importing explore first deadlocks the pair.
    import explore
    return explore


def build(store):
    entities, areas, trends = {}, {}, {}

    for row in store.rows(
            "SELECT e.pid6, COALESCE(e.common_name, e.legal_name) AS name, "
            "e.gov_type_name AS gov_type, w.population "
            "FROM entities e LEFT JOIN workforce_profile w ON w.pid6 = e.pid6"):
        entities[row["pid6"]] = [row["name"], row["gov_type"], row["population"]]

    # Rounded to whole dollars. Sub-dollar precision on a municipal budget is
    # noise, and carrying it would double the payload.
    for row in store.rows(
            "SELECT pid6, service_area, total, percentage, year "
            "FROM spending_by_service_area WHERE total > 0"):
        areas.setdefault(row["pid6"], []).append(
            [row["service_area"], round(row["total"]), round(row["percentage"], 2),
             row["year"]])

    for row in store.rows(
            "SELECT pid6, year, revenue, expenditure, total_debt "
            "FROM financial_trends ORDER BY year"):
        trends.setdefault(row["pid6"], []).append([
            row["year"],
            None if row["revenue"] is None else round(row["revenue"]),
            None if row["expenditure"] is None else round(row["expenditure"]),
            None if row["total_debt"] is None else round(row["total_debt"])])

    # Every entity ships, including the fifteen with neither a breakdown nor a
    # trend. They used to be filtered out here, which cost almost nothing in
    # bytes and produced the worst failure this file exists to prevent: the
    # search box still offered them, so a reader could pick Coaledo Drainage
    # District, and the browser — not recognising the id — dropped it without a
    # word and answered about the other government alone. A government that
    # cannot be charted has to be *known* to be sayable, and §9 wants it said:
    # reporting nothing here is not the same as not existing.
    chartable = {pid for pid in entities if pid in areas or pid in trends}
    for pid, value in entities.items():
        value.append(1 if pid in chartable else 0)

    gov_types = sorted({value[1] for value in entities.values() if value[1]})
    service_areas = sorted({row[0] for rows in areas.values() for row in rows})

    # Computed by the server's own functions, so the median a reader sees in the
    # browser is the median the server would have drawn.
    area_stats = {}
    for gov_type in gov_types:
        per_area = {}
        for service_area in service_areas:
            stats = _explore()._per_capita_area_stats(store, gov_type, service_area)
            if stats:
                per_area[service_area] = stats
        if per_area:
            area_stats[gov_type] = per_area

    # The absolute form compares shares, not per-resident amounts, so it needs
    # the share distribution the ETL already computed rather than the per-capita
    # one built above. Two different medians, and using one for the other would
    # put a number under the wrong column.
    share_stats = {}
    for row in store.rows(
            "SELECT peer_group, service_area, median, degenerate, n FROM peer_stats "
            "WHERE metric = 'service_area_share' AND service_area IS NOT NULL"):
        share_stats.setdefault(row["peer_group"], {})[row["service_area"]] = {
            "median": row["median"], "degenerate": row["degenerate"], "n": row["n"]}

    # Five-year peer medians, computed once here so the browser never has to
    # scan the corpus. Keyed by government type and measure, over entities that
    # filed both ends of the window — the same pool the server builds.
    change_stats = {}
    for measure, column in _explore().MEASURE_COLUMNS.items():
        for gov_type in gov_types:
            changes = sorted(
                (row["e"] / row["s"] - 1) * 100 for row in store.rows(
                    f"SELECT a.{column} AS s, b.{column} AS e FROM financial_trends a "
                    "JOIN financial_trends b ON b.pid6 = a.pid6 AND b.year = ? "
                    "JOIN entities x ON x.pid6 = a.pid6 "
                    f"WHERE a.year = ? AND x.gov_type_name = ? AND a.{column} > 0 "
                    f"AND b.{column} IS NOT NULL",
                    _explore().CHANGE_END, _explore().CHANGE_START, gov_type))
            if len(changes) >= 4:
                change_stats.setdefault(measure, {})[gov_type] = {
                    "n": len(changes),
                    "median": _explore().quantile(changes, 0.5)}

    baselines = {}
    for gov_type in gov_types:
        baseline = _explore()._per_capita_baseline(store, gov_type)
        if baseline:
            baselines[gov_type] = baseline

    return {
        "entities": entities,
        "areas": areas,
        "trends": trends,
        "areaStats": area_stats,
        "areaShareStats": share_stats,
        "baselines": baselines,
        "changeStats": change_stats,
        # Shipped rather than restated in the browser, so a measure renamed here
        # is renamed in both engines at once.
        "measureLabels": T.MEASURE_LABELS,
        "windows": {
            "panel": [_explore().PANEL_START, _explore().PANEL_END],
            "change": [_explore().CHANGE_START, _explore().CHANGE_END],
        },
        "serviceAreas": service_areas,
        # The payload calls one field population for every type and it is not the
        # same quantity in each: for a school district it is enrollment. The
        # browser needs the same rule the server uses, not its own copy of it.
        "denominators": {k: list(v) for k, v in DENOMINATOR.items()},
        "tokens": viz.TOKENS,
        "layout": {
            "font": viz.FONT,
            "barThickness": viz.BAR_THICKNESS,
            "barGap": viz.BAR_GAP,
            "labelColumn": viz.LABEL_COLUMN,
            "barLabelChars": viz.BAR_LABEL_CHARS,
            "valueColumn": viz.VALUE_COLUMN,
            "width": 680,
            "seriesHeight": 300,
            "maxSeries": 4,
            "labelGap": 15,
        },
    }
