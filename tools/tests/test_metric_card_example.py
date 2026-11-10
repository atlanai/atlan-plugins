"""The author-metric-card skill's worked example must stay a card the X3 gate accepts.

An author copies its shape, so a field the schema does not list or a grain written as a
sentence in the example is a refusal (and a wasted gate round) on every run.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ENGINE = (Path(__file__).resolve().parents[2]
          / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine')
SKILL = ENGINE / 'skills/author-metric-card/SKILL.md'
SCHEMA = ENGINE / 'src/metrics_agent/data/schemas/authored_metric_cards.schema.json'
RUNTIME = os.environ.get('ANALYTICS_METRICS_RUNTIME')
COLUMN = re.compile(r'^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)+$')


def example() -> dict:
    text = SKILL.read_text()
    found = re.search(r'## A complete card that passes the gate.*?```json\n(.*?)```', text, re.S)
    if not found:
        raise AssertionError('the skill has no complete example card')
    return json.loads(found.group(1))


class ExampleCard(unittest.TestCase):
    def test_example_uses_only_schema_fields(self):
        item = json.loads(SCHEMA.read_text())['properties']['metrics']['items']
        for card in example()['metrics']:
            self.assertEqual(set(card) - set(item['properties']), set())
            self.assertEqual(set(item['required']) - set(card), set())
            self.assertIn(card['aggregation']['type'],
                          item['properties']['aggregation']['properties']['type']['enum'])

    def test_example_grain_is_a_key(self):
        for card in example()['metrics']:
            for part in card['grain'].split(','):
                self.assertRegex(part.strip(), COLUMN)

    def test_grain_is_never_described_as_a_sentence(self):
        self.assertNotIn('"one value for the whole population", not', SKILL.read_text())

    @unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME to run the X3 field checks')
    def test_example_passes_the_x3_checks(self):
        python = Path(RUNTIME) / 'venv/bin/python'
        code = ('import json,sys,jsonschema\n'
                'from metrics_agent.stages import x3_emit as X\n'
                'd=json.loads(sys.argv[1]); jsonschema.validate(d, json.load(open(sys.argv[2])))\n'
                'c=d["metrics"][0]; p=X._compiler_field_problems(c, c["aggregation"]["type"], "sqlite")\n'
                'sys.exit("; ".join(p) if p else 0)\n')
        done = subprocess.run([str(python), '-c', code, json.dumps(example()), str(SCHEMA)],
                              env={**os.environ, 'PYTHONPATH': str(ENGINE / 'src')},
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == '__main__':
    unittest.main()
