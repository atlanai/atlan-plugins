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
import side_page
import side_view
import visuals

ORDER = ('connect', 'sources', 'build', 'context', 'saved', 'answer', 'layout', 'edit')
BUILD = ['Use case', 'Sources', 'Select', 'Metrics', 'Save', 'Ask', 'Dashboard']
DEMO = ['Sources', 'Select', 'Metrics', 'Save', 'Ask', 'Dashboard']


def before_the_engine(run, stage='scope'):
    """The run as it stood before the metrics engine started: no engine run named, no engine record in the run, the
    given stage done."""
    meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    meta.pop('engine_run', None)
    manifest = run / 'engine' / 'manifest.json'
    if manifest.is_file():
        manifest.rename(manifest.with_name('manifest.later.json'))
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
        """The chat shows these moments; the side page reads the run's own files and needs nothing from them."""
        for kind in kinds:
            side_view.note(run, kind, visuals.build_spec(kind, run, list(data), engine_dir))

    def doc(self, run, engine_dir=None):
        return side_page.snapshot(run, engine_dir)

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
        self.assertEqual(doc['now']['at'], 0)
        self.assertEqual(doc['now']['next'], 'Select tables and dashboards', 'the Next line skips a step that is already done')

    def test_a_piece_of_work_ticks_as_soon_as_the_run_has_it(self):
        run, manifest, gates = self.building()
        self.assertEqual(self.step(self.doc(run), 'Metrics')['status'], 'now')
        self.assertNotEqual(self.nodes(self.doc(run), 'Metrics')["Keep queries about the dashboard's tables"].get('s'), 'done')
        manifest.write_text(gates, encoding='utf-8')
        kept = self.nodes(self.doc(run), 'Metrics')["Keep queries about the dashboard's tables"]
        self.assertEqual((kept['s'], kept['n']), ('done', '3 → 2'), 'its piece of work is ticked with its count, with no push')

    def test_metrics_tick_their_pieces_in_the_order_they_happen(self):
        run, manifest, gates = self.building()
        manifest.write_text(json.dumps({'stages': [{'stage': 'a3_translate', 'counts': {'units': 2, 'candidates': 2}},
                                                   {'stage': 'a3b_emit', 'counts': {'prompted': 2}}, {'stage': 'a3b_ingest', 'counts': {}}]}),
                            encoding='utf-8')
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
        save = self.nodes(self.doc(run), 'Save')
        self.assertEqual(save['You pick a workspace']['s'], 'done')
        self.assertNotEqual(save['Read every item back'].get('s'), 'done', 'nothing read back yet')

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

    def test_own_data_pages_carry_full_detail_on_this_machine(self):
        # The page never leaves this machine, so own data shows what a demo shows: names, values, the answer.
        run = make_run(self.root)
        self.record(run, 'connect')
        doc = self.doc(run)
        text = json.dumps(doc, ensure_ascii=False)
        for shown in ('Gross margin', 'Rework cost', '£118,681', 'Turbine base: -£6,360', 'Test workspace'):
            self.assertIn(shown, text)
        self.assertEqual((self.step(doc, 'Metrics')['fact'], self.step(doc, 'Ask')['fact']), ('2 metrics', '-£6,360'))
        self.assertEqual((doc['flow'], doc['title'], doc['subtitle']), ('build', 'Job margin', 'See which jobs lose margin and why'),
                         'named from the person\'s own use case, not a folder')
        early = make_run(self.root / 'early', saved=False)
        interaction = json.loads((early / 'interaction.json').read_text(encoding='utf-8'))
        (early / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        self.assertEqual(self.doc(early)['title'], 'Analytics setup', 'before the use case: a neutral name, never a stray folder\'s')

    def test_after_the_answer_a_demo_page_leads_with_it(self):
        run = make_run(self.root, demo=True)
        self.record(run, 'connect', data=[facts()])
        doc = self.doc(run)
        self.assertEqual((doc['now']['at'], doc['answer']['headline']), (5, 'Turbine base: -£6,360'))
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
        values = dict((r[0], r[1]) for r in self.step(self.doc(run), 'Metrics')['result'][1]['list'])
        self.assertEqual(values['Gross margin'], '£1')

    def test_a_fault_in_the_side_view_never_fails_the_command_that_carried_it(self):
        import csv
        from unittest import mock
        import project
        run = make_run(self.root, saved=False)
        with mock.patch.object(side_view, 'due', side_effect=csv.Error('a malformed query log')):
            self.assertEqual(project._due(run), {}, "the helper's own work already succeeded")

    def test_past_queries_read_are_one_number_and_the_page_says_where_some_went(self):
        run = make_run(self.root, saved=False)
        log = self.root / 'source' / 'history' / 'querylog.jsonl'
        log.write_text(log.read_text(encoding='utf-8') + json.dumps({'sql': 'SELECT broken FROM site.jobs', 'exec': 1}) + '\n',
                       encoding='utf-8')
        chip = visuals.render('sources', visuals.build_spec('sources', run), plain=True)
        card = visuals.render('build', visuals.build_spec('build', run), plain=True)
        self.assertTrue(chip.startswith('▤ 129 rows of your data') and '4 past queries' in chip, chip)
        self.assertRegex(card, r'Past queries read +█+ +4\n', 'the card counts what was read, as the chip does')
        self.assertEqual(self.nodes(side_view.model(run), 'Metrics')['Keep the queries the build can use']['n'], '4 → 3',
                         'the engine took in the 3 that ran; the page says so')
        clean = make_run(self.root / 'clean', saved=False)
        self.assertNotIn('Keep the queries the build can use', self.nodes(side_view.model(clean), 'Metrics'), 'nothing left out, no piece')

    def test_once_the_checks_are_over_an_item_never_run_needs_a_look_too(self):
        run = make_run(self.root, saved=False)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        del entries['sql_query:q-jobs']
        checks.write_text(json.dumps(entries), encoding='utf-8')
        at(run, 'preview')
        self.record(run, 'connect', 'context')
        self.assertEqual(self.step(self.doc(run), 'Metrics')['status'], 'done')
        self.assertEqual(self.nodes(self.doc(run), 'Metrics')['Hold what needs a look']['n'], '1 need a look',
                         "the save question's rule: whatever did not pass")
        self.assertEqual(visuals.render('context', visuals.build_spec('context', run)),
                         '△ **3 ran cleanly** · 1 need a look · on your data')

    def test_the_metrics_step_stays_open_until_its_checks_have_run(self):
        run = make_run(self.root, saved=False)
        checks = run / 'context' / 'candidate-checks.json'
        final = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: {'status': 'schema_checked', 'errors': []} for k in final}), encoding='utf-8')
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertEqual([self.step(doc, n)['status'] for n in ('Metrics', 'Save')], ['now', 'next'],
                         'the task list counts the first pass as the checks; a live run then showed "39 need a look"')
        self.assertNotIn('n', self.nodes(doc, 'Metrics')['Hold what needs a look'])
        checks.write_text(json.dumps(final), encoding='utf-8')
        self.assertEqual(self.step(self.doc(run), 'Metrics')['status'], 'done')

    def test_a_finished_save_closes_the_metrics_step_even_with_an_item_never_run(self):
        run = make_run(self.root, saved=False, local=True)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        entries['sql_query:q-jobs'] = {'status': 'schema_checked', 'errors': []}
        checks.write_text(json.dumps(entries), encoding='utf-8')
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertEqual([self.step(doc, n)['status'] for n in ('Metrics', 'Save')], ['done', 'done'],
                         'the person has kept the context; the page never walks back a finished step')

    def test_a_context_kept_here_finishes_the_save_whatever_the_journey_still_waits_on(self):
        # A changed rule re-runs its check, which leaves the run's progress at the checks again.
        for demo, stage in ((True, None), (False, None), (True, ('check', 'complete')), (True, ('check', 'running'))):
            run = make_run(self.root / ('%s-%s' % (demo, stage)), saved=False, local=True, demo=demo, stage=stage)
            interaction = run / 'interaction.json'
            interaction.write_text(json.dumps({**json.loads(interaction.read_text(encoding='utf-8')), 'intent_focus_required': True}),
                                   encoding='utf-8')
            doc = side_view.model(run)
            self.assertEqual([self.step(doc, n)['status'] for n in ('Metrics', 'Save', 'Ask')], ['done', 'done', 'done'],
                             'a live run stayed at "Save · Waiting for you" after the person kept, asked and edited')

    def test_the_choice_is_read_from_the_engine_when_the_config_lives_elsewhere(self):
        run = make_run(self.root, warehouse='snowflake', chosen={'bi': False, 'history': True}, saved=False)
        config = run / 'engine-config.json'
        engine = side_view.RunFacts(run).engine
        (engine / 'run.yml').write_text(json.dumps({'inputs': json.loads(config.read_text(encoding='utf-8'))['inputs']}), encoding='utf-8')
        config.unlink()
        pieces = self.nodes(side_view.model(run), 'Metrics')
        self.assertNotIn('Translate each tile and chart', pieces, 'the person chose past queries only; a stray dashboard file is not read')

    def test_a_demo_facts_file_never_turns_an_own_data_page_into_a_demo(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect', data=[facts()])
        doc = self.doc(run)
        self.assertEqual(doc['flow'], 'build', "a demo's facts passed on an own-data run would put its names and values on the page")
        self.assertNotIn(facts()['firm'], json.dumps(doc))
        self.assertNotIn(facts()['firm'], visuals.render('context', visuals.build_spec('context', run, [facts()])))

    def test_a_save_to_atlan_finishes_the_save_step_whatever_was_held(self):
        # A save that held an item leaves progress at publish; a rule changed after it puts progress back at the checks.
        from context_state import digest
        for shape, stage in (('held', ('publish', 'complete')), ('edited', ('check', 'complete'))):
            run = make_run(self.root / shape, stage=stage)
            meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            if shape == 'held':
                queries = json.loads((run / 'context' / 'queries.json').read_text(encoding='utf-8'))
                checks = run / 'context' / 'candidate-checks.json'
                entries = json.loads(checks.read_text(encoding='utf-8'))
                entries['sql_query:q-jobs'] = {'status': 'held', 'definition_hash': digest(queries[1]['definition']), 'errors': ['x']}
                checks.write_text(json.dumps(entries), encoding='utf-8')
                meta['publication']['artifacts'] = [a for a in meta['publication']['artifacts'] if a['name'] != 'q-jobs']
                (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
            else:
                metrics = json.loads((run / 'context' / 'metrics.json').read_text(encoding='utf-8'))
                metrics[0]['definition']['formula'] = 'SUM(y)'
                (run / 'context' / 'metrics.json').write_text(json.dumps(metrics), encoding='utf-8')
            doc = side_view.model(run)
            self.assertEqual([self.step(doc, n)['status'] for n in ('Metrics', 'Save')], ['done', 'done'],
                             'a page stuck on "Saving to Atlan Trident", or walked back, after the receipt landed (%s)' % shape)

    def test_finishing_without_a_dashboard_claims_no_page_built(self):
        run = make_run(self.root, demo=True, saved=False, local=True)
        interaction = run / 'interaction.json'
        interaction.write_text(json.dumps({**json.loads(interaction.read_text(encoding='utf-8')), 'dashboard': True}), encoding='utf-8')
        (run / 'layout.json').unlink(missing_ok=True)
        doc = side_view.model(run)
        self.assertEqual(self.step(doc, 'Dashboard')['status'], 'done')
        self.assertNotIn('Build the page', self.nodes(doc, 'Dashboard'), 'the person picked "I\'m done" with no tiles proposed')

    def test_the_answer_moment_finishes_the_ask_step_before_the_journey_records_it(self):
        run = make_run(self.root, demo=True, saved=False, local=True)
        interaction = run / 'interaction.json'
        interaction.write_text(json.dumps({**json.loads(interaction.read_text(encoding='utf-8')), 'asked': False}), encoding='utf-8')
        self.record(run, 'connect', data=[facts()])
        self.record(run, 'answer', data=[facts()])
        self.assertEqual(self.step(self.doc(run), 'Ask')['status'], 'done',
                         'a live run recorded the question after the answer, with its output thrown away; that update never reached the page')

    def test_a_malformed_breakdown_never_stops_the_page(self):
        run = make_run(self.root, demo=True)
        answer = run / 'answer.json'
        answer.write_text(json.dumps({**json.loads(answer.read_text(encoding='utf-8')), 'breakdown': {'steps': 5}}), encoding='utf-8')
        self.assertNotIn('Break the answer down', self.nodes(side_view.model(run), 'Ask'))

    def test_a_warehouse_built_from_its_dashboard_alone_reads_no_past_queries(self):
        run = make_run(self.root, warehouse='snowflake', chosen={'bi': True, 'history': False}, saved=False)
        self.assertEqual(visuals.render('sources', visuals.build_spec('sources', run)), '')

    def test_a_dashboard_only_build_shows_no_past_queries(self):
        run = make_run(self.root, saved=False, chosen={'bi': True, 'history': False})
        card = visuals.render('build', visuals.build_spec('build', run), plain=True)
        self.assertNotIn('Past queries', card, 'the person left the past queries out')

    def test_the_structural_pass_is_not_a_check_run(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        checks = run / 'context' / 'candidate-checks.json'
        names = list(json.loads(checks.read_text(encoding='utf-8')))
        checks.write_text(json.dumps({k: {'status': 'schema_checked', 'errors': []} for k in names}), encoding='utf-8')
        self.record(run, 'connect')
        self.assertNotIn('context', side_view.due(run), 'nothing has run yet (live runs showed "0/39 checked, 39 need a look")')
        metrics = self.nodes(self.doc(run), 'Metrics')
        self.assertEqual(metrics['Check each item is complete'].get('s'), 'done')
        for piece in ('Run each metric on the data', 'Run each query on the data', 'Hold what needs a look'):
            self.assertNotEqual(metrics[piece].get('s'), 'done', piece)
            self.assertNotIn('w', metrics[piece], piece)
        self.assertNotIn('n', metrics['Hold what needs a look'], 'no count of what needs a look before anything ran')
        entries = {k: {'status': 'schema_checked', 'errors': []} for k in names}
        entries['metric:Gross Margin'] = {'status': 'failed', 'errors': ['The table no longer holds that period.']}
        checks.write_text(json.dumps(entries), encoding='utf-8')
        hold = self.nodes(side_view.model(run), 'Metrics')['Hold what needs a look']
        self.assertEqual(hold['n'], '1 need a look', 'only what ran and did not pass')
        entries['metric:Rework Cost'] = {'status': 'example_executed', 'errors': [], 'composition': {'status': 'executed', 'rows': [[1.0]]}}
        entries.update({k: {'status': 'executed'} for k in names if k.startswith('sql_query:')})
        checks.write_text(json.dumps(entries), encoding='utf-8')
        metrics = self.nodes(self.doc(run), 'Metrics')
        self.assertEqual([(metrics[p]['n'], metrics[p].get('w')) for p in ('Run each metric on the data', 'Hold what needs a look')],
                         [('1 of 2', True), ('1 need a look', True)])
        self.assertEqual(side_view.due(run), ['sources', 'group', 'tiles', 'described', 'metrics', 'rules', 'links', 'build', 'context'])
        self.assertEqual(visuals.render('context', visuals.build_spec('context', run)),
                         '△ **3 ran cleanly** · 1 need a look · on your data')

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

    def test_a_paused_run_says_so_instead_of_looking_busy(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps(dict(interaction, mode='paused')), encoding='utf-8')
        self.assertEqual(side_view.model(run).get('state'), 'paused')
        self.assertNotIn('state', side_view.model(make_run(self.root / 'b', saved=False)), 'a running run carries no state')

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
        self.assertEqual(self.step(self.doc(run, 'work/engine'), 'Metrics')['fact'], '2 metrics')

    def test_a_save_under_way_stays_open_until_it_is_read_back(self):
        run = make_run(self.root, saved=False, stage=('publish', 'running'))
        # The receipt's real shape (publication_receipts.py): a list of what was read back as live.
        (run / 'context' / 'published.json').write_text(json.dumps([{'id': 'm1', 'kind': 'metric', 'name': 'Gross Margin'}]),
                                                       encoding='utf-8')
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
                          'Select': 'Dashboard and past queries', 'Metrics': '2 metrics', 'Save': '4 live in Atlan', 'Ask': '-£6,360',
                          'Dashboard': None})

    def test_a_run_whose_task_list_cannot_be_read_follows_its_moments(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect')
        (run / 'interaction.json').write_text('{"mode": "nonsense"}', encoding='utf-8')
        self.record(run, 'sources', 'build')
        doc = self.doc(run)
        self.assertEqual([s['name'] for s in doc['steps']], BUILD)
        self.assertEqual([s['status'] for s in doc['steps']], ['done', 'done', 'done', 'done', 'now', 'next', 'next'],
                         'each moment seen finishes its step, and so do checks with every verdict in')
