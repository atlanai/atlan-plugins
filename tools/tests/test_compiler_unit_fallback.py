"""A unit's measures judged one by one: a refused unit keeps the measures that compile, a part
left out is never read, aliases stay unique, and a share of total never comes out as a constant 1."""
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
    from metrics_agent import sockets
    from metrics_agent.lib import evidence
    from metrics_agent.lib import rules as R
    from metrics_agent.plugs.readers import bicompile
    from metrics_agent.stages import a3_translate

FACT, BUDGET, SITE = 'retail.fct_sales', 'retail.fct_budget', 'retail.dim_site'
DATES, SHIP = 'retail.dim_date', 'retail.dim_ship_date'
COLUMNS = {FACT: {'date_sk', 'ship_sk', 'site_key', 'amount', 'order_id'},
           BUDGET: {'date_sk', 'site_key', 'budget'},
           SITE: {'site_key', 'profile', 'region'},
           DATES: {'date_sk', 'calendar_date'}, SHIP: {'ship_sk', 'ship_date'}}
TABLE_OF = {'sales': FACT, 'budget': BUDGET, 'site': SITE, 'date': DATES, 'ship date': SHIP}
TYPES = {('date', 'calendar_date'): 'dateTime', ('ship date', 'ship_date'): 'dateTime'}


def edge(f, t, fc, tc):
    return {'from_table': f, 'from_column': fc, 'to_table': t, 'to_column': tc,
            'cardinality': 'M:1', 'kind': 'relationship', 'is_active': True}


GRAPH = [edge(FACT, SITE, 'site_key', 'site_key'), edge(FACT, DATES, 'date_sk', 'date_sk'),
         edge(FACT, SHIP, 'ship_sk', 'ship_sk'), edge(BUDGET, SITE, 'site_key', 'site_key'),
         edge(BUDGET, DATES, 'date_sk', 'date_sk')]
MEASURES = {'sales': 'SUM(Sales[amount])', 'budget': 'SUM(Budget[budget])',
            'budget by site': 'SUMX(SUMMARIZE(Budget, Budget[site_key]), [Budget])',
            'store label': 'CONCATENATEX(VALUES(Site[region]), Site[region], ", ")'}


def measure(name, formula):
    return {'name': name, 'aggregation': None, 'formula_language': 'dax', 'formula': formula}


def unit(*measures, tables=(FACT,), group_by=(), group_by_tables=()):
    return {'unit_id': 'folder', 'warehouse_tables': list(tables), 'group_by': list(group_by),
            'group_by_tables': list(group_by_tables), 'measures': list(measures),
            'filters': [], 'top_n': None, 'join_graph': GRAPH}


def compile_(u, dialect='snowflake'):
    return bicompile.compile_unit(u, RULES, dialect, columns=COLUMNS, table_of=TABLE_OF,
                                  measures=MEASURES, model_graph=GRAPH, types=TYPES)


def names(u):
    return [m['name'] for m in u['measures']]


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class CompilerUnitFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RULES
        table = yaml.safe_load(bicompile.RULES.read_text())
        languages = {r['evidence'] for r in table['rules']} - set(evidence.CORE_SOURCES)
        with mock.patch.object(evidence, 'sources', lambda: (*languages, *evidence.CORE_SOURCES)):
            RULES = R.load(bicompile.RULES)

    # -- 1. a refused unit keeps the measures that compile -------------------------------------
    def folder(self):
        return unit(measure('Sales', 'SUM(Sales[amount])'),
                    measure('Sales YTD', "TOTALYTD([Sales], 'Date'[calendar_date])"),
                    measure('Shipped YTD', "TOTALYTD([Sales], 'Ship Date'[ship_date])"),
                    measure('Store Label', MEASURES['store label']),
                    measure('Orders', 'DISTINCTCOUNT(Sales[order_id])'))

    def test_one_measure_no_longer_refuses_its_whole_folder(self):
        u = self.folder()
        whole = compile_(u)
        self.assertEqual(whole.coverage, sockets.REFUSED)     # judged as one statement
        parts, dropped = a3_translate.split_measures(u, compile_, whole)
        self.assertEqual([m['name'] for m, _why in dropped], ['Store Label'])
        self.assertIn('CONCATENATEX', dropped[0][1])
        # Two date columns cannot share one statement: each gets its own, the rest packed in.
        self.assertEqual([names(p) for p, _r in parts],
                         [['Sales', 'Sales YTD', 'Orders'], ['Shipped YTD']])
        for _p, r in parts:
            self.assertTrue(r.sql)
            self.assertNotEqual(r.coverage, sockets.REFUSED)
        first = parts[0][1]
        self.assertEqual(first.coverage, sockets.PARTIAL)
        self.assertIn("'Store Label' left out", first.reason)
        self.assertIn('Store Label', [g['in_measure'] for g in first.untranslatable])
        self.assertNotIn('store_label', first.sql)

    def test_a_measure_reading_a_dropped_measure_is_dropped_with_it(self):
        def stub(u):
            ok = 'Bad' not in names(u)
            return sockets.Compiled(sql='SELECT 1' if ok else '',
                                    coverage=sockets.TRANSLATED if ok else sockets.REFUSED,
                                    reason='' if ok else 'no rule for Bad')
        u = unit(measure('Good', 'SUM(Sales[amount])'), measure('Bad', 'NOPE()'),
                 measure('Uses Bad', '[Bad] + 1'), measure('Uses Uses Bad', "[Uses Bad] * 2"),
                 measure('Column Not Measure', "Sales[Bad]"))
        parts, dropped = a3_translate.split_measures(u, stub, stub(u))
        self.assertEqual({m['name']: why for m, why in dropped},
                         {'Bad': 'no rule for Bad',
                          'Uses Bad': 'it reads measure [bad], which is left out',
                          'Uses Uses Bad': 'it reads measure [uses bad], which is left out'})
        self.assertEqual([names(p) for p, _r in parts], [['Good', 'Column Not Measure']])

    def test_a_unit_that_compiles_is_left_as_it_is(self):
        u = unit(measure('Sales', 'SUM(Sales[amount])'), measure('Orders', 'DISTINCTCOUNT(Sales[order_id])'))
        self.assertIsNone(a3_translate.split_measures(u, compile_, compile_(u)))

    def test_a_unit_where_nothing_compiles_keeps_its_own_refusal(self):
        u = unit(measure('Store Label', MEASURES['store label']), measure('Other', 'NOPE(1)'))
        self.assertIsNone(a3_translate.split_measures(u, compile_, compile_(u)))

    # -- 5. a statement over the size cap is split by its measures -----------------------------
    def test_a_statement_over_the_size_cap_is_split_within_it(self):
        u = unit(*[measure(f'Sales {k}', f'CALCULATE([Sales], Site[region] = "R{k}")')
                   for k in range(6)])
        whole = compile_(u)
        cap = len(whole.sql) // 2
        parts, dropped = a3_translate.split_measures(u, compile_, whole, max_chars=cap)
        self.assertEqual(dropped, [])
        self.assertGreater(len(parts), 1)
        self.assertEqual(sum((names(p) for p, _r in parts), []), names(u))
        for _p, r in parts:
            self.assertLessEqual(len(r.sql), cap)
            self.assertEqual(r.coverage, sockets.TRANSLATED)
        self.assertIsNone(a3_translate.split_measures(u, compile_, whole, max_chars=0))

    # -- 2. a composite never reads a measure its inner statement left out ---------------------
    def test_a_composite_never_selects_a_column_its_inner_statement_left_out(self):
        u = unit(measure('Sales', 'SUM(Sales[amount])'), measure('Budget', 'SUM(Budget[budget])'),
                 measure('Budget By Site', MEASURES['budget by site']),
                 measure('Var To Budget', '[Sales] - [Budget]'),
                 measure('Var To Budget By Site', '[Sales] - [Budget By Site]'),
                 tables=(FACT, BUDGET))
        res = compile_(u, 'duckdb')
        self.assertEqual(res.coverage, sockets.PARTIAL, res.reason)
        self.assertIn('j.var_to_budget_part0', res.sql)
        for gone in ('budget_by_site', 'var_to_budget_by_site'):
            self.assertNotRegex(res.sql, rf'\bj\.{gone}\b')
            self.assertNotRegex(res.sql, rf'\bAS {gone}\b')
        self.assertIn('Var To Budget By Site', [g['in_measure'] for g in res.untranslatable])
        if DUCKDB:
            import duckdb
            con = duckdb.connect()
            con.execute('create schema retail')
            con.execute('create table retail.fct_sales(date_sk int, ship_sk int, site_key int, '
                        'amount double, order_id int)')
            con.execute('create table retail.fct_budget(date_sk int, site_key int, budget double)')
            con.execute('insert into retail.fct_sales values (1, 1, 1, 10, 1)')
            con.execute('insert into retail.fct_budget values (1, 1, 4)')
            self.assertEqual(con.execute(res.sql).fetchall(), [(10.0, 4.0, 6.0)])

    def test_a_composite_whose_every_measure_is_left_out_is_refused(self):
        u = unit(measure('Var To Budget By Site', '[Sales] - [Budget By Site]'),
                 tables=(FACT, BUDGET))
        res = compile_(u, 'duckdb')
        self.assertFalse(res.sql)
        self.assertIn('SUMMARIZE', res.reason)

    # -- 3. one column name per measure --------------------------------------------------------
    def test_measures_whose_names_share_an_alias_get_their_own_columns(self):
        res = compile_(unit(measure('Cost $', 'SUM(Sales[amount])'),
                            measure('Cost ($)', 'SUM(Sales[amount]) * 2'),
                            measure('Cost (USD) 2', 'SUM(Sales[amount]) + 1')), 'duckdb')
        self.assertEqual(res.coverage, sockets.TRANSLATED, res.reason)
        self.assertIn('SUM(t.amount) AS cost_usd,', res.sql)
        self.assertIn('SUM(t.amount) * 2 AS cost_usd_3', res.sql)
        self.assertRegex(res.sql, r'\+ 1 AS cost_usd_2\b')
        self.assertIsNone(bicompile._ALIASES.get())            # reset after the compile

    def test_aliases_are_untouched_when_every_name_is_its_own(self):
        self.assertEqual(bicompile._unique_aliases(unit(measure('Cost $', 'x'),
                                                        measure('Margin %', 'y'))), {})

    # -- 4. a share of total whose denominator removes a filter -------------------------------
    SHARE = "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS('Site'[profile])), 0)"

    def test_a_share_with_nothing_to_remove_is_refused_not_a_constant_one(self):
        for gb in ([], ['region']):
            res = compile_(unit(measure('Sales', 'SUM(Sales[amount])'), measure('Share', self.SHARE),
                                group_by=gb, group_by_tables=[SITE] * len(gb)), 'duckdb')
            self.assertEqual(res.coverage, sockets.PARTIAL, gb)
            self.assertNotIn(' AS share', res.sql)
            self.assertIn("'Site'[profile]", res.reason)
            self.assertIn('Share', [g['in_measure'] for g in res.untranslatable])

    def test_a_share_over_the_removed_column_is_still_compiled(self):
        res = compile_(unit(measure('Share', self.SHARE), group_by=['profile'],
                            group_by_tables=[SITE]), 'duckdb')
        self.assertEqual(res.coverage, sockets.TRANSLATED, res.reason)
        self.assertIn('OVER ()', res.sql)

    def test_a_share_removing_a_whole_table_the_statement_groups_by_is_compiled(self):
        res = compile_(unit(measure('Share', "DIVIDE([Sales], CALCULATE([Sales], ALL(Site)))"),
                            group_by=['region'], group_by_tables=[SITE]), 'duckdb')
        self.assertNotIn('Share', [g.get('in_measure') for g in res.untranslatable])


if __name__ == '__main__':
    unittest.main()
