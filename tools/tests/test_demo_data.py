"""The bundled demos: each opens in a working folder, surveys and loads as its manifest says, and
the engine configuration a local setup run writes for itself."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import test_local_project as fixtures
from project import Project, ProjectError
from context_state import resolve_run

SKILL = fixtures.ROOT / 'plugins/atlanai-analytics/skills/analytics-demo'
spec = importlib.util.spec_from_file_location('demo_data', SKILL / 'scripts/demo_data.py')
demo_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_data)


class BundledDemos(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name).resolve()

    def surveyed(self, demo):
        """Open the demo and survey it into a run placed as the skill places it."""
        placed = demo_data.open_demo(demo, self.folder)
        run_dir = resolve_run(self.folder)
        run = Project(run_dir)
        run.survey(placed['working_folder'])
        return placed, run_dir, run

    def loaded(self, demo):
        placed, run_dir, run = self.surveyed(demo)
        asked = demo_data.requests(demo, run_dir)
        run.prepare(json.loads(Path(asked['prepare']).read_text()))
        run.bind(asked['database'], schema=asked['schema'])
        return placed, run_dir, run, asked

    def test_every_demo_has_facts_and_a_manifest(self):
        self.assertEqual(demo_data.demos(), ['box-office', 'yard'])
        for demo in demo_data.demos():
            facts = demo_data.manifest(demo)
            self.assertTrue((SKILL / 'assets' / demo / facts['bi']).is_file(), demo)
            self.assertIn(f'DEMO open {demo}', (SKILL / 'references' / (demo + '.md')).read_text())
            self.assertEqual(Path(facts['database']).stem, facts['schema'], demo)
            self.assertFalse((SKILL / 'assets' / demo / facts['database']).exists(), demo)

    def test_each_demo_loads_and_configures_its_engine(self):
        for demo in demo_data.demos():
            with self.subTest(demo=demo):
                placed, run_dir, run = self.surveyed(demo)
                source = Path(placed['working_folder'])
                self.assertEqual(source, self.folder / 'atlan-analytics-demo' / demo)
                self.assertFalse(run_dir.is_relative_to(source))
                sources = run.current()['sources']
                dashboards = [s for s in sources if s['kind'] in ('powerbi', 'tableau')]
                self.assertEqual([s['path'] for s in dashboards], [demo_data.manifest(demo)['bi']])
                asked = demo_data.requests(demo, run_dir)
                self.assertEqual((run_dir / 'demo-prepare.json').stat().st_mode & 0o777, 0o600)
                result = run.prepare(json.loads(Path(asked['prepare']).read_text()))
                self.assertEqual(len(result['tables']), asked['tables'])
                self.assertTrue(all(t['rows'] > 0 for t in result['tables']))
                run.bind(asked['database'], schema=asked['schema'])
                if dashboards[0]['kind'] == 'powerbi':
                    scope = run.scope(json.loads(Path(asked['scope']).read_text()))
                    self.assertEqual(len(scope['selection']['measure_refs']), 4)
                else:
                    self.assertIsNone(asked['scope'])
                with self.assertRaisesRegex(ProjectError, 'PROJECT history'):
                    run.engine_config({'sources': 'all', 'bi': asked['bi']})
                run.history()
                configured = run.engine_config({'sources': 'all', 'bi': asked['bi']})
                self.assertEqual(run.current()['engine_run'], str(run_dir / 'engine'))
                config = json.loads(Path(configured['config']).read_text())
                files = config['settings']['local-files']
                self.assertEqual(files['history'], str(run_dir / 'context/querylog.clean.jsonl'))
                self.assertEqual((files['bi'], files['database']), (str(source / asked['bi']), asked['database']))
                self.assertEqual(files['bi_vendor'], dashboards[0]['kind'])
                self.assertEqual((config['dialect'], config['inputs']['schema']), ('sqlite', []))
                self.assertNotIn('tables', config)
                only = json.loads(Path(run.engine_config({'sources': 'dashboard'})['config']).read_text())
                self.assertEqual(only['inputs']['history'], [])
                self.assertNotIn('history', only['settings']['local-files'])
                demo_data.open_demo(demo, self.folder)

    def test_a_loaded_database_is_bound_not_prepared_again(self):
        placed, run_dir, run, asked = self.loaded('yard')
        again = demo_data.requests('yard', run_dir)
        self.assertIsNone(again['prepare'])
        self.assertEqual(again['database'], asked['database'])

    def test_only_the_demos_own_csvs_become_tables(self):
        placed, run_dir, run = self.surveyed('yard')
        (Path(placed['working_folder']) / 'data/mine.csv').write_text('a,b\n1,2\n')
        run.survey(placed['working_folder'])
        asked = demo_data.requests('yard', run_dir)
        tables = [t['table'] for t in json.loads(Path(asked['prepare']).read_text())['tables']]
        self.assertEqual(len(tables), 5)
        self.assertNotIn('site.mine', tables)

    def test_reopening_removes_what_the_skill_dropped_and_keeps_the_persons_files(self):
        first = demo_data.open_demo('yard', self.folder)
        source = Path(first['working_folder'])
        ledger = source.parent / '.yard-files.json'
        (source / 'powerbi/model.bim').write_text('{}')
        ledger.write_text(json.dumps(json.loads(ledger.read_text()) + ['powerbi/model.bim', '../../escape.txt']))
        (self.folder / 'escape.txt').write_text('outside')
        notes = source / 'notes.txt'
        notes.write_text('mine')
        database = source / demo_data.manifest('yard')['database']
        database.write_bytes(b'prepared')
        again = demo_data.open_demo('yard', self.folder)
        self.assertEqual((again['status'], again['database_exists']), ('refreshed', True))
        self.assertFalse((source / 'powerbi/model.bim').exists())
        self.assertTrue((self.folder / 'escape.txt').exists())
        self.assertEqual(notes.read_text(), 'mine')
        (source / 'history/querylog.jsonl').write_text('{}\n')
        self.assertTrue(demo_data.open_demo('yard', self.folder)['database_exists'])
        (source / 'data/crew.csv').write_text('stale\n')
        refreshed = demo_data.open_demo('yard', self.folder)
        self.assertEqual((refreshed['status'], refreshed['database_exists']), ('refreshed', False))
        self.assertEqual((source / 'data/crew.csv').read_bytes(), (SKILL / 'assets/yard/data/crew.csv').read_bytes())
        self.assertEqual(demo_data.open_demo('yard', self.folder)['status'], 'current')

    def test_engine_config_refuses_what_it_cannot_configure(self):
        placed, run_dir, run = self.surveyed('yard')
        with self.assertRaisesRegex(ProjectError, 'Bind the local database'):
            run.engine_config({'sources': 'dashboard'})
        placed, run_dir, run, asked = self.loaded('yard')
        with self.assertRaisesRegex(ProjectError, 'Choose sources'):
            run.engine_config({'sources': 'everything'})
        shutil.copy(Path(placed['working_folder']) / asked['bi'], Path(placed['working_folder']) / 'powerbi/copy.pbit')
        run.survey(placed['working_folder'])
        with self.assertRaisesRegex(ProjectError, 'Name one surveyed dashboard'):
            run.engine_config({'sources': 'dashboard'})
        run.write_text(run.context / 'history-tables.txt', 'site.projects\nsite.crew')
        run.history()
        config = json.loads(Path(run.engine_config({'sources': 'all', 'bi': asked['bi']})['config']).read_text())
        self.assertEqual(config['tables'], ['site.projects', 'site.crew'])

    def test_requests_need_a_survey_of_the_working_folder(self):
        with self.assertRaisesRegex(demo_data.DemoError, 'Survey the demo'):
            demo_data.requests('yard', self.folder / 'run')
        placed, run_dir, run = self.surveyed('box-office')
        with self.assertRaisesRegex(demo_data.DemoError, 'not the yard working folder'):
            demo_data.requests('yard', run_dir)

    def test_unknown_demo_and_missing_folder_are_refused(self):
        with self.assertRaisesRegex(demo_data.DemoError, 'box-office, yard'):
            demo_data.open_demo('../yard', self.folder)
        with self.assertRaisesRegex(demo_data.DemoError, 'not a folder'):
            demo_data.open_demo('yard', self.folder / 'missing')


if __name__ == '__main__':
    unittest.main()
