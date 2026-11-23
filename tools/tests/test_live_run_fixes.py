"""What 0.9.76's first live dashboard run showed, each fixed at its root: the warehouse record no step wrote, a
generic error before a run had its record, "this folder" for a dashboard project, the check ticked before it ran, a
question from another flow, and the notes step still called "defined"."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from analytics_run import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import checklist  # noqa: E402
import journey  # noqa: E402
from run_facts import RunFacts  # noqa: E402

PROJECT = [sys.executable, str(SCRIPTS / 'project.py')]


class WarehouseRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # The temp folder resolved: on macOS it sits behind a symlink, which the helper refuses.
        self.run = Path(self.tmp.name).resolve() / '.atlan-analytics' / 'report'

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, *extra):
        return subprocess.run(PROJECT + ['--run-dir', str(self.run), 'warehouse', *extra],
                              capture_output=True, text=True, cwd=self.tmp.name)

    def test_the_command_writes_the_record_the_tests_read(self):
        done = self.record('--kind', 'snowflake', '--via', 'atlan', '--tool', 'snow-server__sql_exec_tool',
                           '--server', 'snow-server', '--runner', 'mcp__call_read_tool', '--bulk-via', 'tool')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        state = json.loads((self.run / 'state.json').read_text())
        self.assertEqual(state['warehouse'], {'kind': 'snowflake', 'dialect': 'snowflake', 'via': 'atlan',
                                              'tool': 'snow-server__sql_exec_tool', 'server': 'snow-server',
                                              'runner': 'mcp__call_read_tool', 'bulk': {'via': 'tool'}})

    def test_it_keeps_what_the_state_already_holds(self):
        self.run.mkdir(parents=True)
        (self.run / 'state.json').write_text(json.dumps({'decisions': [{'key': 'k'}]}))
        self.record('--kind', 'databricks', '--via', 'cli', '--tool', 'databricks', '--profile', 'work')
        state = json.loads((self.run / 'state.json').read_text())
        self.assertEqual(state['decisions'], [{'key': 'k'}])
        self.assertEqual(state['warehouse']['profile'], 'work')

    def test_a_route_without_its_details_is_refused_by_name(self):
        refused = self.record('--kind', 'snowflake', '--via', 'atlan', '--tool', 't')
        self.assertEqual(refused.returncode, 2)
        self.assertIn('--server and --runner', json.loads(refused.stdout)['message'])
        self.assertFalse((self.run / 'state.json').exists())


class MissingRunRecord(unittest.TestCase):
    def test_a_step_before_the_run_exists_says_what_makes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / '.atlan-analytics' / 'report'
            (run / 'engine').mkdir(parents=True)
            out = subprocess.run(PROJECT + ['--run-dir', str(run), 'journey', 'agentic'],
                                 capture_output=True, text=True, cwd=tmp)
            message = json.loads(out.stdout.strip().splitlines()[-1])['message']
            self.assertNotIn('Project step could not complete', message)
            self.assertIn('PROJECT survey', message)


class DashboardProjectFolder(unittest.TestCase):
    def test_a_run_in_a_dashboard_project_names_its_folder(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            run = Path(tmp) / '.atlan-analytics' / 'report'
            run.mkdir(parents=True)
            self.assertEqual(RunFacts(run).folder, '~/' + str(Path(tmp).relative_to(Path.home())))


class DashboardItems(unittest.TestCase):
    def model(self, interaction):
        return {'phase': {'code': 'build', 'status': 'running', 'label': 'Building', 'source': 'engine'},
                'counts': {'metrics': 0, 'queries': 0, 'released': 0}, 'progress': {},
                'interaction': {'flow': 'dashboard', 'mode': 'active', **interaction}, 'engine': {}}

    def test_a_build_the_fetch_started_ticks_neither_early_item(self):
        # A Power BI link's fetch advances the build before the pages are picked or the check is recorded.
        rows = {r['id']: r['status'] for r in checklist.build(self.model({}))['steps']}
        self.assertEqual((rows['intent'], rows['scope'], rows['readiness']), ('completed', 'in_progress', 'pending'))
        rows = {r['id']: r['status'] for r in checklist.build(self.model({'report': 'R: all pages'}))['steps']}
        self.assertEqual((rows['scope'], rows['readiness']), ('completed', 'in_progress'))

    def test_the_notes_item_names_the_saved_queries_too(self):
        model = dict(self.model({'report': 'R', 'readiness': {'result': '1 of 1 tiles'}}),
                     phase={'code': 'check', 'status': 'running', 'label': 'Checking', 'source': 'project'},
                     counts={'metrics': 1, 'queries': 1, 'released': 0})
        write = {r['id']: r for r in checklist.build(model)['steps']}['write']
        self.assertEqual(write['content'], 'Add detailed information to 1 metric and 1 saved query')


class DashboardContinuation(unittest.TestCase):
    def test_the_dashboard_branch_is_never_told_to_ask_for_a_business_area(self):
        model = {'phase': {'code': 'build', 'status': 'running', 'source': 'engine'}, 'counts': {'held': 0, 'provisional': 0},
                 'interaction': {'flow': 'dashboard', 'mode': 'active', 'intent': {}}, 'intent_checkpoint': True,
                 'warnings': [], 'items': [], 'auth_status': None}
        self.assertNotEqual(journey.continuation(model)['action'], 'choose-domain')
        model['interaction']['flow'] = 'build'
        self.assertEqual(journey.continuation(model)['action'], 'choose-domain')


class DemoRunFixes(unittest.TestCase):
    """What 0.9.77's Fixit demo run showed."""

    def test_a_demo_names_its_topic_in_words_not_its_focus_label(self):
        from analytics_run import make_run
        import questions
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(Path(tmp), local=True, demo=True)
            facts = {'firm': 'Fixit', 'ready': {'agent': 'job profit agent',
                                                'answers': 'Questions about job margin, rework and late jobs'}}
            text = questions.close(run, data=[facts])['text']
            self.assertIn('ask anything about job margin, rework and late jobs', text)

    def test_a_half_percent_rounds_up(self):
        import inspect
        import analytics_eval
        source = inspect.getsource(analytics_eval.result_facts)
        self.assertIn('int(100 * k / n + 0.5)', source, '1 of 8 reads 13%, not 12%')


class SignInRunFixes(unittest.TestCase):
    """What 0.9.82's live Power BI run showed: a report fetched from its link was missing from the sources chip, and
    nothing told the person what happens after they sign in."""

    def test_a_report_fetched_from_its_link_counts_as_a_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / '.atlan-metrics' / 'run'
            (run / 'engine').mkdir(parents=True)
            (run / 'engine' / 'bi_fetch.json').write_text(json.dumps({'plug': 'powerbi-fabric'}))
            found = RunFacts(run).found
            self.assertEqual(found[0], 1)
            self.assertEqual(found[2], ['Power BI report'])

    def test_the_browser_page_after_sign_in_says_where_to_go(self):
        source = (SCRIPTS.parents[1] / 'metrics-generation' / 'modules' / 'metrics' / 'engine' / 'packages'
                  / 'metrics-agent-powerbi' / 'src' / 'metrics_agent_powerbi' / 'pbi_definition_extract.py').read_text()
        self.assertIn('Go back to your', source)
        self.assertIn('about a minute', source)


if __name__ == '__main__':
    unittest.main()
