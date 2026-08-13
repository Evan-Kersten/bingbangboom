#!/usr/bin/env python3
"""Smoke tests for the app's API, without a browser or a running server.

Calls the handlers directly. What matters here is that a preset returns real
figures and the rules that bind them, and that the interface never offers a
question the data cannot answer.

    python3 app/test_server.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server as S

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


def main():
    store = S.STORE

    def pid(legal_name):
        return store.row("SELECT pid6 FROM entities WHERE legal_name=?", legal_name)["pid6"]

    sumpter = pid("CITY OF SUMPTER")
    baker = pid("COUNTY OF BAKER")
    new_bridge = pid("NEW BRIDGE WATER SUPPLY DISTRICT")

    print("\nevery preset answers without raising")
    for preset_id, label, _, _, _ in S.PRESETS:
        result = S.answer_preset(preset_id, baker)
        check(f"{preset_id} returns blocks", bool(result["blocks"]), label)

    print("\npresets carry their rules and their trace")
    result = S.answer_preset("driver", sumpter)
    codes = {r["code"] for r in result["rules"]}
    check("the debt cap reaches the interface", "debt_capped" in codes)
    check("the prior-year artifact reaches the interface", "prior_year_missing" in codes)
    check("a never-rule is marked as one",
          any(r["kind"] == "never" for r in result["rules"]))
    check("the tool trace is reported", "get_financial_position" in result["trace"])
    check("a chart is returned", any(b["kind"] == "chart" for b in result["blocks"]))
    check("the headline reads the real score",
          any("60 of 100" in b.get("text", "") for b in result["blocks"]),
          str(result["blocks"][0]))

    print("\nrefusals survive into the interface")
    result = S.answer_preset("peers", new_bridge)
    codes = {r["code"] for r in result["rules"]}
    check("a degenerate peer median still refuses here",
          "peer_median_degenerate" in codes)
    check("and the refusal is drawn rather than dropped",
          any(b["kind"] == "chart" and "not a midpoint" in b["svg"].lower()
              for b in result["blocks"]))

    print("\nthe ecosystem answer names who is filed elsewhere")
    result = S.answer_preset("ecosystem", None, county="Multnomah")
    text = " ".join(b.get("text", "") for b in result["blocks"])
    check("it reports the count", "governments are filed under" in text)
    check("it names the cross-boundary set", "filed under a neighbouring county" in text)
    check("and includes a map", any(b["kind"] == "chart" for b in result["blocks"]))

    print("\nreports render into the thread")
    result = S.answer_preset("report", baker)
    report = next((b for b in result["blocks"] if b["kind"] == "report"), None)
    check("a report block is returned", report is not None)
    check("with several sections", report and report["sections"] >= 5)
    check("and a stated word budget", report and report["budget"] > 0)

    print("\npresets are gated by the availability manifest")
    row = store.row("SELECT * FROM data_availability WHERE pid6=?", new_bridge)
    gated = [(label, bool(dict(row).get(needs)))
             for _, label, _, needs, _ in S.PRESETS if needs]
    check("at least one preset is withheld for a sparse entity",
          any(not available for _, available in gated), str(gated))
    check("and spending is the one withheld here",
          not dict(row)["has_spending_breakdown"])

    print("\nan entity with nothing to show says so")
    result = S.answer_preset("spending", new_bridge)
    text = " ".join(b.get("text", "") for b in result["blocks"])
    check("absence is not reported as zero",
          "absence of data" in text or "no spending breakdown" in text.lower(), text[:90])

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print(f"\nFAILED: {len(failures)}")
        for label in failures:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
