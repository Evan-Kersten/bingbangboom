# Evals

```
python3 evals/run.py --probe              # no model needed, runs in CI
python3 evals/run.py --model claude-opus-5  # needs ANTHROPIC_API_KEY
```

22 cases. Most are traps: the correct answer is a refusal, a correction, or a
labeled approximation, and a fluent direct answer is the failure.

## Two modes, two different questions

**Probe mode** asserts that the tool layer actually hands the model the caveat
each rule depends on. It needs no credentials and no network, so it gates every
build. This is what catches a guardrail regression.

**Model mode** puts the question to a model and asserts on the answer: required
and forbidden phrasing, length, and §4 style. This is what tells you whether a
prompt change helped.

Neither replaces the other, and the gap between them is the interesting signal.
A caveat that reaches the context and is then ignored passes the probe and fails
the model run. That is precisely the failure mode worth being able to see, and
it is invisible if you only test one way.

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

`build_context` implements the §5 routing table: sections 1 through 5 always,
plus only the sections a case routes to. Across the case set this averages 28%
of the full prompt, so most turns carry roughly a third of the text. That cuts
tokens, and it should raise adherence, since the model is not holding eleven
sections of conditionals while answering a factual lookup.

Whether it does raise adherence is itself measurable: run the set with routing
on and off and compare. That is the first experiment to do once credentials
exist.

## Adding a case

A case needs a question, the sections it routes to, and at least one assertion.
Prefer a `tool_probe` so the case runs in CI, and add answer assertions for the
part only prose can violate. Write the trap first and the fix second: a case
that passes the day you write it was probably not testing anything.
