"""Exact execution checks for accepted metric contracts."""
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import context_contracts as mine
import test_local_project as fixtures


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
