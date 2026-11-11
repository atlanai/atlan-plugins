"""A SQLite metric card names its tables the way SQLite runs them.

The engine keys a table as DB.SCHEMA.TABLE, and a SQLite file attached as `site` is both its
database and its schema (`site.site.projects`). SQLite runs at most `site.projects.col`, so a
card that kept the engine's key failed `check-metric` on its first column.
"""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

CARD_SQL = (Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/'
            'metrics-generation/modules/metrics/engine/src/metrics_agent/lib/card_sql.py')
_spec = importlib.util.spec_from_file_location('card_sql_under_test', CARD_SQL)
card_sql = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(card_sql)
SKILLS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills'


def runnable(value, dialect, declared):
    return card_sql.without_catalog(value, declared)


def card(entity='SITE.SITE.MATERIAL_ORDERS'):
    return {
        'formula': f'SUM({entity}.WASTED_QUANTITY * {entity}.UNIT_COST_GBP)',
        'entity': entity,
        'mandatory_filters': [f"{entity}.ORDER_STATUS = 'delivered'"],
        'output': {'alias': 'waste'},
    }


class RunnableNames(unittest.TestCase):
    def test_sqlite_drops_the_repeated_level(self):
        out = runnable(card(), 'sqlite', ['SITE.SITE.MATERIAL_ORDERS'])
        self.assertEqual(out['formula'],
                         'SUM(SITE.MATERIAL_ORDERS.WASTED_QUANTITY * SITE.MATERIAL_ORDERS.UNIT_COST_GBP)')
        self.assertEqual(out['entity'], 'SITE.MATERIAL_ORDERS')
        self.assertEqual(out['mandatory_filters'], ["SITE.MATERIAL_ORDERS.ORDER_STATUS = 'delivered'"])

    def test_export_rewrites_only_sqlite_definitions(self):
        import sys
        for path in (SKILLS / 'metrics-generation', SKILLS / 'metrics-generation/modules/metrics',
                     SKILLS / 'metrics-generation/modules/metrics/engine/src'):
            sys.path.insert(0, str(path))
        try:
            from modules.metrics.export import metric_body
        except ImportError as exc:
            self.skipTest(f'export needs its dependencies: {exc}')
        entity = 'SITE.SITE.MATERIAL_ORDERS'
        example = {'sql': 'SELECT 1 FROM site.material_orders', 'question': 'q'}
        for dialect, expected in (('sqlite', 'SITE.MATERIAL_ORDERS'), ('snowflake', entity),
                                  ('duckdb', entity)):
            body = metric_body({**card(entity), 'name': 'waste', 'description': 'd', 'source_id': 's',
                                'alias': None, 'dialect': dialect, 'aggregation': {'type': 'sum'},
                                'examples': [example]}, {'layer_sha': 'x'}, 'c')['definition']
            self.assertEqual(body['source']['entity'], expected, dialect)
            self.assertTrue(body['formula'].startswith(f'SUM({expected}.WASTED_QUANTITY'), dialect)
            self.assertEqual(body['extensions']['metrics_agent']['card']['entity'], entity)
            self.assertEqual(body['examples'], [example])

    def test_only_declared_tables_with_a_repeated_level(self):
        text = 'a.b.t.c + x.x.other.c + site.site.material_orders_v2.c'
        self.assertEqual(runnable(text, 'sqlite', ['a.b.t', 'site.site.material_orders']), text)
        self.assertEqual(runnable(text, 'sqlite', ['x.x.other']), 'a.b.t.c + x.other.c + site.site.material_orders_v2.c')

    def test_literals_and_quoted_names(self):
        text = """"SITE"."SITE"."CREW".rate WHERE note = 'site.site.crew.rate'"""
        self.assertEqual(runnable(text, 'sqlite', ['SITE.SITE.CREW']),
                         """"SITE"."CREW".rate WHERE note = 'site.site.crew.rate'""")

    def test_case_insensitive_and_join_targets(self):
        text = 'FROM site.site.work_logs INNER JOIN SITE.SITE.CREW ON SITE.SITE.CREW.ID = site.site.work_logs.crew_id'
        self.assertEqual(runnable(text, 'SQLite', ['SITE.SITE.WORK_LOGS', 'SITE.SITE.CREW']),
                         'FROM site.work_logs INNER JOIN SITE.CREW ON SITE.CREW.ID = site.work_logs.crew_id')

    def test_composed_statement_runs_on_an_attached_file(self):
        con = sqlite3.connect(':memory:')
        con.execute("ATTACH DATABASE ':memory:' AS site")
        con.execute('CREATE TABLE site.material_orders (wasted_quantity, unit_cost_gbp, order_status)')
        con.executemany('INSERT INTO site.material_orders VALUES (?, ?, ?)',
                        [(2, 10.0, 'delivered'), (5, 1.0, 'cancelled')])
        before = card_sql.canonical_statement(card())
        with self.assertRaises(sqlite3.OperationalError):
            con.execute(before)
        after = card_sql.canonical_statement(runnable(card(), 'sqlite', ['SITE.SITE.MATERIAL_ORDERS']))
        self.assertEqual(con.execute(after).fetchall(), [(20.0,)])


if __name__ == '__main__':
    unittest.main()
