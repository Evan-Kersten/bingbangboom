#!/usr/bin/env python3
"""Build the Public Foundry analytical store from the source files.

Reads the repository as it stands and produces a queryable database plus the
crosswalks, guardrail flags and availability manifest that the runtime tools
depend on. Standard library only.

    python3 etl/build.py [--out build]

Outputs, all under the output directory:

    pf.sqlite            the store, for verification and local development
    load_duckdb.sql      DDL and COPY statements to build the same store in DuckDB
    tables/*.csv         the normalized tables, portable between engines
    geo/*.geojson        boundary layers converted from the shapefiles
    defects.json         source data problems found and what was done about them
    build_report.json    row counts, join coverage, flag frequencies

The guardrail flags are the point of this build. The system prompt specifies
which computations are meaningful on this data; a query engine cannot enforce
that, so the conditions are evaluated once here and travel with the rows.
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

import peers
import shapefile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FISCAL_SOURCES = [
    ("oregon-state-fiscal-context.jsonl", "state"),
    ("oregon-counties-fiscal-context.jsonl", "counties"),
    ("oregon-places-fiscal-context.jsonl", "places"),
    ("oregon-school-districts-fiscal-context.jsonl", "school_districts"),
    ("oregon-special-districts-fiscal-context.jsonl", "special_districts"),
]

GOV_CSV = "Governments - General Purpose - OR Govs Simplified.csv"
OFFICE_CSV = "OR Office Taxonomy.csv"
DP03_VINTAGES = [2019, 2023, 2024]

# §8: a peer median at or near zero is not a midpoint. Below this, the pool is
# mostly entities reporting no activity and a distance calculation is refused.
DEGENERATE_MEDIAN = 3.0

defects = []


def note_defect(kind, detail, affected, action):
    defects.append({"defect": kind, "detail": detail, "affected": affected, "action": action})


# ---------------------------------------------------------------- utilities

# Census legal names abbreviate; OCD slugs spell out. Expanded before keying so
# "CENTRAL OREGON CMTY COLLEGE" and "central_oregon_community_college" agree.
ABBREVIATIONS = {
    "sch": "school", "dist": "district", "cmty": "community", "cnty": "county",
    "co": "county", "util": "utility", "utils": "utility", "svc": "service",
    "svcs": "service", "dept": "department", "prot": "protection",
    "rec": "recreation", "cons": "conservation", "irr": "irrigation",
    "san": "sanitary", "sd": "school district", "esd": "education service district",
    "rfpd": "rural fire protection district", "hosp": "hospital",
    "mem": "memorial", "twp": "township", "jr": "junior", "reg": "regional",
    "pud": "peoples utility district", "peoples": "peoples", "people": "peoples",
}


def expand(text):
    # A trailing parenthetical is a nickname, not part of the legal name:
    # "... District of Oregon (TriMet)" is one government, not two.
    text = re.sub(r"\([^)]*\)", " ", (text or ""))
    words = re.split(r"[^a-z0-9]+", text.lower())
    return " ".join(ABBREVIATIONS.get(w, w) for w in words if w)


def norm(text):
    """Aggressive name key: lowercase alphanumerics only, abbreviations expanded."""
    return re.sub(r"[^a-z0-9]", "", expand(text))


def loose(text):
    """Normalized key with the characters Census scans confuse folded together.

    "ASTORIA SCH DIST IC" and "astoria_school_district_1c" are the same district;
    the difference is a letter I read for a digit 1.
    """
    return norm(text).translate(str.maketrans({"i": "1", "l": "1", "o": "0"}))


def tokens(text):
    stop = {"of", "the", "and", "district", "no", "number"}
    return {t for t in expand(text).split() if t and t not in stop}


def jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def num(value):
    """Parse a Census cell into a float, or None where it is not a measurement."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "(X)", "N", "**", "***", "*****", "null"):
        return None
    text = text.rstrip("+").lstrip("+")
    try:
        return float(text)
    except ValueError:
        return None


def get(record, *path, default=None):
    """Walk nested dicts, tolerating None at any level."""
    cursor = record
    for key in path:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
        if cursor is None:
            return default
    return cursor


# ------------------------------------------------------------ fiscal blocks

def load_entities():
    entities, raw = [], {}
    for filename, label in FISCAL_SOURCES:
        path = os.path.join(REPO, filename)
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                pid6 = str(record["pid6"])
                raw[pid6] = record
                entities.append({
                    "pid6": pid6,
                    "legal_name": record.get("name"),
                    "common_name": record.get("alias"),
                    "gov_type_id": record.get("governmentTypeId"),
                    "gov_type_name": record.get("governmentTypeName"),
                    "state_fips": record.get("stateFips"),
                    "county_fips": record.get("countyFips"),
                    "place_fips": record.get("placeFips"),
                    "geoid": record.get("geoid"),
                    "gidid": record.get("gidid"),
                    "host_county": record.get("hostCounty"),
                    "host_county_pid6": record.get("hostCountyPid6"),
                    "is_dependent": 1 if record.get("isDependent") else 0,
                    "website": record.get("website"),
                    "sd_lea": record.get("sdLea"),
                    "school_district_type": record.get("schoolDistrictTypeName"),
                    "source_file": label,
                })
    return entities, raw


def build_fiscal_tables(raw, type_counts):
    out = defaultdict(list)
    flags = {}

    for pid6, record in raw.items():
        gov_type = record.get("governmentTypeName")
        flag = {
            "pid6": pid6,
            "prior_year_missing": 0, "debt_capped": 0, "capital_median_degenerate": 0,
            "other_share_high": 0, "concentration_total": 0, "peer_group_mismatch": 0,
            "peer_count_disagrees": 0, "no_population_denominator": 0,
            "revenue_volatility_absent": 0, "capital_share_active": 0,
            "capital_share_lead": 0, "personnel_share_unusual": 0,
        }

        # --- fiscal stability -------------------------------------------------
        fs = record.get("fiscalStability")
        if fs:
            prior = fs.get("priorYearScore")
            score = fs.get("fiscalStabilityScore")
            yoy = fs.get("yoyChange")
            # The source encodes "no prior year" as 0. Left alone this reads as a
            # near-full-scale improvement and trips the §11 attention threshold.
            if prior == 0 and (score or 0) > 0:
                prior, yoy = None, None
                flag["prior_year_missing"] = 1

            stated = get(fs, "benchmark", "peerGroup", "count")
            criteria = get(fs, "benchmark", "peerGroup", "criteria") or ""
            observed = type_counts.get(gov_type)
            if gov_type and norm(gov_type) not in norm(criteria):
                flag["peer_group_mismatch"] = 1
            if stated and observed and stated != observed:
                flag["peer_count_disagrees"] = 1

            if get(fs, "components", "debtToRevenueRatio", "score") == 0:
                flag["debt_capped"] = 1

            out["fiscal_stability"].append({
                "pid6": pid6, "year": fs.get("year"), "score": score,
                "rating_label": fs.get("ratingLabel"), "prior_year_score": prior,
                "yoy_change": yoy,
                "structural_balance": get(fs, "components", "structuralBalance", "value"),
                "structural_balance_score": get(fs, "components", "structuralBalance", "score"),
                "debt_to_revenue": get(fs, "components", "debtToRevenueRatio", "value"),
                "debt_to_revenue_score": get(fs, "components", "debtToRevenueRatio", "score"),
                "peer_group": criteria or None, "peer_count_stated": stated,
                "peer_count_observed": observed,
                "peer_min": get(fs, "benchmark", "peerStats", "min"),
                "peer_min_name": get(fs, "benchmark", "peerStats", "minGovernmentName"),
                "peer_median": get(fs, "benchmark", "peerStats", "median"),
                "peer_max": get(fs, "benchmark", "peerStats", "max"),
                "peer_max_name": get(fs, "benchmark", "peerStats", "maxGovernmentName"),
            })

        # --- operating vs capital --------------------------------------------
        ovc = record.get("operatingVsCapital")
        if ovc:
            median = get(ovc, "benchmarks", "peerStats", "median")
            degenerate = 1 if (median is not None and median < DEGENERATE_MEDIAN) else 0
            flag["capital_median_degenerate"] = degenerate

            capital_pct = ovc.get("capitalPercentage")
            if capital_pct is not None:
                flag["capital_share_active"] = 1 if capital_pct > 20 else 0
                flag["capital_share_lead"] = 1 if capital_pct > 30 else 0

            criteria = get(ovc, "benchmarks", "peerGroup", "criteria") or ""
            if criteria and gov_type and norm(gov_type) not in norm(criteria):
                flag["peer_group_mismatch"] = 1

            out["operating_vs_capital"].append({
                "pid6": pid6, "year": ovc.get("year"),
                "operating_expenditure": ovc.get("operatingExpenditure"),
                "capital_expenditure": ovc.get("capitalExpenditure"),
                "total_expenditure": ovc.get("totalExpenditure"),
                "operating_pct": ovc.get("operatingPercentage"),
                "capital_pct": capital_pct,
                "yoy_change": ovc.get("yoyChange"),
                "yoy_pct_change": ovc.get("yoyPercentChange"),
                "previous_year": ovc.get("previousYear"),
                "peer_group": criteria or None,
                "peer_count_stated": get(ovc, "benchmarks", "peerGroup", "count"),
                "peer_low": get(ovc, "benchmarks", "peerStats", "low"),
                "peer_median": median,
                "peer_high": get(ovc, "benchmarks", "peerStats", "high"),
                "peer_median_degenerate": degenerate,
            })

        # --- revenue volatility ----------------------------------------------
        rv = record.get("revenueVolatility")
        if rv:
            cov = rv.get("coefficientOfVariation")
            out["revenue_volatility"].append({
                "pid6": pid6, "earliest_year": rv.get("earliestYear"),
                "latest_year": rv.get("latestYear"), "num_yoy_pairs": rv.get("numYoyPairs"),
                "volatility_score": rv.get("volatilityScore"),
                "coefficient_of_variation": cov,
                "mean_yoy_change": rv.get("meanYoyChange"),
                "stddev_yoy_change": rv.get("stddevYoyChange"),
                "swings_dominate_trend": 1 if (cov is not None and cov > 1.0) else 0,
            })
            for pair in rv.get("yoyChangesDetail") or []:
                out["revenue_volatility_yoy"].append({
                    "pid6": pid6, "year_pair": pair.get("yearPair"),
                    "prev_revenue": pair.get("prevRevenue"),
                    "current_revenue": pair.get("currentRevenue"),
                    "yoy_change": pair.get("yoyChange"),
                })
            if cov is None:
                flag["revenue_volatility_absent"] = 1
        else:
            flag["revenue_volatility_absent"] = 1

        # --- spending by service area ----------------------------------------
        sba = record.get("spendingByServiceArea")
        if sba:
            year = sba.get("year")
            for area in sba.get("serviceAreas") or []:
                name = get(area, "serviceArea", "name")
                pct = area.get("percentage")
                if name == "Other" and (pct or 0) > 20:
                    flag["other_share_high"] = 1
                if (pct or 0) > 90:
                    flag["concentration_total"] = 1
                out["spending_by_service_area"].append({
                    "pid6": pid6, "year": year, "service_area": name,
                    "total": area.get("total"), "percentage": pct,
                })

        # --- workforce --------------------------------------------------------
        wsa = record.get("workforceByServiceArea")
        wcp = record.get("workforceCompensationProfile")
        population = wsa.get("population") if wsa else None
        if not population:
            flag["no_population_denominator"] = 1

        if wsa or wcp:
            personnel_pct = wcp.get("personnelCostPercentage") if wcp else None
            if personnel_pct is not None and (personnel_pct < 35 or personnel_pct > 65):
                flag["personnel_share_unusual"] = 1
            out["workforce_profile"].append({
                "pid6": pid6,
                "year": (wcp or wsa or {}).get("year"),
                "total_employees": (wsa or wcp or {}).get("totalEmployees"),
                "population": population,
                "employees_per_1000": (wsa or {}).get("employeesPer1000") or (wcp or {}).get("employeesPerThousand"),
                "total_annual_payroll": (wcp or {}).get("totalAnnualPayroll"),
                "full_time_ratio": (wcp or {}).get("fullTimeRatio"),
                "average_annual_wage": (wcp or {}).get("averageAnnualWage"),
                "personnel_cost_pct": personnel_pct,
                "operating_expenditures": (wcp or {}).get("operatingExpenditures"),
            })
        if wsa:
            for area in wsa.get("serviceAreas") or []:
                out["workforce_by_service_area"].append({
                    "pid6": pid6, "year": wsa.get("year"),
                    "service_area": get(area, "serviceArea", "name"),
                    "headcount": area.get("headcount"), "percentage": area.get("percentage"),
                })
        if wcp:
            for fn in wcp.get("topEmploymentFunctions") or []:
                out["workforce_top_functions"].append({
                    "pid6": pid6, "year": wcp.get("year"),
                    "function_name": get(fn, "employmentFunction", "name"),
                    "function_code": get(fn, "employmentFunction", "code"),
                    "headcount": fn.get("headcount"), "headcount_yoy": fn.get("headcountYoY"),
                    "average_wage": fn.get("averageWage"), "pct_of_total": fn.get("percentOfTotal"),
                    "total_payroll": fn.get("totalPayroll"),
                })

        # --- financial functions ---------------------------------------------
        po = record.get("procurementOpportunity")
        if po:
            for fn in po.get("financialFunctions") or []:
                out["financial_functions"].append({
                    "pid6": pid6, "year": po.get("year"),
                    "function_name": fn.get("financialFunctionName"),
                    "service_area": fn.get("serviceArea"),
                    "operating_expenditures": fn.get("operatingExpenditures"),
                    "capital_expenditures": fn.get("capitalExpenditures"),
                    "excluded_amounts": fn.get("excludedAmounts"),
                    "personnel_costs_adjusted": fn.get("personnelCostsAdjusted"),
                    "operating_pae": fn.get("operatingPae"), "capital_pae": fn.get("capitalPae"),
                    "total_function_pae": fn.get("totalFunctionPae"),
                    "pct_of_entity_total": fn.get("pctOfEntityTotal"),
                })

        # --- trends and revenue sources --------------------------------------
        ft = record.get("financialTrends")
        if ft:
            for year_row in ft.get("history") or []:
                out["financial_trends"].append({
                    "pid6": pid6, "year": year_row.get("year"),
                    "revenue": year_row.get("revenue"), "expenditure": year_row.get("expenditure"),
                    "total_debt": year_row.get("totalDebt"),
                    "revenue_yoy": year_row.get("revenueYoY"),
                    "expenditure_yoy": year_row.get("expenditureYoY"),
                })

        prs = record.get("primaryRevenueSources")
        if prs:
            for source in prs.get("revenueSources") or []:
                out["revenue_sources"].append({
                    "pid6": pid6, "year": prs.get("year"),
                    "category": source.get("category"), "amount": source.get("amount"),
                    "percentage": source.get("percentage"),
                    "is_top_three": 1 if source.get("isTopThree") else 0,
                })

        flags[pid6] = flag

    return out, flags


# ------------------------------------------------------- office crosswalk

OCD_LEVELS = ("place", "county", "school_district", "special_district")

# Legislative, congressional and judicial districts are constituencies, not
# governments (§7). They have no entity to match and are recorded as such
# rather than being counted as match failures.
NON_GOVERNMENT_LEVELS = ("sldl", "sldu", "cd", "circuit_court", "state")


def ocd_level_and_slug(ocd_id):
    """Return the most specific governing level and its slug.

    A board_district or council_district segment is an electoral district inside
    a government, not a government (§7), so the slug is taken from the parent.
    """
    parts = ocd_id.split("/")
    level = slug = None
    for part in parts:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        if key in OCD_LEVELS:
            level, slug = key, value
    if level is None:
        for part in parts:
            if ":" in part:
                key, value = part.split(":", 1)
                if key not in ("country", "state"):
                    return key, value
        return "state", None
    return level, slug


FUZZY_ACCEPT = 0.70   # token overlap below this is not a candidate at all
FUZZY_MARGIN = 0.15   # best must beat the runner-up by this much to be unambiguous

CONFIDENCE_BY_METHOD = {
    "alias_exact": "high", "legal_name_exact": "high", "county_pattern": "high",
    "loose_exact": "medium", "token_exact": "medium", "token_overlap": "low",
}


def resolve_slug(slug, level, index):
    """Resolve one OCD slug to a pid6. Returns (pid6, method).

    Tiers descend in reliability, and the fuzzy tier only fires when a single
    candidate stands clear of the field. §7 requires that a low-confidence match
    is never presented as established, so the tier is recorded, not smoothed over.
    """
    readable = slug.replace("_", " ")
    key, loose_key, token_set = norm(readable), loose(readable), tokens(readable)

    if key in index["alias"]:
        return index["alias"][key], "alias_exact"
    if key in index["legal"]:
        return index["legal"][key], "legal_name_exact"

    if level == "county":
        # OCD names counties bare ("baker"); Census uses "COUNTY OF BAKER".
        for candidate in (f"countyof{key}", f"{key}county"):
            for table in ("alias", "legal"):
                if candidate in index[table]:
                    return index[table][candidate], "county_pattern"

    if loose_key in index["loose"]:
        return index["loose"][loose_key], "loose_exact"

    exact_tokens = index["tokens"].get(frozenset(token_set))
    if exact_tokens and len(exact_tokens) == 1:
        return exact_tokens[0], "token_exact"

    scored = sorted(
        ((jaccard(token_set, cand_tokens), pid6)
         for cand_tokens, pid6 in index["token_list"]
         if token_set & cand_tokens),
        reverse=True,
    )
    if scored and scored[0][0] >= FUZZY_ACCEPT:
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if scored[0][0] - runner_up >= FUZZY_MARGIN:
            return scored[0][1], "token_overlap"

    return None, None


def build_office_tables(entities):
    index = {"alias": {}, "legal": {}, "loose": {}, "tokens": defaultdict(list), "token_list": []}
    for entity in entities:
        name = entity["common_name"] or entity["legal_name"]
        if entity["common_name"]:
            index["alias"].setdefault(norm(entity["common_name"]), entity["pid6"])
        index["legal"].setdefault(norm(entity["legal_name"]), entity["pid6"])
        index["loose"].setdefault(loose(name), entity["pid6"])
        token_set = tokens(name)
        index["tokens"][frozenset(token_set)].append(entity["pid6"])
        index["token_list"].append((token_set, entity["pid6"]))

    offices, matches = [], {}
    with open(os.path.join(REPO, OFFICE_CSV), encoding="utf-8-sig") as fh:
        for position, row in enumerate(csv.DictReader(fh)):
            ocd_id = row["ocd_id"]
            level, slug = ocd_level_and_slug(ocd_id)
            offices.append({
                "office_id": position, "ocd_id": ocd_id, "ocd_level": level,
                "role": row["role"] or None,
                "office_position": row["office_position"] or None,
                "partisan_election": row["partisan_election"] or None,
                "occupation_method": row["occupation_method"] or None,
                "term_length": row["term_length"] or None,
            })
            if ocd_id in matches:
                continue

            if level in NON_GOVERNMENT_LEVELS or slug is None:
                matches[ocd_id] = {"ocd_id": ocd_id, "pid6": None,
                                   "match_method": "not_a_government", "confidence": "none"}
                continue

            pid6, method = resolve_slug(slug, level, index)
            matches[ocd_id] = {
                "ocd_id": ocd_id, "pid6": pid6,
                "match_method": method or "unmatched",
                "confidence": CONFIDENCE_BY_METHOD.get(method, "none"),
            }

    return offices, list(matches.values())


# ------------------------------------------------------------------- DP03

def build_dp03():
    rows, variables = [], []
    for vintage in DP03_VINTAGES:
        path = os.path.join(REPO, f"ACSDP5Y{vintage}.DP03-Data.csv")
        # The 2024 file carries a UTF-8 BOM; utf-8-sig strips it where present.
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            codes = next(reader)
            labels = next(reader)
            index = {code: i for i, code in enumerate(codes)}
            estimates = [c for c in codes if c.startswith("DP03_") and c.endswith("E")]

            for code in estimates:
                variables.append({"variable": code[:-1], "vintage": vintage,
                                  "label": labels[index[code]]})

            for record in reader:
                if not record or not record[0]:
                    continue
                geo_id = record[index["GEO_ID"]]
                geo_name = record[index["NAME"]]
                geo_id = geo_id.split("US")[-1] if "US" in geo_id else geo_id
                geo_level = "county" if geo_name.endswith("County, Oregon") else "place"
                for code in estimates:
                    moe_code = code[:-1] + "M"
                    estimate = num(record[index[code]])
                    moe = num(record[index[moe_code]]) if moe_code in index else None
                    if estimate is None and moe is None:
                        continue
                    rows.append({"geo_id": geo_id, "geo_name": geo_name, "geo_level": geo_level,
                                 "vintage": vintage, "variable": code[:-1],
                                 "estimate": estimate, "moe": moe})
    return rows, variables


# -------------------------------------------------------------- geometry

GEO_LAYERS = [
    ("county", "cb_2018_us_county_500k/cb_2018_us_county_500k", "41"),
    ("place", "cb_2018_41_place_500k/cb_2018_41_place_500k", None),
    ("school_district", "cb_2018_41_unsd_500k/cb_2018_41_unsd_500k", None),
]


def build_geometry(entities, out_dir):
    os.makedirs(os.path.join(out_dir, "geo"), exist_ok=True)
    layers, geo_rows = {}, []

    for layer, base, state_filter in GEO_LAYERS:
        collection = shapefile.read(os.path.join(REPO, base))
        if state_filter:
            collection["features"] = [
                f for f in collection["features"]
                if f["properties"].get("STATEFP") == state_filter
            ]
        layers[layer] = collection
        with open(os.path.join(out_dir, "geo", f"{layer}.geojson"), "w") as fh:
            json.dump(collection, fh)

    county_ids = {f["properties"]["GEOID"] for f in layers["county"]["features"]}
    place_ids = {f["properties"]["GEOID"] for f in layers["place"]["features"]}
    school_by_name = {norm(f["properties"]["NAME"]): f["properties"]["GEOID"]
                      for f in layers["school_district"]["features"]}
    school_by_loose = {loose(f["properties"]["NAME"]): f["properties"]["GEOID"]
                       for f in layers["school_district"]["features"]}

    for entity in entities:
        gov_type, geoid, pid6 = entity["gov_type_name"], entity["geoid"], entity["pid6"]
        row = None
        if gov_type == "County" and geoid in county_ids:
            row = (pid6, "county", geoid, "geoid_exact", "high")
        elif gov_type == "Municipal" and geoid in place_ids:
            row = (pid6, "place", geoid, "geoid_exact", "high")
        elif gov_type == "School District":
            # No geoid on any school district record, so geometry is reached by
            # name only, and the match confidence travels with the row.
            name = entity["common_name"] or entity["legal_name"]
            if norm(name) in school_by_name:
                row = (pid6, "school_district", school_by_name[norm(name)],
                       "name_normalized", "medium")
            elif loose(name) in school_by_loose:
                row = (pid6, "school_district", school_by_loose[loose(name)],
                       "name_loose", "low")
        if row:
            geo_rows.append(dict(zip(
                ("pid6", "layer", "geo_id", "match_method", "confidence"), row)))

    # Multnomah precinct splits carry special-district membership per polygon.
    # Dissolving them is the only special-district geography available, and it
    # covers one county, so it is written out but deliberately not joined.
    precincts = shapefile.read(os.path.join(
        REPO, "March_2026_Multco_PrecinctSplit_Shapefile/Precinct_Split_March2024_Final"))
    with open(os.path.join(out_dir, "geo", "multnomah_precinct_splits.geojson"), "w") as fh:
        json.dump(precincts, fh)

    return geo_rows, layers, precincts


# --------------------------------------------------------------- assembly

def build_availability(entities, tables, office_matches, geo_rows, dp03_rows):
    pid_keyed = ("fiscal_stability", "operating_vs_capital", "revenue_volatility",
                 "spending_by_service_area", "workforce_profile", "financial_functions",
                 "financial_trends", "revenue_sources")
    present = {name: {row["pid6"] for row in tables.get(name, [])} for name in pid_keyed}
    office_pids = {m["pid6"] for m in office_matches if m["pid6"]}
    geo_pids = {row["pid6"] for row in geo_rows}
    dp03_geos = {row["geo_id"] for row in dp03_rows}
    populated = {row["pid6"] for row in tables.get("workforce_profile", []) if row["population"]}

    rows = []
    for entity in entities:
        pid6 = entity["pid6"]
        rows.append({
            "pid6": pid6,
            "has_fiscal_stability": int(pid6 in present.get("fiscal_stability", set())),
            "has_operating_vs_capital": int(pid6 in present.get("operating_vs_capital", set())),
            "has_revenue_volatility": int(pid6 in present.get("revenue_volatility", set())),
            "has_spending_breakdown": int(pid6 in present.get("spending_by_service_area", set())),
            "has_workforce": int(pid6 in present.get("workforce_profile", set())),
            "has_financial_functions": int(pid6 in present.get("financial_functions", set())),
            "has_financial_trends": int(pid6 in present.get("financial_trends", set())),
            "has_revenue_sources": int(pid6 in present.get("revenue_sources", set())),
            "has_offices": int(pid6 in office_pids),
            "has_geometry": int(pid6 in geo_pids),
            "has_dp03": int((entity["geoid"] or "") in dp03_geos),
            "has_population": int(pid6 in populated),
        })
    return rows


def write_sqlite(path, tables):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")) as fh:
        conn.executescript(fh.read())
    for name, rows in tables.items():
        if not rows:
            continue
        columns = list(rows[0].keys())
        placeholders = ",".join("?" * len(columns))
        conn.executemany(
            f"INSERT INTO {name} ({','.join(columns)}) VALUES ({placeholders})",
            [tuple(row[c] for c in columns) for row in rows],
        )
    conn.commit()
    return conn


def write_csvs(out_dir, tables):
    directory = os.path.join(out_dir, "tables")
    os.makedirs(directory, exist_ok=True)
    for name, rows in tables.items():
        if not rows:
            continue
        with open(os.path.join(directory, f"{name}.csv"), "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


def write_duckdb_loader(out_dir, tables):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")) as fh:
        schema = fh.read()
    lines = [
        "-- Build the store in DuckDB. Run from the repository root:",
        "--     duckdb build/pf.duckdb < build/load_duckdb.sql",
        "INSTALL spatial; LOAD spatial;",
        "",
        schema,
        "",
    ]
    for name, rows in tables.items():
        if rows:
            lines.append(f"COPY {name} FROM 'build/tables/{name}.csv' (HEADER, AUTO_DETECT true);")
    lines += [
        "",
        "-- Boundary layers read straight from the shapefiles by the spatial extension.",
        "CREATE TABLE geo_county AS SELECT * FROM ST_Read('cb_2018_us_county_500k/cb_2018_us_county_500k.shp') WHERE STATEFP = '41';",
        "CREATE TABLE geo_place AS SELECT * FROM ST_Read('cb_2018_41_place_500k/cb_2018_41_place_500k.shp');",
        "CREATE TABLE geo_school_district AS SELECT * FROM ST_Read('cb_2018_41_unsd_500k/cb_2018_41_unsd_500k.shp');",
        "CREATE TABLE geo_multnomah_precincts AS SELECT * FROM ST_Read('March_2026_Multco_PrecinctSplit_Shapefile/Precinct_Split_March2024_Final.shp');",
    ]
    with open(os.path.join(out_dir, "load_duckdb.sql"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(REPO, "build"))
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    entities, raw = load_entities()
    type_counts = Counter(e["gov_type_name"] for e in entities)

    tables, flags = build_fiscal_tables(raw, type_counts)
    tables["entities"] = entities
    tables["entity_flags"] = list(flags.values())

    offices, office_matches = build_office_tables(entities)
    tables["offices"] = offices
    tables["office_entity_match"] = office_matches

    dp03_rows, dp03_variables = build_dp03()
    tables["dp03"] = dp03_rows
    tables["dp03_variables"] = dp03_variables

    geo_rows, layers, precincts = build_geometry(entities, out_dir)
    tables["geo_entity"] = geo_rows

    tables["data_availability"] = build_availability(
        entities, tables, office_matches, geo_rows, dp03_rows)

    # ---- defects found while building -------------------------------------
    note_defect(
        "prior_year_encoded_as_zero",
        "priorYearScore of 0 means no prior year, but yoyChange was computed against it, "
        "producing a phantom improvement that trips the §11 five-point threshold.",
        sum(f["prior_year_missing"] for f in flags.values()),
        "prior_year_score and yoy_change set to NULL; entity_flags.prior_year_missing set.")
    note_defect(
        "peer_group_mismatch",
        "Benchmark peer pool does not match the entity's own government type. The State of "
        "Oregon is benchmarked on capital share against 12,511 school districts.",
        sum(f["peer_group_mismatch"] for f in flags.values()),
        "entity_flags.peer_group_mismatch set; tools must refuse the comparison.")
    note_defect(
        "peer_count_disagrees",
        "Stated peer count differs from the number of entities of that type in the data.",
        sum(f["peer_count_disagrees"] for f in flags.values()),
        "Both counts stored as peer_count_stated and peer_count_observed.")
    note_defect(
        "degenerate_capital_median",
        f"Capital-share peer median below {DEGENERATE_MEDIAN}. Most of the pool reports no "
        "capital activity, so the median is not a midpoint (§8).",
        sum(f["capital_median_degenerate"] for f in flags.values()),
        "peer_median_degenerate set; distance calculation must be declined.")
    note_defect(
        "school_district_geoid_null",
        "geoid and sdLea are null on every school district record, so there is no key to "
        "boundary or federal education data.",
        sum(1 for e in entities if e["gov_type_name"] == "School District"),
        f"Name matching recovered {sum(1 for g in geo_rows if g['layer'] == 'school_district')} "
        "of them at medium confidence.")
    note_defect(
        "special_district_no_geometry",
        "No boundary file exists for special districts in this repository.",
        sum(1 for e in entities if e["gov_type_name"] == "Special District"),
        "Not mappable. Multnomah precinct splits written to geo/ as the only partial source.")
    note_defect(
        "offices_have_no_holders",
        "OR Office Taxonomy carries role, seat, term and selection method but no holder names.",
        len(offices),
        "Tools answering §7 must state that holders are not in the data.")

    conn = write_sqlite(os.path.join(out_dir, "pf.sqlite"), tables)

    # Peer distributions are computed from the loaded tables rather than shipped
    # in the payload, which only benchmarks the composite score.
    peer_rows = peers.compute(conn)
    peers.write(conn, peer_rows)
    write_csvs(out_dir, tables)
    write_duckdb_loader(out_dir, tables)

    with open(os.path.join(out_dir, "defects.json"), "w") as fh:
        json.dump(defects, fh, indent=2)

    matched = sum(1 for m in office_matches if m["pid6"])
    report = {
        "row_counts": {name: len(rows) for name, rows in sorted(tables.items())},
        "entities_by_type": dict(type_counts),
        "office_crosswalk": {
            "distinct_divisions": len(office_matches),
            "resolved": matched,
            "unresolved": len(office_matches) - matched,
            "by_method": dict(Counter(m["match_method"] for m in office_matches)),
        },
        "geometry_coverage": dict(Counter(g["layer"] for g in geo_rows)),
        "flag_frequencies": {
            key: sum(f[key] for f in flags.values())
            for key in next(iter(flags.values())) if key != "pid6"
        },
        "defects": len(defects),
        "peer_distributions": len(peer_rows),
    }
    conn.close()

    # The dissolve pilot writes service_extent into the same database, so a
    # rebuild drops it unless the rebuild also runs it. Keeping it a separate
    # script that has to be remembered is how the table went missing once
    # already; a build that produces a store missing a table the tools query is
    # not a build.
    try:
        import dissolve_multnomah
        report["service_extent_rows"] = dissolve_multnomah.main(
            argv=["--out", out_dir, "--quiet"])
    except Exception as error:                       # pragma: no cover
        report["service_extent_rows"] = f"skipped: {error}"

    # The same precinct file read the other way round: keeping the zone rather
    # than stripping it recovers the ground each elected seat answers to, and
    # district_geometry lands in this store too.
    try:
        import dissolve_districts
        report["electoral_districts"] = dissolve_districts.main(
            argv=["--out", out_dir, "--quiet"])
    except Exception as error:                       # pragma: no cover
        report["electoral_districts"] = f"skipped: {error}"

    with open(os.path.join(out_dir, "build_report.json"), "w") as fh:
        json.dump(report, fh, indent=2)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
