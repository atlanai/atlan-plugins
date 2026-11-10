"""Compiled SQL stays linear in the formula, and shapes keep what defines a metric."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    import yaml
    from metrics_agent.lib import corpus, evidence, sqlshape
    from metrics_agent.lib import rules as R
    from metrics_agent.plugs.readers import bicompile
    from metrics_agent.stages import h1_shapes

COLUMNS = {'retail.fct_sales': {'amount', 'qty', 'unit_price', 'channel'}}


def compile_formula(formula, dialect='snowflake'):
    unit = {'unit_id': 'u', 'warehouse_tables': ['retail.fct_sales'], 'group_by': ['channel'],
            'measures': [{'name': 'M', 'aggregation': None, 'formula_language': 'dax', 'formula': formula}],
            'filters': [], 'top_n': None, 'join_graph': []}
    return bicompile.compile_unit(unit, RULES, dialect, columns=COLUMNS,
                                  table_of={'sales': 'retail.fct_sales'})


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class CompiledSizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RULES
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            RULES = R.load(bicompile.RULES)

    def test_a_long_dax_sum_compiles_linearly(self):
        terms = ['SUM(Sales[amount])', 'SUM(Sales[qty])', 'SUM(Sales[unit_price])'] * 5
        result = compile_formula(' + '.join(terms[:14]))
        self.assertEqual(result.coverage, 'translated', result.reason)
        self.assertLess(len(result.sql), 5000)

    def test_a_flattened_sum_keeps_dax_blank_arithmetic(self):
        result = compile_formula('SUM(Sales[amount]) + SUM(Sales[qty]) - SUM(Sales[unit_price])', 'duckdb')
        self.assertEqual(result.coverage, 'translated', result.reason)
        db = sqlite3.connect(':memory:')
        db.execute("ATTACH ':memory:' AS retail")
        db.execute('CREATE TABLE retail.fct_sales (channel TEXT, amount REAL, qty REAL, unit_price REAL)')
        db.executemany('INSERT INTO retail.fct_sales VALUES (?, ?, ?, ?)',
                       [('blank', None, None, None), ('one', 1, None, None), ('mixed', None, 2, 3)])
        got = dict(db.execute(result.sql).fetchall())
        self.assertEqual(got, {'blank': None, 'one': 1, 'mixed': -1})

    def test_a_statement_too_large_to_run_is_refused(self):
        result = compile_formula(' / '.join(['SUM(Sales[amount])'] * 10))
        self.assertEqual(result.coverage, 'refused')
        self.assertIn('too large', result.reason)


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class ShapeIdentityTest(unittest.TestCase):
    def key(self, sql):
        tree = sqlshape.parse(sql, 'snowflake')
        return h1_shapes.skeleton({'tables': ['retail.opps'], 'feat': corpus.feat(tree, sql)}, tree)

    def test_a_label_inside_the_measure_is_a_different_metric(self):
        won = "SELECT SUM(CASE WHEN stage = 'Won' THEN amt END) FROM retail.opps"
        lost = "SELECT SUM(CASE WHEN stage = 'Lost' THEN amt END) FROM retail.opps"
        self.assertNotEqual(self.key(won), self.key(lost))

    def test_a_grain_is_a_different_metric(self):
        month = "SELECT DATE_TRUNC('month', d), SUM(amt) FROM retail.opps GROUP BY DATE_TRUNC('month', d)"
        week = "SELECT DATE_TRUNC('week', d), SUM(amt) FROM retail.opps GROUP BY DATE_TRUNC('week', d)"
        self.assertNotEqual(self.key(month), self.key(week))

    def test_filter_values_still_collapse_into_one_shape(self):
        emea = "SELECT SUM(amt) FROM retail.opps WHERE region = 'EMEA' LIMIT 10"
        apac = "SELECT SUM(amt) FROM retail.opps WHERE region = 'APAC' LIMIT 100"
        self.assertEqual(self.key(emea), self.key(apac))


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class TableauDeclaredDateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CompiledSizeTest.setUpClass()

    def compile(self, types):
        unit = {'unit_id': 'u', 'warehouse_tables': ['retail.fct_sales'], 'group_by': ['channel'],
                'measures': [{'name': 'first_month', 'aggregation': 'custom',
                              'formula_language': 'tableau-calc',
                              'formula': "MIN(DATETRUNC('month', [order_date]))"}],
                'filters': [], 'top_n': None, 'join_graph': []}
        return bicompile.compile_unit(unit, RULES, 'duckdb',
                                      columns={'retail.fct_sales': {'channel', 'order_date'}},
                                      types=types)

    def test_a_field_declared_a_date_is_read_as_one_when_it_holds_text(self):
        import duckdb
        result = self.compile({('retail.fct_sales', 'order_date'): 'date',
                               ('fct_sales', 'order_date'): 'date'})
        self.assertEqual(result.coverage, 'translated', result.reason)
        self.assertIn('engine.tableau_declared_date', result.rules_used)
        con = duckdb.connect()
        con.execute('CREATE SCHEMA retail')
        con.execute('CREATE TABLE retail.fct_sales (channel VARCHAR, order_date VARCHAR)')
        con.execute("INSERT INTO retail.fct_sales VALUES ('web', '2025-06-06'), ('web', '2025-07-02')")
        self.assertEqual(str(con.execute(result.sql).fetchall()[0][1])[:10], '2025-06-01')

    def test_an_undeclared_field_compiles_as_before(self):
        result = self.compile(None)
        self.assertEqual(result.coverage, 'translated', result.reason)
        self.assertNotIn('CAST(', result.sql)

    def test_a_month_pill_on_custom_sql_text_runs(self):
        import duckdb
        from metrics_agent import readers
        native = 'SELECT substr(order_ts, 1, 10) AS order_date, amount FROM shop.orders'
        col = {'caption': None, 'default_format': None, 'description': '', 'role': None}
        facts = {'vendor': 'tableau', 'model_name': 'm', 'relationships': [], 'usage': [],
                 'assets': [], 'parameters': [],
                 'measures': [{'name': 'Sales', 'formula': 'SUM([amount])',
                               'formula_language': 'tableau-calc', 'home_table': 'Orders'}],
                 'tables': [{'name': 'Orders', 'logical_name': 'Orders', 'description': '',
                             'is_hidden': False,
                             'source_lineage': {'connection': 'federated', 'database': None, 'schema': None,
                                                'object': None, 'published_datasource': False,
                                                'object_kind': 'custom-sql', 'native_sql': native,
                                                'raw_expression': native, 'statement_is_source': True,
                                                'reads': ['shop.orders'], 'native_columns': [
                                                    {'name': 'order_date', 'column': 'order_date'},
                                                    {'name': 'amount', 'column': 'amount'}]},
                             'columns': [
                                 {**col, 'name': 'order_date', 'data_type': 'date', 'is_calculated': False,
                                  'formula': None, 'formula_language': None, 'source_column': 'order_date'},
                                 {**col, 'name': 'amount', 'data_type': 'real', 'is_calculated': False,
                                  'formula': None, 'formula_language': None, 'source_column': 'amount'},
                                 {**col, 'name': 'MONTH-TRUNC(order_date)', 'data_type': None,
                                  'is_calculated': True, 'formula': "DATETRUNC('month', [order_date])",
                                  'formula_language': 'tableau-calc', 'source_column': None}]}]}
        unit = {'unit_id': 'u', 'warehouse_tables': ['Orders'], 'group_by': ['MONTH-TRUNC(order_date)'],
                'measures': [{'name': 'Sales', 'formula': 'SUM([amount])',
                              'formula_language': 'tableau-calc', 'aggregation': 'custom'}],
                'filters': [], 'top_n': None, 'join_graph': [], 'expression_language': 'tableau-calc',
                'native_source': {'logical': 'Orders', 'sql': native,
                                  'columns': {'order_date': 'order_date', 'amount': 'amount'}}}
        reader = readers.compiler('tableau')
        result = reader.compile_unit(unit, RULES, 'duckdb', model=reader.prepare(facts))
        self.assertEqual(result.coverage, 'translated', result.reason)
        con = duckdb.connect()
        con.execute('CREATE SCHEMA shop')
        con.execute('CREATE TABLE shop.orders (order_ts VARCHAR, amount DOUBLE)')
        con.execute("INSERT INTO shop.orders VALUES ('2025-06-06 15:48:50', 2), ('2025-06-20 09:00:00', 3)")
        self.assertEqual([r[1] for r in con.execute(result.sql).fetchall()], [5])
