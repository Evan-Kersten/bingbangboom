# The tool layer

Twelve typed tools over the store built by `etl/`, plus the §4 formatter.

```
python3 agent/test_tools.py     # 72 checks
```

## The envelope

Every tool returns the same shape:

```python
{
  "tool":           "compare_to_peers",
  "entity":         {...},
  "data":           {...},
  "caveats":        [{"code": ..., "rule": "§8", "guidance": "..."}],
  "not_computable": [{"code": ..., "rule": "§8", "reason": "..."}],
  "vintage":        {"finance": 2022, "workforce": 2023},
}
```

`caveats` is the reason this layer exists. A model asking for a peer comparison
cannot receive the median without also receiving the flag saying the median is
degenerate, because both arrive in the same object. The same query written as
free-form SQL returns the median alone, and the resulting answer looks grounded
while breaking §8.

`not_computable` is stronger: it names arithmetic the model must not attempt on
this result. `get_spending_breakdown` always carries `service_area_yoy`, because
§8 says prior-year figures exist only at entity total level and there is no
circumstance under which a per-service-area growth rate is legitimate.

## Tools

| Tool | Answers | Rules it enforces |
|---|---|---|
| `find_entity` | name to entity | §14 ambiguity, not-found vs not-existing |
| `get_entity_profile` | what kind of government, what data exists | §6 denominators, dependent entities |
| `get_financial_position` | is this government financially healthy | §8 components before label, names the driver |
| `get_spending_breakdown` | where the money goes | §8 partial breakdowns, §9 absence |
| `get_workforce` | how staffing is distributed | §8 listed functions only, reported zeros |
| `compare_to_peers` | how it compares | §8 degenerate medians, refuses the ratio |
| `compare_entities` | rank several | §12 like with like, names who is missing |
| `find_salient` | what stands out | §11 divergence over scale, allows "nothing" |
| `list_ecosystem` | which governments serve this place | §9 basis, no summing, partial geometry |
| `get_offices` | who governs | §7 seats vs roles, confidence, no holders |
| `map_topic_to_categories` | issue questions | §12 name the gap, offer the proxy |
| `run_sql` | anything else | read-only, logged, carries a standing caveat |

## `rules.py`

One catalogue of every caveat, keyed by code, each naming its section and
carrying the guidance text that goes into the model's context. A rule is written
once here rather than restated in each tool, so the tools cannot drift from each
other, and a rule cannot be quietly dropped.

It also holds the §12 topic concordance and the §11 attention thresholds as
data, so a threshold change is a one-line edit with a test beside it rather
than a prompt rewrite.

## `format.py`

§4 pins rounding: `$2.0B`, `$700M`, `$410K`, and thousands to the nearest ten
below a million. That is a formatter, not a prompt instruction, and it applies
identically whether a figure lands in prose, an axis tick or a table cell.

`check_style` returns §4 violations in generated prose: em and en dashes, the
banned word list, and unrounded figures. The eval harness asserts on it, so
style is measured rather than hoped for.

## `run_sql`

The escape hatch is deliberately unattractive. It is read-only, rejects stacked
statements, and every call is appended to `store.sql_log`. A query that shows up
repeatedly in that log is a missing typed tool, and the log is the backlog.
Results carry a standing caveat telling the model these rows arrived without
guardrails and that `entity_flags` should be checked before interpreting them.

## What is not here

No model call, and no chart or map rendering. The tools return data and
constraints; composing prose is the orchestrator's job and drawing is the render
layer's. Keeping the boundary sharp is what makes the tool layer testable
without credentials, which is why the whole suite runs in CI.
