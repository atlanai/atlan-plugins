"""BI units over a native query: one source per table, and the member filters it can carry."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema', 'duckdb'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    import duckdb
    import yaml
    from metrics_agent.lib import evidence
    from metrics_agent.lib import rules as R
    from metrics_agent.plugs.readers import bicompile
    from metrics_agent.stages import a2_question_units as a2

NATIVE_SQL = 'SELECT region, amount, amount * 2 AS doubled FROM retail.fct_sales'
ROWS = [('north', 10), ('north', 30), ('south', 20), ('east', 20), ('west', 5), (None, 50)]


def native_unit(filters, **extra):
    return {'unit_id': 'u', 'warehouse_tables': ['retail.fct_sales'], 'group_by': ['region'],
            'measures': [{'name': 'Avg Amount', 'aggregation': None, 'formula_language': 'dax',
                          'formula': 'AVERAGE(Sales[Amount])'}],
            'filters': filters, 'join_graph': [],
            'native_source': {'logical': 'Sales', 'sql': NATIVE_SQL,
                              'columns': {'Region': 'region', 'Amount': 'amount', 'Doubled': 'doubled'}},
            **extra}


def top_n(population='filtered', n=2):
    return {'scope': 'visual', 'stage': 'dimension', 'column': 'region', 'op': 'top_n',
            'table': 'retail.fct_sales',
            'value': {'n': n, 'by': '[Avg Amount]', 'direction': 'desc', 'ties': True,
                      'population': population, 'empty_members': 'excluded'}}


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class NativeSourceBindingTest(unittest.TestCase):
    def test_a_native_query_over_a_physical_table_is_the_source_of_a_unit_without_measures(self):
        tables = [{'name': 'db.marts.accounts', 'logical_name': 'Accounts',
                   'source_lineage': {'object': 'accounts', 'native_sql': 'SELECT 1'}}]
        logical_of = a2._logical_of(tables, {'accounts': {'sql': 'SELECT 1'}})
        self.assertEqual(logical_of, {'db.marts.accounts': 'accounts'})
        self.assertEqual(a2._native_for([], {}, {'accounts': 'native'}, ['db.marts.accounts'],
                                        logical_of), 'native')

    def test_two_native_queries_over_one_object_keep_the_physical_binding(self):
        tables = [{'name': 'db.marts.accounts', 'logical_name': 'Open',
                   'source_lineage': {'native_sql': 'SELECT 1'}},
                  {'name': 'db.marts.accounts', 'logical_name': 'Closed',
                   'source_lineage': {'native_sql': 'SELECT 2'}}]
        logical_of = a2._logical_of(tables, {'open': {}, 'closed': {}})
        self.assertEqual(logical_of, {})

    def test_a_statement_sourced_table_is_still_bound(self):
        tables = [{'name': 'Report', 'logical_name': 'Report',
                   'source_lineage': {'statement_is_source': True, 'native_sql': 'SELECT 1'}}]
        self.assertEqual(a2._logical_of(tables, {'report': {}}), {'Report': 'report'})


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class NativeTopNTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RULES
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            RULES = R.load(bicompile.RULES)

    def compile(self, unit):
        return bicompile.compile_unit(unit, RULES, 'duckdb',
                                      columns={'retail.fct_sales': {'region', 'amount'}},
                                      table_of={'sales': 'retail.fct_sales'},
                                      measures={'avg amount': 'AVERAGE(Sales[Amount])'})

    def run_sql(self, sql):
        db = duckdb.connect()
        db.execute('CREATE SCHEMA retail')
        db.execute('CREATE TABLE retail.fct_sales (region TEXT, amount INTEGER)')
        db.executemany('INSERT INTO retail.fct_sales VALUES (?, ?)', ROWS)
        return sorted(db.execute(sql).fetchall(), key=repr)

    def test_a_filtered_top_n_over_a_native_query_keeps_the_ranked_members_with_ties(self):
        result = self.compile(native_unit([top_n()], top_n=2, sort='[Avg Amount] DESC'))
        self.assertEqual(result.coverage, 'translated', result.reason)
        expected = self.run_sql(
            'SELECT region, AVG(amount) FROM retail.fct_sales GROUP BY region '
            'QUALIFY RANK() OVER (ORDER BY AVG(amount) DESC) <= 2')
        self.assertEqual(self.run_sql(result.sql), expected)

    def test_other_member_filters_over_a_native_query_stay_refused(self):
        result = self.compile(native_unit([top_n(population='all')], top_n=2, sort='[Avg Amount] DESC'))
        self.assertEqual(result.coverage, 'refused')
        condition = {'scope': 'visual', 'stage': 'dimension', 'column': 'region', 'op': 'condition',
                     'table': 'retail.fct_sales', 'value': {'expression': '[Avg Amount] > 10'}}
        self.assertEqual(self.compile(native_unit([condition])).coverage, 'refused')


LONG_NATIVE = ('-- the account list, one row per company\nSELECT region,\n       amount,\n'
               + ',\n'.join(f'       amount * {i} AS a{i}' for i in range(40))
               + '\nFROM retail.fct_sales')


def write_run(root, units=(), candidates=(), findings=()):
    import json
    (root / 'question_units.json').write_text(json.dumps({'units': list(units)}))
    (root / 'bi_candidates.json').write_text(json.dumps({'candidates': list(candidates)}))
    if findings:
        (root / 'a3b_review.json').write_text(json.dumps({'findings': list(findings)}))


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class EarlierWorkTest(unittest.TestCase):
    """Fewer bytes and fewer rounds, with the same checks deciding the same things."""

    def setUp(self):
        import tempfile
        from metrics_agent.lib import card_sql, early_holds, shared_sources
        from metrics_agent.stages import x3_emit
        self.card_sql, self.early, self.shared, self.x3 = card_sql, early_holds, shared_sources, x3_emit
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_native_query_repeated_in_a_bundle_is_held_once_and_put_back_exactly(self):
        write_run(self.root, units=[{'unit_id': 'u', 'native_source': {'sql': LONG_NATIVE}}])
        statement = f'SELECT COUNT(*) FROM (\n{LONG_NATIVE}\n) t'
        doc = {'instruction': 'x', 'items': [{'sql': statement}, {'sql': statement}],
               'derived_source': {'sql': statement}}
        out = self.shared.factor(doc, self.root)
        self.assertEqual(list(out['shared_sources']), ['S1'])
        self.assertNotIn(LONG_NATIVE, out['items'][0]['sql'])
        self.assertEqual(out['derived_source']['sql'], statement)
        self.assertEqual(self.shared.expand(out['items'][1]['sql'], out['shared_sources']), statement)
        once = {'instruction': 'x', 'items': [{'sql': statement}]}
        self.assertIs(self.shared.factor(once, self.root), once)

    def test_only_certain_holds_are_predicted(self):
        unit = {'unit_id': 'drill', 'parameters': [{'kind': 'member', 'column': 'company'}],
                'filters': [{'column': 'company', 'op': 'filter', 'value': None, 'value_kind': 'absent'}]}
        variant = {**unit, 'unit_id': 'drill::each', 'variant_of': 'drill', 'filters': []}
        chosen = {**unit, 'unit_id': 'chosen', 'filters': [{'column': 'company', 'op': 'eq', 'value': 'A'}]}
        cands = [{'candidate_id': c, 'sql': f'SELECT {i}', 'provenance': {'bi': {'unit_id': u}}}
                 for i, (c, u) in enumerate([('c1', 'drill'), ('c2', 'drill::each'), ('c3', 'chosen'), ('c4', 'other')])]
        findings = [{'candidate_id': 'c4', 'severity': 'wrong', 'finding': 'reads the wrong table',
                     'sql_sha': self.card_sql.statement_sha('SELECT 3')},
                    {'candidate_id': 'c3', 'severity': 'wrong', 'finding': 'about older SQL',
                     'sql_sha': self.card_sql.statement_sha('SELECT 99')}]
        write_run(self.root, units=[unit, variant, chosen, {'unit_id': 'other'}], candidates=cands, findings=findings)
        held = self.early.predicted(self.root)
        self.assertEqual({k: v['reason'] for k, v in held.items()},
                         {'c1': 'required_filter_missing', 'c4': 'compiler_review_wrong'})

    def test_a_literal_from_the_native_query_is_its_evidence_and_no_other_is(self):
        from metrics_agent.stages import a3_translate
        unit = {'native_source': {'sql': 'SELECT IFF(COALESCE(signal_1, 0) > 0, 1, 0) AS fired FROM t'}}
        self.assertTrue(a3_translate._in_native(unit, 'signal_1'))
        self.assertTrue(a3_translate._in_native(unit, 'SIGNAL_1'))
        self.assertFalse(a3_translate._in_native(unit, 'signal'))
        self.assertFalse(a3_translate._in_native({}, 'signal_1'))

    def test_a_date_literal_is_one_rule_and_function_aggregations_read_as_custom(self):
        self.assertTrue(self.card_sql.DATE_LITERAL.search("t.created_at >= '2026-04-22'"))
        self.assertFalse(self.card_sql.DATE_LITERAL.search("t.created_at >= {{start}}"))
        self.assertEqual(self.x3.card_aggregation('avg'), 'custom')
        self.assertEqual(self.x3.card_aggregation('MEDIAN'), 'custom')
        self.assertEqual(self.x3.card_aggregation('count'), 'count')
        self.assertEqual(self.x3.card_aggregation('nonsense'), 'nonsense')

    def test_the_card_is_handed_the_native_query_as_its_from_clause(self):
        units = {'u1': {'native_source': {'logical': 'AR Accounts', 'sql': LONG_NATIVE,
                                          'columns': {'Region': 'region', 'Amount': 'amount'}}},
                 'u2': {'native_source': {'logical': 'Other', 'sql': 'SELECT 1 AS x'}}}
        got = self.x3._derived_source(['u1', 'u1'], units, 'duckdb')
        self.assertEqual(got['alias'], 'ar_accounts')
        self.assertNotIn('--', got['sql'])
        self.assertTrue(got['sql'].endswith(') AS ar_accounts'))
        db = duckdb.connect()
        db.execute('CREATE SCHEMA retail')
        db.execute('CREATE TABLE retail.fct_sales (region TEXT, amount INTEGER)')
        db.executemany('INSERT INTO retail.fct_sales VALUES (?, ?)', ROWS)
        self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {got['sql']} WHERE ar_accounts.region = 'north'").fetchone(), (2,))
        self.assertIsNone(self.x3._derived_source(['u1', 'u2'], units, 'duckdb'))
        self.assertIsNone(self.x3._derived_source(['u1', None], units, 'duckdb'))

    def test_a_card_the_warehouse_refuses_is_refused_at_its_gate_and_an_access_limit_is_not(self):
        class Ctx:
            def __init__(self):
                self.said = []

            def say(self, text):
                self.said.append(text)

        class Runner:
            def __init__(self, db):
                self.db = db

            def connect(self):
                pass

            def close(self):
                pass

            def fetch(self, sql, limit):
                cur = self.db.execute(sql)
                return [d[0] for d in cur.description], cur.fetchmany(limit)

        db = duckdb.connect()
        db.execute('CREATE SCHEMA retail')
        db.execute('CREATE TABLE retail.fct_sales (region TEXT, amount INTEGER)')
        good = {'name': 'Sales', 'formula': 'SUM(retail.fct_sales.amount)', 'entity': 'retail.fct_sales',
                'source': {'entity': 'retail.fct_sales'}}
        bad = {**good, 'name': 'Broken', 'formula': 'SUM(retail.fct_sales.missing_column)'}
        ctx = Ctx()
        with mock.patch.object(self.x3._runtime, 'sql_runner', return_value=Runner(db)):
            problems = self.x3._card_witness([good, bad], {'witness_mode': 'execute'}, 'duckdb', ctx)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith('Broken: the warehouse refused'))
        with mock.patch.object(self.x3._runtime, 'sql_runner', side_effect=RuntimeError('no route')):
            self.assertEqual(self.x3._card_witness([bad], {'witness_mode': 'execute'}, 'duckdb', ctx), [])
        self.assertIn('checked by parsing only', ctx.said[-1])


POWERBI = ENGINE / 'packages/metrics-agent-powerbi/src'


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class CachedSignInTest(unittest.TestCase):
    """Without a terminal, a person's unexpired cached sign-in is reused, and only that."""

    def setUp(self):
        import json
        import os
        import tempfile
        import time
        sys.path.insert(0, str(POWERBI))
        from metrics_agent_powerbi import pbi_definition_extract as X
        from metrics_agent_powerbi import plug
        self.X, self.plug, self.json, self.time = X, plug, json, time
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {'XDG_CACHE_HOME': self.tmp.name}
        drop = [n for n in ('PBI_TENANT_ID', 'PBI_CLIENT_ID', 'PBI_CLIENT_SECRET', 'PBI_ACCESS_TOKEN',
                            'PBI_PUBLIC_CLIENT_ID', 'PBI_SIGN_IN', 'PBI_CLIENT_CERTIFICATE_PATH') if n in os.environ]
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for n in drop:
            os.environ.pop(n)
        terminal = mock.patch.object(plug, 'at_terminal', return_value=False)
        terminal.start()
        self.addCleanup(terminal.stop)
        self.path = X._cache_path('organizations', X.DEFAULT_PUBLIC_CLIENT_ID, X.DEFAULT_SCOPE)

    def cache(self, expires_in):
        import os
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as handle:
            self.json.dump({'access_token': 'cached-value', 'expires_at': self.time.time() + expires_in}, handle)

    def test_no_terminal_and_no_cache_is_refused(self):
        self.assertIsNone(self.plug.sign_in({}))
        self.assertIsNone(self.plug.sign_in({'sign_in': 'browser'}))

    def test_an_unexpired_cache_is_reused_without_asking(self):
        self.cache(1800)
        self.assertEqual(self.plug.sign_in({}), 'cached')
        self.assertEqual(self.plug.sign_in({'sign_in': 'device'}), 'cached')
        with mock.patch.object(self.plug, 'fetch_wait', return_value=60):
            cmd = self.plug.PowerBIFabric({'targets': ['https://app.powerbi.com/groups/w/reports/r']}).command(Path(self.tmp.name))
        self.assertEqual(cmd[cmd.index('--auth') + 1], 'cached')
        self.assertNotIn('--no-token-cache', cmd)

    def test_an_expired_cache_is_refused_and_the_extractor_never_asks(self):
        self.cache(-10)
        self.assertIsNone(self.plug.sign_in({}))
        import argparse
        args = argparse.Namespace(auth='cached', tenant='organizations', client_id=self.X.DEFAULT_PUBLIC_CLIENT_ID,
                                  scope=self.X.DEFAULT_SCOPE, no_token_cache=False, sign_in_timeout=1)
        with self.assertRaises(self.X.AuthError):
            self.X.acquire_token(args)


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class DetailTableTest(unittest.TestCase):
    """Detail tables over a native query: bound to it, quoted, scoped and described as they run."""

    def test_a_filter_on_a_table_no_relationship_reaches_does_not_apply(self):
        rels = [{'from_table': 'Activity', 'to_table': 'Snapshot', 'is_active': True},
                {'from_table': 'Orders', 'to_table': 'Snapshot', 'is_active': False}]
        self.assertTrue(a2._related('snapshot', {'activity'}, rels))
        self.assertTrue(a2._related('Activity', {'snapshot'}, rels))
        self.assertFalse(a2._related('snapshot', {'funnel'}, rels))
        # An inactive relationship carries no filter either.
        self.assertFalse(a2._related('snapshot', {'orders'}, rels))

    def test_a_column_name_that_cannot_be_bare_is_quoted_and_a_bare_one_is_left_as_written(self):
        self.assertEqual(bicompile._legal('ACCOUNT_ID'), 'ACCOUNT_ID')
        self.assertEqual(bicompile._legal('Pod'), 'Pod')
        self.assertEqual(bicompile._legal('ai maturity level'), '"ai maturity level"')
        self.assertEqual(bicompile._legal('Close "Date"'), '"Close ""Date"""')

    def test_a_literal_inside_a_moving_date_is_not_the_operand(self):
        from metrics_agent.lib import sqlshape

        def first(where):
            f = sqlshape.extract_feat(sqlshape.parse(f'SELECT pod FROM t WHERE {where}', 'snowflake'), [])
            return f['filters'][0]['value'], f['filters'][0]['value_kind']
        self.assertEqual(first("qs >= DATEADD(QUARTER, -5, DATE_TRUNC('QUARTER', CURRENT_DATE))")[1], 'expression')
        self.assertEqual(first("d >= TO_DATE('2026-01-01')"), ('2026-01-01', 'literal'))
        self.assertEqual(first("d >= '2026-01-01'::DATE"), ('2026-01-01', 'literal'))
        self.assertEqual(first('n > -5'), ('-5', 'literal'))

    def test_a_per_member_variant_over_a_native_query_proves_its_member(self):
        sys.path.insert(0, str(ENGINE.parents[2]))
        from modules.metrics.required_filters import required_filter_holds
        native = 'SELECT account_name AS "Account Name", score FROM db.m.snapshot'
        unit = {'unit_id': 'drill::each', 'variant_of': 'drill', 'warehouse_tables': ['db.m.snapshot'],
                'group_by': ['Account Name'], 'filters': [],
                'native_source': {'logical': 'Snapshot', 'sql': native, 'columns': {}},
                'parameters': [{'kind': 'member', 'name': 'm', 'column': 'Account Name', 'table': 'db.m.snapshot'}]}

        def holds(sql):
            q = {'dialect': 'snowflake', 'objects': [{'vq_id': 'q', 'sql': sql,
                                                     'provenance': {'lane': 'bi', 'bi': {'unit_id': unit['unit_id']}}}]}
            return required_filter_holds(q, {'units': [unit]})
        self.assertEqual(holds(f'SELECT t."Account Name", SUM(t.score) FROM ({native}) t GROUP BY t."Account Name"'), {})
        with_pod = 'SELECT account_name AS "Account Name", pod AS "Pod", score FROM db.m.snapshot'
        self.assertIn('q', holds(f'SELECT t."Pod", SUM(t.score) FROM ({with_pod}) t GROUP BY t."Pod"'))
        self.assertIn('q', holds(f'SELECT SUM(t.score) FROM ({native}) t'))

    def test_the_derived_source_names_the_table_it_reads(self):
        from metrics_agent.stages import x3_emit
        units = {'u1': {'warehouse_tables': ['retail.fct_sales', 'retail.dim_region'],
                        'native_source': {'logical': 'Sales', 'sql': NATIVE_SQL, 'columns': {'Region': 'region'}}}}
        got = x3_emit._derived_source(['u1'], units, 'duckdb')
        self.assertEqual(got['entity'], 'RETAIL.FCT_SALES')
        self.assertIn('RETAIL.FCT_SALES', got['how_to_use'])
        units['u2'] = {'warehouse_tables': ['retail.other'], 'native_source': units['u1']['native_source']}
        self.assertNotIn('entity', x3_emit._derived_source(['u1', 'u2'], units, 'duckdb'))

    def test_a_question_is_asked_at_c3_as_m2_requires(self):
        from metrics_agent.stages import c3_questions
        cands = [{'candidate_id': 'c1'}]
        row = {'candidate_id': 'c1', 'description': 'd', 'evidence_citations': []}
        with self.assertRaises(SystemExit) as stop:
            c3_questions._validate_model_output([{**row, 'question': 'Region Targets (held)'}], cands)
        self.assertIn('question mark', str(stop.exception))
        out = c3_questions._validate_model_output([{**row, 'question': 'What is each pod\'s target?'}], cands)
        self.assertIn('c1', out)

    def test_a_verified_query_keeps_a_filter_polarity_and_provenance(self):
        from metrics_agent.lib import filter_text
        from metrics_agent.stages import c5_emit
        parsed = {'column': 'classification', 'op': 'in', 'value': "('No Transcript')", 'stage': 'where',
                  'negated': True, 'in_source': True, 'value_kind': 'expression', 'scope_tables': ['db.m.calls'],
                  'is_identity': False}
        kept = c5_emit._filter_record(parsed)
        self.assertNotIn('is_identity', kept)
        self.assertTrue(kept['negated'] and kept['in_source'])
        self.assertEqual(filter_text.restriction(kept, mark_stage=False), "classification not in ('No Transcript')")
        plain = c5_emit._filter_record({'column': 'pod', 'op': 'eq', 'value': 3})
        self.assertEqual(plain, {'column': 'pod', 'op': 'eq', 'value': '3', 'stage': None})


if __name__ == '__main__':
    unittest.main()
