"""The dashboard branch's side page (A2) and chat fixes: checkpoints, drafts, tiles that could not be built, kept-back
metrics, the person's choice about the warehouse checks, its save question, and refusals that change nothing."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, make_run

sys.path.insert(0, str(SCRIPTS))
import questions
import side_page
from run_facts import RunFacts

PROJECT = SCRIPTS / 'project.py'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class DashboardBranch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.tmp.name).resolve())
        engine = self.run / 'engine'
        write(engine / 'question_units.json', {'units': [
            {'unit_id': 'powerbi::Sales::Overview::a', 'title': 'Pipeline by stage'},
            {'unit_id': 'powerbi::Sales::Overview::b', 'title': 'Win rate'},
            {'unit_id': 'powerbi::Sales::Detail::c', 'title': 'Deal list'}]})
        write(engine / 'a3_compile_report.json', {'refused': [
            {'unit_id': 'powerbi::Sales::Detail::c', 'reason': 'compiler emitted unparseable SQL: ParseError'}]})
        write(engine / 'metric_proposals.json', {'proposals': [{'proposal_id': 'q-win#0', 'name': 'Win rate', 'vq_id': 'q-win'}]})
        manifest = json.loads((engine / 'manifest.json').read_text(encoding='utf-8'))
        manifest['stages'] = [{'stage': 'a3_translate', 'counts': {'units': 3, 'candidates': 2}}] + manifest.get('stages', [])
        write(engine / 'manifest.json', manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def interaction(self, **extra):
        path = self.run / 'interaction.json'
        write(path, dict(json.loads(path.read_text(encoding='utf-8')), **extra))

    def test_the_page_carries_checkpoints_drafts_and_the_tiles_that_could_not_be_built(self):
        snap = side_page.snapshot(self.run)
        self.assertTrue(snap['build'] and all(s['state'] in ('done', 'now', 'next') for s in snap['build']))
        self.assertTrue(any(s['id'] == 'translate' and s.get('result') == '2 of 3 tiles' for s in snap['build']))
        self.assertEqual(snap['cant'], [{'tile': 'Deal list', 'page': 'Detail', 'why': "It couldn't be read correctly"}])
        self.assertEqual([d['name'] for d in snap['drafts']], ['Win rate'], 'every draft the build found is listed')
        self.assertTrue(all(m.get('stage') in ('draft', 'merged', 'final', 'kept') for m in snap['metrics']))

    def test_a_kept_back_metric_is_matched_by_its_tiles_when_its_name_differs(self):
        snap = side_page.snapshot(self.run)
        metric = next(m for m in snap['metrics'] if m.get('sources'))
        write(self.run / 'context' / 'publication-holds.json', {'metrics': [
            {'name': 'A different name', 'reason': 'source_query_held', 'query_ids': [metric['sources'][0]['id']]}]})
        kept = next(m for m in side_page.snapshot(self.run)['metrics'] if m['name'] == metric['name'])
        self.assertEqual(kept['stage'], 'kept')
        self.assertIn('held back', kept['kept_reason'])

    def hold(self):
        """The save kept back one metric whose number did not match its tile, and one query missing its tile's filter."""
        metric = next(m for m in side_page.snapshot(self.run)['metrics'] if m.get('sources'))
        write(self.run / 'context' / 'publication-holds.json', {
            'metrics': [{'source_id': 's1', 'name': metric['name'], 'status': 'held', 'reason': 'dashboard_parity_failed'}],
            'queries': [{'source_id': 's2', 'name': 'margin-by-job', 'status': 'held', 'reason': 'required_filter_missing',
                         'query_id': 'bi-q2', 'sql_sha256': 'a' * 64}]})
        return metric

    def test_every_reason_the_save_writes_reads_in_plain_words(self):
        # A tester, 2026-11-19: "It said it held back X metrics and queries, but I couldn't tell why." Five of the seven
        # reasons the save writes fell back to "It needs a look before it can be saved."
        for code in ('compiler_review_wrong', 'required_filter_missing', 'source_query_held', 'dashboard_parity_failed',
                     'required_metric_held', 'source_of_held_metric', 'privacy_review'):
            self.assertIn(code, side_page.KEPT)
            self.assertIn(code, side_page.KEPT_SHORT)

    def test_a_kept_back_query_shows_on_the_page_with_why(self):
        self.hold()
        held = next(q for q in side_page.snapshot(self.run)['queries'] if q['id'] == 'bi-q2')
        self.assertEqual(held['status'], 'kept')
        self.assertIn('filter', held['kept_reason'])

    def test_the_save_and_ready_questions_name_each_reason_with_its_count(self):
        self.hold()
        self.interaction(flow='dashboard', report='Sales: Overview')
        save = questions.save(self.run, widget=True)['question']
        self.assertNotIn('Most often', save)
        for text in (save, ' '.join(questions.ready(self.run, widget=True)['question'].split())):
            self.assertIn("1 didn't match the dashboard's number", text)
            self.assertIn('1 left out a filter its tile always applies', text)

    def test_the_ready_question_offers_the_person_s_own_question(self):
        # A tester, 2026-11-19: "there was no way to ask a question unless I picked Other."
        # The product owner, 2026-11-21: an "Ask your own question" option led to a turn with nothing to pick. The
        # host's free-text entry takes it, and the text says so.
        asked = questions.ready(self.run, widget=True)
        self.assertNotIn('Ask your own question', [o['label'] for o in asked['options']])
        self.assertTrue(asked['question'].endswith('Pick one, or type your own in Other.'))
        self.assertLessEqual(len(asked['options']), 4)

    def test_tiles_by_page_reports_the_real_translation_for_the_chosen_pages(self):
        got = side_page.tiles_by_page(RunFacts(self.run), ['Detail'])
        self.assertEqual((got['tiles'], got['buildable']), (1, 0))
        self.assertEqual(got['pages'][0]['cannot'][0]['tile'], 'Deal list')
        manifest = json.loads((self.run / 'engine' / 'manifest.json').read_text(encoding='utf-8'))
        write(self.run / 'engine' / 'manifest.json', {'stages': [s for s in manifest['stages'] if s['stage'] != 'a3_translate']})
        self.assertEqual(side_page.tiles_by_page(RunFacts(self.run))['status'], 'not_translated', 'never a guess')

    def test_the_dashboard_save_question_waits_for_checks_or_the_persons_skip(self):
        self.interaction(flow='dashboard', report='Sales: Overview')
        structural = json.loads((self.run / 'context' / 'candidate-checks.json').read_text(encoding='utf-8'))
        (self.run / 'context' / 'candidate-checks.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            questions.save(self.run, widget=True)
        self.interaction(checks='skip')
        # d3, 2026-11-19: asked before the structural pass, the question said "Built 0 metrics" for a build of 4.
        with self.assertRaises(ValueError) as said:
            questions.save(self.run, widget=True)
        self.assertIn('`PROJECT check`', str(said.exception))
        write(self.run / 'context' / 'candidate-checks.json', {k: {'status': 'schema_checked', 'errors': []} for k in structural})
        text = questions.save(self.run, widget=True)['question']
        self.assertTrue(text.startswith('Not validated yet on your data: you skipped the tests.'), text)
        self.assertNotIn('answers from', text, 'with widgets the build card says what was built')
        self.assertIn('1 tile stayed out', text)
        self.assertNotIn('CONTEXT BUILT', text)
        self.assertNotIn('As you asked', text)
        asked = questions.save(self.run)
        self.assertRegex(asked['question'], r'^Your .* is built\.\nIt answers from [1-9].* built from [1-9] tiles? on the ',
                         asked['question'])
        self.assertIn('you skipped the tests', asked['question'])
        self.assertEqual(asked['shown'], ['build'])
        for block in '█░▓':
            self.assertNotIn(block, asked['question'])
        self.assertFalse([x for x in asked['question'].splitlines() if x.startswith('Next:')])

    def skip_checks(self):
        """The person chose to skip the checks: only the structural pass ran, and the flow moved on to the save."""
        self.interaction(flow='dashboard', report='Sales: Overview', checks='skip')
        checks = json.loads((self.run / 'context' / 'candidate-checks.json').read_text(encoding='utf-8'))
        write(self.run / 'context' / 'candidate-checks.json', {k: {'status': 'schema_checked', 'errors': []} for k in checks})
        write(self.run / 'progress.json', {'stage': 'ready', 'status': 'complete'})

    def test_a_skipped_check_leaves_each_item_untested_never_needing_a_look(self):
        # Live, every metric and query read "Needs a look: Its check never ran." after the person chose to skip.
        self.skip_checks()
        self.assertFalse(RunFacts(self.run).checks_over)
        snap = side_page.snapshot(self.run)
        defined = [m for m in snap['metrics'] if m.get('stage') == 'final']
        self.assertTrue(defined and snap['queries'])
        for item in defined + snap['queries']:
            self.assertEqual(item['status'], 'untested', item['name'])
            self.assertNotIn('why', item)

    def test_the_ready_question_lists_nothing_as_needing_a_look_and_its_recap_says_the_checks_were_skipped(self):
        self.skip_checks()
        text = questions.ready(self.run, widget=True)['question']
        self.assertNotIn('Needs your review', text)
        self.assertNotIn('checked on', text)
        self.assertNotIn('tested', text, 'the save question just said so')
        recap = questions.ready(self.run, widget=True, recap=True)['question']
        self.assertNotIn('Needs your review', recap)
        self.assertIn("They aren't tested yet: you skipped the tests.", recap)

    def test_the_closing_message_says_not_validated_without_giving_the_reason_again(self):
        # The save question and the ready recap had each said "you skipped the tests"; a third time read as a reproach.
        self.skip_checks()
        closing = questions.close(self.run)['text']
        self.assertIn('not validated yet', closing)
        self.assertNotIn('you skipped the tests', closing)

    def test_a_refused_command_leaves_progress_as_it_was(self):
        # Live, a refused check marked the stage failed and the side page said the build had stopped.
        write(self.run / 'progress.json', {'stage': 'check', 'status': 'complete'})
        request = self.run / 'req.json'
        write(request, {'name': 'no-such-query', 'parameters': {}})
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run), 'check-query', str(request)],
                             capture_output=True, text=True)
        self.assertIn('"status": "blocked"', out.stdout)
        self.assertEqual(json.loads((self.run / 'progress.json').read_text(encoding='utf-8'))['status'], 'complete')

    def test_the_dashboard_flow_follows_the_build_as_every_flow_does(self):
        # Its chat carried checkpoints only, with a "Still writing..." line after two quiet minutes. The person chose our
        # chat for it (2026-11-18): the one wait every flow runs, and never "still".
        guide = (SCRIPTS.parent / 'references' / 'setup' / 'dashboard-flow.md').read_text(encoding='utf-8')
        for gone in ('--heartbeat', 'Still writing', 'final, kept back', '--follow', '--every-chip', 'so-far chip', 'Monitor'):
            self.assertNotIn(gone, guide)
        self.assertIn('PROJECT drafts --wait', guide)


if __name__ == '__main__':
    unittest.main()
