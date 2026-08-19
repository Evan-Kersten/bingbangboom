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
python3 etl/verify.py         # 69 assertions about the built store
python3 app/server.py         # http://localhost:8000
python3 app/export_static.py --out site --clean   # ~160s, ~496 MB
```

`build/` and `site/` are gitignored and reproducible; never commit either.

The full suite, all of which must pass before a push:

```
python3 agent/test_tools.py        # 328    python3 app/test_server.py    # 209
python3 agent/test_orchestrator.py #  44    python3 app/test_parity.py    # 461
python3 agent/test_viz.py          # 107    python3 app/test_tables.py    #  30
python3 agent/test_reports.py      #  54    python3 etl/verify.py         #  69
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

**Most rules are written for the writer, not the reader.** The catalogue in
`rules.py` is imperative because a model is being told what to do: name the
driver before the label, decline the distance calculation, never divide one row
by one row. Rendering all of them is what produced a disclosure headed "11 rules
bind this answer" in which the two that would have changed a conclusion were
buried among nine that could not.

`rules.READER_NOTES` is the subset that reaches a reader: the ones that are a
*fact about the data* and change what somebody concludes or does next, phrased
declaratively. `blocks.notes` renders those, capped, minus any the answer
already states in prose. Everything else still reaches the model in full and
still travels on the response under `rules`, because separating "the caveat
never reached the answer" from "the caveat reached it and the answer broke the
rule anyway" needs the whole record. A code absent from `READER_NOTES` is
model-only, which is the safe default for the hundred-odd codes tools raise
inline.

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

**A subject must land on a category that exists.** `infrastructure` pointed at
`Capital expenditure` — the capital side of the operating split, not a service
area — so it joined to nothing. Every government in every stack reported nothing
on it, the county map refused for want of a category that was never in the
table, and the brief read as a finding about Oregon rather than as a typo.
Nothing failed. `etl/verify.py` now asserts every entry in `TOPIC_CONCORDANCE`
names a real service area *and* that at least fifty governments report in it,
because a subject that resolves to categories nobody files is the same failure
one step later. The capital question is real and belongs to `capital_split`,
which asks whether a government is building rather than what it is building.

**A figure with no benchmark is a figure a reader sizes from somewhere else.**
This is the thing every comparable product supplies and this one did not, and it
is not decoration: 19% below the poverty level, 16% of a county budget, $302M on
electric power are each unreadable alone and each decisive next to the right
comparison. Three benchmarks now ride through the brief — `_typical_share` for a
government against the median for its type, the peer median the drill already
carried for a function against governments reporting the same job, and
`community.against` for the place's ACS estimates against the county's.

The last one carries the discipline the others do not need. Two ACS estimates
are separated only when the difference exceeds the root sum of squares of their
margins, and where it does not the difference is **not printed at all**: the
column says "too close to call" rather than naming a gap the survey cannot see.
A small number still reads as a direction, which is why the refusal is a phrase
and not a rounded figure.

**A slot in `BLOCK_ORDER` may hold several kinds.** §15.2 gives most question
types one chart and one table, where grouping by kind and authored order are the
same thing. A brief is a document with a funnel in it — the state, the stack,
one government, the jobs inside it, whether that money builds or runs — and each
step is a picture with its table under it. Grouping by kind collected four
charts at the top and four tables beneath: the same blocks and none of the
argument. A tuple in the order is one slot whose kinds interleave freely, and
`validate` maps every kind in a tuple to that slot's position.

The §15.1 rule about a table past the row threshold leading over a chart is
scoped to one chart against one table for the same reason. Unscoped it paired
every chart with every table, and reported a three-government stack chart as the
wrong lead for a nine-row table of districts filed in another county.

**A block kind with no renderer is composed, ordered, validated and dropped.**
`text` had none. Presets never noticed because they join their sentences into
the single §15.1 `answer` block before composing; the brief emits section leads
and every one of them vanished on the way to the page. When adding a kind on the
server, add it to `BLOCK` in `index.html` in the same change.

**An issue question opens on the gap, never on a figure.** §12. `brief.py` is
the front door and it exists to make the §12 sequence structural: the gap
between the topic and the Census categories, then the proxy and how loosely it
fits, then figures, in that order, because §5 puts scope limits before
everything. `answer_brief` asserts the order in a test rather than trusting the
prose, and no dollar figure appears in the opening sentence at all.

Above that sequence sits one line, and it is not part of it. `brief.narrative`
returns a claim, a finding and a texture; the claim carries no money and no
share, asserts nothing measured, and says *who* — which is the §9 material this
data supports. It is rendered as `answer` with `lead: true` and set at display
size. What §5 protects is that no reader carries away a number without its
scope, and the gap sentence still lands before the first figure anywhere in the
answer; what it cannot protect against is four thousand briefs opening on the
same disclaimer, which taught readers to start halfway down. The tests pin both
halves: no `$` and no `%` in the lead, and `Census categories` before the first
`%` in the joined answer.

The claim is ranked by how much the finding changes what somebody does next.
The commonest shape by far, at about a third of briefs, is that more bodies
report from outside the stack than on it — because 1,029 of Oregon's 1,529
governments are districts filed under a county with no boundary — and that one
names them: "6 fire districts file Public Safety spending near Estacada." Named
by the Census category they file under and never by the topic, because "six fire
districts do wildfire" is the §12 slide from category to subject and the
headline sits *above* the sentence that draws the distinction. `_dominant`
groups on the kind and the category together rather than taking a majority of
each independently: Adrian's eight off-stack bodies are six irrigation districts
filing Environment and one fire district filing Public Safety, and the
independent majorities produced "eight irrigation districts file Public Safety
spending", which seven of its eight subjects contradict.

A 2× lead over the rest of a stack is not the same as a presence.
`LEAD_SHARE_FLOOR` is a floor on the leader's own budget, because Deschutes
County at 5.9% on housing clears the same ratio test as Multnomah at 47% on
homelessness, and calling the first a government built around housing is false
in a way a reader cannot check. `_texture` returns `None` where nothing about a
place is unusual, and the brief is three sentences shorter: §3 forbids padding
an answer to seem thorough. §13.3's delegation note fires on Health & Human
Services and Public Safety only — attached to a county's broadband share it was
a true sentence about the wrong number. The
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

**Only a county is shaded for a government.** `atlas.FISCAL_LAYERS` is
`("county", "government")` and nothing else: a county genuinely is the ground it
covers and all 36 join exactly, so it keeps the choropleth. Everything else is a
body with an office. Shading 378 city polygons compared land areas — every
statewide place map refused, correctly — and school districts joined by name at
156 of 223, so a third of them were simply missing from their own layer. Both
became pins. `place` survives in `SURVEY_LAYERS` alone, because a poverty rate
was measured over that ground and the polygon is the right shape for it; a
fiscal measure asked for it is refused by `atlas.draw` and shown as "not on this
layer" in the catalogue, which is the account somebody deciding whether to build
a city layer actually wants. `atlas.layers()` is the one definition of what the
picker offers and is sent to the page at runtime.

The consequence reaches the answers, not just the lab. The ecosystem preset and
the county report drew a place choropleth directly under a paragraph counting
how few of a county's governments carry a boundary — illustrating that sentence
with a picture of the handful it excludes. Multnomah shaded six cities; the same
county draws 41 of its 44 governments as pins. Total expenditure there rather
than per resident, because a district has no residents and the per-head measure
silently narrows the map back to the cities.

`render_point_map` returns `highlight_placed`. Drawn is not enough when a caller
asked to mark one government: the map can clear its coverage gate on the
strength of a county's other bodies while this one has an address in an
unincorporated community with no polygon, and a highlight that is not there
reads as a government spending nothing. The `scale` preset falls back to the
locator on `highlight_placed is False`.

**The capital split is three pieces, not two.** `tools.capital_split` returns
operating, capital and whatever the source files under neither. The first two
reach the reported total for 1,251 of 1,529 governments and fall short for the
other 278; Oregon's own filing accounts for 79.8% of its total this way. Folding
the shortfall into operating is the convenient move and it inflates the
denominator every capital share on the page is drawn against, so it is a named
third segment with `split_does_not_reach_the_total` beside it. The source does
not say what it is — debt service and transfers are the usual candidates and
this data cannot separate them — so it is named and left unexplained. There is
one year of this split per government and `capital_split_trend` refuses the
direction outright.

**The source's function share is not a share of anything.**
`financial_functions.pct_of_entity_total` is negative for 85 rows and above 100%
for 442 of 3,645. Harney County's Health function is filed at **-3,871%** against
$2.9M of positive spending, and a proportional symbol sized by that is not a
smaller circle, it is a wrong one. The ETL recomputes `share_of_total` as the
function's operating plus capital over the entity's own operating plus capital,
which takes the sane rows from 3,171 to 3,591 and, more usefully, makes the
service-area share and the function share two levels of one fraction instead of
two unrelated ones. The source column is stored and never read. Four rows still
exceed 100% because the two expenditure totals disagree, which is the documented
trap rather than a new one.

**The area and the function are selectable, not constants.** They were
`DEFAULT_AREA` and `DEFAULT_FUNCTION` in `index.html`, so of the 36 named things
Oregon's governments do exactly one could be drawn. `/api/functions` is the one
definition of what lives inside each area, sent at runtime like the preset list,
and the pickers cascade because a function only means something inside the area
holding it. A pre-rendered atlas file is keyed by the selector as well as the
measure: police protection and fire protection are the same measure with a
different argument, and keying on the measure alone meant one function had a map
and the other thirty-five silently returned it.

`explore.inside_service_area` answers the same question statewide that `drill`
answers for one government, and the two are different questions. Public Safety
is five functions: police protection carries the most money at $1.9B across 187
governments, and fire protection is reported by the most governments, 327 of
them, 243 of which are special districts. One is city work and the other is
district work, and that is the shape of how Oregon delivers public safety. The
function totals must never be added: a city paying a district for the work
reports the payment while the district reports the spending, so the same dollar
is in both rows and no share of the service area can be computed from them.

**Two thirds of Oregon's governments have no boundary, so they get a pin.**
1,029 of the 1,529 are special districts and fewer than twenty carry a polygon
anywhere in this data, which means every choropleth here silently omits most of
the state's governments. `entity_point` places 1,388 of them by matching the
city on the government's mailing address to a place polygon and taking its
centroid: no geocoder, no network, and 91% coverage. `maps.points` draws them
over the county outlines, sized by quantile because the values span six orders
of magnitude and any scale continuous in the value puts nine hundred at the
floor.

This is what the boundary layers structurally cannot do. Fire protection draws
at **one county in thirty-six** and at **300 governments** as pins, because the
districts doing the work are exactly the ones with no polygon.

**A pin is an office and never a service area.** It is where the mail goes. A
rural fire district filed at a Eugene address covers ground outside Eugene
entirely, and a district serving three counties has one pin. `pin_is_an_office`
travels on every drawing of these rows, is in `READER_NOTES`, and is written
into the frame beneath the map, because a dot on a map reads as the location of
the thing and no caveat elsewhere undoes that.

`_base_markup` caches the state's paths per frame. A county-scoped point map
projects the same 36 counties through a different transform, so the cache key
gained the projection identity; without it the second caller got the first
caller's paths at the wrong scale.

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
agent/        rules.py (the caveat catalogue), tools.py (42 tools), explore.py,
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
