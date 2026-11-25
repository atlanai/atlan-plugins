"""Adding past queries to a dashboard's build, quickly: only the dashboard's own queries, long steps shared among
helpers, checks only for what has not run, and the warehouse tool recorded by a command."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
SCHEMAS = ROOT / ('plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/src/metrics_agent/data/'
                  'schemas')
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(SCRIPTS))
import analytics_ux_prototype as fixture  # noqa: E402
import gate_shares  # noqa: E402


class SharesTest(unittest.TestCase):
    def test_one_helper_up_to_sixty_then_shares_of_about_sixty(self):
        self.assertEqual(gate_shares.sizes(60), [60])
        self.assertEqual(gate_shares.sizes(161), [54, 54, 53])
        self.assertEqual(len(gate_shares.sizes(1000)), 4)

    def test_each_share_is_a_file_of_ids_that_covers_the_prompt_once(self):
        root = Path(tempfile.mkdtemp()).resolve()
        (root / 'run.json').write_text('{}')
        engine = root / 'engine'
        engine.mkdir()
        ids = [f'h-{i}' for i in range(130)]
        (engine / 'x1_prompt.json').write_text(json.dumps({'tasks': [{'domain': 'd', 'queries': [{'vq_id': v} for v in ids]}]}))
        out = gate_shares.split(root, 'engine', 'x1')
        got = [line for s in out['shares'] for line in Path(s['ids']).read_text().split()]
        self.assertEqual(sorted(got), sorted(ids))
        self.assertEqual([s['answers_as'] for s in out['shares']], ['p1-NN.json', 'p2-NN.json', 'p3-NN.json'])
        with self.assertRaises(gate_shares.SharesError):
            gate_shares.split(root, 'engine', 'x2')


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = fixture.seed_run(fixture.ContextState(Path(self.tmp.name).resolve()), 'checked')

    def test_checks_due_lists_only_definitions_that_have_not_run_in_shares(self):
        import metric_preview
        items = metric_preview.candidates(self.run)
        ran = sum(i['status'] in ('executed', 'example_executed', 'released') for i in items)
        out = self.run.checks_due(parts=2)
        self.assertEqual((out['due'], out['already_ran']), (len(items) - ran, ran))
        listed = [x for share in out['parts'] for x in share]
        self.assertEqual(len(listed), out['due'])
        self.assertLessEqual(len(out['parts']), 2)

    def test_the_warehouse_tool_is_recorded_by_a_command(self):
        out = self.run.bind_warehouse('snowflake', 'mcp__snowflake__run_snowflake_query')
        self.assertEqual(out['execution']['kind'], 'warehouse')
        self.assertEqual(self.run.execution_binding()['tool'], 'mcp__snowflake__run_snowflake_query')
        with self.assertRaises(ValueError):
            self.run.bind_warehouse('Snow Flake', 'x')


class RunRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = fixture.seed_run(fixture.ContextState(Path(self.tmp.name).resolve()), 'checked')

    def test_the_current_engine_run_is_recorded_by_a_command(self):
        (self.run.root / 'engine-history').mkdir()
        self.assertEqual(self.run.use_engine('engine-history')['engine_run'], 'engine-history')
        self.assertEqual(self.run.manifest()['engine_run'], 'engine-history')
        with self.assertRaises(ValueError):
            self.run.use_engine('../elsewhere')

    def test_the_dashboard_branch_names_its_own_next_step(self):
        # Walk 3, 2026-11-23: after "Make my dashboard agentic" the run asked for a business area.
        self.run.write(self.run.root / 'progress.json', {'stage': 'inspect', 'status': 'running'})
        nxt = self.run.journey('agentic')['continuation']
        self.assertEqual((nxt['action'], nxt['needs_input']), ('dashboard-scope', True))
        self.assertNotIn('business area', nxt['label'])

    def test_a_pulled_query_log_must_be_inside_the_run(self):
        outside = Path(tempfile.mkdtemp()).resolve() / 'log.jsonl'
        outside.write_text('')
        with self.assertRaises(ValueError):
            self.run.history(outside)


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'needs jsonschema')
class HistoryScopeTest(unittest.TestCase):
    def test_a_run_can_keep_only_past_queries_inside_its_tables(self):
        import jsonschema
        schema = json.loads((SCHEMAS / 'run_config.schema.json').read_text())
        prop = schema['properties']['history_scope']
        jsonschema.validate('inside', prop)
        jsonschema.validate('touches', prop)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate('all', prop)
        inputs = json.loads((SCHEMAS / 'run_inputs.schema.json').read_text())
        self.assertIn('scope_match', inputs['properties']['querylog']['oneOf'][0]['properties'])


if __name__ == '__main__':
    unittest.main()


class Walk4Test(unittest.TestCase):
    """The open points of walk 4 (2026-11-23), each held by a test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_a_fetched_report_lists_its_pages_with_tile_counts(self):
        import page_scope
        report = self.root / 'pbi' / 'reports' / 'ws' / 'rep'
        pages = report / 'definition' / 'pages'
        for name, display, hidden, kinds in (('ov', 'Overview', False, ['card', 'slicer', 'barChart']),
                                             ('dt', 'Detail', True, ['tableEx'])):
            (pages / name / 'visuals').mkdir(parents=True)
            (pages / name / 'page.json').write_text(json.dumps(
                {'name': name, 'displayName': display, **({'visibility': 'HiddenInViewMode'} if hidden else {})}))
            for i, k in enumerate(kinds):
                (pages / name / 'visuals' / str(i)).mkdir()
                (pages / name / 'visuals' / str(i) / 'visual.json').write_text(json.dumps({'visual': {'visualType': k}}))
        (pages / 'pages.json').write_text(json.dumps({'pageOrder': ['ov', 'dt']}))
        (report / '.platform').write_text(json.dumps({'metadata': {'displayName': 'Sales'}}))
        got = page_scope.list_pages(self.root / 'pbi')
        self.assertEqual([(p['report'], p['page'], p['tiles'], p['hidden']) for p in got],
                         [('Sales', 'Overview', 2, False), ('Sales', 'Detail', 1, True)])

    def test_discovery_skips_plugin_files_and_the_fetch_manifest(self):
        import source_inventory
        (self.root / 'plugin' / '.claude-plugin').mkdir(parents=True)
        (self.root / 'plugin' / 'report.html').write_text('<html></html>')
        (self.root / 'evals').mkdir()
        (self.root / 'evals' / 'report.html').write_text('<html></html>')
        (self.root / 'pbi' / 'reports').mkdir(parents=True)
        (self.root / 'pbi' / 'manifest.json').write_text('{}')
        (self.root / 'mine.html').write_text('<html></html>')
        found = [p.relative_to(self.root).as_posix() for p, _ in source_inventory.discover(self.root)[0]]
        self.assertEqual(found, ['mine.html'])

    def test_a_count_of_another_querys_rows_is_dropped_from_past_queries(self):
        if not importlib.util.find_spec('sqlglot'):
            self.skipTest('needs sqlglot')
        import sqlglot
        sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/src'))
        from metrics_agent.lib import corpus
        wrap = lambda q: corpus._row_count_wrapper(sqlglot.parse_one(q, read='snowflake'))
        self.assertTrue(wrap('SELECT COUNT(*) FROM (SELECT a, SUM(b) FROM db.s.t GROUP BY a)'))
        self.assertFalse(wrap('SELECT COUNT(*) FROM db.s.t'))
        self.assertFalse(wrap('SELECT COUNT(*) FROM (SELECT * FROM db.s.t) x WHERE x.a > 1'))

    def test_the_person_can_end_without_a_question(self):
        run = fixture.seed_run(fixture.ContextState(self.root), 'checked')
        run.journey('agentic')
        run.journey('asked', answer='declined')
        self.assertEqual(json.loads((run.root / 'interaction.json').read_text())['asked_result'], 'declined')
        with self.assertRaises(ValueError):
            run.journey('asked', answer='maybe')
