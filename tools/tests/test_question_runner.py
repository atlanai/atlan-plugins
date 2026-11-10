"""Explicit schema-only evaluation retains isolation and cannot silently drop context."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import analytics_case

SCHEMA = [{'name': 'site.work', 'columns': [{'name': 'amount', 'type': 'DECIMAL(12,2)'}]}]
CONTEXT = [{'id': 'metric_example', 'release_id': 'release_example', 'name': 'work-amount',
            'definition': {'formula': 'SUM(work.amount)'}}]


class QuestionRunner(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.answer = {'answer': 'Proposed total', 'sql': 'SELECT SUM(amount) FROM site.work',
                       'used_context_ids': [], 'parameters': []}
        self.inputs = []

    def host(self, args, **kwargs):
        self.inputs.append((args, kwargs['input']))
        event = {'type': 'result', 'is_error': False, 'structured_output': self.answer}
        kwargs['stdout'].write(json.dumps(event) + '\n')
        return SimpleNamespace(returncode=0)

    def run_case(self, context, target='case', **kwargs):
        with patch('analytics_case.shutil.which', return_value='/host/claude'), \
                patch('analytics_case.subprocess.run', side_effect=self.host):
            return analytics_case.run_case('claude', context, 'What is the total amount?', self.root / target, **kwargs)

    def test_schema_only_records_distinct_runner_and_keeps_all_no_tool_controls(self):
        result = self.run_case([], schema_only=True, schema=SCHEMA)
        receipt = json.loads((self.root / 'case/run.json').read_text())
        answer = json.loads((self.root / 'case/answer.json').read_text())
        self.assertEqual(receipt['runner'], 'question-only-schema-only')
        self.assertIn('schema_sha256', receipt)
        self.assertEqual(receipt['tools_used'], 0)
        self.assertEqual(answer['used_context_refs'], [])
        args, prompt = self.inputs[0]
        self.assertIn('--restricted', args)
        self.assertIn('--strict-mcp-config', args)
        self.assertIn('--no-session-persistence', args)
        self.assertIn('No metric definitions are supplied', args[args.index('--system-prompt') + 1])
        self.assertIn('Untrusted input data:', prompt)
        self.assertIn('"released_context": []', prompt)
        self.assertIn('"schema":', prompt)
        self.assertEqual(result['status'], 'ok')

    def test_same_schema_is_available_to_released_context_arm_without_changing_default_guard(self):
        self.answer['used_context_ids'] = ['metric_example']
        self.run_case(CONTEXT, schema=SCHEMA)
        receipt = json.loads((self.root / 'case/run.json').read_text())
        self.assertEqual(receipt['runner'], 'question-only-released-context')
        self.assertIn('schema_sha256', receipt)
        answer = json.loads((self.root / 'case/answer.json').read_text())
        self.assertEqual(answer['used_context_refs'], [{'artifact_id': 'metric_example', 'release_id': 'release_example'}])
        with self.assertRaisesRegex(ValueError, 'nonempty released'):
            self.run_case([], target='empty')
        self.assertFalse((self.root / 'empty').exists())

    def test_ambiguous_or_leaking_inputs_are_refused_before_any_host_run(self):
        cases = [({}, {'schema_only': True, 'schema': SCHEMA}),
                 (CONTEXT, {'schema_only': True, 'schema': SCHEMA}),
                 ([], {'schema_only': True}),
                 ([], {'schema_only': True, 'schema': []})]
        for extra in ({'gold_sql': 'SELECT 999'}, {'examples': [999]}, {'question': 'Hidden evaluation question'}):
            schema = copy.deepcopy(SCHEMA);schema[0].update(extra)
            cases.append(([], {'schema_only': True, 'schema': schema}))
        bad_column = copy.deepcopy(SCHEMA);bad_column[0]['columns'][0]['values'] = [999]
        cases.append(([], {'schema_only': True, 'schema': bad_column}))
        for i, (context, options) in enumerate(cases):
            with self.subTest(i=i), self.assertRaises(ValueError):
                self.run_case(context, target=str(i), **options)
            self.assertFalse((self.root / str(i)).exists())
        self.assertEqual(self.inputs, [])

    def test_schema_only_cannot_claim_metric_context_and_retains_failed_evidence(self):
        self.answer['used_context_ids'] = ['metric_example']
        with self.assertRaisesRegex(ValueError, 'outside the supplied'):
            self.run_case([], schema_only=True, schema=SCHEMA)
        self.assertTrue((self.root / 'case/transcript.jsonl').exists())
        self.assertFalse((self.root / 'case/run.json').exists())

    def test_completed_case_is_immutable(self):
        self.run_case([], schema_only=True, schema=SCHEMA)
        with self.assertRaisesRegex(ValueError, 'new run directory'):
            self.run_case([], schema_only=True, schema=SCHEMA)
        self.assertEqual(len(self.inputs), 1)


if __name__ == '__main__':
    unittest.main()
