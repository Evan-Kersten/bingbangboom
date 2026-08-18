#!/usr/bin/env python3
"""How a table block is drawn.

Every table the thread shows goes through app/tables.js, so the rules in it
apply to every answer at once and a mistake here is a mistake everywhere. The
rules are also the kind that look harmless and are not: a column lifted out
because it happened to be constant would be a value silently dropped if the
lift were wrong about constant.

Driven through node against the real module rather than a copy of it.

    python3 app/test_tables.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))
sys.path.insert(0, HERE)

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def draw(cases):
    """Return {html, layout} for each (rows, caption) case, from the real module."""
    script = f"""
const fs = require('fs');
require({json.dumps(os.path.join(HERE, 'tables.js'))});
const cases = JSON.parse(fs.readFileSync({json.dumps('CASES')}, 'utf8'));
process.stdout.write(JSON.stringify(cases.map((c) => ({{
  html: PFTables.card(c.rows, c.caption),
  layout: c.rows && c.rows.length ? PFTables.layout(c.rows) : null,
}}))));
"""
    paths = []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(cases, fh)
        paths.append(fh.name)
    script = script.replace(json.dumps("CASES"), json.dumps(paths[0]))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        paths.append(fh.name)
    try:
        done = subprocess.run(["node", paths[1]], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip()[-800:])
        return json.loads(done.stdout)
    finally:
        for path in paths:
            os.unlink(path)


def main():
    if not node_available():
        print("node is not available, so the render layer cannot be driven.")
        return 1

    cases = [
        # 0: the shape a brief's conditions table has. Two columns are the same
        # in every row and belong above the table, not in it.
        {"rows": [
            {"what residents report": "Median household income", "figure": "$91K",
             "safe to quote": "yes", "measured over": "this place"},
            {"what residents report": "Unemployment rate", "figure": "5.8%",
             "safe to quote": "yes", "measured over": "this place"}]},
        # 1: the same table where one row disagrees. Nothing may be lifted.
        {"rows": [
            {"what residents report": "Median household income", "figure": "$91K",
             "safe to quote": "yes", "measured over": "this place"},
            {"what residents report": "Unemployment rate", "figure": "5.8%",
             "safe to quote": "no, the margin is too wide",
             "measured over": "this place"}]},
        # 2: every column constant. The last one has to survive.
        {"rows": [{"a": "same", "b": "same"}, {"a": "same", "b": "same"}]},
        # 3: one column of sentences.
        {"rows": [{"what to ask for next": "Ask for the budget message."},
                  {"what to ask for next": "Ask what sits inside Public Safety."}]},
        # 4: a single row, where every column is trivially constant. This is the
        # shape a brief takes for a place with one silent government, and
        # lifting on it produced a caption holding most of the record above a
        # table holding the rest.
        {"rows": [{"government": "Portland", "type": "Municipal"}]},
        # 5: nothing at all.
        {"rows": []},
        # 6: a caption with no constant column.
        {"rows": [{"government": "Portland"}, {"government": "Salem"}],
         "caption": "Reporting nothing usually means another government holds it."},
        # 7: markup in the data.
        {"rows": [{"government": "<script>x</script>", "figure": "$1"},
                  {"government": "Salem", "figure": "$2"}]},
    ]
    got = draw(cases)

    print("\na column that is the same in every row is said once, above the table")
    lifted = got[0]["layout"]["lifted"]
    check("both constant columns are lifted",
          sorted(lifted) == ["measured over", "safe to quote"], str(lifted))
    check("and the columns that vary are kept",
          got[0]["layout"]["kept"] == ["what residents report", "figure"],
          str(got[0]["layout"]["kept"]))
    check("the lifted value appears once above the table",
          got[0]["html"].count("this place") == 1)
    check("and it is not lost", "this place" in got[0]["html"])

    print("\none row disagreeing keeps the column")
    check("the column that now varies stays in the table",
          "safe to quote" in got[1]["layout"]["kept"], str(got[1]["layout"]))
    check("and only the one still constant is lifted",
          got[1]["layout"]["lifted"] == ["measured over"], str(got[1]["layout"]))
    check("both verdicts are still shown",
          got[1]["html"].count("<td class=\"verdict\"") == 2)
    check("a firm estimate is marked good",
          'class="dot good"' in got[1]["html"])
    check("and a wide margin is marked warning",
          'class="dot warning"' in got[1]["html"])

    print("\nthe last column is never lifted away")
    check("a table where everything is constant still has a column",
          got[2]["layout"]["kept"] == ["b"], str(got[2]["layout"]))
    check("and the other one is stated above it",
          got[2]["layout"]["lifted"] == ["a"], str(got[2]["layout"]))
    check("a single row lifts nothing, because one row cannot be constant",
          got[4]["layout"] == {"lifted": [], "kept": ["government", "type"]},
          str(got[4]["layout"]))
    check("so it renders as an ordinary row",
          "Portland" in got[4]["html"] and "tnote" not in got[4]["html"])

    print("\nfigures are set as figures and names are not")
    check("money is right-aligned", '<td class="num">$91K</td>' in got[0]["html"])
    check("a percentage is right-aligned", '<td class="num">5.8%</td>' in got[0]["html"])
    check("a name is not",
          '<td class="num">Median household income</td>' not in got[0]["html"])

    print("\na one-column table is prose, not headings")
    check("the single column carries no lead emphasis",
          'class="lead"' not in got[3]["html"], got[3]["html"][:200])
    check("a multi-column table does emphasise its first column",
          'class="lead"' in got[0]["html"])

    print("\nthe rest")
    check("no rows renders nothing at all", got[5]["html"] == "", got[5]["html"][:80])
    check("a caption is shown even with nothing lifted",
          "another government holds it" in got[6]["html"])
    check("markup in the data is escaped",
          "<script>" not in got[7]["html"] and "&lt;script&gt;" in got[7]["html"])

    print("\nthe brief's tables go through it end to end")
    import server as S
    portland = S.STORE.row(
        "SELECT pid6 FROM entities WHERE legal_name='CITY OF PORTLAND'")["pid6"]
    brief = S.answer_brief(portland, "technology")
    tables = [b for b in brief["blocks"] if b["kind"] == "table"]
    silent = [t for t in tables if "reports nothing here" in (t["rows"][0] or {})]
    check("the governments that report nothing are their own table", len(silent) == 1)
    check("and the reason is stated once as a caption, not once per row",
          bool(silent) and "caption" in silent[0]
          and not any("usually means" in str(v) for v in silent[0]["rows"][0].values()))
    reporting = [t for t in tables
                 if "government" in (t["rows"][0] or {}) and t is not silent[0]]
    check("the governments that do report are listed first",
          bool(reporting) and tables.index(reporting[0]) < tables.index(silent[0]))

    rendered = draw([{"rows": t["rows"], "caption": t.get("caption")} for t in tables])
    check("every table in a real brief renders",
          all(r["html"].startswith("<div class=\"card\">") for r in rendered))

    # Estacada has exactly one government reporting nothing, which is the shape
    # that broke the lift. Every brief in the build goes through this renderer,
    # so a one-row table is not an edge case here, it is a common one.
    estacada = S.STORE.row(
        "SELECT pid6 FROM entities WHERE legal_name='CITY OF ESTACADA'")["pid6"]
    one = [b for b in S.answer_brief(estacada, "homelessness")["blocks"]
           if b["kind"] == "table" and "reports nothing here" in (b["rows"][0] or {})]
    check("a place with one silent government has a one-row table",
          bool(one) and len(one[0]["rows"]) == 1)
    if one:
        html = draw([{"rows": one[0]["rows"], "caption": one[0].get("caption")}])[0]
        check("whose name is in the table rather than in the caption above it",
              "<td" in html["html"] and "Estacada School District 108" in
              html["html"].split("<tbody>")[1])
        check("and whose caption is still the shared sentence",
              "usually means another government" in html["html"].split("<table>")[0])

    print("\na pre-rendered atlas file is found by the key it was written under")
    # The bug this pins: the browser keys the fetch by the selected function and
    # the export keys the file by it too, in two implementations, and they have
    # to agree exactly. When they did not, every function but the default 404ed
    # and the lab's coverage panel came up empty in the static build only.
    import export_static as E
    import tools as TL
    names = sorted({r["function_name"] for r in S.STORE.rows(
        "SELECT DISTINCT function_name FROM financial_functions")})
    areas = sorted({r["service_area"] for r in S.STORE.rows(
        "SELECT DISTINCT service_area FROM financial_functions")})
    # The browser's function, lifted out of index.html and inlined rather than
    # copied, so the assertion is against the one the page actually ships.
    page = open(os.path.join(HERE, "index.html")).read()
    start = page.index("function selectorKey(body) {")
    source = page[start:page.index("\n}", start) + 2]
    script = source + f"""
const names = JSON.parse(require('fs').readFileSync({json.dumps('NAMES')}, 'utf8'));
process.stdout.write(JSON.stringify(names.map((n) => selectorKey(n.body))));
"""
    cases = ([{"body": {"function": n}} for n in names]
             + [{"body": {"service_area": a}} for a in areas]
             + [{"body": {}}])
    try:
        paths = []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(cases, fh)
            paths.append(fh.name)
        script = script.replace(json.dumps("NAMES"), json.dumps(paths[0]))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            paths.append(fh.name)
        done = subprocess.run(["node", paths[1]], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip()[-600:])
        js = json.loads(done.stdout)
    finally:
        for path in paths:
            os.unlink(path)

    expected = [E.slug(n) for n in names] + [E.slug(a) for a in areas] + [E.slug(None)]
    disagreed = [(c, e, g) for c, e, g in zip(cases, expected, js) if e != g]
    check(f"all {len(cases)} selectors key identically in both", not disagreed,
          str(disagreed[:2]))
    check("and a measure with no selector is keyed under nothing",
          expected[-1] == "" and js[-1] == "")

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
