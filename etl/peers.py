"""Peer distributions computed from the entity set.

The payload ships one benchmark, on the fiscal stability score, and that score
is a composite whose components move for opposite reasons. Every other question
a reader actually asks (is this a lot of money, are they staffed unusually, do
they spend more on public safety than their peers) had no peer context at all.

So the distributions are computed here, once, over the 1,529 entities: low,
first quartile, median, third quartile and high, per government type, and per
government type crossed with service area. That is what lets an answer say "the
peer median is X across Y counties" for anything, rather than only for a score.

Two rules the tools depend on:

    Peer groups are within a government type. §13.3 is explicit that Oregon
    counties carry state-delegated functions cities do not, so a county and a
    city are not peers on health, corrections or judicial spending.

    A distribution whose median sits at or near zero is marked degenerate. Most
    special districts report nothing in most service areas, so a median of zero
    is not a midpoint, and §8 says to decline the distance rather than compute
    a ratio against it.
"""

# Degeneracy is "most of the pool reports nothing", not "the distribution is
# skewed". County revenue runs from $2M to $2.35B around a $59M median, which is
# very skewed and perfectly meaningful; a service area that most special
# districts do not report at all has a median of zero and is not a midpoint.
DEGENERATE_RATIO = 0.05


def quantile(ordered, fraction):
    """Linear-interpolated quantile over a pre-sorted list."""
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize(values, group, metric, service_area=None):
    """Return one peer_stats row, or None where there is nothing to summarize."""
    numbers = sorted(v for v in values if v is not None)
    if len(numbers) < 3:
        return None

    median = quantile(numbers, 0.5)
    p75 = quantile(numbers, 0.75)
    high = numbers[-1]
    degenerate = bool(
        median is None or median <= 0
        or (p75 and (median / p75) < DEGENERATE_RATIO))

    return {
        "peer_group": group,
        "metric": metric,
        "service_area": service_area,
        "n": len(numbers),
        "low": numbers[0],
        "p25": quantile(numbers, 0.25),
        "median": median,
        "p75": p75,
        "high": high,
        "degenerate": int(degenerate),
    }


def compute(conn):
    """Build every peer distribution from the already-loaded tables."""
    conn.row_factory = __import__("sqlite3").Row
    rows = []

    def collect(sql, metric, service_area_column=None):
        buckets = {}
        for record in conn.execute(sql):
            key = (record["peer_group"],
                   record[service_area_column] if service_area_column else None)
            buckets.setdefault(key, []).append(record["value"])
        for (group, area), values in buckets.items():
            row = summarize(values, group, metric, area)
            if row:
                rows.append(row)

    # ---- entity-level scale and structure --------------------------------
    collect("""SELECT e.gov_type_name AS peer_group, o.total_expenditure AS value
               FROM operating_vs_capital o JOIN entities e ON e.pid6=o.pid6
               WHERE o.total_expenditure > 0""", "total_expenditure")

    collect("""SELECT e.gov_type_name AS peer_group, o.capital_pct AS value
               FROM operating_vs_capital o JOIN entities e ON e.pid6=o.pid6
               WHERE o.capital_pct IS NOT NULL""", "capital_share")

    collect("""SELECT e.gov_type_name AS peer_group, t.revenue AS value
               FROM financial_trends t JOIN entities e ON e.pid6=t.pid6
               WHERE t.revenue > 0 AND t.year = (
                   SELECT MAX(year) FROM financial_trends WHERE pid6=t.pid6)""",
            "total_revenue")

    # ---- normalized by population ----------------------------------------
    collect("""SELECT e.gov_type_name AS peer_group,
               o.total_expenditure * 1.0 / w.population AS value
               FROM operating_vs_capital o
               JOIN workforce_profile w ON w.pid6=o.pid6
               JOIN entities e ON e.pid6=o.pid6
               WHERE w.population > 0 AND o.total_expenditure > 0""",
            "expenditure_per_capita")

    # ---- workforce ---------------------------------------------------------
    collect("""SELECT e.gov_type_name AS peer_group, w.employees_per_1000 AS value
               FROM workforce_profile w JOIN entities e ON e.pid6=w.pid6
               WHERE w.employees_per_1000 > 0""", "employees_per_1000")

    collect("""SELECT e.gov_type_name AS peer_group, w.average_annual_wage AS value
               FROM workforce_profile w JOIN entities e ON e.pid6=w.pid6
               WHERE w.average_annual_wage > 0""", "average_wage")

    collect("""SELECT e.gov_type_name AS peer_group, w.personnel_cost_pct AS value
               FROM workforce_profile w JOIN entities e ON e.pid6=w.pid6
               WHERE w.personnel_cost_pct > 0""", "personnel_share")

    # ---- per service area, which is what the drill-down compares against ---
    # Entities reporting nothing in an area are included as zero on purpose:
    # a median taken only over entities that report the area would overstate
    # what is typical for the type.
    collect("""SELECT e.gov_type_name AS peer_group, s.service_area,
                      s.percentage AS value
               FROM spending_by_service_area s JOIN entities e ON e.pid6=s.pid6""",
            "service_area_share", "service_area")

    collect("""SELECT e.gov_type_name AS peer_group, s.service_area,
                      s.total AS value
               FROM spending_by_service_area s JOIN entities e ON e.pid6=s.pid6
               WHERE s.total > 0""",
            "service_area_total", "service_area")

    # No function-level distribution is written here. The drill computes its
    # median per named function rather than per service area, because the
    # comparison a reader wants is police against police, not police against the
    # typical function in the area, and a per-area statistic cannot answer that.
    return rows


def write(conn, rows):
    conn.execute("DROP TABLE IF EXISTS peer_stats")
    conn.execute("""
        CREATE TABLE peer_stats (
            peer_group   TEXT,      -- government type; peers are within a type
            metric       TEXT,
            service_area TEXT,      -- NULL for entity-level metrics
            n            INTEGER,   -- always stated with the median
            low          DOUBLE,
            p25          DOUBLE,
            median       DOUBLE,
            p75          DOUBLE,
            high         DOUBLE,
            degenerate   INTEGER    -- median at or near the floor; decline ratios
        )""")
    conn.executemany(
        "INSERT INTO peer_stats (peer_group, metric, service_area, n, low, p25, "
        "median, p75, high, degenerate) VALUES (:peer_group, :metric, "
        ":service_area, :n, :low, :p25, :median, :p75, :high, :degenerate)", rows)
    conn.execute("CREATE INDEX idx_peer_lookup ON peer_stats(peer_group, metric, service_area)")
    conn.commit()
    return len(rows)
