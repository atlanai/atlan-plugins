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
from contextlib import nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parent / "dashboard_runtime"))
from trident.core import Service, clean, digest, require
from trident.contracts import SCHEMAS, DESCRIPTIONS, validate
from trident.evaluation import validation_status, readiness, usable_metric
from dashboard_errors import FlowError, public_error


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
    marker = r"\$(p\d+)\b" if translation.get('binding_style') == 'named_dollar' else r":(p\d+)\b"
    sql = re.sub(marker, lambda m: "{{" + m[1] + "}}", sql)
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
        if state['status'] == 'evaluated' and not usable_metric(state, metric):
            held.append({'metric_id': metric['id'], 'reason': 'requires_correction',
                         'validation': validation_status(state, metric)})
            continue
        body = portable_metric(metric)
        body['definition']['extensions']['atlan_analytics']['dashboard_validation'] = validation_status(state, metric)
        body["source_version"] = state["capture_hash"]
        metrics.append(body)
    require(bool(metrics), "No warehouse metrics are ready to prepare.")
    for name, value in {"metrics.json": metrics, "queries.json": [], "links.json": [],
                        "dashboard-source.json": {"job_id": job_id, "capture_hash": state["capture_hash"],
                        "definitions_hash": digest([{k: m.get(k) for k in ('id', 'definition', 'translation')}
                                                    for m in state.get('metrics', [])]),
                        "context_hash": state["context_hash"], "held": held,
                        "readiness": readiness(state), "validation": service.view(state)["summary"]}}.items():
        require(not (root / name).is_symlink(), "Use ordinary context files.")
        service.write(root / name, value)
    return {"status": "ok", "metrics": len(metrics), "held": held, "context_dir": str(root),
            "next": "Read live metric schema and workspace artifacts, save existing.json, then run metrics_mine.py check.",
            "remote_writes": False}


def run(args):
    if args.command in ('start', 'status', 'stage-begin', 'stage-ack'):
        from dashboard_run import Run
        if not args.run_dir:
            raise FlowError('invalid_request')
        tracker = Run(args.run_dir)
        if args.command == 'start':
            return tracker.start(args.url)
        if args.command == 'status':
            return tracker.status()
        if args.command == 'stage-begin':
            return tracker.begin_stage(args.stage, load(args.request))
        return tracker.acknowledge(args.stage, load(args.receipt))
    if args.command == "schema":
        require(args.operation in SCHEMAS, "Unknown dashboard operation.")
        return {"name": args.operation, "description": DESCRIPTIONS[args.operation], "inputSchema": SCHEMAS[args.operation]}
    service = Service(args.workspace)
    if args.command == "call":
        payload = load(args.request)
        if args.run_dir:
            from dashboard_run import Run
            job = Run(args.run_dir).manifest()['job_id']
            if args.operation == 'dashboard_begin' or payload.get('job_id', job) != job:
                raise FlowError('run_mismatch')
            payload['job_id'] = job
        validate(args.operation, payload)
        return service.dispatch(args.operation, payload)
    if args.command == "prepare-context":
        with service.lock():
            return prepare_context(service, args.job, args.context)
    if args.command == "registry-inspect":
        from registry_inspection import ReadCLI, inspect_contract
        selected = load(args.target)
        try:
            observed = inspect_contract(ReadCLI(), selected)
        except ValueError as exc:
            code = str(exc)
            allowed = {'registry_not_installed','registry_response_limits','registry_invalid_response',
                       'registry_kind_contract_mismatch','registry_workspace_contract_mismatch',
                       'registry_metric_unsupported','registry_auth_required','registry_permission_denied',
                       'registry_call_failed','registry_target_changed','invalid_arguments'}
            return {'status': 'error', 'code': code if code in allowed else 'registry_call_failed',
                    'remote_writes': False, 'next': 'Resolve this specific inspection error; do not infer a login or permission failure from a schema mismatch.'}
        root = Path(args.output).absolute()
        require(not any(p.is_symlink() for p in (root, *root.parents)), 'Use an ordinary inspection file.')
        service.write(root, observed)
        return {**{k:v for k,v in observed.items() if k != '_manifest'},
                'status':'ok','schema_file':str(root),'remote_writes':False,
                'next':'Read the complete metric inventory and prepare the publication plan. This read grants no new access and writes nothing to Registry.'}
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
    parser.add_argument('--run-dir', help='One private dashboard run directory; overrides the legacy workspace.')
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser('start')
    start.add_argument('--url', required=True)
    commands.add_parser('status')
    for name in ('stage-begin', 'stage-ack'):
        stage = commands.add_parser(name)
        stage.add_argument('--stage', choices=['agent', 'heldout_eval'], required=True)
        stage.add_argument('--request' if name == 'stage-begin' else '--receipt', required=True)
    schema = commands.add_parser("schema")
    schema.add_argument("operation", choices=SCHEMAS)
    call = commands.add_parser("call")
    call.add_argument("operation", choices=SCHEMAS)
    call.add_argument("--request", required=True)
    context = commands.add_parser("prepare-context")
    context.add_argument("--job")
    context.add_argument("--context")
    inspect = commands.add_parser("registry-inspect")
    inspect.add_argument("--target", required=True)
    inspect.add_argument("--output", required=True)
    for name in ("registry-plan", "registry-next", "registry-ack", "registry-recover"):
        sub = commands.add_parser(name)
        sub.add_argument("--context")
        if name == "registry-plan":
            sub.add_argument("--snapshot", required=True)
        elif name == "registry-next":
            sub.add_argument("--approved-plan", required=True)
        else:
            sub.add_argument("--receipt", required=True)
    args = parser.parse_args()
    operation = args.command + ':' + getattr(args, 'operation', '')
    tracker = None
    try:
        if args.run_dir:
            from dashboard_run import Run
            tracker = Run(args.run_dir)
            args.workspace = str(tracker.jobs)
            if hasattr(args, 'context'):
                if args.context and Path(args.context).absolute() != tracker.context:
                    raise FlowError('run_mismatch')
                args.context = str(tracker.context)
            if hasattr(args, 'job'):
                job = tracker.manifest()['job_id']
                if args.job and args.job != job:
                    raise FlowError('run_mismatch')
                args.job = job
        if args.command == 'prepare-context' and (not args.job or not args.context):
            raise FlowError('invalid_request')
        if args.command.startswith('registry-') and args.command != 'registry-inspect' and not args.context:
            raise FlowError('invalid_request')
        with tracker.store.lock() if tracker else nullcontext():
            result = run(args)
            if tracker and tracker.path.exists() and args.command not in ('start', 'status', 'schema'):
                if result.get('status') not in ('error', 'needs_attention', 'BLOCKED'):
                    tracker.resolve_issue(operation)
                progress = tracker.status()['workflow']
                result['workflow'] = progress
                if 'registry_status' in result:
                    result['registry_status'] = progress['publication']['state']
        print(json.dumps(result, allow_nan=False))
        return 2 if result.get("status") in ('error', 'needs_attention', 'BLOCKED') else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = public_error(exc)
        result['operation'] = operation
        if tracker:
            try:
                with tracker.store.lock():
                    tracker.issue(result)
            except (ValueError, OSError, KeyError, TypeError):
                pass
        print(json.dumps(result))
        return 2


if __name__ == "__main__":
    sys.exit(main())
