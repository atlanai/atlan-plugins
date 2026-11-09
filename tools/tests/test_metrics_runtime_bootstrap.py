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
sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics'))
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
                with mock.patch.object(driver, 'snapshot', return_value=({'commit': 'a' * 64}, 'a' * 64)), \
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
