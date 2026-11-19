"""The side view: the moments the chat has shown and the ones due, and what the side page shows of the run, read from
the run's own files."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, at, facts, make_run

sys.path.insert(0, str(SCRIPTS))
import side_page
import side_view
import visuals


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

    def built(self, run):
        """The build's checkpoints the page follows, by id."""
        return [s['id'] for s in side_page.snapshot(run)['build']]

    def test_own_data_pages_carry_full_detail_on_this_machine(self):
        # The page never leaves this machine, so own data shows what a demo shows: names, values, where it was kept.
        run = make_run(self.root)
        self.record(run, 'connect')
        page = side_page.snapshot(run)
        text = json.dumps(page, ensure_ascii=False)
        for shown in ('Gross margin', 'Rework cost', '£118,681', 'Test workspace'):
            self.assertIn(shown, text)
        self.assertEqual((page['flow'], page['title'], page['subtitle']), ('build', 'Job margin', 'See which jobs lose margin and why'),
                         'named from the person\'s own use case, not a folder')
        early = make_run(self.root / 'early', saved=False)
        interaction = json.loads((early / 'interaction.json').read_text(encoding='utf-8'))
        (early / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        self.assertEqual(side_page.snapshot(early)['title'], 'Analytics setup', 'before the use case: a neutral name, never a stray folder\'s')

    def test_a_metric_shows_the_runs_current_truth(self):
        run = make_run(self.root, demo=True, saved=False)
        self.record(run, 'connect', data=[facts()])
        checks = run / 'context' / 'candidate-checks.json'
        changed = json.loads(checks.read_text(encoding='utf-8'))
        changed['metric:Gross Margin']['composition']['rows'] = [[1.0]]
        checks.write_text(json.dumps(changed))
        values = {m['name']: m.get('value') for m in side_page.snapshot(run)['metrics']}
        self.assertEqual(values['Gross margin'], '£1', 'a rule changed after the checks shows its new value, as the edit card does')

    def test_a_fault_in_the_side_view_never_fails_the_command_that_carried_it(self):
        import csv
        from unittest import mock
        import project
        run = make_run(self.root, saved=False)
        with mock.patch.object(side_view, 'due', side_effect=csv.Error('a malformed query log')):
            self.assertEqual(project._due(run), {}, "the helper's own work already succeeded")

    def test_past_queries_read_are_one_number(self):
        run = make_run(self.root, saved=False)
        log = self.root / 'source' / 'history' / 'querylog.jsonl'
        log.write_text(log.read_text(encoding='utf-8') + json.dumps({'sql': 'SELECT broken FROM site.jobs', 'exec': 1}) + '\n',
                       encoding='utf-8')
        chip = visuals.render('sources', visuals.build_spec('sources', run), plain=True)
        card = visuals.render('build', visuals.build_spec('build', run), plain=True)
        self.assertTrue(chip.startswith('▤ 129 rows of your data') and '4 past queries' in chip, chip)
        self.assertRegex(card, r'Past queries read +█+ +4\n', 'the card counts what was read, as the chip does')

    def test_once_the_checks_are_over_an_item_never_run_needs_a_look_too(self):
        run = make_run(self.root, saved=False)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        del entries['sql_query:q-jobs']
        checks.write_text(json.dumps(entries), encoding='utf-8')
        at(run, 'preview')
        self.record(run, 'connect', 'context')
        self.assertEqual(visuals.render('context', visuals.build_spec('context', run)),
                         '△ **3 ran cleanly** · 1 need a look · on your data')

    def test_the_choice_is_read_from_the_engine_when_the_config_lives_elsewhere(self):
        run = make_run(self.root, warehouse='snowflake', chosen={'bi': False, 'history': True}, saved=False)
        config = run / 'engine-config.json'
        engine = side_view.RunFacts(run).engine
        (engine / 'run.yml').write_text(json.dumps({'inputs': json.loads(config.read_text(encoding='utf-8'))['inputs']}), encoding='utf-8')
        config.unlink()
        self.assertNotIn('translate', self.built(run), 'the person chose past queries only; a stray dashboard file is not read')

    def test_a_demo_facts_file_never_turns_an_own_data_page_into_a_demo(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect', data=[facts()])
        page = side_page.snapshot(run)
        self.assertEqual(page['flow'], 'build', "a demo's facts passed on an own-data run would put its names and values on the page")
        self.assertNotIn(facts()['firm'], json.dumps(page))
        self.assertNotIn(facts()['firm'], visuals.render('context', visuals.build_spec('context', run, [facts()])))

    def test_a_warehouse_built_from_its_dashboard_alone_reads_no_past_queries(self):
        run = make_run(self.root, warehouse='snowflake', chosen={'bi': True, 'history': False}, saved=False)
        self.assertEqual(visuals.render('sources', visuals.build_spec('sources', run)), '')

    def test_a_dashboard_only_build_shows_no_past_queries(self):
        run = make_run(self.root, saved=False, chosen={'bi': True, 'history': False})
        card = visuals.render('build', visuals.build_spec('build', run), plain=True)
        self.assertNotIn('Past queries', card, 'the person left the past queries out')

    def test_the_page_opens_once_the_build_has_a_possible_metric_to_show(self):
        # A tester, 2026-11-19: the panel opened as the build started and stayed empty for a while. "Let's only open
        # it once the metric draft starts to show up."
        run = make_run(self.root, saved=False)
        for name in ('context/metrics.json', 'context/queries.json', 'engine/merged_metrics.json', 'engine/metric_cards.json'):
            (run / name).unlink()
        self.assertNotIn('connect', side_view.due(run))
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(run), 'visual', 'connect'],
                             capture_output=True, text=True, timeout=60)
        self.assertIn('Nothing to show on the side page yet', out.stdout)
        self.assertNotIn('url', out.stdout)
        self.assertFalse((run / 'view' / 'server.json').exists(), 'no page started with nothing on it')
        self.assertNotIn('connect', side_view.seen(run))
        batch = run / 'engine' / 'answers' / 'x1' / '0001.json'
        batch.parent.mkdir(parents=True)
        batch.write_text(json.dumps({'statements': [{'vq_id': 'h-1', 'metrics': [{'name': 'Rework cost'}]}]}), encoding='utf-8')
        self.assertEqual(side_view.due(run)[0], 'connect')

    def test_the_structural_pass_is_not_a_check_run(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        checks = run / 'context' / 'candidate-checks.json'
        names = list(json.loads(checks.read_text(encoding='utf-8')))
        checks.write_text(json.dumps({k: {'status': 'schema_checked', 'errors': []} for k in names}), encoding='utf-8')
        self.record(run, 'connect')
        self.assertNotIn('context', side_view.due(run), 'nothing has run yet (live runs showed "0/39 checked, 39 need a look")')
        entries = {k: {'status': 'schema_checked', 'errors': []} for k in names}
        entries['metric:Gross Margin'] = {'status': 'failed', 'errors': ['The table no longer holds that period.']}
        entries['metric:Rework Cost'] = {'status': 'example_executed', 'errors': [], 'composition': {'status': 'executed', 'rows': [[1.0]]}}
        entries.update({k: {'status': 'executed'} for k in names if k.startswith('sql_query:')})
        checks.write_text(json.dumps(entries), encoding='utf-8')
        self.assertEqual(side_view.due(run), ['sources', 'group', 'tiles', 'described', 'drafted', 'metrics', 'rules', 'links', 'build', 'context'])
        self.assertEqual(visuals.render('context', visuals.build_spec('context', run)),
                         '△ **3 ran cleanly** · 1 need a look · on your data')

    def test_failed_checks_need_a_look_and_say_so(self):
        run = make_run(self.root, failing=2, saved=False)
        self.record(run, 'connect')
        page = side_page.snapshot(run)
        self.assertEqual({q['status'] for q in page['queries']}, {'look'})
        self.assertEqual({q['why'] for q in page['queries']}, {'The table no longer holds that period.'})
        self.assertEqual({m['status'] for m in page['metrics']}, {'checked'}, 'every metric passed')

    def test_a_moved_run_folder_keeps_its_engine(self):
        import shutil
        run = make_run(self.root / 'before', saved=False)
        moved = self.root / 'after' / 'run'
        shutil.copytree(run, moved)
        shutil.rmtree(self.root / 'before')
        self.assertEqual(len(side_page.snapshot(moved)['metrics']), 2)

    def test_a_paused_run_says_so_instead_of_looking_busy(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps(dict(interaction, mode='paused')), encoding='utf-8')
        self.assertEqual(side_view.stopped(run), 'paused')
        self.assertIsNone(side_view.stopped(make_run(self.root / 'b', saved=False)), 'a running run carries no state')

    def test_a_survey_path_stays_off_an_own_data_page(self):
        run = make_run(self.root, saved=False)
        before_the_engine(run, 'inspect')
        at(run, 'inspect', 'running')
        progress = json.loads((run / 'progress.json').read_text(encoding='utf-8'))
        (run / 'progress.json').write_text(json.dumps(dict(progress, completed_items=1, total_items=3)), encoding='utf-8')
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        meta['scope'] = [{'name': 'secret/folder/report.pbit', 'status': 'pending_inspection'}]
        (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        self.assertNotIn('secret/folder', json.dumps(side_page.snapshot(run)), 'the count, never the path')

    def test_a_dashboard_only_choice_shows_no_history_work(self):
        run = make_run(self.root, chosen={'bi': True, 'history': False}, saved=False)
        before_the_engine(run, 'scope')
        self.record(run, 'connect')
        built = self.built(run)
        self.assertNotIn('group', built, 'past queries exist but were not chosen')
        self.assertIn('translate', built)

    def test_a_dashboard_only_run_shows_no_history_work(self):
        run = make_run(self.root, history=False)
        self.record(run, 'connect')
        built = self.built(run)
        self.assertNotIn('group', built)
        self.assertIn('translate', built)

    def test_a_history_only_run_shows_no_dashboard_work(self):
        run = make_run(self.root, dashboard=False)
        self.record(run, 'connect')
        built = self.built(run)
        self.assertNotIn('translate', built)
        self.assertIn('group', built)

    def test_an_engine_that_left_counts_out_still_draws(self):
        run = make_run(self.root, saved=False)
        manifest = run / 'engine' / 'manifest.json'
        manifest.write_text(json.dumps({'stages': [{'stage': 'a4_witness_bi', 'counts': {'witnessed': 2}}, {'stage': 'x2_ingest', 'counts': {}}]}))
        self.record(run, 'connect')
        self.assertTrue(side_page.snapshot(run)['build'])
        manifest.write_text('{"stages": "not a list"}')
        self.assertTrue(side_page.snapshot(run)['metrics'], 'a broken manifest leaves the page with less to show, not a crash')

    def test_an_engine_folder_named_on_the_command_is_remembered(self):
        run = make_run(self.root, engine='work/engine', saved=False)
        self.record(run, 'connect', engine_dir='work/engine')
        self.assertEqual(len(side_page.snapshot(run, 'work/engine')['metrics']), 2)

    def test_a_run_whose_task_list_cannot_be_read_never_says_it_stopped(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect')
        (run / 'interaction.json').write_text('{"mode": "nonsense"}', encoding='utf-8')
        self.record(run, 'sources', 'build')
        self.assertIsNone(side_view.stopped(run))
        self.assertNotIn('state', side_page.snapshot(run))


if __name__ == '__main__':
    unittest.main()
