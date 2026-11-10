"""Batched native checks retain the existing read-only execution boundary."""
import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import local_data
import test_local_project as fixtures
import test_metric_authoring as cards


class BatchExecution(unittest.TestCase):
    setUp = fixtures.SQLiteFlow.setUp

    def binding(self):
        return self.run.current()['execution']

    def test_one_worker_keeps_separate_results_parameters_limits_and_ids(self):
        queries = [
            {'sql': 'SELECT SUM(cost) FROM site.work WHERE kind=:kind',
             'parameters': {'kind': 'rework'}},
            {'sql': 'SELECT cost FROM site.work ORDER BY cost', 'limit': 1},
            {'sql': 'SELECT COUNT(*) FROM site.work WHERE 1=0'}]
        with patch('local_data.subprocess.run', wraps=subprocess.run) as runner:
            results = local_data.call_batch(self.binding(), queries, timeout=7)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(runner.call_args.kwargs['timeout'], 7)
        self.assertEqual([r['rows'] for r in results], [[[14.75]], [[2.25]], [[0]]])
        self.assertTrue(results[1]['truncated'])
        self.assertEqual(len({r['query_id'] for r in results}), 3)
        self.assertEqual(len({r['sql_hash'] for r in results}), 3)

    def test_later_unsafe_query_refuses_whole_batch_and_leaves_data_intact(self):
        for sql in ('DELETE FROM site.work', 'SELECT * FROM site.other',
                    'SELECT * FROM site.hidden', 'SELECT 1; SELECT 2',
                    "SELECT * FROM read_csv('/unavailable.csv')", 'SELECT random()'):
            with self.subTest(sql=sql), self.assertRaises(local_data.DataError):
                local_data.call_batch(self.binding(), [
                    {'sql': 'SELECT COUNT(*) FROM site.work'}, {'sql': sql}])
        self.assertEqual(self.run.execute({'sql': 'SELECT COUNT(*) FROM site.work'})['rows'], [[3]])

    def test_no_parameter_or_schema_checks_are_reused_from_first_statement(self):
        with self.assertRaises(local_data.DataError):
            local_data.call_batch(self.binding(), [
                {'sql': 'SELECT COUNT(*) FROM site.work'},
                {'sql': 'SELECT COUNT(*) FROM site.work WHERE kind=:kind'}])
        binding = copy.deepcopy(self.binding()); binding['schema_hash'] = 'stale'
        with self.assertRaisesRegex(local_data.DataError, 'schema changed'):
            local_data.call_batch(binding, [{'sql': 'SELECT COUNT(*) FROM site.work'}])

    def test_batch_bounds_fail_before_starting_worker(self):
        for queries in ([], [{'sql': 'SELECT 1'}] * 4,
                        [{'sql': 'SELECT 1', 'operation': 'inspect'}],
                        [{'sql': 'SELECT 1', 'limit': 1001}],
                        [{'sql': 'SELECT 1', 'parameters': []}]):
            with self.subTest(queries=queries), patch('local_data.subprocess.run') as runner:
                with self.assertRaises(local_data.DataError):
                    local_data.call_batch(self.binding(), queries)
                runner.assert_not_called()
        with patch('local_data.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)) as runner:
            with self.assertRaisesRegex(local_data.DataError, 'timed out'):
                local_data.call_batch(self.binding(), [{'sql': 'SELECT 1'}] * 3, timeout=1)
            self.assertEqual(runner.call_args.kwargs['timeout'], 1)

    def test_identical_statements_are_executed_independently_without_result_cache(self):
        results = local_data.call_batch(self.binding(), [{'sql': 'SELECT SUM(cost) FROM site.work'}] * 2)
        self.assertEqual(results[0]['rows'], results[1]['rows'])
        self.assertNotEqual(results[0]['query_id'], results[1]['query_id'])

    def test_metric_check_batches_all_three_checks_and_holds_failed_calculation(self):
        card = cards.CardExecution.card(self)
        self.run.write(self.run.context / 'metrics.json', [card])
        self.run.write(self.run.context / 'queries.json', [])
        with patch('local_data.call_batch', wraps=local_data.call_batch) as batch:
            self.run.check_metric({'name': 'cost'})
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(len(batch.call_args.args[1]), 3)
        check = self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']
        self.assertEqual(check['composition']['rows'], [[14.75]])
        self.assertEqual(check['composition']['empty_population']['rows'], [[None]])
        card['definition']['mandatory_filters'] = ["site.work.kind='rework'"]
        self.run.write(self.run.context / 'metrics.json', [card])
        with self.assertRaises(local_data.DataError):
            self.run.check_metric({'name': 'cost'})
        self.assertEqual(self.run.read(self.run.context / 'candidate-checks.json')['metric:cost']['status'], 'held')
