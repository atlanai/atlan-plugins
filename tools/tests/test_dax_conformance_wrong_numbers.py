"""Four DAX forms the compiler answered with a number Power BI does not give, each now either
compiled to Power BI's own answer or refused by name:

- CROSSFILTER with a one-way direction on a one-to-one relationship is an error in Power BI,
  as is the many side of a many-to-one relationship alone filtering the one side; OneWay on a
  many-to-many relationship names no filtering side. Each is refused by name (the join plan
  read them all as the relationship's single direction).
- A row whose every value is empty is left out of a visual. Where a value is computed across
  the visual's rows (a share of an ALLSELECTED total), a HAVING cannot test it, and the row
  stayed with a NULL; it is now left out of the finished rows.
- ISFILTERED, ISCROSSFILTERED and their kin read the visual's context. A measure of nothing
  else is a statement over the table its test names; on another table, a grouping column that
  does not filter that table is listed apart, out of the context the test reads (it answered
  FALSE for a grouped column). Where such a test sits beside a column listed apart from its
  rows, it is refused by name.
  The table's blank member (a key a relationship into it holds and it lacks) is a row of the
  visual too, with the test's value there.
- Along a both-ways path, a row with no row of the table reached on its many side reaches no
  member of the tables beyond it; it was kept, and counted for their blank member (a shop with
  no orders gave its country to the unknown customer).
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

ONE_TO_ONE = 'CROSSFILTER ( Profiles[shop_id], Shops[shop_id], {} )'
MEASURES = {
    'amount': 'SUM ( Orders[amount] )',
    # -- CROSSFILTER's one-way directions
    'profiles one way': 'CALCULATE ( COUNTROWS ( Profiles ), '
                        + ONE_TO_ONE.format('OneWay') + ', Shops[country_id] = 2 )',
    'profiles left': 'CALCULATE ( COUNTROWS ( Profiles ), '
                     + ONE_TO_ONE.format('OneWay_LeftFiltersRight') + ', Shops[country_id] = 2 )',
    'profiles right': 'CALCULATE ( COUNTROWS ( Profiles ), '
                      + ONE_TO_ONE.format('OneWay_RightFiltersLeft') + ', Shops[country_id] = 2 )',
    'profiles both': 'CALCULATE ( COUNTROWS ( Profiles ), '
                     + ONE_TO_ONE.format('Both') + ', Shops[country_id] = 2 )',
    'orders one way': 'CALCULATE ( COUNTROWS ( Orders ), CROSSFILTER ( Orders[item_id], '
                      'Items[item_id], OneWay ), Items[colour] = "Red" )',
    'orders one side': 'CALCULATE ( COUNTROWS ( Orders ), CROSSFILTER ( Orders[item_id], '
                       'Items[item_id], OneWay_RightFiltersLeft ), Items[colour] = "Red" )',
    'orders many side': 'CALCULATE ( COUNTROWS ( Orders ), CROSSFILTER ( Orders[item_id], '
                        'Items[item_id], OneWay_LeftFiltersRight ), Items[colour] = "Red" )',
    'plans one way': 'CALCULATE ( COUNTROWS ( Plans ), CROSSFILTER ( Plans[colour], '
                     'Items[colour], OneWay ), Items[colour] = "Red" )',
    # -- an empty row beside a value computed across the visual's rows
    'red share': 'CALCULATE ( DIVIDE ( [Amount], CALCULATE ( [Amount], ALLSELECTED () ) ), '
                 'Items[colour] = "Red" )',
    'customers': 'COUNTROWS ( Customers )',
    # -- tests of the visual's context
    'town filtered': 'ISFILTERED ( Customers[town] )',
    'orders crossfiltered': 'ISCROSSFILTERED ( Orders[qty] )',
    'colour filtered': 'ISFILTERED ( Items[colour] )',
    'priced when town': 'IF ( ISFILTERED ( Customers[town] ), SUM ( Items[price] ) )',
    # -- a both-ways path
    'countries reached': 'CALCULATE ( COUNTROWS ( Countries ), CROSSFILTER ( Orders[shop_id], '
                         'Shops[shop_id], Both ), CROSSFILTER ( Shops[country_id], '
                         'Countries[country_id], Both ) )',
}


def _edge(a, ac, b, bc, cardinality='M:1', cross='single'):
    return {'from_table': f'shop.sales.{a}', 'from_column': ac, 'to_table': f'shop.sales.{b}',
            'to_column': bc, 'cardinality': cardinality, 'cross_filter': cross,
            'is_active': True, 'kind': 'relationship'}


GRAPH = [_edge('orders', 'customer_id', 'customers', 'customer_id'),
         _edge('orders', 'shop_id', 'shops', 'shop_id'),
         _edge('orders', 'item_id', 'items', 'item_id'),
         _edge('shops', 'country_id', 'countries', 'country_id'),
         _edge('profiles', 'shop_id', 'shops', 'shop_id', '1:1', 'both'),
         _edge('plans', 'colour', 'items', 'colour', 'M:M')]
COLUMNS = {'shop.sales.orders': {'order_id', 'customer_id', 'shop_id', 'item_id', 'amount', 'qty'},
           'shop.sales.customers': {'customer_id', 'town'},
           'shop.sales.shops': {'shop_id', 'country_id'},
           'shop.sales.countries': {'country_id', 'country'},
           'shop.sales.items': {'item_id', 'colour', 'price'},
           'shop.sales.profiles': {'shop_id', 'format'},
           'shop.sales.plans': {'colour', 'planned'}}
TABLE_OF = {name.rsplit('.', 1)[1]: name for name in COLUMNS}
DATA = [
    'CREATE TABLE shop.sales.customers (customer_id INTEGER, town VARCHAR)',
    "INSERT INTO shop.sales.customers VALUES (1, 'Alba'), (2, 'Brin'), (3, 'Cove')",
    'CREATE TABLE shop.sales.countries (country_id INTEGER, country VARCHAR)',
    "INSERT INTO shop.sales.countries VALUES (1, 'North'), (2, 'South'), (3, 'East')",
    'CREATE TABLE shop.sales.shops (shop_id INTEGER, country_id INTEGER)',
    # Shop 30 has no orders: nobody reaches its country, East.
    'INSERT INTO shop.sales.shops VALUES (10, 1), (20, 2), (30, 3)',
    'CREATE TABLE shop.sales.items (item_id INTEGER, colour VARCHAR, price DOUBLE)',
    "INSERT INTO shop.sales.items VALUES (1, 'Red', 2), (2, 'Blue', 3)",
    'CREATE TABLE shop.sales.profiles (shop_id INTEGER, format VARCHAR)',
    "INSERT INTO shop.sales.profiles VALUES (10, 'Kiosk'), (20, 'Hall')",
    'CREATE TABLE shop.sales.plans (colour VARCHAR, planned DOUBLE)',
    "INSERT INTO shop.sales.plans VALUES ('Red', 5)",
    'CREATE TABLE shop.sales.orders (order_id INTEGER, customer_id INTEGER, shop_id INTEGER, '
    'item_id INTEGER, amount DOUBLE, qty INTEGER)',
    # Customer 1 buys red and blue in two countries, customer 2 blue only, customer 3 nothing;
    # customer 99 is on no customer row (the blank customer) and buys in the South.
    'INSERT INTO shop.sales.orders VALUES (1, 1, 10, 1, 30, 1), (2, 1, 20, 2, 10, 1), '
    '(3, 2, 10, 2, 40, 2), (4, 99, 20, 1, 70, 1)',
]


@unittest.skipUnless(DEPENDENCIES, 'the engine dependencies are not installed')
class DaxConformanceWrongNumbersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            cls.rules = R.load(bicompile.RULES)

    def compile(self, names, group=None, first='orders'):
        tables = [f'shop.sales.{first}'] + [t for t in COLUMNS if t != f'shop.sales.{first}']
        unit = {'unit_id': 'u', 'warehouse_tables': tables, 'filters': [], 'join_graph': GRAPH,
                'group_by': [group[1]] if group else [],
                'group_by_tables': [f'shop.sales.{group[0]}'] if group else [],
                'blank_rows': 'hidden',
                'measures': [{'name': name, 'aggregation': None, 'formula_language': 'dax',
                              'formula': MEASURES[name.lower()]} for name in names]}
        return bicompile.compile_unit(unit, self.rules, 'duckdb', columns=COLUMNS,
                                      table_of=TABLE_OF, measures=MEASURES)

    def sql(self, names, **kw):
        got = self.compile(names, **kw)
        self.assertTrue(got.sql, got.reason)
        self.assertEqual(got.coverage, 'translated', got.reason)
        return got.sql

    def refused(self, names, why, **kw):
        got = self.compile(names, **kw)
        self.assertNotEqual(got.coverage, 'translated', got.sql)
        self.assertIn(why, str(got.reason))

    def rows(self, sql):
        import duckdb
        db = duckdb.connect()
        db.execute("ATTACH ':memory:' AS shop")
        db.execute('CREATE SCHEMA shop.sales')
        for statement in DATA:
            db.execute(statement)
        return sorted(db.execute(sql).fetchall(), key=lambda r: tuple(str(v) for v in r))

    # -- CROSSFILTER's one-way directions
    def test_one_way_on_a_one_to_one_relationship_is_refused(self):
        for name in ('Profiles One Way', 'Profiles Left', 'Profiles Right'):
            with self.subTest(name=name):
                self.refused([name], 'one-way direction on a one-to-one relationship',
                             first='profiles')

    def test_both_on_a_one_to_one_relationship_still_compiles(self):
        sql = self.sql(['Profiles Both'], first='profiles')
        if DUCKDB:
            self.assertEqual(self.rows(sql), [(1,)])

    def test_the_many_side_alone_filtering_the_one_side_is_refused(self):
        self.refused(['Orders Many Side'], 'the many side of a many-to-one relationship')

    def test_one_way_from_the_one_side_still_compiles(self):
        for name in ('Orders One Way', 'Orders One Side'):
            with self.subTest(name=name):
                sql = self.sql([name])
                if DUCKDB:
                    self.assertEqual(self.rows(sql), [(2,)])

    def test_one_way_on_a_many_to_many_relationship_is_refused(self):
        self.refused(['Plans One Way'], 'OneWay on a many-to-many relationship', first='plans')

    # -- an empty row beside a value computed across the visual's rows
    @unittest.skipUnless(DUCKDB, 'duckdb is not installed')
    def test_a_row_with_only_an_empty_windowed_value_is_left_out(self):
        # Red: Alba 30 and the blank customer 70. Brin bought no red: no share, and no row.
        got = self.rows(self.sql(['Red Share'], group=('customers', 'town')))
        self.assertEqual([(town, round(share, 2)) for town, share in got],
                         [('Alba', 0.3), (None, 0.7)])

    def test_the_empty_rows_are_left_out_after_the_window(self):
        sql = self.sql(['Red Share'], group=('customers', 'town'))
        self.assertIn(' OVER (', sql)
        self.assertNotIn('HAVING', sql)
        self.assertRegex(sql, r'\) b_\nWHERE  \(b_\.\w+ IS NOT NULL\)$')

    # -- tests of the visual's context
    @unittest.skipUnless(DUCKDB, 'duckdb is not installed')
    def test_context_tests_read_the_grouping_whatever_table_is_first(self):
        names = ['Amount', 'Town Filtered', 'Orders Crossfiltered', 'Colour Filtered']
        for first in ('items', 'orders', 'customers'):
            with self.subTest(first=first):
                got = self.rows(self.sql(names, group=('customers', 'town'), first=first))
                by_town = {r[0]: set(r[1:]) for r in got}
                # Never BLANK, so every town is listed, Cove (no orders) and the blank included.
                self.assertEqual(set(by_town), {'Alba', 'Brin', 'Cove', None})
                self.assertEqual(by_town['Alba'], {40.0, True, False})
                self.assertEqual(by_town['Cove'], {None, True, False})
                for row in got:
                    self.assertEqual(sorted(v for v in row[1:] if isinstance(v, bool)),
                                     [False, True, True])

    @unittest.skipUnless(DUCKDB, 'duckdb is not installed')
    def test_the_grouped_tables_blank_member_shows_a_test_that_is_never_blank(self):
        # Order 4's customer is on no customer row: the towns list a blank, where the town is
        # filtered as on every other row. One blank row, also where a customer has no town.
        sql = self.sql(['Town Filtered'], group=('customers', 'town'), first='customers')
        self.assertEqual(self.rows(sql), [('Alba', True), ('Brin', True), ('Cove', True),
                                          (None, True)])
        DATA.append('INSERT INTO shop.sales.customers VALUES (4, NULL)')
        try:
            self.assertEqual(len(self.rows(sql)), 4)
        finally:
            DATA.pop()

    @unittest.skipUnless(DUCKDB, 'duckdb is not installed')
    def test_the_blank_member_shows_the_test_beside_the_tables_own_aggregate(self):
        # The aggregate reads the table's rows, of which the blank member has none; the test
        # is a statement of its own, so the blank row is listed with it.
        sql = self.sql(['Customers', 'Town Filtered'], group=('customers', 'town'),
                       first='customers')
        self.assertEqual(self.rows(sql), [('Alba', 1, True), ('Brin', 1, True),
                                          ('Cove', 1, True), (None, None, True)])

    def test_a_context_test_of_a_table_listed_apart_is_refused(self):
        self.refused(['Priced When Town'], 'tests the context of customers',
                     group=('customers', 'town'), first='items')

    # -- a both-ways path
    @unittest.skipUnless(DUCKDB, 'duckdb is not installed')
    def test_a_shop_with_no_orders_gives_its_country_to_no_customer(self):
        got = self.rows(self.sql(['Countries Reached'], group=('customers', 'customer_id'),
                                 first='countries'))
        # The blank customer (order 4) reaches the South only, not shop 30's East as well.
        self.assertEqual(got, [(1, 2), (2, 1), (None, 1)])

    def test_the_hop_to_the_many_side_is_an_inner_join(self):
        sql = self.sql(['Countries Reached'], group=('customers', 'customer_id'),
                       first='countries')
        self.assertIn('INNER JOIN shop.sales.orders', sql)
        self.assertNotIn('LEFT JOIN shop.sales.orders', sql)


if __name__ == '__main__':
    unittest.main()
