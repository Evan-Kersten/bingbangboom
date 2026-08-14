#!/usr/bin/env python3
"""Which governments serve a place — the question a resident actually arrives with.

Nobody has *a* government. The median address in Multnomah County sits inside
ten of them: a city, a county, the state, a school district, an education
service district, a community college, a fire district, a water district, a
transit district, a soil and water conservation district. A resident can
usually name two.

That is why a search box over 1,529 names is the wrong front door. You cannot
look up what you cannot name, and two thirds of Oregon's governments are
special districts nobody can name. Choosing a place you *do* know and being
handed the stack is the way in.

Three bases, strongest first, and every row says which one it rests on because
they are not equally true:

    precinct_exact      the Multnomah precinct file lists, per polygon, which
                        districts that polygon sits in. For the eight cities in
                        that county this is the real answer, not an inference.

    boundary_overlap    the place falls inside the government's own boundary.
                        Available for school districts statewide and for the 19
                        special districts the dissolve pilot recovered.

What this table deliberately does NOT hold is the fourth, weakest tier: every
special district filed under the same county. That is 46 governments for
Multnomah alone, most of which serve somewhere else in the county, and mixing
them in here would swamp an established relationship with a guess. The interface
asks for those separately and puts them under their own heading, because §13.11
is explicit that published data assigns a district to the county holding most of
its assessed value — so a county-filed list over-collects districts that serve
elsewhere and misses districts that serve here while filing next door.

The consequence is worth stating plainly, because it shapes what can be built on
this: outside Multnomah a typical city resolves to two governments, its county
and its school district, against the ten that really serve it. The missing eight
are special districts with no boundary anywhere in this data. This table is
honest about what is established; it is not yet a complete answer to "what do I
pay for", and only boundary data for special districts would make it one.

    python3 etl/serving.py [--out build]
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict

import shapefile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRECINCTS = "March_2026_Multco_PrecinctSplit_Shapefile/Precinct_Split_March2024_Final"

# The precinct columns that name a government, and what each one is. CITY is
# excluded: it is the place being asked about, not a government serving it.
PRECINCT_GOVERNMENTS = {
    "TRAN_DIST": "Mass transit",
    "FIRE_DIST": "Fire protection",
    "WaterDist": "Water supply",
    "SewerDist": "Sanitary",
    "SchoolDist": "School district",
    "PUD": "People's utility",
    "CommColleg": "Community college",
    "Soil_Water": "Soil and water conservation",
    "ESD": "Education service",
    "METRO": "Regional government",
    "UFSWQD": "Water quality",
}

# How many boundary vertices to test besides the centroid. A city wholly inside
# one school district needs one point; a city split across three needs several,
# and eight is enough to find a split without turning containment into a scan.
SAMPLE_POINTS = 8


def centroid(geometry):
    """Area centroid of the largest ring, which is inside the place for a city."""
    best, area = None, 0.0
    for polygon in geometry["coordinates"]:
        if not polygon:
            continue
        ring = polygon[0]
        signed = 0.0
        for i in range(len(ring) - 1):
            signed += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
        if abs(signed) > area:
            best, area = ring, abs(signed)
    if not best or area < 1e-14:
        return None
    cx = cy = 0.0
    total = 0.0
    for i in range(len(best) - 1):
        cross = best[i][0] * best[i + 1][1] - best[i + 1][0] * best[i][1]
        cx += (best[i][0] + best[i + 1][0]) * cross
        cy += (best[i][1] + best[i + 1][1]) * cross
        total += cross
    total /= 2
    return (cx / (6 * total), cy / (6 * total)) if total else None


def sample(geometry, count=SAMPLE_POINTS):
    """The centroid plus points around the boundary, for a place that is split."""
    points = []
    middle = centroid(geometry)
    if middle:
        points.append(middle)
    ring = None
    area = 0.0
    for polygon in geometry["coordinates"]:
        if not polygon:
            continue
        candidate = polygon[0]
        signed = abs(sum(candidate[i][0] * candidate[i + 1][1]
                         - candidate[i + 1][0] * candidate[i][1]
                         for i in range(len(candidate) - 1)))
        if signed > area:
            ring, area = candidate, signed
    if ring and len(ring) > count and middle:
        step = len(ring) // count
        # Pulled well inside, then checked. A point sitting on the city's edge is
        # by definition adjacent to whatever is next door, and testing it found
        # Portland "overlapping" two Clackamas water districts it does not touch.
        # Only points the place actually contains may vote.
        for i in range(0, len(ring) - 1, step):
            x, y = ring[i]
            inward = (x + (middle[0] - x) * 0.15, y + (middle[1] - y) * 0.15)
            if contains(geometry, inward):
                points.append(inward)
    return points


def contains(geometry, point):
    """Ray casting, counting crossings across every ring of every polygon."""
    x, y = point
    inside = False
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            n = len(ring)
            j = n - 1
            for i in range(n):
                xi, yi = ring[i]
                xj, yj = ring[j]
                if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                    inside = not inside
                j = i
    return inside


def bounds(geometry):
    xs, ys = [], []
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def load_layer(out_dir, name):
    path = os.path.join(out_dir, "geo", f"{name}.geojson")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [f for f in json.load(handle)["features"] if f.get("geometry")]


def precinct_stack(conn):
    """The governments each Multnomah city actually sits inside.

    Read straight off the precinct file, which records the assignment per
    polygon. This is the only place in the data where the question "which
    governments serve this address" is answered rather than inferred.
    """
    base = os.path.join(REPO, PRECINCTS)
    if not os.path.exists(base + ".dbf"):
        return {}
    _, rows = shapefile.read_dbf(base + ".dbf")

    # The dissolve already resolved these names to entities; reuse its answer so
    # one name resolution serves both rather than drifting apart.
    resolved = {}
    for row in conn.execute(
            "SELECT pid6, serves_county FROM service_extent WHERE serves_county='MULTNOMAH'"):
        resolved[row["pid6"]] = True

    import dissolve_multnomah as pilot
    import build as etl
    index, by_pid = pilot.load_entities(
        os.path.join(os.path.dirname(base), "..", "build", "pf.sqlite")
        if False else os.path.join(REPO, "build", "pf.sqlite"))

    by_city = defaultdict(set)
    for row in rows:
        city = (row.get("CITY") or "").strip()
        if not city or city.lower() == "none":
            continue
        for column in PRECINCT_GOVERNMENTS:
            raw = (row.get(column) or "").strip()
            if not raw or raw.lower() == "none":
                continue
            name = pilot.canonical(raw, pilot.COLUMNS.get(column, (None, None, None))[1])
            if name:
                by_city[city].add(name)

    stacks = defaultdict(set)
    for city, names in by_city.items():
        for name in names:
            pid6, _ = etl.resolve_slug(name, "special_district", index)
            if pid6:
                stacks[city.upper()].add(pid6)
    return stacks


def build(out_dir):
    db_path = os.path.join(out_dir, "pf.sqlite")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    places = {row["geo_id"]: dict(row) for row in conn.execute(
        "SELECT g.geo_id, g.pid6, e.common_name, e.legal_name, e.host_county, "
        "       e.host_county_pid6 "
        "FROM geo_entity g JOIN entities e ON e.pid6 = g.pid6 WHERE g.layer='place'")}
    counties = {(row["host_county"] or "").upper(): row["pid6"] for row in conn.execute(
        "SELECT pid6, COALESCE(common_name, legal_name) AS host_county FROM entities "
        "WHERE gov_type_name='County'")}
    county_by_name = {(row["name"] or "").upper(): row["pid6"] for row in conn.execute(
        "SELECT pid6, COALESCE(common_name, legal_name) AS name FROM entities "
        "WHERE gov_type_name='County'")}

    place_shapes = {f["properties"].get("GEOID"): f for f in load_layer(out_dir, "place")}
    overlap_layers = [("school_district", "GEOID"), ("special_district", "pid6")]
    shapes = []
    for layer, key in overlap_layers:
        for feature in load_layer(out_dir, layer):
            geo_id = feature["properties"].get(key)
            row = conn.execute(
                "SELECT pid6 FROM geo_entity WHERE layer=? AND geo_id=?",
                (layer, geo_id)).fetchone()
            if row:
                shapes.append((row["pid6"], feature["geometry"], bounds(feature["geometry"])))

    exact = precinct_stack(conn)

    rows = []
    for geo_id, place in places.items():
        shape = place_shapes.get(geo_id)
        if not shape:
            continue
        name = (place["common_name"] or place["legal_name"] or "").upper()

        # The county is exact from the register, and so is the place itself.
        if place["host_county_pid6"]:
            rows.append((place["pid6"], place["host_county_pid6"], "register", "county"))

        seen = set()
        for pid6 in exact.get(name, ()):
            if pid6 != place["pid6"]:
                rows.append((place["pid6"], pid6, "precinct_exact", "overlaps"))
                seen.add(pid6)

        points = sample(shape["geometry"])
        for pid6, geometry, box in shapes:
            if pid6 == place["pid6"] or pid6 in seen:
                continue
            if not box:
                continue
            hit = False
            for point in points:
                if not (box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]):
                    continue
                if contains(geometry, point):
                    hit = True
                    break
            if hit:
                rows.append((place["pid6"], pid6, "boundary_overlap", "overlaps"))
                seen.add(pid6)

    conn.execute("DROP TABLE IF EXISTS serves_place")
    conn.execute("""
        CREATE TABLE serves_place (
            place_pid6   TEXT,   -- the city or town a reader picked
            pid6         TEXT,   -- a government that serves it
            basis        TEXT,   -- precinct_exact | boundary_overlap | register
            relation     TEXT    -- overlaps | county
        )""")
    conn.executemany("INSERT INTO serves_place VALUES (?,?,?,?)", rows)
    conn.execute("CREATE INDEX IF NOT EXISTS serves_place_place "
                 "ON serves_place(place_pid6)")
    conn.commit()

    by_basis = defaultdict(int)
    for _, _, basis, _ in rows:
        by_basis[basis] += 1
    covered = len({r[0] for r in rows})
    per_place = sorted(defaultdict(int, {
        p: sum(1 for r in rows if r[0] == p) for p in {r[0] for r in rows}}).values())
    conn.close()

    return {
        "places_with_a_stack": covered,
        "relationships": len(rows),
        "by_basis": dict(by_basis),
        "median_governments_per_place": per_place[len(per_place) // 2] if per_place else 0,
        "most": per_place[-1] if per_place else 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(REPO, "build"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    report = build(os.path.abspath(args.out))
    if not args.quiet:
        print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
    sys.exit(0)
