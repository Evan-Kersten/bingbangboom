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
    import brief as BR
    write(os.path.join(data_dir, "topics.json"), {"topics": BR.topics()})
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
    # the exported build opens on a question it cannot answer. Only towns: the
    # stack is established for a place, and asking a fire district which
    # governments serve it is the question answering itself.
    stacked = [row["place_pid6"] for row in store.rows(
        "SELECT DISTINCT place_pid6 FROM serves_place")]
    for place_pid6 in stacked:
        for topic in BR.topics():
            total += write(
                os.path.join(data_dir, place_pid6, f"brief-{topic}.json"),
                S.answer_brief(place_pid6, topic))
            written += 1
    print(f"  {len(stacked)} towns x {len(BR.topics())} subjects, {total/1e6:.0f} MB so far")

    for preset_id in sorted(S.ENTITY_INDEPENDENT):
        total += write(os.path.join(data_dir, "shared", f"{preset_id}.json"),
                       S.answer_preset(preset_id))
        written += 1

    # The interface itself, with a flag telling it to read files rather than
    # call an API. One source file, two modes, so they cannot drift.
    shutil.copyfile(os.path.join(HERE, "comparison.js"),
                    os.path.join(out_dir, "comparison.js"))

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
    html = html.replace('src="comparison.js"', f'src="comparison.js?v={stamp}"', 1)
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
