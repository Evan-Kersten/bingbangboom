#!/usr/bin/env python3
"""Reconstruct special district boundaries from Multnomah precinct splits.

This is the pilot that decides whether a statewide special district boundary
layer is worth licensing or building. Special districts are two thirds of
Oregon's governments and have no boundaries anywhere in this repository, so the
question is what a boundary layer would actually buy.

The precinct-split file carries, per polygon, which fire, water, sewer, transit,
utility, community college, soil and water, and education service district that
polygon sits in. Grouping polygons by that attribute recovers an approximate
district boundary. Approximate because the edges are precinct edges, not the
district's own filed boundary, and because the vintage is March 2024 despite the
directory name.

Two cautions drive the code. Several columns record **board zones rather than
governments**: "Portland Community College Zone 3" and "ESD Multnomah Zone 5"
are constituencies inside a government, and §7 calls treating them as
governments the most common structural error available in this data. So zone
suffixes are stripped per column, with the patterns pinned to columns where the
pattern was actually observed rather than applied blindly.

And several recovered districts are hosted in another county in the fiscal data.
That is the point of §13.11: published data assigns a district to the county
holding most of its assessed value, so host-county grouping under-captures
service areas. The precinct file shows the service extent directly, and counting
those cases is most of this pilot's value.

    python3 etl/dissolve_multnomah.py [--out build]
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

import build as etl
import project
import shapefile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = "March_2026_Multco_PrecinctSplit_Shapefile/Precinct_Split_March2024_Final"

# column -> (what it is, zone pattern to strip, is it a special district)
COLUMNS = {
    "FIRE_DIST":  ("Fire protection", None, True),
    "WaterDist":  ("Water supply", None, True),
    "SewerDist":  ("Sanitary", None, True),
    "TRAN_DIST":  ("Mass transit", None, True),
    "UFSWQD":     ("Water quality", None, True),
    "PUD":        ("People's utility", r"\s+(?:Sub-)?D(?:istrict)?\.?\s*\d+$", True),
    "CommColleg": ("Community college", r"\s+Zone\s+\d+$", True),
    "Soil_Water": ("Soil and water conservation", r"\s+\d+$", True),
    "ESD":        ("Education service", r"\s+Zone\s+\d+$", True),
    "METRO":      ("Regional government", r"\s+District\s+\d+$", True),
    "SchoolDist": ("School district", None, False),
    "CITY":       ("Municipality", None, False),
}


def canonical(value, zone_pattern):
    """Strip a board zone suffix to reach the government it sits inside."""
    name = re.sub(r"\s+", " ", (value or "").strip())
    if not name:
        return None
    if zone_pattern:
        name = re.sub(zone_pattern, "", name).strip()
    return name or None


def load_entities(db_path):
    """Build the same tiered index the ETL uses for the office crosswalk.

    The precinct file names districts its own way, so exact matching recovers
    almost nothing: "ESD Multnomah" has to reach "MULTNOMAH EDUCATION SERVICE
    DISTRICT", which is a word-order inversion after an abbreviation expansion.
    Reusing the ETL matcher means one set of rules, and the match tier travels
    with the boundary so a weak match is never drawn as a fact.
    """
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county FROM entities")]
    conn.close()

    index = {"alias": {}, "legal": {}, "loose": {},
             "tokens": defaultdict(list), "token_list": []}
    by_pid = {}
    for row in rows:
        by_pid[row["pid6"]] = row
        name = row["common_name"] or row["legal_name"]
        if row["common_name"]:
            index["alias"].setdefault(etl.norm(row["common_name"]), row["pid6"])
        index["legal"].setdefault(etl.norm(row["legal_name"]), row["pid6"])
        index["loose"].setdefault(etl.loose(name), row["pid6"])
        token_set = etl.tokens(name)
        index["tokens"][frozenset(token_set)].append(row["pid6"])
        index["token_list"].append((token_set, row["pid6"]))
    return index, by_pid


def dissolve(rows, geometries):
    """Group polygons by government. Rendering a group as one feature without
    internal strokes gives a continuous fill, which is what a dissolve is for
    here; it is not a topological union and the shared edges remain in the data."""
    groups = defaultdict(lambda: {"polygons": [], "zones": set(), "precincts": 0})
    for row, geometry in zip(rows, geometries):
        if geometry is None:
            continue
        for column, (kind, zone_pattern, is_district) in COLUMNS.items():
            raw = row.get(column)
            name = canonical(raw, zone_pattern)
            if not name:
                continue
            key = (column, name)
            groups[key]["polygons"] += geometry["coordinates"]
            groups[key]["precincts"] += 1
            if zone_pattern and raw and raw.strip() != name:
                groups[key]["zones"].add(raw.strip())
    return groups


def write_service_extent(db_path, features):
    """Record which governments actually serve this county.

    This is the pilot's most useful output. list_ecosystem assembles on shared
    host county, and published data files a district under the county holding
    most of its assessed value, so a district serving Multnomah from a
    Washington County filing is invisible to that assembly. The precinct file
    shows service extent directly, so the entities it reveals are written back
    as measured fact rather than inference from names.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS service_extent")
    conn.execute("""
        CREATE TABLE service_extent (
            pid6            TEXT,
            serves_county   TEXT,   -- county the entity actually operates in
            filed_county    TEXT,   -- county it is filed under in the fiscal data
            basis           TEXT,   -- how the extent was established
            match_confidence TEXT
        )""")
    rows = [(f["properties"]["pid6"], "MULTNOMAH",
             (f["properties"]["fiscal_host_county"] or "").upper() or None,
             "multnomah_precinct_dissolve", f["properties"]["match_confidence"])
            for f in features if f["properties"]["pid6"]]
    conn.executemany("INSERT INTO service_extent VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return len(rows)


def write_geo_entity(db_path, features):
    """Register the recovered boundaries so entity_layer can find them.

    geo_entity is built before this script runs, from the Census layers, which
    have nothing for special districts. Appending here rather than rebuilding
    keeps the Census join authoritative for the types it covers; the match
    confidence travels so a low-confidence boundary can be said to be one.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM geo_entity WHERE layer = 'special_district'")
    conn.executemany(
        "INSERT INTO geo_entity (pid6, layer, geo_id, match_method, confidence) "
        "VALUES (?,?,?,?,?)",
        [(f["properties"]["pid6"], "special_district", f["properties"]["pid6"],
          "multnomah_precinct_dissolve", f["properties"]["match_confidence"])
         for f in features])
    conn.commit()
    conn.close()


def main(argv=None):
    """`argv` lets build.py call this in-process as the last ETL step; the
    dissolved boundaries live in the same store, so they are rebuilt with it."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(REPO, "build"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    out_dir = os.path.abspath(args.out)
    os.makedirs(os.path.join(out_dir, "geo"), exist_ok=True)

    base = os.path.join(REPO, SOURCE)
    _, rows = shapefile.read_dbf(base + ".dbf")
    geometries = shapefile.read_shp(base + ".shp")
    index, by_pid = load_entities(os.path.join(out_dir, "pf.sqlite"))

    # The precinct file is Oregon State Plane North in feet; every other layer
    # here is longitude and latitude. Converting at the source means nothing
    # downstream has to know this file was ever projected.
    to_lonlat = project.inverse()
    geometries = [project.reproject_geometry(g, to_lonlat) if g else g
                  for g in geometries]

    groups = dissolve(rows, geometries)

    features, report = [], defaultdict(lambda: {
        "recovered": 0, "matched": 0, "unmatched": [], "other_county": []})

    for (column, name), group in sorted(groups.items()):
        kind, _, is_district = COLUMNS[column]
        pid6, method = etl.resolve_slug(name, "special_district", index)
        entity = by_pid.get(pid6) if pid6 else None
        bucket = report[kind]
        bucket["recovered"] += 1
        if entity:
            bucket["matched"] += 1
            if entity["host_county"] and entity["host_county"].upper() != "MULTNOMAH":
                bucket["other_county"].append(
                    {"name": name, "host_county": entity["host_county"].title()})
        else:
            bucket["unmatched"].append(name)

        features.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "kind": kind,
                "column": column,
                "precinct_polygons": group["precincts"],
                "board_zones_collapsed": sorted(group["zones"]) or None,
                "pid6": entity["pid6"] if entity else None,
                "match": method or "unmatched",
                "match_confidence": etl.CONFIDENCE_BY_METHOD.get(method, "none"),
                "fiscal_host_county": entity["host_county"] if entity else None,
                "is_special_district": is_district,
            },
            "geometry": {"type": "MultiPolygon", "coordinates": group["polygons"]},
        })

    path = os.path.join(out_dir, "geo", "multnomah_dissolved_districts.geojson")
    with open(path, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh)

    write_service_extent(os.path.join(out_dir, "pf.sqlite"), features)

    # The whole point of the pilot was to find out whether special districts can
    # be given boundaries. Nineteen of them can, from this file, and until now
    # that answer sat in a report nobody read while the interface went on saying
    # these governments cannot be put on a map. They become a real layer here.
    recovered = [f for f in features if f["properties"]["pid6"]
                 and f["properties"]["is_special_district"]]
    layer_path = os.path.join(out_dir, "geo", "special_district.geojson")
    with open(layer_path, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"pid6": f["properties"]["pid6"],
                            "NAME": f["properties"]["name"],
                            "kind": f["properties"]["kind"],
                            "match_confidence": f["properties"]["match_confidence"],
                            # Precinct edges, not the district's own filed
                            # boundary, and the renderer says so.
                            "source": "multnomah_precinct_dissolve"},
             "geometry": f["geometry"]}
            for f in recovered]}, fh)
    write_geo_entity(os.path.join(out_dir, "pf.sqlite"), recovered)

    # What fraction of Multnomah's special districts in the fiscal data would a
    # layer like this cover? That is the number the licensing decision turns on.
    import sqlite3
    conn = sqlite3.connect(f"file:{os.path.join(out_dir, 'pf.sqlite')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    multnomah_districts = [dict(r) for r in conn.execute(
        "SELECT e.pid6, e.common_name, e.legal_name FROM entities e "
        "JOIN entities c ON c.pid6 = e.host_county_pid6 "
        "WHERE e.gov_type_name='Special District' AND c.host_county='MULTNOMAH'")]
    conn.close()

    covered = {f["properties"]["pid6"] for f in features
               if f["properties"]["pid6"] and f["properties"]["is_special_district"]}
    in_county_covered = sum(1 for d in multnomah_districts if d["pid6"] in covered)

    summary = {
        "source_vintage": "March 2024 precinct splits, despite the March 2026 directory name",
        "precinct_polygons": len(rows),
        "governments_recovered": len(features),
        "special_districts_recovered": sum(
            1 for f in features if f["properties"]["is_special_district"]),
        "board_zones_collapsed": sum(
            len(f["properties"]["board_zones_collapsed"] or []) for f in features),
        "by_kind": {k: {"recovered": v["recovered"], "matched_to_fiscal_entity": v["matched"],
                        "unmatched": v["unmatched"],
                        "hosted_in_another_county": v["other_county"]}
                    for k, v in sorted(report.items())},
        "multnomah_special_districts_in_fiscal_data": len(multnomah_districts),
        "of_those_given_a_boundary": in_county_covered,
        "coverage_rate": round(in_county_covered / len(multnomah_districts), 3)
        if multnomah_districts else None,
    }

    with open(os.path.join(out_dir, "multnomah_dissolve_report.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    if not args.quiet:
        print(json.dumps(summary, indent=2))
    return summary["special_districts_recovered"]


if __name__ == "__main__":
    # main() returns the district count for build.py, which reports it. A
    # process exit code is not a return value: handing it 31 recovered districts
    # is handing it exit 31, which every CI runner reads as a failure.
    main()
    sys.exit(0)
