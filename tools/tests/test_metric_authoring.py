"""Exact execution checks for accepted metric contracts."""
import copy
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[1] / 'metrics-generation'))
import context_contracts as mine
from context_state import digest
from modules.metrics import holds
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


ALL_WORK = '(SELECT cost FROM site.work)'
REWORK_ONLY = "(SELECT cost FROM site.work WHERE kind = 'rework')"


class RuleRevision(unittest.TestCase):
    """An engine edit reaches the context only for its metric, checked before and after."""

    def setUp(self):
        fixtures.SQLiteFlow.setUp(self)
        self.other = self.body(REWORK_ONLY, name='rework cost', source_id='metric:site:rework-cost')
        self.run.write(self.run.context / 'metrics.json', [self.body(ALL_WORK), self.other])
        self.run.write(self.run.context / 'queries.json', [])
        self.run.write(self.run.context / 'links.json', [])
        self.receipt(self.run.context, 'a')
        self.edit = self.run.root / 'engine' / 'edits' / '1'
        self.engine_edit(self.body(REWORK_ONLY))

    def body(self, source_sql, name='cost', source_id='metric:site:cost'):
        body = CardExecution.card(self)
        body.update(name=name, source_type='external', source_id=source_id)
        body['definition']['source']['sql'] = source_sql
        body['definition']['examples'][0]['sql'] = 'SELECT SUM(cost) FROM site.work'
        return body

    def receipt(self, folder, outputs, held=()):
        document = {'format': 2, 'checks': holds.CHECKS, 'outputs': {'metric_cards.json': outputs * 64},
                    'metrics': list(held), 'queries': []}
        self.run.write(folder / holds.FILE, document)
        self.run.write(folder / 'metrics-source.json', {'module': 'metrics', 'outputs': document['outputs'],
                                                         'publication_holds_sha256': holds.identity(document)})

    def engine_edit(self, body, held=()):
        self.run.write(self.edit / 'context' / 'metrics.json', [body, self.other])
        self.run.write(self.edit / 'context' / 'queries.json', [])
        self.run.write(self.edit / 'context' / 'links.json', [])
        self.receipt(self.edit / 'context', 'b', held)
        self.run.write(self.edit / 'edit.json', {'status': 'edited', 'metric': 'cost', 'source_id': 'metric:site:cost',
                                                 'before': {'rule': {'population': 'All work'}},
                                                 'after': {'rule': {'population': 'Rework only'}}})

    def context_files(self):
        return {name: (self.run.context / name).read_text()
                for name in ('metrics.json', 'metrics-source.json', holds.FILE)}

    def test_revision_checks_both_rules_on_the_data_and_installs_only_that_metric(self):
        result = self.run.revise_metric({'name': 'cost', 'edit': str(self.edit)})
        self.assertEqual((result['status'], result['metric'], result['source_id']),
                         ('revised', 'cost', 'metric:site:cost'))
        self.assertEqual((result['before']['rule'], result['after']['rule']),
                         ({'population': 'All work'}, {'population': 'Rework only'}))
        before, after = result['before']['check'], result['after']['check']
        self.assertEqual((before['calculation'], after['calculation']), ([[34.75]], [[14.75]]))
        self.assertEqual((before['example'], after['example']), ([[34.75]], [[34.75]]))
        self.assertEqual(after['status'], 'example_executed')
        metrics = self.run.read(self.run.context / 'metrics.json')
        self.assertEqual([m['definition']['source']['sql'] for m in metrics], [REWORK_ONLY, REWORK_ONLY])
        self.assertEqual(metrics[1], self.other)
        self.assertEqual(self.run.read(self.run.context / 'metrics-source.json')['outputs'],
                         {'metric_cards.json': 'b' * 64})
        mine.check_publication_holds(self.run.context)
        check = self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']
        self.assertEqual(check['definition_hash'], digest(metrics[0]['definition']))

    def test_a_revision_that_changes_identity_or_holds_is_refused(self):
        unchanged = self.context_files()
        moved = self.body(REWORK_ONLY, source_id='metric:site:other')
        held = [{'source_id': 'metric:site:rework-cost', 'name': 'rework cost', 'status': 'held',
                 'reason': 'dashboard_parity_failed'}]
        for body, holding, reason in ((moved, (), 'identity'), (self.body(REWORK_ONLY), held, 'holds')):
            with self.subTest(reason=reason):
                self.engine_edit(body, holding)
                with self.assertRaisesRegex(ValueError, reason):
                    self.run.revise_metric({'name': 'cost', 'edit': str(self.edit)})
                self.assertEqual(self.context_files(), unchanged)

    def test_a_new_rule_that_fails_its_check_leaves_the_old_rule_in_place(self):
        failing = self.body(REWORK_ONLY)
        failing['definition']['mandatory_filters'] = ["site.work.kind = 'rework'"]
        self.engine_edit(failing)
        unchanged = self.context_files()
        with self.assertRaisesRegex(ValueError, 'keeps the old rule') as refused:
            self.run.revise_metric({'name': 'cost', 'edit': str(self.edit)})
        self.assertIn(str(self.edit / 'previous-card.json'), str(refused.exception))
        self.assertEqual(self.context_files(), unchanged)
        check = self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']
        old = self.run.read(self.run.context / 'metrics.json')[0]
        self.assertEqual((check['definition_hash'], check['status']),
                         (digest(old['definition']), 'example_executed'))


    def test_a_rule_whose_current_check_fails_can_be_revised(self):
        broken = self.body(ALL_WORK)
        broken['definition']['mandatory_filters'] = ["site.work.kind = 'rework'"]
        self.run.write(self.run.context / 'metrics.json', [broken, self.other])
        result = self.run.revise_metric({'name': 'cost', 'edit': str(self.edit)})
        self.assertEqual((result['before']['check']['status'], result['after']['check']['status']),
                         ('held', 'example_executed'))
        self.assertEqual(result['after']['check']['calculation'], [[14.75]])
