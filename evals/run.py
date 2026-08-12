#!/usr/bin/env python3
"""Eval runner.

Two modes, because they answer different questions.

    python3 evals/run.py --probe
        Runs the tool probes only. Asserts that for every case, the tool layer
        actually hands the model the caveat the rule depends on. Needs no model
        and no network, so it runs in CI.

    python3 evals/run.py --model <name>
        Also puts the question to a model and asserts on the answer: required
        and forbidden phrasing, length, and §4 style. Needs credentials.

The probe mode is the one that catches regressions in the guardrails. The model
mode is the one that tells you whether a prompt change helped. Neither replaces
the other: a caveat that reaches the context and is then ignored looks fine to
the probe and fails the model run, which is exactly the distinction worth
being able to see.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

import format as fmt
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
        return None, ["no tool probe defined"]

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
            problems.append(f"missing caveat '{code}' (got: {sorted(caveats) or 'none'})")
    for code in probe.get("must_block", []):
        if code not in blocked:
            problems.append(f"missing not_computable '{code}' (got: {sorted(blocked) or 'none'})")
    return result, problems


def run_answer(answer, case):
    """Assert on generated prose."""
    problems = []
    lowered = answer.lower()

    for phrase in case.get("answer_must_mention", []):
        if phrase.lower() not in lowered:
            problems.append(f"answer never mentions '{phrase}'")
    for pattern in case.get("answer_must_match", []):
        if not re.search(pattern, lowered, re.I):
            problems.append(f"answer matches nothing in /{pattern}/")
    for pattern in case.get("answer_must_not_match", []):
        found = re.search(pattern, lowered, re.I)
        if found:
            problems.append(f"answer contains forbidden /{pattern}/ -> {found.group(0)!r}")

    limit = case.get("answer_max_words")
    if limit:
        words = len(answer.split())
        if words > limit:
            problems.append(f"answer runs {words} words against a {limit} word limit")

    for violation in fmt.check_style(answer):
        problems.append(f"§4 style: {violation['kind']} {violation['detail']!r}")
    return problems


def load_model(name):
    """Return a callable(question, context) -> str, or exit with what is missing.

    Deliberately thin. The point of the harness is that the same case set can be
    run against a self-hosted model and a frontier model and the rule adherence
    compared directly, so the model is a parameter, not an assumption.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "No ANTHROPIC_API_KEY in the environment.\n"
            "Run with --probe to check the tool layer without a model.")
    try:
        import anthropic
    except ImportError:
        raise SystemExit("The anthropic package is not installed. pip install anthropic")

    client = anthropic.Anthropic()

    def ask(question, context):
        message = client.messages.create(
            model=name, max_tokens=1024,
            system=context["system"],
            messages=[{"role": "user", "content": question}])
        return "".join(block.text for block in message.content if block.type == "text")

    return ask


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true",
                        help="run tool probes only, no model required")
    parser.add_argument("--model", help="model id to evaluate, e.g. claude-opus-5")
    parser.add_argument("--case", help="run a single case by id")
    args = parser.parse_args()

    if not args.probe and not args.model:
        args.probe = True

    with open(CASES) as fh:
        cases = json.load(fh)
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            raise SystemExit(f"no case with id {args.case}")

    store = T.Store()
    ask = load_model(args.model) if args.model else None

    passed = failed = skipped = 0
    for case in cases:
        problems = []

        if case.get("tool_probe"):
            _, probe_problems = run_probe(store, case)
            problems += probe_problems
        elif args.probe and not ask:
            skipped += 1
            print(f"  skip  {case['id']}  (no tool probe; needs a model)")
            continue

        if ask:
            answer = ask(case["question"], {"system": build_context(store, case)})
            problems += run_answer(answer, case)

        if problems:
            failed += 1
            print(f"  FAIL  {case['id']}")
            for problem in problems:
                print(f"          {problem}")
        else:
            passed += 1
            print(f"  pass  {case['id']}")

    total = passed + failed
    print(f"\n{passed}/{total} cases passed" + (f", {skipped} skipped" if skipped else ""))
    if not ask:
        print("\nProbe mode only. Answer assertions need --model; they are what tell you\n"
              "whether a caveat that reached the context actually changed the answer.")
    return 1 if failed else 0


def build_context(store, case):
    """Compose the system prompt for a case.

    §5 defines a routing table mapping question type to the sections that apply.
    Sending all thirteen sections every turn costs tokens and lowers adherence,
    so the context is assembled from the sections this case actually routes to.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "public_foundry_system_prompt_v2.md")
    with open(path) as fh:
        text = fh.read()

    sections = {}
    for match in re.finditer(r"^# (\d+(?:\.\d+)?)\.?\s*([^\n]*)\n(.*?)(?=^# |\Z)",
                             text, re.M | re.S):
        sections[match.group(1)] = match.group(0)

    # Sections 1 through 5 govern every answer; the rest are routed in by §5.
    # Membership must be a set test: "12" is a substring of "12345" and a
    # containment check silently drops the topic section.
    always = ["1", "2", "3", "4", "5"]
    routed = [s for s in case.get("routes_to", []) if s not in set(always)]
    missing = [s for s in routed if s not in sections]
    if missing:
        raise SystemExit(f"{case['id']}: routes to sections not found in the prompt: {missing}")
    return "\n\n".join(sections[s] for s in always + routed if s in sections)


if __name__ == "__main__":
    sys.exit(main())
