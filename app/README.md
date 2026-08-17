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

**The rules panel.** Every answer carries the constraints it is bound by, keyed
to the prompt section each comes from, with prohibitions marked separately from
requirements. Asking what drives Sumpter's stability score returns four: the
debt cap, the missing prior year, the Oregon property tax rule, and a standing
prohibition on per-service-area year over year. A reader who can see why an
answer stops where it does will trust the answers that do not stop.

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

## Keyboard

`/` focuses search. `Enter` sends, `Shift+Enter` adds a line.

## Testing

```
python3 app/test_server.py    # 179 checks, no browser needed
python3 app/test_parity.py    # 461 checks, drives the browser code through node
```

`test_server.py` calls the handlers directly. It asserts that presets return
real figures, that refusals survive into the interface rather than being
smoothed away, and that the gating actually withholds what it should.

`test_parity.py` is the reason two implementations of anything are acceptable
here. It runs Python and JavaScript over the same comparisons and the same
typed subjects, and fails on any disagreement in refusal state, answer
sentence, caveat codes, table rows, every SVG text node, or which subject a
word resolves to.
