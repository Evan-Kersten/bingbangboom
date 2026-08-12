#!/usr/bin/env python3
"""Render one of every chart form to a single HTML page.

Exists so the output can be looked at. The palette validator checks colour; it
does not check whether a label collides, a bar overflows its plot, or a map
projects to a smear. Those are eyeball defects, and the only way to catch them
is to render the page and look.

    python3 agent/gallery.py [--out build/gallery.html] [--theme light|dark]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools as T
import viz

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def figure(title, note, svg, table):
    parts = [f'<figure><figcaption>{viz.esc(title)}</figcaption>']
    if note:
        parts.append(f'<p class="note">{viz.esc(note)}</p>')
    parts.append(f'<div class="chart">{svg or ""}</div>')
    if table:
        columns = list(table[0].keys())
        parts.append('<details><summary>Table view</summary><table><thead><tr>')
        parts += [f"<th>{viz.esc(c)}</th>" for c in columns]
        parts.append("</tr></thead><tbody>")
        for row in table:
            parts.append("<tr>" + "".join(
                f"<td>{viz.esc(row[c])}</td>" for c in columns) + "</tr>")
        parts.append("</tbody></table></details>")
    parts.append("</figure>")
    return "".join(parts)


def build(theme="light"):
    store = T.Store()

    def pid(legal_name):
        return store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)["pid6"]

    sections = []

    subjects = [
        ("spending_composition", "COUNTY OF BAKER",
         "Nominal categories, so one hue for every bar. A value ramp here would "
         "double-encode bar length as colour."),
        ("stability_components", "CITY OF SUMPTER",
         "Two meters rather than a bar chart: each component is a ratio against a "
         "limit, and the composite hides which one drives it."),
        ("finances_over_time", "COUNTY OF BAKER",
         "Two series, same unit, one axis. Endpoint labels only."),
        ("workforce_composition", "COUNTY OF BAKER",
         "Shares are of listed functions, not of all staff."),
        ("peer_position", "CITY OF PORTLAND",
         "Emphasis form: the entity is the accent, the peer range is context."),
        ("peer_position", "NEW BRIDGE WATER SUPPLY DISTRICT",
         "The same call against a degenerate peer median. The chart refuses."),
    ]

    for form, entity, note in subjects:
        result = T.render_chart(store, pid(entity), form)
        sections.append(figure(f"{form} · {entity.title()}", note,
                               result["data"]["svg"], result["data"]["table"]))

    for layer, county in (("county", None), ("place", "Multnomah")):
        result = T.render_map(store, layer, "fiscal_stability", county=county)
        data = result["data"]
        scope = f", scoped to {county} County" if county else ""
        sections.append(figure(
            f"choropleth · {layer}{scope}",
            f"{data['covered']} of {data['polygons']} boundaries carry a value. "
            "No data has its own neutral, well off the ramp, so a coverage gap "
            "cannot be read as a low value."
            + (" Statewide, city polygons are dots, so a place map is scoped to a county."
               if county else ""),
            data["svg"], None))

    stamp = f' data-theme="{theme}"' if theme in ("light", "dark") else ""
    return f"""<!doctype html>
<html lang="en"{stamp}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Public Foundry chart forms</title>
<style>
{viz.css(".pf-viz")}
{viz.css("body")}
body {{
  background: var(--pf-surface, #fcfcfb);
  color: var(--pf-ink, #0b0b0b);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0; padding: 40px 24px 80px; line-height: 1.5;
}}
main {{ max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 44px; }}
h1 {{ font-size: 22px; margin: 0; font-weight: 600; }}
.lede {{ color: var(--pf-ink-2, #52514e); font-size: 14px; margin: 8px 0 0; max-width: 62ch; }}
figure {{ margin: 0; display: flex; flex-direction: column; gap: 10px; }}
figcaption {{
  font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
  color: var(--pf-muted, #898781); font-weight: 600;
}}
.note {{ font-size: 13px; color: var(--pf-ink-2, #52514e); margin: 0; max-width: 64ch; }}
.chart {{
  border: 1px solid var(--pf-grid, #e1e0d9); border-radius: 6px;
  padding: 18px; overflow-x: auto; background: var(--pf-surface, #fcfcfb);
}}
details {{ font-size: 13px; color: var(--pf-ink-2, #52514e); }}
summary {{ cursor: pointer; color: var(--pf-muted, #898781); font-size: 12px; }}
table {{ border-collapse: collapse; margin-top: 10px; font-size: 12.5px; width: 100%; }}
th {{
  text-align: left; font-weight: 600; color: var(--pf-muted, #898781);
  border-bottom: 1px solid var(--pf-axis, #c3c2b7); padding: 6px 12px 6px 0;
  font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
}}
td {{
  padding: 6px 12px 6px 0; border-bottom: 1px solid var(--pf-grid, #e1e0d9);
  font-variant-numeric: tabular-nums;
}}
</style>
</head>
<body class="pf-viz">
<main>
<header>
  <h1>Chart forms</h1>
  <p class="lede">Every form the model can select, rendered from the live store. The
  model chooses a form and an entity; it supplies no title, axis label, colour or
  number. Each chart carries a table view, and the forms that must refuse do refuse.</p>
</header>
{"".join(sections)}
</main>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(REPO, "build", "gallery.html"))
    parser.add_argument("--theme", default="light", choices=["light", "dark", "system"])
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write(build(args.theme))
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
