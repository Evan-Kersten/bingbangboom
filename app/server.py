#!/usr/bin/env python3
"""Local server for the Public Foundry chat UI.

    python3 app/server.py            then open http://localhost:8000

Standard library only, no build step, no dependencies.

Runs in one of two modes, and says which in the interface rather than pretending.

    grounded   No model. Presets map directly to tool calls, so every figure,
               chart and rule is real; what is missing is the prose that reads
               them back. This is the whole product minus the sentence.

    full       ANTHROPIC_API_KEY is set and the anthropic package is installed.
               Free-text questions go through the orchestrator and the model
               writes the answer against the same tool results.

The mode boundary is deliberate. Everything except the wording is deterministic
and testable, so a wrong answer in grounded mode is a bug in the tool layer, and
a wrong answer only in full mode is a model adherence problem. That is the same
delivery-versus-adherence split the eval harness reports.
"""

import http.server
import json
import os
import socketserver
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agent"))

import format as fmt          # noqa: E402
import reports as R           # noqa: E402
import tools as T             # noqa: E402
import viz                    # noqa: E402

STORE = T.Store()

# Appendix A, gated by the availability manifest the ETL computed. A preset is
# only offered when the block it needs is present, which is what the prompt
# already asks for and what stops the interface promising an answer the data
# cannot give.
PRESETS = [
    # (id, label, group, required availability key, handler)
    ("profile", "What kind of government is this?", "Orientation", None, "profile"),
    ("finance", "What should I know about its finances?", "Orientation",
     "has_fiscal_stability", "finance"),
    ("spending", "Where does the money actually go?", "Orientation",
     "has_spending_breakdown", "spending"),
    ("salient", "What stands out about this government?", "What stands out",
     "has_fiscal_stability", "salient"),
    ("driver", "What is driving the fiscal stability score?", "Financial position",
     "has_fiscal_stability", "driver"),
    ("peers", "Where does it differ most from its peers?", "Financial position",
     "has_operating_vs_capital", "peers"),
    ("trend", "How has revenue and spending moved?", "Financial position",
     "has_financial_trends", "trend"),
    ("workforce", "How is the workforce distributed?", "Workforce",
     "has_workforce", "workforce"),
    ("governance", "Who governs this entity?", "Governance", "has_offices", "governance"),
    ("ecosystem", "Which governments serve this place?", "Ecosystem", None, "ecosystem"),
    ("report", "Give me the full report", "Reports", None, "report"),
    ("limits", "What can this data not tell me?", "Data limits", None, "limits"),
]


def model_client():
    """Return an orchestrator with a model attached, or None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import orchestrator as O
        model = os.environ.get("PF_MODEL", "claude-opus-5")
        return O.Orchestrator(store=STORE, client=O.anthropic_client(model))
    except Exception:
        return None


AGENT = model_client()
MODE = "full" if AGENT else "grounded"


# ------------------------------------------------------------- handlers

def _headline(entity, result):
    """One sentence of real numbers, assembled from the tool result.

    Deliberately templated rather than generated. In grounded mode the interface
    says a model wrote nothing, so this must read as a readout, not as prose.
    """
    data = result["data"]
    name = entity["common_name"] or entity["legal_name"]
    tool = result["tool"]

    if tool == "get_financial_position" and data.get("score") is not None:
        driver = {"debt_to_revenue": "debt to revenue",
                  "structural_balance": "structural balance"}.get(data.get("driver"), "")
        return (f"Fiscal stability {data['score']:.0f} of 100, rated {data['rating_label']}. "
                f"The lower component is {driver}.")
    if tool == "get_spending_breakdown" and data.get("service_areas"):
        top = data["service_areas"][0]
        return (f"Largest reported area is {top['service_area']} at "
                f"{fmt.money(top['total'])}, {fmt.percent(top['percentage'])} of spending.")
    if tool == "get_workforce" and data.get("total_employees") is not None:
        return (f"{fmt.count(data['total_employees'])} employees, average wage "
                f"{fmt.money(data.get('average_annual_wage'))}.")
    if tool == "find_salient":
        if data.get("nothing_unusual"):
            return "Nothing in this profile clears an attention threshold."
        lead = data["lead"]
        return f"Most unusual: {lead['finding']}."
    if tool == "get_offices" and data.get("total_seats"):
        return (f"{data['total_seats']} seats recorded across "
                f"{len(data['roles'])} roles. No holder names are in this data.")
    if tool == "list_ecosystem":
        return (f"{data['total']} governments are filed under {data['county']}, "
                f"of which {data['mappable']} have boundaries.")
    if tool == "get_entity_profile":
        population = data.get("population")
        return (f"{entity['gov_type_name']}"
                + (f", population {fmt.count(population)}" if population else "")
                + f", host county {(entity['host_county'] or '').title()}.")
    return None


def answer_preset(preset_id, pid6=None, county=None):
    """Run a preset. Returns blocks the UI renders in order."""
    entity = T._entity(STORE, pid6) if pid6 else None
    blocks, results = [], []

    def add(result, chart_form=None):
        results.append(result)
        headline = _headline(entity, result) if entity else None
        if not headline:
            # A tool with nothing to return says why. Leading with that sentence
            # is better than dropping the reader straight into a refusal drawing.
            absence = next((c for c in result["caveats"]
                            if c["code"].startswith("no_")), None)
            if absence:
                headline = absence["guidance"]
        if headline:
            blocks.append({"kind": "text", "text": headline})
        if chart_form and entity:
            chart = T.render_chart(STORE, pid6, chart_form)
            results.append(chart)
            if chart["data"].get("svg"):
                blocks.append({"kind": "chart", "svg": chart["data"]["svg"],
                               "table": chart["data"].get("table")})

    if preset_id == "profile":
        add(T.get_entity_profile(STORE, pid6))
    elif preset_id in ("finance", "driver"):
        add(T.get_financial_position(STORE, pid6), "stability_components")
    elif preset_id == "spending":
        add(T.get_spending_breakdown(STORE, pid6), "spending_composition")
    elif preset_id == "workforce":
        add(T.get_workforce(STORE, pid6), "workforce_composition")
    elif preset_id == "trend":
        add(T.get_financial_position(STORE, pid6), "finances_over_time")
    elif preset_id == "peers":
        add(T.compare_to_peers(STORE, pid6, "capital_share"), "peer_position")
    elif preset_id == "salient":
        add(T.find_salient(STORE, pid6))
    elif preset_id == "governance":
        add(T.get_offices(STORE, pid6))
        result = results[0]
        if result["data"].get("roles"):
            blocks.append({"kind": "table",
                           "rows": [{"role": r["role"], "seats": r["seat_count"]}
                                    for r in result["data"]["roles"]]})
    elif preset_id == "ecosystem":
        name = county or (entity["host_county"] if entity else None)
        result = T.list_ecosystem(STORE, county_name=name)
        results.append(result)
        data = result["data"]
        if data:
            blocks.append({"kind": "text",
                           "text": f"{data['total']} governments are filed under "
                                   f"{data['county']}, of which {data['mappable']} have "
                                   f"boundaries in this data."})
            blocks.append({"kind": "table",
                           "rows": [{"type": k, "count": v}
                                    for k, v in sorted(data["by_type"].items())]})
            if data.get("also_serving_filed_elsewhere"):
                blocks.append({
                    "kind": "text",
                    "text": f"{len(data['also_serving_filed_elsewhere'])} more operate here "
                            "but are filed under a neighbouring county, so a host-county "
                            "list misses them:"})
                blocks.append({"kind": "table", "rows": [
                    {"government": e["common_name"] or e["legal_name"],
                     "type": e["gov_type_name"],
                     "filed under": (e["filed_county"] or "").title()}
                    for e in data["also_serving_filed_elsewhere"]]})
            county_map = T.render_map(STORE, "place", "fiscal_stability",
                                      county=(data["county"] or "").split()[0])
            results.append(county_map)
            if county_map["data"].get("svg"):
                blocks.append({"kind": "chart", "svg": county_map["data"]["svg"]})
    elif preset_id == "limits":
        result = T.get_entity_profile(STORE, pid6) if pid6 else T.envelope("scope")
        results.append(result)
        available = result["data"].get("available") or {}
        absent = [k.replace("has_", "").replace("_", " ")
                  for k, v in available.items() if not v]
        blocks.append({"kind": "text",
                       "text": "This data measures what governments spend and who they "
                               "employ. It contains no performance measures, service "
                               "levels or results."})
        if absent:
            blocks.append({"kind": "list", "items": [f"No {a}" for a in absent]})
    elif preset_id == "report":
        kind = "entity" if pid6 else "place"
        plan = R.compose_report(STORE, kind=kind, pid6=pid6,
                                county_name=county or (entity["host_county"] if entity else None))
        results.append(plan)
        if plan["data"]:
            blocks.append({"kind": "report", "html": R.render_report(plan),
                           "sections": len(plan["data"]["sections"]),
                           "budget": plan["data"]["total_word_budget"]})
    else:
        return {"blocks": [{"kind": "text", "text": f"No preset '{preset_id}'."}],
                "rules": [], "trace": []}

    rules, seen = [], set()
    for result in results:
        for rule in result.get("caveats", []):
            if rule["code"] not in seen:
                seen.add(rule["code"])
                rules.append({**rule, "kind": "must"})
        for rule in result.get("not_computable", []):
            if rule["code"] not in seen:
                seen.add(rule["code"])
                rules.append({"code": rule["code"], "rule": rule["rule"],
                              "guidance": rule["reason"], "kind": "never"})

    if not blocks:
        blocks = [{"kind": "text",
                   "text": "The data has nothing to show for that here. "
                           "That is an absence of data, not a report of zero."}]

    return {"blocks": blocks, "rules": rules,
            "trace": [r["tool"] for r in results]}


# ------------------------------------------------------------------ http

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/mode":
            return self._json({"mode": MODE,
                               "entities": STORE.row(
                                   "SELECT COUNT(*) AS n FROM entities")["n"],
                               "css": viz.css(".pf-viz") + R.REPORT_CSS})

        if parsed.path == "/api/search":
            term = (query.get("q") or [""])[0].strip()
            if len(term) < 2:
                return self._json({"matches": []})
            result = T.find_entity(STORE, term, limit=12)
            return self._json({"matches": result["data"]["matches"],
                               "ambiguous": any(c["code"] == "ambiguous_name"
                                                for c in result["caveats"])})

        if parsed.path == "/api/presets":
            pid6 = (query.get("pid6") or [None])[0]
            available = {}
            if pid6:
                row = STORE.row("SELECT * FROM data_availability WHERE pid6=?", pid6)
                available = dict(row) if row else {}
            entity = T._entity(STORE, pid6) if pid6 else None
            return self._json({
                "entity": entity,
                "presets": [
                    {"id": pid, "label": label, "group": group,
                     "available": (needs is None or bool(available.get(needs)))}
                    for pid, label, group, needs, _ in PRESETS]})

        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or "{}")
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/preset":
            return self._json(answer_preset(payload.get("id"), payload.get("pid6"),
                                            payload.get("county")))

        if parsed.path == "/api/ask":
            question = (payload.get("question") or "").strip()
            if not AGENT:
                return self._json({
                    "blocks": [{"kind": "text",
                                "text": "Free-text questions need a model. Set "
                                        "ANTHROPIC_API_KEY and install the anthropic "
                                        "package, then restart. Every preset works now, "
                                        "and the figures, charts and rules they return are "
                                        "the same ones the model would be given."}],
                    "rules": [], "trace": []})
            answer, trace = AGENT.answer(question)
            return self._json({
                "blocks": [{"kind": "text", "text": answer}],
                "rules": [], "trace": [c["tool"] for c in trace["calls"]],
                "question_type": trace["question_type"],
                "delivered": sorted(set(trace["caveats_delivered"]))})

        return self._json({"error": "unknown endpoint"}, 404)


def main():
    port = int(os.environ.get("PORT", 8000))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), Handler) as server:
        print(f"Public Foundry, {MODE} mode, http://localhost:{port}")
        if MODE == "grounded":
            print("No ANTHROPIC_API_KEY, so presets work and free text does not.")
        server.serve_forever()


if __name__ == "__main__":
    main()
