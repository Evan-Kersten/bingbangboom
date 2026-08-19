"""The rule catalogue.

Every caveat the tool layer can attach to a result, keyed by code. Each entry
names the section of the system prompt it comes from and carries the guidance
text that travels into the model's context alongside the figure.

This exists so that a rule is stated once. The prompt says what to do; this
module is where the tools reach for the words, so the two cannot drift apart
and so a rule cannot be silently dropped by a model that skimmed section 8.
"""

import re

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
    "pin_is_an_office": (
        "9",
        "Every pin on this map is the centroid of the city on the government's mailing "
        "address, which is where its office is and not the ground it serves. A rural fire "
        "protection district filed at a Eugene address covers land outside Eugene "
        "entirely, and a district serving three counties has one pin in whichever city "
        "takes its post. Never describe a pin as coverage, a boundary, a catchment or a "
        "service area, and never read the density of pins in a county as the density of "
        "the service. What this map supports is where the governments doing a thing are "
        "based and how large they are; nothing about extent can be read off it."),
    "points_place_what_polygons_cannot": (
        "9",
        "This layer exists because 1,029 of Oregon's special districts have no boundary "
        "anywhere in this data and never appear on a choropleth at all. A point map "
        "includes them at the cost of saying only where each one is based. That trade is "
        "the reason to use it and the limit on what it can answer."),
    "topic_reads_as_several": (
        "14",
        "The word asked about names more than one subject in this concordance, and they map "
        "to different categories with different fit. Say which reading the figures are for "
        "and name the others, rather than answering the one and leaving the reader to "
        "assume it was the only one."),
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
    "capital_split_trend": (
        "8",
        "Whether capital spending is rising or falling here is not computable. The "
        "source files one operating-and-capital split per entity, for a single year, "
        "with no prior-year split of its own; the total those two sum toward does have "
        "a history and is a different measure. Capital is the lumpiest series in this "
        "data — one plant finished or started moves the share by tens of points — so a "
        "single year is a position in a construction cycle and never a direction."),
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
    # Roads, water, sewer and the ground they sit on — the three service areas
    # that hold the built environment. It pointed at "Capital expenditure" for a
    # long time, which is not a service area at all but the capital side of the
    # operating split, so nothing joined: every government in every stack
    # reported nothing on infrastructure and the map refused for want of a
    # category that was never in the table. The capital question is real and is
    # answered by `capital_split`, which is a different tool and a different
    # question: whether a government is building, rather than what it is
    # building. `verify.py` now asserts every category here is a service area
    # that exists, because this failed silently and looked like a finding.
    "infrastructure": (
        ["Transportation", "Utilities", "Environment & Natural Resources"], "moderate",
        "Roads, water and sewer are each most of one category, so the categories "
        "together are broader than the subject. Whether a government is building "
        "rather than maintaining is the capital share, which is a separate figure."),
    "technology": (
        ["General Government", "Financial Administration"], "weak",
        "Not separately reported anywhere in this data."),
    "opioid": (
        ["Health & Human Services"], "weak",
        "No separate category exists. Response is usually a county or state program."),
}

# The words somebody actually types, against the seventeen the concordance is
# keyed by. Nobody sits down to analyse "homelessness" — they sit down having
# been handed a scope of work that says unsheltered, or encampment, or shelter
# beds, and the concordance key is a word they would only reach by guessing it.
#
# This is not a search convenience. §12 says the answer to an issue question
# opens on the gap between the subject and the Census categories, and a subject
# that fails to resolve never reaches that sentence: the reader gets an empty
# result, concludes the data has nothing, and goes to a source that will hand
# them a number without the gap attached. A miss here is the one failure mode
# that routes around every refusal in the system.
#
# An alias may name more than one topic, and both are offered rather than the
# first being picked silently. "fire" is wildland and structural and they land
# on different categories with different fit; choosing for the reader is the
# §14 ambiguity failure with the ambiguity hidden.
TOPIC_ALIASES = {
    "unhoused": ("homelessness",), "houseless": ("homelessness",),
    "unsheltered": ("homelessness",), "homeless": ("homelessness",),
    "shelter": ("homelessness",), "encampment": ("homelessness",),
    "encampments": ("homelessness",), "supportive housing": ("homelessness", "housing"),

    "affordable housing": ("housing",), "affordability": ("housing",),
    "rent": ("housing",), "renters": ("housing",), "zoning": ("housing",),
    "land use": ("housing",), "development": ("housing",),
    "community development": ("housing",),

    "police": ("public safety",), "policing": ("public safety",),
    "law enforcement": ("public safety",), "sheriff": ("public safety",),
    "crime": ("public safety",), "corrections": ("public safety",),
    "jail": ("public safety",), "courts": ("public safety",),
    "emergency": ("public safety",), "911": ("public safety",),
    "dispatch": ("public safety",), "ems": ("public safety",),
    "ambulance": ("public safety",),
    # Both, in this order. Structural fire is a Public Safety function with real
    # coverage; wildland is mostly state and federal and the concordance rates it
    # weak. A reader typing "fire" is usually after the first and needs to be
    # told which one they got.
    "fire": ("public safety", "wildfire"),
    "fire protection": ("public safety",), "firefighters": ("public safety",),
    "wildland": ("wildfire",), "wildfires": ("wildfire",),
    "defensible space": ("wildfire",), "smoke": ("wildfire",),

    "behavioral health": ("mental health",), "behavioural health": ("mental health",),
    "crisis": ("mental health",), "suicide": ("mental health",),
    "substance use": ("opioid", "mental health"), "addiction": ("opioid",),
    "overdose": ("opioid",), "fentanyl": ("opioid",), "naloxone": ("opioid",),
    "treatment": ("opioid", "mental health"), "recovery": ("opioid",),
    "public health": ("mental health",), "health": ("mental health",),
    "human services": ("mental health",), "social services": ("mental health",),

    "roads": ("transportation",), "streets": ("transportation",),
    "highways": ("transportation",), "traffic": ("transportation",),
    "bridges": ("transportation",), "pavement": ("transportation",),
    "sidewalks": ("transportation", "infrastructure"),
    "bike": ("transportation",), "pedestrian": ("transportation",),
    "vision zero": ("transportation",), "safe routes": ("transportation",),
    "bus": ("transit",), "rail": ("transit",), "light rail": ("transit",),
    "transit oriented": ("transit", "housing"), "mobility": ("transit",),

    "schools": ("education",), "students": ("education",), "school": ("education",),
    "childcare": ("education",), "child care": ("education",),
    "early learning": ("education",), "literacy": ("education",),
    "enrollment": ("education",),

    "drinking water": ("water",), "water quality": ("water",),
    "wells": ("water",), "watershed": ("water", "environment"),
    "stormwater": ("sewer",), "wastewater": ("sewer",),
    "sanitation": ("sewer",), "sewage": ("sewer",), "septic": ("sewer",),
    "utilities": ("water", "sewer"), "garbage": ("sewer",), "waste": ("sewer",),
    "landfill": ("sewer",), "recycling": ("sewer",),

    "broadband": ("technology",), "internet": ("technology",),
    "digital equity": ("technology",), "digital": ("technology",),
    "connectivity": ("technology",), "it": ("technology",),
    "cybersecurity": ("technology",), "data": ("technology",),

    "emissions": ("climate",), "greenhouse gas": ("climate",),
    "decarbonization": ("climate",), "decarbonisation": ("climate",),
    "resilience": ("climate",), "heat": ("climate",), "extreme heat": ("climate",),
    "flooding": ("climate", "infrastructure"), "drought": ("climate", "water"),
    "sustainability": ("climate", "environment"), "energy": ("climate",),
    "conservation": ("environment",), "habitat": ("environment",),
    "air quality": ("environment",), "pollution": ("environment",),
    "natural resources": ("environment",), "forestry": ("environment", "wildfire"),

    "trails": ("parks",), "open space": ("parks",), "green space": ("parks",),
    "playgrounds": ("parks",), "library": ("recreation",),
    "libraries": ("recreation",), "culture": ("recreation",),
    "arts": ("recreation",), "community center": ("recreation",),

    "capital": ("infrastructure",), "construction": ("infrastructure",),
    "facilities": ("infrastructure",), "buildings": ("infrastructure",),
    "deferred maintenance": ("infrastructure",), "public works": ("infrastructure",),
}


# The seventeen subjects arranged for scanning. Alphabetical is the order a
# list has when nobody chose one, and it puts climate beside education and
# education beside environment; a reader looking for the drainage question has
# to read all seventeen. Grouping is presentation and nothing reads it as data,
# but it lives here rather than in the page for the reason the concordance does:
# a subject added there and not here would silently stop being offerable.
TOPIC_GROUPS = (
    ("People and health",
     ["homelessness", "housing", "mental health", "opioid", "education"]),
    ("Safety",
     ["public safety", "wildfire"]),
    ("Built environment",
     ["transportation", "transit", "water", "sewer", "infrastructure",
      "technology"]),
    ("Land and climate",
     ["climate", "environment", "parks", "recreation"]),
)


def resolve_topic(term):
    """The canonical concordance topics a typed word names, best fit first.

    Three passes, and the order is the whole behaviour: an exact hit on a
    concordance key or an alias wins outright, and only a term that matches
    nothing exactly falls through to substring. Without that ordering "water"
    drags in "wastewater" and a reader asking about drinking water is offered
    the sewer concordance first.
    """
    needle = " ".join((term or "").lower().split())
    if not needle:
        return []
    if needle in TOPIC_CONCORDANCE:
        return [needle]
    if needle in TOPIC_ALIASES:
        return list(TOPIC_ALIASES[needle])

    # Two characters is enough to be typing and not enough to mean anything, and
    # a subject offered on one letter fires on every keystroke of a city name.
    if len(needle) < 3:
        return []

    # Sorted, not insertion order, in both loops below. app/subjects.js resolves
    # the same term in the browser and can only iterate the list it was sent,
    # which is sorted; matching that here is what lets the parity test compare
    # the two orderings rather than only the two sets.
    found = []
    for topic in sorted(TOPIC_CONCORDANCE):
        if needle in topic or topic in needle:
            found.append(topic)

    # Aliases match on whole words, and the concordance keys above do not. The
    # keys are long enough that a bare substring is safe; the aliases include
    # "it", "ems" and "911", and a bare substring made "housing situation"
    # resolve to technology because "it" sits inside "situation".
    # Prefix rather than substring, so a half-typed word still offers the
    # subject while "car" does not pull in childcare from the middle of it.
    # Both directions, because the box is matched on every keystroke: the typed
    # word is the shorter one while somebody is still typing it, and the longer
    # one once they have finished and pluralised it.
    words = re.findall(r"[a-z0-9]+", needle)
    for alias, topics in sorted(TOPIC_ALIASES.items()):
        hit = alias.startswith(needle) or all(
            any(w.startswith(part) or (len(w) >= 3 and part.startswith(w))
                for w in words)
            for part in alias.split())
        if not hit:
            continue
        for topic in topics:
            if topic not in found:
                found.append(topic)
    return found

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


# Which rules a reader should see, and in what words.
#
# Almost everything above is an instruction to whoever writes the answer: name
# the driver before the label, decline the distance calculation, never divide
# one row by one row. Those are manufacturing instructions. Showing them to an
# analyst was showing them the inside of the factory — the interface said
# "11 rules bind this answer" and every one of the eleven was either an order
# aimed at a writer or a restatement of a sentence already in the answer.
#
# What is left, and what belongs in front of a reader, is the subset that is a
# *fact about the data* and that changes what they would conclude or do next.
# An Oregon county's health budget being a state mandate rather than a local
# priority is a finding. "Name the gap, offer the proxy" is not.
#
# The phrasing here is deliberately not the phrasing above. The guidance text
# is imperative because a model is being told what to do; these are declarative
# because a person is being told what is true. A code absent from this dict
# still reaches the model in full — it is simply never rendered.
READER_NOTES = {
    # What the figures are and are not
    "inputs_not_outcomes":
        "This data records what governments spend and who they employ. It "
        "contains no service levels, caseloads or results, so nothing here says "
        "whether any of it worked.",
    "two_expenditure_measures":
        "Two different totals in this source are both called expenditure and "
        "they disagree for most governments. Figures here are operating plus "
        "capital, which is the basis every share is computed against, so a "
        "number checked against a budget document may not match it.",
    "other_share_high":
        "More than a fifth of this budget is filed under Other, which is a "
        "Census classification artifact rather than a programme, so this "
        "breakdown is partial.",
    "capital_lumpy":
        "Capital spending follows a bond and construction cycle. A high year is "
        "a position in that cycle rather than a standing preference.",
    "cost_per_head_is_not_a_salary":
        "This is everything spent on the function divided by the people "
        "employed in it, so it includes trucks, buildings and debt-funded "
        "construction. It is not a wage.",

    # Coverage and vintage, which decide whether a layer is worth building
    "census_year_coverage":
        "The Census of Governments takes a full census in years ending 2 and 7 "
        "and samples in between. 2022 reaches the most governments; 2023 "
        "reaches fewer and skews to the largest, so the latest year is a "
        "picture of big governments rather than of the state.",
    "peer_pool_mixed_vintage":
        "Each government is shown at whichever year it last reported, so a peer "
        "median blends two or three vintages rather than describing one moment.",
    "workforce_partial":
        "The source lists top employment functions only. A function that does "
        "not appear here is not necessarily one nobody staffs.",

    # Who is in the list, and who cannot be
    "serving_stack_incomplete":
        "Special districts are two thirds of Oregon's governments and almost "
        "none of them have a boundary in this data. This is who is established "
        "to serve here, not everyone who does.",
    "host_county_undercaptures":
        "A district crossing county lines is filed under the county holding "
        "most of its assessed value, so a county list over-collects districts "
        "serving elsewhere and misses districts serving here.",
    "ecosystem_no_summing":
        "These budgets do not add up to a figure for the place. The same dollar "
        "appears twice wherever one government transfers to another.",
    "peer_median_degenerate":
        "Most of the peer pool reports nothing on this measure, so the median "
        "is not a midpoint and the distance from it is not meaningful.",

    # Conditions
    "dp03_cannot_measure_homelessness":
        "These estimates come from a household survey, and people who are "
        "unsheltered are largely outside its frame. Nothing here counts them.",
    "dp03_geography_differs":
        "These conditions are measured over a geography rather than over the "
        "governments above, and the two do not share a boundary.",

    # Oregon, where the expected pattern is the finding
    "oregon_county_delegated":
        "Oregon counties run health, corrections, veterans' services and courts "
        "on the state's behalf, so those budgets are mandates rather than county "
        "priorities, and they are not comparable to a city's.",
    "oregon_city_health_expected":
        "An Oregon city spending little or nothing on health is the expected "
        "pattern rather than a gap. The county is the local public health "
        "authority.",
    "oregon_property_tax_constrained":
        "Flat or slow property tax growth in an Oregon local government is what "
        "Measure 5 and Measure 50 produce, not a local choice or a sign of "
        "weakness.",
    "oregon_school_state_funded":
        "Oregon school districts are funded more by state revenue than by local "
        "taxes, so local revenue is not a measure of total capacity.",
    "oregon_cog_not_a_government":
        "A council of governments has no taxing or regulatory authority. It is a "
        "group of governments and its finances are not comparable to a city's.",

    # Prohibitions worth stating, because the absence is the scoping finding
    "issue_level_spending":
        "No spending figure for this subject exists in the source and none can "
        "be derived from it. The figures here are for the Census categories "
        "that stand nearest to it.",
    "service_area_yoy":
        "A single service area has one year and no history of its own, so "
        "nothing here supports a growth rate for it. The total it sits inside "
        "does have a history.",
    "cross_denominator_comparison":
        "A per-resident figure and a per-student figure are not the same "
        "measure and cannot be ranked against each other.",
    "property_tax_summed":
        "Property tax must not be added across these districts. Overlapping "
        "boundaries and bond pockets mean the sum counts the same base twice.",

    # Codes raised inline by a tool rather than drawn from the catalogue above.
    # Registering one here is the only thing that makes it reader-facing, so an
    # unregistered code defaults to model-only, which is the safe direction: a
    # tool that invents a code cannot accidentally put text in front of anybody.
    "conditions_are_not_a_record":
        "These describe the population these governments operate in. They are "
        "not those governments' results and were not produced by the spending "
        "beside them.",
    "survey_not_census":
        "These are American Community Survey five-year estimates. They describe "
        "a five-year window rather than a single year, and they are a sample "
        "rather than a count.",
    "margin_too_wide":
        "Some of these estimates have a margin of error wider than a fifth of "
        "the estimate itself. A small place's figure can move by half its own "
        "value and still be the same sample, so read those with the margin or "
        "not at all.",
    "vintage_dollars":
        "Each release is in its own dollars, so a difference between two of "
        "them is part inflation and part change, and nothing in this data can "
        "separate the two. Compare a share or a rate instead.",
    "no_price_index":
        "There is no price index in this data, so these figures are nominal. A "
        "government that grew is not necessarily buying more than it did.",
    "population_held_constant":
        "Population is one estimate per government rather than a series, so the "
        "denominator is fixed and what moves in a per-resident line is spending.",
    "indexed_hides_level":
        "Every line starts at 100, so this shows growth and deliberately hides "
        "level. A government that doubled from a low base outruns one that grew "
        "slightly from a high one.",
    "one_year_per_entity":
        "Service-area spending is one year per government, so this is a "
        "snapshot rather than a trend.",
    "map_years_differ":
        "This map is one year per government rather than one year across the "
        "map, so neighbouring boundaries can be two years apart. Read it as a "
        "level and not as a moment.",
    "geometry_by_name":
        "School district boundaries are matched by name rather than by a shared "
        "identifier, and reach 156 of 223 districts.",
    "map_is_partial":
        "This layer covers one government type. Special districts are the "
        "largest group in Oregon and almost none of them have a boundary here.",
    "debt_is_not_a_deficit":
        "Outstanding debt is a balance rather than an annual shortfall, and most "
        "of it is borrowing against capital a government intends to hold for "
        "decades.",
    "pin_is_an_office":
        "Each pin is the city on that government's mailing address, which is where its "
        "office is and not the ground it serves. A rural fire district filed at a Eugene "
        "address covers land outside Eugene entirely, so read these as where the "
        "governments are based, never as coverage.",
    "points_place_what_polygons_cannot":
        "This is the only layer that can show special districts at all. They are two "
        "thirds of Oregon's governments and none of them has a boundary in this data, so "
        "the choice here is a point or nothing.",
    "catalogue_is_a_snapshot":
        "Coverage is a property of this build of the data. A refreshed source "
        "can change a verdict here, so record the date beside any layer "
        "decision this informs.",
}


def reader_note(code):
    """The reader-facing sentence for a rule, or None if it is model-only."""
    return READER_NOTES.get(code)


def caveat(code):
    """Return a caveat as a dict ready to place in a tool result."""
    section, text = CAVEATS[code]
    return {"code": code, "rule": f"§{section}", "guidance": text}


def not_computable(code):
    section, text = NOT_COMPUTABLE[code]
    return {"code": code, "rule": f"§{section}", "reason": text}
