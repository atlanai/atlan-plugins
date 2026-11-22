"""A Power BI project is read however its author hands it over, and a model whose tables
share a warehouse table keeps each table's own column names."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    import yaml
    from metrics_agent.lib import bi_models, evidence
    from metrics_agent.lib import rules as R
    from metrics_agent.plugs.readers import bicompile, pbip
    from metrics_agent.plugs.readers.powerbi import PowerBIReader
    from metrics_agent.sockets import ReaderRefused
    from metrics_agent.stages import a2_question_units as a2

TABLE = '''table Orders
\tlineageTag: 11111111-1111-4111-8111-111111111111

\tmeasure 'Order Amount' = SUM ( Orders[Amount] )
\t\tlineageTag: 22222222-2222-4222-8222-222222222222

\t\tkpi
\t\t\ttargetExpression = 100
\t\t\tstatusGraphic: Traffic Light - Single
\t\t\tstatusExpression = IF ( [Order Amount] > 100, 1, -1 )
\t\t\ttrendExpression = 1

\tcolumn Amount
\t\tdataType: double
\t\tsummarizeBy: sum
\t\tsourceColumn: Amount

\tcolumn 'Order Status'
\t\tdataType: string
\t\tsummarizeBy: none
\t\tsourceColumn: Order Status

\tpartition Orders = m
\t\tmode: import
\t\tsource =
\t\t\t\tlet
\t\t\t\t    Source = Snowflake.Databases("example.snowflakecomputing.com", "WH"),
\t\t\t\t    DB = Source{[Name="SHOP",Kind="Database"]}[Data],
\t\t\t\t    S = DB{[Name="SALES",Kind="Schema"]}[Data],
\t\t\t\t    T = S{[Name="ORDERS",Kind="Table"]}[Data],
\t\t\t\t    #"Renamed Columns" = Table.RenameColumns(T,{{"AMOUNT_USD", "Amount"}, {"STATUS_CD", "Order Status"}})
\t\t\t\tin
\t\t\t\t    #"Renamed Columns"
'''
BIM = {'model': {'tables': [{'name': 'Orders', 'columns': [
    {'name': 'Amount', 'dataType': 'double', 'sourceColumn': 'AMOUNT_USD'}],
    'measures': [{'name': 'Order Amount', 'expression': 'SUM ( Orders[Amount] )'}],
    'partitions': [{'name': 'Orders', 'mode': 'import', 'source': {'type': 'm', 'expression': [
        'let', '    Source = Snowflake.Databases("example.snowflakecomputing.com", "WH"),',
        '    DB = Source{[Name="SHOP",Kind="Database"]}[Data],',
        '    S = DB{[Name="SALES",Kind="Schema"]}[Data],',
        '    T = S{[Name="ORDERS",Kind="Table"]}[Data]', 'in', '    T']}}]}]}}


def visual(name):
    measure = {'Measure': {'Expression': {'SourceRef': {'Entity': 'Orders'}}, 'Property': 'Order Amount'}}
    return {'name': name, 'position': {'x': 0, 'y': 0, 'z': 0, 'height': 100, 'width': 100},
            'visual': {'visualType': 'card', 'query': {'queryState': {'Values': {'projections': [
                {'field': measure, 'queryRef': 'Orders.Order Amount', 'nativeQueryRef': 'Order Amount'}]}}}}}


def project(root: Path, name='Shop', model='Shop', tmsl=False, by_connection=False):
    """A project as Power BI Desktop saves one: <name>.pbip, <name>.Report, <model>.SemanticModel."""
    rep, mod = root / f'{name}.Report', root / f'{model}.SemanticModel'
    (rep / 'definition/pages/p1/visuals/v1').mkdir(parents=True)
    ref = ({'byConnection': {'connectionString': 'Data Source=x;semanticmodelid=abc'}} if by_connection
           else {'byPath': {'path': f'../{model}.SemanticModel'}})
    (rep / 'definition.pbir').write_text(json.dumps({'version': '4.0', 'datasetReference': ref}))
    (rep / 'definition/pages/pages.json').write_text(json.dumps({'pageOrder': ['p1'], 'activePageName': 'p1'}))
    (rep / 'definition/pages/p1/page.json').write_text(json.dumps({'name': 'p1', 'displayName': 'Page 1'}))
    (rep / 'definition/pages/p1/visuals/v1/visual.json').write_text(json.dumps(visual('v1')))
    (root / f'{name}.pbip').write_text(json.dumps({'version': '1.0', 'artifacts': [{'report': {'path': f'{name}.Report'}}]}))
    if by_connection:
        return rep, None
    mod.mkdir(exist_ok=True)
    (mod / 'definition.pbism').write_text(json.dumps({'version': '4.2'}))
    if tmsl:
        (mod / 'model.bim').write_text(json.dumps(BIM))
    else:
        (mod / 'definition/tables').mkdir(parents=True, exist_ok=True)
        (mod / 'definition/tables/Orders.tmdl').write_text(TABLE)
        (mod / 'definition/model.tmdl').write_text('model Model\n\tculture: en-US\n\nref table Orders\n')
    return rep, mod


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class ProjectHandoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'repo'
        self.root.mkdir()

    def test_every_form_of_one_project_names_the_same_model_and_report(self):
        rep, mod = project(self.root)
        for form in (self.root / 'Shop.pbip', rep, mod, mod / 'definition', self.root):
            with self.subTest(form=form.name):
                got = pbip.resolve(form)
                self.assertEqual((got.model, got.kind, got.reports), (mod, 'tmdl', (rep,)))

    def test_every_form_is_read_with_its_visuals(self):
        rep, mod = project(self.root)
        reader = PowerBIReader()
        for form in (self.root / 'Shop.pbip', rep, mod, self.root):
            with self.subTest(form=form.name):
                self.assertIsNone(reader.model_gap(form))
                self.assertEqual(reader.form(form), 'powerbi')
                facts = reader.parse(form, dialect='snowflake')
                self.assertEqual([u['measures'] for u in facts['usage']], [['Order Amount']])
                self.assertEqual(facts['tables'][0]['name'], 'shop.sales.orders')

    def test_a_project_whose_model_is_a_bim_is_read_with_its_report(self):
        rep, mod = project(self.root, tmsl=True)
        reader = PowerBIReader()
        self.assertEqual(pbip.resolve(rep).kind, 'bim')
        self.assertEqual(reader.form(self.root / 'Shop.pbip'), 'tabular')
        facts = reader.parse(self.root / 'Shop.pbip', dialect='snowflake')
        self.assertEqual([u['measures'] for u in facts['usage']], [['Order Amount']])

    def test_a_folder_of_several_projects_is_refused_by_name_and_never_merged(self):
        project(self.root, 'Shop', 'Shop')
        project(self.root, 'Stock', 'Stock')
        with self.assertRaises(pbip.Ambiguous) as stop:
            pbip.resolve(self.root)
        self.assertIn('Shop.pbip', str(stop.exception))
        self.assertIn('Stock.pbip', str(stop.exception))
        with self.assertRaises(ReaderRefused):
            PowerBIReader().form(self.root)
        self.assertEqual(pbip.resolve(self.root / 'Stock.pbip').model.name, 'Stock.SemanticModel')

    def test_a_model_is_every_report_over_it_and_a_report_is_itself(self):
        rep, mod = project(self.root, 'Shop', 'Shop')
        project(self.root, 'Shop Detail', 'Shop')
        self.assertEqual(len(pbip.resolve(mod).reports), 2)
        # One report handed over is that report alone, by its folder or its .pbip.
        self.assertEqual(pbip.resolve(rep).reports, (rep,))
        self.assertEqual(pbip.resolve(self.root / 'Shop.pbip').reports, (rep,))

    def test_a_report_over_a_published_model_is_a_named_gap(self):
        rep, _ = project(self.root, by_connection=True)
        reader = PowerBIReader()
        self.assertIsNone(pbip.resolve(rep))
        self.assertTrue(pbip.thin_report(self.root / 'Shop.pbip'))
        self.assertIn('published semantic model', reader.model_gap(rep))
        with self.assertRaises(ReaderRefused):
            reader.form(self.root / 'Shop.pbip')

    def test_a_fetched_extract_is_not_taken_for_a_project(self):
        extract = self.root / 'extract'
        model = extract / 'semanticModels/ws/m1'
        (model / 'definition/tables').mkdir(parents=True)
        (model / 'definition.pbism').write_text('{}')
        (model / 'definition/tables/Orders.tmdl').write_text(TABLE)
        (extract / 'reports/ws/r1').mkdir(parents=True)
        (extract / 'reports/ws/r1/definition.pbir').write_text(json.dumps(
            {'datasetReference': {'byConnection': {'connectionString': 'semanticmodelid=m1'}}}))
        self.assertIsNone(pbip.resolve(extract))
        self.assertEqual(pbip.resolve(model).reports, ())

    def test_a_kpi_trend_formula_is_carried_and_fits_the_facts_contract(self):
        import jsonschema
        _, mod = project(self.root)
        facts = PowerBIReader().parse(mod, dialect='snowflake')
        kpi = next(m['kpi'] for m in facts['measures'] if m.get('kpi'))
        self.assertEqual(kpi['trend'], '1')
        schema = json.loads((ENGINE / 'src/metrics_agent/data/schemas/bi_facts.schema.json').read_text())
        kpi_schema = schema['properties']['measures']['items']['properties']['kpi']
        jsonschema.validate({k: v for k, v in kpi.items() if not k.startswith('_')}, kpi_schema)


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class SharedWarehouseTableTest(unittest.TestCase):
    """Two model tables over one warehouse table: the table itself and a native query over it."""

    TABLES = [
        {'name': 'shop.sales.orders', 'logical_name': 'Orders', 'source_lineage': {},
         'columns': [{'name': 'Order Status', 'source_column': 'status_cd'},
                     {'name': 'Customer ID', 'source_column': 'customer_id_qc'}]},
        {'name': 'shop.sales.orders', 'logical_name': 'Order Summary',
         'source_lineage': {'native_sql': 'SELECT status_cd AS "Status" FROM shop.sales.orders'},
         'columns': [{'name': 'Order Status', 'source_column': 'Status'}]},
        {'name': 'shop.sales.customers', 'logical_name': 'Customers', 'source_lineage': {},
         'columns': [{'name': 'Customer ID', 'source_column': 'customer_id'},
                     {'name': 'Net', 'source_column': 'net', 'is_calculated': True}]}]

    def test_a_native_query_over_a_table_does_not_erase_the_tables_own_renames(self):
        got = a2._renames_by_table(self.TABLES)
        self.assertEqual(got['shop.sales.orders'], {'order status': 'status_cd', 'customer id': 'customer_id_qc'})
        self.assertEqual(a2._to_source_columns(['Order Status'], ['shop.sales.orders'], got), ['status_cd'])
        self.assertIn('Status', a2._columns_by_table(self.TABLES)['shop.sales.orders'])
        self.assertIn('status_cd', a2._columns_by_table(self.TABLES)['shop.sales.orders'])

    def test_two_direct_tables_that_disagree_leave_the_name_unmapped(self):
        twice = [self.TABLES[0], {**self.TABLES[0], 'logical_name': 'Orders Copy',
                                  'columns': [{'name': 'Order Status', 'source_column': 'other_status'}]}]
        self.assertNotIn('order status', a2._renames_by_table(twice)['shop.sales.orders'])
        self.assertEqual(a2._renames_by_table(twice)['shop.sales.orders'], {'customer id': 'customer_id_qc'})

    def test_a_relationship_key_is_named_as_each_end_reads_it(self):
        rels = [{'from_table': 'Orders', 'from_column': 'Customer ID', 'to_table': 'Customers',
                 'to_column': 'Customer ID', 'cardinality': 'M:1'},
                {'from_table': 'Order Summary', 'from_column': 'Order Status', 'to_table': 'Customers',
                 'to_column': 'Net', 'columns': [{'from_column': 'Order Status', 'to_column': 'Customer ID'}]}]
        got = bi_models.edges_on_source_columns(rels, self.TABLES)
        self.assertEqual((got[0]['from_column'], got[0]['to_column']), ('customer_id_qc', 'customer_id'))
        self.assertEqual((got[1]['from_column'], got[1]['to_column']), ('Status', 'Net'))
        self.assertEqual(got[1]['columns'], [{'from_column': 'Status', 'to_column': 'customer_id'}])
        self.assertEqual(rels[0]['from_column'], 'Customer ID')

    def test_a_filter_reaches_a_visual_by_the_models_tables_not_the_warehouses(self):
        rels = [{'from_table': 'Orders', 'to_table': 'Customers', 'is_active': True}]
        self.assertTrue(a2._related('customers', {'orders'}, rels))
        self.assertFalse(a2._related('customers', {'order summary'}, rels))

    def test_a_calculate_filter_reads_the_column_as_its_table_does(self):
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            rules = R.load(bicompile.RULES)
        formula = 'CALCULATE ( SUM ( Orders[Amount] ), Orders[Order Status] = "Open" )'
        unit = {'unit_id': 'u', 'warehouse_tables': ['shop.sales.orders'], 'group_by': [], 'filters': [],
                'join_graph': [], 'measures': [{'name': 'Open Amount', 'aggregation': None,
                                                'formula_language': 'dax', 'formula': formula}]}
        got = bicompile.compile_unit(unit, rules, 'snowflake', columns={'shop.sales.orders': {'amount_usd', 'status_cd'}},
                                     table_of={'orders': 'shop.sales.orders'}, measures={'open amount': formula},
                                     renames={('orders', 'amount'): 'amount_usd', ('orders', 'order status'): 'status_cd'})
        self.assertIn('t.status_cd', got.sql)
        self.assertIn('t.amount_usd', got.sql)
        self.assertNotIn('Order Status', got.sql)


if __name__ == '__main__':
    unittest.main()
