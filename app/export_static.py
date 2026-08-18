#!/usr/bin/env python3
"""Pre-render the whole interface to static files.

    python3 app/export_static.py --out site

Grounded mode is deterministic: a preset for an entity produces the same answer
every time. So every answer can be rendered ahead of time and served as plain
files, which means the interface runs on GitHub Pages with no server at all.

One file per answer rather than one per entity. Drawings are about seven tenths
of the payload and the full report is the heaviest single answer, so splitting
them means the browser fetches only what someone clicks. A typical session
pulls a few hundred kilobytes rather than the whole entity.

What static mode cannot do is free text, which needs a model and therefore a
server. The exported interface says so in the same place the served one does.
"""

import argparse
import hashlib
import re
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))
sys.path.insert(0, HERE)

import server as S   # noqa: E402
import viz           # noqa: E402
import reports as R  # noqa: E402

# The function the lab fills in for a measure that needs one. Police protection
# rather than fire, because fire refuses on every layer and a demo that opened
# on a refusal would read as broken rather than as careful.
DEFAULT_FUNCTION = "Police Protection"


def slug(text):
    """The suffix a pre-rendered atlas file is keyed by, past its measure.

    Police protection and fire protection are the same measure with a different
    argument. Keyed on the measure alone, one function had a map in the build
    and the other thirty-five silently returned it, and the per-function
    catalogue 404ed so the lab showed a coverage panel with no verdicts.

    `selectorKey` in index.html computes the same string in the browser, and
    app/test_tables.py drives both over every function name to keep them equal.
    """
    return "-" + re.sub(r"[^a-z0-9]+", "-", text.lower()) if text else ""


def write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    return os.path.getsize(path)


def export(out_dir):
    store = S.STORE
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # The magnitude rides along because it is what tells two same-named
    # governments apart in a result row, and the browser filters this list
    # itself rather than calling a search endpoint.
    entities = [S._with_magnitude(row) for row in store.rows(
        "SELECT e.pid6, e.legal_name, e.common_name, e.gov_type_name, e.host_county "
        "FROM entities e ORDER BY e.gov_type_name, e.common_name")]

    # The search index is small enough to filter in the browser, which is both
    # simpler than a search endpoint and faster than one.
    write(os.path.join(data_dir, "entities.json"), entities)
    # Everything the browser needs to assemble a comparison for any set a
    # reader picks. About 400 KB, 90 KB over the wire, against a combinatorial
    # number of pages if each set were pre-rendered instead.
    import comparison_data
    write(os.path.join(data_dir, "comparison.json"), comparison_data.build(store))

    # The doors that are not the search box, and one answer per named function
    # behind them. Thirty-six small files against a search that only finds what
    # a reader could already name.
    import browse_data
    browse = browse_data.build(store)
    write(os.path.join(data_dir, "browse.json"), browse)
    for function in browse["functions"]:
        write(os.path.join(data_dir, "function", f"{function['name']}.json"),
              S.answer_function(function["name"]))

    write(os.path.join(data_dir, "service_areas.json"), {
        "areas": [r["service_area"] for r in store.rows(
            "SELECT service_area, COUNT(*) AS n FROM spending_by_service_area "
            "WHERE total > 0 GROUP BY service_area ORDER BY n DESC")]})
    import atlas as A
    import brief as BR
    # The alias index rides with the topic list. Aliases resolve to the same
    # seventeen canonical topics in the browser, so no extra brief is rendered:
    # "unhoused" lands on the homelessness file that is already there.
    write(os.path.join(data_dir, "topics.json"), BR.topic_index())
    write(os.path.join(data_dir, "measures.json"),
          {"measures": A.measures(), "layers": A.layers()})
    write(os.path.join(data_dir, "mode.json"), {
        "mode": "static",
        "entities": len(entities),
        "presets": S.preset_shell(),
        "shared": sorted(S.ENTITY_INDEPENDENT),
        "css": viz.css(".pf-viz") + R.REPORT_CSS,
        "built": time.strftime("%Y-%m-%d"),
    })

    total = written = 0
    for index, entity in enumerate(entities, 1):
        pid6 = entity["pid6"]
        available = S.availability(pid6)

        presets = [{"id": pid, "label": label, "group": group,
                    "available": (needs is None or bool(available.get(needs)))}
                   for pid, label, group, needs, _ in S.PRESETS]
        total += write(os.path.join(data_dir, pid6, "presets.json"),
                       {"entity": entity, "presets": presets})

        for preset in presets:
            # Do not render an answer the interface will not offer.
            if not preset["available"]:
                continue
            # Rendered once into data/shared, below.
            if preset["id"] in S.ENTITY_INDEPENDENT:
                continue
            answer = S.answer_preset(preset["id"], pid6)
            total += write(os.path.join(data_dir, pid6, f"{preset['id']}.json"), answer)
            written += 1

        if index % 250 == 0:
            print(f"  {index}/{len(entities)} entities, {total/1e6:.0f} MB so far")

    # Briefs, for every town with an established stack crossed with every
    # subject. A brief is the front door, so leaving it to the server would mean
    # the exported build opens on a question it cannot answer. Only cities: the
    # stack is established for a place, and asking a fire district which
    # governments serve it is the question answering itself.
    stacked = [row["place_pid6"] for row in store.rows(
        "SELECT DISTINCT place_pid6 FROM serves_place")]

    # The brief opens on a county map, and that map is the same picture for
    # every place inside the same county on the same category. Embedded once per
    # brief it was 4,080 copies of roughly 200 distinct drawings, which took the
    # build from 386 MB to 649 MB on its own. Written once and referenced, the
    # briefs go back to about a tenth of that and the reader fetches each map at
    # most once.
    seen_maps = {}
    for place_pid6 in stacked:
        for topic in BR.topics():
            answer = S.answer_brief(place_pid6, topic)
            for block in answer["blocks"]:
                if block["kind"] != "map" or not block.get("svg"):
                    continue
                key = hashlib.sha1(block["svg"].encode()).hexdigest()[:16]
                if key not in seen_maps:
                    path = os.path.join(data_dir, "maps", f"{key}.json")
                    total += write(path, {"svg": block["svg"]})
                    seen_maps[key] = True
                    written += 1
                block["src"] = f"maps/{key}.json"
                del block["svg"]
            total += write(
                os.path.join(data_dir, place_pid6, f"brief-{topic}.json"), answer)
            written += 1
    print(f"  {len(stacked)} cities x {len(BR.topics())} subjects over "
          f"{len(seen_maps)} distinct maps, {total/1e6:.0f} MB so far")

    # The map lab: every measure on every layer it is valid on, plus the
    # catalogue per layer. A refusal is pre-rendered too, because "this layer
    # would be mostly absence" is the answer a discovery phase came for.
    # Every service area and every function inside it, not one of each. The lab
    # used to hard-code Police Protection, so 35 of the 36 named things
    # governments do had no map anywhere in the build; a picker over them is
    # only real if the files behind it exist.
    areas = sorted({r["service_area"] for r in store.rows(
        "SELECT DISTINCT service_area FROM financial_functions")})
    functions_by_area = {}
    for row in store.rows(
            "SELECT service_area, function_name FROM financial_functions "
            "WHERE COALESCE(operating_expenditures, 0) "
            "    + COALESCE(capital_expenditures, 0) > 0 "
            "GROUP BY service_area, function_name"):
        functions_by_area.setdefault(row["service_area"], []).append(row["function_name"])
    all_functions = sorted({f for v in functions_by_area.values() for f in v})

    write(os.path.join(data_dir, "functions.json"), {
        "areas": {area: [{"name": name,
                          "governments": store.row(
                              "SELECT COUNT(DISTINCT pid6) AS n FROM financial_functions "
                              "WHERE function_name = ? AND COALESCE(operating_expenditures, 0)"
                              " + COALESCE(capital_expenditures, 0) > 0", name)["n"]}
                         for name in sorted(names)]
                  for area, names in functions_by_area.items()}})

    # What each service area is made of, across the state. One file per area.
    for area in areas:
        total += write(
            os.path.join(data_dir, "inside",
                         re.sub(r"[^a-z0-9]+", "-", area.lower()) + ".json"),
            S.answer_inside_area(area))
        written += 1

    for layer in A.layers():
        # One catalogue per function, not one per layer. Four of the fifteen
        # rows in it are a measure plus a selector, and their verdicts change
        # with the selector: fire protection draws at one county and police
        # protection at thirty-four. A single catalogue per layer answered the
        # coverage question for whichever function happened to be the default
        # and 404ed for the other thirty-five, which showed up in the lab as a
        # panel with no verdicts at all.
        #
        # Cheap despite the count: render_map memoizes on its arguments, so the
        # 144 catalogues are the same few hundred draws read repeatedly.
        for name in all_functions:
            total += write(
                os.path.join(data_dir, "atlas",
                             f"catalogue-{layer}{slug(name)}.json"),
                S.answer_atlas([], layer=layer, mode="catalogue",
                               service_area=S.DEFAULT_AREA, function=name))
            written += 1
        for measure in A.measures():
            if layer not in measure["layers"]:
                continue
            # A measure that takes a selector is rendered once per value of it.
            # A measure that takes none is rendered once, under no key.
            if measure["needs"] == "service_area":
                choices = [(area, {"service_area": area}) for area in areas]
            elif measure["needs"] == "function":
                choices = [(name, {"function": name}) for name in all_functions]
            else:
                choices = [(None, {})]
            for label, selectors in choices:
                total += write(
                    os.path.join(data_dir, "atlas",
                                 f"{layer}-{measure['id']}{slug(label)}.json"),
                    S.answer_atlas([measure["id"]], layer=layer,
                                   service_area=selectors.get("service_area",
                                                              S.DEFAULT_AREA),
                                   function=selectors.get("function")))
                written += 1

    for preset_id in sorted(S.ENTITY_INDEPENDENT):
        total += write(os.path.join(data_dir, "shared", f"{preset_id}.json"),
                       S.answer_preset(preset_id))
        written += 1

    # The interface itself, with a flag telling it to read files rather than
    # call an API. One source file, two modes, so they cannot drift.
    for script in ("comparison.js", "subjects.js", "tables.js"):
        shutil.copyfile(os.path.join(HERE, script), os.path.join(out_dir, script))

    # A build stamp on every asset the page fetches. GitHub Pages caches HTML,
    # and a stale index.html paired with a fresh data directory is the worst
    # kind of broken: it asks for files the new build no longer writes and falls
    # into its own "not available" branch, which reads as a dead feature rather
    # than as a cache. Stamping the URLs means an old page can only ever load
    # the data it was built against.
    stamp = str(int(time.time()))
    with open(os.path.join(HERE, "index.html")) as fh:
        html = fh.read()
    html = html.replace("<script>\nconst $ =",
                        f"<script>\nwindow.PF_STATIC = true;\n"
                        f"window.PF_BUILD = '{stamp}';\nconst $ =", 1)
    for script in ("comparison.js", "subjects.js", "tables.js"):
        html = html.replace(f'src="{script}"', f'src="{script}?v={stamp}"', 1)
    with open(os.path.join(out_dir, "index.html"), "w") as fh:
        fh.write(html)

    # GitHub Pages runs Jekyll by default, which ignores directories it does not
    # recognise and would drop parts of data/.
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    return {"entities": len(entities), "answers": written,
            "megabytes": round(total / 1e6, 1)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(
        os.path.dirname(HERE), "site"))
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out)
    if args.clean and os.path.isdir(out_dir):
        shutil.rmtree(out_dir)

    started = time.time()
    report = export(out_dir)
    report["seconds"] = round(time.time() - started, 1)
    report["out"] = out_dir
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
