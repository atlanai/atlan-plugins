"""Synthetic regressions for compiler-review holds through export and publication."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'plugins/atlanai-analytics'
SCRIPTS = PLUGIN / 'skills/atlan-analytics/scripts'
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(SCRIPTS))
from modules.metrics import holds, driver
import metrics_mine

spec = importlib.util.spec_from_file_location('hold_export', PLUGIN / 'modules/metrics/export.py')
exporter = importlib.util.module_from_spec(spec)
with mock.patch.dict(sys.modules, {'driver': driver}):
    spec.loader.exec_module(exporter)


def query(i):
    return {'vq_id': f'q-{i}', 'name': f'query-{i}', 'sql': f'SELECT {i} AS value',
            'provenance': {'lane': 'bi', 'bi': {'unit_id': f'unit-{i}'}}, 'description': 'A synthetic query.'}


def report(*wrong):
    return {'contract_version': '1.0', 'stage': 'c4_gates', 'clean': True, 'counts': {},
            'checks': [{'gate': 'compiler_review', 'object': f'q-{i}', 'status': 'warn',
                        'rule': 'review', 'detail': 'The selection filter is missing.'} for i in wrong]}


class StructuredHoldTests(unittest.TestCase):
    def test_only_effective_compiler_failures_hold_not_prose_or_missing_access(self):
        queries = {'objects': [query(0), query(1)]}
        queries['objects'][1].update(description='Previously held for review.', label='parse_only')
        checked = report(0)
        checked['checks'].append({'gate': 'column_grounding', 'object': 'q-1', 'status': 'warn'})
        self.assertEqual(set(holds.review_holds(queries, checked)), {'q-0'})
        # C4 omits a compiler-review warning when the reviewed SQL has been replaced.
        self.assertEqual(holds.review_holds(queries, report()), {})
        with self.assertRaisesRegex(ValueError, 'compiler-review gate report'):
            holds.review_holds(queries, None)
        queries['objects'] = [{'vq_id': 'history-1', 'provenance': {'lane': 'history'}}]
        self.assertEqual(holds.review_holds(queries, None), {})
        with self.assertRaisesRegex(ValueError, 'absent'):
            holds.review_holds(queries, report(0))

    def context(self, root):
        document = {'format': 1, 'outputs': {'verified_queries.json': 'a' * 64},
                    'metrics': [{'source_id': 'metric:held', 'name': 'Held metric',
                                 'status': 'held', 'reason': 'source_query_held'}],
                    'queries': [{'source_id': 'query:held', 'name': 'held-query', 'query_id': 'q-0',
                                 'status': 'held', 'reason': 'compiler_review_wrong',
                                 'sql_sha256': holds.sql_identity('SELECT 0 AS value')}]}
        source = {'module': 'metrics', 'outputs': document['outputs'],
                  'publication_holds_sha256': holds.identity(document)}
        for name, value in [('publication-holds', document), ('metrics-source', source),
                            ('metrics', []), ('queries', []), ('links', [])]:
            (root / (name + '.json')).write_text(json.dumps(value))
        return document

    def test_preflight_rejects_reinserted_queries_metrics_links_and_changed_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            document = self.context(root)
            self.assertEqual(holds.enforce_context(root), document)
            cases = [([], [{'source_id': 'query:held', 'name': 'renamed'}], []),
                     ([], [{'name': 'renamed', 'definition': {'sql': 'SELECT 0 AS value'}}], []),
                     ([], [{'definition': {'extensions': {'metrics_agent': {'vq_id': 'q-0'}}}}], []),
                     ([{'source_id': 'metric:held'}], [], []),
                     ([{'definition': {'depends_on': ['Held metric']}}], [], []),
                     ([{'definition': {'extensions': {'metrics_agent': {'card': {'source_id': 'metric:held'}}}}}], [], []),
                     ([], [], [{'from': {'kind': 'sql_query', 'name': 'held-query'},
                                'to': {'kind': 'metric', 'name': 'Existing metric'}}])]
            for metrics, queries, links in cases:
                with self.subTest(metrics=metrics, queries=queries, links=links), self.assertRaisesRegex(ValueError, 'held'):
                    holds.enforce_context(root, metrics, queries, links)
            document['queries'] = []
            (root / holds.FILE).write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                holds.enforce_context(root)
            (root / holds.FILE).unlink()
            with self.assertRaisesRegex(ValueError, 'Re-export'):
                holds.enforce_context(root)

    def test_publication_checker_and_authorization_cli_refuse_readmission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.context(root)
            (root / 'queries.json').write_text(json.dumps([{'source_id': 'query:held'}]))
            with self.assertRaises(metrics_mine.Blocked):
                metrics_mine.cmd_check(types.SimpleNamespace(dir=str(root)))
            (root / 'authorization.json').write_text(json.dumps({'intent': 'publish', 'mode': 'auto',
                                    'workspace_id': 'workspace_demo', 'sources': ['source_demo']}))
            (root / 'plan.json').write_text(json.dumps({'metrics': [], 'queries': [], 'links': []}))
            p = subprocess.run([sys.executable, str(SCRIPTS / 'analytics_flow.py'), '--authorization',
                str(root / 'authorization.json'), '--context', str(root), '--workspace', 'workspace_demo',
                '--source', 'source_demo'], text=True, capture_output=True)
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertEqual(json.loads(p.stdout)['decision'], 'needs_context')

    def test_legacy_authored_contexts_keep_their_existing_path(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(holds.enforce_context(Path(temp).resolve()))


class ExportHoldTests(unittest.TestCase):
    def export(self, root, *, dependent=False):
        run, context = root / 'run', root / 'context'
        run.mkdir()
        objects = [query(i) for i in range(18)]
        objects[17]['description'] = 'Previously held for a presentation, not a review decision.'
        cards = [{'name': f'Metric {i}', 'source_id': f'metric:{i}', 'derived_from': [f'q-{i}']}
                 for i in range(7, 11)]
        if dependent:
            cards[0]['derived_from'] = ['q-0']
        metrics = [{'name': c['name'], 'source_id': c['source_id'],
                    'definition': {'extensions': {}}} for c in cards]
        saved = [{'name': q['name'], 'source_id': 'query:' + q['vq_id'], 'description': q['description'],
                  'definition': {'sql': q['sql'], 'extensions': {'metrics_agent': {'vq_id': q['vq_id']}}}}
                 for q in objects]
        links = [{'from': {'kind': 'sql_query', 'name': 'query-' + c['derived_from'][0].split('-')[1]},
                  'to': {'kind': 'metric', 'name': c['name']}, 'rel_type': 'source_of'} for c in cards]
        if dependent == 'card':
            metrics[1]['definition']['depends_on'] = ['Metric 7']
        elif dependent:
            links.append({'from': {'kind': 'metric', 'name': 'Metric 8'},
                          'to': {'kind': 'metric', 'name': 'Metric 7'}, 'rel_type': 'depends_on'})
        documents = {'metric_cards.json': {'metrics': cards, 'spec_version': '1.0-draft.1'},
                     'verified_queries.json': {'objects': objects, 'dialect': 'duckdb'}, 'metric_relationships.json': {},
                     'run_inputs.json': {'scope': 'example'}, 'verify_report.json': report(0),
                     'question_units.json': {'units': [
                         {'unit_id': f'unit-{i}', 'parameters': [{'kind': 'member', 'column': 'member_key'}] if 1 <= i <= 6 else [],
                          'filters': [], 'group_by': [], 'warehouse_tables': ['demo.main.items']} for i in range(18)]}}
        for name, value in documents.items():
            (run / name).write_text(json.dumps(value))
        receipt = {'commit': 'synthetic', 'outputs': {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                   for name in documents if name != 'run_inputs.json'}}
        agent = types.ModuleType('metrics_agent')
        agent.sockets = types.SimpleNamespace(Bundle=lambda *args: None)
        lib = types.ModuleType('metrics_agent.lib')
        lib.artifacts = types.SimpleNamespace(read=lambda path, name: json.loads((path / name).read_text()))
        checked = types.SimpleNamespace(validation=lambda *args: {})
        with mock.patch.dict(sys.modules, {'metrics_agent': agent, 'metrics_agent.lib': lib, 'validation': checked}), \
             mock.patch.object(exporter, 'project', return_value=(metrics, saved, links)), \
             mock.patch.object(metrics_mine, 'check_metric', return_value=([], [])), \
             mock.patch.object(metrics_mine, 'check_query', return_value=([], [])):
            result = exporter.export_context(run, context, receipt)
        return result, context

    def test_seven_of_eighteen_queries_are_held_without_reading_descriptions(self):
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.export(Path(temp).resolve())
            self.assertEqual((result['metrics'], result['queries'], len(result['held_queries'])), (4, 11, 7))
            self.assertEqual({r['query_id'] for r in result['held_queries']}, {f'q-{i}' for i in range(7)})
            self.assertTrue(all(r['status'] == 'held' for r in result['held_queries']))
            admitted = json.loads((context / 'queries.json').read_text())
            self.assertIn('query-17', {q['name'] for q in admitted})
            self.assertEqual(holds.enforce_context(context)['queries'], result['held_queries'])
            self.assertNotIn('query-0', {l['from']['name'] for l in json.loads((context / 'links.json').read_text())})

    def test_metrics_and_dependents_of_held_queries_are_excluded(self):
        for dependency in (True, 'card'):
            with self.subTest(dependency=dependency), tempfile.TemporaryDirectory() as temp:
                result, context = self.export(Path(temp).resolve(), dependent=dependency)
                self.assertEqual({m['name'] for m in result['held']}, {'Metric 7', 'Metric 8'})
                self.assertEqual(result['metrics'], 2)
                self.assertIn('q-8', {q['query_id'] for q in result['held_queries']})
                held_names = {'Metric 7', 'Metric 8', *(q['name'] for q in result['held_queries'])}
                self.assertFalse(any(l[e]['name'] in held_names for l in json.loads((context / 'links.json').read_text())
                                     for e in ('from', 'to')))


if __name__ == '__main__':
    unittest.main()
