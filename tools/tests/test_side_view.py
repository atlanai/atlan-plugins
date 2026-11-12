"""The side view: one document per finished step, read from the run's own files, for every kind of run."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, facts, make_run

sys.path.insert(0, str(SCRIPTS))
import side_view
import visuals

ORDER = ('connect', 'sources', 'build', 'context', 'saved', 'answer', 'layout', 'edit')


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

    def test_a_piece_of_work_ticks_as_soon_as_the_run_has_it(self):
        run = make_run(self.root)
        manifest = run / 'engine' / 'manifest.json'
        gates = manifest.read_text(encoding='utf-8')
        manifest.unlink()
        self.record(run, 'connect', 'sources')
        side_view.link(run, 'https://claude.ai/artifact/abc')
        self.assertNotEqual(self.nodes(self.doc(run), 'Build')["Keep queries about the dashboard's tables"].get('s'), 'done')
        manifest.write_text(gates, encoding='utf-8')
        out = side_view.progress(run)
        self.assertTrue(out['doc'].endswith('s03.json'), 'a new document, numbered after the last')
        build = self.step(self.doc(run), 'Build')
        self.assertEqual(build['status'], 'now', 'the step is still running')
        kept = self.nodes(self.doc(run), 'Build')["Keep queries about the dashboard's tables"]
        self.assertEqual((kept['s'], kept['n']), ('done', '3 → 2'), 'its piece of work is ticked with its count')
        self.assertEqual(side_view.progress(run), {}, 'nothing new finished: no new document')

    def test_an_engine_gate_names_the_update_for_its_setup_run(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / 'metrics-generation'))
        from modules.metrics import driver
        run = make_run(self.root)
        manifest = run / 'engine' / 'manifest.json'
        gates = manifest.read_text(encoding='utf-8')
        manifest.unlink()
        self.record(run, 'connect', 'sources')
        side_view.link(run, 'https://claude.ai/artifact/abc')
        manifest.write_text(gates, encoding='utf-8')
        self.assertIn('doc_id `s03`', driver._side_view(run / 'engine')['side_view'], 'each gate ticks on the page as it finishes')
        self.assertEqual(driver._side_view(run / 'engine'), {}, 'a gate that changed nothing names nothing')
        self.assertEqual(driver._side_view(self.root / 'elsewhere' / 'engine'), {}, 'an engine run outside a setup run is left alone')

    def test_build_ticks_its_pieces_in_the_order_they_happen(self):
        run = make_run(self.root)
        manifest = run / 'engine' / 'manifest.json'
        gates = manifest.read_text(encoding='utf-8')
        manifest.unlink()
        self.record(run, 'connect', 'sources')
        manifest.write_text(json.dumps({'stages': [{'stage': 'a3_translate', 'counts': {'units': 2, 'candidates': 2}},
                                                   {'stage': 'a3b_emit', 'counts': {'prompted': 2}}, {'stage': 'a3b_ingest', 'counts': {}}]}),
                            encoding='utf-8')
        side_view.progress(run)
        build = self.nodes(self.doc(run), 'Build')
        self.assertEqual(build["Translate the dashboard's formulas"].get('s'), 'done', 'translated before it was reviewed')
        self.assertEqual(build['Review each translation'].get('s'), 'done')
        manifest.write_text(gates, encoding='utf-8')

    def test_a_step_waits_for_every_item_before_ticking_a_run_of_checks(self):
        run = make_run(self.root)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: v for k, v in entries.items() if k.startswith('metric:Gross')}), encoding='utf-8')
        self.record(run, 'connect', 'sources', 'build')
        side_view.progress(run)
        ran = self.nodes(self.doc(run), 'Check')['Run each metric on the data']
        self.assertNotEqual(ran.get('s'), 'done', 'one of two metrics checked is not done yet')

    def test_the_save_ticks_the_workspace_as_soon_as_it_is_picked(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect', 'sources', 'build', 'context')
        (run / 'authorization.json').write_text(json.dumps({'workspace_id': 'w1', 'approved_plans': []}), encoding='utf-8')
        side_view.progress(run)
        publish = self.nodes(self.doc(run), 'Publish')
        self.assertEqual(publish['You pick a workspace']['s'], 'done')
        self.assertNotEqual(publish['Read every item back'].get('s'), 'done', 'nothing read back yet')

    def test_the_first_moment_writes_the_page_and_a_document(self):
        run = make_run(self.root, demo=True)
        out = self.record(run, 'connect', data=[facts()])['connect']
        self.assertEqual(set(out), {'page', 'local', 'doc'})
        page = Path(out['page']).read_text()
        self.assertIn('<title>Job profit agent for Fixit &amp; Co.</title>', page)
        self.assertNotIn('<!doctype', page.lower(), 'the artifact tool wraps the page in its own document')
        self.assertIn('--bg:light-dark(', page, 'the design tokens are filled in, light and dark')
        self.assertEqual(set(re.findall(r'https://([^/\'")]+)', page)), {'fonts.googleapis.com'}, 'the page loads nothing but its font')
        local = Path(out['local']).read_text()
        self.assertNotIn('https://', local, 'the local copy loads nothing from the network')
        doc = self.doc(run)
        self.assertEqual([s['status'] for s in doc['steps']], ['done', 'now', 'next', 'next', 'next', 'next'])
        self.assertTrue(doc['demo'])
        self.assertEqual(doc['n'], 1)

    def test_a_run_writes_one_document_per_step_and_no_more(self):
        run = make_run(self.root)
        out = self.record(run, *ORDER)
        self.assertEqual(len(list((run / 'view' / 'steps').glob('s*.json'))), 6)
        self.assertEqual([k for k, v in out.items() if v], list(side_view.FINISHES))
        self.assertEqual(self.record(run, 'sources', 'answer'), {'sources': {}, 'answer': {}}, 'a repeated moment writes nothing')

    def test_a_finished_step_shows_how_with_numbers_and_who_did_it(self):
        run = make_run(self.root)
        self.record(run, 'connect', 'sources', 'build')
        build = self.step(self.doc(run), 'Build')
        self.assertEqual((build['status'], build['fact']), ('done', '2 metrics'))
        nodes = self.nodes(self.doc(run), 'Build')
        self.assertEqual(nodes["Keep queries about the dashboard's tables"]['n'], '3 → 2')
        self.assertEqual(nodes['Merge numbers that share a rule']['n'], '3 → 2')
        self.assertIn('hold 3 numbers', nodes['Merge numbers that share a rule']['d'], 'the counts before and after add up')
        self.assertTrue(nodes['Review each translation']['ai'], "a judgement the model made is marked as Claude's")
        self.assertNotIn('ai', nodes["Translate the dashboard's formulas"], 'the engine translates; Claude only reviews')
        self.assertEqual(build['result'][0]['list'], [['Gross margin', 'the dashboard, 1 past query'], ['Rework cost', '1 past query']])
        self.assertEqual(self.step(self.doc(run), 'Check')['status'], 'now')

    def test_steps_ahead_say_how_they_work_without_numbers(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        running = self.step(self.doc(run), 'Read')
        self.assertEqual((running['status'], {n['s'] for n in running['how']}), ('now', {'done'}), 'the running step ticks what the run has')
        for step in self.doc(run)['steps'][2:]:
            self.assertTrue(step['how'], step['name'])
            self.assertFalse([n for n in step['how'] if 'n' in n], step['name'])
            self.assertEqual({n['s'] for n in step['how']}, {'next'})

    def test_a_finished_step_keeps_what_it_showed(self):
        run = make_run(self.root)
        self.record(run, 'connect', 'sources', 'build', 'context')
        checks = run / 'context' / 'candidate-checks.json'
        changed = json.loads(checks.read_text(encoding='utf-8'))
        changed['metric:Gross Margin']['composition']['rows'] = [[1.0]]
        checks.write_text(json.dumps(changed))
        self.record(run, 'saved')
        values = dict((r[0], r[1]) for r in self.step(self.doc(run), 'Check')['result'][0]['list'])
        self.assertEqual(values['Gross margin'], '£118,681', 'a later edit does not rewrite what the check found')

    def test_a_dashboard_only_run_shows_no_history_work(self):
        run = make_run(self.root, history=False)
        self.record(run, 'connect', 'sources', 'build')
        titles = [n['t'] for s in self.doc(run)['steps'] for n in s['how']]
        self.assertNotIn('Group repeats into questions', titles)
        self.assertNotIn('Count the past queries', titles)
        self.assertIn("Translate the dashboard's formulas", titles)

    def test_a_history_only_run_shows_no_dashboard_work(self):
        run = make_run(self.root, dashboard=False)
        self.record(run, 'connect', 'sources', 'build')
        titles = [n['t'] for s in self.doc(run)['steps'] for n in s['how']]
        self.assertNotIn("Translate the dashboard's formulas", titles)
        self.assertNotIn('Read the dashboard', titles)
        self.assertIn('Keep queries about the chosen tables', titles)

    def test_a_data_only_run_reads_its_tables(self):
        run = make_run(self.root, dashboard=False, history=False)
        self.record(run, 'connect', 'sources')
        self.assertEqual(self.step(self.doc(run), 'Read')['fact'], '2 tables')

    def test_an_engine_that_left_counts_out_still_draws(self):
        run = make_run(self.root)
        manifest = run / 'engine' / 'manifest.json'
        manifest.write_text(json.dumps({'stages': [{'stage': 'a4_witness_bi', 'counts': {'witnessed': 2}}, {'stage': 'x2_ingest', 'counts': {}}]}))
        self.record(run, 'connect', 'sources', 'build')
        self.assertEqual(self.nodes(self.doc(run), 'Build')["Translate the dashboard's formulas"]['n'], '0 of 2')
        manifest.write_text('{"stages": "not a list"}')
        self.assertTrue(self.record(run, 'context')['context'], 'a broken manifest leaves the step with less to show, not a crash')

    def test_an_engine_folder_named_on_the_command_is_remembered(self):
        run = make_run(self.root, engine='work/engine')
        self.record(run, 'connect', 'sources')
        self.record(run, 'build', engine_dir='work/engine')
        self.assertEqual(self.step(self.doc(run), 'Build')['fact'], '2 metrics')

    def test_a_save_with_no_read_back_is_never_shown_as_done(self):
        run = make_run(self.root, saved=False)
        self.record(run, 'connect', 'sources', 'build', 'context', 'answer')
        publish = self.step(self.doc(run), 'Publish')
        self.assertEqual((publish['status'], publish['fact']), ('unconfirmed', 'Not confirmed live'))
        self.assertIn('read back', publish['note'])
        make_run(self.root, saved=True)
        self.assertEqual(self.step(side_view.model(run), 'Publish')['status'], 'done', 'a read-back that arrives later still counts')

    def test_a_context_kept_local_marks_publish_as_kept(self):
        run = make_run(self.root, local=True)
        self.record(run, *ORDER[:5])
        publish = self.step(self.doc(run), 'Publish')
        self.assertEqual((publish['status'], publish['fact']), ('local', 'Kept on this machine'))
        self.assertEqual([n['t'] for n in publish['how']], ['You keep the context on this machine'])

    def test_each_finished_step_reads_a_short_fact(self):
        run = make_run(self.root)
        self.record(run, *ORDER[:6])
        self.assertEqual({s['name']: s.get('fact') for s in self.doc(run)['steps']},
                         {'Connect': 'Power BI dashboard, 2 data files, query history', 'Read': '3 queries, 2 tables', 'Build': '2 metrics', 'Check': '4 ran, 1 to confirm',
                          'Publish': '4 live in Atlan', 'Answer': '-£6,360'})

    def test_after_the_answer_the_view_leads_with_it(self):
        run = make_run(self.root)
        self.record(run, *ORDER[:6])
        doc = self.doc(run)
        self.assertEqual((doc['current'], doc['answer']['headline']), (6, 'Turbine base: -£6,360'))
        self.assertTrue(self.nodes(doc, 'Answer')['Write the answer']['ai'])

    def test_a_damaged_state_file_never_reuses_a_document_number(self):
        run = make_run(self.root)
        self.record(run, 'connect', 'sources', 'build')
        (run / 'view' / 'state.json').write_text('{not json', encoding='utf-8')
        self.record(run, 'context')
        self.assertEqual(self.doc(run)['n'], 4, 'the page shows the newest number, so a new start continues from it')

    def test_the_key_names_only_the_parts_on_the_page(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        parts = self.doc(run)['parts']
        self.assertIn('Claude', parts)
        self.assertNotIn('Demo guide', parts, 'not a demo')

    def test_own_data_is_named_from_its_folder_and_purpose(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        doc = self.doc(run)
        self.assertEqual((doc['title'], doc['tagline'], doc['demo']), ('source analytics', 'See which jobs lose margin and why', False))
        self.assertIn('your', self.step(doc, 'Read')['doing'])

    def test_the_local_page_loads_its_data_file_by_itself(self):
        run = make_run(self.root)
        self.record(run, 'connect', 'sources')
        page = (run / 'view' / 'progress.html').read_text()
        self.assertTrue(page.lower().startswith('<!doctype html>'))
        self.assertLess(page.index('SIDE_VIEW_FILE'), page.index('<body>'), 'the data file is named before the page script runs')
        data = (run / 'view' / 'progress-data.js').read_text()
        self.assertEqual(json.loads(data[data.index('{'):data.rindex('}') + 1])['n'], 2)

    def test_a_published_address_is_named_in_every_later_update(self):
        run = make_run(self.root)
        self.record(run, 'connect')
        side_view.link(run, 'https://claude.ai/artifact/abc')
        self.assertEqual(self.record(run, 'sources')['sources']['url'], 'https://claude.ai/artifact/abc')
        with self.assertRaises(ValueError):
            side_view.link(run, 'https://example.com/page')


if __name__ == '__main__':
    unittest.main()
