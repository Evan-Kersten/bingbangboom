# The agent

Thirty-six typed tools over the store built by `etl/`, the §4 formatter, and the
orchestrator that routes a question and runs the tool loop.

```
python3 agent/test_tools.py         # 222 checks
python3 agent/test_orchestrator.py  # 44 checks, uses a scripted model
python3 agent/test_viz.py           # 88 checks on the render layer
python3 agent/test_reports.py       # 53 checks on report composition
python3 agent/test_blocks.py        # 45 checks on §15 block order and gating
python3 agent/gallery.py            # every chart form on one page, to look at
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
| `render_function_map` | where one function is done | §15.1 coverage gate, §12 layer that does the work |
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

## `orchestrator.py`

Classifies the question against the §5 routing table, composes the system prompt
from sections 1 to 5 and 14 plus only what the question routes to, then runs the
tool loop. The model client is a parameter, so the same orchestrator runs against
a frontier model and a self-hosted one and adherence can be compared directly.
That comparison is what should settle the hosting question, and it cannot be made
if the model is baked into the design.

§14 loads on every question. The prompt's preamble assigns sections 6 to 13 to
domain knowledge and leaves 14 unassigned, and its content is answer shape rather
than domain: unanswerable question, false premise, entity not found, ambiguous
name. Those arise on any question type, so routing it selectively means the shape
rules go missing exactly when an answer is going wrong.

**The trace matters as much as the answer.** Every run records which caveats were
delivered to the model. That separates two failures that look identical in the
output and have completely different fixes:

- the caveat never reached the context, which is a tool layer bug
- the caveat reached the context and the answer broke the rule anyway, which is
  a prompt or model bug

`_render` places caveats after the data and labels them as requirements rather
than context. A caveat inside a JSON blob reads as one more field; stated as an
instruction it reads as a constraint on the answer.

A tool call that fails returns an envelope explaining the failure rather than
raising, so a bad call lets the model correct itself instead of killing the turn.

## `viz.py` and `maps.py` — the render layer

The model picks a form and an entity. It supplies no title, no axis label, no
colour and no number: `render_chart` has exactly three parameters and a test
asserts it never grows a fourth. Everything visible is derived from the same
tool result that produced the prose, which keeps the chart and the sentence in
agreement and makes a caption physically unable to assert something §4 forbids.
A chart captioned "public safety investment and poverty" claims a relationship
more forcefully than a sentence does, and no prompt instruction reliably stops a
model writing it. Not having the parameter does.

**Charts carry the same refusals as the tools.** A bar drawn against a degenerate
peer median is as wrong as the sentence would be and worse in practice, because a
picture reads as measurement. When a chart must not be drawn it returns the
reason drawn in its place, since a blank space reads as a failure while a stated
reason reads as a rule.

Forms are bound to tool output shapes:

| Form | Shape | Why this form |
|---|---|---|
| `spending_composition` | horizontal bars, one hue | Nominal categories, so a value ramp would double-encode length as colour |
| `service_area_functions` | horizontal bars, median ticks | Level two of the drill-down; the tick is the median for that named function among peers reporting it, on the same operating-plus-capital basis as the bar |
| `peer_range` | quartile band, entity marker | The interquartile band is what stops a reader treating the median as a target |
| `finances_over_time` | two lines, one axis | Same unit, so never a second y-scale |
| `workforce_composition` | horizontal bars, one hue | Shares of listed functions only |
| choropleth | sequential bins | Counties and places only; see below |

Four more put several governments on one axis, through `render_comparison`:

| Form | Shape | Why this form |
|---|---|---|
| `per_capita_by_service_area` | bars, per resident | Absolute dollars rank a set by population, which the reader already knows |
| `per_capita_over_time` | lines, dashed peer baseline | Optionally rebased to 100, which shows growth and deliberately hides level |
| `service_area_across_entities` | bars, absolute | A snapshot and never a line: service-area spending is one year per entity |
| `entities_over_time` | lines, entity totals | The only measure that genuinely spans 2017 to 2023 |

**Fiscal stability is not a chart form.** It is a composite whose two components
move for opposite reasons, so a position on it tells a reader less than a
position on any single measure underneath it. `peer_range` on spending per
resident replaced it.

**Two limits travel with every per-resident series.** Population is a single
estimate per entity rather than a series, so the denominator is held constant and
what moves in the lines is spending. And no price index is in this data, so the
dashed baseline is the peer median and not inflation: nothing here is in real
terms, and a line above the baseline has outgrown its peers rather than prices.

Every chart returns a table twin alongside the SVG, so no value is reachable
only by hovering.

**Colour was computed, not chosen.** The palette is the reference instance from
the dataviz skill, run through its validator against both surfaces: the two
categorical slots pass every gate in light and dark, and the choropleth bins pass
the ordinal gates in both. Values are emitted as CSS custom properties with light
fallbacks, so a page defining the tokens gets dark mode and a standalone SVG
still renders.

**Maps are constrained by coverage, not by drawing.** Counties and places join
exactly, school districts by name at 156 of 223, and special districts have no
boundaries at all, so they are not offerable as a layer. No data gets its own
neutral well off the ramp, and the ramp's light end is a mid step, because a
near-white lightest bin is indistinguishable from an empty one and turns a
coverage gap into an apparent measurement.

The constraint is measured rather than asserted. Before drawing, `render_map`
counts how many boundaries in the extent carry a value and what share of the
money for that measure sits on the layer at all, and it refuses on three
grounds, tested strongest first:

| Refusal | What it means | Example |
|---|---|---|
| `mostly_absence` | under half the extent reports it, so the marks read as the pattern | fire protection, 83 of 378 places |
| `thin_extent` | fewer than four boundaries; a choropleth of three shapes is a chart of three numbers with a coastline | a place map scoped to a rural county |
| `needs_a_county` | a statewide place map is 378 specks whose areas are land area | sewerage across Oregon |

The order is the point. Fire protection is a place-layer function drawn without
a county, so the weak reason applies to it too, and reporting that one would
send a reader to scope a map that is empty for a reason nobody stated.

`render_function_map` is the drill one level below a service area. It picks the
layer holding most of the function rather than taking one from the caller, so
police lands on counties, sewerage on places and elementary education on school
districts, and a city's sewers are never drawn against counties that run none.
Where no layer survives the gate the refusal comes from the layer holding the
work, because that is the one whose reason names the governments doing it. The
share of the money the drawn layer holds travels on every result and is stated
in the answer: a county police map is 35% of what Oregon's local governments
report on policing, and saying so is the difference between a map of county
policing and a claim about policing.

`gallery.py` renders every form to one page. The validator checks colour; it does
not catch a label collision, a legend sitting on an axis, or a map drawing over
its own legend. Those were all real defects here, found by rendering the page and
looking at it, which is the only way they are ever found.
