"""Synthetic fixtures verify fail-closed reporting; these are never live model evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/accuracy-check/scripts'


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prepare = module('comparison')
report = module('comparison_report')


class ReportTest(unittest.TestCase):
    def test_codex_startup_warning_allowlist_is_narrow(self):
        self.assertTrue(report.codex_startup_warning({'type': 'error', 'message':
            'Under-development features enabled: code_mode. Under-development features are incomplete.'}))
        self.assertFalse(report.codex_startup_warning({'type': 'error', 'message':
            'Unknown item type from Codex'}))
        self.assertFalse(report.codex_startup_warning({'type': 'error', 'message':
            'Under-development feature warning: code_mode'}))
        self.assertFalse(report.codex_startup_warning({'type': 'agent_message', 'message':
            'Under-development features enabled: code_mode.'}))

    def setup_comparison(self, root, host='claude'):
        args = {'root': root / 'comparisons', 'model': 'fixture-model', 'effort': 'unsupported', 'harness': 'fixture',
                'host': host, 'execution_schema': 'site'}
        values = {'cases': {'case_id': 'q', 'question': '<script>test</script>'},
                  'gold': {'case_id': 'q', 'gold_sql': 'SELECT 7'}, 'metrics': [], 'schema': {}, 'history': {}, 'data': {}}
        for name, value in values.items():
            args[name] = root / name
            args[name].write_text(json.dumps(value))
        return Path(prepare.prepare(**args)['directory'])

    def fake_execution(self, comparison, arm, host='claude'):
        folder = comparison / 'executions' / arm / report.case_key('q')
        folder.mkdir(parents=True)
        answer = {'answer': '7', 'sql': 'SELECT 7', 'used_context_ids': []}
        result = {'columns': ['n'], 'rows': [[7]], 'truncated': False}
        tools = [{'tool': 'read_context', 'arguments': {'kind': 'schema'}, 'result': {'text': '{}'}},
                 {'tool': 'execute_sql', 'arguments': {'sql': 'SELECT 7'}, 'result': result}]
        if host == 'claude':
            events = [{'type': 'system', 'subtype': 'init', 'model': 'fixture-model',
                       'tools': ['mcp__data__read_context', 'mcp__data__execute_sql']},
                      {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'mcp__data__' + t['tool'], 'input': t['arguments']} for t in tools]}},
                      {'type': 'result', 'session_id': 'fixture-session', 'is_error': False, 'result': json.dumps(answer)}]
        else:
            events = [{'type': 'thread.started', 'thread_id': 'fixture-thread'}]
            events.extend({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'server': 'data',
                           'tool': t['tool'], 'arguments': t['arguments']}} for t in tools)
            events.extend([{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(answer)}},
                           {'type': 'turn.completed'}])
            (folder / 'native-transcript.jsonl').write_text(json.dumps({'type': 'turn_context', 'payload':
                {'model': 'fixture-model', 'effort': 'unsupported'}}))
        (folder / 'events.jsonl').write_text('\n'.join(map(json.dumps, events)))
        (folder / 'tools.jsonl').write_text('\n'.join(map(json.dumps, tools)))
        (folder / 'prompt.txt').write_text('<script>test</script>')
        server_sha = 'fixture-server-sha'
        command = (['claude', '--model', 'fixture-model'] if host == 'claude' else
                   ['codex', 'exec', '-m', 'fixture-model', '-c', 'model_reasoning_effort="unsupported"'])
        (folder / 'launch.json').write_text(json.dumps({'host': host, 'execution_schema': 'site', 'command': command,
                                                        'server_sha256': server_sha}))
        (folder / 'gold-execution.json').write_text(json.dumps({'sql': 'SELECT 7', 'result': result,
                                                                'database_sha256': report.load(comparison / 'manifest.json')['inputs']['data'],
                                                                'trusted_sha256': report.load(comparison / 'manifest.json')['inputs']['gold'],
                                                                'executor_sha256': server_sha}))
        record = {'case_id': 'q', 'arm': arm, 'status': 'scored', 'model': 'fixture-model', 'effort': 'unsupported',
                  'isolation': 'capability_allowlist_and_trace_audit', 'session_id': 'fixture-session',
                  'answer': answer, 'agent_result': result, 'gold_result': result,
                  'score': report.scorer.grade(result, result, 'SELECT 7', 'SELECT 7'),
                  'evidence_hashes': {p.name: report.sha(p) for p in folder.iterdir()}}
        (folder / 'record.json').write_text(json.dumps(record))
        return folder

    def test_format3_execution_host_and_schema_must_match(self):
        for field, changed in (('host', 'codex'), ('execution_schema', 'other')):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); comparison = self.setup_comparison(root)
                self.fake_execution(comparison, 'without_metrics')
                self.fake_execution(comparison, 'with_metrics')
                manifest_path = comparison / 'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                manifest[field] = changed
                manifest['baseline_key'] = report.baseline_identity(manifest)
                manifest_path.write_text(json.dumps(manifest))
                expected = 'host differs' if field == 'host' else 'schema differs'
                with self.assertRaisesRegex(ValueError, expected):
                    report.collect(comparison)

    def test_native_codex_effort_must_match_even_if_evidence_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); comparison = self.setup_comparison(root, host='codex')
            folder = self.fake_execution(comparison, 'without_metrics', host='codex')
            self.fake_execution(comparison, 'with_metrics', host='codex')
            native_path = folder / 'native-transcript.jsonl'
            native_path.write_text(json.dumps({'type': 'turn_context', 'payload':
                {'model': 'fixture-model', 'effort': 'high'}}))
            record_path = folder / 'record.json'; record = json.loads(record_path.read_text())
            record['evidence_hashes']['native-transcript.jsonl'] = report.sha(native_path)
            record_path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'actual effort'):
                report.collect(comparison)

    def test_pending_report_never_claims_accuracy_and_escapes_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            comparison = self.setup_comparison(Path(tmp))
            self.assertFalse(report.collect(comparison)['complete'])
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                report.persist_baseline(comparison, Path(tmp) / 'cache')
            html = report.render(comparison).read_text()
            self.assertIn('Comparison in progress', html)
            self.assertNotIn('<script>test</script>', html)
            self.assertIn('&lt;script&gt;test&lt;/script&gt;', html)

    def test_complete_cache_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); comparison = self.setup_comparison(root)
            baseline = self.fake_execution(comparison, 'without_metrics')
            self.fake_execution(comparison, 'with_metrics')
            self.assertEqual(report.collect(comparison)['totals']['both_right'], 1)
            cache = report.persist_baseline(comparison, root / 'cache')
            self.assertTrue(cache.is_dir())
            saved = json.loads((baseline / 'record.json').read_text())
            saved['score']['grade']['pass'] = 0
            (baseline / 'record.json').write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError, 'rescoring'):
                report.collect(comparison)
            saved['score']['grade']['pass'] = 1
            (baseline / 'record.json').write_text(json.dumps(saved))
            (baseline / 'events.jsonl').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                report.collect(comparison)

    def test_cache_key_and_context_packet_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); comparison = self.setup_comparison(root)
            self.fake_execution(comparison, 'without_metrics')
            self.fake_execution(comparison, 'with_metrics')
            manifest_path = comparison / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['baseline_key'] = 'forged'
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'baseline identity'):
                report.collect(comparison)
            manifest['baseline_key'] = report.baseline_identity(manifest)
            manifest_path.write_text(json.dumps(manifest))
            (comparison / 'without_metrics' / 'schema.json').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'context packet'):
                report.collect(comparison)

    def test_reversed_manifest_arms_cannot_invert_reported_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); comparison = self.setup_comparison(root)
            self.fake_execution(comparison, 'without_metrics')
            with_metrics = self.fake_execution(comparison, 'with_metrics')
            wrong = {'columns': ['n'], 'rows': [[8]], 'truncated': False}
            tools_path = with_metrics / 'tools.jsonl'
            tools = [json.loads(line) for line in tools_path.read_text().splitlines()]
            tools[-1]['arguments']['sql'] = 'SELECT 8'
            tools[-1]['result'] = wrong
            tools_path.write_text('\n'.join(map(json.dumps, tools)))
            events_path = with_metrics / 'events.jsonl'
            events = [json.loads(line) for line in events_path.read_text().splitlines()]
            calls = events[1]['message']['content']
            calls[-1]['input']['sql'] = 'SELECT 8'
            answer = {'answer': '8', 'sql': 'SELECT 8', 'used_context_ids': []}
            events[-1]['result'] = json.dumps(answer)
            events_path.write_text('\n'.join(map(json.dumps, events)))
            record_path = with_metrics / 'record.json'
            record = json.loads(record_path.read_text())
            record['answer'] = answer
            record['agent_result'] = wrong
            record['score'] = report.scorer.grade(record['gold_result'], wrong, 'SELECT 7', 'SELECT 8',
                                                  report.scorer.case_sig({'case_id': 'q', 'gold_sql': 'SELECT 7'}))
            record['evidence_hashes'] = {p.name: report.sha(p) for p in with_metrics.iterdir()
                                         if p.is_file() and p.name != 'record.json'}
            record_path.write_text(json.dumps(record))
            self.assertEqual(report.collect(comparison)['totals']['regression'], 1)

            manifest_path = comparison / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['arms'].reverse()
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'arms must be ordered'):
                report.collect(comparison)

    def test_gold_execution_receipt_must_match_record_and_frozen_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); comparison = self.setup_comparison(root)
            folder = self.fake_execution(comparison, 'without_metrics')
            self.fake_execution(comparison, 'with_metrics')
            receipt_path = folder / 'gold-execution.json'
            receipt = json.loads(receipt_path.read_text()); receipt['result'] = {'columns': ['n'], 'rows': [[8]], 'truncated': False}
            receipt_path.write_text(json.dumps(receipt))
            record_path = folder / 'record.json'; record = json.loads(record_path.read_text())
            record['evidence_hashes']['gold-execution.json'] = report.sha(receipt_path)
            record_path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'Gold execution receipt'):
                report.collect(comparison)

    def test_controller_replay_is_anchored_and_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); comparison = self.setup_comparison(root)
            folder = self.fake_execution(comparison, 'without_metrics')
            self.fake_execution(comparison, 'with_metrics')
            answer = {'answer': '7', 'sql': 'SELECT 7 ', 'used_context_ids': []}
            events_path = folder / 'events.jsonl'
            events = [json.loads(line) for line in events_path.read_text().splitlines()]
            events[-1]['result'] = json.dumps(answer)
            events_path.write_text('\n'.join(map(json.dumps, events)))
            replay_path = folder / 'final-sql-replay.json'
            launch = json.loads((folder / 'launch.json').read_text())
            manifest = json.loads((comparison / 'manifest.json').read_text())
            result = {'columns': ['n'], 'rows': [[7]], 'truncated': False}
            replay = {'sql': answer['sql'], 'result': result, 'database_sha256': manifest['inputs']['data'],
                      'executor_sha256': launch['server_sha256']}
            replay_path.write_text(json.dumps(replay))
            record_path = folder / 'record.json'; record = json.loads(record_path.read_text())
            record['answer'] = answer
            record['result_source'] = 'controller_replay'
            record['score'] = report.scorer.grade(record['gold_result'], result, 'SELECT 7', answer['sql'],
                                                  report.scorer.case_sig({'case_id': 'q', 'gold_sql': 'SELECT 7'}))
            record['evidence_hashes'] = {p.name: report.sha(p) for p in folder.iterdir() if p.is_file() and p.name != 'record.json'}
            record_path.write_text(json.dumps(record))
            self.assertTrue(report.collect(comparison)['complete'])
            html = report.render(comparison).read_text()
            self.assertIn('Result source: Controller replay', html)

            for field, bad_value in (('sql', 'SELECT 8'), ('result', {'columns': ['n'], 'rows': [[8]], 'truncated': False})):
                replay[field] = bad_value
                replay_path.write_text(json.dumps(replay))
                record = json.loads(record_path.read_text())
                record['evidence_hashes']['final-sql-replay.json'] = report.sha(replay_path)
                record_path.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError, 'Final SQL replay receipt'):
                    report.collect(comparison)
                replay[field] = answer['sql'] if field == 'sql' else result
                replay_path.write_text(json.dumps(replay))
                record['evidence_hashes']['final-sql-replay.json'] = report.sha(replay_path)
                record_path.write_text(json.dumps(record))


if __name__ == '__main__':
    unittest.main()
