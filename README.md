# Public Foundry

Follow the money to the line item, for all 1,529 of Oregon's local governments.

Start from where you live and see the governments with a claim on that address,
or search any of them by name. Then go from a whole budget down to one function
inside one service area, and out to who else does that function across the
state, with every figure placed against its peers — and with the reasons a
figure cannot carry weight travelling beside it rather than in a footnote
nobody reads.

## Run it

Nothing to install. Standard library Python 3 only.

```
python3 etl/build.py     # ~20s, builds build/pf.sqlite from the files in the root
python3 app/server.py    # http://localhost:8000
```

That gives you **grounded mode**: all 21 questions answer, free text does not.
Free text needs a model — `pip install anthropic` and set `ANTHROPIC_API_KEY` —
and everything else is identical, because the figures, charts and rules a preset
returns are the same ones the model is handed.

## Deploy it

`.github/workflows/pages.yml` rebuilds the store, runs every test, pre-renders
all 29,744 answers and publishes to GitHub Pages. Grounded mode is
deterministic, so the whole interface can be served as static files with no
server at all. The rendered site is ~382 MB and is never committed.

Details in `app/DEPLOY.md`.

## What is in here

| | |
|---|---|
| `public_foundry_system_prompt_v2.md` | the specification: which computations this data supports, and which it forbids |
| `etl/` | builds the queryable store from the source files in the repository root |
| `agent/` | 35 typed tools, the caveat catalogue, the renderers, the orchestrator |
| `app/` | the interface, served and static |
| `evals/` | trap questions — the ones a fluent model gets wrong |
| root `*.jsonl`, `*.csv`, `*.json`, `cb_2018_*`, `March_2026_*`, `cnty_voterprecincts/` | the sources, committed so the build reproduces from a clone |
| `PF Chat Bot Architecture.png`, `Handbook_-_Chapter_1_Nature_of_Cities.pdf` | background reading, not build inputs — nothing reads them |

Each directory has its own README.

## The idea

A model with SQL access over this data will produce confident answers that are
wrong in ways the reader cannot see: a growth rate for a category that has no
prior-year figure, a per-resident cost for a district with no residents, a
distance from a peer median that sits at zero, a spending map read as a verdict
on the people who live there.

So the model does not get SQL. It gets typed tools, and every tool returns its
figures together with the rules that bind them and a list of arithmetic that
must not be attempted on the result. Where the data cannot support a question,
the tool refuses and says why in terms a reader can check.

`CLAUDE.md` is the orientation for anyone — human or model — picking the project
up: the invariants, the data traps, and what is deliberately unfinished.
