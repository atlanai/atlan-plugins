"""Generic guards from a live run: filter polarity, list-literal shapes, alias and domain checks,
history size bounds, model-only scope, one-connection publication, partial-save follow-up and
organization-aware proof links. Synthetic inputs only; no network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
ASK = ROOT / 'plugins/atlanai-analytics/skills/analytics-ask/scripts'
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/src'
sys.path.insert(0, str(SCRIPTS))
import context_contracts as mine  # noqa: E402
import publication_batches as batches  # noqa: E402
import publication_followup as followup  # noqa: E402
from project import Project, ProjectError  # noqa: E402

HAS_SQLGLOT = importlib.util.find_spec('sqlglot') is not None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


filter_text = load(ENGINE / 'metrics_agent/lib/filter_text.py', 'filter_text_under_test')
links = load(ASK / 'definition_link.py', 'definition_link_under_test')


class FilterPolarity(unittest.TestCase):
    def test_negation_survives_text_and_key(self):
        not_null = {'column': 'industry', 'op': 'is', 'value': 'NULL', 'negated': True}
        is_null = {'column': 'industry', 'op': 'is', 'value': 'NULL'}
        self.assertEqual(filter_text.restriction(not_null), 'industry is not NULL')
        self.assertNotEqual(filter_text.key(not_null), filter_text.key(is_null))

    def test_word_operators_and_stage(self):
        f = {'column': 'region', 'op': 'in', 'value': "('a')", 'negated': True, 'stage': 'cte'}
        self.assertEqual(filter_text.restriction(f), "region not in ('a') [in a CTE]")
        self.assertEqual(filter_text.key(f), 'region not in')
        g = {'column': 'amount', 'op': '>', 'value': 5, 'negated': True}
        self.assertEqual(filter_text.restriction(g), 'not (amount > 5)')


@unittest.skipUnless(HAS_SQLGLOT, 'the metrics runtime (sqlglot) is not installed')
class ShapeAndGateChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ENGINE))
        import sqlglot
        from metrics_agent.lib import sqlshape
        cls.sqlglot, cls.S = sqlglot, sqlshape

    def shape(self, sql):
        return self.S.canonicalise(self.sqlglot.parse_one(sql, read='snowflake'), 'snowflake')

    def test_literal_lists_of_any_length_share_a_shape(self):
        self.assertEqual(self.shape("select a from t where id in ('x', 'y', 'z')"),
                         self.shape("select a from t where id in ('q')"))
        self.assertEqual(self.shape("select * from (values ('a'), ('b'))"),
                         self.shape("select * from (values ('c'))"))

    def test_a_subquery_list_keeps_its_own_shape(self):
        self.assertNotEqual(self.shape("select a from t where id in ('x', 'y')"),
                            self.shape('select a from t where id in (select id from u)'))

    def test_a_one_column_values_list_does_not_define_the_shape(self):
        parse = lambda sql: self.sqlglot.parse_one(sql, read='snowflake')
        ids = ("with t as (select column1 as id from (values ('a'), ('b'))) "
               "select count(*) from x join t on x.id = t.id")
        self.assertEqual(self.S.defining_literals(parse(ids)), [])
        weights = "with t as (select column1 as id, column2 as w from (values ('a', 2))) select sum(w) from t"
        self.assertIn('2', self.S.defining_literals(parse(weights)))

    def test_opposite_null_tests_are_two_shapes(self):
        feat = {'filters': [{'column': 'x', 'op': 'is', 'value': 'NULL', 'negated': True}]}
        self.assertEqual(self.S.normalised_predicates(feat), ['x is not NULL'])

    def test_quoted_domains_and_addresses_are_not_aliases(self):
        from metrics_agent.stages import x3_emit
        found = x3_emit._unqualified(
            "domain NOT IN ('atlan.com', 'ex-hq.co') and m.amount > 0, see ops@acme.io or ex-hq.co")
        self.assertEqual(found, ['m.amount'])


class HistorySizeBound(unittest.TestCase):
    def test_over_size_statements_are_counted_not_dropped_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'pull.jsonl'
            src.write_text('\n'.join(json.dumps(r) for r in (
                {'sql': 'SELECT 1', 'exec': 3}, {'sql': 'SELECT ' + 'x, ' * 400 + '1', 'exec': 7})))
            dst = root / 'querylog.jsonl'
            args = type('A', (), {'src': [str(src)], 'dst': str(dst), 'append': False, 'limit': 5000,
                                  'max_statement_chars': 200})
            result = mine.cmd_querylog(args)
            self.assertEqual(result['statements'], 1)
            self.assertEqual(result['over_size'], {'over_size_statements': 1, 'over_size_runs': 7})
            coverage = json.loads(Path(result['coverage']).read_text())
            self.assertEqual(coverage['max_statement_chars'], 200)


class ModelOnlyScope(unittest.TestCase):
    def test_tmdl_folder_takes_a_measure_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate = Path(tmp).resolve() / 'estate'
            (estate / 'definition/tables').mkdir(parents=True)
            (estate / 'definition/tables/orders.tmdl').write_text(
                'table orders\n\tmeasure Total = SUM ( orders[amount] )\n')
            run = Project(estate / '.atlan-metrics' / 'example')
            survey = run.survey(estate)
            source = next(s for s in survey['sources'] if s['path'].endswith('.tmdl'))
            self.assertNotEqual(source['kind'], 'powerbi')
            scoped = run.scope({'source_id': source['source_id'], 'measures': ['Total'],
                                'history_tables': ['DB.SALES.ORDERS']})
            self.assertEqual(scoped['selection']['measures'], ['Total'])
            self.assertTrue(scoped['narrowed'])
            with self.assertRaises(ProjectError):
                run.scope({'source_id': source['source_id'], 'measures': [''], 'history_tables': []})


def query(index):
    return {'name': 'query-' + str(index), 'display_name': 'Example query', 'source_id': 'q-' + str(index),
            'definition': {'spec_version': '1.0-draft.1', 'dialect': 'sqlite',
                           'question': 'How many orders are there?', 'sql': 'SELECT COUNT(*) FROM orders'}}


class OneConnectionPublication(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.docs = {'metrics': [], 'queries': [query(i) for i in range(6)], 'links': [],
                     'plan': {'metrics': [], 'links': [], 'queries': [
                         {'name': query(i)['name'], 'source_id': query(i)['source_id'], 'action': 'create',
                          'ok': True, 'match_id': None} for i in range(6)]}}
        for name, value in self.docs.items():
            (self.root / (name + '.json')).write_text(json.dumps(value))
        identity, _ = batches.publication_identity(workspace_id='workspace_target', sources=['src'],
                                                   documents=self.docs)
        self.auth = {'intent': 'publish', 'workspace_id': 'workspace_target', 'sources': ['src'],
                     'approved_plans': [identity]}

    def flow(self):
        auth = self.root.parent / (self.root.name + '-auth.json')
        auth.write_text(json.dumps(self.auth))
        out = subprocess.run([sys.executable, str(SCRIPTS / 'analytics_flow.py'), '--authorization', str(auth),
                              '--workspace', 'workspace_target', '--context', str(self.root), '--source', 'src'],
                             capture_output=True, text=True)
        return json.loads(out.stdout)

    def test_an_inventory_from_another_workspace_is_refused(self):
        self.assertEqual(self.flow()['decision'], 'continue')
        (self.root / 'existing.json').write_text(json.dumps(
            [{'kind': 'sql_query', 'name': 'other', 'workspace_id': 'workspace_elsewhere'}]))
        result = self.flow()
        self.assertEqual(result['decision'], 'needs_context')
        self.assertIn('another workspace', result['reason'])
        (self.root / 'existing.json').write_text('[]')
        (self.root / 'inventory.json').write_text(json.dumps({'workspace_id': 'workspace_elsewhere'}))
        self.assertEqual(self.flow()['decision'], 'needs_context')

    def test_preflight_and_small_batches(self):
        result = batches.prepare(self.root, self.auth, 'workspace_target', ['src'], max_bytes=600)
        self.assertEqual(result['preflight']['name'], 'registry__get_artifact_counts')
        self.assertEqual(result['preflight']['expect_created'], {'metric': 0, 'sql_query': 6})
        self.assertGreater(len(result['calls']), 1)
        self.assertEqual(sum(c['count'] for c in result['calls']), 6)


class PartialSaveFollowup(unittest.TestCase):
    def test_settles_cut_results_and_plans_only_safe_next_calls(self):
        batch = {'calls': [{'arguments': {'kind': 'sql_query'}, 'source_ids': ['q-0', 'q-1', 'q-2', 'q-3']},
                           {'arguments': {'kind': 'metric'}, 'source_ids': ['m-0', 'm-1']}]}
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / 'first.json'
            # Cut to two rows; its totals say the one failure is among them.
            first.write_text(json.dumps([{'type': 'text', 'text': json.dumps({
                'created': 3, 'failed': 1, 'items': [
                    {'index': 0, 'ok': True, 'artifact': {'id': 'sql_query_a'}},
                    {'index': 1, 'ok': False, 'code': 'conflict', 'error': 'taken'}]})}]))
            second = Path(tmp) / 'second.json'
            second.write_text(json.dumps({'created': 1, 'failed': 1, 'items': [
                {'index': 0, 'ok': False, 'code': 'internal'},
                {'index': 1, 'ok': True, 'artifact': {'id': 'metric_b'}}]}))
            names = {'q-0': 'a', 'q-1': 'b', 'q-2': 'c', 'q-3': 'd', 'm-0': 'M0', 'm-1': 'M1'}
            approved = [{'from': {'kind': 'sql_query', 'name': 'a'}, 'to': {'kind': 'metric', 'name': 'M1'},
                         'rel_type': 'source_of'},
                        {'from': {'kind': 'sql_query', 'name': 'b'}, 'to': {'kind': 'metric', 'name': 'M1'},
                         'rel_type': 'source_of'},
                        {'from': {'kind': 'sql_query', 'name': 'c'}, 'to': {'kind': 'metric', 'name': 'M1'},
                         'rel_type': 'source_of'},
                        {'from': {'kind': 'metric', 'name': 'M1'}, 'to': {'kind': 'metric', 'name': 'M0'},
                         'rel_type': 'depends_on'}]
            out = followup.settle(batch, [str(first), str(second)], {}, approved, 'workspace_x', names)
        self.assertEqual(out['counts'], {'created': 4, 'failed': 2})
        self.assertEqual([h['name'] for h in out['held']], ['b'])
        self.assertEqual([r['name'] for r in out['retry_after_lookup']], ['M0'])
        self.assertEqual(len(out['link_calls']), 1)              # a -> M1, both ids known
        self.assertEqual(out['held_links'][0]['from'], 'b')       # the conflict holds its link
        looked = {c['arguments']['name'] for c in out['lookup_calls']}
        self.assertEqual(looked, {'M0', 'c'})                    # retry check and missing link id
        self.assertNotIn('depends_on', json.dumps(out['link_calls']))


class ProofLinkOrganizations(unittest.TestCase):
    def test_not_found_in_the_cli_organization_names_the_others(self):
        replies = {
            ('auth', 'status'): '{"state":"authenticated","gateway":"https://api.atlan.com","orgID":"org_a"}',
            ('api', 'get', 'registry:/users/me'): '{"org_id":"org_a","orgs":[{"id":"org_a","slug":"first"},'
                                                  '{"id":"org_b","slug":"second"}]}',
        }

        def run(argv, **_):
            for prefix, body in replies.items():
                if tuple(argv[1:1 + len(prefix)]) == prefix:
                    return subprocess.CompletedProcess(argv, 0, stdout=body, stderr='')
            return subprocess.CompletedProcess(argv, 1, stdout='private body',
                                               stderr='atlanai: hidden (HTTP 404, not_found)')
        with patch.object(links.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, "organization 'first'.*second") as caught:
                links.resolve_link('https://api.atlan.com', 'https://app.atlan.com', 'metric',
                                   'metric_x', 'workspace_y')
        self.assertNotIn('private', str(caught.exception))
        self.assertNotIn('hidden', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
