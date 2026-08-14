# Working on this repository

A chatbot over Oregon's 1,529 local governments. Read this before changing
anything; it is the part that is not derivable from reading the code.

## What the project is

`public_foundry_system_prompt_v2.md` is the specification. It has fifteen
sections and an Appendix A, and it is not a style guide — it is a statement of
which computations are meaningful on this data and which are forbidden. Sections
are cited throughout the code as `§4`, `§8`, `§13.11` and so on. When a comment
says §9, go read §9.

The architecture answers one question: how do you stop a model producing a
fluent answer that breaks the specification? The answer here is that **a figure
and the reason it cannot be trusted arrive in the same object**. Every tool
returns an envelope:

```python
{"tool": ..., "entity": ..., "data": ..., "caveats": [...],
 "not_computable": [...], "vintage": {...}}
```

A model asking for a peer comparison cannot receive the median without also
receiving the flag saying the median is degenerate. The same query written as
free-form SQL returns the median alone, and the answer built on it looks
grounded while breaking §8. That is the whole design. Preserve it.

`not_computable` is stronger than a caveat: it names arithmetic that must not be
attempted on that result at all.

## Getting to a working state

No dependencies. Standard library only, so there is nothing to install.

```
python3 etl/build.py          # ~20s, writes build/ from the files in the root
python3 etl/verify.py         # 52 assertions about the built store
python3 app/server.py         # http://localhost:8000
python3 app/export_static.py --out site --clean   # ~50s, ~256 MB
```

`build/` and `site/` are gitignored and reproducible; never commit either.

The full suite, all of which must pass before a push:

```
python3 agent/test_tools.py        # 201    python3 app/test_server.py    # 168
python3 agent/test_orchestrator.py #  44    python3 app/test_parity.py    # 452
python3 agent/test_viz.py          #  87    python3 evals/run.py          #  22
python3 agent/test_reports.py      #  53    python3 etl/verify.py         #  52
python3 agent/test_blocks.py       #  45    python3 etl/test_project.py   #   6
```

CI runs exactly these ten. `evals/run.py --route` is the CI form: without a
model it checks that each trap question routes to the right tools, which is
what can be asserted deterministically. The answer assertions need `--model`
and an API key, and they are what tell you whether a caveat that reached the
context actually changed the answer — worth running by hand before a release.

`anthropic` is the only third-party import in the tree, in
`agent/orchestrator.py` alone. Without it — and without `ANTHROPIC_API_KEY` —
the app runs in **grounded mode**: every preset answers, free text does not.
That is the mode the deployed site runs in.

## Invariants that break silently

These have each been broken at least once and cost real time to find.

**Python and JavaScript must render identically.** `agent/` renders answers for
the server; `app/comparison.js` re-renders comparisons in the browser so a
reader can pick any set of governments without a round trip. `app/test_parity.py`
runs both through node and compares refusal state, answer sentence, caveat
codes, table rows and **every SVG text node**. Change one side, change the other.

**No API key may ever reach the static site.** The exported build is public.
Free text needs a server; that is not a limitation to engineer around.

**One definition per list.** The question list, the set of entity-independent
answers and the availability manifest each have exactly one definition
(`server.preset_shell()`, `server.ENTITY_INDEPENDENT`, `tools.availability()`)
and are sent to the page at runtime. The page used to keep its own copy of the
question list and it drifted: a question shipped and the exported interface
never offered it. Do not reintroduce a second copy.

**A preset must never be offered without its data.** §15.1. The availability
manifest gates them, and `blocks.next_questions` gates the follow-up chips
against the same dict.

**Absence is not zero.** §9. An entity reporting nothing in a category is
usually evidence that another government holds that responsibility. A blank must
never render as `$0`, and a reported zero must be said to be a reported zero.

## Data traps

**There are two different expenditure totals and they disagree** for 1,112 of
the 1,480 entities that report both. `financial_trends.expenditure` is the
reported total; `service_area_totals.total_expenditure` is operating plus
capital, which is what the service-area shares are drawn against. Corvallis in
2023 is $97.3M reported against $118.2M operating-plus-capital — the reported
total is the *smaller* of the two here, which is the opposite of what most
people assume when they first see the pair. `etl/verify.py` asserts they keep
disagreeing, so a "fix" that reconciles them fails the suite. They are labelled
differently everywhere and must stay that way.

**The denominator is not always residents.** School districts report enrollment
in the same `population` field; special districts have no denominator at all and
carry total spending instead. `rules.denominator()` is the only correct source.
"48,601 population" for a school district is students, and a reader comparing it
against a city's residents is comparing two different things.

**Host-county filing under-captures.** §13.11: a district crossing county lines
is filed under the county holding most of its assessed value. A host-county list
therefore over-collects districts serving elsewhere and misses districts serving
here from a neighbouring filing. `service_extent` holds measured extent, which
outranks filing.

**The trend data is two panels, not one.** 1,006 entities have only 2017 and
2022; 313 have all seven years. A chart mixing them without marking which is
which is a lie about coverage.

**DP03 is a survey, not a count.** Five-year estimates in the dollars of their
own vintage, with overlapping windows. Two estimates are separated only if the
difference exceeds the root sum of squares of their margins — `math.hypot`, the
Census significance test. Adding the margins looks safer and is about 40% too
strict; it hid a real decline in Portland's poverty rate. There are **no
tract-level DP03 rows** in any of the three CSVs (county and place only), so the
tract shapefile in the root cannot be coloured however fine it is.

**Community conditions are never a government's record.** §4. They are drawn on
their own colour ramp (`maps.SURVEY_BINS`) for exactly this reason, and must
never share an axis or a legend with a spending figure.

## Where things are

```
public_foundry_system_prompt_v2.md   the specification
etl/          build.py, verify.py, schema.sql, plus the shapefile and
              dissolve work. Reads the loose files in the repository root.
agent/        rules.py (the caveat catalogue), tools.py (35 tools), explore.py,
              community.py (DP03, structurally unable to see a fiscal figure),
              viz.py + maps.py (hand-authored SVG), blocks.py (§15 block order),
              reports.py, orchestrator.py, schemas.py (what the model sees)
app/          server.py (21 presets), index.html, comparison.js, export_static.py
evals/        trap questions, the ones a fluent model gets wrong
```

Each directory has its own README with more detail.

`.github/workflows/pages.yml` runs the ten suites and a real render on every
pull request and on every push to the default branch, and publishes to Pages
from the default branch only. Doc-only changes are skipped via `paths-ignore`,
so a README push does not spend a deploy.

## Known unfinished

- `serves_place` is thin outside Multnomah. That county's precinct file is the
  only place "which governments serve this address" is recorded rather than
  inferred; elsewhere a town resolves to its county and its school districts,
  against the roughly ten governments a real address sits inside. Boundary data
  for special districts is what would close this, and nothing else will.
- `cnty_voterprecincts/` (Clackamas, 121 precincts with REPDIST/SENDIST/CONGDIST)
  is unused and would extend legislative district maps to a second county. Its
  projection is `NAD_1983_HARN_StatePlane_Oregon_North_FIPS_3601` **without**
  `_Feet_Intl` — metres, so `etl/project.inverse()` needs `unit=1.0`. Passing
  the default silently lands everything in the wrong hemisphere of the state.
- `cb_2018_41_tract_500k/` is 828 tract polygons with no estimates to colour
  them, and no GeoJSON layer is built from it for that reason.
- The office crosswalk carries confidence scores; low-confidence matches are
  disclosed rather than hidden, and §7 forbids naming holders in any case.

## House style

Comments explain *why*, especially why an obvious simpler thing is wrong — the
bug it caused is the most valuable line in the file. Match the surrounding
density. Refusals are features here: a tool that declines to compute something
and says why in the reader's terms is working correctly, and a change that makes
a refusal quietly succeed is a regression even when the tests pass.
