"""Past queries added after a dashboard build: the second run reuses the first run's answers."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics'
ENGINE = MODULE / 'engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    from metrics_agent.lib import carried
    from metrics_agent.stages import run


class Ctx:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class CarriedTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.file = self.dir / 'carried-engine.json'
        self.file.write_text(json.dumps({'statements': [{'vq_id': 'bi-1', 'verdict': 'none'},
                                                        {'vq_id': 'bi-gone', 'verdict': 'none'}]}))

    def test_only_statements_no_answer_covers_are_asked(self):
        objs = [{'vq_id': 'bi-1'}, {'vq_id': 'h-1'}]
        ctx = Ctx()
        rest = carried.apply(ctx, 'x1', 'statements', str(self.dir / 'carried-*.json'), {'bi-1', 'h-1'}, objs)
        self.assertEqual([o['vq_id'] for o in rest], ['h-1'])
        self.assertIn('1 statement(s) carried', ctx.said[0])

    def test_an_answer_for_a_statement_this_run_lacks_is_dropped(self):
        carried.apply(Ctx(), 'x1', 'statements', str(self.dir / 'carried-*.json'), {'bi-1'}, [{'vq_id': 'bi-1'}])
        kept = json.loads(self.file.read_text())['statements']
        self.assertEqual([r['vq_id'] for r in kept], ['bi-1'])

    def test_no_carried_files_change_nothing(self):
        objs = [{'vq_id': 'h-1'}]
        self.assertIs(carried.apply(Ctx(), 'x1', 'statements', None, {'h-1'}, objs), objs)


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class WaitTest(unittest.TestCase):
    def setUp(self):
        self.run = Path(tempfile.mkdtemp())
        (self.run / 'answers' / 'x1').mkdir(parents=True)
        (self.run / 'answers' / 'x1' / 'carried-engine.json').write_text('{"statements": []}')
        self.args = SimpleNamespace(answers_dir=None, run=str(self.run))
        self.step = run.Step('x1_ingest', 'x1_propose.py', [], gate='x1', ingest=True)

    def prompt(self, queries):
        (self.run / 'x1_prompt.json').write_text(json.dumps({'tasks': [{'domain': 'd', 'queries': queries}]}))

    def test_the_gate_waits_for_the_new_statements(self):
        self.prompt([{'vq_id': 'h-1'}])
        self.assertFalse(run._answers_present(self.step, self.args))
        (self.run / 'answers' / 'x1' / 'p1-01.json').write_text('{"statements": []}')
        self.assertTrue(run._answers_present(self.step, self.args))

    def test_a_prompt_with_nothing_new_does_not_wait(self):
        self.prompt([])
        self.assertTrue(run._answers_present(self.step, self.args))

    def test_the_two_per_statement_gates_are_told_where_carried_answers_are(self):
        answers = Path('/tmp/answers')
        emit = {g: run.gate_steps(g, answers)[0].args for g in ('m2', 'x1', 'x2')}
        self.assertIn('--carried', emit['m2'])
        self.assertIn('--carried', emit['x1'])
        self.assertNotIn('--carried', emit['x2'])


class CarryFromTest(unittest.TestCase):
    """`prepare --carry-from` copies the earlier run's answers; it needs no engine dependency."""

    def setUp(self):
        sys.path.insert(0, str(MODULE))
        import worker
        self.worker = worker
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.src, self.dst = self.root / 'engine', self.root / 'engine-history'
        for gate, name, doc in (('a3b', 'a3b.json', {'findings': []}), ('c3', 'c3.json', {'items': [{'id': 'bi-1'}]}),
                                ('m2', '01.json', {'queries': [{'vq_id': 'bi-1', 'question': 'q'}]}),
                                ('m2', '02.json', {'queries': [{'vq_id': 'bi-2', 'question': 'q'}]}),
                                ('x1', '01.json', {'statements': [{'vq_id': 'bi-1', 'verdict': 'none'}]}),
                                ('x2', '01.json', {'metrics': [{'name': 'M'}]})):
            path = self.src / 'answers' / gate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc))
        (self.src / 'module.json').write_text(json.dumps({'commit': 'abc'}))
        self.dst.mkdir()

    def test_answers_are_carried_and_the_merge_is_not(self):
        counts = self.worker.carry(self.src, self.dst, 'abc')
        self.assertEqual(counts, {'a3b': 1, 'c3': 1, 'm2': 2, 'x1': 1})
        m2 = json.loads((self.dst / 'answers' / 'm2' / 'carried-engine.json').read_text())
        self.assertEqual([r['vq_id'] for r in m2['queries']], ['bi-1', 'bi-2'])
        self.assertFalse((self.dst / 'answers' / 'x2').exists(), 'the merge runs again over every source')

    def test_a_run_on_another_runtime_is_refused(self):
        with self.assertRaises(ValueError):
            self.worker.carry(self.src, self.dst, 'other')


if __name__ == '__main__':
    unittest.main()
