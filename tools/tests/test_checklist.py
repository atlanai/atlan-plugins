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
            'Clarify your use case', 'Survey your sources', 'Select tables and dashboards',
            'Generate and check metrics', 'Keep local or save to Atlan Trident', 'Ask a question', 'Build a dashboard'])
        self.assertEqual([r['status'] for r in rows], ['in_progress'] + ['pending'] * 6)
        self.assertEqual([r['id'] for r in rows if r['needs_you']], ['intent', 'scope', 'save', 'ask', 'dashboard'])

    def test_initial_demo_list_starts_with_demo_data(self):
        rows = checklist.initial('demo', compact=True)
        self.assertEqual([r['content'] for r in rows], [
            'Choose and load demo data', 'Choose what to build from', 'Build and check metrics',
            'Keep local or save to Atlan Trident', 'Ask a question'])

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
        self.assertEqual((step['content'], step['status']), ('Choose and load demo data', 'in_progress'))
        self.bind(self.run)
        step = self.steps()['steps'][0]
        self.assertEqual((step['content'], step['status']), ('Load demo data: 5 tables', 'completed'))

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
                self.assertEqual(self.compact(result, run)['steps'][saved - 1]['content'], 'Kept on this machine')

    def test_one_step_runs_at_a_time_with_real_counts(self):
        self.progress(stage='inspect', status='running', completed_items=1, total_items=3)
        result = self.steps()
        # The use case was asked in chat before this run existed; the survey is what is under way.
        self.assertEqual([r['status'] for r in result['steps']], ['pending', 'in_progress'] + ['pending'] * 5)
        self.assertEqual(result['steps'][1]['active'], 'Reading source 2 of 3')
        self.assertFalse(result['steps'][0]['needs_you'])
        self.assertEqual((result['step'], result['of'], result['state']), (2, 7, 'running'))
        self.progress(stage='inspect', status='complete', completed_items=3, total_items=3)
        result = self.steps()
        self.assertEqual(result['steps'][1], {'id': 'sources', 'content': 'Survey 3 sources', 'active': 'Surveying your sources',
                                              'needs_you': False, 'status': 'completed'})
        # The survey is done and no use case is saved yet: that step is the one in progress, and says so.
        self.assertEqual((result['steps'][0]['status'], result['steps'][0]['needs_you'], result['steps'][0]['active']),
                         ('in_progress', True, 'Waiting for your use case'))
        self.run.journey('domain', answer='Shipping   cost')
        step = self.steps()['steps'][0]
        self.assertEqual((step['content'], step['active'], step['status']), ('Use case: Shipping cost', 'Waiting for your goal', 'in_progress'))
        self.run.journey('purpose', answer='Prepare team metrics')
        self.assertEqual([r['status'] for r in self.steps()['steps']][:3], ['completed', 'completed', 'in_progress'])

    def test_counts_sharpen_step_names_and_checks_are_counted(self):
        fixture.seed_checked(self.run, 'checked')
        self.progress(stage='check', status='running', completed_items=1, total_items=3)
        result = self.steps()
        rows = {r['id']: r for r in result['steps']}
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 3 + ['in_progress'] + ['pending'] * 3)
        self.assertEqual(rows['metrics']['active'], 'Checking 2 of 3')
        # A count the run did not save is never invented.
        self.progress(stage='check', status='running', completed_items=9, total_items=3)
        self.assertEqual({r['id']: r for r in self.steps()['steps']}['metrics']['active'], 'Generating metrics and queries')

    def test_paused_and_local_runs_are_reported_as_they_are(self):
        fixture.seed_checked(self.run, 'checked')
        self.progress(stage='check', status='waiting-for-user')
        result = self.steps()
        self.assertEqual(result['state'], 'paused')
        self.assertNotIn('in_progress', [r['status'] for r in result['steps']])
        self.progress(stage='preview', status='running')
        save = {r['id']: r for r in self.steps()['steps']}['save']
        self.assertEqual((save['status'], save['needs_you']), ('in_progress', True))
        self.run.journey('keep-local')
        result = self.steps()
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 5 + ['in_progress', 'pending'])
        self.assertEqual(({r['id']: r for r in result['steps']}['save']['content'], result['state']), ('Kept on this machine', 'running'))
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
            'Pick report and pages', 'Check what can be built', 'Build metrics and queries', 'Check against the dashboard',
            'Add past queries (optional)', 'Save or keep local', 'Ask a question', 'Build a page from the metrics (optional)'])
        # It waits at the report, past queries, the save, the question and the page; the check waits only when it changes
        # the plan.
        self.assertEqual([r['id'] for r in rows if r['needs_you']], ['scope', 'history', 'save', 'ask', 'dashboard'])

    def test_dashboard_items_show_their_results_and_the_check_waits_only_when_it_changes_the_plan(self):
        self.progress(stage='inspect', status='running')
        result = self.compact(self.run.journey('agentic'))
        self.assertEqual((result['flow'], result['of']), ('dashboard', 8))
        first = result['steps'][0]
        self.assertEqual((first['id'], first['status'], first['needs_you']), ('scope', 'in_progress', True))
        result = self.compact(self.run.journey('report', answer='Sales pipeline:  Overview, Stages'))
        self.assertEqual(result['steps'][0]['content'], 'Pick report and pages: Sales pipeline: Overview, Stages')
        check = result['steps'][1]
        self.assertEqual((check['status'], check['needs_you']), ('in_progress', False), 'a check that finds nothing needs nobody')
        check = self.compact(self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date', waits=True))['steps'][1]
        self.assertEqual((check['status'], check['needs_you']), ('in_progress', True))
        self.assertIn('18 of 25 tiles', check['active'])
        result = self.compact(self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date'))
        self.assertEqual(result['steps'][1]['content'], 'Check what can be built: 18 of 25 tiles, stage list out of date')
        self.assertEqual([r['status'] for r in result['steps']][:3], ['completed', 'completed', 'in_progress'],
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
        self.assertEqual(result['steps'][5]['content'], 'Save or keep local: kept on this machine')
        self.run.journey('dashboard', answer='declined')
        result = self.steps()
        self.assertEqual((result['state'], result['steps'][7]['content']),
                         ('done', 'Build a page from the metrics (optional): declined'))

    def test_past_queries_are_offered_after_the_checks_and_wait_for_the_person(self):
        # The person, 2026-11-23: a dashboard build reads the dashboard alone; its past queries are offered before saving.
        fixture.seed_checked(self.run, 'checked')
        self.bind(self.run)
        self.run.journey('agentic')
        self.progress(stage='check', status='complete')
        row = lambda: next(r for r in self.steps()['steps'] if r['id'] == 'history')
        self.assertEqual((row()['status'], row()['needs_you']), ('in_progress', True))
        self.run.journey('history', answer='add')
        self.assertEqual((row()['status'], row()['needs_you'], row()['active']),
                         ('in_progress', False, 'Adding past queries'))
        self.run.journey('history', answer='added')
        self.assertEqual((row()['status'], row()['content']), ('completed', 'Add past queries (optional): added'))
        self.run.journey('history', answer='skip')
        self.assertEqual(row()['content'], 'Add past queries (optional): skipped, your choice')
        self.run.journey('history', answer='unavailable')
        self.assertEqual(row()['content'], 'Add past queries (optional): none readable for these tables')
        import journey
        with self.assertRaises(journey.JourneyError):
            self.run.journey('history', answer='maybe')

    def test_dashboard_records_refuse_what_they_cannot_hold(self):
        import journey
        self.run.journey('agentic')
        with self.assertRaises(journey.JourneyError):
            self.run.journey('report', answer='  ')
        with self.assertRaises(journey.JourneyError):
            self.run.journey('report', answer='Sales', waits=True)
        with self.assertRaises(journey.JourneyError):
            self.run.journey('dashboard', answer='maybe')

    def test_the_checks_are_the_persons_choice_and_item_four_says_so(self):
        import journey
        fixture.seed_checked(self.run, 'checked')
        self.run.journey('agentic')
        self.progress(stage='check', status='running')
        self.assertNotEqual({r['id']: r for r in self.steps()['steps']}['check']['status'], 'completed')
        with self.assertRaises(journey.JourneyError):
            self.run.journey('checks', answer='maybe')
        steps = {r['id']: r for r in self.compact(self.run.journey('checks', answer='skip'))['steps']}
        self.assertEqual((steps['check']['status'], steps['check']['content']),
                         ('completed', 'Check against the dashboard: skipped, your choice'))

    def test_other_flows_keep_their_lists(self):
        self.assertEqual(len(checklist.initial(compact=True)), 7)
        self.assertEqual(checklist.initial(compact=True)[0]['content'], 'Clarify your use case')
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
        self.assertEqual(len(json.loads(out.stdout)['steps']), 9, 'seven steps, metric generation as three')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()


class ThreeSteps(unittest.TestCase):
    """Team review, 2026-11-18: metric generation is three steps in the host's own task list (draft, merge, write),
    each with a time estimate fitted to measured runs, so the person sees the flow and how long it takes."""

    def model(self, done=(), counts=None, flow='demo'):
        return {'phase': {'code': 'build', 'status': 'working', 'label': 'Building', 'source': 'engine'},
                'counts': {'metrics': 0, 'queries': 0, 'released': 0}, 'progress': {},
                'interaction': {'flow': flow, 'mode': 'active', 'intent': {'domain': 'x', 'purpose': 'y'}},
                'bound_tables': 5, 'engine': {'done': list(done), 'counts': counts or {}}}

    def test_the_lists_show_three_metric_steps(self):
        self.assertEqual([r['content'] for r in checklist.initial('demo')], [
            'Choose and load demo data', 'Choose what to build from', 'Find metrics in your sources',
            'Combine duplicates, one metric per number', 'Define and test each metric', 'Keep local or save to Atlan Trident',
            'Ask a question'])
        build = [r['content'] for r in checklist.initial('build')]
        self.assertEqual(build[3:6], ['Find metrics in your sources', 'Combine duplicates, one metric per number',
                                      'Define and test each metric'])
        self.assertEqual([r['id'] for r in checklist.initial('dashboard')][2:5], ['draft', 'merge', 'write'])

    def test_each_step_ticks_as_the_engine_finishes_it_with_its_count_and_estimate(self):
        counts = {'m2_emit': {'queries': 30}, 'x1_ingest': {'proposals': 26}}
        start = {r['id']: r for r in checklist.build(self.model((), counts))['steps']}
        self.assertEqual((start['draft']['status'], start['draft']['content']),
                         ('in_progress', 'Find metrics in your sources (about 5 min)'))
        # The list reaches the person when the queries are described (h8, 4.6 min): what is left is finding the numbers.
        # Measured for 30 queries: 2.0, 1.9 and 2.0 min headless, 3.1 on the desktop; the whole step said "about 5".
        rest = {r['id']: r for r in checklist.build(self.model(('m2_ingest',), counts))['steps']}
        self.assertEqual(rest['draft']['content'], 'Find metrics in your sources (about 2 min)')
        rows = {r['id']: r for r in checklist.build(self.model(('m2_ingest', 'x1_ingest'), counts))['steps']}
        self.assertEqual((rows['draft']['status'], rows['draft']['content']), ('completed', 'Find metrics: 26 possible metrics'))
        self.assertEqual((rows['merge']['status'], rows['merge']['content']),
                         ('in_progress', 'Combine duplicates, one metric per number (about 3 min)'))
        self.assertEqual((rows['write']['status'], rows['write']['content']), ('pending', 'Define and test each metric'))
        counts['x2_ingest'] = {'metrics_out': 9}
        rows = {r['id']: r for r in checklist.build(self.model(('m2_ingest', 'x1_ingest', 'x2_ingest'), counts))['steps']}
        self.assertEqual(rows['merge']['content'], 'Combine duplicates: 9 metrics')
        self.assertEqual(rows['write']['content'], 'Define and test each metric (about 7 min)')

    def test_linking_shows_on_the_define_step_until_it_ends(self):
        # A watched dashboard run defined 40 metrics, then linked them (279 pairs) for 6 minutes with nothing moving in its
        # task list: the define step read as done and the build looked stuck.
        counts = {'m2_emit': {'queries': 46}, 'x1_ingest': {'proposals': 53}, 'x2_ingest': {'metrics_out': 40},
                  'x3_ingest': {'cards': 40}, 'x4_emit': {'candidate_pairs': 279}}
        done = ('m2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest')
        row = {r['id']: r for r in checklist.build(self.model(done, counts, flow='dashboard'))['steps']}['write']
        self.assertEqual((row['status'], row['content']),
                         ('in_progress', 'Define each metric · 40 defined, linking related ones (about 6 min)'))
        small = dict(counts, **{'x3_ingest': {'cards': 4}, 'x4_emit': {'candidate_pairs': 10}})
        row = {r['id']: r for r in checklist.build(self.model(done, small))['steps']}['write']
        self.assertEqual(row['content'], 'Define and test each metric · 4 defined, linking related ones (about 1 min)')

    def test_the_side_view_keeps_one_metrics_item(self):
        rows = checklist.build(self.model(), compact=True)['steps']
        self.assertIn('metrics', [r['id'] for r in rows])
        self.assertFalse({'draft', 'merge', 'write'} & {r['id'] for r in rows})
