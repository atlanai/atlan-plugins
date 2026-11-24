"""A Fabric (Direct Lake) `.bim`: measure tables, warehouse tables named by `bi_tables`, and
renamed columns read through a three-part warehouse name."""
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
    from metrics_agent.plugs.readers import bicompile, tabular
    from metrics_agent.stages.a1_parse_bi import map_tables


def _model():
    """A Direct Lake model: a fact read from OneLake, and a measure table made the way Power BI
    Desktop makes one (`ROW("Column", BLANK())`, its column NOT hidden)."""
    lake = 'Sql.Database("lake.example.fabric.microsoft.com", "0000-lakehouse")'
    return {'model': {
        'expressions': [{'name': 'DatabaseQuery', 'kind': 'm', 'expression': lake}],
        'tables': [
            {'name': 'Sales',
             'columns': [{'name': 'Date Sk', 'dataType': 'int64', 'sourceColumn': 'Date_Sk'},
                         {'name': 'Sale Amount', 'dataType': 'double',
                          'sourceColumn': 'Sale_Amount'},
                         {'name': 'Void Line Flag', 'dataType': 'string',
                          'sourceColumn': 'Void_Line_Flag'}],
             'partitions': [{'name': 'p', 'mode': 'directLake',
                             'source': {'type': 'entity', 'entityName': 'sales',
                                        'schemaName': 'dbo',
                                        'expressionSource': 'DatabaseQuery'}}]},
            {'name': 'Date',
             'columns': [{'name': 'Date Sk', 'dataType': 'int64', 'sourceColumn': 'Date_Sk'},
                         {'name': 'Calendar Date', 'dataType': 'dateTime',
                          'sourceColumn': 'Calendar_Date'},
                         {'name': 'Prior Year Date', 'dataType': 'dateTime',
                          'sourceColumn': 'Prior_Year_Date'}],
             'partitions': [{'name': 'p', 'mode': 'directLake',
                             'source': {'type': 'entity', 'entityName': 'date',
                                        'schemaName': 'dbo',
                                        'expressionSource': 'DatabaseQuery'}}]},
            {'name': 'Sales Metrics',
             'columns': [{'name': 'Column', 'type': 'calculatedTableColumn',
                          'dataType': 'string', 'sourceColumn': '[Column]'}],
             'partitions': [{'name': 'p', 'source': {
                 'type': 'calculated', 'expression': 'Row("Column", BLANK())'}}],
             'measures': [
                 {'name': 'Total Sales', 'expression': "SUM(Sales[Sale Amount])"},
                 {'name': 'Valid Sales', 'expression':
                     "CALCULATE([Total Sales], KEEPFILTERS(Sales[Void Line Flag] = \"N\"))"},
                 {'name': 'Total Sales LY', 'expression':
                     "CALCULATE([Total Sales], FILTER(ALL('Date'), 'Date'[Calendar Date] IN "
                     "VALUES('Date'[Prior Year Date])))"}]},
        ],
        'relationships': [{'name': 'r1', 'fromTable': 'Sales', 'fromColumn': 'Date Sk',
                           'toTable': 'Date', 'toColumn': 'Date Sk'}]}}


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class FabricModelInputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            cls.rules = R.load(bicompile.RULES)

    def facts(self):
        return tabular.parse_model(_model()['model'], 'model.bim', 'model')

    def test_a_blank_row_measure_table_homes_each_measure_on_the_table_it_reads(self):
        facts = self.facts()
        homes = {m['name']: (m['home_table'], m.get('shown_on')) for m in facts['measures']}
        self.assertEqual(homes['Total Sales'], ('Sales', 'Sales Metrics'))
        # Through the measure it names.
        self.assertEqual(homes['Valid Sales'], ('Sales', 'Sales Metrics'))
        mt = [t for t in facts['tables'] if (t.get('logical_name') or t['name']) == 'Sales Metrics']
        self.assertTrue(mt[0].get('measure_table'))
        # Its placeholder row is not read as a table (no refusal note for it).
        self.assertFalse(any('Sales Metrics' in n for n in facts.get('_reader_notes', [])))

    def test_bi_tables_names_the_warehouse_table_a_model_table_reads(self):
        facts = self.facts()
        missing = map_tables(facts, {'Sales': 'ANALYTICS.RETAIL.FCT_SALES',
                                     'Not In Model': 'A.B.C'})
        self.assertEqual(missing, ['Not In Model'])
        sales = next(t for t in facts['tables'] if t.get('logical_name') == 'Sales')
        self.assertEqual(sales['name'], 'ANALYTICS.RETAIL.FCT_SALES')
        lin = sales['source_lineage']
        self.assertEqual((lin['database'], lin['schema'], lin['object']),
                         ('ANALYTICS', 'RETAIL', 'FCT_SALES'))
        self.assertTrue(lin['mapped_from'])

    def test_a_renamed_column_in_a_filter_reads_its_source_through_a_three_part_name(self):
        facts = self.facts()
        map_tables(facts, {'Sales': 'ANALYTICS.RETAIL.FCT_SALES'})
        fc = bicompile.FormulaCompiler()
        model = fc.prepare(facts)
        formulas = {m['name']: m['formula'] for m in facts['measures']}
        unit = {'unit_id': 'u', 'warehouse_tables': ['ANALYTICS.RETAIL.FCT_SALES'],
                'group_by': [], 'filters': [], 'top_n': None, 'join_graph': [],
                'measures': [{'name': 'Valid Sales', 'aggregation': None,
                              'formula_language': 'dax', 'formula': formulas['Valid Sales']}]}
        res = fc.compile_unit(unit, self.rules, 'snowflake', model=model)
        self.assertEqual(res.coverage, 'translated', res.reason)
        self.assertIn('void_line_flag', res.sql.lower())
        self.assertIn('sale_amount', res.sql.lower())
        self.assertNotIn('void line flag', res.sql.lower())

    def test_a_prior_year_lookup_whose_date_edge_keeps_the_model_name(self):
        # A2 rewrites only the edge ends a unit reads; this unit reads only the fact, so the
        # edge still names `Date`, while the formula's `Date` reads ANALYTICS.RETAIL.DIM_DATE.
        facts = self.facts()
        map_tables(facts, {'Sales': 'ANALYTICS.RETAIL.FCT_SALES',
                           'Date': 'ANALYTICS.RETAIL.DIM_DATE'})
        fc = bicompile.FormulaCompiler()
        model = fc.prepare(facts)
        formulas = {m['name']: m['formula'] for m in facts['measures']}
        edge = {'from_table': 'ANALYTICS.RETAIL.FCT_SALES', 'from_column': 'Date Sk',
                'to_table': 'Date', 'to_column': 'Date Sk', 'cardinality': 'M:1',
                'kind': 'relationship', 'is_active': True}
        unit = {'unit_id': 'u', 'warehouse_tables': ['ANALYTICS.RETAIL.FCT_SALES'],
                'group_by': [], 'filters': [], 'top_n': None, 'join_graph': [edge],
                'measures': [{'name': 'Total Sales LY', 'aggregation': None,
                              'formula_language': 'dax', 'formula': formulas['Total Sales LY']}]}
        res = fc.compile_unit(unit, self.rules, 'snowflake', model=model)
        self.assertEqual(res.coverage, 'translated', res.reason)
        self.assertIn('engine.dax_date_set_lookup', res.rules_used)
        self.assertIn('analytics.retail.dim_date', res.sql.lower())


if __name__ == '__main__':
    unittest.main()
