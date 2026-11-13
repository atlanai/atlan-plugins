"""Exact execution checks for accepted metric contracts."""
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import context_contracts as mine
import test_local_project as fixtures
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/analytics-demo/scripts'))
import edit_metric as demo_edit


class CardExecution(unittest.TestCase):
    setUp = fixtures.SQLiteFlow.setUp
    def card(self):
        return {'name': 'cost', 'display_name': 'Cost', 'description': 'Recorded cost.',
                'definition': {'spec_version': mine.METRIC_SPEC, 'version': 1, 'dialect': 'sqlite',
                    'formula': 'SUM(cost_rows.cost)', 'aggregation': {'type': 'sum'},
                    'output': {'alias': 'cost', 'datatype': 'decimal'},
                    'source': {'entity': 'cost_rows', 'alias': 'cost_rows',
                               'sql': "(SELECT cost FROM site.work WHERE kind = 'rework')"},
                    'extensions': {'atlan_analytics': {'declared': ['example:Cost']}},
                    'mandatory_filters': [], 'examples': [
                        {'source': 'declared:example', 'sql': "SELECT SUM(cost) FROM site.work WHERE kind = 'rework'"}]}}

    def save(self, card):
        self.run.write(self.run.context / 'metrics.json', [card])
        self.run.write(self.run.context / 'queries.json', [])


    def test_example_success_cannot_hide_invalid_outer_filter(self):
        card = self.card()
        card['definition']['mandatory_filters'] = ["site.work.kind = 'rework'"]
        self.save(card)
        with self.assertRaises(ValueError): self.run.check_metric({'name': 'cost'})
        check = self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']
        self.assertEqual(check['status'], 'held')
        card['definition']['mandatory_filters'] = []
        self.save(card)
        self.run.check_metric({'name': 'cost'})
        check = self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']
        self.assertEqual(check['composition']['rows'], [[14.75]])
        self.assertEqual(check['composition']['coverage'], 'formula_source_mandatory_filters')

    def test_edit_metric_keeps_the_old_version_and_refuses_a_failing_rule(self):
        self.save(self.card())
        out = demo_edit.edit_metric(self.run, {'name': 'cost', 'changes': [
            {'path': 'source.sql', 'value': "(SELECT cost FROM site.work WHERE kind <> 'rework')"},
            {'path': 'examples', 'value': [{'source': 'declared:example',
                                            'sql': "SELECT SUM(cost) FROM site.work WHERE kind <> 'rework'"}]}]})
        card = self.run.read(self.run.context / 'metrics.json')[0]
        self.assertEqual((out['version'], card['definition']['version']), (2, 2))
        self.assertIn("<> 'rework'", card['definition']['source']['sql'])
        self.assertIn("= 'rework'", self.run.read(Path(out['previous']))['definition']['source']['sql'])
        demo_edit.edit_metric(self.run, {'name': 'cost', 'changes': [{'path': 'description', 'value': 'Cost of non-rework work.'}]})
        self.assertEqual(self.run.read(self.run.context / 'metrics.json')[0]['description'], 'Cost of non-rework work.')
        with self.assertRaises(ValueError):
            demo_edit.edit_metric(self.run, {'name': 'cost', 'changes': [{'path': 'source.sql', 'value': '(SELECT nope FROM nowhere)'}]})
        self.assertEqual(self.run.read(self.run.context / 'metrics.json')[0]['definition']['version'], 3)
        with self.assertRaises(ValueError):
            demo_edit.edit_metric(self.run, {'name': 'cost', 'changes': [{'path': 'version', 'value': 9}]})

    def test_a_refused_query_or_edit_leaves_the_run_progress_as_it_was(self):
        import json, subprocess, progress
        self.save(self.card())
        progress.update(self.run, 'check', 'complete')
        request = self.run.root / 'bad.json'
        demo = Path(demo_edit.__file__)
        for argv, body in (([str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root), 'execute'],
                            {'sql': 'SELECT nope FROM nowhere'}),
                           ([str(demo), '--run-dir', str(self.run.root)],
                            {'name': 'cost', 'changes': [{'path': 'version', 'value': 9}]})):
            request.write_text(json.dumps(body))
            out = subprocess.run([sys.executable, *argv, str(request)], capture_output=True, text=True)
            self.assertEqual(json.loads(out.stdout)['status'], 'blocked', argv[0])
            self.assertEqual(self.run.read(self.run.root / 'progress.json')['status'], 'complete', argv[0])

    def test_edit_metric_reaches_the_embedded_card_notes(self):
        card = self.card()
        card['definition']['extensions']['metrics_agent'] = {'card': {'gotchas': ['rework is not billable'], 'formula': 'x'}}
        self.save(card)
        demo_edit.edit_metric(self.run, {'name': 'cost', 'changes': [
            {'path': 'extensions.metrics_agent.card.gotchas', 'value': ['rework is billable']}]})
        saved = self.run.read(self.run.context / 'metrics.json')[0]['definition']['extensions']['metrics_agent']['card']
        self.assertEqual(saved['gotchas'], ['rework is billable'])

    def test_metric_is_found_by_its_display_name_or_slug(self):
        card = self.card()
        card['name'] = 'Gross Margin'
        self.save(card)
        for name in ('gross-margin', 'metric:gross-margin', 'GROSS MARGIN'):
            self.run.check_metric({'name': name})
        self.assertIn('metric:Gross Margin', self.run.read(self.run.context / 'candidate-checks.json'))
        with self.assertRaises(ValueError) as refused:
            self.run.check_metric({'name': 'margin'})
        self.assertIn('Saved: metric:Gross Margin', str(refused.exception))

    def test_formula_uses_its_own_parameter_bindings(self):
        card = self.card()
        card['definition'].update(formula='SUM(site.work.cost)',
                                 source={'entity': 'site.work', 'alias': 'work', 'table': 'site.work'})
        card['definition']['examples'][0].update(sql='SELECT SUM(cost) FROM site.work WHERE kind = :kind',
                                               parameters={'kind': 'rework'})
        self.save(card)
        self.run.check_metric({'name': 'cost', 'parameters': {'kind': 'rework'}})
        check = self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']
        self.assertEqual(check['composition']['rows'], [[34.75]])
