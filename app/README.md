# The app

```
python3 etl/build.py               # once, ~7s, builds the store
python3 etl/dissolve_multnomah.py  # optional, adds measured service extent
python3 app/server.py              # then open http://localhost:8000
```

Standard library only. No install, no build step, no dependencies.

## Two modes, and it says which

**Grounded** is what you get with no model. The Appendix A presets map directly
to tool calls, so every figure, chart, boundary and rule is real. What is
missing is only the prose that reads them back, and the interface says so
rather than filling the gap with something that sounds like an answer.

**Full** turns on when `ANTHROPIC_API_KEY` is set and `anthropic` is installed.
Free-text questions then run through the orchestrator, and the model writes
against exactly the same tool results the presets use.

That boundary is deliberate and useful. Everything except the wording is
deterministic, so a wrong answer in grounded mode is a tool layer bug, and a
wrong answer only in full mode is a model adherence problem. It is the same
delivery-versus-adherence split the eval harness reports.

Set `PF_MODEL` to evaluate a different model. Nothing else changes.

## What the interface is for

Most chat interfaces hide the plumbing. Here the plumbing is the product, so
three things are deliberately visible.

**What this cannot tell you.** Every answer closes on the limits that bear on
it, in the reader's words. This used to be a fold headed "11 rules bind this
answer" listing every constraint the tools attached, and almost all of them were
imperative instructions aimed at whoever writes the answer rather than facts for
whoever reads it. `rules.READER_NOTES` is the subset that survives into the
interface, capped and minus anything the prose already said, so the two limits
that would change a conclusion are not buried among nine that cannot. The full
set still reaches the model and still travels on the response.

**The tool trace.** Which tools ran, in order. The claim that the model never
recalls a figure is only credible if you can watch it not happening.

**Preset gating.** A preset is offered only when the data behind it exists for
the selected entity, driven by the availability manifest the ETL computes.
New Bridge Water Supply District has no spending breakdown, so that question is
not offered for it. An interface that promises an answer and then explains the
absence has already wasted the click.

## The front door is a subject and a place

Everything starts from one sentence: *working on a subject in a place*. That is
what somebody scoping a piece of work has, and it is not a government name.
Two thirds of Oregon's governments are special districts and nobody arrives
able to name one, so a search box over 1,529 names is a door only for people
who already know the answer.

**A typed subject resolves through an alias index.** The concordance is keyed by
seventeen words. Scopes of work are written in different ones: unsheltered,
broadband, deferred maintenance, behavioral health. `rules.TOPIC_ALIASES` maps
those onto the seventeen, and `rules.resolve_topic` is the resolver.

This is not a search convenience. §12 says an issue answer opens on the gap
between the subject and the Census categories, and **a subject that fails to
resolve never reaches that sentence** — the reader gets an empty pane, concludes
this data has no view on their subject, and goes to a source that will hand them
a number with no gap attached. A miss here routes around every refusal in the
system, which is why the vocabulary is asserted in `test_tools.py` rather than
left to whoever remembers to add a word.

An alias may name more than one subject and both are offered. "fire" is
structural and wildland; they land on different categories with different fit,
and choosing one silently is the §14 ambiguity failure with the ambiguity
hidden. The answer says which reading it took and what else the word could have
meant.

**Fit rides on the chip, before the click.** Every subject shows whether the
data stands in for it well, moderately or weakly. "Can this be measured at all"
is the first question in a prototyping session, and making somebody open a
subject to discover it is a loose proxy wastes the move.

**`subjects.js` is the second implementation** of `resolve_topic`, for the same
reason `comparison.js` is the second implementation of the comparison: a
pre-rendered build has no server, so the browser decides which brief a reader
gets. `test_parity.py` drives both over every alias and fails on any
disagreement. The vocabulary itself is not duplicated — it arrives from
`/api/topics`, one definition in `rules.py`.

**The rail carries the map lab and nothing else by default.** It used to carry
eight headings, every one of them another way to name a subject or a place,
which made it a directory of doors into a room the reader was already standing
in. What survives is the part reachable no other way: trying a measure on a
layer and being told what it can see is not a question about a government, so
no search for a name will ever produce it. The rest is one fold down, because
browsing 36 counties is occasionally the right move and never the first one.

## The map lab

The one surface here that is not a question about a government. It answers "is
this layer worth building", which is the question a discovery phase is actually
trying to close, and no search for a name will ever produce it.

Every measure carries its verdict for the currently selected layer **on the
control that picks it**: a bar whose length is the share of the extent that
would carry a value, and the word drawn or refused beside it. Before this the
verdict only appeared after picking a measure and waiting for a map that turned
out to be a stated reason, so finding out which layers were worth building meant
walking the whole list one dead map at a time.

The numbers are the argument for putting them up front. On counties every one of
the fifteen measures draws. On places three do, and on school districts five,
and the reasons differ: nine refuse because Oregon's 378 cities are specks at
state scale or because most of them report nothing in the category, and three
refuse because the survey margin is too wide to bin. That table is the artifact,
not any map in it.

A refused measure stays pickable. The refusal drawn in the frame, with the count
of boundaries behind it, is a more useful thing to hand somebody scoping a layer
than a disabled control.

## How a table is drawn

`app/tables.js` draws every table block the thread shows, both the ones the
server composes and the ones `comparison.js` assembles in the browser, so a
comparison read from a static file and the same comparison read from a live
server cannot disagree about anything but their data.

**A column that is the same in every row is lifted into a line above the table.**
It is one fact, and repeating it per row buries the rows that carry something.
The lift is measured and stops the moment one row differs, so the value moves
and is never dropped; the last column standing is always kept. It takes two rows
to be constant — one row is a record, every column of it is trivially constant,
and lifting on that scatters the record between a caption and a table.

Figures are detected and set as figures — tabular numerals, aligned right, so
magnitudes compare down the column without being read. Verdicts get a mark
**and** their word, because colour alone does not survive a colourblind reader,
a printout, or a screenshot pasted into a document. A one-column table gets no
lead emphasis: it is a list of sentences, not a stack of headings.

`agent/reports.py` draws its own tables and deliberately does not use these
rules. The full report is a document with its own stylesheet and its own
audience; this is interface behaviour.

## Keyboard

`/` focuses search. `Enter` sends, `Shift+Enter` adds a line.

## Testing

```
python3 app/test_server.py    # 184 checks, no browser needed
python3 app/test_parity.py    # 461 checks, drives the browser code through node
python3 app/test_tables.py    #  28 checks on how a table block is drawn
```

`test_server.py` calls the handlers directly. It asserts that presets return
real figures, that refusals survive into the interface rather than being
smoothed away, and that the gating actually withholds what it should.

`test_parity.py` is the reason two implementations of anything are acceptable
here. It runs Python and JavaScript over the same comparisons and the same
typed subjects, and fails on any disagreement in refusal state, answer
sentence, caveat codes, table rows, every SVG text node, or which subject a
word resolves to.
