"""Exporting a paired metrics arm into EVAL must preserve evidence and refuse collisions."""
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
exporter = module('comparison_export')


class ComparisonExportTest(unittest.TestCase):
    def setup_pair(self, root):
        files = {
            'cases': root / 'cases.jsonl', 'gold': root / 'gold.jsonl',
            'schema': root / 'schema.json', 'history': root / 'history.jsonl',
            'metrics': root / 'metrics.json', 'data': root / 'data.sqlite',
        }
        files['cases'].write_text(json.dumps({'case_id': 'q', 'question': 'How many jobs?'}) + '\n')
        files['gold'].write_text(json.dumps({'case_id': 'q', 'gold_sql': 'SELECT COUNT(*) FROM jobs'}) + '\n')
        files['schema'].write_text('{}')
        files['history'].write_text('{}\n')
        files['metrics'].write_text('[{"name":"job_count"}]')
        files['data'].write_bytes(b'same frozen sqlite bytes')
        comparison = Path(prepare.prepare(root=root / 'comparisons', **files, model='fixture-model',
                                          effort='unsupported', harness='codex-fixture')['directory'])
        evals = root / 'evals'
        (evals / 'eval/gold').mkdir(parents=True)
        (evals / 'eval/cases.jsonl').write_bytes(files['cases'].read_bytes())
        (evals / 'eval/answers.jsonl').write_bytes(files['gold'].read_bytes())
        result = {'columns': ['n'], 'rows': [[7]], 'truncated': False}
        (evals / 'eval/gold/q.json').write_text(json.dumps(result))
        (evals / 'hillclimb').mkdir()
        (evals / 'hillclimb/_state.json').write_text('{}')
        self.fake_pair(comparison, result)
        return comparison, evals

    def fake_pair(self, comparison, result):
        manifest = report.load(comparison / 'manifest.json')
        for arm in ('without_metrics', 'with_metrics'):
            folder = comparison / 'executions' / arm / report.case_key('q')
            folder.mkdir(parents=True)
            answer = {'answer': '7', 'sql': 'SELECT COUNT(*) FROM jobs', 'used_context_ids': []}
            tool_rows = [
                {'tool': 'read_context', 'arguments': {'kind': 'schema'}, 'result': {'text': '{}'}},
                {'tool': 'execute_sql', 'arguments': {'sql': answer['sql']}, 'result': result},
            ]
            events = [
                {'type': 'thread.started', 'thread_id': 'session-q'},
                {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': 't1', 'server': 'data',
                 'tool': 'read_context', 'arguments': {'kind': 'schema'}, 'result': {'text': '{}'}}},
                {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': 't2', 'server': 'data',
                 'tool': 'execute_sql', 'arguments': {'sql': answer['sql']}, 'result': result}},
                {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(answer)}},
                {'type': 'turn.completed'},
            ]
            native = [{'type': 'turn_context', 'payload': {'model': 'fixture-model', 'effort': 'unsupported'}}]
            for name, rows in (('events.jsonl', events), ('tools.jsonl', tool_rows),
                               ('native-transcript.jsonl', native)):
                (folder / name).write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            (folder / 'prompt.txt').write_text('How many jobs?')
            launch = {'host': 'codex', 'execution_schema': 'site', 'command': ['codex', '-m', 'fixture-model', '-c',
                      'model_reasoning_effort="unsupported"'], 'server_sha256': 'server-hash'}
            (folder / 'launch.json').write_text(json.dumps(launch))
            (folder / 'gold-execution.json').write_text(json.dumps({
                'sql': 'SELECT COUNT(*) FROM jobs', 'result': result,
                'database_sha256': manifest['inputs']['data'], 'trusted_sha256': manifest['inputs']['gold'],
                'executor_sha256': 'server-hash'}))
            grade = report.scorer.grade(result, result, 'SELECT COUNT(*) FROM jobs', answer['sql'],
                                        report.scorer.case_sig({'case_id': 'q', 'gold_sql': 'SELECT COUNT(*) FROM jobs'}))
            record = {'case_id': 'q', 'arm': arm, 'status': 'scored', 'model': 'fixture-model',
                      'effort': 'unsupported', 'isolation': 'capability_allowlist_and_trace_audit',
                      'session_id': 'session-q', 'answer': answer, 'agent_result': result,
                      'gold_result': result, 'score': grade, 'seconds': 1.25, 'result_source': 'agent_tool',
                      'evidence_hashes': {p.name: report.sha(p) for p in folder.iterdir()}}
            (folder / 'record.json').write_text(json.dumps(record))

    def test_export_imports_exact_metrics_run_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison, evals = self.setup_pair(root)
            first = exporter.export(comparison, evals, 'baseline')
            self.assertFalse(first['exists'])
            rows = report.scorer.read_jsonl(evals / 'hillclimb/baseline/results.jsonl')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['prompt_id'], 'q')
            self.assertEqual(rows[0]['sql'], 'SELECT COUNT(*) FROM jobs')
            self.assertEqual(rows[0]['grade']['pass'], 1)
            self.assertEqual(rows[0]['isolation'], 'verified')
            target = evals / 'hillclimb/baseline'
            self.assertTrue((target / 'comparison-evidence/with_metrics' / report.case_key('q') / 'events.jsonl').is_file())
            sidecar = json.loads((target / 'comparison-export.json').read_text())
            self.assertEqual(sidecar['arm'], 'with_metrics')
            self.assertEqual(sidecar['without_metrics_source'], str(comparison.resolve() / 'executions/without_metrics'))
            self.assertTrue(exporter.export(comparison, evals, 'baseline')['exists'])

    def test_refuses_question_or_gold_digest_mismatch_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison, evals = self.setup_pair(root)
            (evals / 'eval/answers.jsonl').write_text(json.dumps({'case_id': 'q', 'gold_sql': 'SELECT 8'}) + '\n')
            with self.assertRaisesRegex(ValueError, 'gold digest'):
                exporter.export(comparison, evals, 'baseline')
            self.assertFalse((evals / 'hillclimb/baseline').exists())

    def test_refuses_nonempty_variant_without_matching_export_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison, evals = self.setup_pair(root)
            target = evals / 'hillclimb/baseline'
            target.mkdir()
            (target / 'results.jsonl').write_text('{}\n')
            with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
                exporter.export(comparison, evals, 'baseline')

    def test_incomplete_pair_is_not_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison, evals = self.setup_pair(root)
            folder = comparison / 'executions/with_metrics' / report.case_key('q')
            (folder / 'record.json').unlink()
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                exporter.export(comparison, evals, 'baseline')
            self.assertFalse((evals / 'hillclimb/baseline').exists())


if __name__ == '__main__':
    unittest.main()
