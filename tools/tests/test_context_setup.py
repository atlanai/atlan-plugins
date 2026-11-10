"""Generic setup acceptance contracts using unrelated synthetic inputs."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import test_local_project as fixtures
SCRIPTS, model, readbacks = fixtures.SCRIPTS, fixtures.model, fixtures.readbacks
from project import Project
from context_state import resolve_run, digest
import acquisition
import metric_preview
import page_scope
import progress
import retrieval


def metric(number=1, history=False):
    return {'name': 'total-' + str(number), 'display_name': 'Total ' + str(number),
            'description': 'Total cost of work.', 'definition': {
                'spec_version': '2.0', 'version': 1, 'dialect': 'duckdb', 'formula': 'SUM(site.work.cost)',
                'aggregation': {'type': 'sum'}, 'output': {'alias': 'value', 'datatype': 'decimal'},
                'source': {'entity': 'site.work', 'table': 'site.work', 'alias': 's'},
                'grain': 'One row per source work entry; key uniqueness is not declared.', 'dimensions': [], 'mandatory_filters': [],
                'extensions': {'atlan_analytics': {'shapes': ['shape-example']} if history else {
                    'declared': {'kind': 'powerbi', 'ref': 'model.bim', 'name': 'Cost'}}}}}


def query(sql='SELECT SUM(cost) FROM site.work'):
    raw = readbacks(sql)['artifacts'][0]
    return {k: raw[k] for k in ('name', 'display_name')} | {'definition': raw['metadata']['definition']}


def report_inventory():
    return {'tables': [{'name': 'WORK', 'columns': [{'name': 'cost'}]}],
            'measures': [{'table': 'WORK', 'name': 'Cost', 'expression': 'SUM(WORK[cost])'},
                         {'table': 'WORK', 'name': 'Twice', 'expression': '[Cost] * 2'},
                         {'table': 'WORK', 'name': 'Unused', 'expression': '1'}],
            'report_filters': ['global'], 'pages': [
                {'name': 'overview', 'displayName': 'Overview', 'filters': ['active'],
                 'visuals': [{'id': 'card', 'type': 'card', 'projections': {'Values': [{'queryRef': 'WORK.Twice'}]},
                              'filters': ['first']}]},
                {'name': 'detail', 'displayName': 'Detail', 'filters': ['closed'],
                 'visuals': [{'type': 'card', 'projections': {'Values': [{'queryRef': 'WORK.Cost'}]}}]},
                {'name': 'tooltip', 'pageType': 'Tooltip', 'visuals': []},
                {'name': 'hidden', 'visibility': 1, 'visuals': []}]}


class GenericSetup(unittest.TestCase):
    setUp = fixtures.LocalProject.setUp

    def seed_candidates(self):
        self.run.survey(self.estate)
        import context_contracts as mine
        cards = [metric(i, i % 2 == 0) for i in range(1, 7)]
        for card in cards:
            card['definition']['spec_version'] = mine.METRIC_SPEC
        self.run.write(self.run.context / 'metrics.json', cards)
        self.run.write(self.run.context / 'queries.json', [query()])
        return cards

    def test_folder_catalog_covers_supported_formats_without_executing_them(self):
        for name in ('model.tmdl', 'report.pbip', 'book.twb', 'source.tds', 'archive.twbx', 'archive.tdsx',
                     'report.html', 'manifest.json', 'model.yml', 'view.lkml'):
            (self.estate / name).write_text('not executable')
        cubes = self.estate / 'model/cubes'; cubes.mkdir(parents=True)
        (cubes / 'orders.js').write_text('throw new Error("must never execute")')
        history = self.estate / 'query_history.jsonl'
        history.write_text(json.dumps({'sql': 'SELECT COUNT(*) FROM site.work'}))
        found = self.run.survey(self.estate)['sources']
        self.assertEqual(len(found), 12)
        self.assertEqual({s['kind'] for s in found}, {'powerbi_project', 'tableau', 'dashboard_html', 'model_candidate', 'query_history'})
        self.assertTrue(all(s['status'] == 'needs_model_review' for s in found if s['kind'] != 'query_history'))

    def test_standalone_file_does_not_survey_siblings_and_reuses_inventory(self):
        file = self.estate / 'model.bim'; file.write_text(json.dumps(model()))
        (self.estate / 'other.sql').write_text('SELECT 999')
        first = self.run.survey(file)
        with patch('project.inspect_file', side_effect=AssertionError('must reuse decoded export')):
            second = self.run.survey(file)
        self.assertEqual(first['sources'], second['sources'])
        self.assertEqual(len(first['sources']), 1)

    def test_layout_only_export_keeps_page_choice_and_holds_missing_model(self):
        path = self.estate / 'report.pbix'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Report/Layout', json.dumps({'sections': [{'name': 'Overview'}]}))
        item = self.run.survey(path)['sources'][0]
        self.assertEqual(item['status'], 'held_missing_model')
        self.assertEqual(item['counts']['pages'], 1)
        selected = self.run.scope({'source_id': item['source_id'], 'pages': ['Overview']})['selection']
        self.assertEqual(selected['pages'], ['Overview'])
        self.assertEqual(selected['coverage'], 'partial')
        self.assertEqual(selected['measure_refs'], [])

    def test_legacy_run_gains_cache_metadata_without_losing_release_receipt(self):
        file = self.estate / 'model.bim'; file.write_text(json.dumps(model()))
        self.run.survey(self.estate); self.run.publication(readbacks())
        state = self.run.current()
        for item in state['sources']: item.pop('inventory_hash', None)
        self.run.write(self.run.path, state)
        result = self.run.survey(self.estate)
        self.assertFalse(result['context_changed'])
        self.assertIn('publication', self.run.current())

    def test_five_preview_is_visible_repeatable_and_does_not_need_workspace(self):
        self.seed_candidates()
        before = {p: p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root),
                                 'preview', '--limit', '5'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Showing 5 of 6 metrics', result.stdout)
        self.assertEqual(result.stdout.count('\n### '), 5)
        self.assertIn('SUM(site.work.cost)', result.stdout)
        self.assertIn('shape-example', result.stdout)
        self.assertIn('model.bim', result.stdout)
        self.assertNotIn('saved queries', result.stdout)
        self.assertEqual(result.stdout.strip(), self.run.preview().strip())
        self.assertEqual(before, {p: p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()})
        self.assertNotIn('workspace_id', self.run.current())

    def test_empty_fewer_and_renderer_failure_always_have_text(self):
        self.run.survey(self.estate)
        self.assertIn('No metrics', self.run.preview())
        self.run.write(self.run.context / 'metrics.json', [metric()])
        self.assertIn('Only 1 metrics', self.run.preview())
        with patch('metric_preview.html_snapshot', side_effect=RuntimeError('broken renderer')):
            result = self.run.preview(format='html')
        self.assertEqual(result['status'], 'text_fallback')
        self.assertIn('SUM(site.work.cost)', result['markdown'])

    def test_chat_preview_renders_both_sources_and_exact_history_sql_without_writes(self):
        cards = self.seed_candidates()
        cards[0]['definition']['mandatory_filters'] = ["s.kind <> 'cancelled'"]
        self.run.write(self.run.context / 'metrics.json', cards)
        bodies = self.run.read(self.run.context / 'queries.json')
        bodies[0]['definition']['extensions'] = {'atlan_analytics': {'shapes': ['shape-example']}}
        self.run.write(self.run.context / 'queries.json', bodies)
        before = {p: p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root),
                                 'preview', '--format', 'chat'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Showing 5 of 6 metrics', result.stdout)
        self.assertIn('| Metric | Meaning | Calculation | Population / filters | Source | Check status |', result.stdout)
        self.assertIn('powerbi: Cost', result.stdout)
        self.assertIn('Query history: 1 pattern', result.stdout)
        self.assertIn("s.kind &lt;&gt; 'cancelled'", result.stdout)
        self.assertIn('```sql\n' + bodies[0]['definition']['sql'] + '\n```', result.stdout)
        self.assertNotIn('result_file', result.stdout)
        self.assertNotIn('workspace_id', self.run.current())
        self.assertEqual(before, {p: p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()})

    def test_chat_preview_handles_empty_query_only_and_untrusted_source_text(self):
        self.run.survey(self.estate)
        self.assertIn('No metrics or queries', self.run.preview(format='chat'))
        body = query()
        body['definition']['question'] = '![preview](https://example.com/track) <script>bad()</script>'
        self.run.write(self.run.context / 'queries.json', [body])
        text = self.run.preview(format='chat')
        self.assertIn('0 metrics and 1 query', text)
        self.assertIn(body['definition']['sql'], text)
        self.assertNotIn('![preview]', text)
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('| Metric |', text)

    def test_large_html_preview_keeps_snapshot_path_and_visible_fallback(self):
        cards = self.seed_candidates()
        for card in cards:
            card['description'] = 'A long source description. ' * 150
        self.run.write(self.run.context / 'metrics.json', cards)
        result = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root),
                                 'preview', '--format', 'html'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        compact = json.loads(result.stdout)
        self.assertLess(len(result.stdout.encode()), 4096)
        self.assertTrue(Path(compact['snapshot']).is_file())
        full = json.loads(Path(compact['result_file']).read_text())
        self.assertEqual(full['snapshot'], compact['snapshot'])
        self.assertIn('SUM(site.work.cost)', full['markdown'])
        self.assertIn('preview --format chat', compact['next'])

    def test_changed_definition_resets_checked_preview_and_progress(self):
        cards = self.seed_candidates()
        result = self.run.check()
        self.assertEqual(result['counts']['checked'], 7)
        cards[0]['definition']['formula'] = 'SUM(site.work.cost) * 2'
        self.run.write(self.run.context / 'metrics.json', cards)
        current = progress.update(self.run, 'preview', 'waiting-for-user', 'select_workspace')
        self.assertEqual(current['counts']['draft'], 1)
        self.assertEqual(current['counts']['checked'], 6)
        self.assertEqual(current['steps_remaining'], 2)
        self.assertIn('Status: Draft', self.run.preview(None))
        restarted = Project(self.run.root)
        self.assertEqual(restarted.read(self.run.root / 'progress.json'), current)

    def test_changed_sources_archive_candidates_and_invalidate_receipts(self):
        self.seed_candidates(); self.run.publication(readbacks())
        (self.estate / 'extra.sql').write_text('SELECT 1')
        self.run.survey(self.estate)
        self.assertNotIn('publication', self.run.current())
        self.assertFalse((self.run.context / 'metrics.json').exists())
        self.assertEqual(len(list((self.run.root / 'previous-context').glob('*/metrics.json'))), 1)
        self.assertIn('No metrics', self.run.preview())

    def test_snapshot_escapes_definitions_and_has_real_local_controls(self):
        self.seed_candidates()
        cards = self.run.read(self.run.context / 'metrics.json')
        cards[0]['display_name'] = '<script>untrusted()</script>'
        self.run.write(self.run.context / 'metrics.json', cards)
        result = self.run.preview(format='html')
        text = Path(result['snapshot']).read_text()
        self.assertIn('<details>', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('<script>', text)
        self.assertNotIn('<button', text)
        self.assertIn('Snapshot', text)

    def test_warehouse_answer_uses_old_connection_record_without_duckdb(self):
        self.run.survey(self.estate)
        self.run.write(self.run.root / 'state.json', {'warehouse': {
            'kind': 'snowflake', 'dialect': 'snowflake', 'tool': 'example_read_query', 'via': 'host'}})
        payload = readbacks('SELECT SUM(cost) FROM site.work WHERE kind={{kind}}')
        payload['artifacts'][0]['metadata']['definition']['dialect'] = 'snowflake'
        self.run.publication(payload)
        with patch('local_data.call', side_effect=AssertionError('must not dispatch DuckDB')):
            plan = self.run.answer({'readbacks': payload, 'artifact_id': 'sql_query_example', 'parameters': {'kind': 'repair'}})
        self.assertEqual(plan['status'], 'execute_in_host')
        self.assertEqual(plan['execution']['tool'], 'example_read_query')
        self.assertIn('{{kind}}', plan['sql'])
        self.assertEqual(plan['used_release']['release_id'], 'rel_example')
        self.assertNotIn('rows', plan)

    def test_generic_retrieval_needs_no_project_receipt_but_refuses_unreleased(self):
        payload = readbacks()
        result = retrieval.query_plan({'readbacks': payload, 'artifact_id': 'sql_query_example'}, {'kind': 'duckdb'})
        self.assertEqual(result['status'], 'ready')
        payload['artifacts'][0]['is_released'] = False
        with self.assertRaises(ValueError):
            retrieval.validate(payload)

    def test_cache_reuses_revision_scope_and_hash_without_storing_signed_url(self):
        download = self.run.root / 'acquisitions/export.pbit'
        download.parent.mkdir()
        download.write_bytes(b'example export')
        request = {'url': 'https://example.com/report?signature=opaque', 'revision': 'v1', 'access_scope': 'approved'}
        self.assertEqual(acquisition.cached(self.run, request)['status'], 'needs_download')
        self.assertEqual(acquisition.cached(self.run, {**request, 'file': str(download)})['status'], 'recorded')
        self.assertEqual(acquisition.cached(self.run, request)['status'], 'reused')
        self.assertEqual(acquisition.cached(self.run, {**request, 'revision': 'v2'})['status'], 'needs_download')
        self.assertEqual(acquisition.cached(self.run, {**request, 'access_scope': 'other'})['status'], 'needs_download')
        self.assertNotIn('signature', ''.join(p.read_text() for p in download.parent.glob('*.json')))
        download.write_bytes(b'changed')
        self.assertEqual(acquisition.cached(self.run, request)['status'], 'needs_download')
        with self.assertRaises(ValueError):
            acquisition.cached(self.run, {**request, 'file': str(self.estate / 'outside.pbit')})

    def test_report_page_changes_reuse_export_but_other_query_urls_do_not_alias(self):
        base = 'https://app.powerbi.com/groups/example/reports/report'
        one = acquisition.source_identity(base + '/Overview')
        two = acquisition.source_identity(base + '?pageName=Detail')
        self.assertEqual(one['source_hash'], two['source_hash'])
        self.assertEqual(two['page_ref'], 'Detail')
        self.assertNotEqual(acquisition.source_identity('https://example.com/export?id=1')['source_hash'],
                            acquisition.source_identity('https://example.com/export?id=2')['source_hash'])

    def test_run_location_prefers_explicit_and_does_not_probe_home(self):
        self.assertEqual(resolve_run(self.estate, self.run.root), self.run.root)
        self.assertEqual(resolve_run(self.estate).parent, self.estate / '.atlan-metrics')
        self.assertEqual(resolve_run(self.estate, cache=self.root / 'permitted').parent, self.root / 'permitted')
        self.assertFalse((self.root / 'permitted').exists())
        self.assertEqual(resolve_run(self.estate, home_permitted=True).parent, Path.home() / '.atlan/analytics/runs')

    def test_local_artifact_paths_cannot_escape_with_parent_segments_or_symlinks(self):
        with self.assertRaises(ValueError):
            self.run.write(self.run.root / '../outside.json', {})
        (self.run.root / 'linked').symlink_to(self.estate, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run.write(self.run.root / 'linked/outside.json', {})
        self.assertFalse((self.estate / 'outside.json').exists())

    def test_compact_worker_output_points_to_private_complete_result(self):
        for i in range(40):
            (self.estate / (str(i) + '.sql')).write_text('SELECT 1')
        result = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root),
                                 'survey', str(self.estate)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.encode()), 4096)
        path = Path(json.loads(result.stdout)['result_file'])
        self.assertEqual(len(json.loads(path.read_text())['sources']), 40)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_interrupted_inspection_resumes_completed_decode(self):
        (self.estate / 'model.bim').write_text(json.dumps(model()))
        update = progress.update
        def interrupt_after_first(run, stage, *args, **kwargs):
            result = update(run, stage, *args, **kwargs)
            if kwargs.get('completed') == 1:
                raise KeyboardInterrupt()
            return result
        with patch('progress.update', side_effect=interrupt_after_first):
            with self.assertRaises(KeyboardInterrupt): self.run.survey(self.estate)
        with patch('project.inspect_file', side_effect=AssertionError('completed decode must be reused')):
            result = Project(self.run.root).survey(self.estate)
        self.assertEqual(result['sources'][0]['status'], 'available')

    def test_page_scope_requires_mapping_before_mining_and_preserves_two_reports(self):
        inventory = report_inventory()
        with patch('project.inspect_file', return_value={**inventory, 'partitions': [],
                   'counts': {'pages': 4, 'tables': 1, 'measures': 3}}):
            for name in ('first.pbit', 'second.pbit'):
                (self.estate / name).write_bytes(b'fixture parsed by test adapter')
            (self.estate / 'query_history.jsonl').write_text('\n'.join(json.dumps({'sql': sql}) for sql in (
                'SELECT SUM(cost) FROM site.work', 'SELECT SUM(cost) FROM other.work')))
            sources = self.run.survey(self.estate)['sources']
        reports = [s for s in sources if s['kind'] == 'powerbi']
        self.run.scope({'source_id': reports[0]['source_id'], 'pages': ['overview']})
        self.assertEqual(self.run.history()['status'], 'needs_table_mapping')
        for report in reports:
            self.run.scope({'source_id': report['source_id'], 'pages': ['overview'], 'history_tables': ['site.work']})
        self.assertEqual(len(self.run.current()['scopes']), 2)
        result = self.run.history()
        self.assertEqual(result['tables'], ['site.work'])
        self.assertEqual(len(Path(result['history']).read_text().splitlines()), 2)
        self.assertFalse((self.run.context / 'shapes.jsonl').exists())

    def test_markdown_cannot_render_source_images_and_keeps_quotes_readable(self):
        self.seed_candidates()
        cards = self.run.read(self.run.context / 'metrics.json')
        cards[0]['description'] = '![private](https://example.com/image) "literal"'
        self.run.write(self.run.context / 'metrics.json', cards)
        output = self.run.preview()
        self.assertIn('\\!\\[private\\]', output)
        self.assertIn('"literal"', output)
        self.assertNotIn('&quot;', output)


class ScopeTests(unittest.TestCase):
    def test_all_keeps_filters_separate_and_supporting_measures_only_once(self):
        result = page_scope.select(report_inventory())
        self.assertEqual(result['pages'], ['overview', 'detail'])
        self.assertEqual(result['supporting_pages'], ['tooltip', 'hidden'])
        self.assertEqual(result['measure_refs'], [['WORK', 'Cost'], ['WORK', 'Twice']])
        self.assertEqual([c['page_filters'] for c in result['contexts']], [['active'], ['closed']])
        self.assertEqual(result['held'], [])

    def test_page_missing_or_unresolved_never_expands_to_entire_model(self):
        inv = report_inventory()
        with self.assertRaises(ValueError): page_scope.select(inv, ['missing'])
        inv['pages'][0]['visuals'][0]['projections'] = {'Values': [{'queryRef': 'Unknown.measure'}]}
        selected = page_scope.select(inv, ['overview'])
        self.assertEqual(selected['measure_refs'], [])
        self.assertEqual(selected['coverage'], 'partial')
        self.assertTrue(selected['held'])

    def test_bim_uses_model_scope_and_hidden_page_can_be_selected(self):
        inv = report_inventory()
        self.assertEqual(page_scope.select(inv, ['hidden'])['pages'], ['hidden'])
        inv['pages'] = []
        self.assertEqual(page_scope.select(inv)['mode'], 'model')
        self.assertEqual(len(page_scope.select(inv)['measure_refs']), 3)
        with self.assertRaises(ValueError): page_scope.select(inv, ['overview'])

    def test_pbir_page_type_and_binding_survive_import_and_scope(self):
        from trident.powerbi_file import inspect_file
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / 'report.pbit'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('DataModelSchema', json.dumps(model()))
                archive.writestr('Report/definition/report.json', '{}')
                for name, extra in [('overview', {}), ('tip', {'type': 'Tooltip'}),
                                    ('drill', {'pageBinding': {'type': 'Drillthrough'}})]:
                    archive.writestr('Report/definition/pages/' + name + '/page.json', json.dumps({'name': name, **extra}))
            inventory = inspect_file(path)
        result = page_scope.select(inventory)
        self.assertEqual(result['pages'], ['overview'])
        self.assertEqual(result['supporting_pages'], ['tip', 'drill'])


@unittest.skipUnless(fixtures.HAS_DUCKDB, 'Use an existing approved DuckDB runtime.')
class SQLRegression(unittest.TestCase):
    setUp = fixtures.DuckDBFlow.setUp

    def test_explicit_database_binding_is_independent_of_source_folder(self):
        selected = self.root / 'selected.duckdb'
        self.database.rename(selected)
        self.run.bind(selected, ['site.work'])
        self.assertEqual(self.run.execute({'sql': 'SELECT SUM(cost) FROM site.work'})['rows'], [['34.75']])

    def test_metric_example_execution_is_revision_bound_and_labeled_precisely(self):
        import context_contracts as mine
        body = metric()
        body['definition']['spec_version'] = mine.METRIC_SPEC
        body['definition']['examples'] = [{'source': 'declared:Cost', 'sql': 'SELECT SUM(cost) FROM site.work'}]
        self.run.write(self.run.context / 'metrics.json', [body])
        result = self.run.check_metric({'name': 'total-1', 'example_index': 0})
        self.assertEqual(result['rows'], [['34.75']])
        self.run.check()
        self.assertEqual(metric_preview.candidates(self.run)[0]['status'], 'example_executed')
        self.assertIn('formula equivalence not established', self.run.preview())
        body['definition']['formula'] = 'SUM(site.work.cost) * 2'
        self.run.write(self.run.context / 'metrics.json', [body])
        self.assertEqual(metric_preview.candidates(self.run)[0]['status'], 'draft')
    def test_case_insensitive_identifiers_and_cte_column_lists(self):
        for sql in ('SELECT SUM(cost) FROM SITE.WORK', 'SELECT SUM(cost) FROM "SITE"."WORK"',
                    'WITH totals(value) AS (SELECT SUM(cost) FROM site.work) SELECT value FROM totals',
                    'WITH totals(value) AS (SELECT SUM(cost) FROM site.work), other(n) AS (SELECT 1) SELECT value FROM totals'):
            with self.subTest(sql=sql):
                self.assertEqual(self.run.execute({'sql': sql})['rows'], [['34.75']])

    def test_cte_alias_does_not_hide_function_use_elsewhere(self):
        with self.assertRaises(ValueError):
            self.run.execute({'sql': 'WITH query(value) AS (SELECT 1) SELECT * FROM query(\'SELECT 1\')'})

    def test_failed_parameter_sample_remains_held_after_successful_sample_and_schema_check(self):
        self.run.write(self.run.context / 'queries.json', [query(
            'SELECT SUM(cost) / COUNT(*) FROM site.work WHERE kind={{kind}}')])
        self.run.check_query({'name': 'work-total', 'parameters': {'kind': 'build'}})
        # 0/0 returns NaN and is correctly refused by the local executor.
        bodies = [query('SELECT COUNT(*) * 1.0 / COUNT(*) FROM site.work WHERE kind={{kind}}')]
        self.run.write(self.run.context / 'queries.json', bodies)
        with self.assertRaises(ValueError):
            self.run.check_query({'name': 'work-total', 'parameters': {'kind': 'missing'}})
        self.run.check_query({'name': 'work-total', 'parameters': {'kind': 'build'}})
        self.run.check()
        self.assertEqual(metric_preview.candidates(self.run)[0]['status'], 'held')
        bodies[0]['source_id'] = 'example/shape-cost'
        self.run.write(self.run.context / 'queries.json', bodies)
        import context_contracts as mine
        plan = mine.cmd_check(SimpleNamespace(dir=self.run.context))
        self.assertTrue(any('parameter sample failed' in r['error'] for r in plan['first_refusals']))
        self.assertFalse(self.run.read(self.run.context / 'plan.json')['queries'][0]['ok'])


if __name__ == '__main__':
    unittest.main()
