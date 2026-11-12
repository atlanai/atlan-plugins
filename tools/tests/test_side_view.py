"""The side view: the flow's own steps, each piece of work ticked as the run finishes it, read from the run's own files."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, at, facts, make_run

sys.path.insert(0, str(SCRIPTS))
import side_view
import visuals

ORDER = ('connect', 'sources', 'build', 'context', 'saved', 'answer', 'layout', 'edit')
BUILD = ['Use case', 'Sources', 'Select', 'Metrics', 'Save', 'Ask', 'Dashboard']
DEMO = ['Sources', 'Select', 'Metrics', 'Save', 'Ask', 'Dashboard']


def before_the_engine(run, stage='scope'):
    """The run as it stood before the metrics engine started: no engine run named, the given stage done."""
    meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    meta.pop('engine_run', None)
    (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
    at(run, stage)
    return meta


class SideView(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, run, *kinds, data=(), engine_dir=None):
        return {kind: side_view.record(run, kind, visuals.build_spec(kind, run, list(data), engine_dir), engine_dir) for kind in kinds}

    def doc(self, run):
        return json.loads(sorted((run / 'view' / 'steps').glob('s*.json'))[-1].read_text(encoding='utf-8'))

    def step(self, doc, name):
        return next(s for s in doc['steps'] if s['name'] == name)

    def nodes(self, doc, name):
        return {n['t']: n for n in self.step(doc, name)['how']}

    def building(self, **kw):
        """A run whose engine has started but not finished a gate, with the moments before it recorded."""
        run = make_run(self.root, saved=False, **kw)
        manifest = run / 'engine' / 'manifest.json'
        gates = manifest.read_text(encoding='utf-8')
        manifest.write_text(json.dumps({'stages': []}), encoding='utf-8')
        at(run, 'scope')
        self.record(run, 'connect', 'sources')
        return run, manifest, gates

    def test_each_flow_shows_its_own_steps(self):
        run = make_run(self.root / 'own')
        self.record(run, 'connect')
        self.assertEqual([s['name'] for s in self.doc(run)['steps']], BUILD)
        self.assertEqual(self.step(self.doc(run), 'Select')['purpose'], 'Select tables and dashboards', 'the task list\'s own words')
        demo = make_run(self.root / 'demo', demo=True)
        self.record(demo, 'connect', data=[facts()])
        self.assertEqual([s['name'] for s in self.doc(demo)['steps']], DEMO)
        self.assertEqual(self.step(self.doc(demo), 'Select')['purpose'], 'Choose what to build from')
        titles = [n['t'] for s in self.doc(run)['steps'] for n in s['how']]
        self.assertNotIn('You pick a demo', titles, 'own data shows no demo work')

    def test_a_step_finished_ahead_of_the_open_one_shows_done(self):
        run = make_run(self.root, saved=False)
        before_the_engine(run, 'inspect')
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertEqual([s['status'] for s in doc['steps']][:3], ['now', 'done', 'next'],
                         'own data surveys its sources before the use case is saved, and the page shows the survey done')
        self.assertEqual(self.step(doc, 'Sources')['fact'], 'Power BI dashboard, 2 data files, query history')
        self.assertTrue(all(n['s'] == 'done' for n in self.step(doc, 'Sources')['how']))
        self.assertEqual(doc['current'], 0)
        page = side_view.PAGE.read_text(encoding='utf-8')
        self.assertIn("find(s => s.status !== 'done')", page, 'the Next box skips a step that is already done')

    def test_the_page_is_offered_until_it_is_published(self):
        run = make_run(self.root, saved=False)
        meta = before_the_engine(run)
        self.assertIn('page', self.record(run, 'connect')['connect'])
        (run / 'run.json').write_text(json.dumps({**meta, 'engine_run': str(run / 'engine')}), encoding='utf-8')
        at(run, 'check')
        self.assertIn('page', self.record(run, 'build')['build'], 'a first moment whose page was never published is caught here')
        side_view.link(run, 'https://claude.ai/artifact/abc')
        at(run, 'preview')
        self.assertNotIn('page', self.record(run, 'context')['context'], 'once published, only updates')

    def test_a_piece_of_work_ticks_as_soon_as_the_run_has_it(self):
        run, manifest, gates = self.building()
        side_view.link(run, 'https://claude.ai/artifact/abc')
        before = self.doc(run)['n']
        self.assertEqual(self.step(self.doc(run), 'Metrics')['status'], 'now')
        self.assertNotEqual(self.nodes(self.doc(run), 'Metrics')["Keep queries about the dashboard's tables"].get('s'), 'done')
        manifest.write_text(gates, encoding='utf-8')
        out = side_view.progress(run)
        self.assertEqual(self.doc(run)['n'], before + 1, 'a new document, numbered after the last')
        self.assertEqual(out['url'], 'https://claude.ai/artifact/abc')
        kept = self.nodes(self.doc(run), 'Metrics')["Keep queries about the dashboard's tables"]
        self.assertEqual((kept['s'], kept['n']), ('done', '3 → 2'), 'its piece of work is ticked with its count')
        self.assertEqual(side_view.progress(run), {}, 'nothing new finished: no new document')

    def test_a_count_that_moves_without_a_tick_waits_for_the_next_document(self):
        run, manifest, gates = self.building()
        manifest.write_text(gates, encoding='utf-8')
        side_view.progress(run)
        stages = json.loads(gates)['stages']
        stages[1]['counts']['kept'] = 1
        manifest.write_text(json.dumps({'stages': stages}), encoding='utf-8')
        self.assertEqual(side_view.progress(run), {}, 'only a tick or a step moving is worth a push')

    def test_an_engine_gate_names_the_update_for_its_setup_run(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / 'metrics-generation'))
        from modules.metrics import driver
        run, manifest, gates = self.building()
        side_view.link(run, 'https://claude.ai/artifact/abc')
        manifest.write_text(gates, encoding='utf-8')
        self.assertIn('doc_id `s%02d`' % (self.doc(run)['n'] + 1), driver._side_view(run / 'engine')['side_view'],
                      'each gate ticks on the page as it finishes')
        self.assertEqual(driver._side_view(run / 'engine'), {}, 'a gate that changed nothing names nothing')
        self.assertEqual(driver._side_view(self.root / 'elsewhere' / 'engine'), {}, 'an engine run outside a setup run is left alone')

    def test_metrics_tick_their_pieces_in_the_order_they_happen(self):
        run, manifest, gates = self.building()
        manifest.write_text(json.dumps({'stages': [{'stage': 'a3_translate', 'counts': {'units': 2, 'candidates': 2}},
                                                   {'stage': 'a3b_emit', 'counts': {'prompted': 2}}, {'stage': 'a3b_ingest', 'counts': {}}]}),
                            encoding='utf-8')
        side_view.progress(run)
        metrics = self.nodes(self.doc(run), 'Metrics')
        self.assertEqual(metrics['Translate each tile and chart'].get('s'), 'done', 'translated before it was reviewed')
        self.assertEqual(metrics['Review each translation'].get('s'), 'done')
        self.assertNotEqual(metrics['Describe every query in plain words'].get('s'), 'done')

    def test_a_piece_waits_for_every_item_before_ticking_a_run_of_checks(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: v for k, v in entries.items() if k.startswith('metric:Gross')}), encoding='utf-8')
        self.record(run, 'connect')
        self.assertEqual(self.step(self.doc(run), 'Metrics')['status'], 'now')
        ran = self.nodes(self.doc(run), 'Metrics')['Run each metric on the data']
        self.assertNotEqual(ran.get('s'), 'done', 'one of two metrics checked is not done yet')

    def test_the_save_ticks_the_workspace_as_soon_as_it_is_picked(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect', 'sources', 'build', 'context')
        save = self.nodes(self.doc(run), 'Save')
        self.assertEqual(self.step(self.doc(run), 'Save')['status'], 'now')
        self.assertEqual(save['You choose where the context lives'].get('s'), 'next', 'the person has not chosen yet')
        (run / 'authorization.json').write_text(json.dumps({'workspace_id': 'w1', 'approved_plans': []}), encoding='utf-8')
        side_view.progress(run)
        save = self.nodes(self.doc(run), 'Save')
        self.assertEqual(save['You pick a workspace']['s'], 'done')
        self.assertNotEqual(save['Read every item back'].get('s'), 'done', 'nothing read back yet')

    def test_the_first_moment_writes_the_page_and_a_document(self):
        run = make_run(self.root, demo=True, saved=False)
        before_the_engine(run, 'inspect')
        out = self.record(run, 'connect', data=[facts()])['connect']
        self.assertEqual(set(out), {'page', 'doc'})
        page = Path(out['page']).read_text()
        self.assertIn('<title>Job profit agent for Fixit &amp; Co.</title>', page)
        self.assertNotIn('<!doctype', page.lower(), 'the artifact tool wraps the page in its own document')
        self.assertIn('--bg:light-dark(', page, 'the design tokens are filled in, light and dark')
        self.assertEqual(set(re.findall(r'https://([^/\'")]+)', page)), {'fonts.googleapis.com'}, 'the page loads nothing but its font')
        local = (run / 'view' / 'progress.html').read_text()
        self.assertNotIn('https://', local, 'the local copy loads nothing from the network')
        doc = self.doc(run)
        self.assertEqual([s['status'] for s in doc['steps']], ['done', 'now', 'next', 'next', 'next', 'next'])
        self.assertEqual(self.step(doc, 'Select')['doing'], 'Waiting for your choice of sources', 'the step under way says what it waits for')
        self.assertTrue(self.step(doc, 'Select')['needs_you'])
        self.assertTrue(doc['demo'])
        self.assertEqual(doc['n'], 1)

    def test_a_moment_writes_a_document_only_when_the_page_would_change(self):
        run = make_run(self.root, saved=False)
        meta = before_the_engine(run)
        out = self.record(run, 'connect', 'sources')
        self.assertTrue(out['connect']['doc'].endswith('s01.json'))
        self.assertEqual(sorted(out['sources']), ['page'], 'nothing new on the page, but the page is offered again until published')
        (run / 'run.json').write_text(json.dumps({**meta, 'engine_run': str(run / 'engine')}), encoding='utf-8')
        at(run, 'check')
        self.assertTrue(self.record(run, 'build')['build']['doc'].endswith('s02.json'), 'the metrics finished: the page moves on')
        repeated = self.record(run, 'build', 'context')
        self.assertFalse([k for k, v in repeated.items() if 'doc' in v], 'a repeated moment writes nothing')

    def test_a_finished_step_shows_how_with_numbers_and_who_did_it(self):
        run = make_run(self.root, demo=True, saved=False)
        self.record(run, 'connect', data=[facts()])
        metrics = self.step(self.doc(run), 'Metrics')
        self.assertEqual((metrics['status'], metrics['fact']), ('done', '2 metrics'))
        nodes = self.nodes(self.doc(run), 'Metrics')
        self.assertEqual(nodes["Keep queries about the dashboard's tables"]['n'], '3 → 2')
        self.assertEqual(nodes['Merge numbers that share a rule']['n'], '3 → 2')
        self.assertIn('hold 3 numbers', nodes['Merge numbers that share a rule']['d'], 'the counts before and after add up')
        self.assertEqual(nodes['Review each translation']['by'], 'Metrics engine')
        self.assertEqual(metrics['result'][0]['list'], [['Gross margin', '1 from the dashboard, 1 from past queries'], ['Rework cost', '1 from past queries']])
        self.assertEqual(self.step(self.doc(run), 'Save')['status'], 'now')

    def test_own_data_pages_carry_progress_only(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        doc = self.doc(run)
        text = json.dumps(doc)
        for private in ('Gross margin', 'Rework cost', '118,681', 'Turbine base', '6,360', 'Test workspace'):
            self.assertNotIn(private, text, 'metric names, values, answers and workspaces stay in the chat')
        self.assertNotIn('answer', doc)
        self.assertFalse([s['name'] for s in doc['steps'] if 'result' in s])
        self.assertEqual((self.step(doc, 'Metrics')['fact'], self.step(doc, 'Ask')['fact']), ('2 metrics', 'Answered in the chat'))
        self.assertEqual((doc['title'], doc['tagline']), ('Job margin', 'See which jobs lose margin and why'),
                         'named from the person\'s own use case, not a folder')
        early = make_run(self.root / 'early', saved=False)
        interaction = json.loads((early / 'interaction.json').read_text(encoding='utf-8'))
        (early / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        self.assertIn('<title>Analytics setup</title>', Path(self.record(early, 'connect')['connect']['page']).read_text(),
                      'published before the use case: a neutral name, never a stray folder\'s')

    def test_after_the_answer_a_demo_page_leads_with_it(self):
        run = make_run(self.root, demo=True)
        self.record(run, 'connect', data=[facts()])
        doc = self.doc(run)
        self.assertEqual((doc['current'], doc['answer']['headline']), (5, 'Turbine base: -£6,360'))
        self.assertEqual(self.nodes(doc, 'Ask')['Read the saved rules']['n'], '1 rule · from Atlan')
        unasked = make_run(self.root / 'b', answered=False, demo=True)
        self.assertNotIn('answer', side_view.model(unasked), 'no answer before a question')

    def test_a_finished_step_shows_the_runs_current_truth(self):
        run = make_run(self.root, demo=True, saved=False)
        self.record(run, 'connect', data=[facts()])
        checks = run / 'context' / 'candidate-checks.json'
        changed = json.loads(checks.read_text(encoding='utf-8'))
        changed['metric:Gross Margin']['composition']['rows'] = [[1.0]]
        checks.write_text(json.dumps(changed))
        values = dict((r[0], r[1]) for r in self.step(side_view.model(run), 'Metrics')['result'][1]['list'])
        self.assertEqual(values['Gross margin'], '£1', 'a rule changed after the checks shows its new value, as the edit card does')

    def test_a_warehouse_run_reads_as_a_warehouse(self):
        run = make_run(self.root, warehouse='snowflake', chosen={'bi': False, 'history': True}, saved=False)
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertEqual(self.step(doc, 'Sources')['fact'], 'Snowflake warehouse, Power BI dashboard', 'a stray dashboard file is found, truthfully')
        self.assertEqual(self.step(doc, 'Select')['fact'], 'Past queries, 2 tables')
        metrics = self.nodes(doc, 'Metrics')
        self.assertEqual(metrics["Read the chosen tables' columns"]['n'], '2 tables')
        self.assertEqual(metrics['Read the past queries from Snowflake']['n'], '3 queries')
        self.assertIn('Keep queries about the chosen tables', metrics, 'no dashboard was chosen')
        self.assertIn('Run each metric on Snowflake', metrics)
        for absent in ('Load the tables on this machine', 'Translate each tile and chart', "Keep queries about the dashboard's tables"):
            self.assertNotIn(absent, metrics, absent + ': a piece the run never does is never drawn')

    def test_a_dashboard_only_choice_shows_no_history_work(self):
        run = make_run(self.root, chosen={'bi': True, 'history': False}, saved=False)
        before_the_engine(run, 'scope')
        self.record(run, 'connect')
        titles = [n['t'] for n in self.step(self.doc(run), 'Metrics')['how']]
        self.assertNotIn('Group repeats into questions', titles, 'past queries exist but were not chosen')
        self.assertIn('Translate each tile and chart', titles)

    def test_failed_checks_need_a_look_and_say_so(self):
        run = make_run(self.root, failing=2, saved=False)
        self.record(run, 'connect')
        metrics = self.nodes(self.doc(run), 'Metrics')
        self.assertEqual((metrics['Run each query on the data']['n'], metrics['Run each query on the data'].get('w')), ('0 of 2', True))
        self.assertEqual((metrics['Hold what needs a look']['n'], metrics['Hold what needs a look'].get('w')), ('2 need a look', True))
        self.assertNotIn('w', metrics['Run each metric on the data'], 'every metric passed')

    def test_a_moved_run_folder_keeps_its_engine(self):
        import shutil
        run = make_run(self.root / 'before', saved=False)
        moved = self.root / 'after' / 'run'
        shutil.copytree(run, moved)
        shutil.rmtree(self.root / 'before')
        doc = side_view.model(moved)
        self.assertEqual(self.step(doc, 'Metrics')['fact'], '2 metrics')
        self.assertEqual(self.step(doc, 'Save')['status'], 'now', 'statuses still come from the task list, not a guess')

    def test_the_page_is_offered_at_every_moment_until_published(self):
        run = make_run(self.root, saved=False)
        meta = before_the_engine(run)
        line = side_view.push(self.record(run, 'connect')['connect'])
        self.assertTrue(line.startswith('Open the side view now: publish `'))
        self.assertIn('names no metric, table or value', line, 'the artifact description keeps the run private')
        self.assertIn('If you already published this page, run only that command', line, 'a half-finished open never publishes twice')
        self.assertNotIn('local', line.replace('local_', ''), 'no branch an agent with the tools could take')
        (run / 'run.json').write_text(json.dumps({**meta, 'engine_run': str(run / 'engine')}), encoding='utf-8')
        at(run, 'check')
        between = side_view.progress(run)
        self.assertNotIn('page', between, 'between moments only pushes: a host with no artifact tools hears the offer a few times')
        self.assertIn('page', self.record(run, 'build')['build'], 'a skipped offer comes back at the next moment, however many updates passed')
        named = side_view.link(run, 'https://claude.ai/artifact/abc')
        self.assertEqual(Path(named['doc']).name, 's%02d.json' % self.doc(run)['n'], 'recording the address names the newest update to push')
        self.assertIn('ToolSearch', side_view.push(named))

    def test_a_paused_run_says_so_instead_of_looking_busy(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps(dict(interaction, mode='paused')), encoding='utf-8')
        self.assertEqual(side_view.model(run).get('state'), 'paused')
        self.assertNotIn('state', side_view.model(make_run(self.root / 'b', saved=False)), 'a running run carries no state')
        page = side_view.PAGE.read_text(encoding='utf-8')
        self.assertIn("paused: 'Paused'", page)
        self.assertIn('.stopped .sp{animation:none}', page, 'no spinner on a run that is not moving')

    def test_a_survey_path_stays_off_an_own_data_page(self):
        run = make_run(self.root, saved=False)
        before_the_engine(run, 'inspect')
        at(run, 'inspect', 'running')
        progress = json.loads((run / 'progress.json').read_text(encoding='utf-8'))
        (run / 'progress.json').write_text(json.dumps(dict(progress, completed_items=1, total_items=3)), encoding='utf-8')
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        meta['scope'] = [{'name': 'secret/folder/report.pbit', 'status': 'pending_inspection'}]
        (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        self.assertNotIn('secret/folder', json.dumps(side_view.model(run)), 'the count, never the path')

    def test_every_piece_names_the_part_that_did_it_and_nothing_more(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertNotIn('parts', doc)
        for step in doc['steps']:
            for n in step['how']:
                self.assertEqual(set(n) - {'n'}, {'t', 'd', 'by', 's'}, n['t'])
        self.assertNotIn('Claude', json.dumps(doc), 'no judgement marker')

    def test_steps_ahead_say_how_they_work_without_numbers(self):
        run = make_run(self.root, saved=False)
        before_the_engine(run, 'inspect')
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertEqual(self.step(doc, 'Select')['status'], 'now')
        for step in doc['steps'][3:]:
            self.assertTrue(step['how'], step['name'])
            self.assertFalse([n for n in step['how'] if 'n' in n], step['name'])
            self.assertEqual({n['s'] for n in step['how']}, {'next'}, step['name'])

    def test_a_dashboard_only_run_shows_no_history_work(self):
        run = make_run(self.root, history=False)
        self.record(run, 'connect')
        titles = [n['t'] for s in self.doc(run)['steps'] for n in s['how']]
        self.assertNotIn('Group repeats into questions', titles)
        self.assertNotIn('Count the past queries', titles)
        self.assertIn('Translate each tile and chart', titles)
        self.assertEqual(self.step(self.doc(run), 'Select')['fact'], 'Dashboard only')

    def test_a_history_only_run_shows_no_dashboard_work(self):
        run = make_run(self.root, dashboard=False)
        self.record(run, 'connect')
        titles = [n['t'] for s in self.doc(run)['steps'] for n in s['how']]
        self.assertNotIn('Translate each tile and chart', titles)
        self.assertNotIn('Read the dashboard', titles)
        self.assertIn('Keep queries about the chosen tables', titles)

    def test_own_data_loads_its_tables_when_the_metrics_start(self):
        run = make_run(self.root, dashboard=False, history=False)
        self.record(run, 'connect')
        self.assertEqual(self.step(self.doc(run), 'Sources')['fact'], '2 data files')
        self.assertEqual(self.nodes(self.doc(run), 'Metrics')['Load the tables on this machine']['n'], '2 tables · 129 rows')

    def test_a_bound_database_ticks_its_piece(self):
        run = make_run(self.root, saved=False)
        (run / 'context' / 'data-preparation.json').unlink()
        self.record(run, 'connect')
        piece = self.nodes(self.doc(run), 'Metrics')['Open the database read-only']
        self.assertEqual((piece['s'], piece['n']), ('done', '2 tables'), 'bound as it is, with nothing prepared, still ticks')

    def test_a_demo_loads_its_tables_with_its_sources(self):
        run = make_run(self.root, demo=True)
        self.record(run, 'connect', data=[facts()])
        sources = self.step(self.doc(run), 'Sources')
        self.assertEqual(sources['purpose'], 'Load demo data: 2 tables')
        self.assertEqual(sources['result'][1]['list'], [['work logs', '120 rows'], ['jobs', '9 rows']])

    def test_an_engine_that_left_counts_out_still_draws(self):
        run = make_run(self.root, saved=False)
        manifest = run / 'engine' / 'manifest.json'
        manifest.write_text(json.dumps({'stages': [{'stage': 'a4_witness_bi', 'counts': {'witnessed': 2}}, {'stage': 'x2_ingest', 'counts': {}}]}))
        self.record(run, 'connect')
        self.assertEqual(self.nodes(self.doc(run), 'Metrics')['Translate each tile and chart']['n'], '0 of 2')
        manifest.write_text('{"stages": "not a list"}')
        self.assertTrue(side_view.model(run)['steps'], 'a broken manifest leaves a step with less to show, not a crash')

    def test_an_engine_folder_named_on_the_command_is_remembered(self):
        run = make_run(self.root, engine='work/engine', saved=False)
        self.record(run, 'connect', engine_dir='work/engine')
        self.assertEqual(self.step(self.doc(run), 'Metrics')['fact'], '2 metrics')

    def test_a_save_under_way_stays_open_until_it_is_read_back(self):
        run = make_run(self.root, saved=False, stage=('publish', 'running'))
        (run / 'context' / 'published.json').write_text(json.dumps({'metric:Gross Margin': {}}), encoding='utf-8')
        self.record(run, 'connect')
        save = self.step(self.doc(run), 'Save')
        self.assertEqual((save['status'], save['doing']), ('now', 'Saving to Atlan Trident'))
        self.assertEqual(self.nodes(self.doc(run), 'Save')['Save each metric and query']['n'], '1 saved so far')
        self.assertNotIn('confirmed', json.dumps(self.doc(run)), 'there is no half-done state, only not yet and done')
        make_run(self.root, saved=True)
        save = self.step(side_view.model(run), 'Save')
        self.assertEqual((save['status'], save['fact']), ('done', '4 live in Atlan'), 'a read-back that arrives later finishes it')

    def test_a_context_kept_local_finishes_the_save(self):
        run = make_run(self.root, local=True)
        self.record(run, 'connect')
        save = self.step(self.doc(run), 'Save')
        self.assertEqual((save['status'], save['fact']), ('done', 'Kept on this machine'))
        self.assertEqual([n['t'] for n in save['how']], ['You keep the context on this machine'])

    def test_each_finished_step_reads_a_short_fact(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        self.assertEqual({s['name']: s.get('fact') for s in self.doc(run)['steps']},
                         {'Use case': 'Job margin', 'Sources': 'Power BI dashboard, 2 data files, query history',
                          'Select': 'Dashboard and past queries', 'Metrics': '2 metrics', 'Save': '4 live in Atlan', 'Ask': 'Answered in the chat',
                          'Dashboard': None})

    def test_a_run_whose_task_list_cannot_be_read_follows_its_moments(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect')
        (run / 'interaction.json').write_text('{"mode": "nonsense"}', encoding='utf-8')
        self.record(run, 'sources', 'build')
        doc = self.doc(run)
        self.assertEqual([s['name'] for s in doc['steps']], BUILD)
        self.assertEqual([s['status'] for s in doc['steps']], ['done', 'done', 'done', 'now', 'next', 'next', 'next'],
                         'each moment seen finishes its step')

    def test_a_damaged_state_file_never_reuses_a_document_number(self):
        run = make_run(self.root, saved=False)
        before_the_engine(run)
        self.record(run, 'connect')
        (run / 'view' / 'state.json').write_text('{not json', encoding='utf-8')
        self.record(run, 'sources')
        self.assertEqual(self.doc(run)['n'], 2, 'the page shows the newest number, so a new start continues from it')

    def test_the_local_page_loads_its_data_file_by_itself(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        page = (run / 'view' / 'progress.html').read_text()
        self.assertTrue(page.lower().startswith('<!doctype html>'))
        self.assertLess(page.index('SIDE_VIEW_FILE'), page.index('<body>'), 'the data file is named before the page script runs')
        data = (run / 'view' / 'progress-data.js').read_text()
        self.assertEqual(json.loads(data[data.index('{'):data.rindex('}') + 1])['n'], 1)

    def test_the_page_counts_the_flow_own_steps_and_marks_nothing_as_claude(self):
        page = side_view.PAGE.read_text(encoding='utf-8')
        self.assertNotIn('STEPS = 6', page)
        self.assertIn('d.steps.length', page)
        self.assertNotIn('Claude made', page)
        self.assertNotIn('unconfirmed', page)

    def test_a_published_address_is_named_in_every_later_update(self):
        run = make_run(self.root, saved=False)
        before_the_engine(run)
        self.record(run, 'connect')
        side_view.link(run, 'https://claude.ai/artifact/abc')
        at(run, 'check')
        (run / 'run.json').write_text(json.dumps({**json.loads((run / 'run.json').read_text()), 'engine_run': str(run / 'engine')}))
        self.assertEqual(self.record(run, 'build')['build']['url'], 'https://claude.ai/artifact/abc')
        with self.assertRaises(ValueError):
            side_view.link(run, 'https://example.com/page')


if __name__ == '__main__':
    unittest.main()
