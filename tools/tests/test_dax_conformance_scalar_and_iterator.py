"""Three places where DAX reads a formula otherwise than the compiler did, each pinned to
Power BI's own answer:

- `^` binds tighter than a unary minus: `-2^2` is -4, `(-2)^2` is 4, `2^-1` is 0.5.
- DATE windows a two-digit year (99 is 1999); a year of 100 or more is the year itself.
- An iterator whose expression aggregates with no context transition
  (`SUMX(Customers, SUM(Orders[Amount]))`) adds the visual's whole total once per member; it
  is refused by name rather than compiled as if CALCULATE were there.
"""
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
    'amount': 'SUM ( Orders[Amount] )',
    'neg power': '-2^2',
    'neg power odd': '-2^3',
    'paren power': '(-2)^2',
    'signed exponent': '2^-1',
    'minus power': '0 - 2^2',
    'year 99': 'DATE ( 99, 1, 1 )',
    'year 100': 'DATE ( 100, 1, 1 )',
    'year 2024': 'DATE ( 2024, 3, 1 )',
    'year of max': 'DATE ( MAX ( Orders[Yr] ), 1, 1 )',
    'no transition': 'SUMX ( Customers, SUM ( Orders[Amount] ) )',
    'with calculate': 'SUMX ( Customers, CALCULATE ( SUM ( Orders[Amount] ) ) )',
    'with measure': 'SUMX ( Customers, [Amount] )',
}
GRAPH = [{'from_table': 'shop.sales.orders', 'from_column': 'customer_id',
          'to_table': 'shop.sales.customers', 'to_column': 'customer_id',
          'cardinality': 'M:1', 'cross_filter': 'single', 'is_active': True,
          'kind': 'relationship'}]


@unittest.skipUnless(DEPENDENCIES, 'the engine dependencies are not installed')
class DaxConformanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            cls.rules = R.load(bicompile.RULES)

    def compile(self, name, group_by=(), group_tables=()):
        unit = {'unit_id': 'u', 'warehouse_tables': ['shop.sales.orders', 'shop.sales.customers'],
                'group_by': list(group_by), 'group_by_tables': list(group_tables),
                'filters': [], 'join_graph': GRAPH,
                'measures': [{'name': name, 'aggregation': None, 'formula_language': 'dax',
                              'formula': MEASURES[name.lower()]}]}
        return bicompile.compile_unit(
            unit, self.rules, 'duckdb',
            columns={'shop.sales.orders': {'order_id', 'customer_id', 'amount', 'yr'},
                     'shop.sales.customers': {'customer_id', 'country'}},
            table_of={'orders': 'shop.sales.orders', 'customers': 'shop.sales.customers'},
            measures=MEASURES)

    def sql(self, name, **kw):
        got = self.compile(name, **kw)
        self.assertTrue(got.sql, got.reason)
        return ' '.join(got.sql.split())

    # -- `^` before the sign
    def test_power_binds_tighter_than_unary_minus(self):
        self.assertIn('-POWER(2, 2)', self.sql('Neg Power'))
        self.assertIn('-POWER(2, 3)', self.sql('Neg Power Odd'))

    def test_parenthesised_base_and_signed_exponent(self):
        self.assertIn('POWER((-2), 2)', self.sql('Paren Power'))
        self.assertIn('POWER(2, -1)', self.sql('Signed Exponent'))
        self.assertIn('POWER(2, 2)', self.sql('Minus Power'))

    # -- DATE's year
    def test_two_digit_year_is_windowed(self):
        self.assertIn('make_date(1999, 1, 1)', self.sql('Year 99'))

    def test_year_of_100_and_up_is_kept(self):
        self.assertIn('make_date(100, 1, 1)', self.sql('Year 100'))
        self.assertIn('make_date(2024, 1, 1)', self.sql('Year 2024'))

    def test_computed_year_is_windowed_in_sql(self):
        sql = self.sql('Year of Max')
        self.assertIn('BETWEEN 50 AND 99 THEN', sql)
        self.assertIn('+ 1900', sql)

    def test_dax_year(self):
        self.assertEqual([bicompile.dax_year(y) for y in (0, 49, 50, 99, 100, 1899, 2024)],
                         [2000, 2049, 1950, 1999, 100, 1899, 2024])

    # -- an iterator with no context transition
    def test_iterator_without_transition_is_refused_by_name(self):
        got = self.compile('No Transition', group_by=['country'],
                           group_tables=['shop.sales.customers'])
        self.assertFalse(got.sql)
        self.assertIn('no context transition', str(got.reason))

    def test_iterator_with_transition_still_compiles(self):
        for name in ('With Calculate', 'With Measure'):
            with self.subTest(name=name):
                self.sql(name, group_by=['country'], group_tables=['shop.sales.customers'])


if __name__ == '__main__':
    unittest.main()
