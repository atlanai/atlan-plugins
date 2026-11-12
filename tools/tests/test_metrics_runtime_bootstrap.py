"""The runtime's real interpreter bootstrap works without contacting a package registry."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics/skills/metrics-generation'))
from modules.metrics import driver


@unittest.skipUnless(sys.version_info[:2] in ((3, 11), (3, 12)) and sys.platform != 'win32',
                     'Runtime setup supports Python 3.11/3.12 on macOS/Linux.')
class RuntimeBootstrap(unittest.TestCase):
    def test_setup_bootstraps_a_working_linked_interpreter_and_bundled_pip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve() / 'runtime'
            identity = 'a' * 64
            package_commands = mock.Mock(return_value=SimpleNamespace(returncode=0, stdout=''))
            # Bootstrap and ensurepip are real, offline operations. Only the two later
            # package installation calls are replaced, so this test never uses a registry.
            with mock.patch.object(driver, 'snapshot', return_value=({'commit': identity}, identity)), \
                    mock.patch.object(driver, 'unpack', side_effect=lambda path: path.mkdir(parents=True)), \
                    mock.patch.object(driver, 'subprocess', SimpleNamespace(
                        run=package_commands, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
                        CalledProcessError=subprocess.CalledProcessError)):
                result = driver.setup(root, [])
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(package_commands.call_count, 2)
            python = root / 'venv/bin/python'
            self.assertTrue(python.is_symlink())
            self.assertEqual(python.resolve(), Path(sys._base_executable).resolve())
            process = subprocess.run(
                [str(python), '-I', '-c',
                 'import json,sys,pip; print(json.dumps({"prefix":sys.prefix,"base":sys.base_prefix}))'],
                text=True, capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            identity = json.loads(process.stdout)
            self.assertEqual(Path(identity['prefix']).resolve(), (root / 'venv').resolve())
            self.assertEqual(Path(identity['base']).resolve(), Path(sys.base_prefix).resolve())

    def test_failed_bootstrap_returns_safe_recovery_without_installing_or_erasing(self):
        for exit_code in (1, -6):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve() / 'runtime'
                private_values = ('synthetic-private-command', 'synthetic-private-output', 'synthetic-private-error')

                def fail_bootstrap(path):
                    path.mkdir()
                    (path / 'partial-bootstrap').write_text('preserve this attempt')
                    raise subprocess.CalledProcessError(exit_code, [private_values[0]],
                                                        output=private_values[1], stderr=private_values[2])

                stdout, stderr = io.StringIO(), io.StringIO()
                # This interpreter passed the throwaway venv probe; the real build still failed.
                with mock.patch.object(driver, 'supported_python', return_value=sys.executable), \
                        mock.patch.object(driver, 'snapshot', return_value=({'commit': 'a' * 64}, 'a' * 64)), \
                        mock.patch.object(driver, 'unpack', side_effect=lambda path: path.mkdir(parents=True)), \
                        mock.patch.object(driver.venv, 'EnvBuilder') as builder, \
                        mock.patch.object(driver.subprocess, 'run') as package_commands, \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    builder.return_value.create.side_effect = fail_bootstrap
                    result = driver.main(['--runtime', str(root), 'setup'])
                self.assertEqual(result, 2)
                reply = json.loads(stdout.getvalue())
                self.assertEqual(reply['status'], 'BLOCKED')
                self.assertEqual(reply['bootstrap_error'], {'type': 'CalledProcessError', 'exit_code': exit_code})
                self.assertIn('another approved Python 3.11 or 3.12', reply['fix'])
                self.assertIn('new durable runtime directory', reply['fix'])
                self.assertEqual(stderr.getvalue(), '')
                package_commands.assert_not_called()
                self.assertEqual((root / 'venv/partial-bootstrap').read_text(), 'preserve this attempt')
                self.assertFalse((root / 'runtime.json').exists())
                self.assertFalse((root / 'install.log').exists())
                visible = stdout.getvalue() + stderr.getvalue()
                persisted = ''.join(p.read_text() for p in root.rglob('*') if p.is_file())
                for value in (*private_values, 'Traceback'):
                    self.assertNotIn(value, visible + persisted)


if __name__ == '__main__':
    unittest.main()


class SetupPythonTest(unittest.TestCase):
    """The runtime is pinned to Python 3.11 or 3.12; a newer default python3 finds one itself."""

    def test_setup_under_an_unsupported_python_reruns_with_a_supported_one(self):
        with mock.patch.object(driver, 'SUPPORTED_PYTHON', ((3, 0),)), \
                mock.patch.object(driver, 'supported_python', return_value='/opt/py/python3.12'), \
                mock.patch.object(driver.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            code = driver.main(['--runtime', '/tmp/rt', 'setup', '--feature', 'tableau'])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.args[0], ['/opt/py/python3.12', '-I', str(driver.DISPATCHER), 'metrics',
                                                 '--runtime', '/tmp/rt', 'setup', '--feature', 'tableau'])
        self.assertTrue(driver.DISPATCHER.is_file())

    def test_without_a_supported_python_setup_says_what_to_install(self):
        with mock.patch.object(driver, 'SUPPORTED_PYTHON', ((3, 0),)), \
                mock.patch.object(driver, 'supported_python', return_value=None), \
                tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out:
            runtime = str(Path(tmp).resolve() / 'runtime')
            self.assertEqual(driver.main(['--runtime', runtime, 'setup']), 2)
        self.assertIn('python3.12 or python3.11 on PATH', json.loads(out.getvalue())['reason'])

    def test_one_folder_has_one_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'project/.atlan-metrics/run_1/engine').mkdir(parents=True)
            plain = driver.ordinary(root / 'project/.atlan-metrics/runtime')
            spelled = driver.ordinary(root / 'project/.atlan-metrics/run_1/../../.atlan-metrics/runtime')
            self.assertEqual(str(plain), str(spelled))


class RuntimeMismatchFix(unittest.TestCase):
    """A runtime from another plugin version is refused with the folder to set up instead."""

    def test_an_older_runtime_names_the_new_folder(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out:
            root = Path(tmp).resolve() / 'runtime'
            root.mkdir()
            (root / 'runtime.json').write_text(json.dumps(
                {'module': 'metrics', 'commit': 'b' * 64, 'source_sha256': 'b' * 64}))
            identity = 'c' * 64
            with mock.patch.object(driver, 'snapshot', return_value=({'commit': identity}, identity)):
                self.assertEqual(driver.main(['--runtime', str(root), 'discover']), 2)
        result = json.loads(out.getvalue())
        self.assertIn('does not match this plugin', result['reason'])
        self.assertIn(str(root.parent / ('runtime-' + 'c' * 12)), result['fix'])


def fake_python(folder, name, version, builds):
    """A stand-in interpreter on PATH: it reports `version`, and a venv it makes either has a
    working pip or fails to bootstrap one, as a standalone build behind symlinks can."""
    folder.mkdir(parents=True, exist_ok=True)
    python, log = folder / name, folder / (name + '.log')
    venv = ('/bin/mkdir -p "$target/bin" && /bin/ln -s /usr/bin/true "$target/bin/python"' if builds
            else '/bin/mkdir -p "$target"; exit 1')
    python.write_text('#!/bin/sh\n'
                      'case "$*" in\n'
                      f'  *version_info*) echo "{version}" ;;\n'
                      f'  *"-m venv"*) for target; do :; done; echo "venv $target" >> "{log}"; {venv} ;;\n'
                      f'  *) echo "run $*" >> "{log}" ;;\n'
                      'esac\n')
    python.chmod(0o755)
    return python


@unittest.skipIf(sys.platform == 'win32', 'Runtime setup supports macOS/Linux.')
class InterpreterChoice(unittest.TestCase):
    """Setup builds with the first approved Python that just made a venv with pip in a throwaway
    folder, falls through to the next one otherwise, and leaves no runtime folder when none can."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.runtime = self.root / 'project/.atlan-metrics/runtime'
        # The interpreter running setup is a standalone 3.12 that cannot bootstrap pip in a venv.
        self.standalone = fake_python(self.root / 'uv', 'python3.12', '(3, 12)', builds=False)

    def interpreters(self, *pythons):
        folders = os.pathsep.join(dict.fromkeys(str(p.parent) for p in (self.standalone, *pythons)))
        return mock.patch.dict(os.environ, {'PATH': folders})

    def log(self, python):
        log = python.parent / (python.name + '.log')
        return log.read_text().splitlines() if log.exists() else []

    def probes(self, *pythons):
        return [line.split(' ', 1)[1] for p in pythons for line in self.log(p) if line.startswith('venv ')]

    def test_a_python_that_cannot_build_a_venv_with_pip_is_passed_over(self):
        brew = fake_python(self.root / 'brew', 'python3.11', '(3, 11)', builds=True)
        with self.interpreters(brew), mock.patch.object(driver.sys, 'executable', str(self.standalone)):
            self.assertEqual(driver.supported_python(), str(brew))
        tried = self.probes(self.standalone, brew)
        self.assertEqual(len(tried), 2)
        self.assertFalse(any(Path(folder).exists() for folder in tried))

    def test_setup_reruns_with_the_python_that_works(self):
        brew = fake_python(self.root / 'brew', 'python3.11', '(3, 11)', builds=True)
        with self.interpreters(brew), mock.patch.object(driver.sys, 'executable', str(self.standalone)), \
                mock.patch.object(driver, 'setup', return_value={'status': 'ready'}) as setup:
            code = driver.main(['--runtime', str(self.runtime), 'setup', '--feature', 'tableau'])
        self.assertEqual(code, 0)
        setup.assert_not_called()
        self.assertEqual(self.log(brew)[-1],
                         f'run -I {driver.DISPATCHER} metrics --runtime {self.runtime} setup --feature tableau')
        self.assertFalse(self.runtime.exists())

    def test_without_a_working_approved_python_setup_stops_and_leaves_no_runtime_folder(self):
        broken = fake_python(self.root / 'old', 'python3.11', '(3, 11)', builds=False)
        newer = fake_python(self.root / 'new', 'python3.13', '(3, 13)', builds=True)
        with self.interpreters(broken, newer), mock.patch.object(driver.sys, 'executable', str(self.standalone)), \
                mock.patch.object(driver, 'setup', return_value={'status': 'ready'}) as setup, \
                redirect_stdout(io.StringIO()) as out:
            code = driver.main(['--runtime', str(self.runtime), 'setup'])
        self.assertEqual(code, 2)
        reply = json.loads(out.getvalue())
        self.assertEqual(reply['status'], 'BLOCKED')
        self.assertIn('python3.12 or python3.11 on PATH', reply['reason'])
        setup.assert_not_called()
        self.assertFalse(self.runtime.exists())
        self.assertEqual(self.log(newer), [])
        self.assertFalse(any(Path(folder).exists() for folder in self.probes(self.standalone, broken)))
