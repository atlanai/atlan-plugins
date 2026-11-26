"""The Analytics helpers and metrics runtime run natively on Windows, without WSL.

CI runs on Linux, so these tests load the Windows branches with a stand-in `msvcrt` and
check the sources for Unix-only modules imported where every platform loads them.
"""
import ast
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ANALYTICS = ROOT / 'plugins/atlanai-analytics'
MODULE = ANALYTICS / 'skills/metrics-generation/modules/metrics'
UNIX_ONLY = {'fcntl', 'pty', 'termios', 'tty', 'resource', 'pwd', 'grp'}
UNIX_FLAGS = {'O_NOFOLLOW', 'O_NONBLOCK', 'O_DIRECTORY', 'O_NOCTTY'}


def platform_guarded(node, parents):
    """True when an `if os.name ...` or `try:` block, or a function body, encloses the node."""
    for parent in parents:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Try)):
            return True
        if isinstance(parent, ast.If) and 'os.name' in ast.unparse(parent.test):
            return True
    return False


class FakeMsvcrt:
    """Byte-range locks keyed by file identity, refusing a second holder like Windows."""
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self):
        self.held = {}

    def locking(self, fd, mode, nbytes):
        stat = os.fstat(fd)
        key = (stat.st_dev, stat.st_ino, os.lseek(fd, 0, os.SEEK_CUR), nbytes)
        if mode == self.LK_UNLCK:
            if self.held.pop(key, None) != fd:
                raise OSError('unlocking a range this handle does not hold')
        elif key in self.held:
            raise OSError('locked by another handle')
        else:
            self.held[key] = fd


class WindowsPortabilityTests(unittest.TestCase):
    def test_no_unix_only_module_loads_on_every_platform(self):
        problems = []
        for path in sorted(ANALYTICS.rglob('*.py')):
            if {'tests', '__pycache__'} & set(path.parts):
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            stack = [(tree, [])]
            while stack:
                node, parents = stack.pop()
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split('.')[0]]
                if UNIX_ONLY & set(names) and not platform_guarded(node, parents):
                    problems.append(f'{path.relative_to(ROOT)}:{node.lineno} imports {names}')
                if (isinstance(node, ast.Attribute) and node.attr in UNIX_FLAGS
                        and isinstance(node.value, ast.Name) and node.value.id == 'os'):
                    problems.append(f'{path.relative_to(ROOT)}:{node.lineno} uses os.{node.attr}')
                stack.extend((child, parents + [node]) for child in ast.iter_child_nodes(node))
        self.assertEqual(problems, [])

    def test_no_reference_asks_for_wsl_or_refuses_windows(self):
        for path in ANALYTICS.rglob('*'):
            if path.suffix in ('.md', '.py') and '__pycache__' not in path.parts and 'tests' not in path.parts:
                text = path.read_text(encoding='utf-8')
                self.assertNotIn('requires macOS or Linux', text, path)
                for line in text.splitlines():
                    if 'WSL' in line:
                        self.assertIn('never ask to install WSL', line.replace('Never', 'never'), path)

    def load_driver_as_windows(self, fake):
        sys.path.insert(0, str(MODULE))
        self.addCleanup(sys.path.remove, str(MODULE))
        spec = importlib.util.spec_from_file_location('driver_on_windows', MODULE / 'driver.py')
        module = importlib.util.module_from_spec(spec)
        # Only the driver sees Windows: patching os.name itself would break pathlib here.
        windows_os = types.ModuleType('os')
        windows_os.__getattr__ = lambda attr: getattr(os, attr)
        windows_os.name = 'nt'
        with mock.patch.dict(sys.modules, {'msvcrt': fake, 'os': windows_os}):
            spec.loader.exec_module(module)
        return module

    def test_windows_runtime_lock_refuses_a_second_operation_and_releases(self):
        fake = FakeMsvcrt()
        driver = self.load_driver_as_windows(fake)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with driver.operation_lock(root):
                self.assertEqual(len(fake.held), 1)
                with self.assertRaisesRegex(ValueError, 'Another metrics build'):
                    with driver.operation_lock(root):
                        self.fail('Concurrent operation acquired the runtime lock.')
            self.assertEqual(fake.held, {})
            with driver.operation_lock(root):
                pass
            self.assertEqual(fake.held, {})

    def piped(self, code, timeout=10):
        sys.path.insert(0, str(MODULE))
        self.addCleanup(sys.path.remove, str(MODULE))
        import terminal_io
        real_run = subprocess.run

        def run(command, *args, **kwargs):
            if command[0] == 'taskkill':
                try:
                    os.kill(int(command[-1]), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return subprocess.CompletedProcess(command, 0)
            return real_run(command, *args, **kwargs)

        relayed = io.StringIO()
        with mock.patch.object(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0, create=True), \
                mock.patch.object(subprocess, 'run', run), mock.patch.object(sys, 'stderr', relayed):
            result = terminal_io._run_piped([sys.executable, '-c', code], env=dict(os.environ),
                                            timeout=timeout)
        return result, relayed.getvalue()

    def test_windows_relay_captures_and_shows_both_streams(self):
        result, relayed = self.piped(
            "import sys; print('SIGN IN AT https://example.invalid'); print('accepted', file=sys.stderr)")
        self.assertEqual(result.returncode, 0)
        self.assertIn('SIGN IN AT', result.stdout)
        self.assertIn('accepted', result.stderr)
        self.assertIn('SIGN IN AT', relayed)
        self.assertIn('accepted', relayed)

    def test_windows_relay_times_out_and_stops_the_engine(self):
        with self.assertRaisesRegex(TimeoutError, 'timed out'):
            self.piped('import time; time.sleep(30)', timeout=0.5)


if __name__ == '__main__':
    unittest.main()
