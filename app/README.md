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

## Keyboard

`/` focuses search. `Enter` sends, `Shift+Enter` adds a line.

## Testing

```
python3 app/test_server.py    # 29 checks, no browser needed
```

Calls the handlers directly. Asserts that presets return real figures, that
refusals survive into the interface rather than being smoothed away, and that
the gating actually withholds what it should.
