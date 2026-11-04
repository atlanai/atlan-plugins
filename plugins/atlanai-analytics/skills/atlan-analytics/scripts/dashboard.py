#!/usr/bin/env python3
"""One-shot local dashboard helper. Registry calls stay in the host's OAuth connection.

No daemon, listener, MCP registration, credential reader or automatic dependency install.
The original companion MCP UI can use the same job directory, but is never required.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "dashboard_runtime"))
from trident.core import Service, clean, digest, require
from trident.contracts import SCHEMAS, DESCRIPTIONS, validate


def load(path):
    file = Path(path)
    require(file.is_file() and not file.is_symlink() and file.stat().st_size <= 4_000_000,
            "Use an ordinary JSON file smaller than 4 MB.")
    return clean(json.loads(file.read_text()))


def portable_metric(metric):
    """Adapt a scalar query to native metric composition without executing it per source row."""
    require(metric.get("runtime") == "warehouse", "Resolve this measure's SQL before publishing it.")
    definition = copy.deepcopy(metric["definition"])
    translation = metric["translation"]
    sql = translation["sql"]
    parameters = translation["parameters"]
    # Compiler parameters occur outside quoted literals: values are never interpolated into SQL.
    sql = re.sub(r":(p\d+)\b", lambda m: "{{" + m[1] + "}}", sql)
    definition["formula"] = 'dashboard_value."value"'
    definition["aggregation"] = {"type": "custom", "reaggregation": "none"}
    definition["source"] = {
        "entity": definition["source"]["entity"],
        "sql": "(" + sql + ") AS dashboard_value",
        "alias": "dashboard_value",
        "parameters": [{"name": key, "datatype": "boolean" if type(value) is bool else
                        "decimal" if type(value) in (int, float) else "string", "default": value}
                       for key, value in parameters.items()],
    }
    definition["examples"] = [{"source": "declared_dashboard", "sql": sql, "parameters": parameters}]
    definition["extensions"]["atlan_analytics"] = {
        "declared": [metric["source_id"]], "check": "untested",
        "execution": "scalar derived relation; evaluate once at the captured scope",
    }
    return {"name": metric["id"], "display_name": metric["name"],
            "description": metric.get("description") or "The dashboard measure at its captured reporting scope.",
            "source_type": "external", "source_id": metric["source_id"], "definition": definition}


def prepare_context(service, job_id, destination):
    state = service.load(job_id)
    require(state["scope_complete"], "Complete filters, security scope and data cutoff before preparing context.")
    root = Path(destination).absolute()
    require(not any(p.is_symlink() for p in (root, *root.parents)), "Use an ordinary context directory.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    metrics, held = [], []
    for metric in state["metrics"]:
        if metric.get("runtime") != "warehouse":
            held.append({"metric_id": metric["id"], "reason": "needs_sql"})
            continue
        body = portable_metric(metric)
        body["source_version"] = state["capture_hash"]
        metrics.append(body)
    require(bool(metrics), "No warehouse metrics are ready to prepare.")
    for name, value in {"metrics.json": metrics, "queries.json": [], "links.json": [],
                        "dashboard-source.json": {"job_id": job_id, "capture_hash": state["capture_hash"],
                        "context_hash": state["context_hash"], "held": held}}.items():
        require(not (root / name).is_symlink(), "Use ordinary context files.")
        service.write(root / name, value)
    return {"status": "ok", "metrics": len(metrics), "held": held, "context_dir": str(root),
            "next": "Read live metric schema and workspace artifacts, save existing.json, then run metrics_mine.py check.",
            "remote_writes": False}


def run(args):
    if args.command == "schema":
        require(args.operation in SCHEMAS, "Unknown dashboard operation.")
        return {"name": args.operation, "description": DESCRIPTIONS[args.operation], "inputSchema": SCHEMAS[args.operation]}
    service = Service(args.workspace)
    if args.command == "call":
        payload = load(args.request)
        validate(args.operation, payload)
        return service.dispatch(args.operation, payload)
    if args.command == "prepare-context":
        with service.lock():
            return prepare_context(service, args.job, args.context)
    if args.command.startswith("registry-"):
        from dashboard_publish import Publisher
        publisher = Publisher(Path(args.context))
        with service.lock():
            if args.command == "registry-plan":
                return publisher.plan(load(args.snapshot))
            if args.command == "registry-next":
                return publisher.next(args.approved_plan)
            return publisher.ack(load(args.receipt), recover=args.command == "registry-recover")
    raise ValueError("Unknown command.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=str(Path.home() / ".trident/dashboard-jobs"))
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema")
    schema.add_argument("operation", choices=SCHEMAS)
    call = commands.add_parser("call")
    call.add_argument("operation", choices=SCHEMAS)
    call.add_argument("--request", required=True)
    context = commands.add_parser("prepare-context")
    context.add_argument("--job", required=True)
    context.add_argument("--context", required=True)
    for name in ("registry-plan", "registry-next", "registry-ack", "registry-recover"):
        sub = commands.add_parser(name)
        sub.add_argument("--context", required=True)
        if name == "registry-plan":
            sub.add_argument("--snapshot", required=True)
        elif name == "registry-next":
            sub.add_argument("--approved-plan", required=True)
        else:
            sub.add_argument("--receipt", required=True)
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(result, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        # Payloads and filesystem errors may contain SQL or private source values.
        print(json.dumps({"status": "BLOCKED", "reason": "Dashboard operation could not be completed.",
                          "fix": "Check the command schema and saved job; preserve the input and retry only after resolving the cause."}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
