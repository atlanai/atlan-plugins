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
    from .terminal_io import at_terminal, SIGN_IN_TIMEOUT
except ImportError:  # The isolated worker imports this module by its filename.
    from terminal_io import at_terminal, SIGN_IN_TIMEOUT

HERE = Path(__file__).resolve().parent
ENGINE = HERE / 'engine'
DESCRIPTOR = HERE / 'module.json'
FEATURES = {'snowflake': 'packages/metrics-agent-snowflake',
            'powerbi': 'packages/metrics-agent-powerbi',
            'tableau': 'packages/metrics-agent-tableau',
            'databricks': 'packages/metrics-agent-databricks',
            'postgres': 'examples/plugs/postgres-direct', 'pbix': '.[pbix]', 'test': '.[dev]'}


def ordinary(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Use ordinary private paths, not symbolic links.')
    return path


def write(path, value):
    path = ordinary(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        os.chmod(handle.name, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    os.replace(handle.name, path)


@contextmanager
def operation_lock(root):
    # The bundled readers already require Unix. A process lock is released on a crash,
    # unlike a sentinel file that could strand the next resume indefinitely.
    try:
        import fcntl
    except ImportError:
        raise ValueError('This runtime requires macOS or Linux.') from None
    root = ordinary(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = ordinary(root / '.operation.lock')
    with path.open('a') as handle:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another metrics operation is using this runtime; resume after it finishes.') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
                raise ValueError('Runtime source changed; use a new runtime directory.')
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('x') as handle:
            os.chmod(target, 0o600)
            handle.write(item['text'])
    return data, sha


def setup(root, features):
    with operation_lock(root):
        return _setup(root, features)


def _setup(root, features):
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        raise ValueError('Use Python 3.11 or 3.12 for the pinned metrics runtime.')
    root = ordinary(root)
    data, sha = snapshot()
    selected = sorted(set(features))
    if any(name not in FEATURES for name in selected):
        raise ValueError('Unknown runtime feature.')
    if (root / 'runtime.json').exists():
        receipt = json.loads((root / 'runtime.json').read_text())
        if receipt.get('source_sha256') != sha or receipt.get('features') != selected:
            raise ValueError('Runtime selection changed; use a new runtime directory.')
        return {'status': 'ready', 'runtime': str(root), 'commit': data['commit'], 'reused': True}
    source = root / 'source'
    unpack(source)
    # Standalone Python builds can resolve libpython relative to their executable.
    # Keep that linkage, as the macOS/Linux venv CLI does, instead of copying the binary.
    try:
        venv.EnvBuilder(with_pip=True, symlinks=True).create(root / 'venv')
    except subprocess.CalledProcessError as exc:
        # Child diagnostics can contain private environment or package-registry data.
        # Preserve the failed directory, but expose neither the command nor its output.
        return {'status': 'BLOCKED', 'reason': 'Python could not create the isolated runtime.',
                'bootstrap_error': {'type': 'CalledProcessError',
                                    'exit_code': exc.returncode if type(exc.returncode) is int else None},
                'runtime': str(root),
                'fix': 'Use another approved Python 3.11 or 3.12 interpreter and a new durable runtime directory. '
                       'The failed directory is preserved.'}
    python = root / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
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
                return {'status': 'BLOCKED', 'reason': 'Runtime installation failed.',
                        'fix': 'Inspect the private install log and approved package-registry access.',
                        'log': str(log)}
    write(root / 'runtime.json', {'module': 'metrics', 'commit': data['commit'],
                                'source_sha256': sha, 'features': selected})
    return {'status': 'ready', 'runtime': str(root), 'commit': data['commit'], 'reused': False}


def invoke(root, command, args):
    with operation_lock(root):
        return _invoke(root, command, args)


def _invoke(root, command, args):
    root = ordinary(root)
    data, sha = snapshot()
    receipt = json.loads(ordinary(root / 'runtime.json').read_text())
    if receipt.get('source_sha256') != sha or receipt.get('commit') != data['commit']:
        raise ValueError('Runtime does not match this plugin; keep old projects on their pinned runtime.')
    # Gate instructions are part of the pinned implementation too, not mutable runtime
    # prompts that can drift independently from the validators.
    unpack(root / 'source')
    python = root / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file() or not (root / 'venv/pyvenv.cfg').is_file():
        raise ValueError('Set up this module runtime first.')
    # Do not inherit Python import overrides from the host or another project.
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    interactive = at_terminal()
    try:
        proc = subprocess.run([str(python), '-I', str(HERE / 'worker.py'), command,
                               '--runtime', str(root), *args], env=env,
                              stdout=subprocess.PIPE,
                              stderr=None if interactive else subprocess.PIPE, text=True,
                              timeout=SIGN_IN_TIMEOUT if interactive and command == 'prepare' else None)
    except subprocess.TimeoutExpired:
        return {'status': 'BLOCKED', 'reason': 'Connector sign-in timed out.',
                'fix': 'Retry preparation and complete sign-in in the displayed browser.'}, 2
    if proc.returncode not in (0, 2, 3):
        return {'status': 'BLOCKED', 'reason': 'The metrics runtime failed.',
                'fix': 'Check the private run log; do not bypass its gates.'}, 2
    try:
        return json.loads(proc.stdout), proc.returncode
    except ValueError:
        return {'status': 'BLOCKED', 'reason': 'Invalid metrics runtime response.',
                'fix': 'Check the pinned runtime installation.'}, 2


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
        if args.command == 'setup':
            result = setup(args.runtime, args.feature)
            code = 2 if result['status'] == 'BLOCKED' else 0
        else:
            forwarded = []
            for name in ('run', 'config', 'model', 'context', 'approval_mode', 'parity', 'source', 'destination', 'source_id'):
                if hasattr(args, name) and getattr(args, name) is not None:
                    forwarded += ['--' + name.replace('_', '-'), str(getattr(args, name))]
            result, code = invoke(args.runtime, args.command, forwarded)
    except (ValueError, OSError, KeyError) as exc:
        result, code = {'status': 'BLOCKED', 'reason': str(exc),
                        'fix': 'Resolve this requirement before advancing the metrics module.'}, 2
    print(json.dumps(result, ensure_ascii=False))
    return code
