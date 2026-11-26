"""A Databricks metric view read into `bi_facts`, declarations included, on synthetic views.

Each case is a rule the reader holds: what it binds, what it replaces, what it refuses by name.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema', 'duckdb'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    sys.path.insert(0, str(ENGINE / 'packages/metrics-agent-databricks/src'))
    import jsonschema
    import sqlglot
    from metrics_agent.lib import declared
    from metrics_agent_databricks import metric_view as MV
    from metrics_agent_databricks.plug import DatabricksDirect

ORDERS = '''
    version: 1.1
    source: demo.sales.orders
    comment: One row per order.
    joins:
      - name: customer
        source: demo.sales.customer
        on: source.o_custkey = customer.c_custkey
        joins:
          - name: nation
            source: demo.sales.nation
            'on': customer.c_nationkey = nation.n_nationkey
    dimensions:
      - name: order_date
        expr: o_orderdate
        comment: The day the order was placed.
        synonyms: [placed on]
      - name: order_month
        expr: DATE_TRUNC('MONTH', order_date)
      - name: customer_nation
        expr: customer.nation.n_name
        display_name: Customer nation
      - name: Order Status
        expr: CASE WHEN o_orderstatus = 'F' THEN 'Fulfilled' ELSE 'Open' END
      - name: region
        expr: region
    measures:
      - name: revenue
        expr: SUM(revenue)
        comment: Total order value.
        synonyms: [sales, turnover]
      - name: avg_revenue
        expr: AVG(revenue)
      - name: order_count
        expr: COUNT(1)
      - name: revenue_per_order
        expr: MEASURE(revenue) / MEASURE(order_count)
      - name: statuses
        expr: COUNT(DISTINCT `Order Status`)
      - name: nations
        expr: COUNT(DISTINCT customer.nation.n_name)
'''


def public(value):
    """`value` without the reader's private `_` keys, which A1 reads and never writes."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.startswith('_')}
    return [public(v) for v in value] if isinstance(value, list) else value


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class MetricViewReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.schema = json.loads((ENGINE / 'src/metrics_agent/data/schemas/bi_facts.schema.json').read_text())

    def view(self, text, name='orders_metrics.yaml'):
        path = self.root / name
        path.write_text(textwrap.dedent(text))
        return path

    def read(self, text, name='orders_metrics.yaml'):
        facts = MV.parse(self.view(text, name))
        jsonschema.validate(public(facts), self.schema)
        return facts

    def declared(self, facts):
        return {m['name']: m.get('declared') for m in facts['measures']}

    def notes(self, facts):
        return '\n'.join(facts['_reader_notes'])

    def test_every_column_is_bound_to_its_table_and_the_declaration_is_whole(self):
        facts = self.read(ORDERS)
        self.assertEqual(facts['vendor'], 'databricks-metric-view')
        self.assertEqual([t['logical_name'] for t in facts['tables']], ['source', 'customer', 'customer__nation'])
        d = self.declared(facts)
        self.assertEqual(d['revenue']['entity'], 'demo.sales.orders')
        self.assertEqual(d['revenue']['formula'], 'SUM(demo.sales.orders.revenue)')
        self.assertEqual(d['nations']['formula'], 'COUNT(DISTINCT demo.sales.nation.n_name)')
        self.assertIn('LEFT JOIN demo.sales.customer\n  ON demo.sales.orders.o_custkey = demo.sales.customer.c_custkey',
                      d['nations']['source'])
        self.assertIn('LEFT JOIN demo.sales.nation\n  ON demo.sales.customer.c_nationkey = demo.sales.nation.n_nationkey',
                      d['nations']['source'])
        dims = {x['name']: x for x in d['revenue']['dimensions']}
        self.assertEqual(dims['Order Date']['sql'], 'demo.sales.orders.o_orderdate')
        self.assertEqual(dims['Order Date']['description'], 'The day the order was placed.')
        self.assertEqual(dims['Order Date']['synonyms'], ['placed on'])
        self.assertEqual(dims['Customer Nation']['required_join'], 'customer__nation')
        self.assertEqual(dims['Customer Nation']['synonyms'], ['Customer nation'])
        self.assertEqual(dims['Region']['sql'], 'demo.sales.orders.region')
        self.assertNotIn('Order Month', dims)            # a levelled date: the bare date is the dimension
        measure = next(m for m in facts['measures'] if m['name'] == 'revenue')
        self.assertEqual(measure['synonyms'], ['sales', 'turnover'])
        self.assertEqual(measure['description'], 'Total order value.')
        self.assertEqual([u['measures'] for u in facts['usage']], [[m['name']] for m in facts['measures']])
        self.assertTrue(all(u['container_kind'] == 'perspective' and u['dashboards'] == [] for u in facts['usage']))
        for found in d.values():
            sqlglot.parse_one(f"SELECT {found['formula']} FROM {found['source']}", read='databricks')

    def test_a_measure_with_a_columns_name_is_never_read_as_that_column(self):
        facts = self.read(ORDERS)
        d = self.declared(facts)
        # `revenue: SUM(revenue)` and `avg_revenue: AVG(revenue)`: the column, never the measure.
        self.assertEqual(d['avg_revenue']['formula'], 'AVG(demo.sales.orders.revenue)')
        self.assertTrue(all(m['referable'] is False for m in facts['measures']))
        model = declared.Model.of(facts)
        placed, _joins, _names = declared.place_in_statement(
            model, 'source', [m['formula'] for m in facts['measures'][:2]], 't', 0)
        self.assertEqual(placed, ['SUM(t.revenue)', 'AVG(t.revenue)'])
        # Without the mark, the shared model reads `source.revenue` as the measure.
        marked = [dict(m) for m in facts['measures']]
        for m in marked:
            m.pop('referable')
        unmarked = declared.Model.of({**facts, 'measures': marked})
        self.assertEqual(declared.place_in_statement(unmarked, 'source', ['AVG(source.revenue)'], 't', 0)[0],
                         ['AVG(SUM(t.revenue))'])

    def test_measure_calls_and_quoted_field_names_are_replaced_by_what_they_stand_for(self):
        d = self.declared(self.read(ORDERS))
        self.assertEqual(d['revenue_per_order']['formula'],
                         '(SUM(demo.sales.orders.revenue)) / (COUNT(1))')
        self.assertEqual(d['statuses']['formula'],
                         "COUNT(DISTINCT (CASE WHEN demo.sales.orders.o_orderstatus = 'F' THEN 'Fulfilled' ELSE 'Open' END))")

    def test_the_views_filter_is_written_into_every_aggregate(self):
        facts = self.read(ORDERS.replace("comment: One row per order.",
                                         "filter: o_orderstatus = 'F' AND customer.c_active"))
        d = self.declared(facts)
        cond = "demo.sales.orders.o_orderstatus = 'F' AND demo.sales.customer.c_active"
        self.assertEqual(d['revenue']['formula'], f'SUM(demo.sales.orders.revenue) FILTER(WHERE {cond})')
        self.assertEqual(d['revenue_per_order']['formula'],
                         f'(SUM(demo.sales.orders.revenue) FILTER(WHERE {cond})) / (COUNT(1) FILTER(WHERE {cond}))')
        self.assertIn('LEFT JOIN demo.sales.customer', d['revenue']['source'])   # the filter reads it
        self.assertIn("the view's filter is written into every measure", self.notes(facts))
        own = self.read('''
            version: 1.1
            source: demo.sales.orders
            filter: o_orderstatus = 'F'
            measures:
              - name: big
                expr: COUNT(1) FILTER (WHERE amount > 100)
              - name: odd
                expr: my_udaf(amount)
        ''')
        self.assertEqual(self.declared(own)['big']['formula'],
                         "COUNT(1) FILTER(WHERE (demo.sales.orders.amount > 100) AND (demo.sales.orders.o_orderstatus = 'F'))")
        self.assertNotIn('odd', self.declared(own))
        self.assertIn("measure 'odd' calls MY_UDAF, which may aggregate", self.notes(own))

    DATED = '''
        version: 1.1
        source: demo.sales.orders
        joins:
          - name: customer
            source: demo.sales.customer
            'on': source.o_custkey = customer.c_custkey
        filter: o_orderdate >= DATE'2021-01-01' AND customer.c_active
        measures:
          - name: orders
            expr: COUNT(1)
          - name: year_revenue
            expr: SUM(revenue) FILTER (WHERE o_orderdate BETWEEN '2024-01-01' AND '2024-12-31')
          - name: late
            expr: COUNT(CASE WHEN shipped_at > TIMESTAMP'2024-06-30 23:59:59' THEN 1 END)
          - name: both_sides
            expr: COUNT(CASE WHEN created < '2022-07-01' AND closed >= '2022-07-01' THEN 1 END)
          - name: noted
            expr: COUNT(CASE WHEN note = '2024-01-01 onwards' THEN 1 END)
    '''

    def test_a_fixed_date_is_a_parameter_on_the_declaration_and_stays_in_the_statement(self):
        # Atlan keeps no fixed date in a metric's formula, and one metric that holds one stops
        # the export of every metric in the run. The statement keeps the view's date, so it
        # runs; the declaration names it, with the view's date as the default.
        from metrics_agent.lib import card_sql
        facts = self.read(self.DATED)
        d = self.declared(facts)
        measure = {m['name']: m for m in facts['measures']}
        self.assertIn("source.o_orderdate >= CAST('2021-01-01' AS DATE)", measure['orders']['formula'])
        self.assertEqual(d['orders']['formula'],
                         'COUNT(1) FILTER(WHERE demo.sales.orders.o_orderdate >= CAST({{o_orderdate_from}} AS DATE) '
                         'AND demo.sales.customer.c_active)')
        self.assertEqual(d['orders']['parameters'],
                         [{'name': 'o_orderdate_from', 'datatype': 'date', 'default': '2021-01-01'}])
        # Two dates bound one column from below here, so each of the two also carries its date.
        self.assertEqual({p['name']: p['default'] for p in d['year_revenue']['parameters']},
                         {'o_orderdate_from_2024_01_01': '2024-01-01', 'o_orderdate_to': '2024-12-31',
                          'o_orderdate_from_2021_01_01': '2021-01-01'})
        # A typed value is written as the cast it means: a marker cannot follow the keyword.
        self.assertIn('demo.sales.orders.shipped_at > CAST({{shipped_at_from}} AS TIMESTAMP)', d['late']['formula'])
        self.assertEqual(d['late']['parameters'][0],
                         {'name': 'shipped_at_from', 'datatype': 'timestamp', 'default': '2024-06-30 23:59:59'})
        # One date used two ways is named after the date alone, once.
        self.assertEqual([p['name'] for p in d['both_sides']['parameters']], ['date_2022_07_01', 'o_orderdate_from'])
        self.assertEqual(d['both_sides']['formula'].count('{{date_2022_07_01}}'), 2)
        for name, found in d.items():
            self.assertNotRegex(found['formula'], r"'\d{4}-\d{2}-\d{2}", name)
            used = set(card_sql._MARKER.findall(found['formula']))
            self.assertEqual(used, {p['name'] for p in found['parameters']}, name)
            run = card_sql.bound(f"SELECT {found['formula']} FROM {found['source']}", found['parameters'])
            self.assertNotIn('{{', run)
            sqlglot.parse_one(run, read='databricks')
        self.assertIn("CAST('2021-01-01' AS DATE)", card_sql.bound(d['orders']['formula'], d['orders']['parameters']))
        # A date inside a longer text value cannot be lifted out, so the measure is not read.
        self.assertNotIn('noted', d)
        self.assertIn("measure 'noted' holds a text value that starts like a date and is not a plain date",
                      self.notes(facts))
        self.assertIn("a fixed date is a parameter on the metric, with the view's date as its default",
                      self.notes(facts))
        self.assertNotIn('parameters', self.declared(self.read(ORDERS))['revenue'])

    def test_two_measures_that_differ_only_in_a_date_declare_one_formula_with_two_defaults(self):
        # A name says what a date is for, not what it is. Two measures over two years are then one
        # formula with two defaults: downstream, one metric whose dates a reader may move. X3
        # refuses them as two cards, and merged the card keeps both statements as examples.
        d = self.declared(self.read("""
            version: 1.1
            source: demo.sales.orders
            measures:
              - name: orders_fy23
                expr: COUNT(CASE WHEN o_orderdate BETWEEN '2022-07-01' AND '2023-06-30' THEN 1 END)
              - name: orders_fy24
                expr: COUNT(CASE WHEN o_orderdate BETWEEN '2023-07-01' AND '2024-06-30' THEN 1 END)
              - name: two_spellings
                expr: COUNT(CASE WHEN made >= '2024-01-01 00:00' OR made >= '2024-01-01T00:00' THEN 1 END)
              - name: zoned
                expr: COUNT(CASE WHEN made >= '2024-01-01T00:00:00Z' AND made < TIMESTAMP'2024-02-01 00:00:00+05:30' THEN 1 END)
              - name: other_digits
                expr: COUNT(CASE WHEN note = '２０２４-01-01' THEN 1 END)
        """))
        self.assertEqual(d['orders_fy23']['formula'], d['orders_fy24']['formula'])
        self.assertEqual(declared.identity({**d['orders_fy23'], 'model': 'm', 'metric': 'a'}),
                         declared.identity({**d['orders_fy24'], 'model': 'm', 'metric': 'b'}))
        self.assertIn('BETWEEN {{o_orderdate_from}} AND {{o_orderdate_to}}', d['orders_fy23']['formula'])
        self.assertEqual([p['default'] for p in d['orders_fy23']['parameters']], ['2022-07-01', '2023-06-30'])
        self.assertEqual([p['default'] for p in d['orders_fy24']['parameters']], ['2023-07-01', '2024-06-30'])
        # Two spellings of one moment are two values, and each keeps its own name and default.
        self.assertEqual({p['name']: p['default'] for p in d['two_spellings']['parameters']},
                         {'made_from_2024_01_01_00_00': '2024-01-01 00:00',
                          'made_from_2024_01_01t00_00': '2024-01-01T00:00'})
        # A timestamp with a zone is a plain timestamp.
        self.assertEqual({p['name']: (p['datatype'], p['default']) for p in d['zoned']['parameters']},
                         {'made_from': ('timestamp', '2024-01-01T00:00:00Z'),
                          'made_to': ('timestamp', '2024-02-01 00:00:00+05:30')})
        self.assertNotIn('other_digits', d)               # not a date this reader can lift, so not read

    def test_what_the_sql_writer_would_change_is_not_read(self):
        # The parser's SQL writer renders some forms as other SQL. Each of these would be written
        # as an expression the view does not hold, so each is refused by name.
        facts = self.read("""
            version: 1.1
            source: demo.sales.orders
            joins:
              - name: customer
                source: demo.sales.customer
                'on': source.o_custkey = customer.c_custkey
            fields:
              - name: revenue_usd
                expr: revenue * fx_rate
              - name: twice
                expr: region
              - name: twice
                expr: status
            measures:
              - name: orders
                expr: COUNT(1)
              - name: leak
                expr: MAX(EXTRACT(`YEAR FROM d) + (SELECT MAX(salary) FROM ``hr``.``private``.``people``) + EXTRACT(YEAR` FROM d))
              - name: unit_leak
                expr: SUM(DATEDIFF(`DAY, a, b) + (SELECT 1) + DATEDIFF(DAY`, a, b))
              - name: parsed
                expr: MAX(TO_DATE(shipped_text, 'yyyy-MM-dd'))
              - name: epoch_days
                expr: MAX(UNIX_DATE(o_orderdate))
              - name: days_to_ship
                expr: AVG(DATEDIFF(shipped_at, ordered_at))
              - name: days_to_ship_stated
                expr: AVG(DATEDIFF(DAY, ordered_at, shipped_at))
              - name: order_year
                expr: MAX(EXTRACT(YEAR FROM o_orderdate))
              - name: path_total
                expr: SUM(CAST(payload:customer.total AS DOUBLE))
              - name: named_in_text
                expr: COUNT(IDENTIFIER('customer.c_name'))
              - name: revenue_usd
                expr: SUM(revenue_usd)
              - name: again
                expr: SUM(revenue)
              - name: again
                expr: AVG(revenue)
              - name: by_twice
                expr: COUNT(DISTINCT twice)
        """)
        d = self.declared(facts)
        self.assertEqual(sorted(d), ['days_to_ship_stated', 'order_year', 'orders'])
        self.assertEqual(d['days_to_ship_stated']['formula'],
                         'AVG(DATEDIFF(DAY, demo.sales.orders.ordered_at, demo.sales.orders.shipped_at))')
        notes = self.notes(facts)
        for said in ("measure 'leak' names a date part or unit that is not a plain word",
                     "measure 'unit_leak' names a date part or unit that is not a plain word",
                     "measure 'parsed' uses a form this reader's SQL writer does not write back unchanged",
                     "measure 'epoch_days' uses a form this reader's SQL writer does not write back unchanged",
                     "measure 'days_to_ship' calls DATEDIFF with 2 argument(s)",
                     "measure 'path_total' reads a path inside a value",
                     "measure 'named_in_text' uses IDENTIFIER()",
                     "measure 'revenue_usd' has the name of a field",
                     "measure 'again' shares its name with another measure",
                     "field 'twice' shares its name with another field"):
            self.assertIn(said, notes)
        for found in d.values():
            self.assertNotIn('SELECT', found['formula'].upper())
            self.assertNotIn('HR', found['source'].upper().split('.'))
        both = "version: 1.1\nsource: demo.sales.orders\nfields:\n  - name: a\n    expr: a\ndimensions:\n  - name: b\n    expr: b\n" + self.COUNTED
        self.assertIn('lists both `fields` and `dimensions`', self.refused(both))

    def test_a_view_too_large_to_read_in_a_short_time_is_refused_in_words(self):
        fields = ''.join(f'  - name: f{i}\n    expr: c{i}\n' for i in range(400))
        measures = ''.join(f'  - name: m{i}\n    expr: SUM(c{i})\n' for i in range(400))
        started = time.monotonic()
        said = self.refused(f'version: 1.1\nsource: demo.sales.orders\nfields:\n{fields}measures:\n{measures}')
        self.assertIn('400 fields and 400 measures, more than this reader reads from one view', said)
        # One long expression shared by a YAML alias: a small file that writes out a great deal.
        long = ' + '.join(f'SUM(c{i})' for i in range(1500))
        aliased = ('version: 1.1\nsource: demo.sales.orders\nmeasures:\n  - name: m0\n    expr: &e ' + long + '\n'
                   + ''.join(f'  - name: m{i}\n    expr: *e\n' for i in range(1, 120)))
        self.assertLess(len(aliased), 30_000)
        self.assertIn('characters as written; it is not read', self.refused(aliased))
        self.assertLess(time.monotonic() - started, 5)
        # A filter every measure carries: small as written, large once it is written into each.
        dates = ' OR '.join(f"d = '20{i % 90 + 10}-01-0{i % 9 + 1}'" for i in range(700))
        filtered = ('version: 1.1\nsource: demo.sales.orders\nfilter: ' + dates + '\nmeasures:\n'
                    + ''.join(f'  - name: m{i}\n    expr: COUNT(c{i})\n' for i in range(60)))
        self.assertIn('characters once the fields, measures and filter they read are written out', self.refused(filtered))
        self.assertLess(time.monotonic() - started, 30)

    def test_a_card_takes_the_declarations_parameters_and_runs_at_their_defaults(self):
        from metrics_agent.lib import card_sql
        decl = self.declared(self.read(self.DATED))['orders']
        card = {'name': 'Orders', 'formula': decl['formula'],
                'source': {'entity': decl['entity'], 'alias': 'Orders',
                           'parameters': [{'name': 'o_orderdate_from', 'datatype': 'date', 'default': '1999-09-09'},
                                          {'name': 'kept', 'datatype': 'integer', 'default': 1}]}}
        self.assertEqual(declared.held_to(card, decl), [])
        done, counts = declared.complete(card, decl, dialect='databricks')
        # The model's value leads; a parameter of the author's own is kept.
        self.assertEqual(done['source']['parameters'],
                         [{'name': 'kept', 'datatype': 'integer', 'default': 1},
                          {'name': 'o_orderdate_from', 'datatype': 'date', 'default': '2021-01-01'}])
        self.assertEqual(counts['parameters'], 1)
        statement = card_sql.canonical_statement({**done, 'dialect': 'databricks', 'output': {'alias': 'orders'}})
        self.assertIn('{{o_orderdate_from}}', statement)
        run = card_sql.bound(statement, done['source']['parameters'])
        self.assertIn("demo.sales.orders.o_orderdate >= CAST('2021-01-01' AS DATE)", run)
        sqlglot.parse_one(run, read='databricks')
        # A marker inside a quoted run of any kind is left alone, and one with no default stays
        # for the reader to fill.
        self.assertEqual(card_sql.bound('SELECT \'{{a}}\', "{{a}}", `{{a}}`, {{a}}, {{n}}, {{on}}, {{b}}',
                                        [{'name': 'a', 'default': 'plain text 1'}, {'name': 'n', 'default': 3},
                                         {'name': 'on', 'default': True}, {'name': 'b', 'default': None}]),
                         'SELECT \'{{a}}\', "{{a}}", `{{a}}`, \'plain text 1\', 3, TRUE, {{b}}')
        # Only a plain value is written in: the statement is run on a warehouse, and a default
        # that could end its own text value is left as the marker, so nothing it says is run.
        for unsafe in ("it's", "\\' OR (SELECT MAX(ssn) FROM hr.private.people) IS NOT NULL -- ", 'a"b', 'a`b',
                       'a\\', 'line\nbreak', float('nan'), float('inf'), ['a'], {'a': 1}):
            self.assertEqual(card_sql.bound('WHERE region = {{region}}', [{'name': 'region', 'default': unsafe}]),
                             'WHERE region = {{region}}', repr(unsafe))
        self.assertEqual(card_sql.bound('WHERE x = {{x}}', [{'name': 'x', 'default': 1.5}]), 'WHERE x = 1.5')
        # An apostrophe in a quoted name does not turn the rest of the statement into text.
        self.assertEqual(card_sql.bound('SELECT 1 AS `Customer\'s orders` WHERE d >= {{d}}',
                                        [{'name': 'd', 'default': '2021-01-01'}]),
                         "SELECT 1 AS `Customer's orders` WHERE d >= '2021-01-01'")

    def test_what_is_not_read_is_named_and_takes_what_is_built_on_it(self):
        facts = self.read('''
            version: 1.1
            source: demo.sales.orders
            parameters:
              - name: discount
                data_type: double
                default: 0
            joins:
              - name: items
                source: demo.sales.order_items
                on: source.o_orderkey = items.i_orderkey
                cardinality: one_to_many
              - name: daily
                source: SELECT d, COUNT(*) AS n FROM demo.sales.orders GROUP BY d
                on: source.d = daily.d
            fields:
              - name: order_date
                expr: o_orderdate
              - name: all_columns
                expr: source.*
              - name: city
                expr: address.city
              - name: discounted
                expr: (1 - discount) * amount
            measures:
              - name: orders
                expr: COUNT(1)
              - name: t7d
                expr: COUNT(DISTINCT o_custkey)
                window:
                  - order: order_date
                    range: trailing 7 day
              - name: t7d_share
                expr: MEASURE(t7d) / MEASURE(orders)
              - name: net
                expr: SUM((1 - discount) * amount)
              - name: net_fields
                expr: SUM(discounted)
              - name: quantity
                expr: SUM(items.i_qty)
              - name: per_day
                expr: MAX(daily.n)
              - name: spaced
                expr: SUM(`order total`)
        ''')
        d = self.declared(facts)
        self.assertEqual(sorted(d), ['orders', 'quantity'])
        self.assertIsNone(d['quantity'])                 # carried, and refused where it is placed
        notes = self.notes(facts)
        for said in ("measure 't7d' is a window measure",
                     "measure 't7d_share' reads MEASURE(t7d), which is not read",
                     "measure 'net' reads the parameter `discount`",
                     "field 'discounted' reads the parameter `discount`",
                     "measure 'net_fields' reads the field `discounted`, which is not read",
                     "field 'all_columns' is a wildcard",
                     "field 'city' names `address.city`",
                     "join 'items' is one_to_many: it is not walked",
                     "metric 'quantity' reads items, which no declared many-to-one or one-to-one path",
                     "join 'daily' has a source that is not a table name this reader can read",
                     "measure 'per_day' names `daily.n`",
                     "measure 'spaced' names the column `order total`, which needs quoting",
                     "the view declares no key"):
            self.assertIn(said, notes)

    def test_a_computed_field_with_a_columns_name_drops_what_reads_the_column(self):
        facts = self.read('''
            version: 1.1
            source: demo.sales.orders
            fields:
              - name: gross
                expr: amount + tax
              - name: amount
                expr: amount / 100
              - name: net
                expr: amount - tax
            measures:
              - name: total
                expr: SUM(amount)
              - name: total_gross
                expr: SUM(gross)
              - name: raw
                expr: SUM(source.amount)
              - name: orders
                expr: COUNT(1)
        ''')
        d = self.declared(facts)
        # After its definition, `amount` is the field: the lib expands it once.
        self.assertEqual(d['total']['formula'], 'SUM((demo.sales.orders.amount / 100))')
        dims = {x['name']: x['sql'] for x in d['orders']['dimensions']}
        self.assertEqual(dims['Net'], '(demo.sales.orders.amount / 100) - demo.sales.orders.tax')
        # `gross` reads the COLUMN (written before the field), and `raw` names it by table: the
        # shared model cannot tell either from the field, so neither is read.
        self.assertNotIn('Gross', dims)
        self.assertEqual(sorted(d), ['orders', 'total'])
        notes = self.notes(facts)
        self.assertIn("field 'gross' reads the column `amount`, and a field of that name redefines it", notes)
        self.assertIn("measure 'raw' reads the column `amount`", notes)
        self.assertIn("measure 'total_gross' reads field 'gross', which is not read", notes)

    def test_join_conditions(self):
        base = '''
            version: 1.1
            source: demo.sales.orders
            joins:
              - name: customer
                source: demo.sales.customer
                {join}
            measures:
              - name: names
                expr: COUNT(DISTINCT customer.c_name)
        '''
        def rels(join, extra=None):
            path = self.root / 'v.json'
            doc = {'metric_view': {'catalog_name': 'demo', 'schema_name': 'sales', 'table_name': 'v',
                                   'view_text': textwrap.dedent(base.format(join=join)), 'columns': []},
                   **(extra or {})}
            path.write_text(json.dumps(doc))
            facts = MV.parse(path)
            return [[(p['from_column'], p['to_column']) for p in r['columns']] for r in facts['relationships']], facts
        self.assertEqual(rels('on: customer.c_custkey = source.o_custkey AND source.o_region = customer.c_region')[0],
                         [[('o_custkey', 'c_custkey'), ('o_region', 'c_region')]])
        self.assertEqual(rels('using: [custkey, region]')[0], [[('custkey', 'custkey'), ('region', 'region')]])
        self.assertEqual(rels('on: source.o_custkey = c_custkey')[0], [[('o_custkey', 'c_custkey')]])
        # Bare names on both sides are read only when the tables' columns come with the view.
        found, facts = rels('on: o_custkey = c_custkey')
        self.assertEqual(found, [])
        self.assertIn("join 'customer' names its columns without a table prefix", self.notes(facts))
        self.assertIsNone(self.declared(facts)['names'])
        columns = {'source_columns': {'demo.sales.orders': [{'name': 'o_custkey', 'data_type': 'BIGINT'}],
                                      'demo.sales.customer': [{'name': 'c_custkey', 'data_type': 'BIGINT'},
                                                              {'name': 'c_name', 'data_type': 'STRING'}]}}
        found, facts = rels('on: o_custkey = c_custkey', columns)
        self.assertEqual(found, [[('o_custkey', 'c_custkey')]])
        self.assertEqual(self.declared(facts)['names']['formula'], 'COUNT(DISTINCT demo.sales.customer.c_name)')
        for bad, said in (('on: source.o_custkey > customer.c_custkey', 'is not written as equalities'),
                          ('on: source.a = source.b', 'compares two columns of one table'),
                          ('comment: none', 'has neither `on` nor `using`')):
            found, facts = rels(bad)
            self.assertEqual(found, [])
            self.assertIn(said, self.notes(facts))

    def test_a_description_carries_its_types_and_an_extended_one_its_tables_key(self):
        described = {'catalog_name': 'demo', 'schema_name': 'sales', 'table_name': 'orders_metrics',
                     'type': 'METRIC_VIEW', 'view_text': textwrap.dedent('''
                         version: 1.1
                         source: demo.sales.orders
                         dimensions:
                           - name: order_date
                             expr: o_orderdate
                           - name: priority
                             expr: o_priority
                           - name: is_open
                             expr: o_open
                         measures:
                           - name: orders
                             expr: COUNT(1)
                     '''),
                     'columns': [{'name': 'order_date', 'type': {'name': 'date'}},
                                 {'name': 'priority', 'type': {'name': 'int'}},
                                 {'name': 'is_open', 'type': {'name': 'boolean'}},
                                 {'name': 'orders', 'type': {'name': 'bigint'}, 'is_measure': True}]}
        fetched = self.root / 'orders_metrics.extended.json'
        fetched.write_text(json.dumps({'metric_view': described, 'source_columns': {},
                                       'primary_keys': {'demo.sales.orders': ['o_orderkey']}}))
        facts = MV.parse(fetched)
        self.assertEqual(facts['model_name'], 'demo.sales.orders_metrics')
        d = self.declared(facts)['orders']
        self.assertEqual(d['grain'], ['demo.sales.orders.o_orderkey'])
        self.assertEqual({x['name']: (x['kind'], x['datatype']) for x in d['dimensions']},
                         {'Order Date': ('time', 'date'), 'Priority': ('categorical', 'integer'),
                          'Is Open': ('boolean', 'boolean')})
        self.assertNotIn('declares no key', self.notes(facts))
        saved = self.root / 'described.json'
        saved.write_text(json.dumps(described))
        self.assertEqual(self.declared(MV.parse(saved))['orders']['grain'], [])
        # Unity Catalog's table API spells the same description another way.
        api = self.root / 'table_api.json'
        api.write_text(json.dumps({
            'catalog_name': 'demo', 'schema_name': 'sales', 'name': 'orders_metrics',
            'table_type': 'METRIC_VIEW', 'view_definition': described['view_text'],
            'columns': [{'name': 'order_date', 'type_text': 'date'}, {'name': 'priority', 'type_text': 'int'},
                        {'name': 'is_open', 'type_text': 'boolean'}, {'name': 'orders', 'type_text': 'bigint'}]}))
        from_api = MV.parse(api)
        self.assertEqual(from_api['model_name'], 'demo.sales.orders_metrics')
        self.assertEqual(self.declared(from_api)['orders']['dimensions'], d['dimensions'])
        ddl = self.root / 'orders_metrics.sql'
        ddl.write_text('CREATE OR REPLACE VIEW demo.sales.orders_metrics\nWITH METRICS\nLANGUAGE YAML\nAS $$\n'
                       + described['view_text'] + '$$;\n')
        self.assertEqual(MV.parse(ddl)['model_name'], 'demo.sales.orders_metrics')
        reader = MV.MetricViewReader()
        self.assertTrue(all(reader.detect(p) for p in (fetched, saved, api, ddl)))
        self.assertEqual(reader.models(self.root), sorted([fetched, saved, api, ddl]))

    def test_the_view_is_the_models_asset_and_is_linked_only_when_its_workspace_is_named(self):
        described = {'catalog_name': 'demo', 'schema_name': 'sales', 'table_name': 'orders_metrics',
                     'type': 'METRIC_VIEW', 'table_id': 'example-table-id',
                     'view_text': 'version: 1.1\nsource: demo.sales.orders\nmeasures:\n  - name: orders\n    expr: COUNT(1)\n'}

        def read(doc, name):
            path = self.root / name
            path.write_text(json.dumps(doc))
            facts = MV.parse(path)
            jsonschema.validate(public(facts), self.schema)
            return facts

        # A description names the view, never the workspace it was read from: no link.
        self.assertEqual(read(described, 'described.json')['assets'], [{
            'role': 'semantic_model', 'name': 'demo.sales.orders_metrics', 'vendor_id': 'example-table-id',
            'path': 'demo/sales/orders_metrics', 'site': None}])
        linked = read({'metric_view': described, 'workspace_host': 'Example.cloud.databricks.com'},
                      'linked.extended.json')
        self.assertEqual(linked['assets'][0]['site'], 'example.cloud.databricks.com')
        self.assertEqual(linked['assets'][0]['url'],
                         'https://example.cloud.databricks.com/explore/data/demo/sales/orders_metrics')
        self.assertNotIn('no link', self.notes(linked))
        # Only a bare workspace host is taken: nothing with a scheme, a path, credentials or another domain.
        for host in ('https://example.cloud.databricks.com', 'example.cloud.databricks.com/login',
                     'user@example.cloud.databricks.com', 'example.com', 7):
            with self.subTest(host=host):
                refused = read({'metric_view': described, 'workspace_host': host}, 'refused.extended.json')
                self.assertNotIn('url', refused['assets'][0])
                self.assertIsNone(refused['assets'][0]['site'])
                self.assertIn('not a Databricks workspace host', self.notes(refused))
        # A definition kept as YAML has no catalog.schema.view name to link to.
        bare = self.read('version: 1.1\nsource: demo.sales.orders\nmeasures:\n  - name: orders\n    expr: COUNT(1)\n')
        self.assertEqual(bare['assets'], [{'role': 'semantic_model', 'name': 'orders_metrics',
                                           'vendor_id': None, 'path': None, 'site': None}])

    def test_what_is_not_a_readable_metric_view_is_refused_in_words(self):
        reader = MV.MetricViewReader()
        other = self.view('tables:\n  - name: orders\n', 'semantic_model.yaml')
        self.assertFalse(reader.detect(other))
        for text, said in (('version: 1.1\nsource: SELECT * FROM demo.sales.orders\nmeasures:\n  - name: n\n    expr: COUNT(1)\n',
                            'its source is not a table name this reader can read: a query, or a name that needs quoting'),
                           ('version: 1.1\nsource: orders\nmeasures:\n  - name: n\n    expr: COUNT(1)\n',
                            'not a CATALOG.SCHEMA.TABLE name'),
                           ('version: 1.1\nsource: demo.sales.orders\ndimensions:\n  - name: d\n    expr: d\n',
                            'declares no measure'),
                           ('version: 1.1\nsource: demo.sales.orders\nfilter: discount > 0\nparameters:\n  - name: discount\n    data_type: double\nmeasures:\n  - name: n\n    expr: COUNT(1)\n',
                            "the view's filter reads the parameter `discount`"),
                           ('tables: []\n', 'not a metric view')):
            with self.assertRaises(MV.MetricViewError) as refused:
                MV.parse(self.view(text))
            self.assertIn(said, str(refused.exception))
        old = self.read('version: 0.1\nsource: demo.sales.orders\nmeasures:\n  - name: n\n    expr: COUNT(1)\n')
        self.assertIn('specification 0.1', self.notes(old))
        new = self.read('version: 9.9\nsource: demo.sales.orders\nmeasures:\n  - name: n\n    expr: COUNT(1)\n')
        self.assertIn("specification version '9.9' is not one this reader was written for", self.notes(new))

    # What follows holds the reader to four rules. A formula returns the view's number or is
    # refused by name. No SQL rides in on a name. Every refusal is the reader's own. A small
    # file takes a small time.

    COUNTED = 'measures:\n  - name: orders\n    expr: COUNT(1)\n'

    def refused(self, text, name='orders_metrics.yaml'):
        with self.assertRaises(MV.MetricViewError) as caught:
            MV.parse(self.view(text, name))
        return str(caught.exception)

    def test_a_source_that_is_not_a_plain_table_name_refuses_the_view(self):
        for source in ('demo.sales.orders CROSS JOIN hr_salaries', '/* c */ SELECT * FROM demo.sales.orders',
                       'demo.sales.orders TABLESAMPLE (10 PERCENT)', 'FROM demo.sales.orders |> WHERE open',
                       'demo.sales.orders AS o', 'demo.sales.orders; DROP TABLE demo.sales.x',
                       '`demo`.`sales`.`orders 2024`', 'demo..orders', 'range(10)'):
            with self.subTest(source=source):
                said = self.refused(f'version: 1.1\nsource: {json.dumps(source)}\n{self.COUNTED}')
                self.assertIn('the metric view is not read: its source is not a table name this reader can read: '
                              'a query, or a name that needs quoting', said)
        # A plain name in backticks is the same name, and its backticks are not carried.
        facts = self.read(f'version: 1.1\nsource: "`demo`.sales.`orders`"\n{self.COUNTED}')
        self.assertEqual(facts['tables'][0]['name'], 'demo.sales.orders')
        self.assertEqual(self.declared(facts)['orders']['source'], 'demo.sales.orders')

    def test_a_join_whose_names_are_not_plain_is_refused_with_what_reads_it(self):
        facts = self.read("""
            version: 1.1
            source: demo.sales.orders
            joins:
              - name: sampled
                source: demo.sales.customer TABLESAMPLE (10 PERCENT)
                on: source.o_custkey = sampled.c_custkey
              - name: quoted
                source: "`demo`.`sales`.`customer 2024`"
                on: source.o_custkey = quoted.c_custkey
              - name: customer
                source: demo.sales.customer
                using: ["id OR TRUE"]
              - name: region
                source: demo.sales.region
                using: region_id
              - name: nation
                source: "`demo`.`sales`.`nation`"
                using: ["`nation_id`"]
            measures:
              - name: orders
                expr: COUNT(1)
              - name: sampled_names
                expr: COUNT(DISTINCT sampled.c_name)
              - name: customers
                expr: COUNT(DISTINCT customer.c_name)
              - name: nations
                expr: COUNT(DISTINCT nation.n_name)
        """)
        notes = self.notes(facts)
        for said in ("join 'sampled' has a source that is not a table name this reader can read: a query, or a name "
                     "that needs quoting; it is not read, and nothing that needs it is placed",
                     "join 'quoted' has a source that is not a table name this reader can read",
                     "measure 'sampled_names' names `sampled.c_name`: `sampled` is neither the source nor a join",
                     "join 'customer' has a `using` that is not a list of plain column names",
                     "join 'region' has a `using` that is not a list of plain column names",
                     "metric 'customers' reads customer, which no declared many-to-one or one-to-one path"):
            self.assertIn(said, notes)
        d = self.declared(facts)
        self.assertEqual(sorted(d), ['customers', 'nations', 'orders'])
        self.assertIsNone(d['customers'])
        self.assertEqual(d['nations']['source'],
                         'demo.sales.orders\nLEFT JOIN demo.sales.nation\n  ON demo.sales.orders.nation_id = demo.sales.nation.nation_id')
        carried = json.dumps(public(facts))
        for text in ('TABLESAMPLE', 'OR TRUE', 'customer 2024', '`'):
            self.assertNotIn(text, carried)

    def test_an_expression_is_one_expression_and_a_key_is_a_plain_name(self):
        described = {'metric_view': {'catalog_name': 'demo', 'schema_name': 'sales', 'table_name': 'v',
                                     'view_text': textwrap.dedent("""
                                         version: 1.1
                                         source: demo.sales.orders
                                         measures:
                                           - name: orders
                                             expr: COUNT(1)
                                           - name: two
                                             expr: COUNT(1); DROP TABLE demo.sales.orders
                                           - name: aliased
                                             expr: SUM(amount) AS total
                                           - name: share
                                             expr: SUM(amount) / (SELECT SUM(amount) FROM demo.sales.orders)
                                           - name: folded
                                             expr: SUM(AGGREGATE(items, 0, (acc, x) -> acc + x))
                                     """)},
                     'primary_keys': {'demo.sales.orders': ['o_orderkey OR TRUE']}}
        path = self.root / 'v.json'
        path.write_text(json.dumps(described))
        facts = MV.parse(path)
        self.assertEqual(sorted(self.declared(facts)), ['orders'])
        self.assertEqual(self.declared(facts)['orders']['grain'], [])
        notes = self.notes(facts)
        for said in ("measure 'two' is not one SQL expression", "measure 'aliased' is not one SQL expression",
                     "measure 'share' holds a query of its own", "measure 'folded' uses a lambda",
                     "`primary_keys` for demo.sales.orders names a column that needs quoting; the key is ignored"):
            self.assertIn(said, notes)
        self.assertNotIn('DROP', json.dumps(public(facts)))

    def test_a_measure_the_views_filter_cannot_reach_is_refused(self):
        facts = self.read("""
            version: 1.1
            source: demo.sales.orders
            filter: status = 'F'
            measures:
              - name: orders
                expr: COUNT(1)
              - name: any_late
                expr: ANY(is_late)
              - name: doubled
                expr: amount * 2
              - name: per_unit
                expr: SUM(amount) / units
              - name: first_amount
                expr: FIRST(amount) IGNORE NULLS
        """)
        d = self.declared(facts)
        self.assertEqual(sorted(d), ['first_amount', 'orders'])
        self.assertEqual(d['orders']['formula'], "COUNT(1) FILTER(WHERE demo.sales.orders.status = 'F')")
        self.assertEqual(d['first_amount']['formula'],
                         "FIRST(demo.sales.orders.amount) FILTER(WHERE demo.sales.orders.status = 'F') IGNORE NULLS")
        notes = self.notes(facts)
        for name, column in (('doubled', 'amount'), ('per_unit', 'units')):
            self.assertIn(f"measure {name!r} reads `{column}` outside every aggregate this reader recognises, so the "
                          "view's filter could not be applied to all of it; it is not read", notes)
        # ANY reads no column when its argument is a constant, so it is refused as itself.
        self.assertIn("measure 'any_late' uses ANY, SOME or ALL, which this reader does not recognise as an "
                      "aggregate: the view's filter cannot be written into it; it is not read", notes)
        constant = self.read("version: 1.1\nsource: demo.sales.orders\nfilter: status = 'F'\nmeasures:\n"
                             "  - name: orders\n    expr: COUNT(1)\n  - name: any_true\n    expr: ANY(TRUE)\n")
        self.assertNotIn('any_true', self.declared(constant))
        # Without a filter there is nothing to apply, and the same measure is read as written.
        plain = self.read('version: 1.1\nsource: demo.sales.orders\nmeasures:\n  - name: any_late\n    expr: ANY(is_late)\n')
        self.assertEqual(self.declared(plain)['any_late']['formula'], 'ANY(demo.sales.orders.is_late)')

    def test_a_string_literal_with_a_quote_or_a_backslash_is_refused_by_name(self):
        facts = self.read(r"""
            version: 1.1
            source: demo.sales.orders
            dimensions:
              - name: mens
                expr: category = 'Men\'s'
              - name: segment
                expr: CASE WHEN category = 'Sport' THEN 'A' ELSE 'B' END
            measures:
              - name: orders
                expr: COUNT(1)
              - name: escaped
                expr: SUM(CASE WHEN category = 'Men\'s' THEN amount END)
              - name: double_quoted
                expr: SUM(CASE WHEN category = "it's source.amount" THEN amount END)
              - name: backslash
                expr: COUNT_IF(path = 'a\\b')
              - name: raw
                expr: COUNT_IF(code RLIKE r'\d+')
              - name: doubled
                expr: SUM(CASE WHEN category = 'Men''s' THEN amount END)
              - name: plain
                expr: SUM(CASE WHEN category = 'Sport' THEN amount END)
              - name: noted
                expr: "SUM(amount) -- it's net of source.tax"
        """)
        d = self.declared(facts)
        self.assertEqual(sorted(d), ['noted', 'orders', 'plain'])
        self.assertEqual(d['plain']['formula'], "SUM(CASE WHEN demo.sales.orders.category = 'Sport' THEN demo.sales.orders.amount END)")
        self.assertEqual(d['noted']['formula'], 'SUM(demo.sales.orders.amount)')     # a comment is not carried
        self.assertEqual([x['name'] for x in d['orders']['dimensions']], ['Segment'])
        notes = self.notes(facts)
        for what in ("field 'mens'", "measure 'escaped'", "measure 'double_quoted'", "measure 'backslash'", "measure 'raw'"):
            self.assertIn(f'{what} holds a string literal with a quote or a backslash in it, which this reader does '
                          'not carry; it is not read', notes)
        self.assertIn("measure 'doubled' writes two string literals side by side", notes)
        for found in (x for x in d.values() if x):
            self.assertNotIn('source.', found['formula'])
        # The filter is part of every number, so a filter refused this way leaves no measure.
        said = self.refused("version: 1.1\nsource: demo.sales.orders\nfilter: category = 'Men\\'s'\n" + self.COUNTED)
        self.assertIn("the view's filter holds a string literal with a quote or a backslash in it, which this reader "
                      'does not carry; the filter is part of every measure of the view, so none is read', said)

    def test_an_ordered_aggregate_takes_the_filter_after_within_group_or_is_refused(self):
        view = """
            version: 1.1
            source: demo.sales.orders
            {filter}
            measures:
              - name: orders
                expr: COUNT(1)
              - name: names
                expr: LISTAGG(name, ',') WITHIN GROUP (ORDER BY name)
              - name: own_filter
                expr: LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) FILTER (WHERE amount > 0)
              - name: median_amount
                expr: PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)
              - name: amounts
                expr: ARRAY_AGG(amount ORDER BY placed_at)
        """
        refusal = ('uses an ordered aggregate (WITHIN GROUP, or ORDER BY inside the call) that this reader\'s SQL '
                   'writer does not write back unchanged; it is not read')
        for filter_line in ("filter: status = 'F'", 'comment: no filter'):
            facts = self.read(view.format(filter=filter_line))
            self.assertEqual(sorted(m['name'] for m in facts['measures']), ['names', 'orders', 'own_filter'])
            for name in ('median_amount', 'amounts'):
                self.assertIn(f'measure {name!r} {refusal}', self.notes(facts))
        by_name = {m['name']: m['formula'] for m in facts['measures']}
        self.assertEqual(by_name['names'], "LISTAGG(source.name, ',') WITHIN GROUP (ORDER BY source.name)")
        filtered = {m['name']: m['formula'] for m in self.read(view.format(filter="filter: status = 'F'"))['measures']}
        self.assertEqual(filtered['names'],
                         "LISTAGG(source.name, ',') WITHIN GROUP (ORDER BY source.name) FILTER(WHERE source.status = 'F')")
        self.assertEqual(filtered['own_filter'],
                         "LISTAGG(source.name, ',') WITHIN GROUP (ORDER BY source.name) "
                         "FILTER(WHERE (source.amount > 0) AND (source.status = 'F'))")
        # The round trip: what is written parses, as a FILTER over the whole WITHIN GROUP clause, and writes the same.
        for formula in (filtered['names'], filtered['own_filter']):
            back = sqlglot.parse_one(formula, read='databricks')
            self.assertIsInstance(back, sqlglot.exp.Filter)
            self.assertIsInstance(back.this, sqlglot.exp.WithinGroup)
            self.assertEqual(back.sql(dialect='databricks'), formula)
        # Why the percentile is refused: the writer makes it another function.
        self.assertEqual(sqlglot.parse_one('PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)', read='databricks')
                         .sql(dialect='databricks'), 'PERCENTILE_APPROX(amount, 0.5)')

    def test_a_parameter_marker_is_refused_by_name(self):
        facts = self.read("""
            version: 1.1
            source: demo.sales.orders
            dimensions:
              - name: discounted
                expr: amount * (1 - :discount)
            measures:
              - name: orders
                expr: COUNT(1)
              - name: named
                expr: SUM(amount * (1 - :discount))
              - name: positional
                expr: SUM(amount * ?)
        """)
        self.assertEqual(sorted(self.declared(facts)), ['orders'])
        for what in ("field 'discounted'", "measure 'named'", "measure 'positional'"):
            self.assertIn(f'{what} reads a parameter (a marker such as `:name` or `?`)', self.notes(facts))
        said = self.refused('version: 1.1\nsource: demo.sales.orders\nfilter: amount > :floor\n' + self.COUNTED)
        self.assertIn("the view's filter reads a parameter", said)
        for bad, why in (('parameters: discount', 'its `parameters` is not a list'),
                         ('parameters:\n  - default: 0', 'a `parameters` entry has no name')):
            self.assertIn(why, self.refused(f'version: 1.1\nsource: demo.sales.orders\n{bad}\n{self.COUNTED}'))

    def test_the_views_filter_reads_the_sources_columns_and_a_measure_reads_the_field(self):
        # Both rules are what Databricks does (observed on a workspace on 2026-11-26).
        view = """
            version: 1.1
            source: demo.sales.orders
            filter: status = 'open'
            dimensions:
              - name: status
                expr: UPPER(status)
              - name: priority
                expr: o_priority
            measures:
              - name: orders
                expr: COUNT(1)
              - name: revenue
                expr: SUM(amount)
              - name: open_orders
                expr: COUNT_IF(status = 'OPEN')
        """
        facts = self.read(view)
        d = self.declared(facts)
        cond = "FILTER(WHERE demo.sales.orders.status = 'open')"
        self.assertEqual(d['orders']['formula'], f'COUNT(1) {cond}')
        self.assertEqual(d['revenue']['formula'], f'SUM(demo.sales.orders.amount) {cond}')
        # The measure reads the FIELD, whose expression stands in its place; the filter beside it reads the column.
        self.assertEqual(d['open_orders']['formula'], f"COUNT_IF((UPPER(demo.sales.orders.status)) = 'OPEN') {cond}")
        for found in d.values():
            self.assertTrue(found['formula'].endswith(cond))
            self.assertNotIn('UPPER', found['formula'].rsplit('FILTER', 1)[1])
        self.assertEqual([x['name'] for x in d['orders']['dimensions']], ['Priority'])
        self.assertIn("field 'status' redefines a column the view's filter reads. The filter reads the column, so the "
                      'field is not listed as a dimension', self.notes(facts))
        # With no filter the field is a dimension as before, and a measure reads it by name.
        unfiltered = self.declared(self.read(view.replace("filter: status = 'open'", 'comment: unfiltered')))
        self.assertEqual(unfiltered['open_orders']['formula'], "COUNT_IF(UPPER(demo.sales.orders.status) = 'OPEN')")
        self.assertEqual([x['name'] for x in unfiltered['orders']['dimensions']], ['Status', 'Priority'])

    def test_a_filter_that_names_only_a_field_is_refused_when_the_sources_columns_are_known(self):
        text = textwrap.dedent("""
            version: 1.1
            source: demo.sales.orders
            filter: order_year >= 1996 AND o_status = 'F'
            dimensions:
              - name: order_year
                expr: YEAR(o_orderdate)
            measures:
              - name: orders
                expr: COUNT(1)
        """)
        path = self.root / 'v.extended.json'
        path.write_text(json.dumps({'metric_view': {'view_text': text},
                                    'source_columns': {'demo.sales.orders': ['o_orderdate', 'o_status']}}))
        with self.assertRaises(MV.MetricViewError) as caught:
            MV.parse(path)
        self.assertIn("the view's filter reads `order_year`, which is a field of the view and not a column of its "
                      'source', str(caught.exception))
        # Unknown columns prove nothing: the name is bound to the source, as every bare name in a filter is.
        self.assertEqual(self.declared(self.read(text))['orders']['formula'],
                         "COUNT(1) FILTER(WHERE demo.sales.orders.order_year >= 1996 AND demo.sales.orders.o_status = 'F')")

    def test_a_one_to_many_join_refuses_the_measure_that_reads_it_with_its_reason(self):
        facts = self.read("""
            version: 1.1
            source: demo.sales.orders
            joins:
              - name: items
                source: demo.sales.order_items
                on: source.o_orderkey = items.i_orderkey
                cardinality: one_to_many
            measures:
              - name: orders
                expr: COUNT(1)
              - name: quantity
                expr: SUM(items.i_qty)
        """)
        quantity = next(m for m in facts['measures'] if m['name'] == 'quantity')
        self.assertNotIn('declared', quantity)
        self.assertEqual(facts['relationships'][0]['cardinality'], '1:M')
        notes = self.notes(facts)
        self.assertIn("join 'items' is one_to_many: it is not walked, and a measure that reads it is not placed", notes)
        self.assertIn("metric 'quantity' reads items, which no declared many-to-one or one-to-one path from source "
                      'reaches; it carries no declaration', notes)
        self.assertEqual(self.declared(facts)['orders']['source'], 'demo.sales.orders')    # and no join fans it out

    def test_a_declared_formula_returns_the_views_number_on_duckdb(self):
        import duckdb
        facts = self.read("""
            version: 1.1
            source: demo.sales.orders
            filter: o_status <> 'P' AND customer.c_active
            joins:
              - name: customer
                source: demo.sales.customer
                on: source.o_custkey = customer.c_custkey
            dimensions:
              - name: segment
                expr: customer.c_segment
              - name: o_status
                expr: LOWER(o_status)
            measures:
              - name: orders
                expr: COUNT(1)
              - name: revenue
                expr: SUM(amount)
              - name: big_orders
                expr: COUNT(1) FILTER (WHERE amount > 50)
              - name: fulfilled
                expr: COUNT(CASE WHEN o_status = 'f' THEN 1 END)
              - name: revenue_per_order
                expr: MEASURE(revenue) / MEASURE(orders)
              - name: customers
                expr: COUNT(DISTINCT customer.c_name)
        """)
        con = duckdb.connect()
        self.addCleanup(con.close)
        con.execute("ATTACH ':memory:' AS demo")
        con.execute('CREATE SCHEMA demo.sales')
        con.execute('CREATE TABLE demo.sales.customer (c_custkey INT, c_name VARCHAR, c_segment VARCHAR, c_active BOOLEAN)')
        con.execute('CREATE TABLE demo.sales.orders (o_orderkey INT, o_custkey INT, o_status VARCHAR, amount DOUBLE)')
        con.executemany('INSERT INTO demo.sales.customer VALUES (?, ?, ?, ?)',
                        [(1, 'Ada', 'retail', True), (2, 'Bo', 'retail', False), (3, 'Cy', 'trade', True), (4, 'Ada', 'trade', None)])
        con.executemany('INSERT INTO demo.sales.orders VALUES (?, ?, ?, ?)',
                        [(1, 1, 'F', 10.0), (2, 1, 'P', 20.0), (3, 2, 'F', 40.0), (4, 3, 'O', 80.0), (5, 3, 'F', None),
                         (6, 4, 'F', 160.0), (7, None, 'F', 320.0), (8, 9, 'O', 640.0), (9, 1, 'F', 60.0)])
        # The view's meaning, written by hand: the source, its join, the filter as a WHERE.
        base = ("FROM demo.sales.orders o LEFT JOIN demo.sales.customer c ON o.o_custkey = c.c_custkey "
                "WHERE o.o_status <> 'P' AND c.c_active")
        hand = {'orders': 'COUNT(1)', 'revenue': 'SUM(o.amount)', 'big_orders': 'COUNT(1) FILTER (WHERE o.amount > 50)',
                'fulfilled': "COUNT(CASE WHEN LOWER(o.o_status) = 'f' THEN 1 END)",
                'revenue_per_order': 'SUM(o.amount) / COUNT(1)', 'customers': 'COUNT(DISTINCT c.c_name)'}
        d = self.declared(facts)
        self.assertEqual(sorted(d), sorted(hand))
        for name, found in d.items():
            with self.subTest(measure=name):
                self.assertIn("FILTER(WHERE", found['formula'])
                got = con.execute(f"SELECT {found['formula']} FROM {found['source']}").fetchone()[0]
                want = con.execute(f'SELECT {hand[name]} {base}').fetchone()[0]
                self.assertEqual(got, want)
                segment = next(x['sql'] for x in found['dimensions'] if x['name'] == 'Segment')
                cut = dict(con.execute(f"SELECT {segment}, {found['formula']} FROM {found['source']} GROUP BY 1").fetchall())
                by_hand = dict(con.execute(f'SELECT c.c_segment, {hand[name]} {base} GROUP BY 1').fetchall())
                # A group the filter empties is absent from the view and a zero or a null here.
                self.assertEqual({k: v for k, v in cut.items() if v}, {k: v for k, v in by_hand.items() if v})
        self.assertEqual(con.execute(f"SELECT {d['orders']['formula']} FROM {d['orders']['source']}").fetchone()[0], 4)
        self.assertEqual(con.execute(f"SELECT {d['fulfilled']['formula']} FROM {d['fulfilled']['source']}").fetchone()[0], 3)

    def test_a_file_that_is_not_readable_text_is_refused_in_words(self):
        reader = MV.MetricViewReader()

        def raw(name, data):
            path = self.root / name
            path.write_bytes(data)
            return path

        view = ('version: 1.1\nsource: demo.sales.orders\n' + self.COUNTED).encode()
        for path, said in ((raw('latin.yaml', b'version: 1.1\nsource: demo.sales.caf\xe9\n'), 'is not UTF-8 text'),
                           (raw('wide.yaml', view.decode().encode('utf-16')), 'is not UTF-8 text'),
                           (raw('binary.json', b'\xff\xfe\x00'), 'is not UTF-8 text'),
                           (raw('deep.json', b'[' * 100000 + b']' * 100000), 'is nested too deeply to read'),
                           (raw('deep.yaml', b'a: ' + b'[' * 5000 + b']' * 5000), 'is nested too deeply to read'),
                           (raw('big.yaml', view + b'#' * (1 << 20)), 'is larger than 1 MiB'),
                           (raw('list.json', b'[1]'), 'is not a JSON object'),
                           (raw('other.json', b'{"name": "example"}'), 'not a metric view description'),
                           (self.root, 'is a folder that holds no metric view this reader reads with a measure')):
            with self.subTest(path=path.name):
                with self.assertRaises(MV.MetricViewError) as caught:
                    MV.parse(path)
                self.assertIn(said, str(caught.exception))
                self.assertFalse(reader.detect(path))
        # A byte order mark is not content.
        for path in (raw('bom.yaml', b'\xef\xbb\xbf' + view),
                     raw('bom.json', b'\xef\xbb\xbf' + json.dumps({'view_text': view.decode()}).encode())):
            self.assertEqual([m['name'] for m in MV.parse(path)['measures']], ['orders'])
            self.assertTrue(reader.detect(path))

    def test_what_comes_with_a_view_in_another_shape_is_ignored_with_a_note(self):
        text = textwrap.dedent("""
            version: 1.1
            source: demo.sales.orders
            joins:
              - name: customer
                source: demo.sales.customer
                on: o_custkey = c_custkey
            dimensions:
              - name: priority
                expr: o_priority
                synonyms: urgency
                comment: {not: prose}
              - name: no
                expr: o_flag
              - name: placed
                expr: 2024-01-01
            measures:
              - name: orders
                expr: COUNT(1)
                synonyms: [order count, 7, '']
                display_name: [Orders]
              - name: names
                expr: COUNT(DISTINCT customer.c_name)
        """)

        def read(name, **beside):
            path = self.root / name
            path.write_text(json.dumps({'metric_view': {'catalog_name': ['demo'], 'schema_name': 'sales',
                                                        'table_name': 'v', 'table_id': {'id': 1}, 'view_text': text,
                                                        **{k: beside.pop(k) for k in ('columns',) if k in beside}},
                                        **beside}))
            facts = MV.parse(path)
            jsonschema.validate(public(facts), self.schema)
            return facts

        # One name alone is a list of one.
        facts = read('one.json', primary_keys={'demo.sales.orders': 'o_orderkey'},
                     source_columns={'demo.sales.orders': 'o_custkey', 'demo.sales.customer': 'c_custkey'},
                     columns=[{'name': 'priority', 'type': {'name': 'int'}}, 7, {'name': 'orders', 'type': ['bigint']}])
        d = self.declared(facts)
        self.assertEqual(d['orders']['grain'], ['demo.sales.orders.o_orderkey'])
        self.assertEqual(d['names']['formula'], 'COUNT(DISTINCT demo.sales.customer.c_name)')
        self.assertEqual([(x['name'], x['datatype']) for x in d['orders']['dimensions']], [('Priority', 'integer')])
        self.assertNotIn('description', d['orders']['dimensions'][0])
        self.assertEqual(next(m for m in facts['measures'] if m['name'] == 'orders')['synonyms'], ['order count'])
        self.assertEqual(facts['assets'][0]['vendor_id'], None)
        self.assertEqual(facts['model_name'], 'sales.v')
        notes = self.notes(facts)
        for said in ("field 'priority' has `synonyms` that are not a list; they are ignored",
                     'a field whose name is not text (YAML reads it as bool) is not read',
                     "field 'placed' has an expression that is not text (YAML reads it as date)",
                     '2 entries of `columns` did not name a column and its type; ignored'):
            self.assertIn(said, notes)
        # Anything else is left out by name, and the view is read without it.
        facts = read('odd.json', primary_keys={'demo.sales.orders': 5}, source_columns=['o_custkey'], columns=5)
        self.assertEqual(self.declared(facts)['orders']['grain'], [])
        self.assertIsNone(self.declared(facts)['names'])
        notes = self.notes(facts)
        for said in ("`primary_keys` for 'demo.sales.orders' is not a list of column names; it is ignored",
                     '`source_columns` is not a mapping of table to columns; it is ignored',
                     "`columns` is not a list; no field's or measure's type is read",
                     "join 'customer' names its columns without a table prefix"):
            self.assertIn(said, notes)

    def test_an_error_that_is_not_a_refusal_becomes_one_that_names_the_file_only(self):
        path = self.view('version: 1.1\nsource: demo.sales.orders\n' + self.COUNTED)
        whole = MV._read
        self.addCleanup(setattr, MV, '_read', whole)
        for error in (TypeError, AttributeError, KeyError, IndexError, ValueError, RecursionError):
            def broken(src, error=error):
                raise error('customer_secret_value')
            MV._read = broken
            with self.assertRaises(MV.MetricViewError) as caught:
                MV.parse(path)
            said = str(caught.exception)
            self.assertIn(str(path), said)
            self.assertIn(f'({error.__name__})', said)
            self.assertNotIn('customer_secret_value', said)
            self.assertIsNone(caught.exception.__cause__)
        MV._read = whole
        # A refusal passes through as it is, and an expression nested too deeply is one measure's refusal.
        deep = self.read('version: 1.1\nsource: demo.sales.orders\nmeasures:\n  - name: orders\n    expr: COUNT(1)\n'
                         '  - name: deep\n    expr: SUM(' + '+'.join(['x'] * 3000) + ')\n')
        self.assertEqual(sorted(self.declared(deep)), ['orders'])
        self.assertIn("measure 'deep'", self.notes(deep))

    def test_a_small_file_takes_a_small_time(self):
        def chain(kind, expr, steps=40):
            head = 'version: 1.1\nsource: demo.sales.orders\n'
            first = f'  - name: {kind[0]}0\n    expr: {expr}\n'
            rest = ''.join(f'  - name: {kind[0]}{i}\n    expr: {{0}}{kind[0]}{i - 1}{{1}} + {{0}}{kind[0]}{i - 1}{{1}}\n'
                           for i in range(1, steps + 1))
            if kind == 'measure':
                return head + 'measures:\n' + first + rest.format('MEASURE(', ')')
            return head + 'dimensions:\n' + first + rest.format('', '') + self.COUNTED

        started = time.perf_counter()
        # Each measure reads the one before it twice: written out, the fortieth is a million million times the first.
        facts = self.read(chain('measure', 'SUM(amount)'))
        read = [m['name'] for m in facts['measures']]
        self.assertEqual(read, [f'm{i}' for i in range(len(read))])
        self.assertTrue(0 < len(read) < 20)
        self.assertTrue(all(len(m['formula']) <= MV.MAX_EXPANDED for m in facts['measures']))
        notes = self.notes(facts)
        self.assertIn(f"measure 'm{len(read)}' is longer than 20,000 characters once the fields and measures it reads "
                      'are written out; it is not read', notes)
        self.assertIn(f"measure 'm{len(read) + 1}' reads MEASURE(m{len(read)}), which is not read", notes)
        # A field names the field before it, and the shared model writes each name out: the same cap holds.
        facts = self.read(chain('field', 'amount'))
        self.assertIn('is longer than 20,000 characters once the fields and measures it reads are written out',
                      self.notes(facts))
        for found in self.declared(facts)['orders']['dimensions']:
            self.assertLessEqual(len(found['sql']), 2 * MV.MAX_EXPANDED)
        # A file of statements that are not metric views is recognised in one pass, and so is a hostile one.
        reader = MV.MetricViewReader()
        for name, text in (('dump.sql', 'CREATE VIEW demo.sales.v AS SELECT 1 FROM demo.sales.t;\n' * 15000),
                           ('heads.sql', 'CREATE VIEW demo.sales.v ' + 'WITH METRICS ' * 60000),
                           ('open.sql', '/* ' * 300000), ('blank.sql', ' ' * 900000 + 'x'),
                           ('marks.sql', 'CREATE VIEW demo.sales.v WITH METRICS LANGUAGE YAML AS ' + '$ ' * 400000)):
            path = self.root / name
            path.write_text(text)
            self.assertFalse(reader.detect(path))
        self.assertLess(time.perf_counter() - started, 20)

    def test_a_ddl_file_holds_one_statement(self):
        view = 'version: 1.1\nsource: demo.sales.orders\n' + self.COUNTED
        ddl = 'CREATE OR REPLACE VIEW demo.sales.orders_metrics\nWITH METRICS\nLANGUAGE YAML\nAS $$\n' + view + '$$'
        commented = self.view('-- saved from the workspace\n/* CREATE VIEW demo.sales.old WITH METRICS */\n  ' + ddl + ';\n',
                              'commented.sql')
        self.assertEqual(MV.parse(commented)['model_name'], 'demo.sales.orders_metrics')
        quoted = self.view(ddl.replace('demo.sales.orders_metrics', '`demo`.`sales`.`orders metrics`'), 'quoted.sql')
        self.assertEqual(MV.parse(quoted)['model_name'], 'demo.sales.orders metrics')
        for name, text, said in (('two.sql', ddl + ';\n' + ddl + ';\n', "holds text after the view's definition"),
                                 ('dollar.sql', ddl.replace('COUNT(1)', "COUNT(1)\n    comment: costs $$ a lot"),
                                  "holds text after the view's definition"),
                                 ('open.sql', ddl[:-2], 'has no definition between `$$` and `$$`')):
            with self.subTest(name=name):
                self.assertIn(said, self.refused(text, name))


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema, duckdb)')
class MetricViewInputTest(unittest.TestCase):
    """The Databricks connector hands the reader saved definitions; it fetches none."""
    VIEW = 'version: 1.1\nsource: demo.sales.orders\nmeasures:\n  - name: orders\n    expr: COUNT(1)\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def plug(self, **settings):
        plug = DatabricksDirect(settings)

        def no_connection():
            raise AssertionError('reading a saved definition opened a connection')
        plug.sql_runner = no_connection
        return plug

    def test_the_connector_names_its_reader_and_asks_for_a_file(self):
        self.assertIn('bi_definitions', DatabricksDirect.capabilities)
        self.assertEqual(DatabricksDirect.bi_vendor, MV.VENDOR)
        self.assertEqual([q.key for q in DatabricksDirect.setup_questions['bi']], ['bi'])
        self.assertFalse(getattr(DatabricksDirect, 'fetch_settings', ()))

    def test_a_file_or_a_folder_on_disk_is_read_in_place_without_a_connection(self):
        view = self.root / 'orders_metrics.yaml'
        view.write_text(self.VIEW)
        self.assertEqual(self.plug(bi=str(view)).bi_definitions([], self.root / 'unused'), [view])
        self.assertEqual(self.plug(bi=str(self.root)).bi_definitions([], self.root / 'unused'), [view])

    def test_a_folder_gives_only_its_readable_views_and_never_stops_on_another_file(self):
        from metrics_agent import sockets
        folder = self.root / 'views'
        (folder / 'nested').mkdir(parents=True)
        (folder / '.hidden').mkdir()
        outside = self.root / 'outside.yaml'
        outside.write_text(self.VIEW)
        first, second = folder / 'b_orders.yaml', folder / 'nested' / 'a_returns.json'
        first.write_text(self.VIEW)
        second.write_text(json.dumps({'view_text': self.VIEW}))
        (folder / 'dimension_only.yaml').write_text('version: 1.1\nsource: demo.sales.orders\ndimensions:\n  - name: d\n    expr: d\n')
        (folder / 'query_source.yaml').write_text(self.VIEW.replace('demo.sales.orders', 'SELECT 1'))
        (folder / 'package.json').write_text('{"name": "example"}')
        (folder / 'bundle.yml').write_text('bundle:\n  name: example\n')
        (folder / 'query.sql').write_text('SELECT 1')
        (folder / 'binary.json').write_bytes(b'\xff\xfe\x00')
        (folder / 'notes.txt').write_text(self.VIEW)
        (folder / '.hidden' / 'kept_out.yaml').write_text(self.VIEW)
        try:
            os.symlink(outside, folder / 'link.yaml')
            os.symlink(self.root, folder / 'loop')
            os.symlink(folder / 'missing.yaml', folder / 'dangling.yaml')
        except (OSError, NotImplementedError):
            pass                                          # a platform without links has none to skip
        reader = MV.MetricViewReader()
        expected = sorted([first, second])
        self.assertEqual(reader.models(folder), expected)
        self.assertEqual(reader.models(folder), expected)                  # the same order every time
        self.assertEqual(self.plug(bi=str(folder)).bi_definitions([], self.root / 'unused'), expected)
        self.assertEqual([MV.parse(p)['measures'][0]['name'] for p in expected], ['orders', 'orders'])
        self.assertFalse(reader.detect(folder))
        # Named alone, a file the folder left out is refused in words.
        for name, said in (('dimension_only.yaml', 'the metric view declares no measure this reader reads'),
                           ('query_source.yaml', 'its source is not a table name this reader can read'),
                           ('binary.json', 'is not UTF-8 text'), ('package.json', 'not a metric view description')):
            self.assertEqual(reader.models(folder / name), [folder / name])
            with self.assertRaises(MV.MetricViewError) as refused:
                reader.parse(folder / name)
            self.assertIn(said, str(refused.exception))
        # A folder with no view is refused by name, by the reader and by the connector.
        empty = self.root / 'empty'
        empty.mkdir()
        (empty / 'dimension_only.yaml').write_text((folder / 'dimension_only.yaml').read_text())
        self.assertEqual(reader.models(empty), [])
        with self.assertRaisesRegex(MV.MetricViewError, 'is a folder that holds no metric view this reader reads'):
            reader.parse(empty)
        with self.assertRaisesRegex(sockets.NotSupported, 'holds no metric view this reader reads with a measure'):
            self.plug(bi=str(empty)).bi_definitions([], self.root / 'unused')

    def test_without_a_file_the_connector_says_what_to_save(self):
        from metrics_agent import sockets
        with self.assertRaisesRegex(sockets.NotSupported, 'set `bi` to a saved metric view definition'):
            self.plug().bi_definitions(['demo.sales.orders_metrics'], self.root)

    def test_the_reader_is_registered_under_its_name(self):
        text = (ENGINE / 'packages/metrics-agent-databricks/pyproject.toml').read_text()
        self.assertIn('[project.entry-points."metrics_agent.readers"]\n'
                      'databricks-metric-view = "metrics_agent_databricks.metric_view:MetricViewReader"', text)
        self.assertEqual(MV.MetricViewReader.name, 'databricks-metric-view')


if __name__ == '__main__':
    unittest.main()
