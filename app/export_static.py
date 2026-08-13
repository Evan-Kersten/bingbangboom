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

    entities = store.rows(
        "SELECT pid6, legal_name, common_name, gov_type_name, host_county FROM entities "
        "ORDER BY gov_type_name, common_name")

    # The search index is small enough to filter in the browser, which is both
    # simpler than a search endpoint and faster than one.
    write(os.path.join(data_dir, "entities.json"), entities)
    write(os.path.join(data_dir, "service_areas.json"), {
        "areas": [r["service_area"] for r in store.rows(
            "SELECT service_area, COUNT(*) AS n FROM spending_by_service_area "
            "WHERE total > 0 GROUP BY service_area ORDER BY n DESC")]})
    write(os.path.join(data_dir, "mode.json"), {
        "mode": "static",
        "entities": len(entities),
        "css": viz.css(".pf-viz") + R.REPORT_CSS,
        "built": time.strftime("%Y-%m-%d"),
    })

    total = written = 0
    for index, entity in enumerate(entities, 1):
        pid6 = entity["pid6"]
        availability = store.row("SELECT * FROM data_availability WHERE pid6=?", pid6)
        available = dict(availability) if availability else {}

        presets = [{"id": pid, "label": label, "group": group,
                    "available": (needs is None or bool(available.get(needs)))}
                   for pid, label, group, needs, _ in S.PRESETS]
        total += write(os.path.join(data_dir, pid6, "presets.json"),
                       {"entity": entity, "presets": presets})

        for preset in presets:
            # Do not render an answer the interface will not offer.
            if not preset["available"]:
                continue
            answer = S.answer_preset(preset["id"], pid6)
            total += write(os.path.join(data_dir, pid6, f"{preset['id']}.json"), answer)
            written += 1

        # The comparison tray lets a reader pick any set of governments, and
        # every set of four across twelve areas is far more pages than a static
        # build should carry. What is pre-rendered is each government against
        # its own nearest peers, which is the comparison the presets make; the
        # interface says plainly that an arbitrary set needs the server.
        group, _ = S.peer_set(pid6)
        if len(group) > 1:
            for form, area in (("per_capita_over_time", None),
                               ("entities_over_time", None),
                               ("per_capita_by_service_area", S.DEFAULT_AREA),
                               ("service_area_across_entities", S.DEFAULT_AREA)):
                result = S.T.render_comparison(
                    store, group, form, service_area=area,
                    indexed=(form == "per_capita_over_time"))
                payload = {"blocks": [b for b in [
                    {"kind": "answer", "text": S._compare_headline(result)},
                    ({"kind": "chart", "svg": result["data"]["svg"],
                      "table": result["data"].get("table")}
                     if result["data"].get("svg") else None),
                    S.B.limits([result])] if b],
                    "trace": ["render_comparison"]}
                name = form + (f".{area}" if area else "")
                total += write(os.path.join(data_dir, "compare", pid6, f"{name}.json"),
                               payload)
                written += 1

        if index % 250 == 0:
            print(f"  {index}/{len(entities)} entities, {total/1e6:.0f} MB so far")

    # The interface itself, with a flag telling it to read files rather than
    # call an API. One source file, two modes, so they cannot drift.
    with open(os.path.join(HERE, "index.html")) as fh:
        html = fh.read()
    html = html.replace("<script>\nconst $ =",
                        "<script>\nwindow.PF_STATIC = true;\nconst $ =", 1)
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
