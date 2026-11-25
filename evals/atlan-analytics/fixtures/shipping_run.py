#!/usr/bin/env python3
"""A shipping project in the current folder, with a setup run that has already built its metrics.

    python3 shipping_run.py <plugin-root> local|preview|published

A case's scaffold calls this, so the case asks about saved work the way a person would, and the
model has to find the run and read it with the skill's own helpers instead of being handed the
answer in its prompt. The run sits at `.atlan-analytics/shipping-run/`, as a real one does. The
survey is the plugin's own (`project.py survey`); the built metrics, their checks and the
progress are written as the build and its checks leave them.

`local`: 3 metrics and 1 saved query, checked here, kept local, nothing published. Shipped units
and its query ran on a sample (start_date 2026-10-01); the other two are schema checked only.
`preview`: the same 3 metrics and query, all schema checked with SQL untested, no destination
chosen yet, and a preview.html the last session linked in place of showing a sample.
`published`: 7 metrics and 4 saved queries released in the Shipping workspace, and an 8th metric
(On-time rate) held locally because its denominator is undefined.
"""
import json
import subprocess
import sys
from pathlib import Path

PLUGIN, VARIANT = Path(sys.argv[1]).resolve(), sys.argv[2]
SCRIPTS = PLUGIN / "skills" / "analytics-build" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from context_state import digest  # noqa: E402

CWD = Path.cwd()
SOURCE, RUN = CWD / "shipping", CWD / ".atlan-analytics" / "shipping-run"


def write(path: Path, body) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body, indent=2), encoding="utf-8")


write(SOURCE / "models" / "shipments.yml", """version: 2
models:
  - name: shipments
    description: One row per shipment line.
    columns:
      - {name: region}
      - {name: status, description: "shipped, returned or cancelled"}
      - {name: units}
      - {name: weight_kg}
      - {name: shipping_cost}
      - {name: shipped_at}
      - {name: promised_at}
      - {name: arrived_at}
  - name: returns
    columns:
      - {name: region}
      - {name: units}
      - {name: returned_at}
""")
HISTORY = [
    ("SELECT region, SUM(units) AS shipped_units FROM shipping.shipments WHERE status = 'shipped' AND shipped_at >= '2026-10-01' GROUP BY region", 41),
    ("SELECT region, SUM(shipping_cost) FROM shipping.shipments WHERE status = 'shipped' GROUP BY region", 17),
    ("SELECT SUM(weight_kg) FROM shipping.shipments WHERE status = 'shipped'", 6),
]
write(SOURCE / "history" / "querylog.jsonl", "".join(json.dumps({"sql": sql, "exec": n}) + "\n" for sql, n in HISTORY))

subprocess.run([sys.executable, str(SCRIPTS / "project.py"), "--run-dir", str(RUN), "survey", str(SOURCE)],
               check=True, stdout=subprocess.DEVNULL)


def metric(name, label, description, formula, filters, unit, dims=("region",), declared="shipments", shapes=1):
    evidence = {"declared": {"kind": "dbt model", "name": declared, "ref": "models/shipments.yml"}} if declared else {}
    if shapes:
        evidence["shapes"] = ["shipping-%02d" % i for i in range(1, shapes + 1)]
    return {"name": name, "display_name": label, "description": description,
            "definition": {"formula": formula, "mandatory_filters": filters, "grain": "region", "unit": unit,
                           "dimensions": list(dims), "extensions": {"atlan_analytics": evidence}}}


SHIPPED = metric("shipped-units", "Shipped units", "Total units on shipped orders.", "SUM(s.units)",
                 ["s.status = 'shipped'"], "units")
METRICS = [SHIPPED,
           metric("shipping-cost", "Shipping cost", "What dispatching shipped orders cost.", "SUM(s.shipping_cost)",
                  ["s.status = 'shipped'"], "GBP")]
QUERY = {"name": "shipped-units-by-region", "display_name": "Shipped units by region",
         "definition": {"question": "How many units shipped by region?",
                        "sql": "SELECT region, SUM(units) AS shipped_units FROM shipping.shipments WHERE status = 'shipped' "
                               "AND shipped_at >= $start_date GROUP BY region LIMIT 100",
                        "parameters": [{"name": "start_date", "type": "date", "sample": "2026-10-01"}],
                        "extensions": {"atlan_analytics": {"shapes": ["shipping-01"]}}}}
QUERIES = [QUERY]
if VARIANT in ("local", "preview"):
    METRICS.append(metric("shipped-weight", "Shipped weight", "Total weight of shipped orders.", "SUM(s.weight_kg)",
                          ["s.status = 'shipped'"], "kg"))
else:
    METRICS += [metric("returned-units", "Returned units", "Units customers sent back.", "SUM(r.units)", [], "units",
                       declared="returns", shapes=0),
                metric("return-rate", "Return rate", "Returned units as a share of shipped units.",
                       "SUM(r.units) / NULLIF(SUM(s.units), 0)", ["s.status = 'shipped'"], "ratio", declared=None),
                metric("late-shipments", "Late shipments", "Shipments that arrived after the promised date.",
                       "SUM(CASE WHEN s.arrived_at > s.promised_at THEN 1 ELSE 0 END)", ["s.status = 'shipped'"], "shipments",
                       declared=None),
                metric("package-count", "Package count", "Shipments dispatched.", "COUNT(*)", ["s.status = 'shipped'"],
                       "shipments", shapes=0),
                metric("average-package-weight", "Average package weight", "Mean weight of a dispatched shipment.",
                       "SUM(s.weight_kg) / NULLIF(COUNT(*), 0)", ["s.status = 'shipped'"], "kg", shapes=0),
                metric("on-time-rate", "On-time rate", "Share of shipments on time.",
                       "SUM(on_time) / NULLIF(SUM(eligible), 0)", ["s.status = 'shipped'"], "ratio", declared=None)]
    QUERIES += [{"name": "shipping-cost-by-region", "display_name": "Shipping cost by region",
                 "definition": {"question": "What was the shipping cost by region?",
                                "sql": "SELECT region, SUM(shipping_cost) AS shipping_cost FROM shipping.shipments "
                                       "WHERE status = 'shipped' GROUP BY region LIMIT 100",
                                "extensions": {"atlan_analytics": {"shapes": ["shipping-02"]}}}},
                {"name": "shipped-weight-total", "display_name": "Shipped weight",
                 "definition": {"question": "What weight did we ship?",
                                "sql": "SELECT SUM(weight_kg) AS shipped_kg FROM shipping.shipments WHERE status = 'shipped'",
                                "extensions": {"atlan_analytics": {"shapes": ["shipping-03"]}}}},
                {"name": "returned-units-by-region", "display_name": "Returned units by region",
                 "definition": {"question": "How many units came back by region?",
                                "sql": "SELECT region, SUM(units) AS returned_units FROM shipping.returns GROUP BY region LIMIT 100"}}]
write(RUN / "context" / "metrics.json", METRICS)
write(RUN / "context" / "queries.json", QUERIES)

checks = {}
for kind, bodies in (("metric", METRICS), ("sql_query", QUERIES)):
    for body in bodies:
        check = {"definition_hash": digest(body["definition"]), "status": "schema_checked", "errors": []}
        if VARIANT == "published" or VARIANT == "local" and (body is SHIPPED or body is QUERY):
            check["status"] = "example_executed" if kind == "metric" else "executed"
            check["samples"] = {"start_date=2026-10-01": {"status": "executed", "rows": 4}} if body is QUERY else {}
        if body["name"] == "on-time-rate":
            check = {"definition_hash": digest(body["definition"]), "status": "held",
                     "errors": ["The denominator is undefined: no column says which shipments were eligible."]}
        checks[kind + ":" + body["name"]] = check
write(RUN / "context" / "candidate-checks.json", checks)

interaction = {"version": 1, "flow": "build", "mode": "local" if VARIANT == "local" else "active",
               "intent": {"domain": "Shipping", "purpose": "Track units shipped, returns and shipping cost by region"},
               "asked": False}
if VARIANT == "local":
    interaction["focus"] = "metric:shipped-units"
write(RUN / "interaction.json", interaction)
write(RUN / "progress.json", {"stage": "ready" if VARIANT == "published" else "preview", "status": "complete"})
if VARIANT == "preview":
    write(RUN / "preview.html", "<!doctype html><title>Preview</title><p>3 metrics and 1 query, local candidates.</p>")

if VARIANT == "published":
    state = json.loads((RUN / "run.json").read_text(encoding="utf-8"))
    released = [("metric", m) for m in METRICS if m["name"] != "on-time-rate"] + [("sql_query", q) for q in QUERIES]
    state["publication"] = {"workspace_id": "workspace_01evalshipping", "artifacts": [
        {"id": "%s_01evalship%02d" % (kind, i), "kind": kind, "name": body["name"], "release_id": "rel_01evalship%02d" % i,
         "version_id": "ver_01evalship%02d" % i, "channel": "latest", "definition_hash": digest(body["definition"]),
         "version": 1, "version_label": "0.1.0"} for i, (kind, body) in enumerate(released)]}
    write(RUN / "run.json", state)
    write(RUN / "context" / "plan.json", {"counts": {"metrics": {"create": 7}, "queries": {"create": 4}, "links": 0}, "links": []})
    write(RUN / "saved.json", {"workspace": "Shipping"})
