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
