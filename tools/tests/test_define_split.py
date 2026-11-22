"""The define step's metrics are shared among helpers by a fixed rule, each share with the same roster and conventions.

Measured on two demo builds: shares of 12 or more matched a single helper's definitions number for number, and shares
of 6 made the only material errors; each extra helper costs about ten minutes and $3.70 before its first card.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from analytics_run import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import define_split  # noqa: E402


def task(n):
    # As a real work list has them: a past query names tables as it wrote them, the dashboard in full, and a table
    # outside the run's list in two parts.
    return {'metric_id': 'metric-%d' % n, 'name': 'Metric %d' % n, 'n_sources': 2,
            'sources': [{'tables': ['tix.table_%d' % (n % 3), 'tix.events']},
                        {'tables': ['TIX.TIX.TABLE_%d' % (n % 3), 'TIX.TIX.EVENTS'] + (['TIX.SCANS'] if n == 5 else [])}],
            'merged': {'definition': 'Counts thing %d. Leaves out the test venue.' % n, 'entity': 'tix.table_%d' % (n % 3)},
            'evidence': {'joins': [], 'note': ''}}


class Split(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / 'run'
        (self.run / 'engine').mkdir(parents=True)
        (self.run / 'run.json').write_text(json.dumps({'entrypoint': 'project'}))

    def work_list(self, n, tasks=None):
        (self.run / 'engine' / 'x3_prompt.json').write_text(json.dumps({
            'contract_version': '1.0', 'stage': 'x3_emit', 'instruction': 'See gates/author-metric-card/GATE.md.',
            'fields': {'required': ['name'], 'optional': []}, 'n_metrics': n, 'tasks': tasks or [task(i) for i in range(1, n + 1)]}))

    def plan(self, n):
        self.work_list(n)
        return define_split.split(self.run)

    def test_up_to_twenty_metrics_one_helper_writes_them_all(self):
        plan = self.plan(20)
        self.assertEqual(plan['helpers'], 1)
        self.assertEqual(len(plan['shares']), 1)
        self.assertIn(str(self.run / 'engine' / 'x3_prompt.json'), plan['shares'][0]['brief'])
        self.assertFalse((self.run / 'split').exists(), 'one helper reads the work list itself')

    def test_above_twenty_equal_shares_of_at_least_twelve_and_at_most_five_at_once(self):
        for n, sizes in ((21, [21]), (24, [12, 12]), (37, [13, 12, 12]), (61, [13, 12, 12, 12, 12]), (100, [20] * 5),
                         (250, [50] * 5)):
            self.assertEqual(define_split.shares(n), sizes, n)

    def test_each_share_holds_its_own_tasks_and_together_they_hold_every_task_once(self):
        plan = self.plan(37)
        seen = []
        for share in plan['shares']:
            body = json.loads(Path(share['share']).read_text())
            self.assertEqual(body['instruction'], 'See gates/author-metric-card/GATE.md.')
            self.assertEqual([t['metric_id'] for t in body['tasks']], ['metric-%d' % n for n in body['task_numbers']])
            self.assertEqual(share['tasks'], body['task_numbers'])
            seen += body['task_numbers']
        self.assertEqual(seen, list(range(1, 38)), 'in task order, each once')

    def test_the_roster_lists_every_metric_with_its_table_definition_and_a_unique_output_name(self):
        tasks = [task(i) for i in range(1, 25)]
        tasks[0]['name'], tasks[1]['name'] = 'Fees Paid', 'Fees paid!'
        self.work_list(24, tasks)
        plan = define_split.split(self.run)
        roster = json.loads(Path(plan['roster']).read_text())
        self.assertEqual(len(roster['metrics']), 24)
        first = roster['metrics'][0]
        self.assertEqual((first['task'], first['name'], first['table']), (1, 'Fees Paid', 'tix.table_1'))
        self.assertEqual(first['definition'], 'Counts thing 1.', "the merged definition's first sentence")
        outputs = [m['output'] for m in roster['metrics']]
        self.assertEqual(outputs[:2], ['fees_paid', 'fees_paid_2'])
        self.assertEqual(len(set(outputs)), 24)
        # The work list names a table in up to three forms: the roster keeps the fullest it uses, one per table.
        self.assertEqual(roster['tables'], ['TIX.SCANS', 'TIX.TIX.EVENTS', 'TIX.TIX.TABLE_0', 'TIX.TIX.TABLE_1',
                                            'TIX.TIX.TABLE_2'])

    def test_each_brief_names_its_share_the_roster_the_conventions_and_no_engine_run(self):
        plan = self.plan(24)
        answers = self.run / 'engine' / 'answers' / 'x3'
        for share in plan['shares']:
            brief = share['brief']
            for needed in (share['share'], plan['roster'], str(answers), 'author-metric-card/GATE.md'):
                self.assertIn(needed, brief)
            for rule in ('as the roster names them', "the roster's output name", 'domain', 'Write only your own task numbers',
                         'Do not run the metrics engine'):
                self.assertIn(rule, brief)
            # Live, a helper spent its first minute reading the engine's code to check its cards by hand.
            self.assertIn('the session that started you runs the engine', brief)
        self.assertIn('when every share is back', plan['then'])

    def test_every_brief_names_the_schema_each_card_must_pass(self):
        # A helper that never runs the engine never sees the gate's schema; without it, shares wrote example filters
        # as lists and the gate refused them.
        self.assertTrue(define_split.SCHEMA.is_file())
        for n in (12, 37):
            for share in self.plan(n)['shares']:
                self.assertIn(str(define_split.SCHEMA), share['brief'])

    def test_a_smaller_plan_removes_shares_left_from_a_larger_one(self):
        self.plan(61)
        self.assertTrue((self.run / 'split' / 'share_5.json').exists())
        plan = self.plan(24)
        self.assertEqual(sorted(p.name for p in (self.run / 'split').glob('share_*.json')), ['share_1.json', 'share_2.json'])
        self.assertEqual(plan['helpers'], 2)

    def test_project_split_prints_the_plan(self):
        self.work_list(37)
        done = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), '--full', 'split'],
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        plan = json.loads(done.stdout)
        self.assertEqual((plan['metrics'], plan['helpers']), (37, 3))

    def test_no_work_list_says_so(self):
        with self.assertRaises(define_split.SplitError):
            define_split.split(self.run)


if __name__ == '__main__':
    unittest.main()
