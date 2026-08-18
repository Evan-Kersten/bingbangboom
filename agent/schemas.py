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
        "description": "Every government filed under a county, which is a wider and looser "
                       "set than the ones serving any particular city in it. Use this for a "
                       "question about a county, or about a function commonly split across "
                       "entities such as housing, health, transit, water or emergency "
                       "services. When the question names a city, prefer "
                       "governments_serving. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "county_pid6": {"type": "string"},
                "county_name": {"type": "string"},
            },
        },
    },
    {
        "name": "governments_serving",
        "description": "The governments established to serve one city, each row saying what "
                       "established it: a precinct record, an overlap with the government's "
                       "own boundary, or the register. Use this whenever the question is "
                       "about a place rather than a named government — who serves me, what "
                       "am I paying for, which districts cover this city. Returns the "
                       "established stack and never the whole stack: most special districts "
                       "have no boundary in this data, so the count is a floor. Say "
                       "established, never all. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {"pid6": PID},
            "required": ["pid6"],
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
                       "function's total splits between operating and capital. Three levels "
                       "is as deep as the data goes. Use this rather than get_spending_breakdown "
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
        "name": "render_locator_map",
        "description": "Where in Oregon a government is, for every government. Three "
                       "cases and the difference between them is the point: a "
                       "government with its own boundary is drawn as itself; the 29 "
                       "districts whose service extent was measured from precinct "
                       "assignments are drawn as the counties they operate in; and "
                       "everybody else — most of the corpus, since 1,010 special "
                       "districts have no boundary anywhere in this data — is drawn as "
                       "the county it files under. That last one locates the paperwork "
                       "and not the service: §13.11, published data assigns a district "
                       "to the county holding most of its assessed value, so the host "
                       "county under-captures where a district operates. Never describe "
                       "a highlighted county as the district's extent and never read "
                       "area off one. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {"pid6": {"type": "string"}},
            "required": ["pid6"],
        },
    },
    {
        "name": "render_office_map",
        "description": "Draw the ground that elects a seat on a government's governing "
                       "body. Which source applies is decided by how the seat is "
                       "filled, not by what is available: a seat elected at large has "
                       "the whole jurisdiction as its electorate, so the government's "
                       "own boundary is the district and every voter inside it votes "
                       "on every seat — the position numbers are ballot labels, not "
                       "places. A seat elected by ward, zone or board district answers "
                       "to a piece of ground inside the jurisdiction, and a voter fills "
                       "one seat rather than all of them. Those district boundaries are "
                       "recovered from precinct assignments and exist for Multnomah "
                       "County only; a districted body anywhere else is refused rather "
                       "than drawn as one electorate, because drawing the jurisdiction "
                       "would tell a reader they elect every seat. The recovered edges "
                       "are precinct edges, close enough to show which district covers "
                       "a neighbourhood and not close enough to settle an address. No "
                       "holder names are in this data. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": {"type": "string"},
                "role": {"type": "string",
                         "description": "Which body to draw. Defaults to the one with "
                                        "the most seats, since a single office covers "
                                        "the whole jurisdiction and its map says only "
                                        "where the government is."},
            },
            "required": ["pid6"],
        },
    },
    {
        "name": "compare_change",
        "description": "How much several governments' totals moved between two years, "
                       "with the median change for each government type. This is the "
                       "five-year comparison nearly the whole corpus can answer: around "
                       "1,500 Oregon governments filed 2017 and 2022 and nothing "
                       "between, while only about 380 filed every year. It is therefore "
                       "a change measured at two ends and never a trend — nothing in it "
                       "says whether the move was steady, front-loaded or reversed, so "
                       "never describe a direction over the middle years and never call "
                       "it a growth rate per year. No price index is in this data, so "
                       "the figures are nominal and a government that grew is not "
                       "necessarily buying more. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "measure": {"type": "string",
                            "enum": ["expenditure", "revenue", "debt"]},
            },
            "required": ["pid6_list"],
        },
    },
    {
        "name": "trend_panel",
        "description": "Several governments year by year across a stated window, each "
                       "labelled by how densely it is actually measured. The window is "
                       "chosen rather than inferred from whichever entity reported "
                       "longest, because an axis set by one government's filing habits "
                       "is a chart about that government. Coverage comes back per "
                       "entity: annual means every year in the window, endpoints means "
                       "two observations with a gap, and a line between endpoints is "
                       "drawn rather than measured — those are dashed and no year on "
                       "one may be described. Around 380 governments support an annual "
                       "five-year panel; for the rest use compare_change. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6_list": {"type": "array", "items": {"type": "string"}},
                "measure": {"type": "string",
                            "enum": ["expenditure", "revenue", "debt"]},
            },
            "required": ["pid6_list"],
        },
    },
    {
        "name": "community_context",
        "description": "The conditions of the place a government serves — median "
                       "household income, poverty, unemployment, health insurance, "
                       "commute — from the American Community Survey. Report these "
                       "BESIDE a government's fiscal figures and never as a result of "
                       "them. Nothing in either source says a budget produced a poverty "
                       "rate or that a poverty rate produced a budget, so do not "
                       "correlate them, rank places on the pair, or order a sentence so "
                       "one reads as the cause of the other. Counties and cities join "
                       "directly; a school or special district is not a Census "
                       "geography and is described by its host county, which is stated. "
                       "Every figure carries its margin of error and a reliability flag: "
                       "an estimate marked soft has a margin wider than 30% of itself "
                       "and must be reported with the margin or not at all. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": {"type": "string"},
                "vintage": {"type": "integer", "enum": [2024, 2023, 2019]},
            },
            "required": ["pid6"],
        },
    },
    {
        "name": "since_pandemic",
        "description": "One community indicator at the 2019 release and the latest one. "
                       "Two overlapping five-year windows set beside each other, never a "
                       "trend and never a rate of change: the 2019 release covers 2015 "
                       "to 2019 and the latest covers 2020 to 2024, each a five-year "
                       "average. Dollar indicators are refused outright because each "
                       "release is in its own dollars and no price index here can "
                       "separate inflation from change. The result says whether the two "
                       "estimates differ by more than their margins allow, using the "
                       "Census significance test; where they do not, say the survey "
                       "cannot separate them and never state a direction. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": {"type": "string"},
                "code": {"type": "string",
                         "description": "A DP03 variable code. Rates and percentages "
                                        "only; dollar variables are refused."},
            },
            "required": ["pid6"],
        },
    },
    {
        "name": "render_community_map",
        "description": "A condition of Oregon's places, mapped at county or place level. "
                       "Estimates whose margin of error exceeds 30% of the estimate are "
                       "withheld and draw as no data, because binning a figure the "
                       "survey cannot place puts a city in a colour band it has not been "
                       "shown to belong in. Where fewer than half the geographies "
                       "survive that test the map is refused entirely — at place level "
                       "that is unemployment, poverty and health insurance, which would "
                       "colour as few as 20 of 420 cities; counties are reliable for all "
                       "indicators and are the grain to use instead. Never place this "
                       "beside a spending map as cause and effect. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "vintage": {"type": "integer", "enum": [2024, 2023, 2019]},
                "geo_level": {"type": "string", "enum": ["county", "place"]},
                "county": {"type": "string",
                           "description": "Scope a place-level map to one county."},
            },
            "required": [],
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
                       "to scope a place map, which is unreadable statewide. The two function "
                       "metrics map one named function inside a service area, which is a "
                       "level deeper than the service_area metrics. A map is refused where "
                       "fewer than half the boundaries in the extent report the measure, and "
                       "the refusal names the governments that do the work instead: fire "
                       "protection and transit are done by districts with no boundary here, "
                       "so they cannot be mapped however the request is phrased. Use "
                       "who_spends_on for those. " + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "layer": {"type": "string", "enum": ["county", "place", "school_district"]},
                "metric": {"type": "string",
                           "enum": ["spending_per_resident", "total_spending",
                                    "capital_share", "employees_per_1000", "average_wage",
                                    "service_area_share", "service_area_per_resident",
                                    "function_share", "function_per_resident"]},
                "county": {"type": "string",
                           "description": "Optional county name to scope the extent to."},
                "service_area": {"type": "string",
                                 "description": "Required for the two service_area metrics."},
                "function": {"type": "string",
                             "description": "Required for the two function metrics. A named "
                                            "function inside a service area, as returned by "
                                            "drill or who_spends_on."},
            },
        },
    },
    {
        "name": "cost_per_head",
        "description": "What one government spends on a named function against the people "
                       "it employs in it: spending per firefighter, per officer, per "
                       "teacher. This is the only measure that crosses the finance and "
                       "workforce taxonomies, over a crosswalk that is many to many in "
                       "both directions, and both sides are summed before dividing. It is "
                       "operating plus capital over headcount, so it is not a wage: a high "
                       "figure is as likely to be a building year as a generous payroll. "
                       "Refused where the function does not appear among the entity's "
                       "listed employment functions, because the source reports top "
                       "functions only and an absent headcount is a truncated list rather "
                       "than a government doing the work with nobody. About half the "
                       "entities reporting fire protection are in that position. "
                       + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": PID,
                "function_name": {"type": "string",
                                  "description": "A named financial function, as returned "
                                                 "by drill or who_spends_on."},
            },
            "required": ["pid6", "function_name"],
        },
    },
    {
        "name": "place_topic_brief",
        "description": "One place and one issue: the governments with a claim on it, what "
                       "each reports in the categories that stand in for the issue, the "
                       "conditions residents report, and what to ask whom. Use this "
                       "whenever a question names a place and a subject together, which is "
                       "most questions people actually arrive with. It applies §12 in "
                       "order: the gap between the topic and the Census categories comes "
                       "first, then the proxy and how loose it is, then figures. It never "
                       "totals across the governments (§9: the same dollar appears in two "
                       "entities' expenditures) and never states an issue-level dollar "
                       "figure. Ask it of a city rather than a district or a county. "
                       + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "pid6": PID,
                "topic": {"type": "string",
                          "description": "The subject in the reader's words: housing, "
                                         "homelessness, wildfire, transit. Anything the "
                                         "concordance does not cover returns the gap "
                                         "statement with no categories, which is a "
                                         "legitimate answer."},
            },
            "required": ["pid6", "topic"],
        },
    },
    {
        "name": "inside_service_area",
        "description": "What one service area is made of across every government in "
                       "Oregon that reports it: each named function, how many "
                       "governments report it, what they spend on it, and which types "
                       "of government hold it. Use this where the question is how a "
                       "service is delivered rather than what one government spends. "
                       "Fire protection is 327 governments and $1.1B, police protection "
                       "is 187 and $1.9B, and the difference is that fire is district "
                       "work and policing is city and county work. Never add these "
                       "totals together and never state one as a share of the service "
                       "area: a city paying a district for the work reports the payment "
                       "while the district reports the spending, so the same dollar is "
                       "in both rows. Use drill for one government's own split. "
                       + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "service_area": {"type": "string",
                                 "description": "One of the twelve service areas, as "
                                                "returned by get_spending_breakdown."},
            },
            "required": ["service_area"],
        },
    },
    {
        "name": "render_point_map",
        "description": "Draw governments as pins over the county outlines, one pin per "
                       "office, sized by the measure. Use this where a boundary map "
                       "refuses or where the governments doing the work are special "
                       "districts: 1,029 of Oregon's 1,529 governments have no boundary "
                       "anywhere in this data, so every choropleth here omits two thirds "
                       "of them, and fire protection draws at one county in thirty-six "
                       "for exactly that reason while drawing at 300 governments here. "
                       "A pin is the city on the government's mailing address. It is "
                       "where the office is and never the ground served: a rural fire "
                       "district filed at a Eugene address covers land outside Eugene "
                       "entirely. Never describe a pin as coverage, a catchment or a "
                       "service area, and never read pin density as service density. "
                       + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string",
                           "description": "A map metric, service-area metric or function "
                                          "metric, as returned by atlas_measures."},
                "gov_type": {"type": "string",
                             "description": "Optional: County, Municipal, School District "
                                            "or Special District. Omit for all of them."},
                "county": {"type": "string",
                           "description": "Optional county to scope and frame to."},
                "service_area": {"type": "string",
                                 "description": "Required by a service-area metric."},
                "function": {"type": "string",
                             "description": "Required by a function metric."},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "render_function_map",
        "description": "Map one named function on the boundary layer that holds most of "
                       "the work, which is the right level below a service-area map. Police "
                       "protection lands on counties, sewerage on places, elementary and "
                       "secondary education on school districts. Prefer this to render_map "
                       "for a function, because choosing the layer by hand compares a city "
                       "against counties that do not do the work. Where no layer can carry "
                       "it the result is a refusal naming the governments that do, which is "
                       "the answer for fire protection and transit: districts with no "
                       "boundary in this data. Follow a refusal with who_spends_on. "
                       + ENVELOPE_NOTE,
        "input_schema": {
            "type": "object",
            "properties": {
                "function": {"type": "string",
                             "description": "A named function, as returned by drill."},
                "county": {"type": "string",
                           "description": "Optional county to scope a place map to."},
            },
            "required": ["function"],
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
