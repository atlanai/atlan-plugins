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
            'Keep local or save to Atlan Trident', 'Ask a question', 'Build a dashboard'])

    def test_demo_flow_is_recorded_and_question_and_dashboard_are_ticked_only_when_done(self):
        fixture.seed_checked(self.run, 'checked')
        self.run.journey('demo')
        self.run.journey('keep-local')
        result = self.steps()
        self.assertEqual(result['flow'], 'demo')
        self.assertEqual([r['status'] for r in result['steps']], ['completed'] * 4 + ['in_progress', 'pending'])
        self.run.journey('asked')
        self.assertEqual([r['status'] for r in self.steps()['steps']][4:], ['completed', 'in_progress'])
        self.run.journey('dashboard')
        result = self.steps()
        self.assertEqual((result['state'], result['step'], result['of']), ('done', 6, 6))

    def test_a_passing_recheck_after_keeping_local_does_not_rewind_the_steps(self):
        # Changing a rule re-checks one metric; the save choice already made still stands.
        import progress
        fixture.seed_checked(self.run, 'checked')
        self.run.journey('demo')
        self.run.journey('keep-local')
        self.run.journey('asked')
        progress.update_checks(self.run)
        self.assertEqual([r['status'] for r in self.steps()['steps']], ['completed'] * 5 + ['in_progress'])

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

    def test_command_reads_only_and_starts_before_a_run_exists(self):
        fresh = Path(self.tmp.name).resolve() / 'not-started'
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), 'checklist'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)['steps'], checklist.initial())
        self.assertFalse(fresh.exists())
        before = {str(p): p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run.root), 'checklist'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(json.loads(out.stdout)['steps']), 7)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
