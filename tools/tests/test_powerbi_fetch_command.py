"""One command fetches a Power BI report link, run in the background while the person is here.

A live session (2026-11-22) assembled setup, the run settings, preparation and the fetch by
hand, ran the fetch in a terminal tab and read that tab with ever longer waits: three minutes
idle after the fetch had finished. `ENGINE powerbi-report` does all four in one background
command that wakes the assistant when it exits. With no terminal, the person still signs in in
their own browser (the command marks itself attended), and the engine's lines, the sign-in
address and "Signed in." among them, are relayed as they come.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation'
MODULE = METRICS / 'modules/metrics'
POWERBI = MODULE / 'engine/packages/metrics-agent-powerbi/src'
sys.path.insert(0, str(METRICS))
sys.path.insert(0, str(MODULE))
from modules.metrics import driver  # noqa: E402
import terminal_io  # noqa: E402

DEPENDENCIES = all(importlib.util.find_spec(n) for n in ('sqlglot', 'yaml', 'jsonschema'))
WS = '08E85E27-0000-4000-8000-00000000000A'
REPORT = '3013059b-0000-4000-8000-00000000000b'
CANONICAL = f'https://app.powerbi.com/groups/{WS.lower()}/reports/{REPORT}'


class ReportLink(unittest.TestCase):
    def test_a_page_and_query_are_dropped_and_ids_are_lowercase(self):
        link = f'https://app.powerbi.com/groups/{WS}/reports/{REPORT}/a6b3fb4e221e15d23e78?experience=power-bi'
        self.assertEqual(driver.powerbi_report(link), (CANONICAL, f'powerbi:{WS.lower()}:{REPORT}'))
        self.assertEqual(driver.powerbi_report(CANONICAL + '/')[0], CANONICAL)

    def test_other_links_are_refused_by_name(self):
        for link, words in ((f'http://app.powerbi.com/groups/{WS}/reports/{REPORT}', 'Power BI report link'),
                            (f'https://example.com/groups/{WS}/reports/{REPORT}', 'Power BI report link'),
                            (f'https://app.powerbi.com.example.com/groups/{WS}/reports/{REPORT}',
                             'Power BI report link'),
                            ('https://app.powerbi.com/home', 'Power BI report link'),
                            (f'https://app.powerbi.com/groups/{WS}/list', 'workspace, not one report'),
                            (f'https://app.powerbi.com/groups/me/reports/{REPORT}', 'My workspace')):
            with self.subTest(link=link), self.assertRaisesRegex(ValueError, words):
                driver.powerbi_report(link)


class Settings(unittest.TestCase):
    def test_the_settings_an_agent_wrote_by_hand(self):
        doc = driver.powerbi_config(CANONICAL, 'snowflake')
        self.assertEqual(doc['inputs'], {'history': [], 'schema': [], 'bi': ['powerbi-fabric'], 'sql_check': None})
        self.assertEqual(doc['settings'], {'powerbi-fabric': {'targets': [CANONICAL], 'with_model': True}})
        self.assertEqual((doc['dialect'], doc['witness_mode'], doc['publish'], doc['observers']),
                         ('snowflake', 'parse', ['files'], []))
        # No sign-in named: the connector picks the browser where one opens, else a code.
        self.assertEqual(driver.powerbi_config(CANONICAL, 'snowflake', 'device')['settings']['powerbi-fabric']['sign_in'],
                         'device')

    def test_only_the_engines_own_line_says_signed_in(self):
        self.assertEqual(driver.sign_in_seen('=== s_fetch_bi ===\nSign in to Power BI...\nSigned in.\n'), 'signed_in')
        self.assertEqual(driver.sign_in_seen('Using your saved Power BI sign-in.\n'), 'saved')
        self.assertEqual(driver.sign_in_seen('Sign in to Power BI in your browser.\n'), 'not_shown')


class OneCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()  # macOS's temporary folder sits behind a link
        self.root = base / 'runtime'
        self.run_dir = base / 'run_1' / 'engine'
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(terminal_io.ATTENDED, None)
        self.calls = []

    def fake_invoke(self, prepare=None, log='Sign in to Power BI.\nSigned in.\n'):
        def invoke(root, command, args):
            self.calls.append((command, args, os.environ.get(terminal_io.ATTENDED)))
            if command == 'prepare':
                return prepare or {'status': 'prepared', 'run': str(self.run_dir)}, 0
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with (self.run_dir / 'engine.log').open('a') as handle:
                handle.write(log)
            return {'status': 'paused', 'gate': {'id': 'a3b'}}, 3
        return invoke

    def fetch(self, invoke, features=(), **kwargs):
        setup = mock.Mock(return_value={'status': 'ready', 'runtime': str(self.root)})
        with mock.patch.object(driver, 'setup', setup), mock.patch.object(driver, 'invoke', invoke):
            result = driver.fetch_powerbi(self.root, self.run_dir, CANONICAL + '/page?x=1', 'Snowflake',
                                          'model-id', features, **kwargs)
        return result, setup

    def test_setup_settings_prepare_and_fetch_in_one_attended_command(self):
        (result, code), setup = self.fetch(self.fake_invoke(), features=['snowflake'])
        self.assertEqual(code, 3)
        setup.assert_called_once_with(self.root, ['powerbi', 'snowflake'])
        self.assertEqual([c[0] for c in self.calls], ['prepare', 'advance'])
        self.assertTrue(all(c[2] == '1' for c in self.calls), 'the engine is told a person is here')
        config = self.run_dir.parent / 'private' / 'engine-config.json'
        self.assertEqual(json.loads(config.read_text()), driver.powerbi_config(CANONICAL, 'snowflake'))
        self.assertIn(str(config), self.calls[0][1])
        self.assertEqual(self.calls[1][1], ['--run', str(self.run_dir), '--model', 'model-id'])
        self.assertEqual(result['sign_in'], 'signed_in')
        self.assertEqual(result['source_id'], f'powerbi:{WS.lower()}:{REPORT}')
        self.assertEqual((result['engine_run'], result['report']), (str(self.run_dir), CANONICAL))

    def test_a_sign_in_from_an_earlier_fetch_is_not_this_ones(self):
        self.run_dir.mkdir(parents=True)
        (self.run_dir / 'engine.log').write_text('Signed in.\n')
        (result, _), _ = self.fetch(self.fake_invoke(log='=== a3_translate ===\n'))
        self.assertEqual(result['sign_in'], 'not_shown')

    def test_a_refused_preparation_stops_before_the_fetch(self):
        refused = {'status': 'NEEDS_CONTEXT', 'requirements': 'synthetic'}
        (result, code), _ = self.fetch(self.fake_invoke(prepare=refused))
        self.assertEqual((result, code), (refused, 2))
        self.assertEqual([c[0] for c in self.calls], ['prepare'])

    def test_running_it_again_resumes_and_other_settings_are_refused(self):
        self.fetch(self.fake_invoke())
        (_, code), _ = self.fetch(self.fake_invoke())
        self.assertEqual(code, 3)
        with self.assertRaisesRegex(ValueError, 'other settings'):
            self.fetch(self.fake_invoke(), sign_in='device')

    def test_a_runtime_with_more_features_is_reused_as_built(self):
        self.root.mkdir()
        (self.root / 'runtime.json').write_text(json.dumps({'features': ['powerbi', 'snowflake']}))
        _, setup = self.fetch(self.fake_invoke())
        setup.assert_called_once_with(self.root, ['powerbi', 'snowflake'])

    def test_a_blocked_setup_is_returned_as_it_is(self):
        blocked = {'status': 'BLOCKED', 'reason': 'synthetic'}
        with mock.patch.object(driver, 'setup', return_value=blocked), \
                mock.patch.object(driver, 'invoke', self.fake_invoke()):
            self.assertEqual(driver.fetch_powerbi(self.root, self.run_dir, CANONICAL, 'snowflake', 'm'),
                             (blocked, 2))
        self.assertEqual(self.calls, [])

    def test_the_command_line(self):
        fetch = mock.Mock(return_value=({'status': 'paused'}, 3))
        out = io.StringIO()
        with mock.patch.object(driver, 'fetch_powerbi', fetch), mock.patch.object(sys, 'stdout', out), \
                mock.patch.object(driver, 'SUPPORTED_PYTHON', (tuple(sys.version_info[:2]),)):
            code = driver.main(['--runtime', str(self.root), 'powerbi-report', '--link', CANONICAL,
                                '--run', str(self.run_dir), '--dialect', 'snowflake', '--model', 'm',
                                '--feature', 'snowflake'])
        self.assertEqual(code, 0, 'a pause at the first gate exits 0, so the app does not show it as failed')
        self.assertEqual(json.loads(out.getvalue())['status'], 'paused')
        fetch.assert_called_once_with(self.root, str(self.run_dir), CANONICAL, 'snowflake', 'm',
                                      ['snowflake'], 'auto', None, None)


class AttendedRelay(unittest.TestCase):
    def test_without_a_terminal_an_attended_command_relays_each_line(self):
        relayed = io.StringIO()
        code = "import sys; print('SIGN IN AT https://example.invalid', file=sys.stderr); print('{}')"
        with mock.patch.dict(os.environ, {terminal_io.ATTENDED: '1'}), \
                mock.patch.object(terminal_io, 'at_terminal', return_value=False), \
                mock.patch.object(sys, 'stderr', relayed):
            result = terminal_io.run([sys.executable, '-c', code], env=dict(os.environ), timeout=30)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, '{}'))
        self.assertIn('SIGN IN AT', result.stderr)
        self.assertIn('SIGN IN AT', relayed.getvalue())

    def test_an_unattended_command_still_captures_quietly(self):
        relayed = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != terminal_io.ATTENDED}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(terminal_io, 'at_terminal', return_value=False), \
                mock.patch.object(sys, 'stderr', relayed):
            result = terminal_io.run([sys.executable, '-c', "import sys; print('x', file=sys.stderr)"], env=env)
        self.assertIn('x', result.stderr)
        self.assertEqual(relayed.getvalue(), '')
        self.assertFalse(terminal_io.relaying())


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class ConnectorSignIn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(MODULE / 'engine/src'))
        sys.path.insert(0, str(POWERBI))
        from metrics_agent_powerbi import pbi_definition_extract as x
        from metrics_agent_powerbi import plug
        cls.x, cls.plug = x, plug

    def test_an_attended_command_signs_the_person_in_without_a_terminal(self):
        with tempfile.TemporaryDirectory() as cache, \
                mock.patch.dict(os.environ, {'PATH': '/usr/bin', 'XDG_CACHE_HOME': cache,
                                             self.plug.ATTENDED_ENV: '1'}, clear=True), \
                mock.patch.object(self.plug, 'at_terminal', return_value=False), \
                mock.patch.object(self.x, 'person_sign_in', return_value='browser'):
            self.assertEqual(self.plug.sign_in({}), 'browser')
            self.assertEqual(self.plug.sign_in({'sign_in': 'device'}), 'device')

    def test_the_connector_and_the_module_read_one_variable(self):
        self.assertEqual(self.plug.ATTENDED_ENV, terminal_io.ATTENDED)


if __name__ == '__main__':
    unittest.main()
