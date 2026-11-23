"""The setup checklist mirrors saved progress and never claims a step the run has not reached."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import analytics_ux_prototype as fixture
import checklist
import experience

PROJECT = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts/project.py'


class Checklist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = fixture.ContextState(Path(self.tmp.name).resolve())
        self.run = fixture.seed_run(self.output, 'checked')

    def tearDown(self):
        self.tmp.cleanup()

    def steps(self):
        return checklist.build(experience.snapshot(self.run), compact=True)

    def compact(self, result=None, run=None):
        # A journey result's list, as the side view keeps it: the metrics item whole.
        return checklist.build(experience.snapshot(run or self.run), compact=True)

    def progress(self, **state):
        self.run.write(self.run.root / 'progress.json', state)

    def bind(self, run, tables=5):
        state = run.read(run.path)
        state['execution'] = {'kind': 'sqlite', 'read_only': True, 'tables': [f'site.t{i}' for i in range(tables)]}
        run.write(run.path, state)

    def test_initial_list_names_seven_build_steps(self):
        rows = checklist.initial(compact=True)
        self.assertEqual([r['content'] for r in rows], [
            'Choose what your agent answers', 'Find your sources', 'Pick tables and dashboards',
            'Build your context: metrics and saved queries', 'Choose where to store your context', 'Ask your agent a question',
            'Build a dashboard from its answers (optional)'])
        self.assertEqual([r['status'] for r in rows], ['in_progress'] + ['pending'] * 6)
        self.assertEqual([r['id'] for r in rows if r['needs_you']], ['intent', 'scope', 'save', 'ask', 'dashboard'])

    def test_initial_demo_list_starts_with_demo_data(self):
        rows = checklist.initial('demo', compact=True)
        self.assertEqual([r['content'] for r in rows], [
            'Load the demo data', 'Choose what to build your agent from', 'Build your context: metrics and saved queries',
            'Choose where to store your context', 'Ask your agent a question'])

    def test_demo_flow_is_recorded_and_ends_when_the_question_is_answered(self):
        fixture.seed_checked(self.run, 'checked')
        self.bind(self.run)
        self.run.journey('demo')
        self.run.journey('keep-local')
        result = self.steps()
        self.assertEqual(result['flow'], 'demo')
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 4 + ['in_progress'])
        self.run.journey('asked')
        result = self.steps()
        self.assertEqual((result['state'], result['step'], result['of']), ('done', 5, 5))

    def test_a_demo_kept_local_after_its_check_moves_on_to_the_question(self):
        self.bind(self.run)
        self.run.journey('demo')
        self.progress(stage='check', status='complete', completed_items=3, total_items=3)
        self.run.journey('keep-local')
        self.assertEqual([r['status'] for r in self.steps()['steps']], ['completed'] * 4 + ['in_progress'])
        self.run.journey('asked')
        result = self.steps()
        self.assertEqual((result['state'], result['step'], result['of']), ('done', 5, 5))

    def test_demo_data_step_waits_for_bound_data_and_the_demo_never_asks_for_a_focus(self):
        self.progress(stage='inspect', status='complete', completed_items=3, total_items=3)
        result = self.run.journey('demo')
        self.assertNotIn('business area', result['chat'])
        self.assertNotIn(result['continuation']['action'], ('choose-domain', 'choose-purpose'))
        step = self.compact(result)['steps'][0]
        self.assertEqual((step['content'], step['status']), ('Load the demo data', 'in_progress'))
        self.bind(self.run)
        step = self.steps()['steps'][0]
        self.assertEqual((step['content'], step['status']), ('Load the demo data: 5 tables', 'completed'))

    def test_keep_local_after_the_checks_leaves_the_question_next(self):
        # Keeping the work here answers the save question, which follows the checked sample.
        for flow, saved in (('build', 5), ('demo', 4)):
            with self.subTest(flow=flow):
                run = fixture.seed_run(self.output, 'kept-' + flow)
                fixture.seed_checked(run, 'checked')
                run.write(run.root / 'progress.json', {'stage': 'check', 'status': 'complete'})
                self.bind(run)
                if flow == 'demo':
                    run.journey('demo')
                result = run.journey('keep-local')
                self.assertEqual(result['continuation']['action'], 'explore-local')
                self.assertEqual([r['status'] for r in self.compact(result, run)['steps']],
                                 ['completed'] * saved + ['in_progress'] + ['pending'] * (flow == 'build'))
                self.assertEqual(self.compact(result, run)['steps'][saved - 1]['content'],
                                 'Choose where to store your context: on this machine')

    def test_one_step_runs_at_a_time_with_real_counts(self):
        self.progress(stage='inspect', status='running', completed_items=1, total_items=3)
        result = self.steps()
        # The use case was asked in chat before this run existed; the survey is what is under way.
        self.assertEqual([r['status'] for r in result['steps']], ['pending', 'in_progress'] + ['pending'] * 5)
        self.assertEqual(result['steps'][1]['active'], 'Reading 2 of 3')
        self.assertFalse(result['steps'][0]['needs_you'])
        self.assertEqual((result['step'], result['of'], result['state']), (2, 7, 'running'))
        self.progress(stage='inspect', status='complete', completed_items=3, total_items=3)
        result = self.steps()
        self.assertEqual(result['steps'][1], {'id': 'sources', 'content': 'Find your sources: 3 found', 'active': 'Looking at your warehouse',
                                              'needs_you': False, 'status': 'completed'})
        # The survey is done and no use case is saved yet: that step is the one in progress, and says so.
        self.assertEqual((result['steps'][0]['status'], result['steps'][0]['needs_you'], result['steps'][0]['active']),
                         ('in_progress', True, 'Waiting for your choice'))
        self.run.journey('domain', answer='Shipping   cost')
        step = self.steps()['steps'][0]
        self.assertEqual((step['content'], step['active'], step['status']), ('Choose what your agent answers: Shipping cost', 'Waiting for your choice', 'in_progress'))
        self.run.journey('purpose', answer='Prepare team metrics')
        self.assertEqual([r['status'] for r in self.steps()['steps']][:3], ['completed', 'completed', 'in_progress'])

    def test_counts_sharpen_step_names_and_checks_are_counted(self):
        fixture.seed_checked(self.run, 'checked')
        self.progress(stage='check', status='running', completed_items=1, total_items=3)
        result = self.steps()
        rows = {r['id']: r for r in result['steps']}
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 3 + ['in_progress'] + ['pending'] * 3)
        self.assertEqual(rows['metrics']['active'], 'Testing 2 of 3')
        # A count the run did not save is never invented.
        self.progress(stage='check', status='running', completed_items=9, total_items=3)
        self.assertEqual({r['id']: r for r in self.steps()['steps']}['metrics']['active'], 'Building your context')

    def test_paused_and_local_runs_are_reported_as_they_are(self):
        fixture.seed_checked(self.run, 'checked')
        self.progress(stage='check', status='waiting-for-user')
        result = self.steps()
        self.assertEqual(result['state'], 'paused')
        # 0.9.74: after a pause the started step read "pending". It keeps its place, marked as paused.
        stopped = [r for r in result['steps'] if r['status'] == 'in_progress']
        self.assertEqual([(r['id'], r['active']) for r in stopped], [('metrics', checklist.PAUSED)])
        expanded = checklist.build(experience.snapshot(self.run))['steps']
        self.assertEqual([r['active'] for r in expanded if r['status'] == 'in_progress'], [checklist.PAUSED])
        self.progress(stage='preview', status='running')
        save = {r['id']: r for r in self.steps()['steps']}['save']
        self.assertEqual((save['status'], save['needs_you']), ('in_progress', True))
        self.run.journey('keep-local')
        result = self.steps()
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 5 + ['in_progress', 'pending'])
        self.assertEqual(({r['id']: r for r in result['steps']}['save']['content'], result['state']), ('Choose where to store your context: on this machine', 'running'))
        self.assertEqual(self.compact(self.run.journey('status')), result)

    def test_a_recorded_source_choice_moves_the_tracker_to_the_build(self):
        fixture.seed_checked(self.run, 'checked')
        self.bind(self.run)
        self.run.journey('demo')
        self.progress(stage='scope', status='complete')
        result = self.steps()
        self.assertEqual([r['status'] for r in result['steps']][:3], ['completed', 'completed', 'in_progress'])

    def test_initial_dashboard_list_has_its_own_eight_items(self):
        rows = checklist.initial('dashboard', compact=True)
        self.assertEqual([r['content'] for r in rows], [
            'Choose what your agent answers: from your dashboard', 'Pick report and pages', 'Check what your agent can use',
            'Build your context: metrics and saved queries', 'Validate the context on your data',
            'Choose where to store your context', 'Ask your agent a question', 'Build a dashboard from its answers (optional)'])
        # It waits at the report, the save, the question and the page; the check waits only when it changes the plan.
        self.assertEqual([r['id'] for r in rows if r['needs_you']], ['scope', 'save', 'ask', 'dashboard'])

    def test_the_dashboard_list_starts_with_card_one_already_answered(self):
        # Card 1 is answered before the branch starts: its item is ticked, so the list the person saw keeps its place.
        for compact in (True, False):
            rows = checklist.initial('dashboard', compact=compact)
            self.assertEqual((rows[0]['id'], rows[0]['status'], rows[0]['needs_you']), ('intent', 'completed', False))
            self.assertEqual((rows[1]['id'], rows[1]['status']), ('scope', 'in_progress'), 'item 2 is the one under way')
            self.assertEqual([r['status'] for r in rows[2:]], ['pending'] * (len(rows) - 2))
        self.progress(stage='inspect', status='running')
        first = self.compact(self.run.journey('agentic'))['steps'][0]
        self.assertEqual((first['content'], first['status']), ('Choose what your agent answers: from your dashboard', 'completed'))

    def test_dashboard_items_show_their_results_and_the_check_waits_only_when_it_changes_the_plan(self):
        self.progress(stage='inspect', status='running')
        result = self.compact(self.run.journey('agentic'))
        self.assertEqual((result['flow'], result['of']), ('dashboard', 8))
        first = result['steps'][1]
        self.assertEqual((first['id'], first['status'], first['needs_you']), ('scope', 'in_progress', True))
        result = self.compact(self.run.journey('report', answer='Sales pipeline:  Overview, Stages'))
        self.assertEqual(result['steps'][1]['content'], 'Pick report and pages: Sales pipeline: Overview, Stages')
        check = result['steps'][2]
        self.assertEqual((check['status'], check['needs_you']), ('in_progress', False), 'a check that finds nothing needs nobody')
        check = self.compact(self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date', waits=True))['steps'][2]
        self.assertEqual((check['status'], check['needs_you']), ('in_progress', True))
        self.assertIn('18 of 25 tiles', check['active'])
        result = self.compact(self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date'))
        self.assertEqual(result['steps'][2]['content'], 'Check what your agent can use: 18 of 25 tiles, stage list out of date')
        self.assertEqual([r['status'] for r in result['steps']][:4], ['completed', 'completed', 'completed', 'in_progress'],
                         'the build starts on its own: there is no confirm item')
        again = self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date')
        self.assertIn('drafts --wait', again['next'], 'every host waits for the build in one turn')
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}):
            started = self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date')
        # d4, 2026-11-19: the session started the build's helper, ended its turn and said nothing for 8 minutes.
        self.assertIn('`PROJECT drafts --wait --engine-dir engine` in the foreground', started['next'])
        # d5: the session piped this result through a filter that printed only the checklist, so the step never reached
        # it. The step that must not be missed goes to stderr too, which a filter on the output does not drop.
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run.root), 'journey', 'readiness', '--answer',
                              '18 of 25 tiles, stage list out of date'], capture_output=True, text=True,
                             env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('drafts --wait --engine-dir engine', out.stderr)

    def test_the_run_records_whether_the_host_keeps_a_task_list(self):
        self.run.journey('tasks', answer='yes')
        self.assertIs(json.loads((self.run.root / 'interaction.json').read_text(encoding='utf-8'))['tasks'], True)
        self.run.journey('tasks', answer='no')
        self.assertIs(json.loads((self.run.root / 'interaction.json').read_text(encoding='utf-8'))['tasks'], False)
        with self.assertRaises(ValueError):
            self.run.journey('tasks', answer='maybe')

    def test_the_checklist_asks_a_terminal_session_to_record_its_task_list_once(self):
        # The person, 2026-11-19: the terminal's task list carries the metric steps where it has one; the helper cannot see
        # the agent's tools, so the agent records it, and the checklist asks until it has (on stderr: results get filtered).
        def checklist_cli(entry):
            return subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run.root), 'checklist'], capture_output=True,
                                  text=True, env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': entry})
        self.assertIn('journey tasks --answer', checklist_cli('').stderr)
        self.assertNotIn('journey tasks', checklist_cli('claude-desktop').stderr, 'the desktop app keeps one')
        # d7 ran the checklist at the start and never set the app's task list: its plan tool was deferred.
        self.assertIn('plan tool, loading it first if it is deferred', checklist_cli('claude-desktop').stderr)
        self.run.journey('tasks', answer='no')
        self.assertNotIn('journey tasks', checklist_cli('').stderr, 'asked once')

    def test_every_journey_record_asks_a_terminal_session_for_its_task_list_until_it_is_recorded(self):
        # h17t never ran the checklist, so its ask never came: the task tools were deferred and never loaded, and the
        # build ran with no task list. Every run records its flow early (`journey demo`, `journey agentic`).
        def journey_cli(entry, *args):
            return subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run.root), 'journey', *args],
                                  capture_output=True, text=True, env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': entry})
        said = journey_cli('', 'demo').stderr
        self.assertIn('`PROJECT checklist`', said)
        self.assertIn('deferred', said, 'tools a session has to load first')
        self.assertIn('journey tasks --answer', said)
        self.assertNotIn('journey tasks', journey_cli('claude-desktop', 'demo').stderr, 'the desktop app keeps one')
        self.run.journey('tasks', answer='yes')
        self.assertNotIn('journey tasks', journey_cli('', 'status').stderr, 'once recorded, never again')

    def test_dashboard_page_item_ticks_when_built_or_declined(self):
        fixture.seed_checked(self.run, 'checked')
        self.bind(self.run)
        self.run.journey('agentic')
        self.progress(stage='check', status='complete')
        self.run.journey('keep-local')
        self.run.journey('asked')
        result = self.steps()
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 7 + ['in_progress'])
        self.assertEqual(result['steps'][5]['content'], 'Choose where to store your context: on this machine')
        self.run.journey('dashboard', answer='declined')
        result = self.steps()
        self.assertEqual((result['state'], result['steps'][7]['content']),
                         ('done', 'Build a dashboard from its answers (optional): declined'))

    def test_dashboard_records_refuse_what_they_cannot_hold(self):
        import journey
        self.run.journey('agentic')
        with self.assertRaises(journey.JourneyError):
            self.run.journey('report', answer='  ')
        with self.assertRaises(journey.JourneyError):
            self.run.journey('report', answer='Sales', waits=True)
        with self.assertRaises(journey.JourneyError):
            self.run.journey('dashboard', answer='maybe')

    def test_the_checks_are_the_persons_choice_and_the_check_item_says_so(self):
        import journey
        fixture.seed_checked(self.run, 'checked')
        self.run.journey('agentic')
        self.progress(stage='check', status='running')
        self.assertNotEqual({r['id']: r for r in self.steps()['steps']}['check']['status'], 'completed')
        with self.assertRaises(journey.JourneyError):
            self.run.journey('checks', answer='maybe')
        steps = {r['id']: r for r in self.compact(self.run.journey('checks', answer='skip'))['steps']}
        self.assertEqual((steps['check']['status'], steps['check']['content']),
                         ('completed', 'Validate the context on your data: skipped, as you chose'))

    def test_the_test_item_stays_open_until_every_test_has_its_verdict(self):
        # 0.9.74, run 2: "run" ticked "0 of 40 tested, 22 need your review" at once. The structural check records the
        # check stage complete before any test runs.
        fixture.seed_checked(self.run, 'checked')
        self.run.journey('agentic')
        self.progress(stage='check', status='complete')
        steps = {r['id']: r for r in self.compact(self.run.journey('checks', answer='run'))['steps']}
        self.assertEqual((steps['check']['status'], steps['check']['content']), ('in_progress', 'Validate the context on your data'))
        path = self.run.context / 'candidate-checks.json'
        checks = self.run.read(path)
        for entry in checks.values():
            entry['status'] = 'executed'
        self.run.write(path, checks)
        self.assertEqual({r['id']: r for r in self.steps()['steps']}['check']['status'], 'completed')

    def test_own_data_validate_stays_open_until_every_test_has_its_verdict(self):
        # 0.9.83, tables and warehouse routes: "Validate the context on your data: 0 of 6 tested" ticked as the build
        # ended, before any test ran. The dashboard branch had this fix since 0.9.74.
        fixture.seed_checked(self.run, 'checked')
        self.progress(stage='check', status='complete')
        expanded = lambda: {r['id']: r for r in checklist.build(experience.snapshot(self.run))['steps']}
        self.assertNotEqual(expanded()['test']['status'], 'completed')
        self.assertNotEqual({r['id']: r for r in self.steps()['steps']}['metrics']['status'], 'completed')
        path = self.run.context / 'candidate-checks.json'
        checks = self.run.read(path)
        for entry in checks.values():
            entry['status'] = 'executed'
        self.run.write(path, checks)
        self.assertEqual(expanded()['test']['status'], 'completed')

    def test_a_readiness_result_that_waits_keeps_its_item_open_while_the_build_is_prepared(self):
        # 0.9.74, run 3: the check advances the build to its first written step, and the waiting result ticked anyway.
        self.run.journey('agentic')
        self.run.journey('report', answer='Pod targets: all pages')
        self.progress(stage='build', status='running')
        steps = self.compact(self.run.journey('readiness', answer="0 of 1 tiles, report's model not readable", waits=True))['steps']
        self.assertEqual([(r['id'], r['status']) for r in steps[2:4]], [('readiness', 'in_progress'), ('metrics', 'pending')])
        self.assertTrue(steps[2]['needs_you'])
        steps = self.compact(self.run.journey('readiness', answer="0 of 1 tiles, report's model not readable"))['steps']
        self.assertEqual(steps[2]['status'], 'completed')

    def test_other_flows_keep_their_lists(self):
        self.assertEqual(len(checklist.initial(compact=True)), 7)
        self.assertEqual(checklist.initial(compact=True)[0]['content'], 'Choose what your agent answers')
        self.assertEqual(len(checklist.initial('demo', compact=True)), 5)
        self.assertEqual(self.steps()['flow'], 'build')

    def test_command_reads_only_and_starts_before_a_run_exists(self):
        fresh = Path(self.tmp.name).resolve() / 'not-started'
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), 'checklist'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)['steps'], checklist.initial())
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), 'checklist', '--flow', 'dashboard'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((json.loads(out.stdout)['flow'], json.loads(out.stdout)['steps']), ('dashboard', checklist.initial('dashboard')))
        self.assertFalse(fresh.exists())
        before = {str(p): p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run.root), 'checklist'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(json.loads(out.stdout)['steps']), 10, 'seven steps, the build as four')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()


class ThreeSteps(unittest.TestCase):
    """Team review, 2026-11-18: metric generation is its steps in the host's own task list (draft, merge, write, and on
    own data and demos the validation), each with a time estimate fitted to measured runs, so the person sees the flow
    and how long it takes."""

    def model(self, done=(), counts=None, flow='demo'):
        return {'phase': {'code': 'build', 'status': 'working', 'label': 'Building', 'source': 'engine'},
                'counts': {'metrics': 0, 'queries': 0, 'released': 0}, 'progress': {},
                # A dashboard run that is building has its report picked and its check recorded: only then are its
                # first items done (a Power BI link's fetch starts the build before either).
                'interaction': {'flow': flow, 'mode': 'active', 'intent': {'domain': 'x', 'purpose': 'y'},
                                **({'report': 'R: all pages', 'readiness': {'result': '4 of 4 tiles'}} if flow == 'dashboard' else {})},
                'bound_tables': 5, 'engine': {'done': list(done), 'counts': counts or {}}}

    def test_the_lists_show_the_build_as_its_steps(self):
        self.assertEqual([r['content'] for r in checklist.initial('demo')], [
            'Load the demo data', 'Choose what to build your agent from', 'Build your context: metrics and saved queries',
            'Merge duplicate metrics', 'Add detailed information to each metric and saved query', 'Validate the context on your data',
            'Choose where to store your context', 'Ask your agent a question'])
        build = [r['content'] for r in checklist.initial('build')]
        self.assertEqual(build[3:7], ['Build your context: metrics and saved queries', 'Merge duplicate metrics',
                                      'Add detailed information to each metric and saved query', 'Validate the context on your data'])
        build_ids = [r['id'] for r in checklist.initial('build')]
        self.assertEqual(build_ids[3:7], ['draft', 'merge', 'write', 'test'])
        # The dashboard branch validates in an item of its own, so its build is three steps.
        dashboard = checklist.initial('dashboard')
        self.assertEqual([r['id'] for r in dashboard][3:7], ['draft', 'merge', 'write', 'check'])
        self.assertEqual(dashboard[5]['content'], 'Add detailed information to each metric and saved query')
        self.assertEqual([len(build), len(dashboard), len(checklist.initial('demo'))], [10, 10, 8])

    def test_each_step_ticks_as_the_engine_finishes_it_with_its_count_and_estimate(self):
        counts = {'m2_emit': {'queries': 30}, 'x1_ingest': {'proposals': 26}}
        start = {r['id']: r for r in checklist.build(self.model((), counts))['steps']}
        self.assertEqual((start['draft']['status'], start['draft']['content']),
                         ('in_progress', 'Build your context: metrics and saved queries (about 5 min)'))
        # The list reaches the person when the queries are described (h8, 4.6 min): what is left is finding the numbers.
        # Measured for 30 queries: 2.0, 1.9 and 2.0 min headless, 3.1 on the desktop; the whole step said "about 5".
        rest = {r['id']: r for r in checklist.build(self.model(('m2_ingest',), counts))['steps']}
        self.assertEqual(rest['draft']['content'], 'Build your context: metrics and saved queries (about 2 min)')
        rows = {r['id']: r for r in checklist.build(self.model(('m2_ingest', 'x1_ingest'), counts))['steps']}
        self.assertEqual((rows['draft']['status'], rows['draft']['content']), ('completed', 'Build your context: 26 metrics found'))
        self.assertEqual((rows['merge']['status'], rows['merge']['content']),
                         ('in_progress', 'Merge duplicate metrics (about 3 min)'))
        self.assertEqual((rows['write']['status'], rows['write']['content']), ('pending', 'Add detailed information to each metric and saved query'))
        counts['x2_ingest'] = {'metrics_out': 9}
        rows = {r['id']: r for r in checklist.build(self.model(('m2_ingest', 'x1_ingest', 'x2_ingest'), counts))['steps']}
        self.assertEqual(rows['merge']['content'], 'Merge duplicate metrics: 9 metrics')
        self.assertEqual(rows['write']['content'], 'Add detailed information to each metric and saved query (about 7 min)')

    def test_linking_shows_on_the_write_step_until_it_ends(self):
        # A watched dashboard run defined 40 metrics, then linked them (279 pairs) for 6 minutes with nothing moving in its
        # task list: the define step read as done and the build looked stuck.
        counts = {'m2_emit': {'queries': 46}, 'x1_ingest': {'proposals': 53}, 'x2_ingest': {'metrics_out': 40},
                  'x3_ingest': {'cards': 40}, 'x4_emit': {'candidate_pairs': 279}}
        done = ('m2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest')
        row = {r['id']: r for r in checklist.build(self.model(done, counts, flow='dashboard'))['steps']}['write']
        self.assertEqual((row['status'], row['content']),
                         ('in_progress', 'Add detailed information to each metric and saved query · 40 written, linking related ones '
                                         '(about 6 min)'))
        small = dict(counts, **{'x3_ingest': {'cards': 4}, 'x4_emit': {'candidate_pairs': 10}})
        row = {r['id']: r for r in checklist.build(self.model(done, small))['steps']}['write']
        self.assertEqual(row['content'], 'Add detailed information to each metric and saved query · 4 written, linking related ones (about 1 min)')

    def test_the_validation_completes_after_the_notes_and_carries_the_run_s_progress(self):
        # Own data and demos validate inside the build: the notes are written once the run reaches its checks, and the
        # validation ends with the metrics item, its count after a colon.
        for flow in ('build', 'demo'):
            with self.subTest(flow=flow):
                model = dict(self.model(('m2_ingest', 'x1_ingest', 'x2_ingest'), {'x2_ingest': {'metrics_out': 8}}, flow=flow),
                             phase={'code': 'build', 'status': 'running', 'label': 'Building', 'source': 'engine'},
                             counts={'metrics': 8, 'queries': 30, 'released': 0})
                rows = {r['id']: r for r in checklist.build(model)['steps']}
                self.assertEqual((rows['write']['status'], rows['test']['status']), ('in_progress', 'pending'))
                model.update(phase={'code': 'check', 'status': 'running', 'label': 'Checking', 'source': 'project'},
                             progress={'completed': 4, 'total': 9})
                rows = {r['id']: r for r in checklist.build(model)['steps']}
                self.assertEqual((rows['write']['status'], rows['write']['content']),
                                 ('completed', 'Add detailed information to 8 metrics and 30 saved queries'))
                self.assertEqual((rows['test']['status'], rows['test']['active']), ('in_progress', 'Testing 5 of 9'),
                                 "the run's own line shows on the step under way")
                model.update(phase={'code': 'check', 'status': 'complete', 'label': 'Checked', 'source': 'project'},
                             progress={}, counts={'metrics': 8, 'queries': 30, 'released': 0, 'executed': 38, 'held': 2})
                rows = {r['id']: r for r in checklist.build(model)['steps']}
                self.assertEqual((rows['test']['status'], rows['test']['content']),
                                 ('completed', 'Validate the context on your data: 38 of 38 tested, 2 need your review'))

    def test_the_side_view_keeps_one_metrics_item(self):
        rows = checklist.build(self.model(), compact=True)['steps']
        self.assertIn('metrics', [r['id'] for r in rows])
        self.assertFalse({'draft', 'merge', 'write', 'test'} & {r['id'] for r in rows})
