"""Standard-library host driver for the maintained Analytics metrics runtime.

Only setup installs dependencies, after an explicit request. All other commands are local
except reads performed by the input plugs selected in run.yml. No Registry transport.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import venv

try:
    from .terminal_io import ATTENDED, relaying, SIGN_IN_TIMEOUT
except ImportError:  # The isolated worker imports this module by its filename.
    from terminal_io import ATTENDED, relaying, SIGN_IN_TIMEOUT

HERE = Path(__file__).resolve().parent
ENGINE = HERE / 'engine'
DESCRIPTOR = HERE / 'module.json'
FEATURES = {'snowflake': 'packages/metrics-agent-snowflake',
            'powerbi': 'packages/metrics-agent-powerbi',
            'tableau': 'packages/metrics-agent-tableau',
            'databricks': 'packages/metrics-agent-databricks',
            'postgres': 'examples/plugs/postgres-direct', 'pbix': '.[pbix]', 'test': '.[dev]'}


class RuntimeMismatch(ValueError):
    """A runtime built from other module source or features. Its `fix` names the sibling
    folder to set up instead: one per module commit, so an upgrade never needs a made-up name."""

    def __init__(self, reason, root, commit):
        super().__init__(reason)
        self.fix = (f'I\'ll set up a fresh copy of the metric tools in {Path(root).parent / ("runtime-" + commit[:12])}. '
                    'Earlier projects keep their own tools.')


def ordinary(path):
    # One folder has one name: `run/../..` and its plain spelling are the same runtime. With
    # symbolic links refused, removing `..` by text names the folder the filesystem would.
    path = Path(os.path.normpath(Path(path).absolute()))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('That folder is a shortcut. Pick a regular folder instead.')
    return path


def write(path, value):
    path = ordinary(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        os.chmod(handle.name, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    os.replace(handle.name, path)


if os.name == 'nt':
    import msvcrt

    def try_lock(handle):
        # msvcrt locks bytes from the current position; lock the first byte, even past EOF.
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def unlock(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def try_lock(handle):
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def unlock(handle):
        fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def operation_lock(root):
    # A process lock is released on a crash, unlike a sentinel file that could strand the
    # next resume indefinitely.
    root = ordinary(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = ordinary(root / '.operation.lock')
    with path.open('a') as handle:
        os.chmod(path, 0o600)
        if not try_lock(handle):
            raise ValueError('Another metrics build is using these tools. I\'ll carry on once it finishes.')
        try:
            yield
        finally:
            unlock(handle)


def snapshot():
    """Current owned source, hashed for runtime isolation."""
    descriptor = json.loads(DESCRIPTOR.read_text())
    files = {}
    for path in sorted(ENGINE.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(ENGINE)
        if any(part in ('tests', '__pycache__', 'build', 'dist', '.pytest_cache', '.venv', 'venv', '.git', '.mypy_cache', '.ruff_cache') or
               part.endswith('.egg-info') for part in relative.parts) or path.suffix in ('.pyc', '.pyo'):
            continue
        if relative.parts[0] == 'scripts' or relative.as_posix() in ('pytest.ini', 'CONTRIBUTING.md', 'requirements-dev.txt') or relative.as_posix().startswith('examples/plugs/jsonl-trace/'):
            continue
        if path.is_symlink():
            raise ValueError('Engine source must contain ordinary files.')
        text = path.read_text(encoding='utf-8')
        files[relative.as_posix()] = {'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
    if 'src/metrics_agent/cli.py' not in files or 'pyproject.toml' not in files:
        raise ValueError('Metrics engine source is incomplete.')
    identity = {name: item['sha256'] for name, item in files.items()}
    sha = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {'format': 2, 'commit': sha, 'packages': descriptor['packages'],
            'gate_skills': descriptor['gate_skills'], 'files': files}, sha


def unpack(root):
    data, sha = snapshot()
    root = ordinary(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, item in data['files'].items():
        target = ordinary(root / name)
        if target.exists():
            if target.read_text() != item['text']:
                raise ValueError('The metric tools in this folder are out of date. I\'ll set up a fresh copy in a new folder.')
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('x') as handle:
            os.chmod(target, 0o600)
            handle.write(item['text'])
    return data, sha


def setup(root, features):
    with operation_lock(root):
        return _setup(root, features)


SUPPORTED_PYTHON = ((3, 12), (3, 11))
DISPATCHER = Path(__file__).resolve().parents[2] / 'scripts' / 'analytics_agent.py'


def supported_pythons():
    """Each Python on PATH the pinned runtime is built with (its wheels are pinned per version), in
    order of preference. A machine's default `python3` may be older or newer than any of them."""
    import shutil  # noqa: PLC0415
    found = []
    for major, minor in SUPPORTED_PYTHON:
        path = shutil.which(f'python{major}.{minor}')
        if not path:
            continue
        probe = subprocess.run([path, '-I', '-c', 'import sys; print(sys.version_info[:2])'],
                               capture_output=True, text=True, timeout=30)
        if probe.returncode == 0 and probe.stdout.strip() == str((major, minor)):
            found.append(path)
    return found


def supported_python():
    """The first Python on PATH the pinned runtime is built with, or None."""
    return next(iter(supported_pythons()), None)


def _venv_with_another(root):
    """A standalone Python build (as some installers ship) can fail to build the runtime's own
    environment. Build it with the next supported Python on this machine instead, so the agent
    never has to: True once one has. Each failed attempt is kept beside it, never erased, and
    no child's output is kept."""
    current = os.path.realpath(sys.executable)
    for other in supported_pythons():
        if os.path.realpath(other) == current:
            continue
        if (root / 'venv').exists():
            kept = next(root / f'venv.failed-{n}' for n in range(1, 100) if not (root / f'venv.failed-{n}').exists())
            (root / 'venv').rename(kept)
        if subprocess.run([other, '-I', '-m', 'venv', str(root / 'venv')], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return True
    return False


def _python(root):
    """The runtime's own interpreter: every later engine command runs with it."""
    return root / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _setup(root, features):
    if sys.version_info[:2] not in SUPPORTED_PYTHON:
        raise ValueError('Building metrics needs Python 3.11 or 3.12. Install one, then I\'ll set up again.')
    root = ordinary(root)
    data, sha = snapshot()
    selected = sorted(set(features))
    if any(name not in FEATURES for name in selected):
        raise ValueError('Unknown runtime feature.')
    if (root / 'runtime.json').exists():
        receipt = json.loads((root / 'runtime.json').read_text())
        if receipt.get('source_sha256') != sha or receipt.get('features') != selected:
            raise RuntimeMismatch('These metric tools were set up for different sources.',
                                  root, data['commit'])
        return {'status': 'ready', 'runtime': str(root), 'python': str(_python(root)), 'commit': data['commit'], 'reused': True}
    source = root / 'source'
    unpack(source)
    # Standalone Python builds can resolve libpython relative to their executable.
    # Keep that linkage, as the macOS/Linux venv CLI does, instead of copying the binary.
    try:
        venv.EnvBuilder(with_pip=True, symlinks=True).create(root / 'venv')
    except subprocess.CalledProcessError as exc:
        # Child diagnostics can contain private environment or package-registry data.
        # Preserve the failed directory, but expose neither the command nor its output.
        if not _venv_with_another(root):
            return {'status': 'BLOCKED', 'reason': 'Python couldn\'t set up the metric tools on this machine.',
                    'bootstrap_error': {'type': 'CalledProcessError',
                                        'exit_code': exc.returncode if type(exc.returncode) is int else None},
                    'runtime': str(root),
                    'fix': 'Try another Python 3.11 or 3.12 and a new folder. The failed folder is kept.'}
    python = _python(root)
    constraints = str(source / 'constraints.txt')
    commands = [[str(python), '-m', 'pip', '--quiet', '--disable-pip-version-check', 'install', '-c', constraints, '--only-binary=:all:',
                 'setuptools', 'wheel'],
                [str(python), '-m', 'pip', '--quiet', '--disable-pip-version-check', 'install', '--no-build-isolation', '-c', constraints,
                 '--only-binary=:all:', str(source),
                 *(str(source) + ('[pbix]' if name == 'pbix' else '[dev]') if name in ('pbix', 'test') else str(source / FEATURES[name])
                   for name in selected)]]
    log = ordinary(root / 'install.log')
    with log.open('w') as handle:
        os.chmod(log, 0o600)
        for command in commands:
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, cwd=source)
            # Package registries may authenticate in URL userinfo. Do not persist that even
            # when pip has masked only its password field.
            handle.write(re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', proc.stdout))
            if proc.returncode:
                return {'status': 'BLOCKED', 'reason': 'Installing the metric tools failed.',
                        'fix': 'Check the install log. Also check that this machine can reach your approved package source.',
                        'log': str(log)}
    write(root / 'runtime.json', {'module': 'metrics', 'commit': data['commit'],
                                'source_sha256': sha, 'features': selected})
    return {'status': 'ready', 'runtime': str(root), 'python': str(python), 'commit': data['commit'], 'reused': False}


def invoke(root, command, args):
    with operation_lock(root):
        return _invoke(root, command, args)


def _invoke(root, command, args):
    root = ordinary(root)
    data, sha = snapshot()
    receipt = json.loads(ordinary(root / 'runtime.json').read_text())
    if receipt.get('source_sha256') != sha or receipt.get('commit') != data['commit']:
        raise RuntimeMismatch('This project was built with another version of the plugin.', root, data['commit'])
    # Gate instructions are part of the pinned implementation too, not mutable runtime
    # prompts that can drift independently from the validators.
    unpack(root / 'source')
    python = _python(root)
    if not python.is_file() or not (root / 'venv/pyvenv.cfg').is_file():
        raise ValueError('The metric tools aren\'t set up yet. I\'ll set them up first.')
    # Do not inherit Python import overrides from the host or another project.
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    # A terminal, or an attended background command: the person sees sign-in prompts as they come.
    interactive = relaying()
    try:
        proc = subprocess.run([str(python), '-I', str(HERE / 'worker.py'), command,
                               '--runtime', str(root), *args], env=env,
                              stdout=subprocess.PIPE,
                              stderr=None if interactive else subprocess.PIPE, text=True,
                              timeout=SIGN_IN_TIMEOUT if interactive and command == 'prepare' else None)
    except subprocess.TimeoutExpired:
        return {'status': 'BLOCKED', 'reason': 'Sign-in timed out.',
                'fix': 'I\'ll start again. Finish signing in within 15 minutes.'}, 2
    if proc.returncode not in (0, 2, 3):
        return {'status': 'BLOCKED', 'reason': 'Building metrics stopped with an error.',
                'fix': 'I\'ll check the log on this machine and fix the cause. No check is skipped.'}, 2
    try:
        return json.loads(proc.stdout), proc.returncode
    except ValueError:
        return {'status': 'BLOCKED', 'reason': 'The metric tools gave an answer I couldn\'t read.',
                'fix': 'I\'ll check how they were installed.'}, 2


GUID = re.compile(r'^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')


def powerbi_report(link):
    """The canonical report address and stable source id of a Power BI Service report link.
    A page, a query string or a trailing slash is dropped: the fetch reads the whole report."""
    from urllib.parse import urlsplit  # noqa: PLC0415
    parts = urlsplit(str(link or '').strip())
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not (host == 'powerbi.com' or host.endswith('.powerbi.com')):
        raise ValueError('That isn\'t a Power BI report link. Open the report in Power BI and copy its address.')
    segments = [s for s in parts.path.split('/') if s]
    lower = [s.lower() for s in segments]
    if 'groups' not in lower or lower.index('groups') + 1 >= len(segments):
        raise ValueError('That isn\'t a Power BI report link. Open the report in Power BI and copy its address.')
    at = lower.index('groups')
    workspace = segments[at + 1]
    report = segments[at + 3] if len(segments) > at + 3 and lower[at + 2] == 'reports' else None
    if not GUID.match(workspace):
        raise ValueError('That report is in My workspace, which can\'t be read directly. '
                         'Take it from the browser instead.')
    if report is None or not GUID.match(report):
        raise ValueError('That link opens a workspace, not one report. Pick the report and copy its address.')
    workspace, report = workspace.lower(), report.lower()
    return (f'https://app.powerbi.com/groups/{workspace}/reports/{report}',
            f'powerbi:{workspace}:{report}')


def powerbi_config(target, dialect, sign_in=None):
    """The run settings for one Power BI report fetched with its semantic model."""
    settings = {'targets': [target], 'with_model': True}
    if sign_in:
        settings['sign_in'] = sign_in
    return {'version': 1, 'scope': 'powerbi-report', 'dialect': dialect, 'mode': 'standalone',
            'inputs': {'history': [], 'schema': [], 'bi': ['powerbi-fabric'], 'sql_check': None},
            'settings': {'powerbi-fabric': settings}, 'witness_mode': 'parse',
            'publish': ['files'], 'observers': []}


def runtime_with(root, required):
    """Reuse a runtime that already has every required feature, else set one up with them."""
    receipt = ordinary(root) / 'runtime.json'
    features = set(required)
    if receipt.is_file():
        built = json.loads(receipt.read_text()).get('features') or []
        if features <= set(built):
            features = set(built)
    return setup(root, sorted(features))


def sign_in_seen(log_text):
    """What the fetch's own log says about the sign-in: `signed_in` after a new sign-in,
    `saved` when an earlier one was reused, `not_shown` otherwise."""
    if 'Signed in.' in log_text:
        return 'signed_in'
    if 'Using your saved Power BI sign-in.' in log_text:
        return 'saved'
    return 'not_shown'


def fetch_powerbi(root, run, link, dialect, model, features=(), approval_mode='auto', sign_in=None,
                  config=None):
    """One command for a Power BI report link: set up the runtime with the `powerbi` feature
    when missing, write the run settings, prepare, and advance to the first step that needs an
    answer. Meant to run as a background command while the person is in the conversation:
    the sign-in opens in their browser and answers on this machine with no terminal, and each
    engine line is relayed as it comes. Running it again resumes the same build."""
    target, source_id = powerbi_report(link)
    dialect = str(dialect or '').strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', dialect):
        raise ValueError('Name the warehouse\'s SQL dialect, for example snowflake.')
    run = ordinary(run)
    config = ordinary(config) if config else ordinary(run.parent / 'private' / 'engine-config.json')
    doc = powerbi_config(target, dialect, sign_in)
    if config.is_file() and json.loads(config.read_text()) != doc:
        raise ValueError('This build was started with other settings. Start a new build for this report.')
    ready = runtime_with(root, {'powerbi', *features})
    if ready.get('status') != 'ready':
        return ready, 2
    if not config.is_file():
        write(config, doc)
    os.environ[ATTENDED] = '1'
    prepared, code = invoke(root, 'prepare', ['--run', str(run), '--config', str(config),
                                              '--approval-mode', approval_mode])
    if code or prepared.get('status') != 'prepared':
        return prepared, code or 2
    log = run / 'engine.log'
    start = log.stat().st_size if log.is_file() else 0
    result, code = invoke(root, 'advance', ['--run', str(run), '--model', model])
    try:
        with log.open('rb') as handle:
            handle.seek(start)
            text = handle.read().decode('utf-8', 'replace')
    except OSError:
        text = ''
    result.update({'sign_in': sign_in_seen(text), 'source_id': source_id, 'report': target,
                   'runtime': str(ordinary(root)), 'engine_run': str(run), 'config': str(config)})
    return result, code


PAGE_SCOPE = ENGINE / 'src' / 'metrics_agent' / 'lib' / 'page_scope.py'


def _page_scope():
    """The engine's own page matching (stdlib only), so this host and the engine's A2 decide
    the same way what a pick means."""
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location('metrics_page_scope', PAGE_SCOPE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_pages(run, pages=None, every=False):
    """Write the pages the person chose to `<run>/page_scope.json`. run.yml cannot change once
    the report is fetched, so the pick is its own record, which the engine's A2 reads."""
    import datetime  # noqa: PLC0415
    names = [str(p).strip() for p in pages or [] if str(p or '').strip()]
    if every and names:
        raise ValueError('Name some pages or every page, not both.')
    if not every and not names:
        raise ValueError('Name the pages the person chose, or every page.')
    if len(names) > 200 or any(len(n) > 300 or any(ord(c) < 32 for c in n) for n in names):
        raise ValueError('Those page names can\'t be read. Use the names the report shows.')
    doc = {'contract_version': '1.0', 'pages': [] if every else list(dict.fromkeys(names)),
           'all': bool(every),
           'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
    run = ordinary(run)
    write(run / 'page_scope.json', doc)
    return doc


def _a2_scope(run):
    """What the engine's A2 last ran with: (whether it ran, the digest of its pages)."""
    try:
        stages = json.loads((run / 'manifest.json').read_text()).get('stages') or []
    except (OSError, ValueError):
        return False, None
    entry = next((s for s in reversed(stages) if s.get('stage') == 'a2_question_units'), None)
    if not entry:
        return False, None
    return True, str((entry.get('counts') or {}).get('page_scope') or 'none')


def _scope_summary(run):
    try:
        doc = json.loads((run / 'question_units.json').read_text())
    except (OSError, ValueError):
        return None
    scope = doc.get('page_scope')
    if not isinstance(scope, dict):
        return None
    return {**scope, 'tiles': len(doc.get('units') or [])}


def scope_pages(root, run, pages=None, every=False, model=None):
    """Record the person's pages and, when the build already read the report with other pages,
    advance it once so it rebuilds from these before anyone answers its first step. A build
    not yet at the report reads them when it gets there."""
    run = ordinary(run)
    doc = record_pages(run, pages, every)
    result = {'status': 'recorded', 'pages': doc['pages'], 'all': doc['all'],
              'record': str(run / 'page_scope.json')}
    ran, before = _a2_scope(run)
    if not ran or not (run / 'module.json').is_file():
        result['next'] = 'The build reads these pages when it reaches the report\'s tiles.'
        return result, 0
    if _page_scope().digest(doc) == before:
        result['scope'] = _scope_summary(run)
        result['next'] = 'The build already covers these pages.'
        return result, 0
    if not model:
        result['next'] = 'Advance the build once (with --model) before answering its next step: it rebuilds from these pages.'
        return result, 0
    try:
        advanced, code = invoke(root, 'advance', ['--run', str(run), '--model', model])
    except (ValueError, OSError) as exc:
        # The pick is saved either way: the build's next advance rebuilds from it.
        why = str(exc) if isinstance(exc, ValueError) else 'The metric tools couldn\'t be reached.'
        result['next'] = (f'{why} Advance the build once it is free, before answering its next step: it then '
                          'rebuilds from these pages.')
        return result, 0
    advanced.update({'pages': doc['pages'], 'all': doc['all'], 'record': result['record'],
                     'scope': _scope_summary(run)})
    return advanced, code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    install = commands.add_parser('setup')
    install.add_argument('--feature', action='append', choices=FEATURES, default=[])
    commands.add_parser('discover')
    stage = commands.add_parser('stage-input')
    stage.add_argument('--source', required=True)
    stage.add_argument('--source-id', help='Stable source-system asset key, independent of download filename.')
    stage.add_argument('--destination', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--run', required=True)
    prepare.add_argument('--config', required=True)
    prepare.add_argument('--approval-mode', choices=('ask', 'auto'), default='auto')
    report = commands.add_parser('powerbi-report', help='Set up, prepare and fetch one Power BI report link.')
    report.add_argument('--link', required=True)
    report.add_argument('--run', required=True)
    report.add_argument('--dialect', required=True)
    report.add_argument('--model', required=True)
    report.add_argument('--feature', action='append', choices=FEATURES, default=[])
    report.add_argument('--approval-mode', choices=('ask', 'auto'), default='auto')
    report.add_argument('--sign-in', choices=('browser', 'device'))
    report.add_argument('--config')
    scope = commands.add_parser('pages', help='Record the report pages the person chose; the build covers only those.')
    scope.add_argument('--run', required=True)
    scope.add_argument('--page', action='append', default=[], help='A chosen page, by the name the report shows.')
    scope.add_argument('--all', action='store_true', help='Every page: nothing is left out.')
    scope.add_argument('--model', help='The model answering the build, to rebuild from these pages at once.')
    for name in ('advance', 'status', 'parity-plan', 'export'):
        sub = commands.add_parser(name)
        sub.add_argument('--run', required=True)
        if name == 'advance':
            sub.add_argument('--model', required=True)
        if name == 'export':
            sub.add_argument('--context', required=True)
            sub.add_argument('--parity')
    args = parser.parse_args(argv)
    try:
        if args.command in ('setup', 'powerbi-report') and sys.version_info[:2] not in SUPPORTED_PYTHON \
                and (other := supported_python()):
            # Built with this Python the runtime would be refused: run setup with one it takes.
            rest = sys.argv[1:] if argv is None else list(argv)
            return subprocess.run([other, '-I', str(DISPATCHER), 'metrics', *rest]).returncode
        if args.command == 'setup':
            result = setup(args.runtime, args.feature)
            code = 2 if result['status'] == 'BLOCKED' else 0
        elif args.command == 'pages':
            result, code = scope_pages(args.runtime, args.run, args.page, args.all, args.model)
        elif args.command == 'powerbi-report':
            result, code = fetch_powerbi(args.runtime, args.run, args.link, args.dialect, args.model,
                                         args.feature, args.approval_mode, args.sign_in, args.config)
        else:
            forwarded = []
            for name in ('run', 'config', 'model', 'context', 'approval_mode', 'parity', 'source', 'destination', 'source_id'):
                if hasattr(args, name) and getattr(args, name) is not None:
                    forwarded += ['--' + name.replace('_', '-'), str(getattr(args, name))]
            result, code = invoke(args.runtime, args.command, forwarded)
    except (ValueError, OSError, KeyError) as exc:
        result, code = {'status': 'BLOCKED', 'reason': str(exc),
                        'fix': getattr(exc, 'fix', None)
                        or 'This needs fixing before the build can go on.'}, 2
    if args.command in ('prepare', 'advance', 'export', 'powerbi-report') and code in (0, 3) \
            and _in_setup_run(args.run):
        # A setup run shows what each step produced as it finishes; the gate the agent answers next is the moment to.
        result['visual'] = ("Show what the engine's last step produced: run the setup helper's `PROJECT visual next` (with "
                            "`--widget` where you have `show_widget`), and again while it says more is due. The chip is the "
                            "update: add no words of your own about the step, the engine or its files.")
    print(json.dumps(result, ensure_ascii=False))
    if args.command in ('powerbi-report', 'pages') and code == 3:
        # It runs as a background task the app watches, which shows a non-zero exit as "failed": a pause at the first
        # gate is this command's normal end (0.9.76's live run showed "failed" the moment sign-in worked). The result's
        # `status` and `gate` still say it paused.
        return 0
    return code


def _in_setup_run(run):
    """Whether an engine run sits inside a setup run, whose helper draws each step's result."""
    try:
        return (Path(run).resolve().parent / 'run.json').is_file()
    except (OSError, RuntimeError):
        return False
