#!/usr/bin/env python3
"""The browser and the server must answer a comparison identically.

A pre-rendered build assembles its comparisons in JavaScript, because no server
is there to do it and every set of four governments across twelve service areas
is far more pages than a static build can carry. That leaves two implementations
of the same four charts, and two implementations of anything drift.

So this drives both over the same sets and fails on any disagreement: the
figures, their order, the caveat codes attached, and whether the answer refuses.
It is the reason the duplication is acceptable rather than a liability.

Every peer distribution and baseline series is computed once in Python and
shipped as data, so those are identical by construction and not tested here.
What is tested is what is genuinely written twice: per-entity arithmetic, the
sorting, the caveat logic, and the headline sentence.

    python3 app/test_parity.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))
sys.path.insert(0, HERE)

import server as S              # noqa: E402
import comparison_data          # noqa: E402

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


def run_in_node(payload_path, requests):
    """Return the JS answer for each request."""
    script = f"""
const fs = require('fs');
require({json.dumps(os.path.join(HERE, 'comparison.js'))});
PFCompare.load(JSON.parse(fs.readFileSync({json.dumps(payload_path)}, 'utf8')));
const requests = JSON.parse(fs.readFileSync({json.dumps('REQUESTS')}, 'utf8'));
const out = requests.map((r) => {{
  const result = PFCompare.compare(r);
  const chart = result.blocks.filter((b) => b.kind === 'chart')[0] || null;
  const limits = result.blocks.filter((b) => b.kind === 'limits')[0] || null;
  return {{
    answer: (result.blocks.filter((b) => b.kind === 'answer')[0] || {{}}).text || null,
    table: chart ? chart.table : null,
    codes: limits ? limits.rules.map((x) => x.code).sort() : [],
    refused: Boolean(result.refused),
    svgBytes: chart && chart.svg ? chart.svg.length : 0,
    // Text nodes only: coordinates round differently between the two engines,
    // but a sentence drawn on a chart is a claim and has to match exactly.
    svgText: chart && chart.svg
      ? (chart.svg.match(/>([^<>]+)</g) || []).map((t) => t.slice(1, -1)) : [],
  }};
}});
process.stdout.write(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(requests, fh)
        requests_path = fh.name
    script = script.replace(json.dumps("REQUESTS"), json.dumps(requests_path))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        script_path = fh.name
    try:
        done = subprocess.run(["node", script_path], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(done.stderr.strip()[-800:])
        return json.loads(done.stdout)
    finally:
        os.unlink(script_path)
        os.unlink(requests_path)


def python_answer(store, request):
    """The same request through the server path the browser is standing in for."""
    result = S.T.render_comparison(
        store, request["pid6_list"], request["form"],
        service_area=request.get("service_area"),
        measure=request.get("measure", "expenditure"),
        indexed=bool(request.get("indexed")))
    return {
        "answer": S._compare_headline(result),
        "table": result["data"].get("table"),
        "codes": sorted({c["code"] for c in result["caveats"]}),
        "refused": bool(result["data"].get("refused")),
        "svgBytes": len(result["data"].get("svg") or ""),
        "svgText": re.findall(r">([^<>]+)<", result["data"].get("svg") or ""),
    }


def main():
    store = S.STORE

    if not node_available():
        print("node is not available, so the browser implementation cannot be driven.")
        print("Install node, or run this on a machine that has it.")
        return 1

    def pid(legal_name):
        row = store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)
        return row["pid6"] if row else None

    payload = comparison_data.build(store)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        payload_path = fh.name

    portland = pid("CITY OF PORTLAND")
    medford = pid("CITY OF MEDFORD")
    eugene = pid("CITY OF EUGENE")
    salem = pid("CITY OF SALEM")
    bend = pid("CITY OF BEND")
    multnomah = pid("COUNTY OF MULTNOMAH")
    baker_county = pid("COUNTY OF BAKER")
    new_bridge = pid("NEW BRIDGE WATER SUPPLY DISTRICT")
    school = store.row("SELECT pid6 FROM entities WHERE gov_type_name='School District' "
                       "AND legal_name LIKE '%PORTLAND%' LIMIT 1")["pid6"]
    salem_schools = pid("SALEM KEIZER SCH DIST 24J")
    keizer = pid("CITY OF KEIZER")
    unchartable = store.row(
        "SELECT pid6 FROM entities WHERE pid6 NOT IN "
        "(SELECT pid6 FROM spending_by_service_area WHERE total > 0) "
        "AND pid6 NOT IN (SELECT pid6 FROM financial_trends) LIMIT 1")["pid6"]
    beaverton_schools = pid("BEAVERTON SCH DIST 48J")

    # The sets are chosen to hit the branches that differ: the pair the reader
    # actually picked, a mixed-type set, a set carrying an entity with no
    # population, one with an entity absent from the area, one over the four-line
    # cap, and one that must refuse.
    sets = {
        "the pair a reader picked": [portland, medford],
        "four cities": [portland, eugene, salem, bend],
        "mixed types": [multnomah, portland, baker_county],
        "one with no population": [portland, medford, new_bridge],
        "over the four-line cap": [portland, eugene, salem, bend, medford],
        "nothing to compare": [new_bridge],
        # A school district's population is enrollment, so this set spans two
        # denominators and both implementations must refuse it the same way.
        "mixed denominators": [portland, school],
        # And a set entirely of school districts, which is not refused but must
        # be labelled per student everywhere the city set says per resident.
        "school districts only": [school, salem_schools, beaverton_schools],
        # A government the search box offers and no chart can draw. The browser
        # used to drop it silently and answer about the other one alone, which
        # is the exact failure this whole mechanism exists to prevent.
        "one that cannot be charted": [portland, unchartable],
        # And the same government on its own: nothing to draw at all.
        "nothing chartable at all": [unchartable],
        # Keizer filed 2017 and 2022 and nothing between; Portland filed every
        # year. On one axis those look identical unless the engine says otherwise.
        "annual beside two endpoints": [portland, keizer],
    }

    requests = []
    for name, group in sets.items():
        group = [p for p in group if p]
        requests.append({"label": name, "form": "per_capita_by_service_area",
                         "pid6_list": group, "service_area": "Public Safety"})
        requests.append({"label": name, "form": "service_area_across_entities",
                         "pid6_list": group, "service_area": "Public Safety"})
        requests.append({"label": name, "form": "per_capita_over_time",
                         "pid6_list": group, "indexed": True})
        requests.append({"label": name, "form": "per_capita_over_time",
                         "pid6_list": group, "indexed": False})
        requests.append({"label": name, "form": "entities_over_time",
                         "pid6_list": group, "measure": "expenditure"})
        # The two five-year forms. The trend data is two panels — 1,005 entities
        # filed only 2017 and 2022 — so both engines have to classify coverage
        # the same way and dash the same lines.
        requests.append({"label": name, "form": "five_year_change",
                         "pid6_list": group, "measure": "expenditure"})
        requests.append({"label": name, "form": "five_year_panel",
                         "pid6_list": group, "measure": "expenditure"})

    # A second service area, to catch anything keyed to public safety by accident.
    requests.append({"label": "another area", "form": "per_capita_by_service_area",
                     "pid6_list": [portland, eugene, salem],
                     "service_area": "Culture & Recreation"})
    # School districts report Education, not public safety, so the per-student
    # bars only exist here. This is the request that proves the column heading
    # follows the denominator rather than saying resident regardless.
    requests.append({"label": "per student", "form": "per_capita_by_service_area",
                     "pid6_list": [school, salem_schools, beaverton_schools],
                     "service_area": "Education"})

    try:
        js_results = run_in_node(payload_path, requests)
    except RuntimeError as error:
        print(f"  FAIL  the browser implementation raised\n{error}")
        return 1
    finally:
        os.unlink(payload_path)

    print("\nthe browser and the server agree, set by set")
    for request, js in zip(requests, js_results):
        py = python_answer(store, request)
        label = f"{request['label']} · {request['form']}" + (
            " · indexed" if request.get("indexed") else "")

        check(f"{label}: same refusal state", js["refused"] == py["refused"],
              f"js={js['refused']} py={py['refused']}")
        check(f"{label}: same answer sentence", js["answer"] == py["answer"],
              f"\n      js: {js['answer']}\n      py: {py['answer']}")
        check(f"{label}: same rules attached", js["codes"] == py["codes"],
              f"\n      js only: {sorted(set(js['codes']) - set(py['codes']))}"
              f"\n      py only: {sorted(set(py['codes']) - set(js['codes']))}")
        # The chart is a claim too. A note naming a government that could not be
        # drawn has to appear on both, or a reader gets a different picture
        # depending on whether a server happened to be running.
        check(f"{label}: the chart says the same words",
              js["svgText"] == py["svgText"],
              f"\n      js only: {[t for t in js['svgText'] if t not in py['svgText']][:3]}"
              f"\n      py only: {[t for t in py['svgText'] if t not in js['svgText']][:3]}")

        if py["table"] is None or js["table"] is None:
            check(f"{label}: both omit the table", py["table"] == js["table"],
                  f"js={type(js['table'])} py={type(py['table'])}")
            continue

        check(f"{label}: same number of rows", len(js["table"]) == len(py["table"]),
              f"js={len(js['table'])} py={len(py['table'])}")
        if len(js["table"]) == len(py["table"]):
            mismatches = [(i, p, j) for i, (p, j) in enumerate(zip(py["table"], js["table"]))
                          if {k: str(v) for k, v in p.items()}
                          != {k: str(v) for k, v in j.items()}]
            check(f"{label}: every figure matches, in order", not mismatches,
                  "" if not mismatches else
                  f"\n      row {mismatches[0][0]}"
                  f"\n      py: {mismatches[0][1]}"
                  f"\n      js: {mismatches[0][2]}")

    # The bug this whole mechanism exists to prevent: an answer about a set the
    # reader did not choose.
    print("\nthe answer is about the set that was picked")
    picked = [portland, medford]
    js_pair = run_in_node(*(lambda p: (p, [{"form": "per_capita_by_service_area",
                                            "pid6_list": picked,
                                            "service_area": "Public Safety"}]))(
        (lambda: (lambda fh: (json.dump(payload, fh), fh.close(), fh.name)[-1])(
            tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)))()))[0]
    names = {row["government"] for row in (js_pair["table"] or [])}
    check("Portland and Medford return Portland and Medford",
          names == {"Portland", "Medford"}, str(sorted(names)))
    check("and nobody else is in the table", len(names) == 2, str(sorted(names)))

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
