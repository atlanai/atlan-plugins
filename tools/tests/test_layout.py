"""One folder in a person's project: .atlan-analytics/, and the move of what earlier versions wrote."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'))
import layout
from context_state import resolve_run
import eval_runs


class OneFolder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name).resolve()

    def earlier(self):
        """A project as an earlier version left it: a built demo run, its runtime, a set, the demo."""
        p = self.project
        run = p / '.atlan-metrics/run_1'
        (run / 'context').mkdir(parents=True)
        (run / 'engine').mkdir()
        (run / 'context/metrics.json').write_text('[]')
        (run / 'run.json').write_text(json.dumps({'source_root': str(p / 'atlan-analytics-demo/yard'),
                                                  'database': str(p / 'atlan-analytics-demo/yard/data/site.sqlite')}))
        (run / 'engine/run.yml').write_text(f'history: {run}/context/querylog.clean.jsonl\n')
        (p / '.atlan-metrics/runtime/venv/bin').mkdir(parents=True)
        (p / '.atlan-metrics/runtime/runtime.json').write_text('{}')
        (p / '.atlan-evals/sets').mkdir(parents=True)
        (p / '.atlan-evals/sets/mine.jsonl').write_text('{"id": "q1"}\n')
        (p / 'atlan-analytics-demo/yard/data').mkdir(parents=True)
        (p / 'atlan-analytics-demo/yard/data/crew.csv').write_text('a\n1\n')
        return run

    def test_a_new_run_starts_in_the_one_folder(self):
        self.assertEqual(resolve_run(self.project).parent, self.project / '.atlan-analytics')

    def test_earlier_folders_move_in_once_and_their_paths_follow(self):
        self.earlier()
        result = layout.adopt(self.project)
        new = self.project / '.atlan-analytics'
        self.assertEqual(sorted(result['moved']), ['.atlan-evals/sets', '.atlan-metrics/run_1',
                                                   '.atlan-metrics/runtime', 'atlan-analytics-demo/yard'])
        self.assertIn('.gitignore', result['message'])
        for old in ('.atlan-metrics', '.atlan-evals', 'atlan-analytics-demo'):
            self.assertFalse((self.project / old).exists(), old)
        self.assertTrue((new / 'runtime/runtime.json').is_file())
        self.assertTrue((new / 'evals/sets/mine.jsonl').is_file())
        self.assertTrue((new / 'demo/yard/data/crew.csv').is_file())
        recorded = json.loads((new / 'run_1/run.json').read_text())
        self.assertEqual(recorded['source_root'], str(new / 'demo/yard'))
        self.assertEqual(recorded['database'], str(new / 'demo/yard/data/site.sqlite'))
        self.assertEqual((new / 'run_1/engine/run.yml').read_text(),
                         f'history: {new}/run_1/context/querylog.clean.jsonl\n')
        self.assertEqual(layout.adopt(self.project), {'moved': [], 'kept': []})

    def test_a_taken_name_is_never_overwritten(self):
        self.earlier()
        (self.project / '.atlan-analytics/run_1').mkdir(parents=True)
        (self.project / '.atlan-analytics/run_1/mine.txt').write_text('newer')
        (self.project / '.atlan-metrics/evals').mkdir()  # a run an earlier version named like a reserved folder
        result = layout.adopt(self.project)
        self.assertEqual(sorted(Path(k).name for k in result['kept']), ['evals', 'run_1'])
        self.assertEqual((self.project / '.atlan-analytics/run_1/mine.txt').read_text(), 'newer')
        self.assertTrue((self.project / '.atlan-metrics/run_1/run.json').is_file())
        self.assertTrue((self.project / '.atlan-metrics/evals').is_dir())
        self.assertEqual([p.name for p in (self.project / '.atlan-analytics/evals').iterdir()], ['sets'])

    def test_an_earlier_built_run_is_found_after_the_move(self):
        self.earlier()
        self.assertEqual(eval_runs.built_runs(self.project), [self.project / '.atlan-analytics/run_1'])
        self.assertTrue(layout.evals(self.project).joinpath('sets/mine.jsonl').is_file())

    def test_a_run_named_by_its_earlier_path_is_followed(self):
        self.earlier()
        named = self.project / '.atlan-metrics/run_1'
        self.assertEqual(eval_runs.find_run(self.project, str(named)), self.project / '.atlan-analytics/run_1')


if __name__ == '__main__':
    unittest.main()
