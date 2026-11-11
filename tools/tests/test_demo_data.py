"""The bundled demos: each opens in a working folder, surveys and loads as its manifest says, and
the engine configuration a local setup run writes for itself."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

import test_local_project as fixtures
from project import Project, ProjectError, engine_table
from context_state import resolve_run
import local_data

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
                self.assertEqual(configured['engine_run'], str(run_dir / 'engine'))
                self.assertNotIn('engine_run', run.current())
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
        with self.assertRaisesRegex(ProjectError, 'Name one surveyed dashboard.*hard-hat'):
            run.engine_config({'sources': 'dashboard', 'bi': '/elsewhere/report.pbit'})
        folder = Path(placed['working_folder'])
        shutil.copy(folder / asked['bi'], folder / 'powerbi/copy.pbit')
        run.survey(placed['working_folder'])
        with self.assertRaisesRegex(ProjectError, 'Name one surveyed dashboard'):
            run.engine_config({'sources': 'dashboard'})

    def test_a_model_sidecar_is_not_a_second_dashboard(self):
        placed, run_dir, run, asked = self.loaded('yard')
        folder = Path(placed['working_folder'])
        with zipfile.ZipFile(folder / asked['bi']) as template:
            model = template.read('DataModelSchema').decode('utf-16-le').lstrip('\ufeff')
        (folder / 'powerbi/model.bim').write_text(model)
        statuses = {s['path']: s['status'] for s in run.survey(placed['working_folder'])['sources']}
        self.assertEqual(statuses['powerbi/model.bim'], 'model_sidecar')
        config = json.loads(Path(run.engine_config({'sources': 'dashboard'})['config']).read_text())
        self.assertEqual(config['settings']['local-files']['bi'], str(folder / asked['bi']))

    def test_history_tables_are_named_as_the_engine_reads_them(self):
        placed, run_dir, run, asked = self.loaded('yard')
        source_id = json.loads(Path(asked['scope']).read_text())['source_id']
        run.scope({'source_id': source_id, 'pages': ['page1'], 'history_tables': ['site.projects', 'site.crew']})
        run.history()
        config = json.loads(Path(run.engine_config({'sources': 'all'})['config']).read_text())
        self.assertEqual(config['tables'], ['site.site.crew', 'site.site.projects'])
        run.scope({'source_id': source_id, 'pages': ['all']})
        run.history()
        self.assertFalse((run.context / 'history-tables.txt').exists())
        self.assertNotIn('tables', json.loads(Path(run.engine_config({'sources': 'all'})['config']).read_text()))

    def test_history_without_a_dashboard_is_bounded_by_the_bound_tables(self):
        estate = self.folder / 'estate'
        shutil.copytree(SKILL / 'assets/yard/data', estate / 'data')
        shutil.copytree(SKILL / 'assets/yard/history', estate / 'history')
        run = Project(resolve_run(self.folder))
        sources = run.survey(str(estate))['sources']
        result = run.prepare({'database': 'data/site.sqlite', 'tables': [
            {'source_id': s['source_id'], 'table': 'site.' + Path(s['path']).stem}
            for s in sources if s['path'].endswith('.csv')]})
        run.bind(result['database'], schema='site')
        run.history()
        config = json.loads(Path(run.engine_config({'sources': 'all'})['config']).read_text())
        self.assertEqual(config['inputs']['bi'], [])
        self.assertIn('site.site.projects', config['tables'])
        self.assertTrue(all(t.count('.') == 2 for t in config['tables']))
        with self.assertRaisesRegex(ProjectError, 'No surveyed dashboard'):
            run.engine_config({'sources': 'dashboard'})

    def test_the_engines_sqlite_names_run_on_the_bound_database(self):
        placed, run_dir, run, asked = self.loaded('yard')
        def value(sql):
            return run.execute({'sql': sql})['rows'][0][0]
        self.assertEqual(value('SELECT COUNT(*) FROM SITE.SITE.PROJECTS'), value('SELECT COUNT(*) FROM site.projects'))
        self.assertEqual(value('SELECT COUNT(*) FROM "site"."site"."crew"'), value('SELECT COUNT(*) FROM site.crew'))
        self.assertEqual(value("SELECT 'site.site.projects' FROM site.site.projects LIMIT 1"), 'site.site.projects')
        self.assertEqual(local_data.engine_names('SELECT * FROM x.site.site.projects', 'site'),
                         'SELECT * FROM x.site.site.projects')
        run.bind(asked['database'], tables=['site.projects'], schema='site')
        with self.assertRaisesRegex(ValueError, 'outside the selected scope'):
            run.execute({'sql': 'SELECT COUNT(*) FROM site.site.crew'})

    def test_engine_tables_follow_the_database_kind(self):
        self.assertEqual(engine_table({'kind': 'sqlite', 'path': '/x/site.sqlite'}, 'site.crew'), 'site.site.crew')
        self.assertEqual(engine_table({'kind': 'duckdb', 'path': '/x/warehouse.duckdb'}, 'main.crew'), 'warehouse.main.crew')
        self.assertEqual(engine_table({'kind': 'duckdb', 'path': '/x/w.duckdb'}, 'db.main.crew'), 'db.main.crew')

    def test_a_prepared_engine_keeps_its_configuration(self):
        placed, run_dir, run, asked = self.loaded('yard')
        first = run.engine_config({'sources': 'dashboard'})
        Path(first['engine_run']).mkdir()
        self.assertEqual(run.engine_config({'sources': 'dashboard'})['engine_run'], first['engine_run'])
        run.history()
        changed = run.engine_config({'sources': 'all'})
        self.assertEqual(changed['engine_run'], str(run_dir / 'engine-2'))
        self.assertEqual(json.loads(Path(first['config']).read_text())['inputs']['history'], [])

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
