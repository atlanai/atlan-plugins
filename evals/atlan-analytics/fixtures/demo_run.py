#!/usr/bin/env python3
"""The Fixit demo in the current folder, built from its dashboard only and kept on this machine.

    python3 demo_run.py <plugin-root>

A case's scaffold calls this, so a demo case can pick up a demo the way a person would after a
break: the run is there, and the model reads it with the skill's own helpers instead of being
handed its counts in the prompt. The demo opens, its requests are written, its data is loaded
and bound by the plugin's own scripts (`demo_data.py open` and `requests`, `project.py survey`,
`prepare` and `bind`), exactly as step 1 of the demo does. The built collection, 4 metrics and
4 saved queries, each run on the loaded data, is written as the build and its checks leave it.
The collection review and celebration have been shown; no question has been asked and no
dashboard built.
"""
import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(sys.argv[1]).resolve()
SCRIPTS = PLUGIN / "skills" / "analytics-build" / "scripts"
DEMO = PLUGIN / "skills" / "analytics-demo" / "scripts" / "demo_data.py"
sys.path.insert(0, str(SCRIPTS))
from context_state import digest  # noqa: E402

CWD = Path.cwd()
RUN = CWD / ".atlan-metrics" / "fixit-demo"


def run(*argv) -> dict:
    out = subprocess.run([sys.executable, *map(str, argv)], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip().startswith("{") else {}


def project(*argv) -> dict:
    return run(SCRIPTS / "project.py", "--run-dir", RUN, *argv)


def write(path: Path, body) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body, indent=2), encoding="utf-8")


folder = Path(run(DEMO, "open", "yard", CWD)["working_folder"])
project("survey", folder)
requests = run(DEMO, "requests", "yard", "--run-dir", RUN)
if requests.get("prepare"):
    project("prepare", requests["prepare"])
project("bind", requests["database"], "--schema", requests["schema"])


def metric(name, label, description, formula, filters, unit, dims=("project_name",)):
    return {"name": name, "display_name": label, "description": description,
            "definition": {"formula": formula, "mandatory_filters": filters, "grain": "project", "unit": unit,
                           "dimensions": list(dims),
                           "extensions": {"atlan_analytics": {"declared": {
                               "kind": "Power BI measure", "name": label, "ref": "powerbi/hard-hat-area-site-performance.pbit"}}}}}


def query(name, label, question, sql):
    return {"name": name, "display_name": label,
            "definition": {"question": question, "sql": sql,
                           "extensions": {"atlan_analytics": {"declared": {
                               "kind": "Power BI visual", "name": label, "ref": "powerbi/hard-hat-area-site-performance.pbit"}}}}}


METRICS = [
    metric("contract-value", "Contract value", "What clients agreed to pay for each job.", "SUM(p.contract_value_gbp)",
           [], "GBP"),
    metric("material-cost", "Material cost", "What the materials delivered to a job cost.",
           "SUM(m.quantity * m.unit_cost_gbp)", ["m.order_status = 'delivered'"], "GBP"),
    metric("wasted-material-cost", "Wasted material cost", "What the materials thrown away on a job cost.",
           "SUM(m.wasted_quantity * m.unit_cost_gbp)", ["m.order_status = 'delivered'"], "GBP"),
    metric("late-jobs", "Late jobs", "Finished jobs that ended after their planned end date.",
           "SUM(CASE WHEN p.actual_end_date > p.planned_end_date THEN 1 ELSE 0 END)", ["p.status = 'completed'"], "jobs",
           dims=("site_town",)),
]
QUERIES = [
    query("material-cost-by-job", "Material cost by job", "What did materials cost on each job?",
          "SELECT p.project_name, SUM(m.quantity * m.unit_cost_gbp) AS material_cost_gbp FROM site.material_orders m "
          "JOIN site.projects p ON p.project_id = m.project_id WHERE m.order_status = 'delivered' "
          "GROUP BY p.project_name ORDER BY material_cost_gbp DESC LIMIT 100"),
    query("waste-by-material", "Waste by material", "Which materials were wasted most?",
          "SELECT m.material, SUM(m.wasted_quantity * m.unit_cost_gbp) AS wasted_gbp FROM site.material_orders m "
          "WHERE m.order_status = 'delivered' GROUP BY m.material ORDER BY wasted_gbp DESC LIMIT 100"),
    query("late-jobs-by-town", "Late jobs by town", "Which towns had the most late jobs?",
          "SELECT p.site_town, COUNT(*) AS late_jobs FROM site.projects p WHERE p.status = 'completed' "
          "AND p.actual_end_date > p.planned_end_date GROUP BY p.site_town ORDER BY late_jobs DESC LIMIT 100"),
    query("snags-by-cause", "Snags by cause", "What caused the most snags?",
          "SELECT s.caused_by, COUNT(*) AS snags FROM site.site_snags s GROUP BY s.caused_by ORDER BY snags DESC LIMIT 100"),
]
write(RUN / "context" / "metrics.json", METRICS)
write(RUN / "context" / "queries.json", QUERIES)
write(RUN / "context" / "candidate-checks.json", {
    kind + ":" + body["name"]: {"definition_hash": digest(body["definition"]), "errors": [],
                                "status": "example_executed" if kind == "metric" else "executed"}
    for kind, bodies in (("metric", METRICS), ("sql_query", QUERIES)) for body in bodies})
write(RUN / "interaction.json", {
    "version": 1, "flow": "demo", "mode": "local", "asked": False,
    "intent": {"domain": "Job margin", "purpose": "Build a job profit agent that answers which jobs lost margin and why"}})
write(RUN / "progress.json", {"stage": "ready", "status": "complete"})
# The person chose the dashboard alone, and the build's moments up to the celebration were shown.
write(RUN / "engine-config.json", {"inputs": {"bi": True, "history": False}})
write(RUN / "view" / "state.json", {"moments": [{"kind": kind} for kind in (
    "connect", "found", "sources", "group", "tiles", "described", "drafted", "metrics", "rules", "links", "build",
    "context", "save")]})
