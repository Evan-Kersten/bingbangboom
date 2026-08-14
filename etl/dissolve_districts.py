#!/usr/bin/env python3
"""Recover electoral district boundaries from the Multnomah precinct splits.

The sibling script, dissolve_multnomah.py, reads the same file for the opposite
purpose. It strips zone suffixes to reach the *government* underneath, because
§7 calls treating a board zone as a government the most common structural error
available in this data. That is right for assembling an ecosystem.

This script keeps the zone, because the zone is the constituency. "Portland
Community College Zone 3" is not a government and is not claimed to be one here;
it is the piece of ground whose voters fill one seat on that college's board.
The offices table already knows those seats exist —

    ocd-division/country:us/state:or/school_district:portland_community_college
        /board_district:zone_3

— and until now nothing could draw one. A seat count tells a reader a body has
seven members. Only the boundary tells them which of the seven is theirs.

Coverage is Multnomah County, because the precinct-split file is the only source
in this repository that records district assignments per polygon. Everything
here is therefore a slice: a state house district that runs past the county line
is drawn only as far as the county goes, and that is recorded on the feature
rather than left for a reader to misread as the whole district.

    python3 etl/dissolve_districts.py [--out build]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

import build as etl
import project
import shapefile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = "March_2026_Multco_PrecinctSplit_Shapefile/Precinct_Split_March2024_Final"
STATE = "ocd-division/country:us/state:or"

# Districts whose OCD id is fixed by the number alone: there is one Oregon House
# and its district 49 is district 49 wherever the polygon sits.
STATEWIDE = {
    "OR_House":   ("State house district", "State Representative", f"{STATE}/sldl:district_%s"),
    "OR_Senate":  ("State senate district", "State Senator", f"{STATE}/sldu:district_%s"),
    "USCongress": ("Congressional district", "US Representative", f"{STATE}/cd:district_%s"),
    "Mult_Comm":  ("County commissioner district", "County Commissioner",
                   f"{STATE}/county:multnomah/council_district:district_%s"),
    "METRO":      ("Metro council district", "Metro Councilor",
                   f"{STATE}/metro:oregon_metro/council_district:district_%s"),
}

# Board zones inside a government. The government has to be resolved by name
# before the zone can be addressed, so these carry the pattern that separates
# the government's name from the zone number.
BOARD_ZONES = {
    "CommColleg": ("Community college board zone", "Community College District Director",
                   r"\s+Zone\s+(\d+)$"),
    "ESD":        ("Education service district board zone",
                   "Education Service District Director", r"\s+Zone\s+(\d+)$"),
    "Soil_Water": ("Soil and water conservation zone",
                   "Soil and Water Conservation District Board Member", r"\s+(\d+)$"),
    "PUD":        ("People's utility district zone",
                   "Electric Power Utility District Board Member",
                   r"\s+(?:Sub-)?D(?:istrict)?\.?\s*(\d+)$"),
}

# Portland moved to a district-elected council for the November 2024 election.
# The precinct file carries the new districts; the offices table predates them
# and still records four at-large commissioner seats. Both are true of their own
# moment, so the geometry is kept and the disagreement is recorded rather than
# resolved by preferring one source.
CHARTER_PENDING = {
    "CoP_Dist": ("City council district", "Municipal Council Member",
                 f"{STATE}/place:portland"),
}


# The shapefile abbreviates and the entity register spells out, so a literal
# slug match misses governments that are plainly present. These are the
# expansions actually observed in the file, not a general abbreviation list.
ABBREVIATIONS = [
    (r"\bMULT\.?\b", "MULTNOMAH COUNTY"),
    (r"\bP\.?\s*U\.?\s*D\.?\b", "PEOPLES UTILITY DISTRICT"),
    (r"&", "AND"),
    (r"\bSOIL AND WATER\b(?! CONSERVATION)", "SOIL AND WATER CONSERVATION DISTRICT"),
]

# Boards number their seats with either noun. The offices table is the authority
# on which one a given body uses, so both are tried and the one that exists wins.
SUBDISTRICT_NOUNS = ("zone", "district")


def expand(name):
    text = re.sub(r"\s+", " ", (name or "").strip())
    for pattern, replacement in ABBREVIATIONS:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def number(text):
    match = re.search(r"(\d+)", text or "")
    return match.group(1) if match else None


def load_office_index(db_path):
    """Every OCD id the offices table actually knows, and the entity behind it."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    known = {row["ocd_id"] for row in conn.execute("SELECT DISTINCT ocd_id FROM offices")}
    by_ocd = {row["ocd_id"]: row["pid6"] for row in
              conn.execute("SELECT ocd_id, pid6 FROM office_entity_match")}
    conn.close()
    return known, by_ocd


def resolve_zone(name, pattern, index, by_ocd, known):
    """Split 'Portland Community College Zone 3' into its government and its zone.

    The government is resolved through the same tiered slug matcher the office
    crosswalk uses, then the zone is addressed against that government's own OCD
    id. A zone whose government cannot be resolved is returned unmatched rather
    than attached to a guess.
    """
    match = re.search(pattern, name or "")
    if not match:
        return None, None, None
    zone = match.group(1)
    government = re.sub(r"\s+", " ", re.sub(pattern, "", name).strip())
    pid6, method = etl.resolve_slug(government, "special_district", index)
    if not pid6:
        # "EAST MULT. SOIL & WATER" and "ROCKWOOD P.U.D." are the same
        # governments the register spells out in full.
        pid6, method = etl.resolve_slug(expand(government), "special_district", index)
    if not pid6:
        return government, zone, None
    # The government's own OCD id, from whichever of its office records has one.
    parent = next((ocd for ocd, p in by_ocd.items()
                   if p == pid6 and "/board_district:" not in ocd
                   and "/board_distict:" not in ocd
                   and "/council_district:" not in ocd), None)
    if not parent:
        parent = next((re.split(r"/board_distr?ict:", ocd)[0]
                       for ocd, p in by_ocd.items()
                       if p == pid6 and re.search(r"/board_distr?ict:", ocd)), None)
    if not parent:
        return government, zone, None
    for noun in SUBDISTRICT_NOUNS:
        candidate = f"{parent}/board_district:{noun}_{zone}"
        if candidate in known:
            return government, zone, candidate
    return government, zone, None


def outline(polygons, precision=7):
    """The edges on the outside of a group of polygons, chained into polylines.

    Concatenating rings is not a dissolve: drawing it strokes every precinct seam
    inside the district, which on a map reads as a boundary a reader could vote
    across and is the opposite of what the picture is for.

    These polygons come from one topology — precinct splits are cut from the same
    source, so neighbours share vertices exactly — which makes the outline
    available without a geometry library. An edge interior to the group appears
    twice, once in each direction, so cancelling matched pairs leaves exactly the
    outside. What survives is chained into open polylines rather than closed
    rings: closure is where this gets fragile at pinch points, and a stroked
    outline does not need it.

    Returned separately from the fill. The fill stays as the raw polygons, drawn
    without a stroke so neighbouring precincts abut invisibly, and this is drawn
    over it as the only line on the map.
    """
    edges = {}
    for polygon in polygons:
        for ring in polygon:
            points = [(round(x, precision), round(y, precision)) for x, y in ring]
            for a, b in zip(points, points[1:]):
                if a == b:
                    continue
                if edges.get((b, a)):
                    edges[(b, a)] -= 1
                    if not edges[(b, a)]:
                        del edges[(b, a)]
                else:
                    edges[(a, b)] = edges.get((a, b), 0) + 1

    following = {}
    for (a, b), count in edges.items():
        following.setdefault(a, []).extend([b] * count)

    lines = []
    while following:
        start = next(iter(following))
        line = [start]
        current = start
        while following.get(current):
            nxt = following[current].pop()
            if not following[current]:
                del following[current]
            line.append(nxt)
            current = nxt
            if current == start:
                break
        if len(line) > 1:
            lines.append([list(point) for point in line])
    return lines


def dissolve(rows, geometries, index, by_ocd, known):
    """Group precinct polygons into districts, keeping the district identity."""
    groups = defaultdict(lambda: {"polygons": [], "precincts": 0, "raw": set()})

    for row, geometry in zip(rows, geometries):
        if geometry is None:
            continue
        for column in list(STATEWIDE) + list(BOARD_ZONES) + list(CHARTER_PENDING):
            raw = (row.get(column) or "").strip()
            if not raw or raw.lower() == "none":
                continue
            key = (column, re.sub(r"\s+", " ", raw))
            groups[key]["polygons"] += geometry["coordinates"]
            groups[key]["precincts"] += 1
            groups[key]["raw"].add(raw)

    features, skipped = [], []
    for (column, name), group in sorted(groups.items()):
        ocd_id = government = None
        if column in STATEWIDE:
            kind, role, template = STATEWIDE[column]
            index_number = number(name)
            ocd_id = template % index_number if index_number else None
            label = name
        elif column in BOARD_ZONES:
            kind, role, pattern = BOARD_ZONES[column]
            government, index_number, ocd_id = resolve_zone(
                name, pattern, index, by_ocd, known)
            label = name
        else:
            kind, role, government_ocd = CHARTER_PENDING[column]
            index_number = number(name)
            government = "Portland"
            label = f"Portland City Council District {index_number}" if index_number else name

        # Not every value in an electoral column is a district. Mult_Comm carries
        # one stray "c", and the ESD column names neighbouring service districts
        # with no zone at all. Both are real values and neither is a piece of
        # ground that elects somebody, so they are dropped rather than drawn as
        # districts nobody can vote in.
        if index_number is None:
            skipped.append({"column": column, "value": name,
                            "reason": "no district number in the value"})
            continue

        features.append({
            "type": "Feature",
            "properties": {
                "kind": kind,
                "role": role,
                "label": label,
                "government": government,
                "district": index_number,
                "column": column,
                "ocd_id": ocd_id,
                "matches_an_office": bool(ocd_id and ocd_id in known),
                "precinct_polygons": group["precincts"],
                # Multnomah is the only county with precinct-level district
                # assignments here, so anything crossing the county line is cut.
                "coverage": "multnomah_county_only",
            },
            "geometry": {"type": "MultiPolygon", "coordinates": group["polygons"]},
            # The outside of the district, with every interior precinct seam
            # cancelled. Drawn over the fill as the only line on the map.
            "outline": {"type": "MultiLineString",
                        "coordinates": outline(group["polygons"])},
        })
    return features, skipped


def write_table(db_path, features):
    """An index the tool layer can join offices to, without opening the GeoJSON."""
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS district_geometry")
    conn.execute("""
        CREATE TABLE district_geometry (
            ocd_id      TEXT,   -- the seat this ground elects, where one is known
            kind        TEXT,   -- what sort of district it is
            role        TEXT,   -- the office elected from it
            label       TEXT,   -- what a ballot would call it
            government  TEXT,   -- the body the seat sits on, for board zones
            district    TEXT,   -- the number
            feature     INTEGER,-- index into electoral_districts.geojson
            coverage    TEXT    -- how much of the district the geometry covers
        )""")
    conn.executemany(
        "INSERT INTO district_geometry VALUES (?,?,?,?,?,?,?,?)",
        [(f["properties"]["ocd_id"], f["properties"]["kind"], f["properties"]["role"],
          f["properties"]["label"], f["properties"]["government"],
          f["properties"]["district"], i, f["properties"]["coverage"])
         for i, f in enumerate(features)])
    conn.execute("CREATE INDEX IF NOT EXISTS district_geometry_ocd "
                 "ON district_geometry(ocd_id)")
    conn.commit()
    conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(REPO, "build"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    out_dir = os.path.abspath(args.out)
    os.makedirs(os.path.join(out_dir, "geo"), exist_ok=True)
    db_path = os.path.join(out_dir, "pf.sqlite")

    base = os.path.join(REPO, SOURCE)
    _, rows = shapefile.read_dbf(base + ".dbf")
    geometries = shapefile.read_shp(base + ".shp")
    index, _ = dissolve_index(db_path)
    known, by_ocd = load_office_index(db_path)

    # State plane feet in, longitude and latitude out, so a district can be
    # drawn against the state rather than only against its own siblings.
    to_lonlat = project.inverse()
    geometries = [project.reproject_geometry(g, to_lonlat) if g else g
                  for g in geometries]

    features, skipped = dissolve(rows, geometries, index, by_ocd, known)
    path = os.path.join(out_dir, "geo", "electoral_districts.geojson")
    with open(path, "w") as handle:
        json.dump({"type": "FeatureCollection", "features": features}, handle)
    write_table(db_path, features)

    by_kind = defaultdict(lambda: {"districts": 0, "addressable": 0})
    for feature in features:
        bucket = by_kind[feature["properties"]["kind"]]
        bucket["districts"] += 1
        bucket["addressable"] += 1 if feature["properties"]["matches_an_office"] else 0

    report = {
        "source": SOURCE,
        "districts": len(features),
        "addressable": sum(1 for f in features if f["properties"]["matches_an_office"]),
        "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
        "unaddressable": sorted({f["properties"]["kind"] for f in features
                                 if not f["properties"]["matches_an_office"]}),
        "skipped_values": skipped,
        "coverage": "Multnomah County only; districts crossing the county line are cut",
        "geojson": path,
    }
    if not args.quiet:
        print(json.dumps(report, indent=2))
    return report


def dissolve_index(db_path):
    """The tiered name index the office crosswalk uses, reused for zone parents."""
    import dissolve_multnomah
    return dissolve_multnomah.load_entities(db_path)


if __name__ == "__main__":
    main()
    sys.exit(0)
