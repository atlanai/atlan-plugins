"""A Databricks metric view read into `bi_facts`, declarations included, on synthetic views.

Each case is a rule the reader holds: what it binds, what it replaces, what it refuses by name.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import textwrap
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
                     "join 'daily' reads a query, not a table",
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
                            "source is a query"),
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
