"""Regressions for synthetic metric calculations, source aliases, and catalogue ingestion."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema', 'duckdb'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    sys.path.insert(0, str(ENGINE / 'packages/metrics-agent-snowflake/src'))
    import duckdb
    from metrics_agent.lib import artifacts, metric_validation as checks
    from metrics_agent.stages import x1_propose, x3_emit
    from metrics_agent_snowflake.plug import SnowflakeDirect
    from metrics_agent_snowflake.semantic_view import SemanticViewReader


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics runtime dependencies')
class MetricValidation(unittest.TestCase):
    def test_snapshots_must_keep_their_months_separate(self):
        with duckdb.connect() as db:
            db.execute('CREATE TABLE activity (snapshot_month INT, mau INT)')
            db.execute('INSERT INTO activity VALUES (1, 100), (2, 120)')
            raw = 'SELECT SUM(mau) FROM activity'
            self.assertEqual(db.execute(raw).fetchall(), [(220,)])
            self.assertIn('sums a snapshot across periods',
                          checks.snapshot_problem('activity.snapshot_month', raw, 'duckdb'))
            for clause, result in (('WHERE snapshot_month = 2', [(120,)]),
                                   ('GROUP BY snapshot_month ORDER BY snapshot_month', [(100,), (120,)])):
                sql = f'{raw} {clause}'
                self.assertIsNone(checks.snapshot_problem('activity.snapshot_month', sql, 'duckdb'))
                self.assertEqual(db.execute(sql).fetchall(), result)

    def test_x1_keeps_the_full_source_for_a_new_total(self):
        class Context:
            def __init__(self, run):
                from types import SimpleNamespace

                self.run = run
                self.args = SimpleNamespace(answers=[str(run / 'answer.json')])
                self.output = None

            def read(self, *_):
                return {'dialect': 'duckdb', 'objects': [{
                    'vq_id': 'test', 'sql': 'SELECT id, sales FROM accounts', 'tables': ['accounts'],
                }]}

            def write(self, name, value, schema):
                artifacts.validate(value, schema, what=name)
                self.output = value

            def authored(self, *_args, **_kwargs):
                pass

            def note_input_path(self, *_args):
                pass

            def count(self, **_):
                pass

            def say(self, *_):
                pass

        with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as db:
            root = Path(tmp)
            (root / 'answer.json').write_text(json.dumps({'statements': [{
                'vq_id': 'test', 'verdict': 'metrics', 'metrics': [{
                    'name': 'Total Sales', 'expression': 'SUM(sales)', 'entity': 'accounts',
                    'why_a_metric': 'Tracks sales across accounts.', 'aggregation': 'sum',
                }],
            }]}))
            ctx = Context(root)
            x1_propose._ingest(ctx)
            [proposal] = ctx.output['proposals']
            formula, source = checks.sum_over_output(
                proposal['expression'], ctx.read()['objects'][0]['sql'], 'duckdb')
            self.assertEqual(source, proposal['rollup_source'])
            db.execute('CREATE TABLE accounts (id INT, sales INT)')
            db.execute('INSERT INTO accounts VALUES (1, 40), (2, 60)')
            self.assertEqual(db.execute(f'SELECT {formula} FROM {source}').fetchall(), [(100,)])
            self.assertIsNone(checks.sum_over_output('SUM(missing)', 'SELECT sales FROM accounts', 'duckdb'))

    def test_source_aliases_are_visible_only_in_their_own_scope(self):
        bracketed = '(a acc JOIN b u ON acc.id = u.id)'
        union = '((SELECT id FROM a) UNION ALL (SELECT id FROM b)) acc'
        self.assertEqual(checks.source_relations(bracketed, 'duckdb'), ({'acc', 'u'}, [], []))
        self.assertEqual(checks.source_relations(union, 'duckdb'), ({'acc'}, [], []))
        self.assertEqual(checks.source_relations('a JOIN b ON missing.id = b.id', 'duckdb')[2],
                         ['missing.id', 'b.id'])

    def test_the_warehouse_witness_holds_wrong_numbers_and_multirow_lookups(self):
        class Ctx:
            def say(self, message):
                pass

        class Runner:
            def connect(self):
                pass

            def close(self):
                pass

            def fetch(self, sql, limit):
                cursor = db.execute(sql)
                return [d[0] for d in cursor.description], cursor.fetchmany(limit)

        source = {'entity': 'costs'}
        lookup = {'name': 'Lookup', 'formula': 'cost', 'source': source, 'dimensions': []}
        total = {'name': 'Total', 'formula': 'SUM(cost)', 'source': source, 'dimensions': []}
        with duckdb.connect() as db:
            db.execute('CREATE TABLE costs (cost INT)')
            db.execute('INSERT INTO costs VALUES (100), (120), (160)')
            with mock.patch.object(x3_emit._runtime, 'sql_runner', return_value=Runner()):
                problems = x3_emit._card_witness([lookup, total], {'witness_mode': 'execute'},
                                               'duckdb', Ctx(), metric_checks={
                                                   'total': {'expected_value': 350},
                                               })
        self.assertEqual(len(problems), 2)
        self.assertIn('more than one row', problems[0])
        self.assertIn('differs from the expected value', problems[1])

    def test_snowflake_catalogue_fetch_keeps_exact_sql_and_expected_value(self):
        statement = ("SELECT COUNT(*) AS active FROM DEMO.OPS.CUSTOMERS "
                     "WHERE PLAN = 'paid' AND IS_TEST = FALSE")

        class Runner:
            def connect(self):
                pass

            def close(self):
                pass

            def columns_of(self, table):
                assert table == 'DEMO.OPS.DIM_MEASURE'
                return ['measure_name', 'exact_sql', 'required_filters', 'verified_value', 'unused']

            def fetch(self, sql, limit):
                assert sql == ('SELECT measure_name, exact_sql, required_filters, verified_value '
                               'FROM DEMO.OPS.DIM_MEASURE')
                return [], [('Active Customers', statement, "PLAN = 'paid'", 350)]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plug = SnowflakeDirect({'metric_catalogue': 'DEMO.OPS.DIM_MEASURE'})
            with mock.patch.object(plug, 'sql_runner', return_value=Runner()), mock.patch(
                    'metrics_agent.lib.policy.load') as load:
                load.return_value.get.return_value = 10000
                [extract] = plug.bi_definitions([], root)
            reader = SemanticViewReader()
            self.assertTrue(reader.detect(extract))
            facts = reader.parse(extract)
            artifacts.validate(facts, 'bi_facts.schema.json', what='BI facts')
            [measure] = facts['measures']
            declaration = measure['declared']
            self.assertEqual(declaration['expected_value'], 350)
            self.assertEqual(declaration['mandatory_filters'], ["PLAN = 'paid'"])
            self.assertEqual(declaration['statement_sql'], statement)
            self.assertIn(statement, reader.compile_unit({'declared': declaration}, None, 'snowflake').sql)

            import csv
            export = root / 'DIM_MEASURE.csv'
            with export.open('w', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['MEASURE_NAME', 'EXACT_SQL', 'VERIFIED_VALUE'])
                writer.writerow(['Active Customers', statement, 350])
            self.assertTrue(reader.detect(export))
            [measure] = reader.parse(export)['measures']
            self.assertEqual(measure['declared']['expected_value'], 350)
