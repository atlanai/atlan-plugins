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
        'name': 'work-total', 'display_name': 'Work total', 'artifact_status': 'active', 'is_released': True,
        'release': {'release_id': 'rel_example', 'version_id': 'ver_example',
                    'channel': 'latest', 'release_status': 'released', 'is_current': True},
        'metadata': {'definition': {'spec_version': '1.0-draft.1',
            'question': 'What is the total cost?', 'dialect': 'duckdb', 'sql': sql}}}]}


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
        for field in ('release', 'metadata', 'is_released'):
            payload = readbacks(); payload['artifacts'][0].pop(field)
            with self.assertRaises(ValueError): self.run.publication(payload)
        payload = readbacks(); payload['artifacts'][0]['workspace_id'] = 'workspace_other'
        with self.assertRaises(ValueError): self.run.publication(payload)

    def test_retrieval_rejects_drafts_changes_and_wrong_workspace(self):
        self.run.survey(self.estate); self.run.publication(readbacks())
        for change in ({'release': {**readbacks()['artifacts'][0]['release'], 'release_id': 'rel_changed'}},
                       {'withdrawn': True}, {'id': 'sql_query_other'}):
            payload = readbacks(); payload['artifacts'][0].update(change)
            with self.assertRaises(ValueError): self.run.retrieved(payload)
        with self.assertRaises(ValueError): self.run.retrieved(readbacks('SELECT 999'))
        payload = readbacks(); payload['workspace_id'] = 'workspace_other'
        with self.assertRaises(ValueError): self.run.retrieved(payload)
        restarted = Project(self.run.root)
        self.assertNotIn('definition', restarted.current()['publication']['artifacts'][0])
        self.assertEqual(restarted.retrieved(readbacks())['artifacts'][0]['definition']['sql'],
                         readbacks()['artifacts'][0]['metadata']['definition']['sql'])

    def test_summarized_or_unreleased_mcp_definitions_are_refused(self):
        self.run.survey(self.estate)
        payload = readbacks(); artifact = payload['artifacts'][0]
        artifact['definition'] = artifact.pop('metadata')['definition']
        with self.assertRaisesRegex(ValueError, 'complete released MCP'): self.run.publication(payload)
        payload = readbacks(); payload['artifacts'][0]['metadata']['definition'].pop('spec_version')
        with self.assertRaisesRegex(ValueError, 'incomplete'): self.run.publication(payload)
        for field, value in [('is_current', False), ('release_status', 'withdrawn')]:
            payload = readbacks(); payload['artifacts'][0]['release'][field] = value
            with self.assertRaises(ValueError): self.run.publication(payload)

    def test_full_readback_preserves_optional_definition_evidence(self):
        self.run.survey(self.estate)
        payload = readbacks()
        definition = payload['artifacts'][0]['metadata']['definition']
        definition.update(usage_note='Synthetic source evidence', extensions={'example': {'revision': 1}})
        self.run.publication(payload)
        self.assertEqual(self.run.retrieved(payload)['artifacts'][0]['definition'], definition)
        changed = copy.deepcopy(payload)
        changed['artifacts'][0]['metadata']['definition']['extensions']['example']['revision'] = 2
        with self.assertRaisesRegex(ValueError, 'differs'): self.run.retrieved(changed)

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

    def preparation(self):
        data = self.estate / 'work.csv'
        data.write_text('kind,cost\nbuild,20.50\nrework,14.25\n')
        result = self.run.survey(self.estate)
        source = next(s for s in result['sources'] if s['path'] == 'work.csv')
        return {'database': 'prepared.duckdb', 'tables': [
            {'source_id': source['source_id'], 'table': 'site.work'}]}

    def test_cold_csv_preparation_then_read_only_binding(self):
        result = self.run.prepare(self.preparation())
        self.assertEqual(result['tables'][0]['rows'], 2)
        self.assertEqual(result['types'], 'inferred_from_csv')
        self.run.bind(result['database'])
        answer = self.run.execute({'sql': 'SELECT SUM(cost) FROM site.work'})
        self.assertEqual(float(answer['rows'][0][0]), 34.75)
        self.assertEqual((self.estate / 'prepared.duckdb').stat().st_mode & 0o777, 0o600)

    def test_preparation_never_replaces_existing_database(self):
        request = self.preparation()
        request['database'] = self.database.name
        original = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, 'never replaced'):
            self.run.prepare(request)
        self.assertEqual(self.database.read_bytes(), original)

    def test_preparation_rejects_changed_unselected_and_unsafe_inputs(self):
        request = self.preparation()
        for bad in ({'source_id': 'unknown'}, {'table': 'site.work; DROP SCHEMA site'},
                    {'table': 'work'}):
            payload = copy.deepcopy(request); payload['tables'][0].update(bad)
            with self.assertRaises(ValueError): self.run.prepare(payload)
        payload = copy.deepcopy(request); payload['database'] = '../outside.duckdb'
        with self.assertRaises(ValueError): self.run.prepare(payload)
        (self.estate / 'work.csv').write_text('kind,cost\nchanged,99\n')
        with self.assertRaisesRegex(ValueError, 'changed'): self.run.prepare(request)
        self.assertFalse((self.estate / 'prepared.duckdb').exists())

    def test_preparation_rejects_symlinks_and_duplicate_table_names(self):
        request = self.preparation()
        path = self.estate / 'work.csv'
        path.unlink(); path.symlink_to(self.root / 'outside.csv')
        with self.assertRaises(ValueError): self.run.prepare(request)
        path.unlink(); path.write_text('kind,cost\nbuild,20.50\nrework,14.25\n')
        request['tables'].append({**request['tables'][0], 'table': 'SITE.WORK'})
        with self.assertRaisesRegex(ValueError, 'distinct'): self.run.prepare(request)

    def test_preparation_failure_leaves_no_destination(self):
        request = self.preparation()
        with patch('local_prepare.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 60)):
            with self.assertRaisesRegex(ValueError, 'timed out'): self.run.prepare(request)
        self.assertFalse((self.estate / 'prepared.duckdb').exists())
        self.assertFalse(list(self.estate.glob('.atlan-prepare-*')))

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
        self.assertEqual(self.run.execute({'sql': remote['artifacts'][0]['metadata']['definition']['sql']})['rows'], [['34.75']])
        self.run.publication(copy.deepcopy(remote))
        restarted = Project(self.run.root)
        current = restarted.retrieved(copy.deepcopy(remote))
        query = current['artifacts'][0]['definition']['sql']
        self.assertEqual(restarted.execute({'sql': query})['rows'], [['34.75']])
        import duckdb
        with duckdb.connect(str(self.database)) as con:
            con.execute("INSERT INTO site.work VALUES ('build', 10, DATE '2026-01-04')")
        self.assertEqual(restarted.execute({'sql': query})['rows'], [['44.75']])

    def test_answer_checks_release_and_runs_its_sql_in_one_operation(self):
        remote = readbacks("SELECT SUM(cost) AS value FROM site.work WHERE kind={{kind}}")
        remote['artifacts'][0]['metadata']['definition']['parameters'] = [
            {'name': 'kind', 'data_type': 'string', 'description': 'Selected work kind'}]
        self.run.publication(remote)
        request = {'readbacks': remote, 'artifact_id': 'sql_query_example', 'parameters': {'kind': 'rework'}}
        result = self.run.answer(request)
        self.assertEqual(result['rows'], [['14.75']])
        self.assertEqual(result['retrieval_check'], 'passed')
        self.assertIn('$kind', result['sql'])
        self.assertEqual(result['used_release']['release_id'], 'rel_example')
        request['readbacks'] = copy.deepcopy(remote)
        request['readbacks']['artifacts'][0]['metadata']['definition']['sql'] = 'SELECT 999'
        with self.assertRaises(ValueError): self.run.answer(request)
        request['readbacks'] = remote; request['artifact_id'] = 'sql_query_other'
        with self.assertRaises(ValueError): self.run.answer(request)

    def test_answer_does_not_substitute_templates_inside_literals(self):
        remote = readbacks("SELECT '{{not_a_parameter}}' AS value")
        self.run.publication(remote)
        result = self.run.answer({'readbacks': remote, 'artifact_id': 'sql_query_example'})
        self.assertEqual(result['rows'], [['{{not_a_parameter}}']])

    def test_truncation_date_null_and_injection_are_bound(self):
        result = self.run.execute({'sql': 'SELECT day, NULL AS missing FROM site.work ORDER BY day', 'limit': 1})
        self.assertEqual(result['rows'], [['2026-01-01', None]])
        self.assertTrue(result['truncated'])
        result = self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work WHERE kind=$kind',
                                   'parameters': {'kind': "rework' OR 1=1 --"}})
        self.assertEqual(result['rows'], [[0]])

    def test_empty_parameter_population_requires_an_undefined_rate_guard(self):
        sql = 'SELECT COUNT(CASE WHEN cost > 0 THEN 1 END) * 1.0 / COUNT(*) FROM site.work WHERE kind=$kind'
        with self.assertRaisesRegex(local_data.DataError, 'non-finite'):
            self.run.execute({'sql': sql, 'parameters': {'kind': 'missing'}})
        guarded = sql.replace('/ COUNT(*)', '/ NULLIF(COUNT(*), 0)')
        result = self.run.execute({'sql': guarded, 'parameters': {'kind': 'missing'}})
        self.assertEqual(result['rows'], [[None]])

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
                'definition': payload['artifacts'][0]['metadata']['definition']}
        body['definition']['question'] = 'What is cost by work kind?'
        errors, _ = mine.check_query(body)
        self.assertEqual(errors, [])
        self.run.publication(payload)
        retrieved = self.run.retrieved(copy.deepcopy(payload))
        answer = self.run.execute({'sql': retrieved['artifacts'][0]['definition']['sql']})
        self.assertEqual(sorted(answer['rows']), [['build', '20.00'], ['rework', '14.75']])


if __name__ == '__main__':
    unittest.main()


class SQLiteFlow(unittest.TestCase):
    """The standard-library path: no DuckDB, so these always run."""

    def setUp(self):
        LocalProject.setUp(self)
        import sqlite3
        self.database = self.estate / 'site.sqlite'
        with sqlite3.connect(self.database) as con:
            con.execute('CREATE TABLE work (kind TEXT, cost REAL, day TEXT)')
            con.executemany('INSERT INTO work VALUES (?, ?, ?)', [
                ('rework', 12.5, '2026-01-01'), ('build', 20, '2026-01-02'), ('rework', 2.25, '2026-01-03')])
            con.execute('CREATE TABLE other (value INTEGER)')
            con.execute('INSERT INTO other VALUES (99)')
            con.execute('CREATE VIEW hidden AS SELECT * FROM other')
        self.run.survey(self.estate)
        self.run.bind(self.database, ['site.work'], 'site')

    def preparation(self):
        (self.estate / 'work.csv').write_text('kind,cost,day\nbuild,20.50,2026-01-02\nrework,14.25,2026-01-03\n')
        result = self.run.survey(self.estate)
        source = next(s for s in result['sources'] if s['path'] == 'work.csv')
        return {'database': 'prepared.sqlite', 'tables': [
            {'source_id': source['source_id'], 'table': 'site.work'}]}

    def test_binding_attaches_the_file_under_its_schema(self):
        execution = self.run.current()['execution']
        self.assertEqual((execution['kind'], execution['schema'], execution['tables']),
                         ('sqlite', 'site', ['site.work']))

    def test_cold_csv_preparation_then_read_only_binding(self):
        result = self.run.prepare(self.preparation())
        self.assertEqual((result['kind'], result['schema']), ('sqlite', 'site'))
        self.assertEqual(result['tables'][0]['rows'], 2)
        self.assertEqual([c['type'] for c in result['tables'][0]['columns']], ['TEXT', 'REAL', 'TEXT'])
        self.run.bind(result['database'], schema='site')
        answer = self.run.execute({'sql': 'SELECT SUM(cost) FROM site.work'})
        self.assertEqual(answer['rows'], [[34.75]])
        self.assertEqual((self.estate / 'prepared.sqlite').stat().st_mode & 0o777, 0o600)

    def test_preparation_never_replaces_existing_database_or_mixes_schemas(self):
        request = self.preparation()
        request['database'] = self.database.name
        original = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, 'never replaced'):
            self.run.prepare(request)
        self.assertEqual(self.database.read_bytes(), original)
        request = self.preparation()
        request['tables'].append({**request['tables'][0], 'table': 'other.work'})
        with self.assertRaisesRegex(ValueError, 'one schema'):
            self.run.prepare(request)
        self.assertFalse((self.estate / 'prepared.sqlite').exists())

    def test_native_dax_binding_executes_exact_value(self):
        mapping = {'semantic_table': 'WORK', 'relation': ['site', 'work'],
                   'rowset_equivalent': True, 'security_preserved': True,
                   'columns': [{'semantic': 'cost', 'physical': 'cost', 'datatype': 'decimal'},
                               {'semantic': 'kind', 'physical': 'kind', 'datatype': 'string'}]}
        query = translate('CALCULATE(SUM(WORK[cost]), WORK[kind] = "rework")', mapping, {}, 'sqlite')
        self.assertEqual(query['parameters'], {'p0': 'rework'})
        self.assertEqual(self.run.execute(query)['rows'], [[14.75]])

    def test_publish_readback_restart_retrieve_fresh_answer(self):
        import sqlite3
        remote = readbacks("SELECT SUM(cost) AS value FROM site.work WHERE kind={{kind}}")
        definition = remote['artifacts'][0]['metadata']['definition']
        definition['dialect'] = 'sqlite'
        definition['parameters'] = [{'name': 'kind', 'data_type': 'string', 'description': 'Work kind'}]
        self.run.publication(copy.deepcopy(remote))
        restarted = Project(self.run.root)
        request = {'readbacks': remote, 'artifact_id': 'sql_query_example', 'parameters': {'kind': 'rework'}}
        result = restarted.answer(request)
        self.assertEqual(result['rows'], [[14.75]])
        self.assertIn(':kind', result['sql'])
        with sqlite3.connect(self.database) as con:
            con.execute("INSERT INTO work VALUES ('rework', 10, '2026-01-04')")
        self.assertEqual(restarted.answer(request)['rows'], [[24.75]])

    def test_duckdb_release_is_refused_on_a_sqlite_binding(self):
        remote = readbacks()
        self.run.publication(copy.deepcopy(remote))
        with self.assertRaisesRegex(ValueError, 'dialect'):
            self.run.answer({'readbacks': remote, 'artifact_id': 'sql_query_example'})

    def test_injection_and_null_are_bound(self):
        result = self.run.execute({'sql': 'SELECT day, NULL AS missing FROM site.work ORDER BY day', 'limit': 1})
        self.assertEqual(result['rows'], [['2026-01-01', None]])
        self.assertTrue(result['truncated'])
        result = self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work WHERE kind=:kind',
                                   'parameters': {'kind': "rework' OR 1=1 --"}})
        self.assertEqual(result['rows'], [[0]])
        with self.assertRaisesRegex(local_data.DataError, 'parameters'):
            self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work WHERE kind=:kind'})

    def test_like_and_sqlite_date_functions_are_allowed(self):
        result = self.run.execute({'sql': "SELECT COUNT(*) FROM site.work WHERE kind LIKE '%work%'"})
        self.assertEqual(result['rows'], [[2]])
        result = self.run.execute({'sql': "SELECT CAST(julianday(MAX(day)) - julianday(MIN(day)) AS INTEGER), "
                                          "date(MIN(day)), iif(COUNT(*) > 2, 'many', 'few') FROM site.work"})
        self.assertEqual(result['rows'], [[2, '2026-01-01', 'many']])

    def test_forbidden_queries_leave_database_unchanged(self):
        for query in ('DELETE FROM site.work', 'SELECT 1 FROM site.work; SELECT 2 FROM site.work',
                      'SELECT * FROM site.other', 'SELECT * FROM site.hidden',
                      'SELECT * FROM site.sqlite_master', 'SELECT * FROM pragma_database_list',
                      "ATTACH DATABASE '/tmp/x.db' AS x", "SELECT load_extension('x') FROM site.work",
                      'SELECT random() FROM site.work'):
            with self.subTest(query=query), self.assertRaises(local_data.DataError):
                self.run.execute({'sql': query})
        self.assertEqual(self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work'})['rows'], [[3]])

    def test_schema_change_and_missing_database_do_not_create_or_rebuild(self):
        import sqlite3
        with sqlite3.connect(self.database) as con:
            con.execute('ALTER TABLE work ADD COLUMN extra INTEGER')
        with self.assertRaisesRegex(local_data.DataError, 'schema changed'):
            self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work'})
        missing = self.estate / 'missing.sqlite'
        with self.assertRaises(ValueError): self.run.bind(missing)
        self.assertFalse(missing.exists())
