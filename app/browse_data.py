"""Ways into the data for a reader who cannot name a government.

Search over 1,529 names only finds what someone already knew existed, which
excludes almost everything: nobody arrives able to name a rural fire protection
district, and the special districts nobody can name are two thirds of Oregon's
governments. So the interface needs doors that are not the search box.

Three doors, each complete rather than a sample:

    counties      36 of them, and "which governments serve this place" is the
                  question this data answers better than the alternatives
    types         four, with counts, so the shape of the corpus is visible
    functions     36 named things governments actually do, which is how someone
                  who wants fire protection or water supply would look

The function index carries its top spenders so the door opens onto real
governments rather than onto another empty search box.
"""

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

    return {"counties": counties, "types": types, "functions": functions,
            "areas": areas}
