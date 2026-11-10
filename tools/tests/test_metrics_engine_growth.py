"""Compiled SQL stays linear in the formula, and shapes keep what defines a metric."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/analytics-metrics/modules/metrics/engine'
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
