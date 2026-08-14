"""Tool schemas as the model sees them.

The description text is part of the guardrail. A model chooses a tool from its
description, so the descriptions say what each tool refuses as well as what it
returns. `compare_to_peers` announcing that it declines the ratio on a degenerate
median is what stops the model reaching for run_sql to compute the ratio itself.

Kept separate from tools.py so the wire format can change without touching the
implementations, and so the schema list can be diffed on its own.
"""

ENVELOPE_NOTE = (
    "Returns an envelope with data, caveats, not_computable and vintage. Every "
    "caveat must be reflected in your answer. Anything listed in not_computable "
    "must not be attempted, estimated or implied."
)

PID = {"type": "string", "description": "Census PID6, from find_entity."}

SCHEMAS = [
    {
        "name": "find_entity",
        "description": "Resolve a government name to entities. Always call this first when "
                       "the question names a government. Several governments can share a "
                       "name and be separate entities. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name or partial name."},
                "limit": {"type": "integer", "description": "Max matches, default 8."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_entity_profile",
        "description": "What kind of government this is, its population where it has one, "
                       "and which data blocks exist for it. Call this before asking for a "
                       "block that may be absent. " + ENVELOPE_NOTE,
        "input_schema": {"type": "object", "properties": {"pid6": PID}, "required": ["pid6"]},
    },
    {
        "name": "get_financial_position",
        "description": "Fiscal stability with its components, operating versus capital, and "
                       "revenue volatility. Returns which component drives the composite. "
                       "Use this rather than reporting a rating label alone. " + ENVELOPE_NOTE,
        "input_schema": {"type": "object", "properties": {"pid6": PID}, "required": ["pid6"]},
    },
    {
        "name": "get_spending_breakdown",
        "description": "Spending by service area. Flags when the unclassified 'Other' bucket "
                       "exceeds the largest named category, which makes the breakdown "
                       "partial. Never provides year-over-year change for a service area, "
                       "which is not computable from this data. " + ENVELOPE_NOTE,
        "input_schema": {"type": "object", "properties": {"pid6": PID}, "required": ["pid6"]},
    },
    {
        "name": "get_workforce",
        "description": "Headcount, payroll, distribution by service area and top employment "
                       "functions. Distinguishes a reported zero workforce from absent data. "
                       "Covers listed functions only. " + ENVELOPE_NOTE,
        "input_schema": {"type": "object", "properties": {"pid6": PID}, "required": ["pid6"]},
    },
    {
        "name": "compare_to_peers",
        "description": "Compare one entity to its peer group, returning the ratio, the peer "
                       "group definition and the count. Declines to compute the ratio when "
                       "the peer median is at or near zero or the peer pool does not match "
                       "the entity type; in that case ratio is null and ratio_refused is "
                       "true, and you must report the refusal rather than computing a "
                       "distance yourself. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": PID,
                "metric": {"type": "string", "enum": ["capital_share", "fiscal_stability"]},
            },
            "required": ["pid6"],
        },
    },
    {
        "name": "compare_entities",
        "description": "Compare several entities on one metric. Flags mixed government "
                       "types, which are usually not comparable, and names entities missing "
                       "from the data. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "metric": {"type": "string", "enum": ["fiscal_stability", "capital_share"]},
            },
            "required": ["pid6_list"],
        },
    },
    {
        "name": "find_salient",
        "description": "What stands out about an entity, ranked by divergence rather than "
                       "size. Returns nothing_unusual true when the entity clears no "
                       "attention threshold, which is a legitimate answer and must not be "
                       "padded into a finding. " + ENVELOPE_NOTE,
        "input_schema": {"type": "object", "properties": {"pid6": PID}, "required": ["pid6"]},
    },
    {
        "name": "list_ecosystem",
        "description": "Which governments serve a place. Use this whenever the question "
                       "names a place rather than a single government, or concerns a "
                       "function commonly split across entities such as housing, health, "
                       "transit, water or emergency services. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "county_pid6": {"type": "string"},
                "county_name": {"type": "string"},
            },
        },
    },
    {
        "name": "get_offices",
        "description": "Elected and appointed positions: role, seat, term and selection "
                       "method, with the match confidence. The data contains no holder "
                       "names. " + ENVELOPE_NOTE,
        "input_schema": {"type": "object", "properties": {"pid6": PID}, "required": ["pid6"]},
    },
    {
        "name": "map_topic_to_categories",
        "description": "Map an issue or topic to the Census functional categories that come "
                       "closest, with an explicit fit quality. Call this before answering "
                       "any question about an issue such as homelessness, wildfire, mental "
                       "health or climate. Issue-level spending totals do not exist in this "
                       "data. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "drill",
        "description": "Open one government's spending, one level at a time. Called with a "
                       "pid6 alone it returns the twelve service areas with each area's peer "
                       "median share beside it. Add service_area and it returns the functions "
                       "inside that area. Add function_name and it returns how that one "
                       "function's total decomposes: operating, capital, personnel, amounts "
                       "excluded, and the vendor-addressable remainder. Three levels is as "
                       "deep as the data goes. Use this rather than get_spending_breakdown "
                       "whenever the question is about a category rather than the whole "
                       "budget. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": PID,
                "service_area": {"type": "string",
                                 "description": "Level 2. Omit for the service-area list."},
                "function_name": {"type": "string",
                                  "description": "Level 3. Requires service_area first."},
            },
            "required": ["pid6"],
        },
    },
    {
        "name": "compare_to_peer_group",
        "description": "Place one government in the distribution of its own type: low, "
                       "first quartile, median, third quartile and high, with the count of "
                       "peers and which quarter this entity falls in. Peer groups are within "
                       "a government type, because an Oregon county carries state-delegated "
                       "functions a city does not. Where most of the pool reports nothing on "
                       "a measure the median is not a midpoint, and the ratio is refused "
                       "rather than computed. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": PID,
                "metric": {"type": "string",
                           "enum": ["total_expenditure", "total_revenue",
                                    "expenditure_per_capita", "capital_share",
                                    "employees_per_1000", "average_wage",
                                    "personnel_share", "service_area_share",
                                    "service_area_total"]},
                "service_area": {"type": "string",
                                 "description": "Required for the two service_area metrics."},
            },
            "required": ["pid6", "metric"],
        },
    },
    {
        "name": "per_capita_by_service_area",
        "description": "Spending per resident on one service area, across several "
                       "governments, with the median for each government type. This is the "
                       "comparison absolute dollars cannot make: a large city outspends a "
                       "small county on public safety by a margin that is purely population, "
                       "and per resident the ranking is a different one. Entities with no "
                       "denominator in the data are excluded and named rather than shown on "
                       "another basis. The denominator is residents for a city, county or "
                       "the state and students for a school district, since the population "
                       "field holds enrollment there: a set spanning both is refused rather "
                       "than ranked, and the unit is returned so the answer can name it. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "service_area": {"type": "string"},
            },
            "required": ["pid6_list", "service_area"],
        },
    },
    {
        "name": "per_capita_over_time",
        "description": "Spending per resident, or per student for school districts, across "
                       "years for several governments, with a "
                       "dashed peer-median baseline over entities reporting in every year. "
                       "Set indexed to start every line at 100 and compare growth rather "
                       "than level. Two limits travel with the result and must be stated: "
                       "population is a single estimate per entity and is held constant, so "
                       "what moves is spending; and no price index exists in this data, so "
                       "the baseline is peers and not inflation, and no figure is in real "
                       "terms. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "indexed": {"type": "boolean",
                            "description": "Rebase every line to 100 at the first year."},
            },
            "required": ["pid6_list"],
        },
    },
    {
        "name": "revenue_mix",
        "description": "Where a government's money comes from, split into property tax, "
                       "charges and fees, state aid, federal aid and the rest, each with "
                       "the median share for the same government type. This is the "
                       "question a resident actually has about a budget, because one of "
                       "the answers is their own tax bill. Two structural facts come "
                       "back with it: how much is raised inside the boundary against how "
                       "much is transferred in, since a government funded by transfers "
                       "is exposed to a decision made elsewhere; and what each category "
                       "means. A missing category means this government does not levy or "
                       "receive it, not that it collected nothing — Oregon has no "
                       "statewide sales tax, so a sales or income tax line is a local "
                       "levy that most governments do not have. Revenue shares and "
                       "spending areas are different questions; never run them together "
                       "in one sentence. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {"pid6": {"type": "string"}},
            "required": ["pid6"],
        },
    },
    {
        "name": "debt_load",
        "description": "What a government owes, set against what it takes in that same "
                       "year, with the median ratio across its type. Debt alone ranks by "
                       "government size; against annual revenue it becomes a figure a "
                       "reader can hold. Outstanding debt is a balance and not an annual "
                       "shortfall — most of it is borrowing against capital assets meant "
                       "to last decades — so never describe it as overspending or as a "
                       "deficit. A government with nothing here may carry no debt or may "
                       "not report it, and the two are not the same. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {"pid6": {"type": "string"}},
            "required": ["pid6"],
        },
    },
    {
        "name": "staffing",
        "description": "Who a government employs, split into named jobs, with each job "
                       "expressed as a ratio against the population it serves: one sworn "
                       "officer per so many residents, one teacher per so many students. "
                       "This is the workforce question a headcount cannot answer, because "
                       "688 firefighters means nothing without the city behind it. Each "
                       "ratio carries the median for the same job across the same "
                       "government type, so a reader can see whether a place is staffed "
                       "thinly or thickly for Oregon. The unit follows the type: students "
                       "for a school district, residents for a city or county, and a "
                       "government with no denominator gets the split and the wages "
                       "without ratios. Never read a ratio as coverage, response time or "
                       "quality; it counts people employed, not service delivered. The "
                       "named jobs do not cover the whole payroll and the shortfall is "
                       "stated. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {"pid6": {"type": "string"}},
            "required": ["pid6"],
        },
    },
    {
        "name": "who_spends_on",
        "description": "Which governments report spending on one named function, ranked "
                       "by what they spend, with a per-unit figure alongside. That figure carries its "
                       "own unit on every row, because the list mixes cities counted in "
                       "residents with school districts counted in students. This "
                       "is the question a name lookup cannot answer: somebody who wants "
                       "fire protection cannot get there from a list of names, because "
                       "the districts that provide it are the ones nobody can name. "
                       "Ranked by size rather than per resident on purpose, since most "
                       "governments doing a given function have no population in the data "
                       "and ranking on a figure they lack would drop them silently. A "
                       "government absent from the list is one where another body does "
                       "the work, not one that spends nothing. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string",
                                  "description": "An exact function name, such as "
                                                 "'Fire Protection' or 'Water Supply'."},
                "limit": {"type": "integer", "description": "Rows to return, default 12."},
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "compare_entities_over_time",
        "description": "Several governments on one measure across 2017 to 2023, at entity "
                       "totals. Service-area spending is a single year per entity, so no "
                       "category can be tracked over time for anyone; if the question asks "
                       "for a service area over time, say that plainly and offer either this "
                       "or per_capita_by_service_area instead of substituting one "
                       "silently. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "measure": {"type": "string", "enum": ["expenditure", "revenue", "debt"]},
            },
            "required": ["pid6_list"],
        },
    },
    {
        "name": "compare_service_area",
        "description": "Several governments within one service area, in absolute dollars, at "
                       "the year each reports. A snapshot and never a trend. Prefer "
                       "per_capita_by_service_area unless the question is specifically about "
                       "budget size. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "service_area": {"type": "string"},
            },
            "required": ["pid6_list", "service_area"],
        },
    },
    {
        "name": "render_chart",
        "description": "Draw one chart for one entity. You choose the form and the entity "
                       "and nothing else: titles, axis labels, colours and numbers are "
                       "derived from the same data the other tools returned, so the chart "
                       "cannot disagree with your prose. Forms: spending_composition (service "
                       "areas), service_area_functions (inside the largest area), peer_range "
                       "(where the entity sits in its peer distribution), finances_over_time, "
                       "workforce_composition. A chart that would misstate the data refuses "
                       "and returns the reason drawn in its place; report the refusal rather "
                       "than describing the comparison another way. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": PID,
                "form": {"type": "string",
                         "enum": ["spending_composition", "service_area_functions",
                                  "peer_range", "finances_over_time",
                                  "workforce_composition"]},
            },
            "required": ["pid6", "form"],
        },
    },
    {
        "name": "render_comparison",
        "description": "Draw several governments on one axis. Forms: "
                       "per_capita_by_service_area (bars, spending per resident, or per student for "
                       "school districts, within one "
                       "service area, with the type median ticked); per_capita_over_time "
                       "(lines, 2017 to 2023, with a dashed peer-median baseline, and set "
                       "indexed for growth rather than level); entities_over_time (lines, "
                       "entity totals); service_area_across_entities (bars, absolute dollars "
                       "within one area). Up to four lines are drawn and the rest are named. "
                       "A service area cannot be drawn as a line for anyone, because "
                       "service-area spending exists for one year per entity. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "form": {"type": "string",
                         "enum": ["per_capita_by_service_area", "per_capita_over_time",
                                  "entities_over_time", "service_area_across_entities"]},
                "measure": {"type": "string", "enum": ["expenditure", "revenue", "debt"],
                            "description": "For entities_over_time."},
                "service_area": {"type": "string",
                                 "description": "Required for both service-area forms."},
                "indexed": {"type": "boolean",
                            "description": "For per_capita_over_time: rebase lines to 100."},
            },
            "required": ["pid6_list", "form"],
        },
    },
    {
        "name": "render_map",
        "description": "Draw a choropleth over one boundary layer. Counties and places join "
                       "to boundaries exactly; school districts join by name and cover 156 of "
                       "223; special districts have no boundaries in this data and cannot be "
                       "mapped at all, so list them instead. Every map covers one government "
                       "type, so it never shows all public spending in a place. Pass county "
                       "to scope a place map, which is unreadable statewide. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "enum": ["county", "place", "school_district"]},
                "metric": {"type": "string",
                           "enum": ["spending_per_resident", "total_spending",
                                    "capital_share", "employees_per_1000", "average_wage",
                                    "service_area_share", "service_area_per_resident"]},
                "county": {"type": "string",
                           "description": "Optional county name to scope the extent to."},
                "service_area": {"type": "string",
                                 "description": "Required for the two service_area metrics."},
            },
        },
    },
    {
        "name": "compare_normalized",
        "description": "Compare several entities on a stated basis. Absolute spending ranks "
                       "by entity size and answers nothing on its own, so prefer "
                       "share_of_spending or per_resident and say which you used. The basis "
                       "is never silently downgraded: an entity for which it cannot be "
                       "computed is excluded and named, rather than mixed into the same "
                       "table on a different basis. On per_resident a set spanning a school "
                       "district and a city is refused outright, because the population "
                       "field is enrollment for one and residents for the other. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "basis": {"type": "string",
                          "enum": ["share_of_spending", "per_resident", "absolute"]},
                "service_area": {"type": "string",
                                 "description": "Optional service area to compare within."},
            },
            "required": ["pid6_list"],
        },
    },
    {
        "name": "compose_report",
        "description": "Plan a multi-section report. Returns sections in the order the "
                       "prompt requires, each with a heading, what you must write there, a "
                       "word budget, the figures, any chart, and the rules bound to that "
                       "section. Write each section against its own budget and rules. "
                       "Sections with nothing to say are already dropped, so do not pad "
                       "them back in. Kinds: entity, place, comparison.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["entity", "place", "comparison"]},
                "pid6": {"type": "string", "description": "For kind=entity."},
                "county_name": {"type": "string", "description": "For kind=place."},
                "pid6_list": {"type": "array", "items": {"type": "string"},
                              "description": "For kind=comparison."},
                "basis": {"type": "string",
                          "enum": ["share_of_spending", "per_resident", "absolute"]},
                "service_area": {"type": "string"},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "run_sql",
        "description": "Read-only SQL escape hatch for questions no other tool covers. "
                       "Prefer a typed tool wherever one applies: rows returned here arrive "
                       "without the guardrails the typed tools attach, so you must check "
                       "entity_flags before interpreting them. Single SELECT only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["sql"],
        },
    },
]

BY_NAME = {schema["name"]: schema for schema in SCHEMAS}


def for_question_type(question_type=None):
    """All schemas. Narrowing the tool list by question type is possible but not
    done: a misrouted question that cannot reach the right tool fails silently,
    where an unused tool costs only its description."""
    return SCHEMAS
