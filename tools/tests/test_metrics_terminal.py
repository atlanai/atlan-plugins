"""Exercise terminal sign-in across the actual host/worker subprocess boundaries."""
import errno
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'plugins/atlanai-analytics/skills/analytics-metrics/modules/metrics'


class TerminalTests(unittest.TestCase):
    def execute(self, body, prompt=None, answer='continue\n', timeout=8):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'check.py'
            script.write_text('import sys\nsys.path.insert(0, ' + repr(str(MODULE)) + ')\n' + body)
            master, slave = pty.openpty()
            proc = subprocess.Popen([sys.executable, str(script)], stdin=slave, stderr=slave,
                                    stdout=subprocess.PIPE)
            os.close(slave)
            transcript = b''
            answered = False
            deadline = time.monotonic() + timeout
            try:
                while proc.poll() is None:
                    if time.monotonic() >= deadline:
                        self.fail('Terminal command stalled: ' + transcript.decode(errors='replace'))
                    if select.select([master], [], [], 0.05)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError as exc:
                            if exc.errno != errno.EIO:
                                raise
                            chunk = b''
                        transcript += chunk
                        if prompt and prompt.encode() in transcript and not answered:
                            os.write(master, answer.encode())
                            answered = True
                stdout = proc.stdout.read().decode()
                self.assertEqual(proc.returncode, 0, transcript.decode(errors='replace') + stdout)
                if prompt:
                    self.assertTrue(answered, transcript.decode(errors='replace'))
                return json.loads(stdout), transcript.decode(errors='replace')
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                proc.stdout.close()
                os.close(master)

    def test_engine_prompt_streams_before_completion_and_can_read_input(self):
        body = '''import json, os
from terminal_io import run
code = "import sys; assert sys.stdin.isatty() and sys.stderr.isatty() and sys.stdout.isatty(); print('SIGN IN NOW'); assert input() == 'continue'; print('accepted', file=sys.stderr)"
r = run([sys.executable, '-c', code], env=os.environ.copy())
print(json.dumps({'code': r.returncode, 'out': r.stdout, 'err': r.stderr}))
'''
        result, transcript = self.execute(body, 'SIGN IN NOW')
        self.assertEqual(result['code'], 0)
        self.assertIn('SIGN IN NOW', result['out'])
        self.assertIn('accepted', result['err'])
        self.assertIn('SIGN IN NOW', transcript)

    def test_unattended_does_not_fabricate_a_terminal_or_stream_prompts(self):
        code = '''import json, os, sys
sys.path.insert(0, MODULE)
from terminal_io import run
r = run([sys.executable, '-c', "import sys; print(sys.stdin.isatty(), sys.stderr.isatty()); print('diagnostic', file=sys.stderr)"], env=os.environ.copy())
print(json.dumps({'out': r.stdout, 'err': r.stderr}))
'''.replace('MODULE', repr(str(MODULE)))
        proc = subprocess.run([sys.executable, '-c', code], input='', capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        self.assertEqual(json.loads(proc.stdout), {'out': 'False False\n', 'err': 'diagnostic\n'})

    def test_interactive_timeout_terminates_connector_descendants(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'unexpected'
            code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr("import time,pathlib; time.sleep(0.8); pathlib.Path(" + repr(str(marker)) + ").touch()") + "]); time.sleep(30)"
            body = '''import json, os
from terminal_io import run
try:
    run([sys.executable, '-c', CODE], env=os.environ.copy(), timeout=0.15)
except TimeoutError:
    print(json.dumps({'timed_out': True}))
'''.replace('CODE', repr(code))
            result, _ = self.execute(body)
            self.assertTrue(result['timed_out'])
            time.sleep(0.9)
            self.assertFalse(marker.exists())

    def test_prepare_keeps_terminal_detection_and_shows_checks_live(self):
        body = '''import json, tempfile, types
from pathlib import Path
from unittest.mock import patch
import worker
setup = types.ModuleType('metrics_agent.setup')
def check(config, out):
    assert sys.stdin.isatty() and sys.stderr.isatty() and sys.stdout.isatty()
    print('PREPARE SIGN IN', flush=True)
    assert input() == 'continue'
    print('checked', file=sys.stderr)
    return 0
setup.check_file = check
package = types.ModuleType('metrics_agent'); package.setup = setup
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder).resolve()
    config = root / 'config.json'; config.write_text('{}')
    with patch.dict(sys.modules, {'metrics_agent': package}), patch.object(worker, 'local_config', return_value={}), patch.object(worker, 'engine_identity', return_value=('test', 'test')):
        result = worker.prepare(root / 'run', config, root / 'runtime')
print(json.dumps(result))
'''
        result, _ = self.execute(body, 'PREPARE SIGN IN')
        self.assertEqual(result['status'], 'prepared')

    def test_driver_keeps_worker_stderr_and_stdin_attached(self):
        body = '''import json, tempfile, os
from pathlib import Path
from unittest.mock import patch
import driver
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder).resolve(); runtime = root / 'runtime'; runtime.mkdir()
    (runtime / 'runtime.json').write_text(json.dumps({'source_sha256':'test','commit':'test'}))
    (runtime / 'venv/bin').mkdir(parents=True)
    (runtime / 'venv/bin/python').symlink_to(sys.executable)
    (runtime / 'venv/pyvenv.cfg').touch()
    (root / 'worker.py').write_text("import sys,json; assert sys.stdin.isatty() and sys.stderr.isatty(); print('DRIVER SIGN IN',file=sys.stderr,flush=True); assert input() == 'continue'; print(json.dumps({'status':'prepared'}))")
    with patch.object(driver, 'snapshot', return_value=({'commit':'test'},'test')), patch.object(driver, 'unpack'), patch.object(driver, 'HERE', root):
        result, code = driver.invoke(runtime, 'prepare', [])
print(json.dumps({'result':result,'code':code}))
'''
        result, _ = self.execute(body, 'DRIVER SIGN IN')
        self.assertEqual(result, {'result': {'status': 'prepared'}, 'code': 0})

    def test_advance_preserves_gate_feedback_and_private_log(self):
        body = '''import json, tempfile, os, types
from pathlib import Path
from unittest.mock import patch
import worker
stage = types.ModuleType('metrics_agent.stages.run')
stage.GATES = {'a3': ('unused', 'a3.json', 'unused', 'review-compiler')}
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder).resolve(); run = root / 'run'; run.mkdir()
    engine = root / 'engine'
    engine.write_text('#!' + sys.executable + "\\nimport sys; assert sys.stdin.isatty() and sys.stderr.isatty(); print('ADVANCE SIGN IN',flush=True); assert input() == 'continue'; print('waiting at the a3 gate',file=sys.stderr); sys.exit(3)\\n")
    engine.chmod(0o700)
    with patch.object(worker, 'state', return_value=(run, {'approval_mode':'auto'})), patch.object(worker.sys, 'executable', str(engine)), patch.dict(sys.modules, {'metrics_agent.stages.run':stage}):
        result, code = worker.advance(run, root / 'runtime', 'test-model')
    result['log_mode'] = oct((run / 'engine.log').stat().st_mode & 0o777)
    result['code'] = code
print(json.dumps(result))
'''
        result, _ = self.execute(body, 'ADVANCE SIGN IN')
        self.assertEqual(result['code'], 3)
        self.assertEqual(result['gate']['id'], 'a3')
        self.assertIn('waiting at the a3 gate', result['feedback'])
        self.assertEqual(result['log_mode'], '0o600')


if __name__ == '__main__':
    unittest.main()
