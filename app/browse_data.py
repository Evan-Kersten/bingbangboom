"""Ways into the data for a reader who cannot name a government.

Search over 1,529 names only finds what someone already knew existed, which
excludes almost everything: nobody arrives able to name a rural fire protection
district, and the special districts nobody can name are two thirds of Oregon's
governments. So the interface needs doors that are not the search box.

Four doors, each complete rather than a sample:

    places        the towns a stack of governments has been established for.
                  This is the front door: a reader cannot name a district but
                  can always name where they live, and the answer behind it is
                  the several governments serving that address rather than one
    counties      36 of them, for the wider list of what is filed where
    types         four, with counts, so the shape of the corpus is visible
    functions     36 named things governments actually do, which is how someone
                  who wants fire protection or water supply would look

The function index carries its top spenders so the door opens onto real
governments rather than onto another empty search box.
"""

import sqlite3

# Ranked lists are a starting point, not a finding, so they are kept short.

# Ranked lists are a starting point, not a finding, so they are kept short.
TOP_PER_FUNCTION = 10


def build(store):
    counties = [
        {"pid6": row["pid6"], "name": row["name"], "governments": row["governments"]}
        for row in store.rows(
            "SELECT c.pid6, COALESCE(c.common_name, c.legal_name) AS name, "
            "       COUNT(e.pid6) AS governments "
            "FROM entities c LEFT JOIN entities e ON e.host_county_pid6 = c.pid6 "
            "WHERE c.gov_type_name = 'County' "
            "GROUP BY c.pid6 ORDER BY name")]

    types = [
        {"name": row["gov_type_name"], "count": row["n"]}
        for row in store.rows(
            "SELECT gov_type_name, COUNT(*) AS n FROM entities "
            "WHERE gov_type_name != 'State' GROUP BY gov_type_name ORDER BY n DESC")]

    functions = []
    for row in store.rows(
            "SELECT function_name, service_area, COUNT(DISTINCT pid6) AS n "
            "FROM financial_functions "
            "WHERE COALESCE(operating_expenditures, 0) "
            "    + COALESCE(capital_expenditures, 0) > 0 "
            "GROUP BY function_name, service_area ORDER BY n DESC"):
        functions.append({
            "name": row["function_name"],
            "area": row["service_area"],
            "governments": row["n"],
        })

    areas = [
        {"name": row["service_area"], "governments": row["n"]}
        for row in store.rows(
            "SELECT service_area, COUNT(DISTINCT pid6) AS n "
            "FROM spending_by_service_area WHERE total > 0 "
            "GROUP BY service_area ORDER BY n DESC")]

    # Ordered by residents, not by how many governments were established. A
    # reader scanning for their own town finds it faster in a list that opens on
    # the towns most people live in, and stack size is an artefact of which
    # boundary files exist rather than a fact about the town.
    try:
        places = [
            {"pid6": row["pid6"], "name": row["name"], "county": (row["county"] or "").title(),
             "governments": row["governments"] + 1, "population": row["population"]}
            for row in store.rows(
                "SELECT e.pid6, COALESCE(e.common_name, e.legal_name) AS name, "
                "       e.host_county AS county, w.population, "
                "       COUNT(s.pid6) AS governments "
                "FROM entities e "
                "JOIN serves_place s ON s.place_pid6 = e.pid6 "
                "LEFT JOIN workforce_profile w ON w.pid6 = e.pid6 "
                "GROUP BY e.pid6 "
                "ORDER BY COALESCE(w.population, 0) DESC, name")]
    except sqlite3.OperationalError:
        places = []   # the serving ETL has not been run against this build

    return {"places": places, "counties": counties, "types": types,
            "functions": functions, "areas": areas}
