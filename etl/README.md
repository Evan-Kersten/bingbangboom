# ETL: the analytical store

Builds the queryable store the insights agent runs against, from the source
files in the repository root.

```
python3 etl/build.py      # ~20 seconds, writes build/
python3 etl/verify.py     # 67 assertions, exits non-zero on failure
```

No dependencies. Standard library only, so this runs anywhere Python 3 does.

## Why there is a build step at all

The system prompt is not only a description of how to write answers. It is a
specification of which computations are meaningful on this data: do not compute
per-service-area year over year, do not compute per-capita for an entity with no
population, do not measure a distance against a peer median that sits at zero,
do not read a composite score without its components.

A query engine cannot enforce any of that. Given free-form SQL, a model will
return real rows from real tables and still break most of those rules, and the
answer will look perfectly grounded because the numbers are genuine. So the
conditions are evaluated once, here, and stored in `entity_flags` and the
per-block availability manifest. The runtime tools return the flag in the same
object as the figure, which means the model cannot receive a number without
also receiving the condition attached to it.

The second reason is that the joins the data appears to have do not exist.
`OR Office Taxonomy.csv` has no `pid6` at all; it keys on OCD division
identifiers and has to be resolved by name, with a confidence that §7 requires
you to state in the answer. School districts have a null `geoid` on every
record. Those crosswalks are built once and checked, not attempted per query.

## Output

| Path | What it is |
|---|---|
| `build/pf.sqlite` | The store. Used for verification and local development. |
| `build/tables/*.csv` | The normalized tables, portable between engines. |
| `build/load_duckdb.sql` | Builds the same store in DuckDB, which is the deployment target. |
| `build/geo/*.geojson` | Boundary layers converted from the shapefiles. |
| `build/defects.json` | Source data problems found, and what the build did about each. |
| `build/build_report.json` | Row counts, join coverage, flag frequencies. |

SQLite is the verification engine because it is in the standard library and the
whole dataset is small. DuckDB is the deployment target: it reads the shapefiles
directly through its spatial extension, so the geographic layer lives in the same
file as the fiscal tables and there is no separate GIS service.

## Tables

`entities` is the spine, keyed on `pid6`, the Census identifier and the only key
present across every fiscal source. The eight nested analytic blocks in each
JSONL record become their own tables, with arrays as child tables.

Two tables exist purely to carry rules rather than data:

**`entity_flags`** — one row per entity, one column per named rule in the prompt.
`prior_year_missing`, `debt_capped`, `capital_median_degenerate`,
`other_share_high`, `concentration_total`, `peer_group_mismatch`,
`no_population_denominator`, and the §11 attention thresholds.

**`data_availability`** — which blocks a given entity actually has. This drives
the Appendix A preset gating, which the prompt already says should be conditional
on available data. It also separates absent from zero, a distinction SQL erases:
"spends nothing on health" and "we have no health data" are the same NULL, and
§9 says the first usually means another entity holds that responsibility.

## Source defects corrected here

| Defect | Entities | What the build does |
|---|---|---|
| `priorYearScore` of 0 meaning "no prior year", with `yoyChange` computed against it | 1,086 | Sets both to NULL and flags `prior_year_missing`. Left alone this reports a large fiscal stability improvement that never happened, on 71% of entities. |
| State of Oregon benchmarked on capital share against 12,511 school districts | 1 | Flags `peer_group_mismatch`. |
| Stated peer count differs from the observed pool | 1,007 | Stores both `peer_count_stated` and `peer_count_observed`. |
| Capital-share peer median of 2.72 for the special district pool | 1,029 | Flags `peer_median_degenerate`; the ratio must be declined per §8. |
| `geoid` and `sdLea` null on every school district | 223 | Name matching recovers 156 at medium or low confidence. |
| No special district boundaries anywhere in the repository | 1,029 | Not mappable. The Multnomah precinct splits are written out as the only partial source. |
| Office taxonomy carries no holder names | 6,543 rows | Recorded so §7 tools say holders are absent rather than unknown. |

## Name matching

Census legal names abbreviate (`CENTRAL OREGON CMTY COLLEGE`) where OCD slugs
spell out (`central_oregon_community_college`), and some Census names carry scan
errors (`ASTORIA SCH DIST IC` for district `1C`). Matching runs in tiers, and
the tier is stored as `match_method` with a `confidence` beside it:

| Tier | Method | Confidence |
|---|---|---|
| Exact on the common name | `alias_exact` | high |
| Exact on the Census legal name | `legal_name_exact` | high |
| County naming convention, `baker` to `COUNTY OF BAKER` | `county_pattern` | high |
| Exact after folding scan-confusable characters | `loose_exact` | medium |
| Identical token sets | `token_exact` | medium |
| Token overlap above 0.70, unambiguously the best candidate | `token_overlap` | low |
| Not a government: legislative, congressional, judicial districts | `not_a_government` | none |

Current coverage is 1,916 of 2,041 government divisions, or 93.9%. The remaining
125 are mostly genuine renames, where the office file and the Census file
disagree about what a district is called, and a few districts absent from the
fiscal data entirely. §7 forbids presenting a low-confidence match as
established, so the tier is preserved rather than flattened into a boolean.

## Extending

Adding a source means adding a loader in `build.py`, a table in `schema.sql`,
and assertions in `verify.py`. Adding a rule means adding a column to
`entity_flags` and the assertion that proves it fires on the entity the prompt
describes. `verify.py` pins specific named entities on purpose: Sumpter for the
debt cap, Baker County for the prior-year artifact. When a rule changes, the
test names the government it changes for.
