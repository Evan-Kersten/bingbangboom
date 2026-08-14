"""Report composition.

A report is a plan, not prose. The composer decides which sections exist, in
what order, with which figures and charts attached and which constraints bound
to each one. The model writes the words for each section against that plan.

The ordering is not editorial. §5 fixes it: scope limits first, ecosystem
membership second, proxy disclosure third, figures last. A report that opens
with figures and mentions the coverage gap at the end has told the reader the
opposite of what the prompt requires, and the ordering is the only part of that
a renderer can guarantee.

Word budgets are attached per section for the same reason. §3 says match length
to the question and §5 caps a multi-layer answer near 250 words. A report is
several answers, so the budget is per section rather than global, and a section
with nothing to say is dropped rather than padded.
"""

import format as fmt
import tools as T
import viz
from rules import caveat, denominator, not_computable, per_unit_label

# §10: absolute spending ranks by entity size and answers nothing. Each basis
# produces a different and defensible ordering, so the basis is stated, never
# implied.
BASES = {
    "absolute": ("total spending", "Ranks by entity size. Say so, or normalize."),
    # The label is completed from the set: residents for a city or county,
    # students for a school district. A set spanning both is refused, so by the
    # time this is rendered there is exactly one unit to name.
    "per_resident": ("spending {per_unit}",
                     "Requires a denominator for every entity in the set."),
    "share_of_spending": ("share of this entity's own spending",
                          "Comparable across sizes, but says nothing about scale."),
}


def normalize(store, pid6, metric_value, basis):
    """Return (value, unit) on the requested basis, or (None, reason)."""
    if basis == "absolute":
        return metric_value, "dollars"

    if basis == "per_resident":
        profile = store.row(
            "SELECT w.population, e.gov_type_name FROM workforce_profile w "
            "JOIN entities e ON e.pid6 = w.pid6 WHERE w.pid6 = ?", pid6)
        population = (profile or {}).get("population")
        if not population:
            return None, "no population on record"
        # The denominator is enrollment for a school district, so the unit that
        # comes back names itself rather than assuming residents.
        unit = per_unit_label((profile or {}).get("gov_type_name")) or "per unit"
        return metric_value / population, f"dollars {unit}"

    if basis == "share_of_spending":
        total = store.row(
            "SELECT total_expenditure FROM operating_vs_capital WHERE pid6=?", pid6)
        denominator = (total or {}).get("total_expenditure")
        if not denominator:
            return None, "no total expenditure on record"
        return metric_value / denominator * 100, "percent of own spending"

    return None, f"unknown basis '{basis}'"


def compare_normalized(store, pid6_list, service_area=None, basis="share_of_spending"):
    """Cross-entity comparison on a stated basis (§10, §12).

    Refuses rather than silently falling back when the basis is not computable
    for every entity in the set: a table where three rows are per-resident and
    two are absolute is worse than no table.
    """
    if basis not in BASES:
        return T.envelope("compare_normalized", caveats=[{
            "code": "unknown_basis", "rule": "§10",
            "guidance": f"No basis '{basis}'. Available: {', '.join(BASES)}."}])

    # Share of an entity's own spending is only a measure when it is a share of
    # something. Without a service area the numerator and the denominator are the
    # same figure, and every entity comes out at 100%, which looks like a finding.
    if basis == "share_of_spending" and not service_area:
        return T.envelope("compare_normalized", caveats=[{
            "code": "basis_needs_a_service_area", "rule": "§10",
            "guidance": "Share of own spending needs a service area to be a share of. "
                        "Without one every entity is 100% of itself. Name a service area, "
                        "or compare on per_resident or absolute and say which."}])

    entities = [T._entity(store, p) for p in pid6_list]
    found = [e for e in entities if e]
    missing = [p for p, e in zip(pid6_list, entities) if not e]
    types = {e["gov_type_name"] for e in found}

    # A school district's population field is enrollment, so per_resident over a
    # set spanning types would rank dollars-per-student against dollars-per-
    # resident in one column. That is the silent basis change §10 forbids, and it
    # is worse here than elsewhere because the column would look sound.
    units = {denominator(e["gov_type_name"]) for e in found
             if denominator(e["gov_type_name"])}
    if basis == "per_resident" and len(units) > 1:
        return T.envelope("compare_normalized", caveats=[{
            "code": "mixed_denominators", "rule": "§10",
            "guidance": "This set mixes governments measured per "
                        + " and per ".join(sorted(u.rstrip("s") for u in units))
                        + ". Those are different measures, not one measure across "
                          "different governments, so they cannot share a column. "
                          "Compare on absolute or share_of_spending, or split the set "
                          "by type."}])

    rows, uncomputable = [], []
    for entity in found:
        if service_area:
            record = store.row(
                "SELECT total FROM spending_by_service_area WHERE pid6=? AND service_area=?",
                entity["pid6"], service_area)
            raw = (record or {}).get("total")
        else:
            record = store.row(
                "SELECT total_expenditure FROM operating_vs_capital WHERE pid6=?",
                entity["pid6"])
            raw = (record or {}).get("total_expenditure")

        if raw is None:
            uncomputable.append({"name": entity["common_name"] or entity["legal_name"],
                                 "reason": "no figure on record"})
            continue

        value, unit = normalize(store, entity["pid6"], raw, basis)
        if value is None:
            uncomputable.append({"name": entity["common_name"] or entity["legal_name"],
                                 "reason": unit})
            continue
        rows.append({"pid6": entity["pid6"],
                     "name": entity["common_name"] or entity["legal_name"],
                     "gov_type": entity["gov_type_name"],
                     "raw": raw, "value": value, "unit": unit})

    rows.sort(key=lambda r: r["value"], reverse=True)

    label, basis_note = BASES[basis]
    label = label.format(
        per_unit=(f"per {sorted(units)[0].rstrip('s')}" if units else "per resident"))
    caveats = [
        {"code": "comparison_basis", "rule": "§10",
         "guidance": f"This comparison is on {label}. {basis_note} State the basis in the "
                     "answer; a different basis produces a different and equally defensible "
                     "ordering."},
        caveat("inputs_not_outcomes"),
    ]
    if len(types) > 1:
        caveats.append({
            "code": "mixed_entity_types", "rule": "§12",
            "guidance": f"This set mixes {', '.join(sorted(types))}. Compare like with like: "
                        "an Oregon county carries state-delegated functions a city does not. "
                        "Separate the types or decline."})
    if "County" in types and "Municipal" in types:
        caveats.append(caveat("oregon_county_delegated"))
    if uncomputable:
        caveats.append({
            "code": "entities_excluded", "rule": "§12",
            "guidance": f"{len(uncomputable)} entities are not on this table because the "
                        "basis is not computable for them. Note who is missing before "
                        "reading the ranking."})
    if missing:
        caveats.append({
            "code": "entities_missing", "rule": "§12",
            "guidance": f"{len(missing)} requested entities are not in the data at all."})
    if basis == "absolute":
        caveats.append({
            "code": "absolute_ranks_by_size", "rule": "§10",
            "guidance": "An absolute ranking is a ranking by entity size. Say so, and prefer "
                        "a normalized basis if the question is about priority rather than scale."})

    return T.envelope(
        "compare_normalized",
        data={"basis": basis, "basis_label": label, "service_area": service_area,
              "entities": rows, "excluded": uncomputable, "missing": missing,
              "entity_types": sorted(types), "count": len(rows)},
        caveats=caveats,
        blocked=[not_computable("service_area_yoy"), not_computable("outcome_or_performance")])


# ------------------------------------------------------------------ plans

def _section(section_id, heading, purpose, budget, data=None, chart=None,
             table=None, caveats=None, blocked=None):
    return {"id": section_id, "heading": heading, "purpose": purpose,
            "word_budget": budget, "data": data or {}, "chart": chart,
            "table": table, "caveats": caveats or [], "not_computable": blocked or []}


def _scope_section(store, pid6=None, availability=None):
    """§5: scope limits come first, before anything else in the report."""
    gaps = []
    if availability:
        labels = {
            "has_fiscal_stability": "fiscal stability score",
            "has_operating_vs_capital": "operating and capital split",
            "has_revenue_volatility": "revenue volatility",
            "has_spending_breakdown": "spending by service area",
            "has_workforce": "workforce",
            "has_financial_trends": "multi-year trend",
            "has_revenue_sources": "revenue sources",
            "has_offices": "elected offices",
            "has_geometry": "boundary for mapping",
            "has_population": "population, so no per-capita figures",
        }
        gaps = [text for key, text in labels.items() if not availability.get(key)]

    return _section(
        "scope", "What this data cannot tell you",
        "State the gaps plainly, in one or two sentences. This comes first because a "
        "reader who learns the limit after the figures has already formed a view. Do not "
        "apologize for the gaps and do not pad them.",
        60, data={"absent": gaps},
        caveats=[caveat("inputs_not_outcomes")],
        blocked=[not_computable("service_area_yoy"),
                 not_computable("outcome_or_performance")])


def entity_report(store, pid6):
    """A profile of one government."""
    entity = T._entity(store, pid6)
    if not entity:
        return T.envelope("compose_report", caveats=[{
            "code": "entity_not_found", "rule": "§14",
            "guidance": "This pid6 is not in the data."}])

    name = entity["common_name"] or entity["legal_name"]
    profile = T.get_entity_profile(store, pid6)
    availability = profile["data"].get("available") or {}
    sections = [_scope_section(store, pid6, availability)]

    sections.append(_section(
        "identity", f"What {name} is",
        "Name the government type and what that implies for reading the rest. Two or "
        "three sentences. Entity type determines which data is informative.",
        70, data=profile["data"], caveats=profile["caveats"],
        blocked=profile["not_computable"]))

    # Scale before composition. A reader who does not know whether this budget is
    # large or small for its type cannot read a share, and the per-resident
    # position says that in one figure where the composite score did not.
    import explore
    scale = explore.compare_to_peer_group(store, pid6, "expenditure_per_capita")
    if scale["data"].get("peers"):
        chart = T.render_chart(store, pid6, "peer_range")
        sections.append(_section(
            "scale", "How big this budget is for its type",
            "Give the figure on its own denominator, which is residents for a city or "
            "county and students for a school district, then where it sits among peers: "
            "the median, "
            "the middle half, and which quarter this entity falls in. State the peer "
            "group and its count. A median without its pool is not a benchmark.",
            100, data=scale["data"], chart=chart["data"]["svg"],
            table=chart["data"]["table"], caveats=scale["caveats"],
            blocked=scale["not_computable"]))

    spending = T.get_spending_breakdown(store, pid6)
    if spending["data"].get("service_areas"):
        chart = T.render_chart(store, pid6, "spending_composition")
        sections.append(_section(
            "spending", "Where the money goes",
            "State the largest areas and their shares, each against the median share "
            "for this government type rather than against each other. If the "
            "unclassified bucket exceeds the largest named category, say the breakdown "
            "is partial before naming any category as the biggest.",
            110, data=spending["data"], chart=chart["data"]["svg"],
            table=chart["data"]["table"], caveats=spending["caveats"],
            blocked=spending["not_computable"]))

        # One level deeper, into the largest area. This is the drill-down the
        # report exists to demonstrate: a share is a starting point, and the
        # functions underneath it are what a reader can act on.
        largest = (spending["data"].get("largest_named") or {}).get("service_area")
        if largest:
            inside = explore.drill(store, pid6, largest)
            functions = inside["data"].get("functions") or []
            if functions:
                chart = T.render_chart(store, pid6, "service_area_functions")
                sections.append(_section(
                    "inside_largest", f"Inside {largest}",
                    "Name the functions and what each spends, against the median for "
                    "that same function among peers that report it. Then give the "
                    "vendor-addressable figure and say it is a derivation, not a "
                    "reported line: operating and capital less personnel and less "
                    "amounts that cannot be purchased.",
                    110, data=inside["data"], chart=chart["data"]["svg"],
                    table=chart["data"]["table"], caveats=inside["caveats"],
                    blocked=inside["not_computable"]))

    # An entity reporting zero staff has no distribution to show. A section
    # headed "Workforce" over a chart of nothing is padding, and §3 says do not
    # pad an answer to seem thorough. The absence is already named in scope.
    workforce = T.get_workforce(store, pid6)
    if workforce["data"].get("by_service_area") and workforce["data"].get("total_employees"):
        chart = T.render_chart(store, pid6, "workforce_composition")
        sections.append(_section(
            "workforce", "Workforce",
            "Distribution and payroll share. Name two functions moving in opposite "
            "directions if any are; do not explain why.",
            90, data=workforce["data"], chart=chart["data"]["svg"],
            table=chart["data"]["table"], caveats=workforce["caveats"],
            blocked=workforce["not_computable"]))

    trend_chart = T.render_chart(store, pid6, "finances_over_time")
    if not trend_chart["data"]["refused"]:
        sections.append(_section(
            "trend", "Revenue and spending over time",
            "Entity totals only. Do not attribute a change to any single service area.",
            80, chart=trend_chart["data"]["svg"], table=trend_chart["data"]["table"],
            caveats=trend_chart["caveats"], blocked=trend_chart["not_computable"]))

    salient = T.find_salient(store, pid6)
    sections.append(_section(
        "salience", "What stands out",
        "Lead with the most structurally unusual thing, not the largest number. If "
        "nothing clears a threshold, say the profile is close to typical and stop.",
        100, data=salient["data"], caveats=salient["caveats"],
        blocked=salient["not_computable"]))

    offices = T.get_offices(store, pid6)
    sections.append(_section(
        "governance", "Who governs it",
        "Body, seat count, selection method. The data has no holder names, so say that "
        "rather than implying the seats are unknown.",
        70, data=offices["data"], caveats=offices["caveats"]))

    return T.envelope(
        "compose_report", entity=entity,
        data={"kind": "entity", "title": name,
              "subtitle": f"{entity['gov_type_name']}, {(entity['host_county'] or '').title()} County".rstrip(" County").rstrip(","),
              "sections": sections,
              "total_word_budget": sum(s["word_budget"] for s in sections)})


def place_report(store, county_name):
    """An ecosystem report: the question this platform answers best."""
    ecosystem = T.list_ecosystem(store, county_name=county_name)
    if not ecosystem["entity"]:
        return T.envelope("compose_report", caveats=ecosystem["caveats"])

    county = ecosystem["entity"]
    name = county["common_name"] or county["legal_name"]
    data = ecosystem["data"]

    sections = [_scope_section(store)]

    sections.append(_section(
        "membership", "Which governments serve this place",
        "Name the count by type and the basis for inclusion. Residents experience the "
        "combined result of a stack of overlapping governments, and no one of them is "
        "the government of this place.",
        120, data={"total": data["total"], "by_type": data["by_type"]},
        table=[{"type": k, "count": v} for k, v in sorted(data["by_type"].items())],
        caveats=ecosystem["caveats"], blocked=ecosystem["not_computable"]))

    if data.get("also_serving_filed_elsewhere"):
        sections.append(_section(
            "cross_boundary", "Governments serving here but filed elsewhere",
            "These operate in this county but are filed under a neighbouring one, so a "
            "host-county list misses them. State that their finances are recorded "
            "against another county.",
            90,
            data={"count": len(data["also_serving_filed_elsewhere"])},
            table=[{"government": e["common_name"] or e["legal_name"],
                    "type": e["gov_type_name"],
                    "filed under": (e["filed_county"] or "").title()}
                   for e in data["also_serving_filed_elsewhere"]],
            caveats=[caveat("host_county_undercaptures")]))

    county_map = T.render_map(store, "place", "spending_per_resident", county=name.split()[0])
    if county_map["data"].get("svg"):
        sections.append(_section(
            "geography", "What can be mapped",
            "Say how much of the ecosystem the map covers. Most special districts have "
            "no boundary in this data, so a map of a place is always partial and a list "
            "is the complete answer.",
            80, chart=county_map["data"]["svg"],
            data={"mappable": data["mappable"], "total": data["total"]},
            caveats=county_map["caveats"]))

    sections.append(_section(
        "responsibility", "Who is responsible",
        "Responsibility is distributed. Name the bodies rather than nominating one, and "
        "do not sum budgets across them without saying what the sum includes.",
        80, caveats=[caveat("ecosystem_no_summing"),
                     caveat("absence_means_another_entity")],
        blocked=[not_computable("property_tax_summed")]))

    return T.envelope(
        "compose_report", entity=county,
        data={"kind": "place", "title": f"Governments serving {name}",
              "subtitle": f"{data['total']} entities on shared host county",
              "sections": sections,
              "total_word_budget": sum(s["word_budget"] for s in sections)})


def comparison_report(store, pid6_list, basis="absolute", service_area=None):
    """Several entities on one stated basis."""
    comparison = compare_normalized(store, pid6_list, service_area, basis)
    data = comparison["data"]
    if not data.get("entities"):
        return T.envelope("compose_report", caveats=comparison["caveats"])

    sections = [_scope_section(store)]

    sections.append(_section(
        "basis", "What is being compared, and how",
        "Name the basis, the entity types and the count before any figure. A different "
        "basis produces a different and equally defensible ordering.",
        90, data={"basis": data["basis_label"], "types": data["entity_types"],
                  "count": data["count"], "service_area": service_area},
        caveats=comparison["caveats"], blocked=comparison["not_computable"]))

    rows = [{"label": e["name"], "value": e["value"]} for e in data["entities"]]
    unit = data["entities"][0]["unit"]
    formatter = fmt.percent if "percent" in unit else fmt.money
    chart = viz.horizontal_bars(
        f"Compared on {data['basis_label']}",
        f"{data['count']} entities, {', '.join(data['entity_types'])}",
        rows, formatter=formatter,
        note="Higher is not better. This data measures inputs, not results.")

    sections.append(_section(
        "ranking", "The comparison",
        "Give the ordering and the ratio between the ends. Higher spending is not better "
        "performance, and this data contains no results.",
        120, chart=chart,
        table=[{"government": e["name"], "type": e["gov_type"],
                data["basis_label"]: formatter(e["value"])} for e in data["entities"]],
        caveats=[caveat("inputs_not_outcomes")]))

    if data["excluded"] or data["missing"]:
        sections.append(_section(
            "missing", "Who is not on this table",
            "Name them and why. A ranking that silently drops entities reads as complete.",
            70, table=[{"government": e["name"], "reason": e["reason"]}
                       for e in data["excluded"]],
            data={"missing_pid6": data["missing"]}))

    return T.envelope(
        "compose_report",
        data={"kind": "comparison", "title": f"Compared on {data['basis_label']}",
              "subtitle": f"{data['count']} governments",
              "sections": sections,
              "total_word_budget": sum(s["word_budget"] for s in sections)})


REPORT_KINDS = {"entity": entity_report, "place": place_report,
                "comparison": comparison_report}


def compose_report(store, kind="entity", pid6=None, county_name=None,
                   pid6_list=None, basis="absolute", service_area=None):
    """Plan a report. Returns sections in §5 precedence order, not prose."""
    if kind not in REPORT_KINDS:
        return T.envelope("compose_report", caveats=[{
            "code": "unknown_report_kind", "rule": "§4",
            "guidance": f"No report kind '{kind}'. Available: {', '.join(REPORT_KINDS)}."}])
    if kind == "entity":
        return entity_report(store, pid6)
    if kind == "place":
        return place_report(store, county_name)
    return comparison_report(store, pid6_list or [], basis, service_area)


# ---------------------------------------------------------------- render

def render_report(plan, prose=None):
    """Assemble the report as HTML.

    `prose` maps a section id to the text the model wrote. Where a section has
    no prose, its purpose and constraints are shown instead, so an unwritten
    report is legibly a scaffold rather than silently an empty one.
    """
    prose = prose or {}
    data = plan["data"]
    parts = [f'<article class="pf-report">',
             f'<header><h1>{viz.esc(data["title"])}</h1>'
             f'<p class="sub">{viz.esc(data.get("subtitle") or "")}</p></header>']

    for index, section in enumerate(data["sections"], 1):
        parts.append(f'<section id="{viz.esc(section["id"])}">')
        parts.append(f'<h2><span class="n">{index:02d}</span>{viz.esc(section["heading"])}</h2>')

        written = prose.get(section["id"])
        if written:
            parts.append(f'<p>{viz.esc(written)}</p>')
        else:
            parts.append(f'<p class="purpose"><strong>To write here:</strong> '
                         f'{viz.esc(section["purpose"])} '
                         f'<em>Budget {section["word_budget"]} words.</em></p>')

        if section.get("data", {}).get("absent"):
            parts.append("<ul class='absent'>" + "".join(
                f"<li>No {viz.esc(item)}</li>" for item in section["data"]["absent"]) + "</ul>")

        if section.get("chart"):
            parts.append(f'<div class="chart">{section["chart"]}</div>')

        if section.get("table"):
            columns = list(section["table"][0].keys())
            parts.append("<table><thead><tr>" + "".join(
                f"<th>{viz.esc(c)}</th>" for c in columns) + "</tr></thead><tbody>")
            for row in section["table"]:
                parts.append("<tr>" + "".join(
                    f"<td>{viz.esc(row.get(c, ''))}</td>" for c in columns) + "</tr>")
            parts.append("</tbody></table>")

        constraints = section.get("caveats", []) + section.get("not_computable", [])
        if constraints:
            parts.append('<details class="rules"><summary>'
                         f'{len(constraints)} rules bound to this section</summary><ul>')
            for rule in section.get("caveats", []):
                parts.append(f'<li><code>{viz.esc(rule["rule"])}</code> '
                             f'{viz.esc(rule["guidance"])}</li>')
            for rule in section.get("not_computable", []):
                parts.append(f'<li class="never"><code>{viz.esc(rule["rule"])}</code> '
                             f'Never: {viz.esc(rule["reason"])}</li>')
            parts.append("</ul></details>")

        parts.append("</section>")

    parts.append("</article>")
    return "".join(parts)


REPORT_CSS = """
.pf-report { max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 40px; }
.pf-report header h1 { font-size: 26px; margin: 0 0 4px; font-weight: 600; letter-spacing: -.01em; }
.pf-report .sub { margin: 0; color: var(--pf-ink-2, #52514e); font-size: 14px; }
.pf-report section { display: flex; flex-direction: column; gap: 12px; }
.pf-report h2 {
  font-size: 15px; margin: 0; font-weight: 600; display: flex; gap: 10px; align-items: baseline;
  padding-bottom: 8px; border-bottom: 1px solid var(--pf-grid, #e1e0d9);
}
.pf-report h2 .n {
  font-variant-numeric: tabular-nums; color: var(--pf-muted, #898781);
  font-size: 12px; font-weight: 500;
}
.pf-report p { margin: 0; font-size: 14.5px; line-height: 1.6; max-width: 66ch; }
.pf-report .purpose {
  color: var(--pf-ink-2, #52514e); background: var(--pf-track, #e6e5df);
  padding: 12px 14px; border-radius: 4px; font-size: 13.5px;
}
.pf-report .absent { margin: 0; padding-left: 18px; font-size: 13.5px; color: var(--pf-ink-2, #52514e); }
.pf-report .chart {
  border: 1px solid var(--pf-grid, #e1e0d9); border-radius: 6px; padding: 16px; overflow-x: auto;
}
.pf-report table { border-collapse: collapse; font-size: 13px; width: 100%; }
.pf-report th {
  text-align: left; font-size: 10.5px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--pf-muted, #898781); border-bottom: 1px solid var(--pf-axis, #c3c2b7);
  padding: 6px 12px 6px 0;
}
.pf-report td {
  padding: 6px 12px 6px 0; border-bottom: 1px solid var(--pf-grid, #e1e0d9);
  font-variant-numeric: tabular-nums;
}
.pf-report .rules { font-size: 12.5px; color: var(--pf-ink-2, #52514e); }
.pf-report .rules summary { cursor: pointer; color: var(--pf-muted, #898781); font-size: 12px; }
.pf-report .rules ul { margin: 10px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 7px; }
.pf-report .rules code {
  font-size: 11px; padding: 1px 5px; border-radius: 3px;
  background: var(--pf-track, #e6e5df); margin-right: 6px;
}
.pf-report .rules .never code { background: var(--pf-warning, #fab219); color: #0b0b0b; }
"""
