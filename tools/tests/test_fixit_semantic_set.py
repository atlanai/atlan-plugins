"""The fixed source-led questions must continue to distinguish their intended business rules."""
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'evals/atlan-analytics/fixtures'
DATA = ROOT / 'plugins/atlanai-analytics/skills/analytics-demo/assets/yard/data'
SCRIPT = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts/analytics_eval.py'
spec = importlib.util.spec_from_file_location('fixit_scorer', SCRIPT)
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


class FixitSemanticSetTest(unittest.TestCase):
    def test_all_ten_references_and_counterfactual_rules(self):
        cases = [json.loads(line) for line in (FIXTURES / 'fixit-semantic-v3.jsonl').read_text().splitlines()]
        expected = json.loads((FIXTURES / 'fixit-semantic-v3.expected.json').read_text())['cases']
        self.assertEqual(len(cases), 10)
        self.assertEqual({c['id'] for c in cases}, set(expected))
        db = sqlite3.connect(':memory:')
        try:
            for file in DATA.glob('*.csv'):
                with file.open(newline='') as stream:
                    reader = csv.reader(stream)
                    names = next(reader)
                    quoted = ','.join('"' + name.replace('"', '""') + '"' for name in names)
                    db.execute(f'CREATE TABLE "{file.stem}" ({quoted})')
                    db.executemany(f'INSERT INTO "{file.stem}" VALUES ({",".join("?" for _ in names)})', reader)
            for case in cases:
                with self.subTest(case=case['id']):
                    cursor = db.execute(case['sql'])
                    columns = [c[0] for c in cursor.description]
                    actual = {'columns': columns, 'rows': cursor.fetchall()}
                    reference = expected[case['id']]
                    self.assertEqual(case['sql'], reference['gold_sql'])
                    self.assertTrue(actual['rows'])
                    result = scorer.grade({'columns': columns, 'rows': reference['gold_rows']}, actual,
                                          case['sql'], case['sql'])
                    self.assertEqual(result['class'], 'pass')
                    wrong = db.execute(reference['plausible_wrong_rule_sql']).fetchall()
                    self.assertNotEqual(actual['rows'], wrong)
                    contract = case.get('representation_equivalence', {}).get('percent_fraction', {})
                    self.assertLessEqual(set(contract), set(columns))
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
