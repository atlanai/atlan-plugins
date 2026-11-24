"""Three DAX forms the compiler wrote as SQL no warehouse runs, each now either compiled to
Power BI's own answer or refused by name:

- A FILTER's test that reads a column of the iterated row (`FILTER(Customers,
  Customers[Region] = "North")`, `FILTER(VALUES(Customers[Name]), ISBLANK(Customers[Name]))`)
  is the member's one value of it, not a column the per-member statement does not group. It is
  refused by name when the members' key is not known to be unique.
- A date in `+` or `-` is a datetime in DAX, counted in days from 30 December 1899; the
  compiler does not carry that type, so it is refused by name (it was COALESCE(date, 0)).
- A text literal in arithmetic is the number DAX converts it to when it is whole digits
  ("3" * 2 is 6); any other text, and a text read as a date (YEAR("3/5/2024")), depends on the
  model's culture and is refused by name.
"""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))
DUCKDB = importlib.util.find_spec('duckdb') is not None

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    import yaml
    from metrics_agent.lib import evidence
    from metrics_agent.lib import rules as R
    from metrics_agent.plugs.readers import bicompile

MEASURES = {
    'amount': 'SUM ( Orders[Amount] )',
    'north': 'SUMX ( FILTER ( Customers, Customers[Region] = "North" ), [Amount] )',
    'blank name': 'SUMX ( FILTER ( VALUES ( Customers[Name] ), ISBLANK ( Customers[Name] ) ), '
                  '[Amount] )',
    'named': 'SUMX ( FILTER ( VALUES ( Customers[Name] ), NOT ISBLANK ( Customers[Name] ) ), '
             '[Amount] )',
    'segment x': 'SUMX ( FILTER ( Segments, Segments[Label] = "x" ), [Amount] )',
    'text times': '"3" * 2',
    'text plus text': '"1" + "2"',
    'text negated': '-"4"',
    'text divisor': '10 / "4"',
    'text plus number': '"3" + 1',
    'text decimal': '"2.5" * 2',
    'date plus fraction': 'DATE ( 2024, 1, 1 ) + 0.5',
    'date minus date': 'DATE ( 2024, 3, 1 ) - DATE ( 2024, 2, 1 )',
    'year of text': 'YEAR ( "3/5/2024" )',
    'year of date': 'YEAR ( DATE ( 2024, 3, 5 ) )',
}
GRAPH = [{'from_table': 'shop.sales.orders', 'from_column': 'customer_id',
          'to_table': 'shop.sales.customers', 'to_column': 'customer_id',
          'cardinality': 'M:1', 'cross_filter': 'single', 'is_active': True,
          'kind': 'relationship'},
         {'from_table': 'shop.sales.orders', 'from_column': 'segment_id',
          'to_table': 'shop.sales.segments', 'to_column': 'segment_id',
          'cardinality': 'M:M', 'cross_filter': 'single', 'is_active': True,
          'kind': 'relationship'}]
COLUMNS = {'shop.sales.orders': {'order_id', 'customer_id', 'segment_id', 'amount'},
           'shop.sales.customers': {'customer_id', 'name', 'region'},
           'shop.sales.segments': {'segment_id', 'label'}}
TABLE_OF = {'orders': 'shop.sales.orders', 'customers': 'shop.sales.customers',
            'segments': 'shop.sales.segments'}


@unittest.skipUnless(DEPENDENCIES, 'the engine dependencies are not installed')
class DaxConformanceSqlErrorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            cls.rules = R.load(bicompile.RULES)

    def compile(self, name):
        unit = {'unit_id': 'u', 'warehouse_tables': list(COLUMNS), 'group_by': [],
                'group_by_tables': [], 'filters': [], 'join_graph': GRAPH,
                'measures': [{'name': name, 'aggregation': None, 'formula_language': 'dax',
                              'formula': MEASURES[name.lower()]}]}
        return bicompile.compile_unit(unit, self.rules, 'duckdb', columns=COLUMNS,
                                      table_of=TABLE_OF, measures=MEASURES)

    def sql(self, name):
        got = self.compile(name)
        self.assertTrue(got.sql, got.reason)
        return got.sql

    def refused(self, name, why):
        got = self.compile(name)
        self.assertFalse(got.sql, got.sql)
        self.assertIn(why, str(got.reason))

    def run_sql(self, sql):
        import duckdb
        db = duckdb.connect()
        db.execute("ATTACH ':memory:' AS shop")
        db.execute('CREATE SCHEMA shop.sales')
        db.execute('CREATE TABLE shop.sales.customers (customer_id INTEGER, name VARCHAR, '
                   'region VARCHAR)')
        db.execute("INSERT INTO shop.sales.customers VALUES (1, 'Ann', 'North'), "
                   "(2, 'Bo', 'South'), (3, NULL, 'North')")
        db.execute('CREATE TABLE shop.sales.orders (order_id INTEGER, customer_id INTEGER, '
                   'segment_id INTEGER, amount DOUBLE)')
        db.execute('INSERT INTO shop.sales.orders VALUES (1, 1, 1, 10), (2, 1, 1, 5), '
                   '(3, 2, 1, 7), (4, 3, 1, 4)')
        return db.execute(sql).fetchall()

    # -- a bare column of the iterated row
    def test_row_column_is_the_members_one_value(self):
        self.assertIn('MIN(', self.sql('North'))
        self.assertIn('MIN(', self.sql('Blank Name'))

    @unittest.skipUnless(DUCKDB, 'duckdb is not installed')
    def test_row_column_runs_and_gives_the_members_answer(self):
        # North: Ann (15) and the unnamed customer (4); a blank name: the unnamed one (4).
        self.assertEqual(self.run_sql(self.sql('North')), [(19.0,)])
        self.assertEqual(self.run_sql(self.sql('Blank Name')), [(4.0,)])
        self.assertEqual(self.run_sql(self.sql('Named')), [(22.0,)])

    def test_row_column_of_a_key_not_known_unique_is_refused(self):
        self.refused('Segment X', 'not known to be unique')

    # -- a date in + or -
    def test_date_arithmetic_is_refused_by_name(self):
        for name in ('Date Plus Fraction', 'Date Minus Date'):
            with self.subTest(name=name):
                self.refused(name, 'a date in + or -')

    # -- text as a number or a date
    def test_whole_digit_text_is_its_number(self):
        for name, value in (('Text Times', 6), ('Text Plus Text', 3), ('Text Negated', -4),
                            ('Text Divisor', 2.5), ('Text Plus Number', 4)):
            with self.subTest(name=name):
                sql = self.sql(name)
                self.assertNotIn("'", sql.split('FROM')[0])
                if DUCKDB:
                    self.assertEqual(self.run_sql(sql)[0][0], value)

    def test_text_with_a_separator_is_refused_by_name(self):
        self.refused('Text Decimal', "by the model's culture")

    def test_text_read_as_a_date_is_refused_by_name(self):
        self.refused('Year of Text', 'reads text as a date')
        self.assertIn('YEAR(', self.sql('Year of Date'))


if __name__ == '__main__':
    unittest.main()
