"""Publication types fail locally; complete inline release readbacks need no host file."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
import context_contracts as mine
import registry_contract as contract
import test_local_project as fixtures
import test_metric_authoring as cards


class TypedDefinitions(unittest.TestCase):
    def metric(self):
        card = cards.CardExecution.card(self)
        card['definition'].update(grain='One row per source work entry; key is not declared.',
                                  exclusions=['Other task kinds.'])
        return card

    def query(self):
        raw = fixtures.readbacks()['artifacts'][0]
        return {k: raw[k] for k in ('name', 'display_name')} | {
            'source_id': 'example/source', 'definition': raw['metadata']['definition']}

    def test_reported_metric_types_fail_with_paths_and_correct_bodies_pass(self):
        card = self.metric()
        self.assertEqual(mine.check_metric(card)[0], [])
        original = copy.deepcopy(card)
        for field, bad in [('grain', ['example.work.id']), ('exclusions', 'Other rows')]:
            changed = copy.deepcopy(card); changed['definition'][field] = bad
            errors, _ = mine.check_metric(changed)
            self.assertTrue(any('definition.' + field + ' must be' in e for e in errors), errors)
        self.assertEqual(card, original)

    def test_nested_types_and_boolean_versions_fail_before_sql_inspection(self):
        for path, bad in [(('version',), True), (('source',), []),
                          (('formula',), {'sql': 'not text'}),
                          (('output', 'datatype'), 4), (('dimensions',), {}),
                          (('examples',), ['SELECT 1']), (('mandatory_filters',), [False])]:
            card = self.metric(); node = card['definition']
            for key in path[:-1]: node = node[key]
            node[path[-1]] = bad
            self.assertTrue(mine.check_metric(card)[0], path)
        card = self.metric(); card['definition']['source']['parameters'] = [
            {'name': 'limit', 'datatype': 'integer'}]
        self.assertIn('definition.source.parameters[0].default is required', mine.check_metric(card)[0])

    def test_wire_integer_limits_are_exact_and_do_not_round_through_float(self):
        self.assertEqual(contract.schema('metric')['properties']['version']['maximum'], 2**63 - 1)
        card = self.metric(); card['definition']['version'] = 2**63
        self.assertTrue(any('definition.version' in e for e in mine.check_metric(card)[0]))
        query = self.query(); query['definition']['usage'] = {'execution_count': 2**64 - 1}
        self.assertEqual(mine.check_query(query)[0], [])
        query['definition']['usage']['execution_count'] = 2**64
        self.assertTrue(mine.check_query(query)[0])

    def test_query_analysis_requires_structured_filters_and_wire_parameters(self):
        query = self.query()
        query['definition']['analysis'] = {'tables': ['site.work'], 'filters': [
            {'column': 'site.work.kind', 'operator': '=', 'value': 'rework'}]}
        self.assertEqual(mine.check_query(query)[0], [])
        for filters in (['site.work.kind = ?'], [{'column': 'site.work.kind'}],
                        [{'column': 5, 'operator': '='}]):
            bad = copy.deepcopy(query); bad['definition']['analysis']['filters'] = filters
            errors, _ = mine.check_query(bad)
            self.assertTrue(any('definition.analysis.filters[0]' in e for e in errors), errors)
        query['definition']['parameters'] = [{'name': 'kind', 'datatype': 'string', 'default': 'rework'}]
        self.assertTrue(mine.check_query(query)[0])
        query['definition']['parameters'] = [{'name': 'kind', 'data_type': 'string'}]
        self.assertEqual(mine.check_query(query)[0], [])

    def test_mine_plan_refuses_bad_wire_shapes_before_any_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metric, query = self.metric(), self.query()
            metric['definition']['grain'] = ['example.work.id']
            query['definition']['analysis'] = {'filters': ['site.work.kind = ?']}
            for name, value in [('metrics', [metric]), ('queries', [query]), ('links', [])]:
                (root / (name + '.json')).write_text(json.dumps(value))
            result = mine.cmd_check(argparse.Namespace(dir=folder))
            plan = json.loads((root / 'plan.json').read_text())
            self.assertFalse(plan['metrics'][0]['ok'])
            self.assertFalse(plan['queries'][0]['ok'])
            self.assertIn('definition.grain', json.dumps(result))
            self.assertIn('definition.analysis.filters[0]', json.dumps(result))

    def test_extensions_preserve_evidence_and_numeric_sample_cells_are_refused(self):
        card = self.metric()
        card['definition']['extensions']['atlan_analytics']['grain_evidence'] = {
            'keys': ['example.work.id'], 'basis': 'unknown', 'limitations': ['No declared key.']}
        self.assertEqual(mine.check_metric(card)[0], [])
        example = card['definition']['examples'][0]
        example['result'] = {'columns': ['cost'], 'rows': [[14.75]], 'row_count': 1,
                             'hash': 'example', 'executed_at': '2026-01-01T00:00:00Z'}
        self.assertTrue(any('result.rows[0][0]' in e for e in mine.check_metric(card)[0]))
        example['result']['rows'] = [['14.75']]
        self.assertEqual(mine.check_metric(card)[0], [])

    def test_saved_query_review_states_match_registry_and_schema_resources_are_present(self):
        query = self.query(); query['definition']['review_status'] = 'reviewed'
        self.assertTrue(any('definition.review_status' in e for e in mine.check_query(query)[0]))
        self.assertEqual(contract.schema('metric')['properties']['grain']['type'], 'string')
        self.assertEqual(contract.schema('metric')['properties']['exclusions']['items']['type'], 'string')
        for spec in ('metric', 'sql_query'):
            self.assertTrue((contract.CONTRACTS / contract.FILES[spec]).is_file())


class InlinePublication(unittest.TestCase):
    setUp = fixtures.LocalProject.setUp

    def run_stdin(self, payload):
        return subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'),
            '--run-dir', str(self.run.root), 'publication', '-'], input=json.dumps(payload),
            text=True, capture_output=True)

    def test_inline_response_records_same_receipt_without_host_saved_file(self):
        self.run.survey(self.estate)
        result = self.run_stdin(fixtures.readbacks())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['recorded_from'], 'host_mcp_release_readbacks')
        receipt = self.run.current()['publication']
        self.assertEqual(receipt['artifacts'][0]['id'], 'sql_query_example')
        self.assertNotIn('definition', receipt['artifacts'][0])

    def test_inline_readbacks_cannot_substitute_ids_or_draft_responses(self):
        self.run.survey(self.estate)
        for payload in ({'workspace_id': 'workspace_example', 'artifacts': ['sql_query_example']},
                        fixtures.readbacks()):
            if isinstance(payload['artifacts'][0], dict): payload['artifacts'][0]['is_released'] = False
            result = self.run_stdin(payload)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertNotIn('publication', self.run.current())
