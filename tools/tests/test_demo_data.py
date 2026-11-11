"""The bundled demos: each copies into a working folder, surveys and loads as its manifest says."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import test_local_project as fixtures
from project import Project

SKILL = fixtures.ROOT / 'plugins/atlanai-analytics/skills/analytics-demo'
spec = importlib.util.spec_from_file_location('demo_data', SKILL / 'scripts/demo_data.py')
demo_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_data)


class BundledDemos(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()

    def test_every_demo_has_facts_and_a_manifest(self):
        self.assertEqual(demo_data.demos(), ['box-office', 'yard'])
        for demo in demo_data.demos():
            facts = demo_data.manifest(demo)
            self.assertTrue((SKILL / 'assets' / demo / facts['bi']).is_file(), demo)
            self.assertTrue((SKILL / 'references' / (demo + '.md')).is_file(), demo)
            self.assertIn(f'DEMO copy {demo}', (SKILL / 'references' / (demo + '.md')).read_text())
            self.assertEqual(Path(facts['database']).stem, facts['schema'], demo)
            self.assertFalse((SKILL / 'assets' / demo / facts['database']).exists(), demo)

    def test_each_demo_surveys_and_loads_from_its_copy(self):
        for demo in demo_data.demos():
            with self.subTest(demo=demo):
                placed = demo_data.copy(demo, self.folder)
                self.assertEqual(placed['status'], 'copied')
                source = Path(placed['source_folder'])
                self.assertEqual(source, self.folder / 'atlan-analytics-demo' / demo)
                self.assertFalse((source / 'demo.json').exists())
                run_dir = self.folder / '.atlan-analytics' / demo
                run = Project(run_dir)
                sources = run.survey(str(source))['sources']
                dashboards = [s for s in sources if s['kind'] in ('powerbi', 'tableau')]
                self.assertEqual([s['path'] for s in dashboards], [placed['bi']])
                asked = demo_data.requests(demo, run_dir)
                self.assertEqual(asked['bi'], str(source / placed['bi']))
                prepare = json.loads(Path(asked['prepare']).read_text())
                result = run.prepare(prepare)
                self.assertEqual(len(result['tables']), asked['tables'])
                self.assertTrue(all(t['rows'] > 0 for t in result['tables']))
                self.assertEqual(asked['database'], result['database'])
                if dashboards[0]['kind'] == 'powerbi':
                    scope = run.scope(json.loads(Path(asked['scope']).read_text()))
                    self.assertEqual(len(scope['selection']['measure_refs']), 4)
                else:
                    self.assertIsNone(asked['scope'])

    def test_requests_need_a_survey_of_the_copy(self):
        with self.assertRaisesRegex(demo_data.DemoError, 'Survey the copied folder'):
            demo_data.requests('yard', self.folder / 'run')
        placed = demo_data.copy('box-office', self.folder)
        run_dir = self.folder / '.atlan-analytics' / 'box-office'
        Project(run_dir).survey(placed['source_folder'])
        with self.assertRaisesRegex(demo_data.DemoError, 'not the yard copy'):
            demo_data.requests('yard', run_dir)

    def test_a_second_copy_reuses_or_refreshes_without_touching_other_files(self):
        first = demo_data.copy('yard', self.folder)
        source = Path(first['source_folder'])
        database = source / first['database']
        database.write_bytes(b'prepared')
        notes = source / 'notes.txt'
        notes.write_text('mine')
        again = demo_data.copy('yard', self.folder)
        self.assertEqual((again['status'], again['database_exists']), ('current', True))
        (source / 'data/crew.csv').write_text('stale\n')
        refreshed = demo_data.copy('yard', self.folder)
        self.assertEqual((refreshed['status'], refreshed['database_exists']), ('refreshed', False))
        self.assertEqual((source / 'data/crew.csv').read_bytes(),
                         (SKILL / 'assets/yard/data/crew.csv').read_bytes())
        self.assertEqual(notes.read_text(), 'mine')

    def test_unknown_demo_and_missing_folder_are_refused(self):
        with self.assertRaisesRegex(demo_data.DemoError, 'box-office, yard'):
            demo_data.copy('../yard', self.folder)
        with self.assertRaisesRegex(demo_data.DemoError, 'not a folder'):
            demo_data.copy('yard', self.folder / 'missing')


if __name__ == '__main__':
    unittest.main()
