"""Source-complete authoring and executable metric-card regressions."""
import copy
import argparse
import tempfile
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
import metrics_mine as mine
import test_local_project as fixtures


class Coverage(unittest.TestCase):
    def inventory(self):
        return mine.candidate_inventory([
            {'shape_id': 'b', 'sql': 'SELECT SUM(cost) / COUNT(*) FROM example.rows'},
            {'shape_id': 'a', 'sql': "SELECT (SELECT SUM(cost) FROM example.rows) AS cost, 'SUM(fake)' AS label"}],
            [{'source_ref': 'declared:example:Rows.Cost', 'expression': 'SUM(Rows[cost])'}])

    def test_every_aggregate_including_nested_quantities_has_stable_evidence(self):
        inventory = self.inventory()
        self.assertEqual(len(inventory['candidates']), 4)
        self.assertNotIn('SUM(fake)', [c['expression'] for c in inventory['candidates']])
        reversed_inventory = mine.candidate_inventory([
            {'shape_id': 'a', 'sql': "SELECT (SELECT SUM(cost) FROM example.rows) AS cost, 'SUM(fake)' AS label"},
            {'shape_id': 'b', 'sql': 'SELECT SUM(cost) / COUNT(*) FROM example.rows'}],
            [{'source_ref': 'declared:example:Rows.Cost', 'expression': 'SUM(Rows[cost])'}])
        self.assertEqual(inventory, reversed_inventory)

    def test_history_representative_does_not_depend_on_input_order(self):
        rows = [{'sql': "SELECT SUM(cost) FROM example.rows WHERE day = '2026-01-02'", 'exec': 2},
                {'sql': "SELECT SUM(cost) FROM example.rows WHERE day = '2026-01-01'", 'exec': 2}]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            results = []
            for ordered in (rows, list(reversed(rows))):
                (root / 'history.jsonl').write_text('\n'.join(json.dumps(row) for row in ordered))
                mine.cmd_shapes(argparse.Namespace(src=str(root / 'history.jsonl'),
                    dst=str(root / 'shapes.jsonl'), tables=None, top=100))
                results.append((root / 'shapes.jsonl').read_text())
            self.assertEqual(results[0], results[1])

    def test_structured_evidence_enrichment_keeps_quantity_identifiers(self):
        source = {'source_ref': 'declared:example:Rows.Cost', 'name': 'Cost', 'expression': 'SUM(Rows[cost])'}
        original = mine.candidate_inventory([], [source])
        enriched = mine.candidate_inventory([], [{**source, 'source_context': {
            'entity': 'Rows', 'table': {'columns': [{'name': 'cost', 'dataType': 'decimal'}]},
            'relationships': [], 'keys': 'unknown'}}])
        self.assertEqual(original['candidates'][0]['candidate_id'], enriched['candidates'][0]['candidate_id'])
        self.assertNotEqual(original['input_hash'], enriched['input_hash'])
        self.assertEqual(enriched['candidates'][0]['source_context']['keys'], 'unknown')

    def test_omission_changed_evidence_and_untraced_cards_require_reconciliation(self):
        inventory = self.inventory()
        decisions = [{'candidate_id': c['candidate_id'], 'disposition': 'metric',
                      'metrics': ['cost'], 'reason': 'Source-backed business quantity.'}
                     for c in inventory['candidates']]
        review = {'input_hash': inventory['input_hash'], 'decisions': decisions}
        self.assertEqual(mine.review_candidates(inventory, review, {'cost'})['status'], 'complete')
        for changed in [dict(review, decisions=decisions[:-1]), dict(review, input_hash='changed')]:
            with self.assertRaises(ValueError): mine.review_candidates(inventory, changed, {'cost'})
        changed = copy.deepcopy(review)
        changed['decisions'][-1]['metrics'] = ['cost', 'decomposed']
        with self.assertRaises(ValueError): mine.review_candidates(inventory, changed, {'cost', 'decomposed'})
        with self.assertRaises(ValueError): mine.review_candidates(inventory, review, {'cost', 'invented'})
        changed = copy.deepcopy(review)
        changed['decisions'][-1].update(disposition='non_metric', metrics=[])
        with self.assertRaises(ValueError): mine.review_candidates(inventory, changed, {'cost'})
        changed = copy.deepcopy(review)
        changed['decisions'][-1]['metrics'] = ['cost', 'decomposed']
        with self.assertRaises(ValueError): mine.review_candidates(inventory, changed, {'cost', 'decomposed'})


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

    def test_inventory_includes_declarations_once_and_refuses_changed_source_evidence(self):
        fixtures.LocalProject.seed(self)
        self.run.survey(self.estate)
        self.run.history()
        inventory = self.run.candidates()
        self.assertEqual(len([c for c in inventory['candidates'] if c['kind'] == 'declared']), 4)
        source = next(s for s in self.run.current()['sources'] if s.get('inventory_hash'))
        path = self.run.context / (source['source_id'] + '.json')
        changed = self.run.read(path)
        changed['measures'][0]['expression'] = '0'
        self.run.write(path, changed)
        with self.assertRaises(ValueError): self.run.candidates()

    def test_review_packet_provides_scoped_schema_and_does_not_rewrite_cards(self):
        (self.estate / 'querylog.jsonl').write_text(json.dumps({'sql': 'SELECT SUM(cost) FROM site.work'}))
        self.run.survey(self.estate)
        self.run.history()
        card = self.card()
        self.save(card)
        inventory = self.run.candidates()
        decisions = [{'candidate_id': c['candidate_id'], 'disposition': 'metric',
                      'metrics': ['cost'], 'reason': 'Captured cost expression.'}
                     for c in inventory['candidates']]
        self.run.candidates({'input_hash': inventory['input_hash'], 'decisions': decisions})
        self.run.review_cards()
        bundle = self.run.read(self.run.context / 'card-review-bundle.json')
        self.assertEqual([c['table'] for c in bundle['catalog']], ['site.work'])
        self.assertEqual(bundle['catalog'][0]['keys'], 'not_collected')
        self.assertEqual({c['column'] for c in bundle['catalog'][0]['columns']}, {'kind', 'cost', 'day'})
        self.assertEqual(bundle['cards'][0]['body'], card)
        note = {'name': 'cost', 'definition_hash': bundle['cards'][0]['definition_hash'],
                'source_refs': bundle['cards'][0]['supporting_source_refs'],
                'binding_reason': 'Mapped rowset exposes cost.',
                'description_reason': 'Description explains the cost amount.',
                'grain_basis': 'unknown', 'grain_reason': 'No key declaration in this evidence.',
                'dimensions': [], 'excluded_breakdowns': [], 'limitations': ['Grain is unknown.']}
        review = {'input_hash': bundle['input_hash'], 'metrics': [note]}
        self.assertEqual(self.run.review_cards(review)['status'], 'reviewed')
        self.assertEqual(self.run.read(self.run.context / 'metrics.json'), [card])
        card['description'] = 'More precise business wording.'
        self.save(card)
        with self.assertRaises(ValueError): self.run.review_cards(review)

    def test_review_requires_current_complete_dimension_and_card_notes(self):
        inventory = mine.candidate_inventory([{'shape_id': 'example',
            'sql': 'SELECT kind, SUM(cost) FROM example.work GROUP BY kind'}])
        candidate_review = {'input_hash': inventory['input_hash'], 'decisions': [
            {'candidate_id': c['candidate_id'], 'disposition': 'metric',
             'metrics': ['cost'], 'reason': 'Captured business quantity.'}
            for c in inventory['candidates']]}
        card = self.card()
        card['definition']['dimensions'] = [{'name': 'Work Kind', 'kind': 'categorical',
                                             'datatype': 'string', 'sql': 'example.work.kind'}]
        bundle = mine.card_review_bundle(inventory, candidate_review, [card])
        note = {'name': 'cost', 'definition_hash': bundle['cards'][0]['definition_hash'],
                'source_refs': ['shape:example'], 'binding_reason': 'Source binding reviewed.',
                'description_reason': 'Own measure and population explained.', 'grain_basis': 'unknown',
                'grain_reason': 'No declared key.', 'dimensions': [
                    {'name': 'Work Kind', 'reason': 'Observed business grouping.'}],
                'excluded_breakdowns': [], 'limitations': ['No key is declared.']}
        review = {'input_hash': bundle['input_hash'], 'metrics': [note]}
        self.assertEqual(mine.review_card_notes(bundle, review)['status'], 'reviewed')
        for update in ({'dimensions': []}, {'source_refs': ['invented']}, {'grain_basis': 'guessed'}):
            with self.assertRaises(ValueError):
                mine.review_card_notes(bundle, {**review, 'metrics': [{**note, **update}]})
        with self.assertRaises(ValueError): mine.review_card_notes(bundle, {**review, 'metrics': []})

    def test_review_cannot_silently_skip_fields_from_a_joined_source(self):
        inventory = mine.candidate_inventory([{'shape_id': 'example',
            'sql': 'SELECT SUM(w.cost) FROM example.work w JOIN example.regions r ON r.id=w.region_id'}])
        candidate_review = {'input_hash': inventory['input_hash'], 'decisions': [
            {'candidate_id': c['candidate_id'], 'disposition': 'metric', 'metrics': ['cost'],
             'reason': 'Captured cost quantity.'} for c in inventory['candidates']]}
        card = self.card()
        card['definition']['source'] = {'entity': 'example.work', 'alias': 'work',
            'sql': 'example.work JOIN example.regions ON example.regions.id=example.work.region_id'}
        card['definition']['dimensions'] = [{'name': 'Region', 'kind': 'categorical',
            'datatype': 'string', 'sql': 'example.regions.name'}]
        catalog = [{'source_ref': 'catalog:example.regions', 'table': 'example.regions',
                    'columns': [{'column': 'name', 'type': 'TEXT'}, {'column': 'town', 'type': 'TEXT'}]}]
        bundle = mine.card_review_bundle(inventory, candidate_review, [card], catalog)
        fields = mine.review_fields(bundle, bundle['cards'][0])
        self.assertEqual({f['column'] for f in fields}, {'name', 'town'})
        note = {'name': 'cost', 'definition_hash': bundle['cards'][0]['definition_hash'],
                'source_refs': ['shape:example'], 'binding_reason': 'Source scope reviewed.',
                'description_reason': 'Cost for joined work rows.', 'grain_basis': 'unknown',
                'grain_reason': 'No source key declared.', 'dimensions': [
                    {'name': 'Region', 'reason': 'Source-exposed business label.'}],
                'excluded_breakdowns': [], 'limitations': ['No key declaration.']}
        review = {'input_hash': bundle['input_hash'], 'metrics': [note]}
        with self.assertRaises(ValueError): mine.review_card_notes(bundle, review)
        note['field_decisions'] = [
            {'field_id': fields[0]['field_id'], 'decision': 'included', 'dimensions': ['Region'],
             'reason': 'Direct source label.'},
            {'field_id': fields[1]['field_id'], 'decision': 'unresolved', 'dimensions': [],
             'reason': 'Business meaning needs source investigation; do not invent it.'}]
        self.assertEqual(mine.review_card_notes(bundle, review)['status'], 'reviewed')
        self.assertEqual(card['definition']['dimensions'][0]['name'], 'Region')

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


class ReviewReuse(unittest.TestCase):
    def bundle(self, metrics=None, inventory=None, catalog=()):
        metrics = metrics or [CardExecution.card(self)]
        inventory = inventory or mine.candidate_inventory([
            {'shape_id': 'source', 'sql': 'SELECT SUM(cost) FROM example.work'}])
        decisions = {'input_hash': inventory['input_hash'], 'decisions': [
            {'candidate_id': c['candidate_id'], 'disposition': 'metric',
             'metrics': [m['name'] for m in metrics], 'reason': 'Source-backed quantities.'}
            for c in inventory['candidates']]}
        return mine.card_review_bundle(inventory, decisions, metrics, catalog)

    def review(self, bundle):
        notes = []
        for c in bundle['cards']:
            notes.append({'name': c['name'], 'definition_hash': c['definition_hash'],
                'source_refs': c['supporting_source_refs'], 'binding_reason': 'Captured source.',
                'description_reason': 'Own quantity explained.', 'grain_basis': 'unknown',
                'grain_reason': 'No declared key.', 'dimensions': [],
                'field_decisions': [{'field_id': f['field_id'], 'decision': 'excluded',
                    'dimensions': [], 'reason': 'Measured quantity, not a breakdown.'}
                    for f in mine.review_fields(bundle, c)],
                'excluded_breakdowns': [], 'limitations': ['Key not declared.']})
        return mine.review_card_notes(bundle, {'input_hash': bundle['input_hash'], 'metrics': notes})

    def test_shared_fields_resolve_losslessly_for_each_card(self):
        first = CardExecution.card(self)
        first['definition']['source'] = {'entity': 'example.work', 'alias': 'work', 'table': 'example.work'}
        second = copy.deepcopy(first)
        second['name'] = 'another-cost'
        catalog = [{'source_ref': 'catalog:example.work', 'table': 'example.work',
                    'columns': [{'column': 'cost', 'type': 'REAL'}]}]
        bundle = self.bundle([first, second], catalog=catalog)
        self.assertEqual(len(bundle['field_catalog']), 1)
        self.assertEqual(bundle['cards'][0]['source_fields'], bundle['cards'][1]['source_fields'])
        self.assertEqual(mine.review_fields(bundle, bundle['cards'][0])[0]['metadata'],
                         catalog[0]['columns'][0])
        self.assertEqual(len(self.review(bundle)['metrics']), 2)

    def test_prose_edit_only_invalidates_affected_unconnected_card(self):
        first = CardExecution.card(self)
        second = copy.deepcopy(first)
        second['name'] = 'other'
        bundle = self.bundle([first, second])
        reviewed = self.review(bundle)
        self.assertEqual(len(mine.reusable_card_notes(bundle, reviewed)), 2)
        first['description'] = 'More specific business meaning.'
        changed = self.bundle([first, second])
        self.assertEqual([n['name'] for n in mine.reusable_card_notes(changed, reviewed)], ['other'])
        stale = {**reviewed, 'input_hash': changed['input_hash']}
        with self.assertRaises(ValueError): mine.review_card_notes(changed, stale)
        missing = {**stale, 'metrics': mine.reusable_card_notes(changed, reviewed)}
        with self.assertRaises(ValueError): mine.review_card_notes(changed, missing)

    def test_source_and_schema_changes_invalidate_prior_review(self):
        bundle = self.bundle()
        reviewed = self.review(bundle)
        changed_source = self.bundle(inventory=mine.candidate_inventory([
            {'shape_id': 'source', 'sql': 'SELECT SUM(cost) FROM example.work WHERE active=1'}]))
        self.assertEqual(mine.reusable_card_notes(changed_source, reviewed), [])
        changed_schema = self.bundle(catalog=[{'source_ref': 'catalog:example.work',
            'table': 'example.work', 'columns': [], 'schema_hash': 'new-schema'}])
        self.assertEqual(mine.reusable_card_notes(changed_schema, reviewed), [])

    def test_changed_review_instructions_invalidate_completed_annotations(self):
        bundle = self.bundle()
        reviewed = self.review(bundle)
        with patch('metrics_mine.review_policy_hash', return_value='changed-policy'):
            changed = self.bundle()
        self.assertEqual(mine.reusable_card_notes(changed, reviewed), [])
        self.assertNotEqual(bundle['input_hash'], changed['input_hash'])

    def test_component_change_invalidates_consumer_and_transitive_dependents(self):
        first = CardExecution.card(self)
        second, third = copy.deepcopy(first), copy.deepcopy(first)
        second['name'], third['name'] = 'consumer', 'final'
        second['definition']['depends_on'] = ['cost']
        third['definition']['depends_on'] = ['consumer']
        bundle = self.bundle([first, second, third])
        reviewed = self.review(bundle)
        first['description'] = 'Revised component population.'
        changed = self.bundle([first, second, third])
        self.assertEqual(mine.reusable_card_notes(changed, reviewed), [])
        # Legacy notes with no evidence fingerprint never silently become reusable.
        for note in reviewed['metrics']: note.pop('review_hash')
        self.assertEqual(mine.reusable_card_notes(bundle, reviewed), [])


class PartialReviewSubmission(unittest.TestCase):
    setUp = fixtures.SQLiteFlow.setUp

    def test_submission_merges_only_unchanged_completed_reviews(self):
        (self.estate / 'querylog.jsonl').write_text(json.dumps({'sql': 'SELECT SUM(cost) FROM site.work'}))
        self.run.survey(self.estate)
        self.run.history()
        first = CardExecution.card(self)
        second = copy.deepcopy(first)
        second['name'] = 'other'
        self.run.write(self.run.context / 'metrics.json', [first, second])
        self.run.write(self.run.context / 'queries.json', [])
        inventory = self.run.candidates()
        self.run.candidates({'input_hash': inventory['input_hash'], 'decisions': [
            {'candidate_id': c['candidate_id'], 'disposition': 'metric',
             'metrics': ['cost', 'other'], 'reason': 'Captured source quantities.'}
            for c in inventory['candidates']]})
        self.assertEqual(len(self.run.review_cards()['pending']), 2)
        bundle = self.run.read(self.run.context / 'card-review-bundle.json')
        initial = ReviewReuse.review(self, bundle)
        self.run.review_cards(initial)
        first['description'] = 'Updated business description requiring a new review.'
        self.run.write(self.run.context / 'metrics.json', [first, second])
        plan = self.run.review_cards()
        self.assertEqual(plan['pending'], ['cost'])
        self.assertEqual(plan['reusable'], 1)
        bundle = self.run.read(self.run.context / 'card-review-bundle.json')
        revised = ReviewReuse.review(self, bundle)
        revised['metrics'] = [n for n in revised['metrics'] if n['name'] == 'cost']
        result = self.run.review_cards(revised)
        self.assertEqual(result['reused'], 1)
        self.assertEqual({n['name'] for n in result['metrics']}, {'cost', 'other'})
        self.assertEqual(self.run.review_cards()['pending'], [])
        with self.assertRaises(ValueError): self.run.review_cards(initial)
