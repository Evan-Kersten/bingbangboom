#!/usr/bin/env python3
"""Eval runner.

Three modes, because they answer three different questions.

    python3 evals/run.py --probe
        Runs the tool probes only. Asserts the tool layer hands the model the
        caveat each rule depends on. No credentials, no network, runs in CI.

    python3 evals/run.py --route
        Also checks the classifier routes each question to the sections the
        case declares it needs. Still no credentials.

    python3 evals/run.py --model claude-opus-5
        Puts the question through the orchestrator, with tools, and asserts on
        the answer: required and forbidden phrasing, length, and §4 style.

The last mode reports two kinds of failure separately, and the distinction is
the point of the harness:

    delivery   the caveat never reached the model. A tool layer bug.
    adherence  the caveat reached the model and the answer broke the rule
               anyway. A prompt or model bug.

They look identical in the output text and have completely different fixes.
"""

import argparse
import json
import os
import re
import sys

AGENT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
sys.path.insert(0, AGENT)

import format as fmt
import orchestrator as O
import tools as T

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases.json")


def resolve(store, legal_name):
    row = store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)
    if not row:
        raise SystemExit(f"case fixture missing from the data: {legal_name}")
    return row["pid6"]


def run_probe(store, case):
    """Assert the tool layer surfaces the caveats this case depends on."""
    probe = case.get("tool_probe")
    if not probe:
        return []

    tool = T.TOOLS[probe["tool"]]
    args = dict(probe.get("args") or {})
    if "entity" in probe:
        args = {"pid6": resolve(store, probe["entity"]), **args}
    elif "entities" in probe:
        args = {"pid6_list": [resolve(store, name) for name in probe["entities"]], **args}

    result = tool(store, **args)
    caveats = {c["code"] for c in result["caveats"]}
    blocked = {c["code"] for c in result["not_computable"]}

    problems = []
    for code in probe.get("must_caveat", []):
        if code not in caveats:
            problems.append(("delivery", f"tool omits caveat '{code}' "
                                         f"(got: {sorted(caveats) or 'none'})"))
    for code in probe.get("must_block", []):
        if code not in blocked:
            problems.append(("delivery", f"tool omits not_computable '{code}' "
                                         f"(got: {sorted(blocked) or 'none'})"))
    return problems


def run_routing(case):
    """Assert the classifier reaches the sections this case says it needs."""
    declared = [s for s in case.get("routes_to", []) if s not in set(O.ALWAYS)]
    if not declared:
        return []
    question_type = O.classify(case["question"])
    reached = set(O.ROUTING.get(question_type, []))
    missing = [s for s in declared if s not in reached]
    if missing:
        return [("routing", f"classified as '{question_type}', which never reaches "
                            f"section(s) {missing}")]
    return []


def run_answer(answer, trace, case):
    """Assert on generated prose, separating adherence from delivery."""
    problems = []
    lowered = answer.lower()
    delivered = set(trace["caveats_delivered"]) | set(trace["not_computable_delivered"])
    probe = case.get("tool_probe") or {}
    expected = set(probe.get("must_caveat", [])) | set(probe.get("must_block", []))

    if not answer.strip():
        problems.append(("adherence", "no answer produced" +
                         (" (hit the turn limit)" if trace["hit_turn_limit"] else "")))
        return problems

    for code in expected - delivered:
        problems.append(("delivery", f"'{code}' never reached the model in this run"))

    def kind(rule_codes):
        """A violation is adherence when the guardrail was delivered anyway."""
        return "adherence" if (rule_codes & delivered or not rule_codes) else "delivery"

    for phrase in case.get("answer_must_mention", []):
        if phrase.lower() not in lowered:
            problems.append((kind(expected), f"answer never mentions '{phrase}'"))
    for pattern in case.get("answer_must_match", []):
        if not re.search(pattern, lowered, re.I):
            problems.append((kind(expected), f"answer matches nothing in /{pattern}/"))
    for pattern in case.get("answer_must_not_match", []):
        found = re.search(pattern, lowered, re.I)
        if found:
            problems.append((kind(expected),
                             f"answer contains forbidden /{pattern}/ -> {found.group(0)!r}"))

    limit = case.get("answer_max_words")
    if limit and len(answer.split()) > limit:
        problems.append(("adherence",
                         f"answer runs {len(answer.split())} words against a {limit} limit"))

    for violation in fmt.check_style(answer):
        problems.append(("adherence", f"§4 style: {violation['kind']} {violation['detail']!r}"))
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="tool probes only")
    parser.add_argument("--route", action="store_true", help="also check classifier routing")
    parser.add_argument("--model", help="model id, e.g. claude-opus-5")
    parser.add_argument("--case", help="run a single case by id")
    parser.add_argument("--trace", action="store_true", help="print the tool trace per case")
    args = parser.parse_args()

    with open(CASES) as fh:
        cases = json.load(fh)
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            raise SystemExit(f"no case with id {args.case}")

    store = T.Store()
    agent = None
    if args.model:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("No ANTHROPIC_API_KEY in the environment.\n"
                             "Run with --probe or --route to check without a model.")
        agent = O.Orchestrator(store=store, client=O.anthropic_client(args.model))

    passed = failed = 0
    tally = {"delivery": 0, "adherence": 0, "routing": 0}

    for case in cases:
        problems = run_probe(store, case)
        if args.route or args.model:
            problems += run_routing(case)

        if agent:
            answer, trace = agent.answer(case["question"])
            problems += run_answer(answer, trace, case)
            if args.trace:
                print(f"        [{trace['question_type']}] "
                      f"{' -> '.join(c['tool'] for c in trace['calls']) or 'no tool calls'}")

        if problems:
            failed += 1
            print(f"  FAIL  {case['id']}")
            for category, detail in problems:
                tally[category] = tally.get(category, 0) + 1
                print(f"          [{category}] {detail}")
        else:
            passed += 1
            print(f"  pass  {case['id']}")

    print(f"\n{passed}/{passed + failed} cases passed")
    if failed:
        print(f"  delivery failures  {tally['delivery']}  (tool layer: the caveat never arrived)")
        print(f"  adherence failures {tally['adherence']}  (prompt or model: it arrived and was ignored)")
        if tally["routing"]:
            print(f"  routing failures   {tally['routing']}  (classifier: wrong sections loaded)")
    if not agent:
        print("\nNo model run. Answer assertions need --model; they are what tell you\n"
              "whether a caveat that reached the context actually changed the answer.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
