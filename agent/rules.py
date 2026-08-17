"""The rule catalogue.

Every caveat the tool layer can attach to a result, keyed by code. Each entry
names the section of the system prompt it comes from and carries the guidance
text that travels into the model's context alongside the figure.

This exists so that a rule is stated once. The prompt says what to do; this
module is where the tools reach for the words, so the two cannot drift apart
and so a rule cannot be silently dropped by a model that skimmed section 8.
"""

CAVEATS = {
    "two_expenditure_measures": (
        "10",
        "This data carries two different totals both called expenditure, and they are "
        "not interchangeable. financial_trends.expenditure is one measure; "
        "service_area_totals.total_expenditure is operating plus capital, and it is the "
        "denominator every service-area percentage is computed against. They disagree "
        "for 1,111 of 1,479 entities and by an order of magnitude for some. Name which "
        "one a figure came from whenever both could be meant, and never put a share "
        "from one beside a total from the other."),

    # ---- section 8: finance and workforce -------------------------------
    "debt_capped": (
        "8",
        "Debt-to-revenue scored 0, so the composite is capped near 60 regardless of "
        "operational health. Name the driver before any summary characterization. Do not "
        "present the rating label and the peer position as two separate findings."),
    "prior_year_missing": (
        "8",
        "No prior year is available for this entity. The source encoded that absence as a "
        "zero. Do not report a year-over-year change in the stability score, and do not "
        "describe the score as having improved or worsened."),
    "peer_median_degenerate": (
        "8",
        "The peer median for this metric is at or near zero, which means most of the pool "
        "reports no activity. It is not a midpoint. Decline the distance calculation and "
        "say the entity is among the minority with an active program."),
    "peer_group_mismatch": (
        "8",
        "The benchmark peer pool does not match this entity's government type. The "
        "comparison is not valid and must not be reported."),
    "peer_count_disagrees": (
        "8",
        "The stated peer count differs from the number of entities of this type in the "
        "data. State the peer group definition and use the observed count."),
    "other_share_high": (
        "8",
        "The 'Other' category exceeds 20% of spending. 'Other' is a Census classification "
        "artifact, not a program. Say the breakdown is partial, and do not call the largest "
        "named category the biggest spending area."),
    "capital_lumpy": (
        "8",
        "Capital spending is lumpy and reflects position in a bond and construction cycle. "
        "One year is a point, not a trend. Say the entity is in an active capital period; "
        "do not call it a policy preference."),
    "bridge_is_many_to_many": (
        "8",
        "Finance and workforce are filed under different taxonomies and the crosswalk "
        "between them is many to many in both directions. Fire Protection money covers "
        "two employment functions, firefighters and other fire staff; six separate "
        "public welfare financial functions all land on one employment function. Both "
        "sides are summed before dividing here. Never divide one row by one row: the "
        "result looks plausible whichever way it is wrong."),
    "headcount_from_listed_functions": (
        "8",
        "The denominator is headcount from the entity's listed employment functions, "
        "which the source reports as a top-N rather than in full. A government whose "
        "staff in this function did not make its own list has spending here and no "
        "headcount, so it is excluded rather than divided by a partial number. Say the "
        "figure is per listed employee."),
    "cost_per_head_is_not_a_salary": (
        "4",
        "This is everything the government spent on the function, operating and capital "
        "together, divided by the people it employs in it. It is not a wage and not a "
        "compensation figure: it includes trucks, stations and debt-funded construction. "
        "A high figure is as likely to mean a building year as a generous payroll."),
    "census_year_coverage": (
        "8",
        "The Census of Governments takes a full census in years ending 2 and 7 and "
        "samples in between, so coverage and recency trade against each other here. "
        "2017 and 2022 reach the most governments; 2023 reaches fewer and skews to the "
        "largest, with a mean population around 24,000 against 4,100 for 2022. A "
        "statewide picture wants a census year. A current picture of big governments "
        "wants the latest year and must say it is not the whole state."),
    "peer_pool_mixed_vintage": (
        "8",
        "The entity's figure carries its own year, but the peer statistic it is compared "
        "against does not have one: the pool is built from whichever year each peer last "
        "reported, so a median can blend two or three vintages. Say the entity's year and "
        "say the pool spans several. The comparison is still worth making, because the "
        "alternative is discarding most of the pool, but it is a comparison against what "
        "peers most recently reported rather than against a single moment."),
    "workforce_partial": (
        "8",
        "Only top employment functions are listed. A function not listed is not necessarily "
        "absent, and percentages are shares of listed functions, not of all employees."),
    "vintage_split": (
        "8",
        "Finance data and workforce data describe different years. Do not present them as "
        "the same moment."),

    # ---- section 6: entity types ----------------------------------------
    "no_population_denominator": (
        "6",
        "This entity carries no population or enrollment figure. Per-capita figures are not "
        "computable for it, and must not be compared against another entity's per-capita or "
        "per-enrollment figure."),
    "single_category_entity": (
        "6",
        "This entity type reports essentially one spending category, so composition carries "
        "no information. The informative signals are capital share and workforce split."),
    "concentration_total": (
        "11",
        "A single service area exceeds 90% of spending, so composition carries no "
        "information for this entity."),

    # ---- section 7: offices ----------------------------------------------
    "office_match_low_confidence": (
        "7",
        "This office-to-entity match is below the high-confidence threshold. Present it as "
        "probable and flag it for verification. Never present it as established."),
    "offices_have_no_holders": (
        "7",
        "The office data records roles, seats, terms and selection method, but contains no "
        "holder names. Say the data does not include current holders rather than implying "
        "the seats are vacant or unknown."),
    "office_coverage_uneven": (
        "7",
        "Office coverage is uneven and better for larger general-purpose governments. "
        "Absence of records does not mean the entity has no governing body."),

    # ---- section 9: the ecosystem ----------------------------------------
    "ecosystem_basis_host_county": (
        "9",
        "This set was assembled on shared host county, which is the most reliable basis the "
        "data supports. State that basis and the count."),
    "ecosystem_no_summing": (
        "9",
        "Do not sum budgets across these entities without saying what the sum includes. "
        "Intergovernmental transfers mean the same dollar can appear twice."),
    "host_county_undercaptures": (
        "13.11",
        "Districts crossing county lines are assigned to the county holding most of their "
        "assessed value, so host-county grouping under-captures service areas for exactly "
        "the large entities users ask about most."),
    "absence_means_another_entity": (
        "9",
        "An entity showing no spending in a category is usually evidence that another entity "
        "holds that responsibility, not evidence of neglect."),
    "serving_stack_incomplete": (
        "9",
        "This is the governments established to serve the place, not all of them. Special "
        "districts are two thirds of Oregon's governments and almost none of them have a "
        "boundary in this data, so a short list is a limit of the boundary files and not a "
        "small stack. Say established, never say all, and never let a reader read the list "
        "as their whole tax bill."),
    "serving_basis_differs": (
        "9",
        "The rows here do not rest on one kind of evidence. A precinct assignment records "
        "which governments a polygon sits in; a boundary overlap infers it from the "
        "government's own outline; the county comes from the register. Name the basis "
        "beside the row rather than presenting the list as uniformly established."),

    # ---- section 10: conditions -------------------------------------------
    "dp03_geography_differs": (
        "10",
        "This condition figure describes a geography, not the entity. Name the geography in "
        "the sentence where the figure appears."),
    "dp03_cannot_measure_homelessness": (
        "10",
        "DP03 is a household survey and people who are unsheltered are largely outside its "
        "frame. It cannot measure homelessness."),

    # ---- section 4: hard rules --------------------------------------------
    "no_causation": (
        "4",
        "Do not assert that spending explains conditions or that conditions explain "
        "spending. These are aggregate patterns observed in the same geography."),
    "inputs_not_outcomes": (
        "4",
        "This data measures inputs. It contains no performance measures, service levels or "
        "results. Never substitute a spending figure for an outcome."),

    # ---- section 13: Oregon ------------------------------------------------
    "oregon_city_health_expected": (
        "13.3",
        "An Oregon city showing little or no health spending is the expected pattern. The "
        "county is the local public health authority. State that specifically rather than "
        "describing the absence as a possibility."),
    "oregon_county_delegated": (
        "13.3",
        "County spending in health, corrections, veterans' services and judicial functions "
        "reflects state-delegated mandates, not local discretion. Counties and cities are "
        "not comparable on these functions."),
    "oregon_property_tax_constrained": (
        "13.7",
        "Flat or slowly growing property tax revenue in an Oregon local government is the "
        "expected result of Measure 5 and Measure 50, not fiscal weakness or a local choice."),
    "oregon_school_state_funded": (
        "13.4",
        "Oregon school districts are funded more by state revenue than local taxes. Do not "
        "read local revenue capacity as total capacity."),
    "oregon_esd_no_peers": (
        "13.4",
        "Education Service Districts are a regional layer with no equivalent in most states, "
        "so they do not match national school district peer groups."),
    "oregon_cog_not_a_government": (
        "13.6",
        "Councils of governments have no taxing or regulatory authority and are groups of "
        "governments rather than governments. Never compare their finances to a city, county "
        "or district."),

    # ---- section 12: topics -------------------------------------------------
    "topic_gap": (
        "12",
        "The data records Census functional categories, which do not correspond to issues. "
        "Name the gap, offer the proxy and say how loose the fit is, then give the real "
        "figures for the real categories, clearly labeled."),
    "topic_no_dollar_figure": (
        "12",
        "Never state or imply a dollar amount for this topic. The figure does not exist in "
        "the source and cannot be derived from it."),
}

NOT_COMPUTABLE = {
    "service_area_yoy": (
        "8",
        "An individual service area has no year-over-year change: the source gives each "
        "area a share and a total for one year only, with no prior-year figure of its "
        "own, so never state or estimate a growth rate for Public Safety or any other "
        "single area. The total those areas sum to is a different matter and does have "
        "a history — see service_area_totals, which runs to seven years for 312 "
        "entities. Say which of the two you are describing; the areas are a snapshot "
        "inside a total that moves."),
    "per_capita_without_population": (
        "6",
        "Per-capita is not computable for an entity with no population or enrollment field."),
    "cross_denominator_comparison": (
        "6",
        "A per-capita figure and a per-enrollment figure are not comparable."),
    "property_tax_summed": (
        "13.11",
        "Property tax data must not be summed across districts, because of bond pockets and "
        "overlapping boundaries."),
    "issue_level_spending": (
        "12",
        "Issue-level spending totals do not exist in this data and cannot be derived."),
    "outcome_or_performance": (
        "4",
        "This data contains no performance measures, service levels or outcomes."),
}

# Section 12 concordance. Fit quality is part of the answer, not a footnote.
TOPIC_CONCORDANCE = {
    "homelessness": (
        ["Health & Human Services", "Housing & Community Development"], "weak",
        "Both categories contain much else. Response often sits in a separate agency, joint "
        "office, or nonprofit contract that does not appear in this data."),
    "wildfire": (
        ["Public Safety", "Environment & Natural Resources"], "weak",
        "Heavily state and federal. Local budgets do not separate wildland from structural "
        "fire."),
    "mental health": (
        ["Health & Human Services"], "weak",
        "Often a county or a separate behavioral health authority."),
    "public safety": (
        ["Public Safety", "Judicial"], "good",
        "Workforce headcount and wages add real detail."),
    "education": (
        ["Education"], "good",
        "Good for districts. For general-purpose governments this may reflect a dependent "
        "system or none at all."),
    "transit": (
        ["Transportation"], "moderate",
        "Combines roads, transit, airports and ports."),
    "transportation": (
        ["Transportation"], "moderate",
        "Combines roads, transit, airports and ports."),
    "housing": (
        ["Housing & Community Development"], "moderate",
        "Does not isolate affordability work."),
    "water": (
        ["Utilities"], "good",
        "Good where the entity operates its own systems."),
    "sewer": (["Utilities"], "good", "Good where the entity operates its own systems."),
    "parks": (["Culture & Recreation"], "good", ""),
    "recreation": (["Culture & Recreation"], "good", ""),
    "climate": (
        ["Environment & Natural Resources"], "weak",
        "Small and inconsistently used as a category."),
    "environment": (["Environment & Natural Resources"], "weak", "Inconsistently used."),
    "infrastructure": (
        ["Capital expenditure"], "good",
        "Good for whether an entity is building, not for what it is building."),
    "technology": (
        ["General Government", "Financial Administration"], "weak",
        "Not separately reported anywhere in this data."),
    "opioid": (
        ["Health & Human Services"], "weak",
        "No separate category exists. Response is usually a county or state program."),
}

# §11 attention thresholds. Triggers for attention, not verdicts.
# The payload calls one field "population" for every government type, and it is
# not the same quantity in each. Portland School District 1J reports 48,601
# against a city of 641,162, with 7,608 staff: 157 employees per thousand, which
# is an ordinary ratio of staff to *students* and an absurd one to residents. So
# the field is enrollment for a school district and residents for a general
# purpose government, and a per-capita figure means something different in each.
#
# Naming it matters twice. A figure labelled "per resident" on a school district
# is simply wrong. And a comparison that puts a district beside a city divides by
# two different denominators under one heading, which is the silent basis change
# §10 forbids.
DENOMINATOR = {
    "County": ("resident", "residents"),
    "Municipal": ("resident", "residents"),
    "State": ("resident", "residents"),
    "School District": ("student", "students"),
    # Special districts serve an extent, not a countable membership, and the
    # payload gives them no denominator at all.
    "Special District": (None, None),
}


def denominator(gov_type, plural=True):
    """The unit a per-capita figure on this government type is per."""
    singular, many = DENOMINATOR.get(gov_type, ("resident", "residents"))
    return (many if plural else singular)


def per_unit_label(gov_type):
    """'per resident', 'per student', or None where there is no denominator."""
    singular = DENOMINATOR.get(gov_type, ("resident", "residents"))[0]
    return f"per {singular}" if singular else None


THRESHOLDS = {
    "peer_ratio_mention": 1.5,
    "peer_ratio_lead": 2.0,
    "peer_ratio_low": 0.5,
    "yoy_flat": 3.0,
    "yoy_name": 8.0,
    "yoy_lead": 20.0,
    "capital_active": 20.0,
    "capital_lead": 30.0,
    "capital_none": 3.0,
    "personnel_low": 35.0,
    "personnel_high": 65.0,
    "volatility_cov": 1.0,
    "concentration": 90.0,
    "other_partial": 20.0,
    "stability_move": 5.0,
    "workforce_divergence": 5.0,
    # §15.1 says a map is drawn only where coverage is high enough that the
    # picture is not mostly absence, and "mostly" is the line: below half the
    # boundaries in the drawn extent carrying a value, the reader is looking at
    # a field of no-data with a few marks in it and reading the marks as the
    # pattern. Kept here rather than in the map code so the number that decides
    # a refusal is data with a test beside it.
    "map_density_minimum": 0.5,
    # §15.5 forbids presenting a map as complete when a government type is
    # missing from it. Below this share of the money for the mapped measure
    # sitting on the drawn layer, saying so becomes the main point of the
    # caveat rather than a footnote to it.
    "map_layer_share_partial": 0.5,
    # §15.5 forbids a chart of a single number, and a choropleth of two or three
    # polygons is that chart with a coastline on it. Scoping a place map to a
    # rural county is how you get there without noticing.
    "map_min_polygons": 4,
}


def caveat(code):
    """Return a caveat as a dict ready to place in a tool result."""
    section, text = CAVEATS[code]
    return {"code": code, "rule": f"§{section}", "guidance": text}


def not_computable(code):
    section, text = NOT_COMPUTABLE[code]
    return {"code": code, "rule": f"§{section}", "reason": text}
