#!/usr/bin/env python3
"""Tests for the §15 response block vocabulary.

§15.2 fixes the block order per question type and §15.5 lists what never
appears. Both are structural, so both are assertable, which is the reason the
section was written as a vocabulary rather than as advice.

    python3 agent/test_blocks.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blocks as B
import format as fmt
import tools as T

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def main():
    store = T.Store()

    def pid(legal_name):
        return store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)["pid6"]

    baker = pid("COUNTY OF BAKER")
    new_bridge = pid("NEW BRIDGE WATER SUPPLY DISTRICT")

    print("\nthe vocabulary matches §15.2")
    for question_type, order in B.BLOCK_ORDER.items():
        check(f"{question_type} opens with interpretation",
              order[0] == "interpretation", str(order))
        check(f"{question_type} answers before it draws",
              order.index("answer") < min(
                  [order.index(k) for k in ("figure", "chart", "map", "table")
                   if k in order] or [99]), str(order))
        check(f"{question_type} ends with notes then next",
              order[-2:] == ["notes", "next"], str(order))

    print("\ncompose orders and drops")
    entity = T._entity(store, baker)
    made = B.compose("financial_interpretation", [
        {"kind": "chart", "svg": "<svg/>"},
        B.interpretation(store, entity, {"finance": 2022}),
        {"kind": "answer", "text": "x"},
        None,
    ])
    check("blocks are reordered into §15.2 order",
          [b["kind"] for b in made] == ["interpretation", "answer", "chart"],
          str([b["kind"] for b in made]))
    check("absent blocks are dropped rather than stubbed", len(made) == 3)

    print("\nvalidation catches §15 violations")
    check("an out-of-order response is caught",
          B.validate("financial_interpretation", [
              {"kind": "chart"}, {"kind": "answer"}]))
    check("a response with no answer is caught",
          any("answer" in p for p in B.validate("factual_lookup", [{"kind": "figure"}])))
    check("a block the type may not use is caught",
          any("may use" in p for p in B.validate("governance", [
              {"kind": "answer"}, {"kind": "map"}])))
    check("a chart of a single number is caught",
          any("single number" in p for p in B.validate("financial_interpretation", [
              {"kind": "answer"}, {"kind": "chart", "points": 1}])))
    check("a map that is mostly absence is caught",
          any("mostly absence" in p for p in B.validate("place_or_cross_entity", [
              {"kind": "answer"}, {"kind": "map", "coverage": 0.2}])))
    check("a map above the floor passes",
          not any("mostly absence" in p for p in B.validate("place_or_cross_entity", [
              {"kind": "answer"}, {"kind": "map", "coverage": 0.9}])))
    check("a ranking that drops entities silently is caught",
          any("silently" in p for p in B.validate("place_or_cross_entity", [
              {"kind": "answer"}, {"kind": "table", "rows": [], "dropped": 2}])))
    check("a long table leading with a chart is caught",
          any("lead with the table" in p for p in B.validate("financial_interpretation", [
              {"kind": "answer"}, {"kind": "chart"},
              {"kind": "table", "rows": [{}] * 9}])))
    check("a conforming response has no violations",
          B.validate("financial_interpretation", [
              {"kind": "interpretation"}, {"kind": "answer"},
              {"kind": "figure", "value": "$2.0B", "legend": []},
              {"kind": "chart"}, {"kind": "notes"}, {"kind": "next"}]) == [])

    print("\n§15.3 marks")
    marked = B.figure("Spending", fmt.money(2_040_000_000), marks={"partial_breakdown"})
    check("a marked figure carries the glyph", "‡" in marked["value"], marked["value"])
    check("and its legend line", marked["legend"] and "partial" in marked["legend"][0])
    check("a marked figure without a legend is caught",
          any("legend" in p for p in B.validate("factual_lookup", [
              {"kind": "answer"}, {"kind": "figure", "value": "$2.0B†", "legend": []}])))

    plain = B.figure("Spending", fmt.money(410_000))
    check("an unmarked figure has no glyph and no legend",
          plain["value"] == "$410K" and plain["legend"] == [])

    partial = store.row("SELECT * FROM entity_flags WHERE other_share_high=1")
    check("a partial breakdown earns a mark",
          "partial_breakdown" in B.marks_for(dict(partial)))
    clean = store.row("SELECT * FROM entity_flags WHERE other_share_high=0 "
                      "AND debt_capped=1")
    check("a capped composite does not, because its rule says it better",
          B.marks_for(dict(clean)) == set(), str(B.marks_for(dict(clean))))

    print("\ninterpretation (§15.1)")
    block = B.interpretation(store, entity, {"finance": 2022, "workforce": None})
    check("it names the entity and its type",
          block["entity"] == "Baker County" and block["gov_type"] == "County")
    check("it carries the legal name for record matching",
          block["legal_name"] == "COUNTY OF BAKER")
    check("empty vintages are dropped", "workforce" not in block["vintages"])
    check("no entity means no interpretation block",
          B.interpretation(store, None) is None)

    print("\nnext questions are gated (§15.1)")
    offered = B.next_questions(store, new_bridge)["questions"]
    check("a question with no data behind it is never offered",
          "Where does the money actually go?" not in offered, str(offered))
    offered = B.next_questions(store, baker, asked={"Who governs this entity?"})["questions"]
    check("the question just asked is not offered back",
          "Who governs this entity?" not in offered, str(offered))
    check("at most three are offered", len(offered) <= 3)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
