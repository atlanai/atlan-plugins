"""Synthetic metric-module contracts and optional installed-runtime acceptance tests."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics'))
from modules.metrics import driver
from modules.metrics.validation import assess

# The worker imports siblings only inside its isolated runtime. Load the pure projection
# helper here without changing the engine's import path or requiring its dependencies.
spec = importlib.util.spec_from_file_location('metrics_projection', driver.HERE / 'export.py')
projection = importlib.util.module_from_spec(spec)
with mock.patch.dict(sys.modules, {'driver': driver}):
    spec.loader.exec_module(projection)


def card():
    return {'metric_ref': 'revenue', 'name': 'Revenue', 'display_name': 'Revenue',
            'description': 'The total amount earned from orders.', 'formula': 'SUM(demo.main.orders.amount)',
            'sql': None, 'entity': 'demo.main.orders', 'alias': 'revenue', 'dialect': 'duckdb',
            'source_id': 'metric:example-domain:revenue', 'derived_from': ['h-123'],
            'aggregation': {'type': 'sum', 'reaggregation': 'sum'},
            'output': {'alias': 'revenue', 'datatype': 'decimal', 'unit': 'USD'},
            'population': 'All orders', 'grain': 'demo.main.orders.id',
            'mandatory_filters': ["demo.main.orders.status = 'paid'"],
            'filters': ['demo.main.orders.order_date >= {{start_date}}'],
            'parameters': [{'name': 'start_date', 'datatype': 'date', 'default': '2026-01-01',
                            'description': 'Start of the reporting window', 'observed_in': ['h-123']}],
            'joins': [], 'dimensions': [{'name': 'Order Date', 'kind': 'time',
                        'sql': 'demo.main.orders.order_date', 'datatype': 'date', '_roles': ['grouped']}],
            'examples': [{'source': 'verified_query:h-123', 'sql': 'SELECT 1', 'question': 'Example?',
                          'group_by': ['Order Date']}],
            'quality': {'grade': 'exact', 'reason': 'The source formula was preserved.'}}


class InputStagingTests(unittest.TestCase):
    def worker(self):
        spec = importlib.util.spec_from_file_location('metrics_worker_test', driver.HERE / 'worker.py')
        worker = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {'driver': driver}):
            spec.loader.exec_module(worker)
        return worker

    def test_capture_is_immutable_when_files_are_added_removed_or_changed(self):
        worker = self.worker()
        for change in ('add', 'remove', 'change', 'extra_capture'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                source, capture = root / 'source', root / 'capture'
                source.mkdir()
                asset = source / 'model.tmdl'
                asset.write_text('model Synthetic')
                worker.stage_input(source, capture)
                worker.stage_input(source, capture)
                if change == 'add':
                    (source / 'new.tmdl').write_text('model New')
                elif change == 'remove':
                    asset.unlink()
                    (source / 'other.tmdl').write_text('model Other')
                elif change == 'change':
                    asset.write_text('model Changed')
                else:
                    (capture / 'unexpected.tmdl').write_text('model Extra')
                with self.assertRaisesRegex(ValueError, 'input changed'):
                    worker.stage_input(source, capture)

    def test_stable_asset_identity_ignores_download_suffix_and_preserves_custody(self):
        worker = self.worker()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            first, again = root / 'Report.pbix', root / 'Report (3).pbix'
            for path in (first, again):
                path.write_bytes(b'synthetic-asset')
                path.with_name(path.name + '.manifest.json').write_text('{"custody":"customer"}')
            a = worker.stage_input(first, root / 'capture-a', 'bi:workspace:report-a')
            b = worker.stage_input(again, root / 'capture-b', 'bi:workspace:report-a')
            self.assertEqual(Path(a['path']).name, Path(b['path']).name)
            self.assertTrue(Path(a['path'] + '.manifest.json').is_file())
            self.assertEqual(worker.stage_input(again, root / 'capture-a', 'bi:workspace:report-a')['path'], a['path'])
            c = worker.stage_input(again, root / 'capture-c', 'bi:workspace:report-b')
            self.assertNotEqual(Path(a['path']).name, Path(c['path']).name)
            with self.assertRaisesRegex(ValueError, 'same selected source'):
                worker.stage_input(again, root / 'capture-a', 'bi:workspace:report-b')
            again.write_bytes(b'updated-asset')
            d = worker.stage_input(again, root / 'capture-d', 'bi:workspace:report-a')
            self.assertEqual(Path(a['path']).name, Path(d['path']).name)
            with self.assertRaisesRegex(ValueError, 'input changed'):
                worker.stage_input(again, root / 'capture-a', 'bi:workspace:report-a')

    def test_capture_refuses_links_and_credentials_before_copying(self):
        worker = self.worker()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / 'source'
            source.mkdir()
            (source / 'model.tmdl').write_text('model Synthetic')
            (source / '.env').write_text('SYNTHETIC_ONLY')
            with self.assertRaisesRegex(ValueError, 'credential material'):
                worker.stage_input(source, root / 'capture')
            self.assertFalse((root / 'capture').exists())
            (source / '.env').unlink()
            (source / 'linked.tmdl').symlink_to(source / 'model.tmdl')
            with self.assertRaisesRegex(ValueError, 'symbolic links'):
                worker.stage_input(source, root / 'capture')
            self.assertFalse((root / 'capture').exists())


class SnapshotTests(unittest.TestCase):
    def test_concurrent_runtime_operations_refuse_and_resume_after_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with driver.operation_lock(root):
                with self.assertRaisesRegex(ValueError, 'Another metrics operation'):
                    with driver.operation_lock(root):
                        self.fail('Concurrent operation acquired the runtime lock.')
            with driver.operation_lock(root):
                pass

    def test_incomplete_skill_only_install_reports_missing_plugin_module(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / 'skills/atlan-analytics/scripts/analytics_agent.py'
            script.parent.mkdir(parents=True)
            script.write_text((SCRIPTS / 'analytics_agent.py').read_text())
            process = subprocess.run([sys.executable, str(script), 'metrics', '--help'],
                                     text=True, capture_output=True)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(json.loads(process.stdout)['status'], 'BLOCKED')
            self.assertNotIn('Traceback', process.stderr)

    def test_snapshot_has_real_entry_points_and_no_registry_transport(self):
        data, sha = driver.snapshot()
        self.assertEqual(data['commit'], sha)
        self.assertEqual(len(sha), 64)
        self.assertIn('[project.entry-points."metrics_agent.plugs"]', data['files']['pyproject.toml']['text'])
        self.assertTrue(any('compiler_corpus/' in name for name in data['files']))
        for gate in data['gate_skills']:
            self.assertIn(f'skills/{gate}/SKILL.md', data['files'])
        self.assertFalse(any('metrics-agent-atlan/' in name or name.startswith(('internal/', 'tests/'))
                             for name in data['files']))

    def test_editable_source_changes_runtime_identity(self):
        data, sha = driver.snapshot()
        with tempfile.TemporaryDirectory() as temp:
            engine = Path(temp).resolve()
            for name, item in data['files'].items():
                path = engine / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(item['text'])
            with mock.patch.object(driver, 'ENGINE', engine):
                _, before = driver.snapshot()
                generated = engine / '.venv/bin/python'
                generated.parent.mkdir(parents=True)
                generated.write_bytes(b'\x94\x00')
                self.assertEqual(driver.snapshot()[1], before)
                target = engine / 'src/metrics_agent/cli.py'
                target.write_text(target.read_text() + '\n# A normal owned-source edit.\n')
                _, after = driver.snapshot()
            self.assertEqual(before, sha)
            self.assertNotEqual(before, after)

    def test_unpack_is_repeatable_and_refuses_edited_runtime_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            driver.unpack(root)
            driver.unpack(root)
            (root / 'pyproject.toml').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                driver.unpack(root)

class ProjectionTests(unittest.TestCase):
    def test_parity_requires_same_scope_complete_results_and_matching_sql(self):
        plan = {'sql': 'SELECT 30 AS revenue', 'sql_sha256': 'query-digest', 'parameters': {}}
        scope = {'filters': {}, 'security': 'same-viewer', 'cutoff': '2026-01-01', 'parameters': {}}
        check = {'sql_sha256': 'query-digest', 'reference_scope': scope, 'warehouse_scope': scope,
                 'reference': {'columns': ['revenue'], 'rows': [[30]]},
                 'actual': {'columns': ['revenue'], 'rows': [[30]]}}
        self.assertEqual(assess(plan, check)['parity'], 'pass')
        mismatch = copy.deepcopy(check)
        mismatch['actual']['rows'] = [[40]]
        self.assertEqual(assess(plan, mismatch)['parity'], 'failed')
        mismatch['warehouse_scope'] = {**scope, 'cutoff': '2026-01-02'}
        self.assertEqual(assess(plan, mismatch)['parity'], 'unmeasured')
        truncated = copy.deepcopy(check)
        truncated['actual']['has_more'] = True
        self.assertEqual(assess(plan, truncated)['parity'], 'unmeasured')
        with self.assertRaisesRegex(ValueError, 'different metric SQL'):
            assess(plan, {**check, 'sql_sha256': 'old-query'})
        self.assertNotIn('rows', json.dumps(assess(plan, check)))

    def test_execution_fields_and_source_identity_survive_without_reauthoring(self):
        original = card()
        body = projection.metric_body(original, {'layer_sha': 'layer-123'}, 'commit')
        self.assertEqual(body['source_id'], original['source_id'])
        self.assertEqual(body['name'], original['name'])
        self.assertTrue(body['definition']['provenance']['proposed_by'])
        definition = body['definition']
        for key in ('formula', 'aggregation', 'output', 'mandatory_filters', 'filters', 'grain', 'population'):
            self.assertEqual(definition[key], original[key])
        self.assertEqual(definition['source']['table'], original['entity'])
        self.assertNotIn('sql', definition['source'])
        self.assertEqual(definition['source']['parameters'][0],
                         {'name': 'start_date', 'datatype': 'date', 'default': '2026-01-01'})
        self.assertEqual(definition['extensions']['metrics_agent']['card'], original)
        self.assertEqual(original, card())

    def test_join_type_is_preserved_in_source_sql_and_roles_remain_as_evidence(self):
        original = card()
        original['sql'] = 'demo.main.orders LEFT JOIN demo.main.accounts ON demo.main.orders.account_id = demo.main.accounts.id'
        original['joins'] = [{'name': 'Account', 'to': 'demo.main.accounts',
                             'sql': 'demo.main.orders.account_id = demo.main.accounts.id',
                             'cardinality': 'many_to_one', 'origin': 'Carries the account dimension'}]
        body = projection.metric_body(original, {'layer_sha': 'layer-123'}, 'commit')
        self.assertEqual(body['definition']['source']['sql'], original['sql'])
        self.assertNotIn('table', body['definition']['source'])
        self.assertEqual(body['definition']['joins'], original['joins'])
        self.assertNotIn('_roles', body['definition']['dimensions'][0])
        self.assertEqual(body['definition']['extensions']['metrics_agent']['card']['dimensions'][0]['_roles'], ['grouped'])


RUNTIME = os.environ.get('ANALYTICS_METRICS_RUNTIME')


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME for SQL aggregation and reader checks.')
class CardAggregationAcceptance(unittest.TestCase):
    def test_invalid_nested_aggregates_fail_but_subqueries_and_windows_execute(self):
        program = """
import duckdb
from metrics_agent.lib.card_sql import aggregation_problems
bad = [
    "SELECT COUNT(CASE WHEN COUNT(*) > 0 THEN 1 END) FROM (VALUES (1)) t(x)",
    "SELECT SUM(AVG(x)) FROM (VALUES (1)) t(x)",
    "SELECT SUM(CASE WHEN MAX(x)>0 THEN 1 ELSE 0 END) FROM (VALUES (1)) t(x)",
    "SELECT SUM(x) FROM (VALUES (1)) t(x) WHERE COUNT(*)>0",
    "SELECT SUM(SUM(SUM(x))) OVER () FROM (VALUES (1)) t(x)",
    "SELECT SUM(ROW_NUMBER() OVER ()) FROM (VALUES (1)) t(x)"]
good = [
    "SELECT SUM(CASE WHEN x>0 THEN 1 ELSE 0 END) FROM (VALUES (1)) t(x)",
    "SELECT SUM(x)/NULLIF(COUNT(*),0) FROM (VALUES (1)) t(x)",
    "SELECT SUM(total) FROM (SELECT SUM(x) AS total FROM (VALUES (1)) t(x)) q",
    "SELECT SUM(SUM(x)) OVER () FROM (VALUES (1)) t(x)",
    "SELECT COUNT(*) FROM (VALUES (1)) t(x) WHERE x=(SELECT MAX(y) FROM (VALUES (1)) s(y))",
    "SELECT SUM(x) OVER () FROM (VALUES (1)) t(x)"]
for sql in bad:
    assert aggregation_problems(sql,'duckdb'), sql
for sql in good:
    assert not aggregation_problems(sql,'duckdb'), (sql, aggregation_problems(sql,'duckdb'))
    duckdb.connect().execute(sql).fetchall()
"""
        p = subprocess.run([str(Path(RUNTIME) / 'venv/bin/python'), '-c', program], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_staged_pbix_downloads_produce_identical_reader_unit_ids(self):
        program = """
import json,sys,tempfile,zipfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from modules.metrics.worker import stage_input
from metrics_agent.plugs.readers.pbix import report_usage
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp).resolve()
    layout={'sections':[{'name':'page-key','displayName':'Overview','visualContainers':[
        {'config':json.dumps({'name':'visual-key','singleVisual':{'visualType':'card','title':'Revenue','projections':{}}})}]}]}
    results=[]
    for i,name in enumerate(['Report.pbix','Report (3).pbix']):
        src=root/name
        with zipfile.ZipFile(src,'w') as z:z.writestr('Report/Layout',json.dumps(layout))
        staged=stage_input(src,root/str(i),'bi:workspace:report-a')
        results.append(report_usage(Path(staged['path']),[]))
    assert results[0] and results[0]==results[1],results
"""
        p = subprocess.run([str(Path(RUNTIME) / 'venv/bin/python'), '-c', program,
                            str(ROOT / 'plugins/atlanai-analytics')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME to test SQL scope proofs.')
class RequiredFilterAcceptance(unittest.TestCase):
    def check(self, sql, *, variant=False, filters=None, group=None, parameters=None):
        unit = {'unit_id': 'source-unit', 'warehouse_tables': ['demo.main.items'],
                'parameters': parameters if parameters is not None else [{'kind': 'member', 'name': 'member',
                                              'column': 'member_key', 'table': 'demo.main.items'}],
                'filters': filters or [], 'group_by': group or []}
        if variant:
            unit['variant_of'] = 'original-unit'
        evidence = {'objects': [{'vq_id': 'query-1', 'sql': sql,
                                'provenance': {'lane': 'bi', 'bi': {'unit_id': 'source-unit'}}}],
                    'dialect': 'duckdb'}
        program = ("import json,sys; sys.path.insert(0, sys.argv[1]); "
                   "from modules.metrics.required_filters import required_filter_holds; "
                   "q,u=json.load(sys.stdin); print(json.dumps(required_filter_holds(q,u)))")
        proc = subprocess.run([str(Path(RUNTIME).resolve() / 'venv/bin/python'), '-c', program,
                               str(ROOT / 'plugins/atlanai-analytics')],
                              input=json.dumps([evidence, {'units': [unit]}]), text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def selection(self, value='member_a', **changes):
        return {'column': 'member_key', 'table': 'demo.main.items', 'op': 'eq', 'value': value, **changes}

    def test_missing_member_is_held_even_if_sql_invents_an_uncaptured_selection(self):
        for sql in ("SELECT SUM(amount) FROM demo.main.items", "SELECT SUM(amount) FROM demo.main.items WHERE member_key='member_a'"):
            self.assertEqual(self.check(sql)['query-1']['reason'], 'required_filter_missing')

    def test_captured_selection_must_be_an_effective_predicate_on_the_right_source(self):
        self.assertEqual(self.check("SELECT SUM(amount) FROM demo.main.items WHERE member_key='member_a'", filters=[self.selection()]), {})
        bad = ["SELECT SUM(amount) FROM demo.main.items",
               "SELECT SUM(amount) FROM demo.main.items WHERE member_key='member_b'",
               "SELECT SUM(amount) FROM demo.main.items WHERE member_key='member_a' OR TRUE",
               "SELECT SUM(amount) FROM demo.main.items WHERE member_key=member_key",
               "SELECT SUM(amount) FROM demo.main.items WHERE member_key=:member",
               "SELECT SUM(amount) FROM demo.main.items i JOIN demo.main.other j ON i.id=j.id WHERE j.member_key='member_a'",
               "SELECT SUM(amount) FROM demo.main.items WHERE EXISTS (SELECT 1 FROM demo.main.other WHERE member_key='member_a')",
               "SELECT SUM(amount) FROM demo.main.items WHERE member_key='member_a' UNION ALL SELECT SUM(amount) FROM demo.main.items"]
        for sql in bad:
            with self.subTest(sql=sql):
                self.assertIn('query-1', self.check(sql, filters=[self.selection()]))

    def test_transparent_source_lineage_is_supported_but_computed_columns_are_not_proof(self):
        good = "SELECT SUM(amount) FROM (SELECT member_key AS member, amount FROM demo.main.items) x WHERE x.member='member_a'"
        self.assertEqual(self.check(good, filters=[self.selection()]), {})
        wrong = "SELECT SUM(amount) FROM (SELECT other_key AS member_key, amount FROM demo.main.items) x WHERE member_key='member_a'"
        self.assertIn('query-1', self.check(wrong, filters=[self.selection()]))
        constant = "SELECT SUM(amount) FROM (SELECT 'member_a' AS member_key, amount FROM demo.main.items) x WHERE member_key='member_a'"
        self.assertIn('query-1', self.check(constant, filters=[self.selection()]))

    def test_only_declared_per_member_variants_with_all_actual_group_keys_are_eligible(self):
        sql = 'SELECT member_key, SUM(amount) FROM demo.main.items GROUP BY member_key'
        self.assertEqual(self.check(sql, variant=True, group=['member_key']), {})
        self.assertEqual(self.check(sql, parameters=[], filters=[self.selection(None, value_kind='absent')]), {})
        self.assertIn('query-1', self.check(sql, group=['member_key']))
        computed = 'SELECT UPPER(member_key), SUM(amount) FROM demo.main.items GROUP BY UPPER(member_key)'
        self.assertIn('query-1', self.check(computed, variant=True, group=['member_key']))
        self.assertIn('query-1', self.check('SELECT SUM(amount) FROM demo.main.items', variant=True, group=['member_key']))
        self.assertIn('query-1', self.check('SELECT other_key, SUM(amount) FROM demo.main.items GROUP BY other_key', variant=True, group=['member_key']))
        both = [{'kind': 'member', 'name': n, 'column': n, 'table': 'demo.main.items'} for n in ('member_key', 'region')]
        self.assertIn('query-1', self.check(sql, variant=True, group=['member_key'], parameters=both))

    def test_in_and_null_selections_preserve_the_captured_population(self):
        clause = "SELECT SUM(amount) FROM demo.main.items WHERE member_key IN ('member_a','member_b')"
        filters = [self.selection(['member_a', 'member_b'], op='in')]
        self.assertEqual(self.check(clause, filters=filters), {})
        self.assertIn('query-1', self.check(clause.replace(",'member_b'", ''), filters=filters))
        self.assertEqual(self.check('SELECT SUM(amount) FROM demo.main.items WHERE member_key IS NULL', filters=[self.selection(None, op='is')]), {})
        self.assertIn('query-1', self.check('SELECT SUM(amount) FROM demo.main.items WHERE member_key = NULL', filters=[self.selection(None, op='is')]))


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME to the isolated pinned runtime for acceptance.')
class RuntimeAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runtime = Path(RUNTIME).resolve()
        self.python = self.runtime / 'venv/bin/python'
        self.run = self.root / 'engine'
        self.context = self.root / 'context'
        self.database = self.root / 'demo.duckdb'
        subprocess.run([str(self.python), '-c',
                        'import duckdb,sys; c=duckdb.connect(sys.argv[1]); c.execute("CREATE TABLE orders(id INTEGER, amount INTEGER)"); c.execute("INSERT INTO orders VALUES (1,10),(2,20)"); c.close()',
                        str(self.database)], check=True, capture_output=True)
        self.history = self.root / 'querylog.jsonl'
        self.history.write_text(json.dumps({'query_text': 'SELECT SUM(demo.main.orders.amount) AS revenue FROM demo.main.orders',
                                          'exec_count': 30, 'user_count': 3}) + '\n')
        self.config = self.root / 'input.json'
        self.config_doc = {'version': 1, 'scope': 'example-domain', 'dialect': 'duckdb', 'mode': 'standalone',
                     'inputs': {'history': ['local-files'], 'schema': ['local-files'], 'bi': [], 'sql_check': ['local-files']},
                     'settings': {'local-files': {'history': str(self.history), 'database': str(self.database),
                                                 'witness_mode': 'execute'}},
                     'tables': ['demo.main.orders'], 'publish': ['files'], 'observers': []}
        self.config.write_text(json.dumps(self.config_doc))

    def command(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPTS / 'analytics_agent.py'), 'metrics',
                               '--runtime', str(self.runtime), *map(str, args)], capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            self.fail(proc.stderr + proc.stdout)
        return proc.returncode, data

    def answer(self, gate):
        prompt = json.loads(Path(gate['prompt']).read_text())
        ident = gate['id']
        if ident == 'm2':
            body = {'queries': [
                {'vq_id': q['vq_id'], 'question': 'What is total revenue from orders?',
                             'name': 'Total Revenue', 'description': 'The total amount earned from orders.'}
                for task in prompt['tasks'] for q in task['queries']]}
        elif ident == 'x1':
            body = {'statements': [{'vq_id': q['vq_id'], 'verdict': 'metrics', 'metrics': [{
                'name': 'Revenue', 'expression': q['aggregates_found'][0]['expression'],
                'entity': 'demo.main.orders', 'aggregation': 'sum',
                'why_a_metric': 'The business manages revenue.', 'definition': 'The total amount earned from orders.',
                'grain': 'demo.main.orders.id', 'unit': 'USD', 'mandatory_filters': [], 'optional_filters': [], 'dimensions': []}]}
                if q['aggregates_found'] else {'vq_id': q['vq_id'], 'verdict': 'none', 'metrics': []}
                for task in prompt['tasks'] for q in task['queries']]}
        elif ident == 'x2':
            proposals = [p for group in prompt['groups'] for p in group['proposals']]
            body = {'metrics': [{'name': 'Revenue', 'definition': 'The total amount earned from orders.',
                'expression': proposals[0]['expression'], 'entity': 'demo.main.orders', 'aggregation': 'sum',
                'grain': 'demo.main.orders.id', 'unit': 'USD', 'mandatory_filters': [], 'optional_filters': [], 'dimensions': [],
                'sources': [{'proposal_id': p['proposal_id'], 'vq_id': p['vq_id'], 'name_as_proposed': p['name']} for p in proposals],
                'merge_reasoning': 'These statements measure the same order revenue.'}]}
        elif ident == 'x3':
            body = {'metrics': [{'name': task['name'], 'description': 'The total amount earned from orders.',
                'domain': 'Commerce', 'formula': 'SUM(demo.main.orders.amount)', 'population': 'All orders',
                'grain': 'demo.main.orders.id', 'exclusions': [], 'mandatory_filters': [], 'filters': [],
                'aggregation': {'type': 'sum', 'reaggregation': 'sum'},
                'output': {'alias': 'revenue', 'datatype': 'integer', 'unit': 'USD'},
                'source': {'entity': 'demo.main.orders', 'alias': 'Revenue', 'parameters': []},
                'joins': [], 'dimensions': [], 'examples': [{'source': 'verified_query:' + source['vq_id']}
                                                         for source in task['sources'][:1]]}
                for task in prompt['tasks']]}
        elif ident == 'x4':
            body = {'relationships': []}
        else:
            self.fail('Unexpected gate: ' + ident)
        target = Path(gate['answers'].replace('*', 'reply'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body))

    def test_card_gate_rejects_nested_aggregate_and_accepts_the_repaired_answer(self):
        code, result = self.command('prepare', '--run', self.run, '--config', self.config)
        self.assertEqual(code, 0, result)
        rejected = False
        for _ in range(9):
            code, result = self.command('advance', '--run', self.run, '--model', 'synthetic-test')
            if code == 0:
                break
            self.assertEqual(code, 3, result)
            gate = result['gate']
            self.answer(gate)
            if gate['id'] == 'x3' and not rejected:
                target = Path(gate['answers'].replace('*', 'reply'))
                body = json.loads(target.read_text())
                body['metrics'][0]['formula'] = 'SUM(CASE WHEN COUNT(demo.main.orders.id)>0 THEN 1 ELSE 0 END)'
                target.write_text(json.dumps(body))
                code, refusal = self.command('advance', '--run', self.run, '--model', 'synthetic-test')
                self.assertEqual(code, 3, refusal)
                self.assertEqual(refusal['gate']['id'], 'x3')
                self.assertIn('nested aggregates', refusal['feedback'])
                self.assertTrue(Path(refusal['gate']['repair']).is_file())
                self.answer(refusal['gate'])
                rejected = True
        self.assertTrue(rejected)
        self.assertEqual(code, 0, result)
        code, exported = self.command('export', '--run', self.run, '--context', self.context)
        self.assertEqual(code, 0, exported)
        self.assertEqual(exported['metrics'], 1)
        # A previously authored invalid layer cannot obtain fresh eligibility at export.
        program = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from export import export_context
run=Path(sys.argv[2])
path=run/'metric_cards.json'
layer=json.loads(path.read_text())
layer['metrics'][0]['formula']='SUM(COUNT(demo.main.orders.id))'
path.write_text(json.dumps(layer))
try:
    export_context(run,run.parent/'invalid-export',json.loads((run/'module.json').read_text()))
except ValueError as exc:
    assert 'aggregation checks' in str(exc),str(exc)
else:
    raise AssertionError('Invalid older card received current eligibility evidence')
'''
        p = subprocess.run([str(self.python), '-c', program, str(driver.HERE), str(self.run)],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.root / 'invalid-export').exists())

    def test_all_gates_resume_export_and_existing_publication_plan(self):
        code, data = self.command('prepare', '--run', self.run, '--config', self.config, '--approval-mode', 'auto')
        self.assertEqual(code, 0, data)
        seen = []
        for _ in range(8):
            code, data = self.command('advance', '--run', self.run, '--model', 'synthetic-test')
            if code == 0:
                break
            self.assertEqual(code, 3, {**data, 'detail': (self.run / 'engine.log').read_text()[-3500:]})
            self.assertIn('gate', data, data)
            self.assertEqual(data['approval_mode'], 'auto')
            self.assertFalse(data['gate']['requires_user_approval'])
            self.assertNotIn(data['gate']['id'], seen, data)
            seen.append(data['gate']['id'])
            self.answer(data['gate'])
        self.assertEqual(data['status'], 'complete', data)
        self.assertEqual(seen, ['m2', 'x1', 'x2', 'x3', 'x4'])
        code, exported = self.command('export', '--run', self.run, '--context', self.context)
        self.assertEqual(code, 0, exported)
        self.assertEqual((exported['metrics'], exported['queries'], exported['links']), (1, 1, 1))
        import dashboard  # Loads the existing publisher's bundled runtime, as its CLI does.
        from dashboard_publish import Publisher
        publisher = Publisher(self.context)
        plan = publisher.plan({'workspace_id': 'workspace_demo', 'complete': True,
                               'metric_schema': {'kind': 'metric', 'definition': {'spec_version': '1.0-draft.4'}},
                               'artifacts': []})
        operation = publisher.next(plan['plan_id'])
        self.assertEqual(operation['name'], 'registry__create_artifact')
        body = json.loads((self.context / 'metrics.json').read_text())[0]
        self.assertEqual(operation['arguments']['metadata']['definition']['formula'], body['definition']['formula'])
        self.assertFalse(exported['remote_writes'])
        self.assertEqual(body['definition']['provenance']['proposed_by'], 'metrics-agent/synthetic-test')
        # Re-export cannot undo identity reconciliation performed by the existing publisher.
        reconciled = [{**body, 'name': 'Existing Revenue'}]
        (self.context / 'metrics.json').write_text(json.dumps(reconciled))
        code, reused = self.command('export', '--run', self.run, '--context', self.context)
        self.assertEqual(code, 0, reused)
        self.assertEqual(json.loads((self.context / 'metrics.json').read_text()), reconciled)
        code, plan = self.command('parity-plan', '--run', self.run)
        self.assertEqual(code, 0, plan)
        parity_file = self.root / 'parity.json'
        scope = {'filters': {}, 'security': 'same-viewer', 'cutoff': '2026-01-01', 'parameters': {}}
        evidence = {'format': 1, 'outputs': plan['outputs'], 'checks': [{
            'source_id': plan['plans'][0]['source_id'], 'sql_sha256': plan['plans'][0]['sql_sha256'],
            'reference_scope': scope, 'warehouse_scope': scope,
            'reference': {'columns': ['revenue'], 'rows': [[30]]},
            'actual': {'columns': ['revenue'], 'rows': [[40]]}}]}
        parity_file.write_text(json.dumps(evidence))
        code, held = self.command('export', '--run', self.run, '--context', self.root / 'failed-parity', '--parity', parity_file)
        self.assertEqual(code, 0, held)
        self.assertEqual((held['metrics'], held['queries'], len(held['held'])), (0, 0, 1))
        # Completed evidence cannot be edited and quietly exported as accepted output.
        path = self.run / 'metric_cards.json'
        path.write_text(path.read_text() + '\n')
        code, blocked = self.command('export', '--run', self.run, '--context', self.root / 'other-context')
        self.assertEqual(code, 2)
        self.assertIn('changed after completion', blocked['reason'])

    def test_no_engine_registry_destination_or_observer_and_no_early_export(self):
        for field, value in (('publish', ['registry-rest']), ('observers', ['atlan-trace'])):
            changed = {**self.config_doc, field: value}
            self.config.write_text(json.dumps(changed))
            code, data = self.command('prepare', '--run', self.run, '--config', self.config)
            self.assertEqual(code, 2)
            self.assertFalse((self.run / 'module.json').exists())
        self.config.write_text(json.dumps(self.config_doc))
        code, data = self.command('prepare', '--run', self.run, '--config', self.config, '--approval-mode', 'ask')
        self.assertEqual(code, 0, data)
        code, data = self.command('export', '--run', self.run, '--context', self.context)
        self.assertEqual(code, 2)
        self.assertFalse(self.context.exists())
        code, data = self.command('prepare', '--run', self.run, '--config', self.config, '--approval-mode', 'auto')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads((self.run / 'module.json').read_text())['approval_mode'], 'ask')

    def test_powerbi_and_tableau_use_the_original_readers_and_compiler(self):
        definitions = {
            'tableau': ('report.twb', '''<workbook><datasources><datasource name="orders">
<connection class="snowflake" dbname="demo" schema="main">
<relation name="orders" table="[demo].[main].[orders]" type="table" /></connection>
<column name="[amount]" caption="Revenue" datatype="integer" role="measure" />
</datasource></datasources><worksheets><worksheet name="Revenue"><table><view>
<datasource-dependencies datasource="orders" /></view><rows></rows>
<cols>[orders].[sum:amount:qk]</cols></table></worksheet></worksheets></workbook>'''),
            'powerbi': ('model.tmdl', '''table Orders
\tcolumn id
\t\tdataType: int64
\t\tsourceColumn: id
\tcolumn amount
\t\tdataType: int64
\t\tsourceColumn: amount
\tmeasure Revenue = SUM(Orders[amount])
\tpartition Orders = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t    Source = Snowflake.Databases("example.snowflakecomputing.com", "EXAMPLE_WH"),
\t\t\t    DB = Source{[Name="demo"]}[Data],
\t\t\t    SCH = DB{[Name="main",Schema="main"]}[Data],
\t\t\t    TBL = SCH{[Name="orders"]}[Data]
\t\t\tin
\t\t\t    TBL
''')}
        for vendor, (filename, source) in definitions.items():
            with self.subTest(vendor=vendor):
                bi = self.root / vendor
                bi.mkdir()
                (bi / filename).write_text(source)
                config = copy.deepcopy(self.config_doc)
                config['inputs']['history'] = []
                config['inputs']['bi'] = ['local-files']
                config['settings']['local-files'].update({'bi': str(bi), 'bi_vendor': vendor})
                self.config.write_text(json.dumps(config))
                run = self.root / (vendor + '-run')
                code, data = self.command('prepare', '--run', run, '--config', self.config)
                self.assertEqual(code, 0, data)
                code, data = self.command('advance', '--run', run, '--model', 'synthetic-test')
                self.assertEqual(code, 3, {**data, 'detail': (run / 'engine.log').read_text()[-2500:]})
                self.assertIn(data.get('gate', {}).get('id'), ('a3b', 'c3'), data)
                candidates = json.loads((run / 'bi_candidates.json').read_text())['candidates']
                self.assertTrue(any('SUM(' in candidate.get('sql', '').upper() for candidate in candidates))

    def test_compiler_review_hold_reaches_export_and_publication_without_prose_filtering(self):
        bi = self.root / 'bi'
        bi.mkdir()
        (bi / 'report.twb').write_text("""<workbook><datasources><datasource name="orders">
<connection class="snowflake" dbname="demo" schema="main">
<relation name="orders" table="[demo].[main].[orders]" type="table" /></connection>
<column name="[amount]" caption="Revenue" datatype="integer" role="measure" />
<column name="[id]" caption="Order" datatype="integer" role="measure" />
</datasource></datasources><worksheets><worksheet name="Revenue"><table><view>
<datasource-dependencies datasource="orders" /></view><rows></rows>
<cols>[orders].[sum:amount:qk]</cols></table></worksheet><worksheet name="Order Detail"><table><view>
<datasource-dependencies datasource="orders" /></view><rows></rows>
<cols>[orders].[sum:id:qk]</cols></table></worksheet></worksheets></workbook>""")
        config = copy.deepcopy(self.config_doc)
        config['inputs']['history'] = []
        config['inputs']['bi'] = ['local-files']
        config['settings']['local-files'].update({'bi': str(bi), 'bi_vendor': 'tableau'})
        self.config.write_text(json.dumps(config))
        code, prepared = self.command('prepare', '--run', self.run, '--config', self.config)
        self.assertEqual(code, 0, prepared)
        held_id = None
        for _ in range(12):
            code, step = self.command('advance', '--run', self.run, '--model', 'synthetic-test')
            if code == 0:
                break
            self.assertEqual(code, 3, step)
            gate = step['gate']
            prompt = json.loads(Path(gate['prompt']).read_text())
            if gate['id'] == 'a3b':
                held_id = next(c['candidate_id'] for c in prompt['compiled'] if 'ID' in c['sql'].upper())
                body = {'findings': [{'candidate_id': held_id, 'severity': 'wrong',
                                     'finding': 'The required source selection is missing.'}], 'rules': []}
            elif gate['id'] == 'c3':
                body = {'items': [{'candidate_id': c['candidate_id'],
                                  'question': 'What does this source report?',
                                  'description': 'A synthetic report selection.', 'gotchas': [],
                                  'aliases': [], 'evidence_citations': []} for c in prompt['items']]}
            elif gate['id'] == 'm2':
                body = {'queries': [{'vq_id': q['vq_id'],
                                     'name': 'Order selection variant' if q['vq_id'] == held_id else 'Total revenue',
                                     'question': 'What does the order selection return?' if q['vq_id'] == held_id else 'What is total revenue?',
                                     'description': 'A synthetic order selection.' if q['vq_id'] == held_id else 'The total order amount.'}
                                    for task in prompt['tasks'] for q in task['queries']]}
            elif gate['id'] == 'x1':
                self.answer(gate)
                target = Path(gate['answers'].replace('*', 'reply'))
                proposals = json.loads(target.read_text())
                for row in proposals['statements']:
                    if row['vq_id'] == held_id:
                        row.update(verdict='none', metrics=[])
                target.write_text(json.dumps(proposals))
                continue
            else:
                self.answer(gate)
                continue
            target = Path(gate['answers'].replace('*', 'reply'))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(body))
        self.assertEqual(step['status'], 'complete', step)
        self.assertIsNotNone(held_id)
        code, result = self.command('export', '--run', self.run, '--context', self.context)
        self.assertEqual(code, 0, result)
        self.assertEqual((result['metrics'], result['queries']), (1, 1))
        self.assertEqual(result['held_queries'][0]['query_id'], held_id)
        self.assertEqual(result['held_queries'][0]['reason'], 'compiler_review_wrong')
        self.assertEqual(result['held_queries'][0]['status'], 'held')
        document = self.context / 'publication-holds.json'
        subprocess.run([str(self.python), '-c',
                        'import json,jsonschema,sys; jsonschema.validate(json.load(open(sys.argv[1])), json.load(open(sys.argv[2])))',
                        str(document), str(driver.HERE / 'schemas/publication-holds.schema.json')],
                       check=True, capture_output=True)
        code, reused = self.command('export', '--run', self.run, '--context', self.context)
        self.assertEqual(code, 0, reused)
        self.assertTrue(reused['reused'])
        # Source-unit requirements are immutable completion evidence too.
        units = self.run / 'question_units.json'
        original_units = units.read_text()
        units.write_text(original_units + '\n')
        code, refused = self.command('export', '--run', self.run, '--context', self.root / 'changed-units')
        self.assertEqual(code, 2, refused)
        self.assertIn('changed after completion', refused['reason'])
        units.write_text(original_units)
        # Changing gate evidence after completion cannot silently remove the publication hold.
        report = self.run / 'verify_report.json'
        report.write_text(report.read_text() + '\n')
        code, refused = self.command('export', '--run', self.run, '--context', self.root / 'other')
        self.assertEqual(code, 2, refused)
        self.assertIn('changed after completion', refused['reason'])

    def test_input_is_staged_once_with_custody_and_reused_from_project(self):
        sidecar = self.history.with_name(self.history.name + '.manifest.json')
        sidecar.write_text(json.dumps({'custody': 'customer'}))
        capture = self.root / 'sources' / 'history'
        code, staged = self.command('stage-input', '--source', self.history, '--destination', capture)
        self.assertEqual(code, 0, staged)
        self.assertEqual(Path(staged['path']).read_bytes(), self.history.read_bytes())
        self.assertTrue((capture / sidecar.name).exists())
        code, same = self.command('stage-input', '--source', self.history, '--destination', capture)
        self.assertEqual(code, 0, same)
        self.history.unlink()
        self.config_doc['settings']['local-files']['history'] = staged['path']
        self.config.write_text(json.dumps(self.config_doc))
        code, prepared = self.command('prepare', '--run', self.run, '--config', self.config)
        self.assertEqual(code, 0, prepared)
        code, advanced = self.command('advance', '--run', self.run, '--model', 'synthetic-test')
        self.assertEqual(code, 3, advanced)
        self.assertEqual(advanced['approval_mode'], 'auto')
        self.assertEqual(advanced['gate']['id'], 'm2')


if __name__ == '__main__':
    unittest.main()
