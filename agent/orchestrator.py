"""The orchestrator.

Takes a question, routes it to the sections of the system prompt that apply,
runs the tool loop, and returns the answer with a trace of what happened.

Model-agnostic on purpose. The client is a parameter, so the same orchestrator
runs against a frontier model and a self-hosted one and the rule adherence can
be compared directly. That comparison is the thing that should settle the
hosting question, and it cannot be made if the model is baked into the design.

The trace matters as much as the answer. It records which caveats were actually
delivered to the model, which is what separates "the guardrail never reached the
context" from "the guardrail reached the context and was ignored". Those are
different bugs with different fixes, and without the trace they look identical.
"""

import os
import re

import schemas
import tools as T

PROMPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "public_foundry_system_prompt_v2.md")

# §5 routing table, verbatim. Sections 1 through 5 govern every answer and are
# added to whatever a question routes to.
#
# §14 is included too. The prompt's preamble assigns sections 6 through 13 to
# domain knowledge and leaves 14 unassigned, and its content is answer shape,
# not domain: unanswerable question, false premise, entity not found, ambiguous
# name, follow-up. Those can arise on any question type, so loading it only for
# some means the shape rules are missing exactly when an answer goes wrong.
ALWAYS = ["1", "2", "3", "4", "5", "14", "15"]

ROUTING = {
    "factual_lookup":          ["8"],
    "governance":              ["6", "7"],
    "financial_interpretation": ["8", "13"],
    "what_stands_out":         ["8", "11"],
    "place_or_cross_entity":   ["6", "9", "13"],
    "issue_or_topic":          ["12", "9"],
    "investment_vs_conditions": ["9", "10", "13"],
}

# Ordered most specific first: a question about an issue in a place is an issue
# question, and routing it as a place question loses §12.
CLASSIFIER_SIGNALS = [
    ("issue_or_topic", r"\b(homeless|wildfire|mental health|climate|opioid|addiction|"
                       r"affordab|environment|equity|drought|broadband)\w*\b"),
    # "Does spending cause better outcomes" is an investment-versus-conditions
    # question, not a lookup. §4 and §10 both have to be in play to answer it.
    ("investment_vs_conditions", r"\b(poverty|unemploy|income|uninsured|conditions|"
                                 r"residents face|outcomes?|cause[sd]?|lead to|result in|"
                                 r"compared to (its |the )?(need|population))\b"),
    ("governance", r"\b(who governs|governing body|board|council|commission|mayor|elected|"
                   r"appointed|seats?|term|position \d|office)\b"),
    # §9 says to widen the frame when an expected service area is missing, so a
    # question about an absence is an ecosystem question: another entity almost
    # always holds the responsibility.
    # The comparison signals are deliberately narrow. "compare to its peers" is a
    # single-entity benchmark and must stay a lookup; "which is better, X or Y"
    # and "which counties" are cross-entity and pull in §9 and §13.
    ("place_or_cross_entity", r"\b(which governments|who else|serve this place|in \w+ county|"
                              r"across|overlap|ecosystem|responsible for|"
                              r"spends? (nothing|little|no money)|no spending on|"
                              r"why (does|do|is|are)\b[^?]*\bn[o']t?\b|"
                              r"which (is|one|of|counties|cities|districts|governments|entities)|"
                              r"better (managed|run)|versus|\bvs\.?\s)"),
    ("what_stands_out", r"\b(stands? out|unusual|anomal|notable|different from|surpris)\w*\b"),
    ("financial_interpretation", r"\b(healthy|health of|stability|debt|solven|risk|"
                                 r"financial position|fiscally|revenue|volatil)\w*\b"),
]


def classify(question):
    """Assign a §5 question type. Deterministic, so it is testable offline.

    A model can classify better and should be given the chance in deployment,
    but a heuristic that runs without credentials keeps the eval harness honest
    about what it is measuring.
    """
    lowered = question.lower()
    for question_type, pattern in CLASSIFIER_SIGNALS:
        if re.search(pattern, lowered):
            return question_type
    return "factual_lookup"


def load_sections(path=PROMPT_PATH):
    with open(path) as fh:
        text = fh.read()
    return {m.group(1): m.group(0) for m in
            re.finditer(r"^# (\d+(?:\.\d+)?)\.?\s*([^\n]*)\n(.*?)(?=^# |\Z)", text, re.M | re.S)}


def compose_prompt(question_type, sections=None):
    """§1 through §5 plus only the sections this question type routes to."""
    sections = sections if sections is not None else load_sections()
    # Membership must be a set test: "12" is a substring of "12345", and a
    # containment check silently drops the topic section.
    routed = [s for s in ROUTING.get(question_type, []) if s not in set(ALWAYS)]
    wanted = ALWAYS + routed
    missing = [s for s in wanted if s not in sections]
    if missing:
        raise ValueError(f"routing wants sections absent from the prompt: {missing}")
    return "\n\n".join(sections[s] for s in wanted)


class Orchestrator:
    def __init__(self, store=None, client=None, max_turns=8, sections=None):
        self.store = store or T.Store()
        self.client = client
        self.max_turns = max_turns
        self.sections = sections if sections is not None else load_sections()

    def dispatch(self, name, arguments):
        """Run one tool call. A bad call returns an envelope, never an exception,
        so the model can correct itself instead of the turn dying."""
        tool = T.TOOLS.get(name)
        if tool is None:
            return T.envelope(name, caveats=[{
                "code": "unknown_tool", "rule": "§4",
                "guidance": f"No tool named '{name}'. Available: {', '.join(sorted(T.TOOLS))}."}])
        try:
            return tool(self.store, **(arguments or {}))
        except TypeError as error:
            return T.envelope(name, caveats=[{
                "code": "bad_arguments", "rule": "§4",
                "guidance": f"{name} rejected those arguments: {error}"}])
        except Exception as error:  # a tool bug must not take the answer down
            return T.envelope(name, caveats=[{
                "code": "tool_error", "rule": "§4",
                "guidance": f"{name} failed: {error}"}])

    def answer(self, question):
        """Route, run the tool loop, return the answer and the trace."""
        if self.client is None:
            raise RuntimeError("no model client; construct with client=...")

        question_type = classify(question)
        system = compose_prompt(question_type, self.sections)
        trace = {
            "question": question,
            "question_type": question_type,
            "sections": ALWAYS + ROUTING.get(question_type, []),
            "prompt_chars": len(system),
            "calls": [],
            "caveats_delivered": [],
            "not_computable_delivered": [],
            "turns": 0,
            "hit_turn_limit": False,
        }

        messages = [{"role": "user", "content": question}]

        for turn in range(self.max_turns):
            trace["turns"] = turn + 1
            reply = self.client(system=system, messages=messages,
                                tools=schemas.for_question_type(question_type))

            calls = [block for block in reply["content"] if block.get("type") == "tool_use"]
            if not calls:
                text = "".join(block.get("text", "") for block in reply["content"]
                               if block.get("type") == "text")
                trace["answer"] = text
                return text, trace

            messages.append({"role": "assistant", "content": reply["content"]})
            results = []
            for call in calls:
                result = self.dispatch(call["name"], call.get("input"))
                trace["calls"].append({"tool": call["name"], "input": call.get("input")})
                trace["caveats_delivered"] += [c["code"] for c in result["caveats"]]
                trace["not_computable_delivered"] += [c["code"] for c in result["not_computable"]]
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": _render(result),
                })
            messages.append({"role": "user", "content": results})

        trace["hit_turn_limit"] = True
        trace["answer"] = ""
        return "", trace


def _render(result):
    """Serialize an envelope for the model.

    Caveats are placed after the data and labelled as requirements rather than
    context. A caveat buried inside a JSON blob reads as one more field; stated
    as an instruction it reads as a constraint on the answer.
    """
    import json

    parts = [json.dumps({"data": result["data"], "entity": result["entity"],
                         "vintage": result["vintage"]}, default=str)]

    if result["caveats"]:
        parts.append("\nYour answer MUST reflect each of these:")
        for caveat in result["caveats"]:
            parts.append(f"  [{caveat['rule']} {caveat['code']}] {caveat['guidance']}")

    if result["not_computable"]:
        parts.append("\nYou MUST NOT attempt, estimate or imply any of these:")
        for blocked in result["not_computable"]:
            parts.append(f"  [{blocked['rule']} {blocked['code']}] {blocked['reason']}")

    return "\n".join(parts)


def anthropic_client(model):
    """Adapter for the Anthropic API, matching the client contract above."""
    import anthropic

    sdk = anthropic.Anthropic()

    def call(system, messages, tools):
        reply = sdk.messages.create(model=model, max_tokens=1500, system=system,
                                    messages=messages, tools=tools)
        return {"content": [block.model_dump() for block in reply.content],
                "stop_reason": reply.stop_reason}

    return call
