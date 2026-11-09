"""Reconcile synthetic repeated setup runs without duplicate writes or broken edges."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
import metrics_mine as mine


def query(name='new-query', scope='new'):
    return {'name': name, 'display_name': 'Order count', 'source_id': scope + ':orders',
            'definition': {'spec_version': mine.QUERY_SPEC, 'dialect': 'duckdb',
                           'question': 'How many paid orders?',
                           'sql': "SELECT COUNT(*) FROM demo.orders WHERE status = 'paid'",
                           'review_status': 'unreviewed'}}


def metric(name='new-total'):
    return {'name': name, 'display_name': name, 'description': 'Amount from paid orders.',
            'source_id': 'new:' + name, 'definition': {
                'spec_version': mine.METRIC_SPEC, 'version': 1, 'dialect': 'duckdb',
                'formula': 'SUM(demo.orders.amount)', 'aggregation': {'type': 'sum'},
                'output': {'alias': 'total', 'datatype': 'decimal'},
                'source': {'entity': 'demo.orders', 'alias': 'orders', 'table': 'demo.orders'},
                'mandatory_filters': ["demo.orders.status = 'paid'"],
                'extensions': {'atlan_analytics': {'by_hand': True}}}}


def link(kind, source, target, relation='source_of'):
    return {'from': {'kind': kind, 'name': source}, 'to': {'kind': 'metric', 'name': target},
            'rel_type': relation, 'attributes': {'why': 'Source evidence'}}


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def check(self, metrics, queries, existing, links=()):
        for name, value in [('metrics', metrics), ('queries', queries),
                            ('existing', existing), ('links', list(links))]:
            (self.root / (name + '.json')).write_text(json.dumps(value))
        mine.cmd_check(SimpleNamespace(dir=str(self.root)))
        return self.read('plan')

    def read(self, name):
        return json.loads((self.root / (name + '.json')).read_text())

    def test_exact_sql_across_scopes_reuses_live_identity_without_rewriting_notes(self):
        candidate, live = query(), query('saved-query', 'old')
        live.update(id='query_example', kind='sql_query')
        live['definition'].update(question='Reviewed question', review_status='verified')
        plan = self.check([], [candidate], [live])
        self.assertEqual(plan['refused'], [])
        self.assertEqual(plan['counts']['queries'], {'create': 0, 'revise': 0, 'reuse': 1})
        self.assertEqual(plan['queries'][0]['match_id'], live['id'])
        self.assertEqual(self.read('queries')[0]['name'], 'saved-query')
        self.assertEqual(self.read('queries')[0]['definition'], live['definition'])
        self.assertEqual(next(iter(self.read('reconciliation-history').values()))['candidate']['definition'], candidate['definition'])
        mine.cmd_check(SimpleNamespace(dir=str(self.root)))
        self.assertEqual(self.read('plan')['counts'], plan['counts'])

    def test_incomplete_live_bodies_cannot_borrow_candidate_descriptions(self):
        for kind in ('metric', 'sql_query'):
            with self.subTest(kind=kind):
                candidate = metric() if kind == 'metric' else query()
                live = copy.deepcopy(candidate)
                live.update(id='artifact_example', kind=kind)
                del live['display_name']
                plan = self.check([candidate] if kind == 'metric' else [],
                                  [candidate] if kind == 'sql_query' else [], [live])
                self.assertTrue(plan['refused'])
                self.assertFalse(plan['metrics' if kind == 'metric' else 'queries'][0]['ok'])

    def test_literals_parameters_and_unknown_constraints_do_not_merge(self):
        live = query('saved-query', 'old')
        live.update(id='query_example', kind='sql_query')
        for change in ({'sql': live['definition']['sql'].replace("'paid'", "'refunded'")},
                       {'parameters': [{'name': 'viewer', 'default': 'self'}]},
                       {'visibility_filter': 'current_user'},
                       {'extensions': {'metrics_agent': {'viewer_binds': {'column': 'viewer'}}}}):
            candidate = query()
            candidate['definition'].update(change)
            with self.subTest(change=change):
                plan = self.check([], [candidate], [live])
                self.assertEqual(plan['queries'][0]['action'], 'create')
                self.assertIsNone(plan['queries'][0]['match_id'])

    def test_rejected_live_query_cannot_be_resurrected_by_exact_sql_reuse(self):
        live = query('saved-query', 'old')
        live.update(id='query_example', kind='sql_query')
        live['definition']['review_status'] = 'rejected'
        plan = self.check([], [query()], [live])
        self.assertFalse(plan['queries'][0]['ok'])
        self.assertTrue(any('rejected in review' in r['error'] for r in plan['refused']))

    def test_ambiguous_live_matches_are_refused(self):
        lives = [{**query(name, name), 'id': name, 'kind': 'sql_query'} for name in ('one', 'two')]
        plan = self.check([], [query()], lives)
        self.assertFalse(plan['queries'][0]['ok'])
        self.assertIn('multiple live', plan['refused'][0]['error'])

    def test_identity_adoption_rewrites_typed_links_and_dependencies_together(self):
        parent, child = metric('new-total'), metric('new-average')
        child['definition']['formula'] = 'AVG(demo.orders.amount)'
        child['definition']['aggregation'] = {'type': 'custom'}
        child['definition']['depends_on'] = ['new-total']
        live_parent, live_child = copy.deepcopy(parent), copy.deepcopy(child)
        live_parent.update(name='saved-total', source_id='old:total', id='metric_total', kind='metric')
        live_child.update(name='saved-average', source_id='old:average', id='metric_average', kind='metric')
        live_child['definition']['depends_on'] = ['saved-total']
        live_q = {**query('saved-query', 'old'), 'id': 'query_example', 'kind': 'sql_query'}
        plan = self.check([parent, child], [query()], [live_parent, live_child, live_q], [
            link('sql_query', 'new-query', 'new-total'),
            link('metric', 'new-average', 'new-total', 'depends_on')])
        self.assertEqual(plan['refused'], [])
        self.assertEqual(plan['counts']['metrics']['reuse'], 2)
        self.assertEqual(self.read('links')[0]['from']['name'], 'saved-query')
        self.assertEqual(self.read('links')[0]['to']['name'], 'saved-total')
        self.assertEqual(self.read('metrics')[1]['definition']['depends_on'], ['saved-total'])
        mine.cmd_check(SimpleNamespace(dir=str(self.root)))
        self.assertEqual(self.read('plan')['counts'], plan['counts'])

    def test_refused_query_endpoint_never_produces_writable_relationship(self):
        bad = query()
        bad['definition']['sql'] = 'DELETE FROM demo.orders'
        plan = self.check([metric()], [bad], [], [link('sql_query', 'new-query', 'new-total')])
        self.assertFalse(plan['queries'][0]['ok'])
        self.assertFalse(plan['links'][0]['ok'])
        self.assertEqual(plan['counts']['links'], 0)

    def test_multiple_candidates_cannot_write_the_same_adopted_identity(self):
        live = {**query('saved-query', 'old'), 'id': 'query_example', 'kind': 'sql_query'}
        plan = self.check([], [query(), query('another-query', 'another')], [live])
        self.assertTrue(all(not q['ok'] for q in plan['queries']))

    def decide(self, candidate, live, kind='metric', action='reuse'):
        decision = {'kind': kind, 'candidate_name': candidate['name'],
                    'candidate_hash': mine.reconciliation_hash(candidate),
                    'live_id': live['id'], 'live_hash': mine.reconciliation_hash(live),
                    'action': action, 'reason': 'Preserve the reviewed existing definition.',
                    'comparison': {'meaning': 'Order amount in the selected definition.',
                                   'population': 'Paid orders only.', 'grain': 'One row per order.',
                                   'dimensions': 'Use the existing breakdowns.',
                                   'parameters': 'No additional parameter constraints.',
                                   'limitations': 'Candidate-only examples remain in local history.'}}
        (self.root / 'reconciliation-decisions.json').write_text(json.dumps([decision]))

    def test_different_coverage_requires_review_then_preserves_the_full_live_metric(self):
        candidate = metric()
        live = {**copy.deepcopy(candidate), 'id': 'metric_total', 'kind': 'metric',
                'name': 'saved-total', 'source_id': 'old:total'}
        live['definition'].update(version=6, dimensions=[{'name': 'Region', 'sql': 'demo.orders.region',
                                                        'kind': 'categorical', 'datatype': 'string'}])
        plan = self.check([candidate], [], [live])
        self.assertFalse(plan['metrics'][0]['ok'])
        self.assertEqual(self.read('metrics')[0], candidate)
        self.assertEqual(self.read('reconciliation-review')[0]['existing'][0]['id'], live['id'])
        self.decide(candidate, live)
        plan = self.check([candidate], [], [live])
        self.assertEqual(plan['refused'], [])
        self.assertEqual(plan['counts']['metrics']['reuse'], 1)
        self.assertEqual(self.read('metrics')[0]['definition'], live['definition'])
        self.assertEqual(plan['coverage'][0]['comparison']['dimensions'], 'Use the existing breakdowns.')
        mine.cmd_check(SimpleNamespace(dir=str(self.root)))
        self.assertEqual(self.read('plan')['counts'], plan['counts'])
        self.assertEqual(self.read('plan')['coverage'], plan['coverage'])

    def test_similar_business_name_with_different_formula_is_not_silently_duplicated(self):
        candidate = metric()
        live = {**metric('saved-total'), 'display_name': candidate['display_name'],
                'id': 'metric_total', 'kind': 'metric', 'source_id': 'old:total'}
        live['definition']['formula'] = 'SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)'
        plan = self.check([candidate], [], [live])
        self.assertFalse(plan['metrics'][0]['ok'])
        self.assertEqual(plan['counts']['metrics']['create'], 0)
        self.decide(candidate, live)
        plan = self.check([candidate], [], [live])
        self.assertEqual(plan['refused'], [])
        self.assertEqual(self.read('metrics')[0]['definition']['formula'], live['definition']['formula'])

    def test_review_is_invalidated_by_candidate_or_live_changes(self):
        candidate = metric()
        live = {**metric('saved-total'), 'id': 'metric_total', 'kind': 'metric'}
        self.decide(candidate, live)
        for changed_candidate, changed_live in (
                ({**candidate, 'description': 'Changed requested scope.'}, live),
                (candidate, {**live, 'description': 'Changed live scope.'})):
            with self.subTest(candidate=changed_candidate['description']):
                plan = self.check([changed_candidate], [], [changed_live])
                self.assertFalse(plan['metrics'][0]['ok'])

    def test_changed_remote_adoption_cannot_be_rolled_back_as_a_local_revision(self):
        candidate = query()
        live = {**query('saved-query', 'old'), 'id': 'query_example', 'kind': 'sql_query'}
        self.check([], [candidate], [live])
        selected = self.read('queries')
        live['definition']['sql'] += ' AND amount > 0'
        plan = self.check([], selected, [live])
        self.assertFalse(plan['queries'][0]['ok'])
        self.assertTrue(any('changed remotely' in r['error'] for r in plan['refused']))

    def test_revision_version_is_prepared_before_plan_approval_and_is_idempotent(self):
        candidate = metric()
        live = {**copy.deepcopy(candidate), 'id': 'metric_total', 'kind': 'metric'}
        live['definition']['version'] = 8
        candidate['definition']['formula'] = 'SUM(demo.orders.amount) * 2'
        plan = self.check([candidate], [], [live])
        self.assertEqual(plan['refused'], [])
        self.assertEqual(plan['metrics'][0]['action'], 'revise')
        self.assertEqual(self.read('metrics')[0]['definition']['version'], 9)
        mine.cmd_check(SimpleNamespace(dir=str(self.root)))
        self.assertEqual(self.read('metrics')[0]['definition']['version'], 9)

    def test_checks_follow_renaming_only_for_unchanged_definition(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                candidate = query()
                live = {**query('saved-query', 'old'), 'id': 'query_example', 'kind': 'sql_query'}
                if changed:
                    live['definition']['question'] = 'Reviewed wording'
                record = {'definition_hash': mine.definition_hash(candidate), 'status': 'executed',
                          'samples': {'sample': {'status': 'executed', 'query_id': 'local_check'}}}
                (self.root / 'candidate-checks.json').write_text(json.dumps({'sql_query:new-query': record}))
                self.check([], [candidate], [live])
                checks = self.read('candidate-checks')
                self.assertNotIn('sql_query:new-query', checks)
                if changed:
                    self.assertNotIn('sql_query:saved-query', checks)
                else:
                    self.assertEqual(checks['sql_query:saved-query'], record)

    def test_parameterized_coverage_requires_review_and_retains_original_statement(self):
        candidate = query()
        live = {**query('saved-query', 'old'), 'id': 'query_example', 'kind': 'sql_query'}
        live['definition']['sql'] = candidate['definition']['sql'].replace("'paid'", '{{status}}')
        live['definition']['parameters'] = [{'name': 'status', 'data_type': 'string'}]
        plan = self.check([], [candidate], [live])
        self.assertFalse(plan['queries'][0]['ok'])
        self.decide(candidate, live, 'sql_query')
        plan = self.check([], [candidate], [live])
        self.assertEqual(plan['refused'], [])
        self.assertEqual(plan['queries'][0]['action'], 'reuse')
        self.assertEqual(self.read('queries')[0]['definition'], live['definition'])
        self.assertEqual(next(iter(self.read('reconciliation-history').values()))['candidate'], candidate)

    def test_duplicate_live_source_ids_are_not_resolved_by_inventory_order(self):
        for kind, candidate, group in (('metric', metric(), 'metrics'), ('sql_query', query(), 'queries')):
            lives = [{**copy.deepcopy(candidate), 'id': 'first', 'kind': kind},
                     {**copy.deepcopy(candidate), 'id': 'second', 'kind': kind}]
            plan = self.check([candidate] if kind == 'metric' else [],
                              [candidate] if kind == 'sql_query' else [], lives)
            self.assertFalse(plan[group][0]['ok'])

    def test_quoted_identifiers_and_token_boundaries_are_distinct(self):
        candidate, other = metric(), metric()
        for left, right in [('SUM("Amount")', 'SUM("amount")'), ('SUM(a b)', 'SUM(ab)')]:
            candidate['definition']['formula'], other['definition']['formula'] = left, right
            self.assertNotEqual(mine.identity(candidate), mine.identity(other))


if __name__ == '__main__':
    unittest.main()
