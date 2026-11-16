"""The live-test tool server must not widen its SQL or file capabilities."""
import importlib.util
import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/accuracy-check/scripts/comparison_tools.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('comparison_server', SCRIPT)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
RUN_SCRIPT = SCRIPT.parent / 'comparison_run.py'
run_spec = importlib.util.spec_from_file_location('comparison_run', RUN_SCRIPT)
runner = importlib.util.module_from_spec(run_spec)
run_spec.loader.exec_module(runner)


class RuntimeTest(unittest.TestCase):
    def test_failed_reaudit_clears_stale_scored_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'launch.json').write_text(json.dumps({'host': 'claude'}))
            record = {'case_id': 'q', 'arm': 'without_metrics', 'status': 'scored',
                      'score': {'grade': {'pass': 1}}, 'exit_code': 1, 'evidence_hashes': {}}
            record_path = folder / 'record.json'
            record_path.write_text(json.dumps(record))
            audited = runner.audit_existing(folder, {}, None, 'site', {}, 'without_metrics', record)
            self.assertEqual(audited['status'], 'invalid')
            self.assertNotIn('score', audited)
            self.assertIn('exited 1', audited['error'])

    def test_context_pagination_reconstructs_frozen_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp); metrics = [
                {'name': 'sales', 'description': 'Net sales', 'sql': 'SELECT 1'},
                {'name': 'orders', 'description': 'Order count', 'sql': 'SELECT 2'}]
            metric_source = json.dumps(metrics, indent=2, ensure_ascii=False)
            history_rows = [{'sql': 'SELECT sales FROM ledger', 'id': 1},
                            {'sql': 'SELECT count(*) FROM ledger', 'id': 2}]
            history_source = '\n'.join(json.dumps(row) for row in history_rows) + '\n'
            (packet / 'metrics.json').write_text(metric_source)
            (packet / 'history.jsonl').write_text(history_source)
            metric_hash = hashlib.sha256((packet / 'metrics.json').read_bytes()).hexdigest()
            history_hash = hashlib.sha256((packet / 'history.jsonl').read_bytes()).hexdigest()

            inventory = server.read_context(packet, {'kind': 'metrics', 'limit': 3000})
            self.assertEqual(json.loads(inventory['text']), [
                {'name': 'sales', 'description': 'Net sales'}, {'name': 'orders', 'description': 'Order count'}])
            named = server.read_context(packet, {'kind': 'metrics', 'metric': 'sales', 'limit': 13})
            chunks = [named['text']]
            while named['next_offset'] is not None:
                named = server.read_context(packet, {'kind': 'metrics', 'metric': 'sales',
                                                    'offset': named['next_offset'], 'limit': 13})
                chunks.append(named['text'])
            first_span = server._metric_spans(metric_source)[0]
            self.assertEqual(''.join(chunks), metric_source[first_span[1]:first_span[2]])
            self.assertEqual(server.read_context(packet, {'kind': 'history'})['text'], history_source)
            filtered = server.read_context(packet, {'kind': 'history', 'search': 'sales', 'limit': 9})
            filtered_chunks = [filtered['text']]
            while filtered['next_offset'] is not None:
                filtered = server.read_context(packet, {'kind': 'history', 'search': 'sales',
                                                       'offset': filtered['next_offset'], 'limit': 9})
                filtered_chunks.append(filtered['text'])
            self.assertEqual(''.join(filtered_chunks), json.dumps(history_rows[0]) + '\n')
            self.assertEqual(metric_hash, hashlib.sha256((packet / 'metrics.json').read_bytes()).hexdigest())
            self.assertEqual(history_hash, hashlib.sha256((packet / 'history.jsonl').read_bytes()).hexdigest())
            self.assertLessEqual(len(server.read_context(packet, {'kind': 'metrics', 'metric': 'sales', 'limit': 3000})['text']), 3000)

    def test_context_unknown_metric_traversal_and_missing_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp); (packet / 'metrics.json').write_text('[{"name":"safe","description":"ok"}]')
            self.assertEqual(server.read_context(packet, {'kind': 'metrics', 'metric': '../safe'})['error'], 'Unknown metric name')
            self.assertEqual(server.read_context(packet, {'kind': 'metrics', 'metric': 'missing'})['error'], 'Unknown metric name')
            self.assertIn('Unsupported context arguments', server.read_context(packet, {'kind': 'metrics', 'search': 'x'})['error'])
            baseline = packet / 'baseline'; baseline.mkdir()
            self.assertEqual(server.read_context(baseline, {'kind': 'metrics'}), {'unavailable': 'metrics'})

    def test_query_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE jobs (value INT)')
                db.execute('INSERT INTO jobs VALUES (7)')
            self.assertEqual(server.execute(path, 'site', 'SELECT SUM(value) FROM site.jobs')['rows'], [(7,)])
            for sql in ["DELETE FROM site.jobs", "ATTACH DATABASE ':memory:' AS other",
                        'SELECT 1; DELETE FROM site.jobs', "SELECT load_extension('anything')",
                        "WITH x AS (SELECT 1) DELETE FROM site.jobs", "SELECT readfile('/etc/passwd')"]:
                self.assertIn('error', server.execute(path, 'site', sql), sql)
            self.assertEqual(server.execute(path, 'site', 'SELECT COUNT(*) FROM site.jobs')['rows'], [(1,)])
            result = server.execute(path, 'site', 'WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<1002) SELECT * FROM x')
            self.assertTrue(result['truncated'])
            self.assertEqual(len(result['rows']), 1000)


if __name__ == '__main__':
    unittest.main()
