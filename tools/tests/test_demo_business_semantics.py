"""Rebuild both bundled demo databases and execute their controller-only gold fixtures."""
import importlib.util
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import test_local_project as fixtures
from context_state import resolve_run
from project import Project

ROOT = fixtures.ROOT
SKILL = ROOT / 'plugins/atlanai-analytics/skills/analytics-demo'
FIXTURE = ROOT / 'evals/atlan-analytics/fixtures/demo-business-semantics'
PROJECT_SCRIPT = (SKILL / 'scripts/../../analytics-build/scripts/project.py').resolve()
EXPECTED = {
    'yard': FIXTURE / 'fixit.expected.json',
    'box-office': FIXTURE / 'ticketfuddle.expected.json',
}
COUNTERFACTUALS = FIXTURE / 'injection-counterfactuals.json'
SETS = {
    'yard': FIXTURE / 'fixit.jsonl',
    'box-office': FIXTURE / 'ticketfuddle.jsonl',
}

spec = importlib.util.spec_from_file_location('business_semantics_demo_data', SKILL / 'scripts/demo_data.py')
demo_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_data)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def close_value(actual, expected):
    if isinstance(actual, (int, float)) and not isinstance(actual, bool) \
            and isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(close_value(a, e) for a, e in zip(actual, expected))
    return actual == expected


def rows_close(actual, expected):
    return close_value(actual, expected)


class DemoBusinessSemanticsFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()

    def build_demo(self, demo):
        """Use the shipped demo loader and normal PROJECT survey/prepare/bind sequence."""
        opened = demo_data.open_demo(demo, self.workspace)
        run_dir = resolve_run(self.workspace)
        project = Project(run_dir)
        survey = project.survey(opened['working_folder'])
        self.assertEqual(survey['status'], 'ok')
        requests = demo_data.requests(demo, run_dir)
        self.assertIsNotNone(requests['prepare'])
        prepared = project.prepare(json.loads(Path(requests['prepare']).read_text()))
        self.assertEqual(prepared['status'], 'ok')
        project.bind(requests['database'], schema=requests['schema'])
        return run_dir

    def execute(self, run_dir, case_id, sql):
        request = run_dir / f'fixture-{case_id}.json'
        request.write_text(json.dumps({'sql': sql, 'limit': 1000}))
        proc = subprocess.run([sys.executable, str(PROJECT_SCRIPT), '--run-dir', str(run_dir),
                               'execute', str(request)], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        result = json.loads(proc.stdout)
        self.assertEqual(result.get('status'), 'ok', result)
        columns = [column.get('name') if isinstance(column, dict) else column for column in result['columns']]
        return {'columns': columns, 'rows': result['rows']}

    def test_all_twenty_gold_queries_match_rebuilt_demo_data(self):
        self.assertEqual((len(read_jsonl(SETS['yard'])), len(read_jsonl(SETS['box-office']))), (10, 10))
        for demo in ('yard', 'box-office'):
            with self.subTest(demo=demo):
                run_dir = self.build_demo(demo)
                cases = read_jsonl(SETS[demo])
                expected = json.loads(EXPECTED[demo].read_text())
                self.assertEqual(set(expected), {row['id'] for row in cases})
                for case in cases:
                    with self.subTest(case=case['id']):
                        self.assertIn('source_paths', case, 'fixture must name the shipped source files behind its rationale')
                        self.assertTrue(case['source_paths'])
                        for relative in case['source_paths']:
                            rel = Path(relative)
                            self.assertFalse(rel.is_absolute())
                            self.assertNotIn('..', rel.parts)
                            self.assertTrue((ROOT / rel).is_file(), relative)
                        actual = self.execute(run_dir, case['id'], case['sql'])
                        self.assertEqual(actual, expected[case['id']])
                for mutant in (x for x in json.loads(COUNTERFACTUALS.read_text()) if x['demo'] == demo):
                    with self.subTest(counterfactual=mutant['case_id'], demo=demo):
                        self.assertIn(mutant['case_id'], {row['id'] for row in cases})
                        self.assertTrue(rows_close(mutant['gold_rows'], expected[mutant['case_id']]['rows']))
                        actual = self.execute(run_dir, 'mutant-' + mutant['case_id'], mutant['sql'])
                        self.assertTrue(rows_close(actual['rows'], mutant['rows']),
                                        f"observed {actual['rows']}, recorded {mutant['rows']}")
                        self.assertFalse(rows_close(actual['rows'], expected[mutant['case_id']]['rows']),
                                         'the planned defect must change its trusted answer')

    def test_date_windows_are_absolute_and_nonmoving(self):
        moving_sql = re.compile(r"\b(?:current_date|current_timestamp|getdate|now)\s*\(?", re.I)
        moving_question = re.compile(r"\b(?:today|yesterday|current|recent(?:ly)?|last\s+(?:\d+\s+)?(?:days?|weeks?|months?|quarters?|years?)|past\s+(?:\d+\s+)?(?:days?|weeks?|months?|quarters?|years?)|month to date|year to date)\b", re.I)
        fixed_year = re.compile(r"(?<!\d)2026(?:-\d{2}(?:-\d{2})?)?(?!\d)")
        for demo, path in SETS.items():
            for case in read_jsonl(path):
                with self.subTest(demo=demo, case=case['id']):
                    self.assertFalse(moving_sql.search(case['sql']), case['sql'])
                    self.assertFalse(moving_question.search(case['question']), case['question'])
                    if demo == 'box-office' and '2026' in case['question']:
                        self.assertRegex(case['sql'], fixed_year, 'date-scoped question needs an absolute 2026 SQL boundary')


if __name__ == '__main__':
    unittest.main()
