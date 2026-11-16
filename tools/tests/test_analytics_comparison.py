"""Comparison identities must not reuse results across changed experimental inputs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics/skills/accuracy-check/scripts/comparison.py'
spec = importlib.util.spec_from_file_location('comparison', SCRIPT)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class ComparisonTest(unittest.TestCase):
    def test_identity_and_packet_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = {'root': root / 'out', 'model': 'model-1', 'effort': 'low', 'harness': 'host-1'}
            for name in ('cases', 'gold', 'schema', 'history', 'metrics', 'data'):
                args[name] = root / name
                args[name].write_text(json.dumps({'id': 'q1', 'question': 'How many?', 'sql': 'SECRET'})
                                      if name == 'cases' else '{}')
            first = comparison.prepare(**args)
            self.assertTrue(comparison.prepare(**args)['reused_inputs'])
            packet = Path(first['directory']) / 'without_metrics'
            self.assertEqual({p.name for p in packet.iterdir()}, {'schema.json', 'history.jsonl'})
            self.assertNotIn('SECRET', ''.join(p.read_text() for p in packet.iterdir()))
            args['metrics'].write_text('{"version":2}')
            second = comparison.prepare(**args)
            self.assertEqual(first['baseline_key'], second['baseline_key'])
            self.assertNotEqual(first['comparison_key'], second['comparison_key'])
            for field in ('model', 'effort', 'harness'):
                changed = comparison.prepare(**{**args, field: 'different'})
                self.assertNotEqual(first['baseline_key'], changed['baseline_key'])
            for field, value in (('host', 'claude'), ('execution_schema', 'other')):
                changed = comparison.prepare(**{**args, field: value})
                self.assertNotEqual(second['baseline_key'], changed['baseline_key'])
            args['history'].write_text('{"sql":"select 2"}')
            self.assertNotEqual(first['baseline_key'], comparison.prepare(**args)['baseline_key'])
            (packet / 'schema.json').write_text('changed')
            args['history'].write_text('{}')
            args['metrics'].write_text('{}')
            with self.assertRaisesRegex(ValueError, 'input changed'):
                comparison.prepare(**args)


if __name__ == '__main__':
    unittest.main()
