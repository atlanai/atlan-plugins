"""A CALCULATE that removes the visual's filters (ALL, ALLEXCEPT) removes them from its own
aggregate only: a measure beside it, or the numerator of a share over it, still reads the
visual's rows with the page filter and the slicer applied."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    import yaml
    from metrics_agent.lib import evidence
    from metrics_agent.lib import rules as R
    from metrics_agent.plugs.readers import bicompile

MEASURES = {
    'orders': 'COUNTROWS ( Orders )',
    'all orders': 'CALCULATE ( [Orders], ALL ( Orders ) )',
    'share of all': 'DIVIDE ( [Orders], [All Orders] )',
    'orders in region': 'CALCULATE ( [Orders], ALLEXCEPT ( Orders, Orders[Region] ) )',
    'share of region': 'DIVIDE ( [Orders], [Orders in Region] )',
}
FILTERS = [
    {'scope': 'page', 'stage': 'dimension', 'column': 'status_cd', 'op': 'in',
     'value': ['Open', 'Paid'], 'table': 'shop.sales.orders'},
    {'scope': 'slicer', 'stage': 'dimension', 'column': 'segment', 'op': 'in',
     'value': 'Retail', 'table': 'shop.sales.customers'},
]
GRAPH = [{'from_table': 'shop.sales.orders', 'from_column': 'customer_id',
          'to_table': 'shop.sales.customers', 'to_column': 'customer_id',
          'cardinality': 'M:1', 'cross_filter': 'single', 'is_active': True,
          'kind': 'relationship'}]


@unittest.skipUnless(DEPENDENCIES, 'the engine dependencies are not installed')
class AllKeepsNumeratorFiltersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            cls.rules = R.load(bicompile.RULES)

    def compile(self, names, group_by=()):
        unit = {'unit_id': 'u', 'warehouse_tables': ['shop.sales.orders', 'shop.sales.customers'],
                'group_by': list(group_by), 'group_by_tables': ['shop.sales.orders'] * len(group_by),
                'filters': FILTERS, 'join_graph': GRAPH,
                'measures': [{'name': n, 'aggregation': None, 'formula_language': 'dax',
                              'formula': MEASURES[n.lower()]} for n in names]}
        got = bicompile.compile_unit(
            unit, self.rules, 'snowflake',
            columns={'shop.sales.orders': {'order_id', 'customer_id', 'status_cd', 'region'},
                     'shop.sales.customers': {'customer_id', 'segment'}},
            table_of={'orders': 'shop.sales.orders', 'customers': 'shop.sales.customers'},
            measures=MEASURES)
        self.assertTrue(got.sql, got.reason)
        return ' '.join(got.sql.split())

    @staticmethod
    def outer_where(sql):
        # The statement's own WHERE: the last one, after every context statement it joins.
        return sql.rsplit('WHERE', 1)[-1] if 'WHERE' in sql else ''

    def test_share_of_all_on_a_card_keeps_the_numerators_filters(self):
        sql = self.compile(['Share of All'])
        where = self.outer_where(sql)
        self.assertIn("t.status_cd IN ('Open', 'Paid')", where)
        self.assertIn("segment IN ('Retail')", where)
        # The denominator is its own statement over every row.
        self.assertRegex(sql, r"CROSS JOIN \( SELECT NULLIF\(COUNT\(\*\), 0\) AS v FROM shop\.sales\.orders t "
                              r"LEFT JOIN shop\.sales\.customers j1 ON j1\.customer_id = t\.customer_id \) c1")
        self.assertIn('MAX(c1.v)', sql)

    def test_share_of_allexcept_keeps_the_numerators_filters(self):
        sql = self.compile(['Share of Region', 'Orders', 'Orders in Region'], group_by=['region'])
        self.assertIn("t.status_cd IN ('Open', 'Paid')", sql)
        self.assertIn("segment IN ('Retail')", sql)
        # The per-region total reads the rows without the page filter and the slicer.
        self.assertRegex(sql, r"LEFT JOIN \( SELECT t\.region AS k0, NULLIF\(COUNT\(\*\), 0\) AS v "
                              r"FROM shop\.sales\.orders t LEFT JOIN shop\.sales\.customers j1 "
                              r"ON j1\.customer_id = t\.customer_id GROUP BY t\.region \) c\d")

    def test_a_measure_that_is_wholly_the_calculate_still_drops_them(self):
        # Every value shown ignores the filters: the statement is the unit without them.
        sql = self.compile(['All Orders'])
        self.assertNotIn('status_cd', sql)
        self.assertNotIn('segment', sql)


if __name__ == '__main__':
    unittest.main()
