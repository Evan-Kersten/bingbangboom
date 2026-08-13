"""The §15 response block vocabulary.

An answer is a sequence of typed blocks in a fixed order. The model chooses
which blocks appear; it never chooses their wording, colour or layout.

The shape is borrowed from Wolfram Alpha, whose results open with an
interpretation pod before anything else. That block matters more here than it
does there: overlapping jurisdictions mean "Astoria" resolves to a city, a
school district and a port, and a reader who does not know which one was
answered cannot use the answer at all.

The uncertainty marks are borrowed from Census Reporter, which flags an ACS
estimate with a dagger where the margin of error reaches a tenth of the value,
on the figure rather than in a footnote. The same instinct applies to a
below-threshold office match or a boundary matched by name.

`validate` exists because §15.2 and §15.5 are checkable. A chart of a single
number, a map that is mostly absence, a ranking that drops entities silently:
all of those are structural, so they are caught here rather than hoped away.
"""

import format as fmt
import tools as T

# §15.1: a map is offered only where the picture is not mostly absence.
MAP_COVERAGE_FLOOR = 0.5

# §15.1: past roughly seven rows colour classes blur and the reader is looking
# up rather than comparing, so the table becomes the primary form.
TABLE_OVER_CHART = 7

# §15.2, verbatim.
BLOCK_ORDER = {
    "factual_lookup": ["interpretation", "answer", "figure", "limits", "next"],
    "governance": ["interpretation", "answer", "table", "limits", "next"],
    "financial_interpretation": ["interpretation", "answer", "figure", "chart",
                                 "limits", "next"],
    "what_stands_out": ["interpretation", "answer", "chart", "limits", "next"],
    "place_or_cross_entity": ["interpretation", "answer", "table", "map",
                              "limits", "next"],
    "issue_or_topic": ["interpretation", "answer", "table", "limits", "next"],
    "investment_vs_conditions": ["interpretation", "answer", "table", "limits", "next"],
}

# Follow-ups are offered only where the block behind them exists for the entity.
NEXT_QUESTIONS = [
    ("has_spending_breakdown", "Where does the money actually go?"),
    ("has_fiscal_stability", "What is driving the fiscal stability score?"),
    ("has_workforce", "How is the workforce distributed?"),
    ("has_offices", "Who governs this entity?"),
    ("has_financial_trends", "How has revenue and spending moved?"),
    (None, "Which governments serve this place?"),
    (None, "What can this data not tell me?"),
]


def marks_for(flags, extra=None):
    """Which §15.3 marks a figure from this entity carries.

    Deliberately narrow. §15.3 says a page of daggers carries no information, so
    a mark fires only where it changes how the number should be read, and never
    where the rules block already says the same thing better. A capped composite
    is explained by its rule, not by a glyph.
    """
    kinds = set(extra or [])
    if flags.get("other_share_high"):
        kinds.add("partial_breakdown")
    return kinds


def interpretation(store, entity, vintages=None, assumption=None):
    """§15.1: what was understood, before anything else."""
    if not entity:
        return None
    return {
        "kind": "interpretation",
        "entity": entity["common_name"] or entity["legal_name"],
        "legal_name": entity["legal_name"],
        "gov_type": entity["gov_type_name"],
        "host_county": (entity["host_county"] or "").title() or None,
        "vintages": {k: v for k, v in (vintages or {}).items() if v},
        "assumption": assumption,
    }


def figure(label, value_text, marks=None, basis=None, year=None):
    kinds = sorted(marks or [])
    return {"kind": "figure", "label": label,
            "value": fmt.mark(value_text, kinds),
            "basis": basis, "year": year,
            "legend": fmt.mark_legend(kinds)}


def limits(results):
    """One de-duplicated rules block, prohibitions marked apart (§15.1)."""
    rules, seen = [], set()
    for result in results:
        for rule in result.get("caveats", []):
            if rule["code"] not in seen:
                seen.add(rule["code"])
                rules.append({**rule, "kind": "must"})
        for rule in result.get("not_computable", []):
            if rule["code"] not in seen:
                seen.add(rule["code"])
                rules.append({"code": rule["code"], "rule": rule["rule"],
                              "guidance": rule["reason"], "kind": "never"})
    return {"kind": "limits", "rules": rules} if rules else None


def next_questions(store, pid6, asked=None, limit=3):
    """§15.1: never offer a question whose block is absent for this entity."""
    available = {}
    if pid6:
        row = store.row("SELECT * FROM data_availability WHERE pid6=?", pid6)
        available = dict(row) if row else {}
    asked = set(asked or [])
    offered = [q for needs, q in NEXT_QUESTIONS
               if q not in asked and (needs is None or available.get(needs))]
    return {"kind": "next", "questions": offered[:limit]} if offered else None


def compose(question_type, blocks):
    """Order blocks per §15.2 and drop what is absent.

    Blocks are dropped, never padded: an entity with nothing unusual gets an
    interpretation, an answer, its limits and a next, and no chart.
    """
    order = BLOCK_ORDER.get(question_type, BLOCK_ORDER["factual_lookup"])
    by_kind = {}
    for block in blocks:
        if block:
            by_kind.setdefault(block["kind"], []).append(block)
    ordered = []
    for kind in order:
        ordered += by_kind.pop(kind, [])
    # Anything the vocabulary does not place keeps its relative position at the
    # end rather than being silently discarded.
    for remaining in by_kind.values():
        ordered += remaining
    return ordered


# ------------------------------------------------------------- validation

def validate(question_type, blocks):
    """Return §15 violations. Empty means the response conforms."""
    problems = []
    kinds = [b["kind"] for b in blocks]
    order = BLOCK_ORDER.get(question_type, BLOCK_ORDER["factual_lookup"])

    # §15.2: order is fixed, though any block may be absent.
    positions = [order.index(k) for k in kinds if k in order]
    if positions != sorted(positions):
        problems.append(f"blocks out of §15.2 order for {question_type}: {kinds}")

    for kind in kinds:
        if kind not in order:
            problems.append(f"'{kind}' is not a block {question_type} may use")

    if "answer" not in kinds:
        problems.append("every response carries an answer block")

    # §15.5: a chart of a single number.
    for block in blocks:
        if block["kind"] == "chart" and block.get("points") == 1:
            problems.append("a chart of a single number; use a figure block")
        if block["kind"] == "map":
            coverage = block.get("coverage")
            if coverage is not None and coverage < MAP_COVERAGE_FLOOR:
                problems.append(
                    f"a map at {coverage:.0%} coverage is mostly absence; "
                    f"§15.1 floor is {MAP_COVERAGE_FLOOR:.0%}")
        if block["kind"] == "table" and block.get("dropped"):
            problems.append("a ranking that silently drops entities; name them")

    # §15.1: a chart that restates the sentence is noise, and past the row
    # threshold the table is the primary form.
    tables = [b for b in blocks if b["kind"] == "table"]
    charts = [b for b in blocks if b["kind"] == "chart"]
    for table in tables:
        if len(table.get("rows") or []) > TABLE_OVER_CHART and charts:
            problems.append(
                f"{len(table['rows'])} rows past the §15.1 threshold of "
                f"{TABLE_OVER_CHART} should lead with the table, not a chart")

    # §15.4: a figure carrying a mark must carry its legend line.
    for block in blocks:
        if block["kind"] == "figure":
            has_mark = any(glyph in (block.get("value") or "")
                           for glyph, _ in fmt.MARKS.values())
            if has_mark and not block.get("legend"):
                problems.append("a marked figure with no legend line (§15.3)")

    return problems
