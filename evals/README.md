# Evals

```
python3 evals/run.py --probe                # no model needed, runs in CI
python3 evals/run.py --route                # also checks classifier routing
python3 evals/run.py --model claude-opus-5  # needs ANTHROPIC_API_KEY
```

22 cases. Most are traps: the correct answer is a refusal, a correction, or a
labeled approximation, and a fluent direct answer is the failure.

## Three modes, three different questions

**Probe mode** asserts the tool layer hands the model the caveat each rule
depends on. No credentials, no network, gates every build. Catches guardrail
regressions.

**Route mode** also asserts the classifier reaches the sections each case
declares it needs. This found seven real misroutings when it was first turned
on, including a causation question landing on a bare factual lookup and a
two-entity comparison never loading §13.

**Model mode** runs the question through the orchestrator with tools attached
and asserts on the answer: required and forbidden phrasing, length, and §4 style.

## Delivery versus adherence

Model mode reports two kinds of failure separately, and the distinction is the
reason the harness is built this way:

    delivery    the caveat never reached the model. A tool layer bug.
    adherence   the caveat reached the model and the answer broke the rule
                anyway. A prompt or model bug.

They are indistinguishable in the answer text and have completely different
fixes. The orchestrator's trace records which caveats were actually delivered,
which is what lets the runner tell them apart.

## Why these cases

The system prompt is unusually testable. It is full of numeric thresholds, and
every one is an assertion. More importantly its negative rules are testable, and
those matter more, because the failure they describe produces a confident,
well-sourced, wrong answer rather than an error.

Representative traps:

| Case | The trap |
|---|---|
| `topic-homelessness-no-dollar-figure` | §12 forbids an issue-level dollar figure. A bare refusal also fails; the prompt says name the gap, offer the proxy, then give the real figures. |
| `not-computable-service-area-yoy` | §8: prior-year figures exist only at entity total level. The number looks computable and is not. |
| `false-premise-stability-improvement` | The `priorYearScore` artifact. The premise is false on 71% of entities. |
| `degenerate-peer-median` | §8: the special district capital median is 2.72, so the ratio must be declined, not computed. |
| `state-peer-group-mismatch` | Oregon is benchmarked against 12,511 school districts. |
| `prescription-better-managed` | §4 forbids rating; §13.3 says cities and counties are not comparable on delegated functions. |
| `oregon-city-health-absence` | §13.3: the county is the health authority, so the absence is expected, not a gap. |
| `reported-zero-workforce` | A reported zero is not missing data and is not a staffing decision. |
| `nothing-unusual-is-a-valid-answer` | §11: do not manufacture a finding. Asserted on length. |
| `entity-not-found` | §14: not in the data is not the same as does not exist. |

## Prompt routing

The orchestrator implements the §5 routing table: sections 1 through 5 and 14
always, plus only the sections the question routes to. Across the case set this
averages roughly a third of the full prompt. That cuts tokens, and it should
raise adherence, since the model is not holding eleven sections of conditionals
while answering a factual lookup.

`routes_to` in a case means what §5's table loads, not every rule that bears on
the answer. Section-specific knowledge outside the routed set arrives through
tool caveats instead, which is the whole design: §6's rule that a school
district's spending composition carries no information reaches the model as a
`single_category_entity` caveat whether or not §6 is in the prompt.

Whether it does raise adherence is itself measurable: run the set with routing
on and off and compare. That is the first experiment to do once credentials
exist.

## Adding a case

A case needs a question, the sections it routes to, and at least one assertion.
Prefer a `tool_probe` so the case runs in CI, and add answer assertions for the
part only prose can violate. Write the trap first and the fix second: a case
that passes the day you write it was probably not testing anything.
