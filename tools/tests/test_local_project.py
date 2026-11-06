"""Project setup, source preservation and real read-only DuckDB query contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
from project import Project
import local_data
import metrics_mine as mine
from trident.powerbi_file import inspect_file, measure_type
from trident.dax_sql import translate

HAS_DUCKDB = importlib.util.find_spec('duckdb') is not None


def model():
    return {'model': {'tables': [{'name': 'WORK', 'columns': [
        {'name': 'cost', 'dataType': 'decimal'}, {'name': 'kind', 'dataType': 'string'}],
        'measures': [{'name': 'Cost', 'expression': 'SUM(WORK[cost])'},
                     {'name': 'Rows', 'expression': 'COUNTROWS(WORK)'},
                     {'name': 'Rework', 'expression': 'CALCULATE(SUM(WORK[cost]), WORK[kind] = "rework")'},
                     {'name': 'Unknown', 'expression': 'MEDIANX(WORK, WORK[cost])', 'formatString': '$0.00'}],
        'partitions': [{'name': 'rows', 'mode': 'import',
                        'source': {'type': 'm', 'expression': 'let Source = "source evidence" in Source'}}]}]}}


def readbacks(sql='SELECT SUM(cost) AS value FROM site.work'):
    return {'workspace_id': 'workspace_example', 'artifacts': [{
        'workspace_id': 'workspace_example', 'id': 'sql_query_example', 'kind': 'sql_query',
        'name': 'work-total', 'release_id': 'rel_example', 'version_id': 'ver_example',
        'channel': 'latest', 'definition': {'spec_version': '1.0-draft.1',
            'question': 'What is the total cost?', 'dialect': 'duckdb', 'sql': sql}}]}


class LocalProject(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.estate = self.root / 'estate'; self.estate.mkdir()
        self.run = Project(self.estate / '.atlan-metrics' / 'example')

    def seed(self):
        (self.estate / 'model.bim').write_text(json.dumps(model()))
        with zipfile.ZipFile(self.estate / 'report.pbit', 'w') as z:
            z.writestr('DataModelSchema', json.dumps(model()))
            z.writestr('Report/Layout', json.dumps({'sections': [{'name': 'page'}]}))
        history = self.estate / 'history'; history.mkdir()
        (history / 'queries.jsonl').write_text('\n'.join(json.dumps({
            'sql': q, 'exec': 4, 'users': 2}) for q in (
                'SELECT SUM(cost) FROM site.work',
                "SELECT kind, SUM(cost) FROM site.work GROUP BY kind")))

    def test_combined_survey_pairs_sidecar_and_preserves_unresolved_measures(self):
        self.seed()
        (self.estate / '.env').write_text('must not read')
        (self.estate / 'credentials.json').write_text('must not read')
        (self.estate / 'linked.bim').symlink_to(self.estate / 'model.bim')
        (self.estate / 'load.py').write_text('raise RuntimeError("must not execute")')
        with patch('trident.powerbi_file.RUNTIME', self.root / 'missing-runtime'):
            result = self.run.survey(self.estate)
        self.assertEqual(len(result['sources']), 3)
        bim = next(s for s in result['sources'] if s['path'] == 'model.bim')
        self.assertEqual(bim['status'], 'model_sidecar')
        pbit = next(s for s in result['sources'] if s['path'] == 'report.pbit')
        inv = json.loads((self.run.context / (pbit['source_id'] + '.json')).read_text())
        self.assertEqual(len(inv['partitions']), 1)
        self.assertEqual(inv['partitions'][0]['table'], 'WORK')
        self.assertEqual(len(inv['power_query']), 1)
        self.assertEqual([m['resolved_datatype'] for m in inv['measures']],
                         ['decimal', 'integer', 'decimal', None])

    def test_jsonl_intake_and_mining_do_not_lose_rows(self):
        self.seed(); self.run.survey(self.estate)
        result = self.run.history()
        self.assertEqual(result['intake']['rows_read'], 2)
        self.assertEqual(result['shapes']['written'], 2)
        self.assertEqual(mine._records({'SQL': 'SELECT 1', 'EXEC': 2})[0]['EXEC'], 2)
        self.assertEqual(mine._records({'content': [{'type': 'text', 'text': '{"sql":"SELECT 2"}'}]})[0]['sql'], 'SELECT 2')

    def test_history_only_and_model_only_need_no_other_source(self):
        (self.estate / 'model.bim').write_text(json.dumps(model()))
        self.run.survey(self.estate)
        self.assertEqual(self.run.history()['status'], 'not_available')
        (self.estate / 'querylog.jsonl').write_text('{"sql":"SELECT COUNT(*) FROM site.work"}\n')
        self.run.survey(self.estate)
        self.assertEqual(self.run.history()['intake']['rows_read'], 1)

    def test_changed_source_invalidates_publication(self):
        self.seed(); self.run.survey(self.estate); self.run.publication(readbacks())
        p = self.estate / 'history/queries.jsonl'
        p.write_text(p.read_text() + '\n{"sql":"SELECT 2"}')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.run.history()
        self.assertTrue(self.run.survey(self.estate)['context_changed'])
        self.assertNotIn('publication', self.run.current())

    def test_release_receipt_requires_complete_correct_workspace_readbacks(self):
        self.run.survey(self.estate)
        for field in ('release_id', 'version_id', 'definition'):
            payload = readbacks(); payload['artifacts'][0].pop(field)
            with self.assertRaises(ValueError): self.run.publication(payload)
        payload = readbacks(); payload['artifacts'][0]['workspace_id'] = 'workspace_other'
        with self.assertRaises(ValueError): self.run.publication(payload)

    def test_retrieval_rejects_drafts_changes_and_wrong_workspace(self):
        self.run.survey(self.estate); self.run.publication(readbacks())
        for change in ({'release_id': 'rel_changed'}, {'withdrawn': True}, {'id': 'sql_query_other'}):
            payload = readbacks(); payload['artifacts'][0].update(change)
            with self.assertRaises(ValueError): self.run.retrieved(payload)
        with self.assertRaises(ValueError): self.run.retrieved(readbacks('SELECT 999'))
        payload = readbacks(); payload['workspace_id'] = 'workspace_other'
        with self.assertRaises(ValueError): self.run.retrieved(payload)
        restarted = Project(self.run.root)
        self.assertNotIn('definition', restarted.current()['publication']['artifacts'][0])
        self.assertEqual(restarted.retrieved(readbacks())['artifacts'][0]['definition']['sql'],
                         readbacks()['artifacts'][0]['definition']['sql'])

    def test_changed_root_and_outside_database_are_refused(self):
        self.run.survey(self.estate)
        with self.assertRaises(ValueError): self.run.survey(self.root)
        with self.assertRaises(ValueError): self.run.bind(self.root / 'elsewhere.duckdb')

    def test_context_symlinks_cannot_redirect_outputs(self):
        outside = self.root / 'outside'; outside.mkdir()
        self.run.context.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): self.run.survey(self.estate)
        self.assertEqual(list(outside.iterdir()), [])

    def test_cli_survey_works_with_isolated_python(self):
        self.seed()
        result = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'),
                                 '--run-dir', str(self.run.root), 'survey', str(self.estate)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'ok')

    def test_function_guard_blocks_dynamic_external_and_quoted_names(self):
        for q in ("SELECT * FROM query('select 1')", "SELECT * FROM read_csv('file.csv')",
                  'SELECT * FROM "query_table"($name)', 'SELECT custom_macro(cost) FROM site.work'):
            with self.assertRaises(local_data.DataError): local_data.check_functions(q)
        local_data.check_functions("SELECT SUM(cost), 'read_csv(' FROM site.work WHERE kind IN ('a')")


@unittest.skipUnless(HAS_DUCKDB, 'Use an existing approved DuckDB runtime for integration tests.')
class DuckDBFlow(unittest.TestCase):
    def setUp(self):
        LocalProject.setUp(self)
        import duckdb
        self.database = self.estate / 'warehouse.duckdb'
        with duckdb.connect(str(self.database)) as con:
            con.execute('CREATE SCHEMA site')
            con.execute('CREATE TABLE site.work (kind VARCHAR, cost DECIMAL(12,2), day DATE)')
            con.execute("INSERT INTO site.work VALUES ('rework', 12.50, DATE '2026-01-01'), ('build', 20, DATE '2026-01-02'), ('rework', 2.25, DATE '2026-01-03')")
            con.execute('CREATE TABLE site.other AS SELECT 99 AS value')
            con.execute('CREATE VIEW site.hidden AS SELECT * FROM site.other')
            con.execute('CREATE MACRO secret_total() AS (SELECT SUM(value) FROM site.other)')
        self.run.survey(self.estate)
        self.run.bind(self.database, ['site.work'])

    def test_native_dax_binding_executes_exact_value(self):
        mapping = {'semantic_table': 'WORK', 'relation': ['site', 'work'],
                   'rowset_equivalent': True, 'security_preserved': True,
                   'columns': [{'semantic': 'cost', 'physical': 'cost', 'datatype': 'decimal'},
                               {'semantic': 'kind', 'physical': 'kind', 'datatype': 'string'}]}
        query = translate('CALCULATE(SUM(WORK[cost]), WORK[kind] = "rework")', mapping, {}, 'duckdb')
        self.assertEqual(query['binding_style'], 'named_dollar')
        self.assertEqual(query['parameters'], {'p0': 'rework'})
        answer = self.run.execute(query)
        self.assertEqual(answer['rows'], [['14.75']])
        self.assertTrue(answer['columns'][0]['type'].startswith('DECIMAL'))

    def test_publish_readback_restart_retrieve_fresh_answer(self):
        # The Registry transport is a test double; DuckDB execution is real.
        remote = readbacks()
        self.assertEqual(self.run.execute({'sql': remote['artifacts'][0]['definition']['sql']})['rows'], [['34.75']])
        self.run.publication(copy.deepcopy(remote))
        restarted = Project(self.run.root)
        current = restarted.retrieved(copy.deepcopy(remote))
        query = current['artifacts'][0]['definition']['sql']
        self.assertEqual(restarted.execute({'sql': query})['rows'], [['34.75']])
        import duckdb
        with duckdb.connect(str(self.database)) as con:
            con.execute("INSERT INTO site.work VALUES ('build', 10, DATE '2026-01-04')")
        self.assertEqual(restarted.execute({'sql': query})['rows'], [['44.75']])

    def test_truncation_date_null_and_injection_are_bound(self):
        result = self.run.execute({'sql': 'SELECT day, NULL AS missing FROM site.work ORDER BY day', 'limit': 1})
        self.assertEqual(result['rows'], [['2026-01-01', None]])
        self.assertTrue(result['truncated'])
        result = self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work WHERE kind=$kind',
                                   'parameters': {'kind': "rework' OR 1=1 --"}})
        self.assertEqual(result['rows'], [[0]])

    def test_forbidden_queries_leave_database_unchanged(self):
        for query in ('DELETE FROM site.work', 'SELECT 1; SELECT 2', 'SELECT * FROM site.other',
                      'SELECT * FROM site.hidden', 'SELECT * FROM work',
                      "SELECT * FROM read_csv('/etc/passwd')", "SELECT * FROM query('SELECT * FROM site.other')",
                      'SELECT secret_total()', 'SELECT * FROM duckdb_secrets()'):
            with self.subTest(query=query), self.assertRaises(local_data.DataError):
                self.run.execute({'sql': query})
        self.assertEqual(self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work'})['rows'], [[3]])

    def test_schema_change_and_missing_database_do_not_create_or_rebuild(self):
        import duckdb
        with duckdb.connect(str(self.database)) as con:
            con.execute('ALTER TABLE site.work ADD COLUMN extra INTEGER')
        with self.assertRaisesRegex(local_data.DataError, 'schema changed'):
            self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work'})
        missing = self.estate / 'missing.duckdb'
        with self.assertRaises(ValueError): self.run.bind(missing)
        self.assertFalse(missing.exists())

    def test_rebinding_scope_invalidates_previous_publication(self):
        self.run.publication(readbacks())
        self.run.bind(self.database, ['site.work', 'site.other'])
        self.assertNotIn('publication', self.run.current())

    def test_timeout_is_bounded_and_next_query_still_works(self):
        import subprocess
        binding = self.run.current()['execution']
        with patch('local_data.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            with self.assertRaisesRegex(local_data.DataError, 'timed out'):
                local_data.call(binding, 'execute', 'SELECT 1', timeout=1)
        self.assertEqual(self.run.execute({'sql': 'SELECT 1'})['rows'], [[1]])

    def test_history_query_body_passes_existing_registry_contract(self):
        payload = readbacks('SELECT kind, SUM(cost) AS cost FROM site.work GROUP BY kind')
        body = {'name': 'cost-breakdown', 'display_name': 'Cost breakdown',
                'definition': payload['artifacts'][0]['definition']}
        body['definition']['question'] = 'What is cost by work kind?'
        errors, _ = mine.check_query(body)
        self.assertEqual(errors, [])
        self.run.publication(payload)
        retrieved = self.run.retrieved(copy.deepcopy(payload))
        answer = self.run.execute({'sql': retrieved['artifacts'][0]['definition']['sql']})
        self.assertEqual(sorted(answer['rows']), [['build', '20.00'], ['rework', '14.75']])


if __name__ == '__main__':
    unittest.main()
