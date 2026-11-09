"""Reruns retain released dialects/catalogs and trustworthy query usage metadata."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
from project import Project, ProjectError
import metrics_mine as mine
from test_local_project import readbacks

HAS_DUCKDB = importlib.util.find_spec('duckdb') is not None


class ReleasedExecution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source';self.source.mkdir()
        (self.source / 'work.csv').write_text('cost\n10\n25.5\n')
        self.project = Project(self.root / 'run')
        self.project.survey(self.source)
        self.payload = readbacks('SELECT SUM(cost) AS value FROM archive_example.site.work')
        self.payload['artifacts'][0]['source_id'] = 'history:work'
        self.payload['artifacts'][0]['description'] = 'Total work cost.'

    def select(self, payload=None):
        return self.project.released_execution({'readbacks': payload or self.payload})

    def test_released_dialect_and_catalog_are_selected_without_rewriting_or_publishing(self):
        original = copy.deepcopy(self.payload)
        plan = self.select()
        self.assertEqual(plan['kind'], 'duckdb')
        self.assertEqual(plan['catalog'], 'archive_example')
        self.assertEqual(plan['database'], 'archive_example.duckdb')
        self.assertFalse(plan['remote_writes'])
        self.assertEqual(self.payload, original)
        self.assertNotIn('publication', self.project.current())
        with self.assertRaisesRegex(ProjectError, 'released dialect and catalog'):
            self.project.prepare({'database': 'new.sqlite', 'tables': []})
        with self.assertRaisesRegex(ProjectError, 'released dialect and catalog'):
            self.project.prepare({'database': 'other.duckdb', 'tables': []})
        self.assertFalse((self.source / 'new.sqlite').exists())

    def test_current_native_binding_is_reused_only_if_its_tables_and_catalog_match(self):
        state = self.project.current()
        state['execution'] = {'kind': 'duckdb', 'path': str(self.source / 'archive_example.duckdb'), 'tables': ['site.work'], 'read_only': True}
        self.project.write(self.project.path, state)
        self.assertTrue(self.select()['binding_reusable'])
        state['execution']['tables'] = ['other.work'];self.project.write(self.project.path, state)
        self.assertFalse(self.select()['binding_reusable'])
        state['execution']['tables'] = ['site.work'];state['execution']['path'] = str(self.source / 'other.duckdb')
        self.project.write(self.project.path, state)
        self.assertFalse(self.select()['binding_reusable'])

    def test_head_mixed_or_foreign_catalogs_require_review_without_recording_a_plan(self):
        head = copy.deepcopy(self.payload);head['artifacts'][0]['is_released'] = False
        mixed = copy.deepcopy(self.payload);other = copy.deepcopy(mixed['artifacts'][0])
        other['id'] = 'sql_query_other';other['metadata']['definition']['dialect'] = 'sqlite'
        mixed['artifacts'].append(other)
        catalogs = copy.deepcopy(self.payload)
        catalogs['artifacts'][0]['metadata']['definition']['sql'] += ' JOIN other_catalog.site.work USING(cost)'
        for payload in (head, mixed, catalogs):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.select(payload)
            self.assertNotIn('released_execution', self.project.current())

    def test_changed_sources_clear_the_selection_and_empty_selection_keeps_fresh_sqlite_default(self):
        self.select()
        (self.source / 'work.csv').write_text('cost\n22\n')
        self.project.survey(self.source)
        self.assertNotIn('released_execution', self.project.current())
        self.select()
        result = self.project.released_execution({'readbacks': {'workspace_id': 'workspace_example', 'artifacts': []}})
        self.assertEqual(result['kind'], 'sqlite')
        self.assertEqual(result['status'], 'default')
        self.assertNotIn('released_execution', self.project.current())

    @unittest.skipUnless(HAS_DUCKDB, 'Use an existing approved DuckDB runtime for integration tests.')
    def test_old_catalog_sql_executes_unchanged_and_reconciliation_reuses_it(self):
        plan = self.select()
        source = next(s for s in self.project.current()['sources'] if s['path'] == 'work.csv')
        prepared = self.project.prepare({'database': plan['database'], 'tables': [
            {'source_id': source['source_id'], 'table': 'site.work'}]})
        self.project.bind(prepared['database'])
        self.project.publication(self.payload)
        result = self.project.answer({'readbacks': self.payload, 'artifact_id': 'sql_query_example', 'parameters': {}})
        self.assertEqual(float(result['rows'][0][0]), 35.5)
        self.assertEqual(result['sql'], self.payload['artifacts'][0]['metadata']['definition']['sql'])
        raw = self.payload['artifacts'][0]
        candidate = {k: raw[k] for k in ('name', 'display_name', 'description', 'source_id')}
        candidate['definition'] = raw['metadata']['definition']
        for name, value in [('queries', [candidate]), ('metrics', []), ('links', []), ('existing', [{**raw, 'definition': candidate['definition']}])]:
            self.project.write(self.project.context / (name + '.json'), value)
        counts = mine.cmd_check(SimpleNamespace(dir=str(self.project.context)))['counts']
        self.assertEqual(counts['queries'], {'create': 0, 'revise': 0, 'reuse': 1})
        self.assertEqual(self.project.read(self.project.context / 'queries.json')[0]['definition'], candidate['definition'])

    def test_usage_metadata_accepts_observed_counts_but_rejects_invalid_or_identifying_values(self):
        base = {'name': 'work-total', 'display_name': 'Work total', 'definition': self.payload['artifacts'][0]['metadata']['definition']}
        valid = copy.deepcopy(base);valid['definition'].update(usage_note='Mined from captured history.', usage={'execution_count': 8, 'user_count': 2})
        self.assertEqual(mine.check_query(valid)[0], [])
        for usage in ({'execution_count': True}, {'execution_count': -1}, {'user_count': 1.2}, {}, {'users': 2}):
            invalid = copy.deepcopy(base);invalid['definition']['usage'] = usage
            self.assertTrue(mine.check_query(invalid)[0])
        invalid = copy.deepcopy(base);invalid['definition']['usage_note'] = 'Contact person@example.test'
        self.assertTrue(mine.check_query(invalid)[0])
        unknown = copy.deepcopy(base);unknown['definition']['usage_note'] = 'Derived from the model; historical usage is unavailable.'
        self.assertEqual(mine.check_query(unknown)[0], [])
        self.assertNotIn('usage', unknown['definition'])


if __name__ == '__main__':
    unittest.main()
