"""The setup checklist mirrors saved progress and never claims a step the run has not reached."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
        return experience.snapshot(self.run)['checklist']

    def progress(self, **state):
        self.run.write(self.run.root / 'progress.json', state)

    def bind(self, run, tables=5):
        state = run.read(run.path)
        state['execution'] = {'kind': 'sqlite', 'read_only': True, 'tables': [f'site.t{i}' for i in range(tables)]}
        run.write(run.path, state)

    def test_initial_list_names_seven_build_steps(self):
        rows = checklist.initial()
        self.assertEqual([r['content'] for r in rows], [
            'Clarify your use case', 'Survey your sources', 'Select tables and dashboards',
            'Generate and check metrics', 'Keep local or save to Atlan Trident', 'Ask a question', 'Build a dashboard'])
        self.assertEqual([r['status'] for r in rows], ['in_progress'] + ['pending'] * 6)
        self.assertEqual([r['id'] for r in rows if r['needs_you']], ['intent', 'scope', 'save', 'ask', 'dashboard'])

    def test_initial_demo_list_starts_with_demo_data(self):
        rows = checklist.initial('demo')
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
        step = result['checklist']['steps'][0]
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
                self.assertEqual([r['status'] for r in result['checklist']['steps']],
                                 ['completed'] * saved + ['in_progress'] + ['pending'] * (flow == 'build'))
                self.assertEqual(result['checklist']['steps'][saved - 1]['content'], 'Kept on this machine')

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
        self.assertEqual(self.run.journey('status')['checklist'], result)

    def test_a_recorded_source_choice_moves_the_tracker_to_the_build(self):
        fixture.seed_checked(self.run, 'checked')
        self.bind(self.run)
        self.run.journey('demo')
        self.progress(stage='scope', status='complete')
        result = self.steps()
        self.assertEqual([r['status'] for r in result['steps']][:3], ['completed', 'completed', 'in_progress'])

    def test_initial_dashboard_list_has_its_own_seven_items(self):
        rows = checklist.initial('dashboard')
        self.assertEqual([r['content'] for r in rows], [
            'Pick report and pages', 'Check what can be built', 'Build metrics and queries', 'Check against the dashboard',
            'Save or keep local', 'Ask a question', 'Build a page from the metrics (optional)'])
        # It waits at the report, the save, the question and the page; the check waits only when it changes the plan.
        self.assertEqual([r['id'] for r in rows if r['needs_you']], ['scope', 'save', 'ask', 'dashboard'])

    def test_dashboard_items_show_their_results_and_the_check_waits_only_when_it_changes_the_plan(self):
        self.progress(stage='inspect', status='running')
        result = self.run.journey('agentic')['checklist']
        self.assertEqual((result['flow'], result['of']), ('dashboard', 7))
        first = result['steps'][0]
        self.assertEqual((first['id'], first['status'], first['needs_you']), ('scope', 'in_progress', True))
        result = self.run.journey('report', answer='Sales pipeline:  Overview, Stages')['checklist']
        self.assertEqual(result['steps'][0]['content'], 'Pick report and pages: Sales pipeline: Overview, Stages')
        check = result['steps'][1]
        self.assertEqual((check['status'], check['needs_you']), ('in_progress', False), 'a check that finds nothing needs nobody')
        check = self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date', waits=True)['checklist']['steps'][1]
        self.assertEqual((check['status'], check['needs_you']), ('in_progress', True))
        self.assertIn('18 of 25 tiles', check['active'])
        result = self.run.journey('readiness', answer='18 of 25 tiles, stage list out of date')['checklist']
        self.assertEqual(result['steps'][1]['content'], 'Check what can be built: 18 of 25 tiles, stage list out of date')
        self.assertEqual([r['status'] for r in result['steps']][:3], ['completed', 'completed', 'in_progress'],
                         'the build starts on its own: there is no confirm item')

    def test_dashboard_page_item_ticks_when_built_or_declined(self):
        fixture.seed_checked(self.run, 'checked')
        self.bind(self.run)
        self.run.journey('agentic')
        self.progress(stage='check', status='complete')
        self.run.journey('keep-local')
        self.run.journey('asked')
        result = self.steps()
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 6 + ['in_progress'])
        self.assertEqual(result['steps'][4]['content'], 'Save or keep local: kept on this machine')
        self.run.journey('dashboard', answer='declined')
        result = self.steps()
        self.assertEqual((result['state'], result['steps'][6]['content']),
                         ('done', 'Build a page from the metrics (optional): declined'))

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
        steps = {r['id']: r for r in self.run.journey('checks', answer='skip')['checklist']['steps']}
        self.assertEqual((steps['check']['status'], steps['check']['content']),
                         ('completed', 'Check against the dashboard: skipped, your choice'))

    def test_other_flows_keep_their_lists(self):
        self.assertEqual(len(checklist.initial()), 7)
        self.assertEqual(checklist.initial()[0]['content'], 'Clarify your use case')
        self.assertEqual(len(checklist.initial('demo')), 5)
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
        self.assertEqual(len(json.loads(out.stdout)['steps']), 7)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
