"""Executed only in the isolated module environment. Engine stages use the maintained common contracts."""
import argparse
import contextlib
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import traceback

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from driver import ordinary, snapshot, write
from terminal_io import Transcript, at_terminal, run as run_with_terminal


def load(path):
    return json.loads(ordinary(path).read_text())


def digest(path):
    with ordinary(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def stage_input(source, destination, source_id=None):
    """Read the selected asset once, then keep all further processing in the project."""
    source, destination = ordinary(source), ordinary(destination)
    if not source.exists():
        raise ValueError('The selected source does not exist.')
    if source == destination or (source.is_dir() and destination.is_relative_to(source)):
        raise ValueError('Keep the private capture directory outside the selected source.')
    if source_id is not None and (not isinstance(source_id, str) or not source_id.strip()
                                  or any(ord(c) < 32 for c in source_id)):
        raise ValueError('Use a nonempty stable source identity from the selected asset metadata.')
    namespace = 'asset-' + hashlib.sha256(source_id.encode()).hexdigest()[:24] if source_id is not None else None

    def relative(path):
        if source.is_file():
            name = namespace + source.suffix.lower() if namespace else source.name
            return Path(name + ('.manifest.json' if path != source else ''))
        return (Path(namespace) if namespace else Path()) / path.relative_to(source)

    files = [source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file() or p.is_symlink())
    for path in files:
        ordinary(path)
        if path.name == 'staged-input.json':
            raise ValueError('The selected source uses the reserved capture manifest name.')
        if any(part in ('.git', '.aws', '.ssh') for part in path.relative_to(source.parent if source.is_file() else source).parts):
            raise ValueError('Select the BI definitions folder, excluding credential or repository metadata.')
        if path.name == '.env' or path.name.startswith('.env.') or path.suffix.lower() in ('.pem', '.key', '.p12', '.pfx', '.keychain'):
            raise ValueError('The selected folder contains credential material; select only BI definitions.')
    sidecar = source.with_name(source.name + '.manifest.json')
    if source.is_file() and sidecar.is_file():
        ordinary(sidecar)
        files.append(sidecar)
    if not files:
        raise ValueError('The selected source folder is empty.')
    marker = destination / 'staged-input.json'
    if destination.exists() and any(destination.iterdir()):
        if (not marker.is_file() or load(marker).get('source_id') != source_id
                or (source_id is None and load(marker).get('source') != str(source))):
            raise ValueError('Use an empty capture directory or resume the same selected source.')
        recorded = {item['path']: item['sha256'] for item in load(marker)['files']}
        selected = {relative(path).as_posix(): digest(path)
                    for path in files}
        captured = {path.relative_to(destination).as_posix() for path in destination.rglob('*')
                    if path.is_file() and path != marker}
        if selected != recorded or captured != set(recorded):
            raise ValueError('The selected input changed; use a new source capture directory.')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    for path in files:
        rel = relative(path)
        target = ordinary(destination / rel)
        if target.exists() and digest(target) != digest(path):
            raise ValueError('The selected input changed; use a new source capture directory.')
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target)
            target.chmod(0o600)
        records.append({'path': rel.as_posix(), 'sha256': digest(target)})
    write(destination / 'staged-input.json', {'source': str(source), 'source_id': source_id, 'files': records})
    return {'status': 'staged', 'path': str(destination / relative(source) if source.is_file() else destination / namespace if namespace else destination),
            'files': len(records), 'remote_writes': False}


def engine_identity():
    import metrics_agent
    package = Path(metrics_agent.__file__).parent
    data, sha = snapshot()
    for name, item in data['files'].items():
        if name.startswith('src/metrics_agent/'):
            installed = package / name.removeprefix('src/metrics_agent/')
            if not installed.is_file() or digest(installed) != item['sha256']:
                raise ValueError('Installed engine differs from the pinned source; recreate this runtime.')
    return data['commit'], sha


def discover():
    from metrics_agent import setup
    from metrics_agent import plugs
    detected = setup.detect()
    return {'status': 'ok', 'module': 'metrics', 'plugs': plugs.listing(),
            'requirements': [{'plug': p.name, 'kind': p.kind, 'custody': p.custody,
                              'custodian': p.custodian,
                              'credential_routes': [dataclasses.asdict(r) for r in p.routes()],
                              'setup_questions': {role: [dataclasses.asdict(q) for q in questions]
                                                  for role, questions in
                                                  (getattr(p.cls, 'setup_questions', {}) or {}).items()}}
                             for p in detected.plugs.values()],
            'note': 'Reuse known answers. Credentials remain in the environment; these are variable names only.'}


def local_config(path):
    from metrics_agent import run_config
    doc = run_config.load(path)
    if doc['publish'] != ['files'] or doc.get('observers'):
        raise ValueError('This module requires publish: [files] and no observers. The plugin owns publication.')
    return doc


def optional_readers_missing(doc):
    """Requirements for BI files this runtime cannot read, named before the run starts.

    A `.pbix` model is read through the optional `pbix` feature. Without it the run used to
    stop at its BI stage with a missing-module error, after the history lane had run. A
    `.pbix` that holds no model (a live-connection report) needs no reader: the run leaves it
    out and says why."""
    import importlib.util
    out = []
    for plug, settings in (doc.get('settings') or {}).items():
        bi = (settings or {}).get('bi') if isinstance(settings, dict) else None
        if not bi or not str(bi).lower().endswith('.pbix'):
            continue
        from metrics_agent import readers
        path = Path(str(bi)).expanduser()
        if readers.model_gap(path) is None and importlib.util.find_spec('pbixray') is None:
            out.append(f'settings.{plug}.bi is a .pbix ({path.name}); reading it needs the '
                       '`pbix` feature, which this runtime was set up without.')
    return '\n'.join(out)


def prepare(run, config, runtime, approval_mode='auto'):
    from metrics_agent import setup
    run, config = ordinary(run), ordinary(config)
    doc = local_config(config)
    if (run / 'module.json').exists():
        state(run, runtime)
        receipt = load(run / 'module.json')
        if receipt['config_sha256'] != digest(config) or receipt['approval_mode'] != approval_mode:
            raise ValueError('Configuration changed; review it and start a new metrics run.')
        return {'status': 'prepared', 'run': str(run), 'reused': True}
    if run.exists() and any(run.iterdir()):
        raise ValueError('Use a new module run directory; existing engine runs require an explicit migration.')
    missing = optional_readers_missing(doc)
    if missing:
        return {'status': 'NEEDS_CONTEXT', 'requirements': missing,
                'fix': 'Set up a new runtime with the named feature and prepare this run with it.'}
    # check_file performs the original plug/session checks, but never asks the setup interview.
    messages = io.StringIO()
    stream = Transcript(messages, sys.stderr) if at_terminal() else messages
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        code = setup.check_file(config, out=messages)
    if code:
        # Access/credential checks may name environment variables, not their values.
        return {'status': 'NEEDS_CONTEXT', 'requirements': messages.getvalue(),
                'fix': 'Resolve only these missing settings or explicitly choose parse-only verification.'}
    run.mkdir(parents=True, mode=0o700, exist_ok=True)
    # JSON is valid YAML. Keep resolved absolute input paths from the approved configuration.
    write(run / 'run.yml', doc)
    commit, sha = engine_identity()
    write(run / 'module.json', {'module': 'metrics', 'commit': commit, 'source_sha256': sha,
                               'config_sha256': digest(config), 'run_config_sha256': digest(run / 'run.yml'),
                               'runtime': str(runtime), 'status': 'prepared', 'approval_mode': approval_mode})
    return {'status': 'prepared', 'run': str(run), 'commit': commit, 'config': str(run / 'run.yml')}


def state(run, runtime):
    run = ordinary(run)
    receipt = load(run / 'module.json')
    commit, sha = engine_identity()
    if receipt['commit'] != commit or receipt['source_sha256'] != sha or receipt['runtime'] != str(runtime):
        raise ValueError('Resume with the runtime that prepared this project.')
    if receipt['run_config_sha256'] != digest(run / 'run.yml'):
        raise ValueError('Run configuration changed; prepare a new run after reviewing the change.')
    local_config(run / 'run.yml')
    return run, receipt


# What each gate's answer file holds at its top level, so the author does not have to read the
# ingest to find out. The keys are the ones each ingest reads; the item fields are in the
# prompt's own `fields`, and the gates whose ingest validates against a schema name it.
ANSWER_SHAPES = {
    'a3b': {'findings': [], 'rules': []},
    'c3': {'items': []},
    'm2': {'queries': []},
    'x1': {'statements': []},
    'x2': {'metrics': []},
    'x3': {'metrics': []},
    'x4': {'relationships': []},
}
ANSWER_SCHEMAS = {'x3': 'authored_metric_cards.schema.json', 'x4': 'metric_relationships.schema.json'}


def answer_contract(run, gate):
    """The answer's top-level shape, the prompt's field list and the validating schema, if any."""
    contract = {}
    if gate in ANSWER_SHAPES:
        contract['answer_shape'] = ANSWER_SHAPES[gate]
    try:
        fields = load(run / f'{gate}_prompt.json').get('fields')
    except (OSError, ValueError):
        fields = None
    if fields:
        contract['answer_fields'] = fields
    if gate in ANSWER_SCHEMAS:
        try:
            import metrics_agent
        except ImportError:
            return contract
        schema = Path(metrics_agent.__file__).resolve().parent / 'data' / 'schemas' / ANSWER_SCHEMAS[gate]
        if schema.is_file():
            contract['answer_schema'] = str(schema)
    return contract


BATCHED_GATES = ('m2', 'x1', 'x2', 'x3')


def batch_progress(run, gate):
    """What the person sees while a batched gate is answered: after each saved file, the setup
    helper prints the metrics in it, and the assistant posts that text in chat."""
    project = Path(__file__).resolve().parents[3] / 'analytics-build' / 'scripts' / 'project.py'
    if gate not in BATCHED_GATES or not project.is_file() or not (run.parent / 'run.json').is_file():
        return None
    command = f'python3 "{project}" --run-dir "{run.parent}" drafts --engine-dir "{run.name}"'
    return {'command': command,
            'follow': command + ' --follow',
            'say': ('One subagent writes this gate in numbered batch files and runs no drafts command. '
                    'The session that talks to the person runs `follow` as a background watch and sends '
                    'each line it prints as its own chat message, exactly as printed: the person does not '
                    'see command output. With no background subagent or watch, write the gate in that '
                    'session and run `command` after each file instead.')}


def last_stage_output(text):
    """The engine's output from the last stage it ran: the one that paused or failed.
    The whole run's output stays in engine.log."""
    start = text.rfind('\n=== ')
    return text[start + 1:] if start >= 0 else text


def blocked_reason(run):
    """The message the blocked stage recorded on the manifest, when it recorded one."""
    try:
        stages = load(run / 'manifest.json').get('stages') or []
    except (OSError, ValueError):
        return None
    failed = next((s for s in reversed(stages) if s.get('verdict') == 'blocked'), None)
    if failed and failed.get('notes'):
        return f"{failed.get('stage')}: {failed['notes']}"
    return None


def advance(run, runtime, model):
    run, receipt = state(run, runtime)
    env = dict(os.environ, METRICS_AGENT_MODEL=model)
    # Never pass publishing flags or a second interview. Upstream owns stage order and resume.
    proc = run_with_terminal([sys.executable, '-m', 'metrics_agent.cli', 'run', '--run', str(run),
                              '--config', str(run / 'run.yml')], env=env)
    log = run / 'engine.log'
    ordinary(log)
    with log.open('a') as handle:
        os.chmod(log, 0o600)
        handle.write(proc.stdout + proc.stderr)
    receipt['status'] = 'complete' if proc.returncode == 0 else 'paused' if proc.returncode == 3 else 'blocked'
    receipt.pop('outputs', None)
    result = {'status': receipt['status'], 'run': str(run), 'log': str(log), 'engine_exit': proc.returncode,
              'approval_mode': receipt['approval_mode']}
    if proc.returncode == 0:
        receipt['producer'] = 'metrics-agent/' + model
        outputs = ['verified_queries.json', 'metric_cards.json', 'metric_relationships.json']
        for evidence in ('verify_report.json', 'question_units.json'):
            if (run / evidence).is_file():
                outputs.append(evidence)
        receipt['outputs'] = {name: digest(run / name) for name in outputs}
    elif proc.returncode == 3:
        from metrics_agent.stages.run import GATES
        # The runner's pause message is authoritative, including refused ingests. A pause at
        # consistency/tracing is deliberately not represented as an ordinary authored gate.
        match = re.search(r'waiting at the (\w+) gate|\b(\w+)_ingest refused', proc.stderr)
        gate = next((g for g in match.groups() if g), '') if match else ''
        if gate in GATES:
            _, relative, _, skill = GATES[gate]
            result['gate'] = {'id': gate, 'prompt': str(run / f'{gate}_prompt.json'),
                              'instructions': str(runtime / 'source/skills' / skill / 'SKILL.md'),
                              'answers': str(run / 'answers' / relative), 'requires_user_approval': False}
            result['gate'].update(answer_contract(run, gate))
            if gate == 'x3' and (run / 'x3_repair.json').is_file():
                result['gate']['repair'] = str(run / 'x3_repair.json')
            progress = batch_progress(run, gate)
            if progress:
                result['gate']['after_each_batch'] = progress
        result['feedback'] = last_stage_output(proc.stderr)
    else:
        result['reason'] = (blocked_reason(run)
                            or 'The engine refused or failed. Read the private log and repair the cause.')
        result['feedback'] = last_stage_output(proc.stderr)
    receipt['last_result'] = result
    write(run / 'module.json', receipt)
    return result, 3 if proc.returncode == 3 else 0 if proc.returncode == 0 else 2


def own_message(exc):
    """The message of an error this module or the core engine wrote itself, else None.

    A plain ValueError and the core's own errors (`metrics_agent.*`, such as a run
    configuration's ConfigError) carry text the engine authored: the setting, the file, what
    to change. Anything else can come from a connector and carry a connection string or a
    source row, so only its type is said."""
    module = (type(exc).__module__ or '').split('.')[0]
    if type(exc) is ValueError or (isinstance(exc, ValueError) and module == 'metrics_agent'):
        return str(exc)[:600] or None
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('discover', 'stage-input', 'prepare', 'advance', 'status', 'parity-plan', 'export'))
    for name in ('runtime', 'run', 'config', 'model', 'context', 'parity', 'source', 'destination', 'source-id'):
        parser.add_argument('--' + name)
    parser.add_argument('--approval-mode', choices=('ask', 'auto'), default='auto')
    args = parser.parse_args()
    runtime = ordinary(args.runtime)
    try:
        engine_identity()
        code = 0
        if args.command == 'discover':
            result = discover()
        elif args.command == 'stage-input':
            result = stage_input(args.source, args.destination, args.source_id)
        elif args.command == 'prepare':
            result = prepare(args.run, args.config, runtime, args.approval_mode)
            code = 2 if result['status'] == 'NEEDS_CONTEXT' else 0
        elif args.command == 'advance':
            result, code = advance(args.run, runtime, args.model)
        else:
            run, receipt = state(args.run, runtime)
            if args.command == 'status':
                result = receipt.get('last_result') or {'status': receipt['status'], 'run': str(run)}
            else:
                if receipt['status'] != 'complete' or not receipt.get('outputs'):
                    raise ValueError('Finish the original engine gates before exporting context.')
                if any(digest(run / name) != sha for name, sha in receipt['outputs'].items()):
                    raise ValueError('Engine output changed after completion; advance and revalidate it.')
                if args.command == 'parity-plan':
                    from validation import plans
                    result = {'format': 1, 'outputs': receipt['outputs'],
                              'plans': plans(load(run / 'metric_cards.json'))}
                else:
                    from export import export_context
                    result = export_context(run, ordinary(args.context), receipt,
                                            load(args.parity) if args.parity else None)
        print(json.dumps(result))
        return code
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        # Do not echo connector exceptions, connection strings or raw source rows to chat.
        diagnostic = runtime / 'last-error.json'
        reason = own_message(exc)
        write(diagnostic, {'type': type(exc).__name__, **({'message': reason} if reason else {}),
                           'frames': [
            {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)]})
        print(json.dumps({'status': 'BLOCKED', 'reason': reason or
                          f'Metrics runtime operation failed ({type(exc).__name__}).',
                          'diagnostic': str(diagnostic), 'fix': 'Resolve the requirement; keep all gates enabled.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
