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
MOVES = 'retail.fct_moves'
DATES, SHIP = 'retail.dim_date', 'retail.dim_ship_date'
COLUMNS = {FACT: {'date_sk', 'ship_sk', 'site_key', 'amount', 'order_id'},
           BUDGET: {'date_sk', 'site_key', 'budget'},
           SITE: {'site_key', 'profile', 'region'},
           DATES: {'date_sk', 'calendar_date', 'retail_year'}, SHIP: {'ship_sk', 'ship_date'},
           MOVES: {'member_id', 'tier', 'change_date', 'date_sk'}}
TABLE_OF = {'sales': FACT, 'budget': BUDGET, 'site': SITE, 'date': DATES, 'ship date': SHIP,
            'moves': MOVES}
TYPES = {('date', 'calendar_date'): 'dateTime', ('ship date', 'ship_date'): 'dateTime',
         ('moves', 'change_date'): 'dateTime'}


def edge(f, t, fc, tc):
    return {'from_table': f, 'from_column': fc, 'to_table': t, 'to_column': tc,
            'cardinality': 'M:1', 'kind': 'relationship', 'is_active': True}


GRAPH = [edge(FACT, SITE, 'site_key', 'site_key'), edge(FACT, DATES, 'date_sk', 'date_sk'),
         edge(FACT, SHIP, 'ship_sk', 'ship_sk'), edge(BUDGET, SITE, 'site_key', 'site_key'),
         edge(BUDGET, DATES, 'date_sk', 'date_sk'), edge(MOVES, DATES, 'date_sk', 'date_sk')]
MEASURES = {'sales': 'SUM(Sales[amount])', 'budget': 'SUM(Budget[budget])',
            'budget by site': 'SUMX(SUMMARIZE(Budget, Budget[site_key]), [Budget])',
            'store label': 'CONCATENATEX(VALUES(Site[region]), Site[region], ", ")',
            'members moved': 'DISTINCTCOUNT(Moves[member_id])'}


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

    def test_a_measure_left_out_only_beside_the_others_gets_a_statement(self):
        def stub(u):
            # 'Toggle' cannot share a statement with 'Time'; 'Broken' compiles nowhere.
            gone = [n for n in names(u) if n == 'Broken'
                    or (n == 'Toggle' and 'Time' in names(u))]
            kept = [n for n in names(u) if n not in gone]
            return sockets.Compiled(
                sql='SELECT ' + ', '.join(kept) if kept else '',
                coverage=(sockets.REFUSED if not kept else
                          sockets.PARTIAL if gone else sockets.TRANSLATED),
                untranslatable=[{'construct': 'x', 'in_measure': n, 'why': 'no'} for n in gone],
                reason='; '.join(gone))
        u = unit(measure('Time', 'a'), measure('Toggle', 'b'), measure('Plain', 'c'),
                 measure('Broken', 'd'))
        parts, dropped = a3_translate.split_measures(u, stub, stub(u))
        self.assertEqual([names(p) for p, _r in parts], [['Time', 'Plain'], ['Toggle']])
        self.assertEqual([m['name'] for m, _w in dropped], ['Broken'])
        # Nothing that compiles on its own is left out: kept as it is.
        u2 = unit(measure('Plain', 'c'), measure('Broken', 'd'))
        self.assertIsNone(a3_translate.split_measures(u2, stub, stub(u2)))

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

    # -- a CALCULATE filter bounded by the visual's dates ------------------------------------
    MOVED = ("CALCULATE([Members Moved], KEEPFILTERS(Moves[tier] = \"GOLD\"), "
             "KEEPFILTERS(Moves[change_date] >= MIN('Date'[calendar_date])), "
             "KEEPFILTERS(Moves[change_date] <= MAX('Date'[calendar_date])))")

    def test_a_filter_bounded_by_min_and_max_of_the_date_table_compiles(self):
        res = compile_(unit(measure('Moved To Gold', self.MOVED), tables=(MOVES,)), 'duckdb')
        self.assertEqual(res.coverage, sockets.TRANSLATED, res.reason)
        self.assertIn('engine.dax_context_bound', res.rules_used)
        self.assertIn('(SELECT MIN(cs.calendar_date) FROM retail.dim_date cs)', res.sql)
        self.assertIn('(SELECT MAX(cs.calendar_date) FROM retail.dim_date cs)', res.sql)
        if not DUCKDB:
            self.skipTest('duckdb is not installed')
        import duckdb
        con = duckdb.connect()
        con.execute('create schema retail')
        con.execute('create table retail.fct_moves(member_id int, tier varchar, '
                    'change_date date, date_sk int)')
        con.execute('create table retail.dim_date(date_sk int, calendar_date date, '
                    'retail_year int)')
        con.execute("insert into retail.dim_date values (1, '2025-01-01', 2025), "
                    "(2, '2025-12-31', 2025)")
        # In range (1, 2), before the calendar (3), after it (4), blank (5: DAX's blank date
        # is day 0, so it fails >= the first date), another tier (6).
        con.execute("insert into retail.fct_moves values (1, 'Gold', '2025-03-01', 1), "
                    "(2, 'gold ', '2025-12-31', 2), (3, 'Gold', '2024-06-01', 1), "
                    "(4, 'Gold', '2026-02-01', 2), (5, 'Gold', NULL, 1), "
                    "(6, 'Silver', '2025-05-01', 1)")
        self.assertEqual(con.execute(res.sql).fetchall(), [(2,)])

    def test_a_bound_the_visual_narrows_is_refused_by_name(self):
        res = compile_(unit(measure('Moved To Gold', self.MOVED), tables=(MOVES,),
                            group_by=['retail_year'], group_by_tables=[DATES]), 'duckdb')
        self.assertFalse(res.sql)
        self.assertIn("MIN('Date'[calendar_date]) in a CALCULATE filter, where the visual "
                      "groups or filters", res.reason)
        self.assertNotIn('modifies filter context', res.reason)

    def test_a_bound_over_the_measures_own_rows_is_refused_by_name(self):
        res = compile_(unit(measure('M', "CALCULATE([Members Moved], Moves[change_date] >= "
                                         "MAX(Moves[change_date]))"), tables=(MOVES,)), 'duckdb')
        self.assertFalse(res.sql)
        self.assertIn("reads the measure's own rows", res.reason)


if __name__ == '__main__':
    unittest.main()


# -- third pass: what was still left out of the large model ------------------------------------
ORDERS, CAL = 'retail.fct_orders', 'retail.dim_calendar'
COLUMNS3 = {ORDERS: {'date_sk', 'order_id', 'amount', 'flag', 'other', 'lfl', 'channel',
                     'Customer Sk'},
            CAL: {'date_sk', 'calendar_date', 'prior_year_date', 'retail_year'}}
TABLE_OF3 = {'orders': ORDERS, 'calendar': CAL}
TYPES3 = {('calendar', 'calendar_date'): 'dateTime', ('calendar', 'prior_year_date'): 'dateTime',
          ('calendar', 'date_sk'): 'int64', ('orders', 'date_sk'): 'int64'}
GRAPH3 = [edge(ORDERS, CAL, 'date_sk', 'date_sk')]
PRIOR = "FILTER(ALL('Calendar'), 'Calendar'[calendar_date] IN VALUES('Calendar'[prior_year_date]))"
BOTH = ('VAR a = CALCULATETABLE(VALUES(Orders[order_id]), Orders[flag] = "Y", Orders[other] = "Y") '
        'VAR b = CALCULATETABLE(VALUES(Orders[order_id]), Orders[flag] = "N") '
        'VAR both = INTERSECT(a, b) RETURN COUNTROWS(both)')
EACH_DATE = """VAR Dates = SUMMARIZE('Calendar', 'Calendar'[calendar_date])
RETURN SUMX(Dates,
  VAR CurrentDate = 'Calendar'[calendar_date]
  VAR LastYearDate = CALCULATE(MAX('Calendar'[prior_year_date]),
                               FILTER(ALL('Calendar'), 'Calendar'[calendar_date] = CurrentDate))
  RETURN CALCULATE(SUM(Orders[amount]), Orders[channel] = "a",
                   FILTER(ALL('Calendar'), 'Calendar'[calendar_date] = LastYearDate)))"""


def compile3(*measures, group_by=(), group_by_tables=(), model=None, dialect='duckdb'):
    u = {'unit_id': 'u', 'warehouse_tables': [ORDERS], 'group_by': list(group_by),
         'group_by_tables': list(group_by_tables), 'measures': list(measures), 'filters': [],
         'top_n': None, 'join_graph': GRAPH3}
    return bicompile.compile_unit(u, RULES, dialect, columns=COLUMNS3, table_of=TABLE_OF3,
                                  measures=model or {}, model_graph=GRAPH3, types=TYPES3)


def orders_db():
    import duckdb
    con = duckdb.connect()
    con.execute('create schema retail')
    con.execute('create table retail.fct_orders(date_sk int, order_id int, amount double, '
                'flag varchar, other varchar, lfl varchar, channel varchar, "Customer Sk" int)')
    con.execute('create table retail.dim_calendar(date_sk int, calendar_date date, '
                'prior_year_date date, retail_year int)')
    # 2025's 28 Feb and 1 Mar both map to 1 Mar 2024; 2 Mar 2025 maps to 2 Mar 2024.
    con.execute("insert into retail.dim_calendar values (1, '2024-03-01', NULL, 2024), "
                "(2, '2024-03-02', NULL, 2024), (3, '2025-02-28', '2024-03-01', 2025), "
                "(4, '2025-03-01', '2024-03-01', 2025), (5, '2025-03-02', '2024-03-02', 2025)")
    # Order 1 has a row passing A (flag Y, other Y) and one passing B (flag N); order 2 only
    # A; order 3 only B (its flag-Y row fails `other`); order 4 only B; order 5 both, not LFL.
    con.execute("insert into retail.fct_orders values "
                "(1, 1, 10, 'Y', 'Y', 'Y', 'a', 1), (1, 1, 1, 'N', 'Y', 'Y', 'a', 1), "
                "(2, 2, 7, 'Y', 'Y', 'N', 'a', NULL), (1, 3, 1, 'Y', 'N', 'Y', 'b', 2), "
                "(1, 3, 1, 'N', 'Y', 'Y', 'b', 2), (2, 4, 1, 'N', 'N', 'N', 'b', 3), "
                "(5, 5, 100, 'Y', 'Y', 'N', 'a', 3), (5, 5, 1, 'N', 'Y', 'N', 'a', 3)")
    return con


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class StillLeftOutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CompilerUnitFallbackTest.setUpClass()

    def run_sql(self, res):
        self.assertTrue(res.sql, res.reason)
        if not DUCKDB:
            self.skipTest('duckdb is not installed')
        return sorted(orders_db().execute(res.sql).fetchall(), key=str)

    def test_a_column_name_that_is_not_an_identifier_is_quoted(self):
        res = compile3(measure('Customers', "COUNTROWS(VALUES(Orders[Customer Sk]))"))
        self.assertIn('t."Customer Sk"', res.sql)
        self.assertEqual(self.run_sql(res), [(4,)])       # 1, 2, 3 and a blank

    def test_a_block_comment_holding_a_block_comment_is_one_comment(self):
        toks = bicompile.tokenize('SUM(T[a]) /* old /* older */ VAR x = 1 */ + 1')
        self.assertEqual([v for _k, v in toks], ['SUM', '(', 'T[a]', ')', '+', '1'])
        # A nesting that does not close is read as before: the first `*/` ends it.
        toks = bicompile.tokenize('SUM(T[a]) /* old /* older */ + 1')
        self.assertEqual([v for _k, v in toks], ['SUM', '(', 'T[a]', ')', '+', '1'])

    def test_an_or_filter_beside_another_filter_keeps_its_parentheses(self):
        res = compile3(measure('M', 'CALCULATE(SUM(Orders[amount]), Orders[channel] = "b", '
                                    'Orders[flag] = "Y" || Orders[other] = "Y")'))
        self.assertIn(" AND (COALESCE(", res.sql)
        self.assertEqual(self.run_sql(res), [(2.0,)])     # order 3's two rows only

    def test_selectedvalue_beside_time_intelligence_reads_the_rows_own_value(self):
        toggle = measure('Toggle', 'VAR f = SELECTEDVALUE(Orders[lfl]) RETURN '
                                   f'SWITCH(TRUE(), f = "Y", SUM(Orders[amount]), '
                                   f'CALCULATE(SUM(Orders[amount]), {PRIOR}))')
        res = compile3(toggle, group_by=['lfl'], group_by_tables=[ORDERS])
        self.assertIn('engine.dax_time_intelligence_row_slot', res.rules_used)
        rows = dict(self.run_sql(res))
        self.assertEqual(rows['Y'], 13.0)                 # its own sum
        self.assertEqual(rows['N'], 8.0)                  # last year's: orders 2 and 4

    def test_a_prior_year_sum_over_each_date_counts_a_shared_date_once_per_date(self):
        res = compile3(measure('Sales LY', EACH_DATE))
        self.assertIn('engine.dax_time_lookup_each_date', res.rules_used)
        # 28 Feb and 1 Mar 2025 both read 1 Mar 2024 (11 of channel a); 2 Mar 2025 reads 2 Mar
        # 2024 (7): 11 + 11 + 7.
        self.assertEqual(self.run_sql(res), [(29.0,)])
        by_year = compile3(measure('Sales LY', EACH_DATE), group_by=['retail_year'],
                           group_by_tables=[CAL])
        self.assertEqual(self.run_sql(by_year), [(2025, 29.0)])

    def test_a_prior_year_sum_of_a_ratio_over_each_date_is_not_rewritten(self):
        ratio = EACH_DATE.replace('SUM(Orders[amount])', 'DIVIDE(SUM(Orders[amount]), 2)')
        self.assertIsNone(bicompile._each_date_lookup(bicompile.tokenize(ratio)))

    def test_counting_the_keys_two_calculatetables_share(self):
        res = compile3(measure('Both', BOTH))
        self.assertIn('engine.dax_intersect_count', res.rules_used)
        self.assertEqual(self.run_sql(res), [(2,)])        # orders 1 and 5
        by_channel = compile3(measure('Both', BOTH), group_by=['channel'],
                              group_by_tables=[ORDERS])
        self.assertEqual(self.run_sql(by_channel), [('a', 2), ('b', None)])
        lfl = compile3(measure('LFL Both', 'CALCULATE([Both], KEEPFILTERS(Orders[lfl] = "Y"))'),
                       model={'both': BOTH})
        self.assertEqual(self.run_sql(lfl), [(1,)])        # order 1 only

    def test_counting_shared_keys_over_a_column_the_visual_groups_by_is_refused(self):
        res = compile3(measure('Both', BOTH), group_by=['flag'], group_by_tables=[ORDERS])
        self.assertFalse(res.sql)
        self.assertIn('which the visual groups or filters by', res.reason)

    def test_treatas_of_the_prior_year_dates_is_the_lookup(self):
        f = ("CALCULATE(SUM(Orders[amount]), TREATAS(SUMMARIZE('Calendar', "
             "'Calendar'[prior_year_date]), 'Calendar'[calendar_date]))")
        res = compile3(measure('Sales LY', f))
        self.assertIn('engine.dax_time_lookup_treatas', res.rules_used)
        self.assertEqual(self.run_sql(res), [(21.0,)])     # 1 and 2 Mar 2024, once each
        grouped = compile3(measure('Sales LY', f), group_by=['retail_year'],
                           group_by_tables=[CAL])
        self.assertNotIn('engine.dax_time_lookup_treatas', grouped.rules_used)

    def test_treatas_inside_another_calculate_is_not_the_lookup(self):
        f = ("CALCULATE(CALCULATE(SUM(Orders[amount]), TREATAS(SUMMARIZE('Calendar', "
             "'Calendar'[prior_year_date]), 'Calendar'[calendar_date])), "
             "'Calendar'[retail_year] = 2025)")
        self.assertNotIn('engine.dax_time_lookup_treatas', compile3(measure('M', f)).rules_used)

    def test_an_or_row_predicate_under_a_calculate_filter_keeps_its_parentheses(self):
        res = compile3(measure('M', 'CALCULATE(COUNTROWS(FILTER(Orders, Orders[flag] = "N" '
                                    '|| Orders[other] = "N")), Orders[channel] = "a")'))
        self.assertEqual(self.run_sql(res), [(2,)])       # order 1's and order 5's N rows

    def test_a_text_bound_compares_a_blank_as_the_empty_string(self):
        COLUMNS3[CAL].add('label')
        TYPES3[('calendar', 'label')] = 'string'
        try:
            res = compile3(measure('M', "CALCULATE(SUM(Orders[amount]), KEEPFILTERS(Orders[flag] "
                                        "<= MAX('Calendar'[label])))"))
        finally:
            COLUMNS3[CAL].discard('label')
            TYPES3.pop(('calendar', 'label'))
        self.assertIn("'' <= (SELECT MAX(cs.label)", res.sql)
