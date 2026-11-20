"""DAX comments, DIVIDE with an alternate, and the prior-year lookup through a date table."""
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

SALES = 'retail.fct_sales'
DATES = 'retail.dim_date'
COLUMNS = {SALES: {'date_sk', 'amount', 'units_sold', 'units_received', 'region'},
           DATES: {'date_sk', 'calendar_date', 'prior_year_date', 'retail_year'}}
TABLE_OF = {'sales': SALES, 'date': DATES}
TYPES = {('date', 'date_sk'): 'int64', ('date', 'calendar_date'): 'dateTime',
         ('date', 'prior_year_date'): 'dateTime', ('sales', 'date_sk'): 'int64'}
EDGE = {'from_table': SALES, 'from_column': 'date_sk', 'to_table': DATES,
        'to_column': 'date_sk', 'cardinality': 'M:1', 'kind': 'relationship',
        'is_active': True}
LOOKUP = ("FILTER(ALL('Date'), 'Date'[calendar_date] IN VALUES('Date'[prior_year_date]))")


def measure(formula, name='M'):
    return {'name': name, 'aggregation': None, 'formula_language': 'dax', 'formula': formula}


def compile_measures(*measures, dialect='duckdb', by_year=False, graph=(EDGE,)):
    unit = {'unit_id': 'u', 'warehouse_tables': [SALES] + ([DATES] if by_year else []),
            'group_by': ['retail_year'] if by_year else [],
            'group_by_tables': [DATES] if by_year else [],
            'measures': list(measures), 'filters': [], 'top_n': None,
            'join_graph': list(graph)}
    return bicompile.compile_unit(unit, RULES, dialect, columns=COLUMNS, table_of=TABLE_OF,
                                  types=TYPES)


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class DaxCompilerGapsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RULES
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            RULES = R.load(bicompile.RULES)

    # -- comments -------------------------------------------------------------------------
    def test_dax_line_and_block_comments_are_dropped(self):
        toks = bicompile.tokenize('-- DISTINCTCOUNT(T[a])\nCOUNTROWS(VALUES(T[a])) /* old */ + 1 // n')
        self.assertEqual([v for _k, v in toks], ['COUNTROWS', '(', 'VALUES', '(', 'T[a]', ')', ')',
                                                 '+', '1'])

    def test_a_comment_marker_inside_a_name_or_string_is_kept(self):
        toks = bicompile.tokenize('[Net -- Gross] & "a /* b */ -- c" & \'T--x\'[c]')
        self.assertEqual([v for _k, v in toks][0], '[Net -- Gross]')
        self.assertIn('"a /* b */ -- c"', [v for _k, v in toks])
        self.assertIn("'T--x'[c]", [v for _k, v in toks])

    def test_an_unterminated_block_comment_ends_the_formula(self):
        toks = bicompile.tokenize('SUM(T[a]) /* never closed')
        self.assertEqual([v for _k, v in toks], ['SUM', '(', 'T[a]', ')'])

    def test_tableau_keeps_double_minus(self):
        # Tableau has no `--` comment: `[a]--[b]` is a subtraction of a negation.
        toks = bicompile.tokenize('[a]--[b]', dax=False)
        self.assertEqual([v for _k, v in toks], ['[a]', '-', '-', '[b]'])
        self.assertEqual([v for _k, v in bicompile._tokens('[a]--[b]', 'tableau-calc')],
                         ['[a]', '-', '-', '[b]'])

    def test_a_commented_out_old_version_compiles_the_live_one(self):
        res = compile_measures(measure(
            '/* CALCULATE(SUM(Sales[amount]), Sales[region] = "South") */\n'
            '-- SUM(Sales[units_sold])\nSUM(Sales[amount])'))
        self.assertEqual(res.coverage, 'translated', res.reason)
        self.assertIn('SUM(t.amount)', res.sql)
        self.assertNotIn('units_sold', res.sql)

    # -- DIVIDE ---------------------------------------------------------------------------
    def test_divide_with_an_alternate_returns_it_on_a_zero_or_blank_denominator(self):
        res = compile_measures(measure('DIVIDE(SUM(Sales[units_sold]), SUM(Sales[units_received]), 0)'))
        self.assertEqual(res.coverage, 'translated', res.reason)
        self.assertIn('CASE WHEN COALESCE(SUM(t.units_received), 0) = 0 THEN 0 '
                      'ELSE SUM(t.units_sold) / SUM(t.units_received) END', res.sql)

    def test_divide_with_four_arguments_is_still_refused(self):
        res = compile_measures(measure('DIVIDE(SUM(Sales[units_sold]), SUM(Sales[units_received]), 0, 1)'))
        self.assertNotEqual(res.coverage, 'translated')

    # -- prior-year lookup ----------------------------------------------------------------
    def test_prior_year_lookup_reads_the_mapped_dates_through_the_key(self):
        res = compile_measures(measure('SUM(Sales[amount])', 'Sales'),
                               measure(f'CALCULATE(SUM(Sales[amount]), {LOOKUP})', 'Sales LY'),
                               dialect='snowflake', by_year=True)
        self.assertEqual(res.coverage, 'translated', res.reason)
        self.assertIn('engine.dax_date_set_lookup', res.rules_used)
        self.assertIn('d.prior_year_date AS dt', res.sql)
        self.assertIn('JOIN retail.dim_date s ON s.calendar_date = c.dt', res.sql)
        self.assertIn('JOIN retail.fct_sales t ON t.date_sk = c.dt', res.sql)

    def test_a_lookup_without_values_is_not_read_as_a_date_set(self):
        res = compile_measures(measure(
            "CALCULATE(SUM(Sales[amount]), FILTER(ALL('Date'), 'Date'[calendar_date] IN "
            "{DATE(2024, 1, 1)}))"), dialect='snowflake', by_year=True)
        self.assertNotIn('engine.dax_date_set_lookup', res.rules_used)

    def test_a_lookup_with_no_relationship_to_the_date_table_is_not_compiled(self):
        res = compile_measures(measure(f'CALCULATE(SUM(Sales[amount]), {LOOKUP})'),
                               dialect='snowflake', by_year=True, graph=())
        self.assertNotEqual(res.coverage, 'translated')
        self.assertNotIn('engine.dax_date_set_lookup', res.rules_used)


if __name__ == '__main__':
    unittest.main()


# -- second pass: cross-fact measures, dimension context tests, calculated tables, keys -----
DUCKDB = importlib.util.find_spec('duckdb') is not None
FACT, BUDGET, SESSIONS, PRODUCT = 'retail.fct_sales', 'retail.fct_budget', 'retail.fct_sessions', 'retail.dim_product'
COLUMNS2 = {FACT: {'date_sk', 'amount', 'product_key', 'online', 'order_id'},
            BUDGET: {'date_sk', 'budget'}, SESSIONS: {'date_sk', 'sessions'},
            DATES: {'date_sk', 'calendar_date'}, PRODUCT: {'product_key', 'brand_flag'}}
TABLE_OF2 = {'sales': FACT, 'budget': BUDGET, 'sessions': SESSIONS, 'date': DATES, 'product': PRODUCT}


def edge(f, t, fc, tc):
    return {'from_table': f, 'from_column': fc, 'to_table': t, 'to_column': tc,
            'cardinality': 'M:1', 'kind': 'relationship', 'is_active': True}


GRAPH2 = [edge(FACT, DATES, 'date_sk', 'date_sk'), edge(BUDGET, DATES, 'date_sk', 'date_sk'),
          edge(SESSIONS, DATES, 'date_sk', 'date_sk'), edge(FACT, PRODUCT, 'product_key', 'product_key')]
MEASURES2 = {'sales': 'SUM(Sales[amount])', 'budget': 'SUM(Budget[budget])',
             'orders': 'DISTINCTCOUNT(Sales[order_id])', 'sessions': 'SUM(Sessions[sessions])'}


def duck():
    import duckdb
    con = duckdb.connect()
    con.execute('create schema retail')
    con.execute('create table retail.fct_sales(date_sk int, amount double, product_key int, online varchar, order_id int)')
    con.execute('create table retail.fct_budget(date_sk int, budget double)')
    con.execute('create table retail.fct_sessions(date_sk int, sessions int)')
    con.execute('create table retail.dim_date(date_sk int, calendar_date date)')
    con.execute('create table retail.dim_product(product_key int, brand_flag varchar)')
    con.execute("insert into retail.fct_sales values (1,10,1,'Y',1),(1,20,2,'N',2),(2,30,1,'Y',3)")
    con.execute('insert into retail.fct_budget values (1,25),(2,40)')
    con.execute('insert into retail.fct_sessions values (1,100),(2,300)')
    con.execute("insert into retail.dim_date values (1,'2025-01-01'),(2,'2025-01-02')")
    con.execute("insert into retail.dim_product values (1,'Y'),(2,'N')")
    return con


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class CrossFactAndKeysTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        DaxCompilerGapsTest.setUpClass()

    def compile(self, formula, tables, measures=MEASURES2, renames=None):
        unit = {'unit_id': 'u', 'warehouse_tables': tables, 'group_by': [], 'filters': [],
                'top_n': None, 'join_graph': GRAPH2,
                'measures': [measure(formula, 'M')]}
        return bicompile.compile_unit(unit, RULES, 'duckdb', columns=COLUMNS2, table_of=TABLE_OF2,
                                      measures=measures, model_graph=GRAPH2, renames=renames)

    def value(self, res):
        self.assertEqual(res.coverage, 'translated', res.reason)
        if not DUCKDB:
            self.skipTest('duckdb is not installed')
        return duck().execute(res.sql).fetchall()[0][-1]

    def test_a_variable_holding_a_difference_of_two_facts_is_two_statements(self):
        res = self.compile('VAR v = [Sales] - [Budget] RETURN v', [FACT, BUDGET])
        self.assertEqual(self.value(res), (10 + 20 + 30) - (25 + 40))

    def test_a_variable_used_inside_calculate_is_not_inlined(self):
        self.assertIsNone(bicompile._inline_vars(bicompile.tokenize(
            'VAR v = [Sales] RETURN CALCULATE(v, Sales[online] = "Y")')))

    def test_calculate_over_two_facts_filters_only_the_fact_it_reaches(self):
        # The online filter is on the sales fact; no relationship carries it to sessions.
        res = self.compile('CALCULATE(DIVIDE([Orders], [Sessions], 0), Sales[online] = "Y")',
                           [FACT, SESSIONS])
        self.assertAlmostEqual(self.value(res), 2 / 400)

    def test_selectedvalue_of_an_unfiltered_dimension_reads_the_dimension(self):
        res = self.compile('VAR p = SELECTEDVALUE(Product[brand_flag]) RETURN '
                           'SWITCH(TRUE(), p = "Y", BLANK(), [Budget])', [BUDGET])
        self.assertIn('FROM retail.dim_product h', res.sql)
        self.assertEqual(self.value(res), 65)

    def test_relationship_columns_are_joined_under_the_names_the_tables_read(self):
        graph = [edge(FACT, PRODUCT, 'Product Key', 'Product Key')]
        renames = {('fct_sales', 'product key'): 'product_key', ('dim_product', 'product key'): 'product_key'}
        self.assertEqual(bicompile._renamed_edges(graph, renames)[0]['from_column'], 'product_key')
        self.assertEqual(bicompile._renamed_edges(graph, renames)[0]['to_column'], 'product_key')

    def test_calculated_table_columns_take_the_statements_names(self):
        from metrics_agent.plugs.readers import daxtables
        tables = [{'name': 'Channel', 'columns': [
            {'name': 'Online Flag', 'is_calculated': True, 'source_column': '[online flag]'},
            {'name': 'Label', 'is_calculated': True, 'formula': '"x"'}],
            '_dax_table': 'DATATABLE("Online Flag", STRING, {{"Y"}, {"N"}})'}]
        daxtables.read_calculated_tables(tables, 'snowflake')
        cols = {c['name']: c for c in tables[0]['columns']}
        self.assertEqual(cols['Online Flag']['source_column'], 'Online_Flag')
        self.assertFalse(cols['Online Flag']['is_calculated'])
        self.assertTrue(cols['Label']['is_calculated'])     # a calculated COLUMN stays one
