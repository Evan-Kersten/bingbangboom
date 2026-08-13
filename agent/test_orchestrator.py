#!/usr/bin/env python3
"""Tests for the orchestrator.

Uses a scripted model that emits a fixed sequence of tool calls, so the loop,
the routing, the error handling and the trace are all verifiable without
credentials. What a real model chooses to do is a separate question, measured
by evals/run.py --model; this file tests the machinery around it.

    python3 agent/test_orchestrator.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import orchestrator as O
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


class ScriptedModel:
    """Replays a script of turns. Each turn is a list of tool calls, or text."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen = []

    def __call__(self, system, messages, tools):
        self.seen.append({"system": system, "messages": messages, "tools": tools})
        turn = self.turns.pop(0) if self.turns else "done"
        if isinstance(turn, str):
            return {"content": [{"type": "text", "text": turn}], "stop_reason": "end_turn"}
        content = []
        for index, (name, arguments) in enumerate(turn):
            content.append({"type": "tool_use", "id": f"call_{len(self.seen)}_{index}",
                            "name": name, "input": arguments})
        return {"content": content, "stop_reason": "tool_use"}


def main():
    store = T.Store()
    sections = O.load_sections()
    sumpter = store.row("SELECT pid6 FROM entities WHERE legal_name='CITY OF SUMPTER'")["pid6"]

    print("\nclassification (§5)")
    cases = [
        ("How much does Multnomah County spend on homelessness?", "issue_or_topic"),
        ("Which governments serve Multnomah County?", "place_or_cross_entity"),
        ("Who governs Baker County?", "governance"),
        ("What stands out about Portland?", "what_stands_out"),
        ("Is Sumpter financially healthy?", "financial_interpretation"),
        ("What kind of government is Baker County?", "factual_lookup"),
        ("How does spending compare to the poverty rate here?", "investment_vs_conditions"),
    ]
    for question, expected in cases:
        got = O.classify(question)
        check(f"'{question[:44]}' routes to {expected}", got == expected, f"got {got}")

    check("an issue inside a place is routed as an issue, not a place",
          O.classify("What is Lane County doing about homelessness?") == "issue_or_topic")

    # Regressions. Each of these was misrouted to factual_lookup, which loses the
    # section the answer depends on.
    check("a causation question reaches §10, not a bare lookup",
          O.classify("Does higher school spending cause better outcomes for students?")
          == "investment_vs_conditions")
    check("a question about an absent service is an ecosystem question (§9)",
          O.classify("Why does Sumpter spend nothing on health?") == "place_or_cross_entity")
    check("a two-entity comparison reaches §13",
          O.classify("Which is better managed, Portland or Washington County?")
          == "place_or_cross_entity")
    check("a cross-entity ranking is not a lookup",
          O.classify("Which counties have the worst fiscal stability?")
          == "place_or_cross_entity")
    check("but a single-entity peer benchmark stays a lookup",
          O.classify("How does New Bridge's capital spending compare to its peers?")
          == "factual_lookup")
    check("§14 response patterns load on every question type",
          all("14" in [l[2:].split(".")[0].strip()
                       for l in O.compose_prompt(t, sections).split("\n") if l.startswith("# ")]
              for t in O.ROUTING))

    print("\nprompt composition")
    full = len(open(O.PROMPT_PATH).read())
    for question_type, routed in O.ROUTING.items():
        prompt = O.compose_prompt(question_type, sections)
        headings = [line[2:].split(".")[0].strip() for line in prompt.split("\n")
                    if line.startswith("# ")]
        check(f"{question_type} includes every routed section",
              all(s in headings for s in routed), f"wanted {routed}, got {headings}")
    check("topic routing keeps section 12 despite the substring trap",
          "12" in [l[2:].split(".")[0].strip()
                   for l in O.compose_prompt("issue_or_topic", sections).split("\n")
                   if l.startswith("# ")])
    sizes = [len(O.compose_prompt(t, sections)) for t in O.ROUTING]
    check("routed prompts are smaller than the full prompt", max(sizes) < full,
          f"largest {max(sizes)} vs {full}")

    print("\ntool loop")
    model = ScriptedModel(
        [("find_entity", {"name": "Sumpter"})],
        [("get_financial_position", {"pid6": sumpter})],
        "Sumpter scores 60. The driver is debt-to-revenue.")
    agent = O.Orchestrator(store=store, client=model, sections=sections)
    answer, trace = agent.answer("Is Sumpter financially healthy?")

    check("the loop returns the model's final text", answer.startswith("Sumpter scores 60"))
    check("the loop ran three turns", trace["turns"] == 3, str(trace["turns"]))
    check("both tool calls are traced", [c["tool"] for c in trace["calls"]] ==
          ["find_entity", "get_financial_position"], str(trace["calls"]))
    check("the debt cap caveat was delivered to the model",
          "debt_capped" in trace["caveats_delivered"], str(trace["caveats_delivered"]))
    check("the prior-year caveat was delivered",
          "prior_year_missing" in trace["caveats_delivered"])
    check("the service-area YoY block was delivered",
          "service_area_yoy" in trace["not_computable_delivered"])
    check("the question was classified as financial interpretation",
          trace["question_type"] == "financial_interpretation")
    check("tools were offered to the model", len(model.seen[0]["tools"]) == len(T.TOOLS))

    print("\ncaveats reach the model as requirements, not as data")
    rendered = model.seen[2]["messages"][-1]["content"][0]["content"]
    check("the rendered result states caveats as a requirement",
          "MUST reflect" in rendered, rendered[:120])
    check("and states not_computable as a prohibition", "MUST NOT" in rendered)
    check("the guidance text itself is present, not just the code",
          "capped near 60" in rendered)

    print("\nerror handling")
    agent = O.Orchestrator(store=store, client=ScriptedModel(
        [("no_such_tool", {})], "recovered"), sections=sections)
    answer, trace = agent.answer("What kind of government is Baker County?")
    check("an unknown tool does not raise", answer == "recovered")
    check("and the model is told the tool does not exist",
          "unknown_tool" in trace["caveats_delivered"])

    agent = O.Orchestrator(store=store, client=ScriptedModel(
        [("get_financial_position", {"wrong_argument": 1})], "recovered"), sections=sections)
    answer, trace = agent.answer("Is Portland healthy?")
    check("bad arguments do not raise", answer == "recovered")
    check("and the model is told why", "bad_arguments" in trace["caveats_delivered"])

    agent = O.Orchestrator(store=store, client=ScriptedModel(
        [("get_financial_position", {"pid6": "does-not-exist"})], "recovered"), sections=sections)
    answer, trace = agent.answer("Is Nowhere healthy?")
    check("an unknown entity does not raise", answer == "recovered")

    print("\nturn limit")
    forever = ScriptedModel(*[[("find_entity", {"name": "x"})] for _ in range(20)])
    agent = O.Orchestrator(store=store, client=forever, max_turns=3, sections=sections)
    answer, trace = agent.answer("What kind of government is Baker County?")
    check("the loop stops at max_turns", trace["turns"] == 3, str(trace["turns"]))
    check("and reports that it hit the limit", trace["hit_turn_limit"] is True)

    print("\nparallel tool calls in one turn")
    agent = O.Orchestrator(store=store, client=ScriptedModel(
        [("get_financial_position", {"pid6": sumpter}),
         ("get_spending_breakdown", {"pid6": sumpter})], "both"), sections=sections)
    answer, trace = agent.answer("Tell me about Sumpter's finances.")
    check("two calls in one turn are both dispatched", len(trace["calls"]) == 2)
    check("and both sets of caveats are delivered",
          "debt_capped" in trace["caveats_delivered"] and
          "inputs_not_outcomes" in trace["caveats_delivered"])

    print("\nno client")
    try:
        O.Orchestrator(store=store, sections=sections).answer("anything")
        check("answering without a client raises", False)
    except RuntimeError:
        check("answering without a client raises", True)

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
