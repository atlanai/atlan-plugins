"""The runtime's real interpreter bootstrap works without contacting a package registry."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
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
            launcher = root.parent / 'launchers' / Path(sys._base_executable).name
            launcher.parent.mkdir()
            launcher.symlink_to(Path(sys._base_executable).resolve())
            # Bootstrap and ensurepip are real, offline operations. Only the two later
            # package installation calls are replaced, so this test never uses a registry.
            with mock.patch.object(sys, '_base_executable', str(launcher)), \
                    mock.patch.object(driver, 'snapshot', return_value=({'commit': identity}, identity)), \
                    mock.patch.object(driver, 'unpack', side_effect=lambda path: path.mkdir(parents=True)), \
                    mock.patch.object(driver, 'subprocess', SimpleNamespace(
                        run=package_commands, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
                        CalledProcessError=subprocess.CalledProcessError)):
                result = driver.setup(root, [])
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(package_commands.call_count, 2)
            config = (root / 'venv/pyvenv.cfg').read_text()
            self.assertIn('home = ' + str(Path(sys._base_executable).resolve().parent), config)
            self.assertNotIn('home = ' + str(launcher.parent) + '\n', config)
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
                with mock.patch.object(driver, 'snapshot', return_value=({'commit': 'a' * 64}, 'a' * 64)), \
                        mock.patch.object(driver, 'unpack', side_effect=lambda path: path.mkdir(parents=True)), \
                        mock.patch.object(driver, 'ResolvedEnvBuilder') as builder, \
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
