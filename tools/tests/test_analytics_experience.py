"""Presentation never advances a run or turns provisional evidence into a release."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
from context_state import ContextState, digest
import experience


class AnalyticsExperience(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = ContextState(self.root / 'run')
        self.run.write(self.run.path, {'sources': [
            {'source_id': 'report', 'path': 'report.pbix', 'kind': 'powerbi', 'status': 'available'}]})
        self.run.write(self.run.root / 'progress.json', {'stage': 'build', 'status': 'running'})

    def write(self, name, value):
        self.run.write(self.run.root / name, value)

    def files(self):
        return {str(p.relative_to(self.run.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.run.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def candidate(self):
        body = {'name': 'shipped-units', 'display_name': 'Shipped units',
                'description': 'Units on shipped orders.', 'source_id': 'metric-source',
                'definition': {'formula': 'SUM(s.units)', 'mandatory_filters': ["s.status = 'shipped'"],
                               'examples': [{'sql': "SELECT SUM(s.units) FROM shipments s WHERE s.status = 'shipped'"}],
                               'extensions': {'atlan_analytics': {'declared': {'name': 'Shipped units', 'ref': 'report.pbix'},
                                                                 'shapes': ['shape_1']}}}}
        self.write('context/metrics.json', [body])
        return body

    def receipt(self, body):
        state = self.run.manifest()
        state['publication'] = {'workspace_id': 'workspace_example', 'artifacts': [
            {'id': 'metric_example', 'kind': 'metric', 'name': body['name'], 'release_id': 'release_example',
             'version_id': 'version_example', 'channel': 'latest', 'definition_hash': digest(body['definition'])}]}
        self.write('run.json', state)

    def test_execution_review_hold_and_release_are_independent(self):
        query = {'name': 'shipping-query', 'display_name': 'Shipping cost by region',
                 'definition': {'sql': 'SELECT SUM(cost) FROM shipments', 'review_status': 'needs_review',
                                'extensions': {'metrics_agent': {'vq_id': 'h-example'}}}}
        self.write('context/queries.json', [query])
        self.write('context/candidate-checks.json', {'sql_query:shipping-query': {
            'definition_hash': digest(query['definition']), 'status': 'executed'}})
        model = experience.snapshot(self.run)
        self.assertEqual(model['counts']['executed'], 1)
        self.assertEqual(model['counts']['review_required'], 1)
        self.assertEqual(model['counts']['held'], 0)
        self.assertEqual(model['counts']['released'], 0)
        item = model['items'][0]
        self.assertIn('source review required', item['status_label'])
        self.assertEqual(item['checks']['parity'], 'unmeasured')
        self.assertIn('Shipping cost by region', '; '.join(item['provenance']))
        self.assertNotIn('h-example', '; '.join(item['provenance']))
        self.assertIn('queries need source review', experience.render_chat(model))

    def test_query_review_count_includes_unreviewed_and_missing_without_losing_reviewed(self):
        for status, expected in [('unreviewed', 1), (None, 1), ('reviewed', 0), ('verified', 0)]:
            with self.subTest(status=status):
                self.write('context/queries.json', [{'name': 'query-example', 'definition': {
                    'sql': 'SELECT 1', 'review_status': status}}])
                model = experience.snapshot(self.run)
                self.assertEqual(model['counts']['review_required'], expected)
                self.assertEqual(model['counts']['source_reviewed'], 1 - expected)

    def test_metric_source_disclosure_resolves_query_names(self):
        metric = self.candidate()
        metric['definition']['extensions'] = {'metrics_agent': {'derived_from': ['h-example']}}
        self.write('context/metrics.json', [metric])
        self.write('context/queries.json', [{'name': 'source-query', 'display_name': 'Units shipped by region',
            'definition': {'sql': 'SELECT SUM(units) FROM shipments',
                           'extensions': {'metrics_agent': {'vq_id': 'h-example'}}}}])
        model = experience.snapshot(self.run)
        self.assertIn('Units shipped by region', '; '.join(model['sample']['provenance']))

    def test_local_preparation_continues_while_registry_access_needs_attention(self):
        self.candidate()
        state = self.run.manifest()
        state['execution'] = {'kind': 'sqlite', 'read_only': True}
        self.write('run.json', state)
        self.write('auth-recovery.json', {'status': 'login_required', 'host': 'claude_code', 'server': 'atlan'})
        model = experience.snapshot(self.run)
        self.assertEqual(model['auth_status'], 'login_required')
        self.assertTrue(model['local_work_available'])
        self.assertEqual(model['phase']['status'], 'running')
        self.assertEqual(model['continuation']['action'], 'build')
        self.assertIn('Local preparation can continue', ' '.join(model['handoff']['attention']))
        self.write('progress.json', {'stage': 'publish', 'status': 'running'})
        model = experience.snapshot(self.run)
        self.assertEqual(model['phase']['status'], 'access_needed')
        self.assertEqual(model['continuation']['action'], 'recover-access')

    def test_handoff_distinguishes_local_checks_from_releases_and_coverage(self):
        body = self.candidate()
        self.write('context/candidate-checks.json', {'metric:shipped-units': {
            'definition_hash': digest(body['definition']), 'status': 'example_executed'}})
        selection = digest({key: body.get(key) for key in
                            ('name', 'display_name', 'description', 'source_id', 'definition')})
        note = {'kind': 'metric', 'selected_hash': selection,
                'comparison': {'dimensions': 'Region only.', 'limitations': 'Channel remains local.'}}
        self.write('context/plan.json', {'coverage': [note]})
        model = experience.snapshot(self.run)
        self.assertEqual(model['handoff']['released'], 0)
        self.assertEqual(model['handoff']['local'], 1)
        self.assertEqual(model['handoff']['coverage_notes'], 1)
        self.assertIn('nothing from this run is shared', model['handoff']['usable_now'])
        self.assertIn('1 have recorded execution checks', model['handoff']['usable_now'])
        self.assertIn('Channel remains local', ' '.join(model['sample']['detail_notes']))
        self.receipt(body)
        model = experience.snapshot(self.run)
        self.assertEqual(model['handoff']['released'], 1)
        self.assertEqual(model['handoff']['local'], 0)
        self.assertIn('matching release receipts', model['handoff']['usable_now'])
        body['definition']['formula'] = 'SUM(s.units) * 2'
        self.write('context/metrics.json', [body])
        model = experience.snapshot(self.run)
        self.assertEqual(model['handoff']['released'], 0)
        self.assertEqual(model['handoff']['coverage_notes'], 0)
        self.assertEqual(model['counts']['executed'], 0)

    def engine(self, sql='SELECT SUM(cost) FROM work'):
        layer = {'contract_version': '1.0', 'dialect': 'duckdb', 'scope': 'example', 'profile': 'history-only',
                 'objects': [{'vq_id': 'vq_1', 'sql': sql, 'question': 'What is total cost?'}]}
        layer['layer_sha'] = hashlib.sha256(json.dumps(layer, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]
        self.write('engine/verified_queries.json', layer)
        proposals = {'contract_version': '1.0', 'layer_sha': layer['layer_sha'], 'proposals': [
            {'proposal_id': 'proposal_1', 'vq_id': 'vq_1', 'name': 'Total cost', 'expression': 'SUM(cost)',
             'entity': 'work', 'definition': 'Cost of the selected work.', 'mandatory_filters': []}]}
        self.write('engine/metric_proposals.json', proposals)
        manifest = {'contract_version': '1.0', 'run_id': 'example', 'stages': [
            {'stage': 'c5_emit', 'verdict': 'ok', 'outputs': ['verified_queries.json']},
            {'stage': 'x1_propose', 'verdict': 'ok', 'outputs': ['metric_proposals.json']},
            {'stage': 'x2_emit', 'verdict': 'ok', 'notes': 'awaiting_decision', 'outputs': []}]}
        self.write('engine/manifest.json', manifest)
        self.write('engine/module.json', {'module': 'metrics', 'status': 'paused'})
        return layer, proposals, manifest

    def test_partial_engine_sample_is_evidenced_but_never_exported_or_published(self):
        self.engine()
        before = self.files()
        model = experience.snapshot(self.run, 'engine')
        self.assertEqual(model['phase']['source'], 'engine')
        self.assertEqual(model['phase']['checkpoint'], 'x2_emit')
        self.assertEqual(model['phase']['completed_checkpoints'], 2)
        self.assertEqual(model['sample']['name'], 'Total cost')
        self.assertEqual(model['sample']['status'], 'provisional')
        self.assertEqual(model['sample']['sql'], 'SELECT SUM(cost) FROM work')
        self.assertEqual(model['counts']['metrics'], 0)
        self.assertEqual(model['counts']['provisional'], 1)
        self.assertEqual(model['counts']['released'], 0)
        self.assertFalse(model['live'])
        self.assertFalse(model['remote_writes'])
        self.assertIn('metric checks pending', experience.render_chat(model, detail=True))
        experience.render_html(model)
        self.assertEqual(before, self.files())
        self.assertFalse((self.run.context / 'metrics.json').exists())

    def test_path_and_string_inputs_select_the_same_saved_run_as_project_objects(self):
        self.candidate()
        before = self.files()
        expected = experience.snapshot(self.run)
        for selected in (self.run.root, str(self.run.root)):
            with self.subTest(selected_type=type(selected).__name__):
                model = experience.snapshot(selected)
                for key in ('sample', 'counts', 'scope', 'phase', 'warnings'):
                    self.assertEqual(model[key], expected[key])
        self.assertEqual(self.files(), before)

    def test_access_readiness_is_scoped_to_the_observed_host_and_server(self):
        body = self.candidate()
        self.receipt(body)
        self.write('auth-recovery.json', {'host': 'claude_code', 'server': 'plugin:atlanai:atlan',
                                         'status': 'login_required', 'started_at': 1000})
        self.write('registry-readiness.json', {'schema_version': 1, 'observations': [
            {'host': 'codex', 'server': 'atlan', 'status': 'connected',
             'read_tool': 'registry__list_workspaces', 'read_succeeded': True, 'observed_at': 2000}]})
        before = self.files()
        codex = experience.snapshot(self.run, host='codex', server='atlan')
        self.assertEqual(codex['phase']['status'], 'complete')
        self.assertEqual(codex['auth_host'], 'codex')
        self.assertEqual(codex['counts']['released'], 1)
        for args in ({}, {'host': 'claude_code', 'server': 'plugin:atlanai:atlan'}):
            model = experience.snapshot(self.run, **args)
            self.assertEqual(model['auth_status'], 'login_required')
            self.assertEqual(model['phase']['status'], 'access_needed')
            self.assertEqual(model['phase']['label'], 'Your metrics and queries are saved in Atlan Trident')
        other_server = experience.snapshot(self.run, host='codex', server='other-atlan')
        self.assertEqual(other_server['auth_status'], 'unverified')
        self.assertEqual(self.files(), before)

    def test_readiness_cannot_hide_a_newer_failure_or_owned_login_task(self):
        self.candidate()
        observation = {'host': 'codex', 'server': 'atlan', 'status': 'connected',
                       'read_tool': 'registry__list_workspaces', 'read_succeeded': True, 'observed_at': 2000}
        self.write('registry-readiness.json', {'schema_version': 1, 'observations': [observation]})
        for recovery in (
            {'status': 'permission_denied', 'started_at': 1000, 'updated_at': 2500},
            {'status': 'waiting_auth', 'started_at': 1000, 'task_id': 'owned_task'},
        ):
            self.write('auth-recovery.json', {'host': 'codex', 'server': 'atlan', **recovery})
            model = experience.snapshot(self.run, host='codex', server='atlan')
            self.assertEqual(model['auth_status'], recovery['status'])
        self.write('auth-recovery.json', {})
        for changed in ({'read_succeeded': False}, {'read_tool': 'other_tool'}, {'observed_at': -1}):
            self.write('registry-readiness.json', {'schema_version': 1, 'observations': [{**observation, **changed}]})
            self.assertEqual(experience.snapshot(self.run, host='codex', server='atlan')['auth_status'], 'unverified')

    def test_m1_query_layer_uses_its_own_recorded_hash_protocol(self):
        layer, proposals, manifest = self.engine()
        # M1 keeps these timestamps in its output, but removes them when hashing.
        layer['objects'][0]['witness'] = {'mode': 'execute', 'executed_at': '2026-01-01T00:00:00Z'}
        content = copy.deepcopy(layer['objects'])
        del content[0]['witness']['executed_at']
        layer['layer_sha'] = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:12]
        proposals['layer_sha'] = layer['layer_sha']
        manifest['stages'].insert(1, {'stage': 'm1_merge', 'verdict': 'ok', 'outputs': ['verified_queries.json']})
        self.write('engine/verified_queries.json', layer)
        self.write('engine/metric_proposals.json', proposals)
        self.write('engine/manifest.json', manifest)
        before = self.files()
        model = experience.snapshot(self.run, 'engine')
        self.assertEqual(model['sample']['calculation'], 'SUM(cost)')
        self.assertEqual(model['sample']['status'], 'provisional')
        self.assertEqual(model['counts']['provisional'], 1)
        self.assertFalse(model['warnings'])
        self.assertEqual(self.files(), before)
        self.assertFalse((self.run.context / 'metrics.json').exists())
        layer['objects'][0]['witness']['executed_at'] = '2026-02-01T00:00:00Z'
        self.write('engine/verified_queries.json', layer)
        self.assertIsNotNone(experience.snapshot(self.run, 'engine')['sample'])
        layer['objects'][0]['sql'] = 'SELECT SUM(other_cost) FROM work'
        self.write('engine/verified_queries.json', layer)
        model = experience.snapshot(self.run, 'engine')
        self.assertIsNone(model['sample'])
        self.assertIn('query layer changed', model['warnings'][0])
        layer['objects'][0]['sql'] = 'SELECT SUM(cost) FROM work'
        self.write('engine/verified_queries.json', layer)
        for producer in ('c5_emit', 'unknown_producer'):
            with self.subTest(producer=producer):
                manifest['stages'][1]['stage'] = producer
                self.write('engine/manifest.json', manifest)
                self.assertIsNone(experience.snapshot(self.run, 'engine')['sample'])

    def test_recorded_engine_path_is_confined_to_this_run(self):
        self.engine()
        state = self.run.manifest()
        state['engine_run'] = str(self.run.root / 'engine')
        self.write('run.json', state)
        self.assertEqual(experience.snapshot(self.run)['sample']['status'], 'provisional')
        state['engine_run'] = str(self.root / 'outside')
        self.write('run.json', state)
        with self.assertRaises(ValueError):
            experience.snapshot(self.run)

    def test_missing_selected_engine_warns_without_changing_saved_evidence(self):
        self.engine()
        body = self.candidate()
        self.receipt(body)
        self.write('progress.json', {'stage': 'ready', 'status': 'complete'})
        for selected in ('missing-engine', 'run/engine'):
            with self.subTest(selected=selected):
                before = self.files()
                model = experience.snapshot(self.run, selected)
                self.assertIsNone(model['engine'])
                self.assertIn('engine directory is missing', model['warnings'][0])
                self.assertIn('engine directory is missing', experience.render_chat(model))
                self.assertNotEqual(model['phase']['label'], 'Your metrics and queries are saved in Atlan Trident')
                self.assertEqual(model['counts']['released'], 1)
                self.assertFalse((self.run.root / selected).exists())
                self.assertEqual(self.files(), before)
        state = self.run.manifest()
        state['engine_run'] = 'missing-engine'
        self.write('run.json', state)
        before = self.files()
        model = experience.snapshot(self.run)
        self.assertIn('engine directory is missing', model['next_step'])
        self.assertEqual(self.files(), before)

    def test_proposal_needs_matching_layer_and_successful_manifest_output(self):
        layer, proposals, manifest = self.engine()
        proposals['layer_sha'] = 'different'
        self.write('engine/metric_proposals.json', proposals)
        self.assertIsNone(experience.snapshot(self.run, 'engine')['sample'])
        proposals['layer_sha'] = layer['layer_sha']
        self.write('engine/metric_proposals.json', proposals)
        manifest['stages'][1]['verdict'] = 'blocked'
        self.write('engine/manifest.json', manifest)
        self.assertIsNone(experience.snapshot(self.run, 'engine')['sample'])
        manifest['stages'][1]['verdict'] = 'ok'
        self.write('engine/manifest.json', manifest)
        layer['objects'][0]['sql'] = 'SELECT SUM(other) FROM work'
        self.write('engine/verified_queries.json', layer)
        self.assertIsNone(experience.snapshot(self.run, 'engine')['sample'])

    def test_unreferenced_or_invented_expression_is_not_a_sample(self):
        _, proposals, _ = self.engine()
        for change in ({'vq_id': 'missing'}, {'vq_id': None}, {'expression': 'SUM(invented)'}):
            row = copy.deepcopy(proposals)
            row['proposals'][0].update(change)
            self.write('engine/metric_proposals.json', row)
            self.assertIsNone(experience.snapshot(self.run, 'engine')['sample'])

    def test_merged_intermediate_metric_keeps_source_provenance(self):
        layer, _, manifest = self.engine()
        manifest['stages'].append({'stage': 'x2_ingest', 'verdict': 'ok', 'outputs': ['merged_metrics.json']})
        self.write('engine/manifest.json', manifest)
        self.write('engine/merged_metrics.json', {'contract_version': '1.0', 'layer_sha': layer['layer_sha'],
                   'metrics': [{'metric_id': 'merged_1', 'name': 'Work cost', 'definition': 'The cost of work.',
                                'expression': 'SUM(cost)', 'sources': [{'vq_id': 'vq_1', 'proposal_id': 'proposal_1'}]}]})
        model = experience.snapshot(self.run, 'engine')
        self.assertEqual(model['sample']['name'], 'Work cost')
        self.assertIn('vq_1', ' '.join(model['sample']['provenance']))
        self.assertEqual(model['sample']['status'], 'provisional')

    def test_compact_metric_has_calculation_population_source_and_optional_sql(self):
        body = self.candidate()
        self.write('context/candidate-checks.json', {'metric:shipped-units': {
            'definition_hash': digest(body['definition']), 'status': 'schema_checked'}})
        model = experience.snapshot(self.run)
        compact = experience.render_chat(model)
        self.assertIn('1 metric,', compact)
        self.assertIn('1 source.', compact)
        self.assertIn('SUM(s.units)', compact)
        self.assertIn("s.status = 'shipped'", compact)
        self.assertIn('Model:', compact)
        self.assertNotIn('SELECT SUM', compact)
        self.assertIn("WHERE s.status = 'shipped'", experience.render_chat(model, detail=True))
        self.assertEqual(model['sample']['status'], 'schema_checked')
        body['definition']['formula'] = 'SUM(s.returns)'
        self.write('context/metrics.json', [body])
        self.assertEqual(experience.snapshot(self.run)['sample']['status'], 'draft')

    def test_release_requires_matching_receipt_and_held_overrides_release(self):
        body = self.candidate()
        self.write('context/candidate-checks.json', {'metric:shipped-units': {
            'definition_hash': digest(body['definition']), 'status': 'released'}})
        self.assertEqual(experience.snapshot(self.run)['counts']['released'], 0)
        self.receipt(body)
        self.assertEqual(experience.snapshot(self.run)['counts']['released'], 1)
        self.write('context/candidate-checks.json', {'metric:shipped-units': {
            'definition_hash': digest(body['definition']), 'status': 'held'}})
        model = experience.snapshot(self.run)
        self.assertEqual(model['counts']['released'], 0)
        self.assertEqual(model['counts']['held'], 1)
        self.assertIn('repair before publication', model['next_step'])
        body['definition']['formula'] = 'SUM(s.returns)'
        self.write('context/metrics.json', [body])
        self.assertEqual(experience.snapshot(self.run)['sample']['status'], 'draft')

    def test_partial_release_handoff_excludes_holds_and_preserves_other_blockers(self):
        released = self.candidate()
        held = copy.deepcopy(released)
        held.update(name='returned-units', display_name='Returned units', source_id='held-source')
        held['definition']['formula'] = 'SUM(s.returns)'
        self.write('context/metrics.json', [released, held])
        self.receipt(released)
        checks = {'metric:returned-units': {'definition_hash': digest(held['definition']), 'status': 'held'}}
        self.write('context/candidate-checks.json', checks)
        self.write('progress.json', {'stage': 'ready', 'status': 'complete-with-held-items'})
        before = self.files()
        model = experience.snapshot(self.run)
        self.assertEqual(model['phase']['status'], 'partial_ready')
        self.assertEqual(model['counts']['released'], 1)
        self.assertEqual(model['counts']['held'], 1)
        self.assertEqual(model['sample']['status'], 'released')
        self.assertIn('only by released definitions', model['next_prompt'])
        self.assertIn('exclude held items', model['next_prompt'])
        self.assertIn('repair before publication', experience.render_chat(model))
        experience.render_html(model)
        self.assertEqual(self.files(), before)
        outstanding = copy.deepcopy(held)
        outstanding.update(name='pending-units', source_id='pending-source')
        self.write('context/metrics.json', [released, held, outstanding])
        for status in ('draft', 'schema_checked'):
            with self.subTest(status=status):
                self.write('context/candidate-checks.json', {**checks, 'metric:pending-units': {
                    'definition_hash': digest(outstanding['definition']), 'status': status}})
                self.assertNotEqual(experience.snapshot(self.run)['phase']['status'], 'partial_ready')
        self.write('context/metrics.json', [released, held])
        self.write('auth-recovery.json', {'status': 'permission_denied'})
        self.assertEqual(experience.snapshot(self.run)['phase']['status'], 'access_needed')
        self.write('auth-recovery.json', {'status': 'connected'})
        self.write('progress.json', {'stage': 'ready', 'status': 'canceled'})
        self.assertEqual(experience.snapshot(self.run)['phase']['status'], 'canceled')
        self.write('progress.json', {'stage': 'ready', 'status': 'complete-with-held-items'})
        (self.run.context / 'queries.json').write_text('{broken')
        self.assertNotEqual(experience.snapshot(self.run)['phase']['status'], 'partial_ready')

    def test_old_release_receipts_do_not_hide_a_blocked_engine(self):
        body = self.candidate()
        self.receipt(body)
        self.engine()
        self.write('engine/module.json', {'module': 'metrics', 'status': 'blocked'})
        for stage, status in (('build', 'running'), ('ready', 'complete')):
            with self.subTest(stage=stage):
                self.write('progress.json', {'stage': stage, 'status': status})
                before = self.files()
                model = experience.snapshot(self.run, 'engine')
                self.assertEqual(model['counts']['released'], 1)
                self.assertEqual(model['sample']['status'], 'released')
                self.assertEqual(model['phase']['status'], 'blocked')
                self.assertIn('repair', model['next_step'])
                self.assertIn('blocking check', model['next_prompt'])
                self.assertNotIn('Choose a question', experience.render_chat(model))
                self.assertEqual(self.files(), before)

    def test_partial_ready_keeps_holds_excluded_from_export_visible(self):
        body = self.candidate()
        self.receipt(body)
        document = {'format': 2, 'checks': {'compiler_review': 1, 'required_member_scope': 1, 'card_aggregation': 1},
                    'outputs': {'metric_cards.json': 'a' * 64}, 'queries': [],
                    'metrics': [{'source_id': 'held-source', 'name': 'returned-units', 'status': 'held',
                                 'reason': 'dashboard_parity_failed'}]}
        self.write('context/publication-holds.json', document)
        self.write('context/metrics-source.json', {'module': 'metrics', 'outputs': document['outputs'],
                                                  'publication_holds_sha256': digest(document)})
        self.write('progress.json', {'stage': 'ready', 'status': 'complete-with-held-items'})
        model = experience.snapshot(self.run)
        self.assertEqual(model['phase']['status'], 'partial_ready')
        self.assertEqual(len(model['items']), 1)
        self.assertEqual(model['counts']['held'], 1)
        self.assertEqual(model['counts']['released'], 1)

    def test_structured_holds_are_visible_even_when_export_excluded_the_metric(self):
        document = {'format': 2, 'checks': {'compiler_review': 1, 'required_member_scope': 1, 'card_aggregation': 1},
                    'outputs': {'metric_cards.json': 'a' * 64}, 'queries': [],
                    'metrics': [{'source_id': 'metric-source', 'name': 'shipped-units', 'status': 'held',
                                 'reason': 'dashboard_parity_failed'}]}
        self.write('context/publication-holds.json', document)
        self.write('context/metrics-source.json', {'module': 'metrics', 'outputs': document['outputs'],
                                                  'publication_holds_sha256': digest(document)})
        model = experience.snapshot(self.run)
        self.assertEqual(model['counts']['held'], 1)
        self.assertEqual(model['counts']['released'], 0)
        self.assertIn('repair', model['next_step'])
        body = self.candidate()
        self.receipt(body)
        self.assertEqual(experience.snapshot(self.run)['sample']['status'], 'held')
        document['checks'] = {}
        self.write('context/publication-holds.json', document)
        model = experience.snapshot(self.run)
        self.assertTrue(model['warnings'])
        self.assertNotEqual(model['sample']['status'], 'released')

    def test_source_sql_privacy_and_length_limits_are_explicit_not_silent_edits(self):
        self.engine("SELECT SUM(cost) FROM work WHERE customer_id = 42")
        model = experience.snapshot(self.run, 'engine')
        self.assertEqual(model['sample']['calculation'], 'SUM(cost)')
        self.assertEqual(model['sample']['sql'], '')
        self.assertIn('privacy filter', ' '.join(model['sample']['detail_notes']))
        self.assertNotIn('customer_id = 42', experience.render_html(model))
        self.engine('SELECT SUM(cost) FROM work /*' + 'x' * 12000 + '*/')
        model = experience.snapshot(self.run, 'engine')
        self.assertEqual(model['sample']['sql'], '')
        self.assertIn('too long', experience.render_chat(model, detail=True))

    def test_missing_corrupt_and_invalid_records_do_not_claim_readiness(self):
        empty = self.root / 'not-created'
        model = experience.snapshot(empty)
        self.assertEqual(model['counts']['released'], 0)
        self.assertFalse(empty.exists())
        self.candidate()
        (self.run.context / 'metrics.json').write_text('{broken')
        model = experience.snapshot(self.run)
        self.assertTrue(model['warnings'])
        self.assertIsNone(model['sample'])
        self.write('context/metrics.json', [{'name': 'bad', 'definition': 'not an object'}])
        with self.assertRaises(ValueError):
            experience.snapshot(self.run)

    def test_symlinks_outside_paths_and_large_files_are_refused(self):
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        self.run.context.mkdir()
        (self.run.context / 'metrics.json').symlink_to(outside)
        with self.assertRaises(ValueError):
            experience.snapshot(self.run)
        (self.run.context / 'metrics.json').unlink()
        with (self.run.context / 'metrics.json').open('wb') as stream:
            stream.truncate(experience.MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            experience.snapshot(self.run)
        with self.assertRaises(ValueError):
            experience.snapshot(self.run, '../outside')

    def test_pause_auth_and_preview_copy_follow_recorded_state(self):
        self.candidate()
        self.write('progress.json', {'stage': 'preview', 'status': 'waiting-for-user'})
        model = experience.snapshot(self.run)
        self.assertIn('paused', model['next_step'])
        self.assertIn('Resume', model['next_prompt'])
        self.write('progress.json', {'stage': 'preview', 'status': 'running'})
        self.assertIn('keep these definitions local', experience.snapshot(self.run)['next_step'])
        self.write('auth-recovery.json', {'status': 'waiting_auth', 'browser_opened': False})
        self.assertNotIn('opened browser', experience.snapshot(self.run)['next_step'])
        self.write('auth-recovery.json', {'status': 'waiting_auth', 'browser_opened': True})
        self.assertIn('opened browser', experience.snapshot(self.run)['next_step'])
        self.write('auth-recovery.json', {'status': 'permission_denied'})
        self.assertIn('existing workspace', experience.snapshot(self.run)['next_step'])

    def test_html_escapes_sources_and_has_only_real_read_only_controls(self):
        body = self.candidate()
        body['display_name'] = '<img src="https://example.invalid/track" onerror="alert(1)">'
        body['description'] = '</script><script>alert(2)</script>'
        self.write('context/metrics.json', [body])
        before = self.files()
        model = experience.snapshot(self.run)
        page = experience.render_html(model)
        self.assertNotIn('<img src=', page)
        self.assertNotIn('<script>alert(2)', page)
        self.assertIn('&lt;img', page)
        self.assertIn('Snapshot at', page)
        self.assertIn('does not update itself', page)
        self.assertIn('script-src \'sha256-', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('<details>', page)
        self.assertIn('Copy next prompt', page)
        self.assertNotIn('fetch(', page)
        self.assertNotIn('>Publish</button>', page)
        self.assertNotIn('>Pause</button>', page)
        self.assertEqual(before, self.files())
        self.assertNotIn('https://example.invalid', experience.render_chat(model))


if __name__ == '__main__':
    unittest.main()
