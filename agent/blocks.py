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
from rules import THRESHOLDS, reader_note

# §15.1: a map is offered only where the picture is not mostly absence.
#
# One rule enforced at two layers, so it is defined once. render_map refuses to
# draw below this and returns the reason in place of the picture; this is the
# backstop that catches a map block which got here some other way. The two used
# to be separate literals that happened to agree, which is the state a rule is
# in just before it stops agreeing.
MAP_COVERAGE_FLOOR = THRESHOLDS["map_density_minimum"]

# §15.1: past roughly seven rows colour classes blur and the reader is looking
# up rather than comparing, so the table becomes the primary form.
TABLE_OVER_CHART = 7

# §15.2.
BLOCK_ORDER = {
    "factual_lookup": ["interpretation", "answer", "figure", "notes", "next"],
    "governance": ["interpretation", "answer", "table", "notes", "next"],
    # A map earns a place in a financial answer where the question is "is this
    # a lot", because a peer distribution says where a government sits among its
    # peers and a map says where it sits in the state, on the same measure.
    "financial_interpretation": ["interpretation", "answer", "figure", "chart",
                                 "map", "notes", "next"],
    # A "what stands out" answer takes a table where the finding is a ranking of
    # divergences: the lead goes in the answer and the ranking is what the reader
    # checks it against. A cross-entity answer takes a chart, because several
    # governments on one axis is a comparison a table makes the reader perform.
    "what_stands_out": ["interpretation", "answer", "figure", "chart", "table",
                        "notes", "next"],
    "place_or_cross_entity": ["interpretation", "answer", "figure", "chart",
                              "table", "map", "report", "notes", "next"],
    # The funnel. A brief opens on the gap, then the state, then the place,
    # then the bodies, then the money: a reader scoping a subject in a place
    # needs to know where that place sits before any figure about it means
    # anything. The map earned its place in §15.2 for this question type
    # because the brief is where somebody arrives first and it had no picture
    # in it at all.
    # A tuple is one slot holding several kinds in the order the composer wrote
    # them. Everywhere else in §15.2 a question has one chart and one table and
    # grouping by kind is the same as authored order; a brief is a document with
    # a funnel in it — the state, then the stack, then one government, then the
    # categories inside it, then whether that money builds or runs — and each
    # step is a picture with its table under it. Grouping by kind there
    # collected four charts at the top and four tables beneath them, which is
    # the same blocks and none of the argument.
    "issue_or_topic": ["interpretation", "answer", "map",
                       ("text", "chart", "table"), "notes", "next"],
    "investment_vs_conditions": ["interpretation", "answer", "table", "notes", "next"],
}

# Follow-ups are offered only where the block behind them exists for the entity.
# Ordered so the first few carry a reader downward: from the whole budget, to
# the categories it splits into, to the functions inside one, and then out to
# where that function is done across the state. A follow-up that opens a new
# topic is offered after the ones that go deeper into this one.
NEXT_QUESTIONS = [
    # Money the reader recognises as their own leads, because it is the one
    # follow-up that does not require already caring about municipal finance.
    ("has_revenue_sources", "Whose money is this — your tax bill, or the state's?"),
    ("has_workforce", "Who does it employ, and how thinly are they spread?"),
    ("has_spending_breakdown", "Where does the money actually go?"),
    ("has_spending_breakdown", "Which service areas is it unusual on?"),
    ("has_financial_functions", "Take me inside its largest service area"),
    ("has_operating_vs_capital", "Is this budget large or small for its type?"),
    ("has_spending_breakdown", "How does its public safety spending compare, head for head?"),
    ("has_financial_trends", "Has its spending outgrown its peers?"),
    ("has_financial_trends", "What does it owe, and against how much a year?"),
    ("has_offices", "Who runs it, and do you elect them?"),
    # Keyed to has_serving, which is computed rather than a manifest column: a
    # town with no established stack must not be offered one, and offering it
    # everywhere was how the label and the preset drifted apart the first time.
    ("has_serving", "Which governments serve you here?"),
    (None, "What else is filed under this county?"),
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


def figure(label, value_text, marks=None, basis=None, year=None, context=None):
    """`context` is the comparison the figure is read against, which §8 requires
    to travel with any number offered as large or small. A median without its
    pool is not a benchmark, so it belongs on the figure, not in a footnote."""
    kinds = sorted(marks or [])
    return {"kind": "figure", "label": label,
            "value": fmt.mark(value_text, kinds),
            "basis": basis, "year": year, "context": context,
            "legend": fmt.mark_legend(kinds)}


def notes(results, said=(), limit=5):
    """What this answer cannot tell the reader, in the reader's words.

    This replaced a block that listed every rule bound to the answer, headed
    "11 rules bind this answer". Every one of the eleven did real work, but
    almost all of it was aimed at whoever wrote the answer rather than at
    whoever reads it: name the driver before the label, decline the distance
    calculation, never divide one row by one row. A reader given those is being
    handed the factory floor, and the ones that would have mattered to them were
    buried among the ones that could not.

    So the rules still all reach the model, unchanged and in full. What surfaces
    here is `rules.READER_NOTES`: the subset that is a fact about the data and
    that changes what somebody would conclude or do next, phrased declaratively
    because a person is being told what is true rather than what to do.

    `said` names codes the answer already states in prose, so the block does not
    repeat a sentence the reader just read. The cap exists because a list long
    enough to skim past is the thing this is replacing.
    """
    said = set(said)
    items, seen = [], set()
    for result in results:
        for rule in list(result.get("caveats", [])) + list(
                result.get("not_computable", [])):
            code = rule["code"]
            if code in seen or code in said:
                continue
            seen.add(code)
            text = reader_note(code)
            if text:
                items.append({"code": code, "rule": rule["rule"], "text": text})
    return {"kind": "notes", "items": items[:limit]} if items else None


def next_questions(store, pid6, asked=None, limit=3):
    """§15.1: never offer a question whose block is absent for this entity."""
    import tools
    available = tools.availability(store, pid6) if pid6 else {}
    asked = set(asked or [])
    offered = [q for needs, q in NEXT_QUESTIONS
               if q not in asked and (needs is None or available.get(needs))]
    return {"kind": "next", "questions": offered[:limit]} if offered else None


def compose(question_type, blocks):
    """Order blocks per §15.2 and drop what is absent.

    Blocks are dropped, never padded: an entity with nothing unusual gets an
    interpretation, an answer, its notes and a next, and no chart.
    """
    order = BLOCK_ORDER.get(question_type, BLOCK_ORDER["factual_lookup"])
    by_kind = {}
    for block in blocks:
        if block:
            by_kind.setdefault(block["kind"], []).append(block)
    ordered = []
    for kind in order:
        if isinstance(kind, tuple):
            held = {k for k in kind}
            ordered += [b for b in blocks if b and b["kind"] in held]
            for k in kind:
                by_kind.pop(k, None)
            continue
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

    # §15.2: order is fixed, though any block may be absent. A tuple in the
    # order is one slot, so the kinds inside it share a position and may
    # interleave freely — that is what makes a slot rather than two entries.
    slot = {}
    for index, kind in enumerate(order):
        for name in (kind if isinstance(kind, tuple) else (kind,)):
            slot[name] = index
    positions = [slot[k] for k in kinds if k in slot]
    if positions != sorted(positions):
        problems.append(f"blocks out of §15.2 order for {question_type}: {kinds}")

    for kind in kinds:
        if kind not in slot:
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

    # §15.1: past the row threshold the table is the primary form and a chart of
    # the same rows is the wrong lead.
    #
    # Scoped to one chart against one table, which is the case the rule is
    # about: a single answer that drew its one table as its one chart. A brief
    # is several pictures each with its own table under it, and the unscoped
    # rule paired every chart with every table — the three-government stack
    # chart was reported as the wrong lead for a nine-row table of districts
    # filed in another county, which is a different subject entirely. A chart
    # with too many marks of its own is still caught, by `points` below.
    tables = [b for b in blocks if b["kind"] == "table"]
    charts = [b for b in blocks if b["kind"] == "chart"]
    if len(tables) == 1 and len(charts) == 1:
        rows = len(tables[0].get("rows") or [])
        if rows > TABLE_OVER_CHART:
            problems.append(
                f"{rows} rows past the §15.1 threshold of "
                f"{TABLE_OVER_CHART} should lead with the table, not a chart")
    for chart in charts:
        marks = chart.get("points")
        if marks is not None and marks > TABLE_OVER_CHART * 2:
            problems.append(
                f"a chart of {marks} marks is a table; §15.1 draws at most "
                f"{TABLE_OVER_CHART * 2}")

    # §15.4: a figure carrying a mark must carry its legend line.
    for block in blocks:
        if block["kind"] == "figure":
            has_mark = any(glyph in (block.get("value") or "")
                           for glyph, _ in fmt.MARKS.values())
            if has_mark and not block.get("legend"):
                problems.append("a marked figure with no legend line (§15.3)")

    return problems
