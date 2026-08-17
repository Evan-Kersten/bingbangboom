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
python3 etl/verify.py         # 58 assertions about the built store
python3 app/server.py         # http://localhost:8000
python3 app/export_static.py --out site --clean   # ~60s, ~385 MB
```

`build/` and `site/` are gitignored and reproducible; never commit either.

The full suite, all of which must pass before a push:

```
python3 agent/test_tools.py        # 289    python3 app/test_server.py    # 179
python3 agent/test_orchestrator.py #  44    python3 app/test_parity.py    # 461
python3 agent/test_viz.py          #  88    python3 app/test_tables.py    #  28
python3 agent/test_reports.py      #  53    python3 etl/verify.py         #  58
python3 agent/test_blocks.py       #  45    python3 etl/test_project.py   #   6
                                            python3 evals/run.py          #  22
```

CI runs exactly these eleven. `evals/run.py --route` is the CI form: without a
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
reader can pick any set of governments without a round trip, and
`app/subjects.js` resolves a typed subject there because a pre-rendered build
has no server to ask which brief to open. `app/test_parity.py` runs both through
node and compares refusal state, answer sentence, caveat codes, table rows,
**every SVG text node**, and which subject every alias resolves to. Change one
side, change the other.

**No API key may ever reach the static site.** The exported build is public.
Free text needs a server; that is not a limitation to engineer around.

**One definition per list.** The question list, the set of entity-independent
answers, the availability manifest and the subject vocabulary each have exactly
one definition (`server.preset_shell()`, `server.ENTITY_INDEPENDENT`,
`tools.availability()`, `brief.topic_index()`) and are sent to the page at
runtime. The page used to keep its own copy of the question list and it
drifted: a question shipped and the exported interface never offered it. Do not
reintroduce a second copy. `subjects.js` duplicates the matching *algorithm*,
under a parity test, and never the vocabulary.

**A subject that does not resolve routes around every refusal here.** The §12
concordance is keyed by seventeen words and nobody arrives with them: a scope of
work says unsheltered, or broadband, or deferred maintenance.
`rules.TOPIC_ALIASES` maps those onto the seventeen and `rules.resolve_topic` is
the only resolver. This is not search polish. An unresolved subject never
reaches the §12 gap sentence, so the reader sees an empty pane, reads it as
"this data has no view on my subject", and leaves for a source that will give
them a number with no gap attached. Adding a subject to the concordance without
adding the words people use for it is the same bug as not adding it at all.

**A preset must never be offered without its data.** §15.1. The availability
manifest gates them, and `blocks.next_questions` gates the follow-up chips
against the same dict.

**A column that says the same thing in every row is a sentence.** `app/tables.js`
draws every table block in the thread and lifts a constant column into a line
above the table. Nine governments each carrying "nothing, which usually means
another government holds this here" is one fact rendered nine times, and it
buries the two rows that do carry a figure. The lift is measured and stops the
moment one row differs, so a value is moved and never dropped, and the last
column standing is always kept.

**It takes two rows to be constant.** One row is a record, and every column of
it is trivially the same in every row. Lifting on that turned Estacada's single
silent government into a caption holding most of the record above a table
holding the rest, so `layout` returns early below two rows. Places with one
silent body are common, not an edge case.

`agent/reports.py` deliberately does not use these rules: the full report is a
document with its own stylesheet, and this is interface behaviour.

**Absence is not zero.** §9. An entity reporting nothing in a category is
usually evidence that another government holds that responsibility. A blank must
never render as `$0`, and a reported zero must be said to be a reported zero.

**An issue question opens on the gap, never on a figure.** §12. `brief.py` is
the front door and it exists to make the §12 sequence structural: the gap
between the topic and the Census categories, then the proxy and how loosely it
fits, then figures, in that order, because §5 puts scope limits before
everything. `answer_brief` asserts the order in a test rather than trusting the
prose, and no dollar figure appears in the opening sentence at all. The
concordance in `rules.TOPIC_CONCORDANCE` is the one definition of the subject
list and is sent to the page at runtime, like the preset list.

The brief never totals across the governments serving a place. §9: the same
dollar appears in two entities' expenditures, so the sum has no referent. The
guarantee is that the key is never computed, because a renderer cannot print
what does not exist.

**A map is drawn by measurement, not by request.** §15.1. `render_map` measures
two things before drawing: how many boundaries in the extent carry a value, and
what share of the money for that measure sits on the layer at all. Below
`THRESHOLDS["map_density_minimum"]` it refuses and draws the reason in the frame.
`blocks.MAP_COVERAGE_FLOOR` is that same threshold, not a second copy of it.

This is what makes the drill from service area to function safe. Public Safety
maps cleanly across the counties; one level down, police protection draws at 34
of 36 counties and fire protection draws at one, because fire is district work
and 243 of those districts have no boundary anywhere in this data. Same code,
same measure, and without the gate the second one is a picture saying rural
Oregon spends nothing on fire. `render_function_map` picks the layer that holds
the work, so a municipal function is not drawn against counties that do not do
it, and a statewide `place` map is refused outright: 378 cities at that scale
are specks whose areas are land area.

The three refusals are ranked and the order matters. Mostly-absence beats a thin
extent beats needing a county, because the weakest reason reads as the most
fixable, and fire protection reported as "scope it to a county" sends a reader
to a map that will be just as empty for a reason nobody stated.

**A layer is vetted before it is committed to.** `agent/atlas.py` is the
prototyping surface: every mappable measure in one registry, each drawn only
through the coverage gate, each result saying whether it was drawn or refused
and over how much of the extent. The registry is derived from `tools.MAP_METRICS`
and `community.INDICATORS` rather than restating them, so a metric added there is
reachable here without a second edit.

`atlas.compare` will not pair a fiscal measure with a survey one. §4: spending
and conditions are aggregate patterns observed in the same geography with nothing
joining them, and two maps side by side is the most persuasive way to assert that
one explains the other without writing a sentence anybody could argue with. The
refusal is by measure kind rather than by a warning, because a warning is advice.

`atlas.catalogue` is the artifact a discovery phase actually wants: every measure
on a layer with its verdict and its coverage, so a decision to build a layer is
made against measurement rather than against whichever example the demo used.

**Each entity is shown at its own latest year, and the year rides on the row.**
Every single-year table already holds exactly one year per entity, whichever the
source last reported, so this is the data's shape rather than a choice the ETL
makes. Portland's spending is 2023 and its workforce 2024, and both appear in
one answer. A comparison across entities is therefore a comparison across years,
which is acceptable because each figure states its own.

Disclosing the entity's year is necessary and not sufficient. **The peer median
beside it has no single year**: the pool is whichever year each peer last
reported, so a median blends two or three vintages. `peer_pool_mixed_vintage`
says so wherever a peer statistic is placed next to an entity figure. Discarding
the pool to get one vintage would throw away most of it, so the comparison
stands and is labelled for what it is.

**The one join between money and people.** Finance is filed by financial
function and workforce by employment function, and `function_bridge` is the only
crosswalk between them. It is **many to many in both directions**: Fire
Protection money covers firefighters and other fire staff, and six public
welfare financial functions land on one employment function. `cost_per_head`
sums both sides before dividing, and a row-to-row join is wrong in a way that
looks plausible whichever direction it errs.

The crosswalk names financial functions by an id whose table, `financial_functions.json`,
arrived in the repository after the crosswalk itself. Resolving it against
`government_functions.json` — a different list of a similar shape — silently
produced pairs like housing and community development against firefighters. The
ETL reads both files so that cannot be repeated by hand.

`cost_per_head` refuses where the function is absent from the entity's listed
employment functions. §8: the source reports top functions only, so a missing
headcount is a truncated list rather than a government doing the work with
nobody. That refusal fires for 165 of the 327 entities reporting fire
protection, and dividing by what happens to be listed would flatter every one
of them.

**Coverage and recency trade against each other, by design.** The Census of
Governments takes a full census in years ending 2 and 7 and samples in between.
2022 reaches 978 entities at function level, 2023 reaches 400 and skews to the
largest: mean population around 24,000 against 4,100. A statewide picture wants
a census year; the latest year is a picture of big governments and has to say
so. `etl/verify.py` asserts both halves of that, so a refresh that quietly
inverted it fails the suite.

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

**Function data is one year per entity, and not the same year.** 978 entities
report their functions at 2022, 400 at 2023, 45 at 2017. A statewide function
map is therefore several vintages at once, and neighbouring boundaries can be
years apart. `map_years_differ` says so on every map drawn from
`financial_functions` or `spending_by_service_area`, both of which have this
shape. It is a level, never a moment.

**There is no vendor-addressable figure any more.** The source nests the
function rows inside a procurement-opportunity object that also carries a
derived "purchasable" remainder: operating and capital less personnel less
amounts held to be unpurchasable. The ETL takes only the reported columns. One
basis, operating plus capital, at every level of the drill, which is also the
basis the service-area shares are computed against. If a future refresh
reintroduces those columns, leaving them unread is deliberate.

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
agent/        rules.py (the caveat catalogue), tools.py (41 tools), explore.py,
              atlas.py (every mappable measure, and whether it should be),
              brief.py (place + issue, the §12 front door),
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
  inferred; elsewhere a city resolves to its county and its school districts,
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
