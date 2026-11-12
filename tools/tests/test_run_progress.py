"""A refused request leaves the run's saved progress as it was; a failed stage still marks it."""
import json
import subprocess
import sys
import unittest

import test_local_project as fixtures
import test_metric_authoring as cards
import progress

PROJECT = fixtures.SCRIPTS / 'project.py'


class RefusedRequests(unittest.TestCase):
    setUp = fixtures.SQLiteFlow.setUp

    def cli(self, command, request):
        path = self.root / (command + '.json')
        path.write_text(json.dumps(request))
        result = subprocess.run([sys.executable, '-I', str(PROJECT), '--run-dir', str(self.run.root), command, str(path)],
                                capture_output=True, text=True)
        return result.returncode, json.loads(result.stdout)

    def saved(self):
        return {name: (self.run.root / name).read_bytes() for name in ('progress.json', 'progress-events.jsonl')}

    def checked_and_shown(self):
        cards.CardExecution.save(self, cards.CardExecution.card(self))
        self.run.check_metric({'name': 'cost'})
        progress.update(self.run, 'preview', 'complete')

    def test_a_query_with_another_binding_style_leaves_progress_alone(self):
        self.checked_and_shown()
        before = self.saved()
        code, reply = self.cli('execute', {'sql': 'SELECT SUM(cost) FROM site.work WHERE kind = $kind',
                                           'parameters': {'kind': 'rework'}})
        self.assertEqual((code, reply['status']), (2, 'blocked'))
        self.assertIn(':name', reply['message'])
        self.assertEqual(self.saved(), before)

    def test_a_check_that_ran_nothing_leaves_progress_alone(self):
        self.checked_and_shown()
        before = self.saved()
        code, reply = self.cli('check-metric', {'name': 'cost', 'parameters': {'kind': 'rework'}})
        self.assertEqual((code, reply['status']), (2, 'blocked'))
        self.assertEqual(self.saved(), before)

    def test_a_failed_check_still_marks_the_run(self):
        self.checked_and_shown()
        card = cards.CardExecution.card(self)
        card['definition']['mandatory_filters'] = ["site.work.kind = 'rework'"]
        cards.CardExecution.save(self, card)
        code, _ = self.cli('check-metric', {'name': 'cost'})
        self.assertEqual(code, 2)
        saved = self.run.read(self.run.root / 'progress.json')
        self.assertEqual((saved['stage'], saved['status'], saved['action_required']), ('check', 'failed', 'review_held'))

    def test_the_project_reference_names_each_engine_binding(self):
        text = (fixtures.SCRIPTS.parent / 'references/project.md').read_text()
        execute = ' '.join(text[text.index('run `PROJECT execute'):].split('\n\n')[0].split())
        self.assertIn('`:name` for SQLite', execute)
        self.assertIn('`$name` for DuckDB', execute)


if __name__ == '__main__':
    unittest.main()
