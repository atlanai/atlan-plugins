"""The dashboard branch's side page (A2) and chat fixes: checkpoints, drafts, tiles that could not be built, kept-back
metrics, the person's choice about the warehouse checks, its save question, and refusals that change nothing."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, make_run

sys.path.insert(0, str(SCRIPTS))
import drafts
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
        self.assertEqual(snap['cant'], [{'tile': 'Deal list', 'page': 'Detail', 'why': 'The translation came out malformed'}])
        self.assertEqual([d['name'] for d in snap['drafts']], ['Win rate'], 'every draft the build found is listed')
        self.assertTrue(all(m.get('stage') in ('draft', 'merged', 'final', 'kept') for m in snap['metrics']))

    def test_a_kept_back_metric_is_matched_by_its_tiles_when_its_name_differs(self):
        snap = side_page.snapshot(self.run)
        metric = next(m for m in snap['metrics'] if m.get('sources'))
        write(self.run / 'context' / 'publication-holds.json', {'metrics': [
            {'name': 'A different name', 'reason': 'source_query_held', 'query_ids': [metric['sources'][0]['id']]}]})
        kept = next(m for m in side_page.snapshot(self.run)['metrics'] if m['name'] == metric['name'])
        self.assertEqual(kept['stage'], 'kept')
        self.assertIn('kept back', kept['kept_reason'])

    def test_tiles_by_page_reports_the_real_translation_for_the_chosen_pages(self):
        got = side_page.tiles_by_page(RunFacts(self.run), ['Detail'])
        self.assertEqual((got['tiles'], got['buildable']), (1, 0))
        self.assertEqual(got['pages'][0]['cannot'][0]['tile'], 'Deal list')
        manifest = json.loads((self.run / 'engine' / 'manifest.json').read_text(encoding='utf-8'))
        write(self.run / 'engine' / 'manifest.json', {'stages': [s for s in manifest['stages'] if s['stage'] != 'a3_translate']})
        self.assertEqual(side_page.tiles_by_page(RunFacts(self.run))['status'], 'not_translated', 'never a guess')

    def test_the_dashboard_save_question_waits_for_checks_or_the_persons_skip(self):
        self.interaction(flow='dashboard', report='Sales: Overview')
        (self.run / 'context' / 'candidate-checks.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            questions.save(self.run, widget=True)
        self.interaction(checks='skip')
        text = questions.save(self.run, widget=True)['question']
        self.assertTrue(text.startswith('Built '), text)
        self.assertIn('you chose to skip the checks', text)
        self.assertIn("1 tile couldn't be built", text)
        self.assertNotIn('CONTEXT BUILT', text)
        self.assertNotIn('As you asked', text)

    def test_a_refused_command_leaves_progress_as_it_was(self):
        # Live, a refused check marked the stage failed and the side page said the build had stopped.
        write(self.run / 'progress.json', {'stage': 'check', 'status': 'complete'})
        request = self.run / 'req.json'
        write(request, {'name': 'no-such-query', 'parameters': {}})
        out = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run), 'check-query', str(request)],
                             capture_output=True, text=True)
        self.assertIn('"status": "blocked"', out.stdout)
        self.assertEqual(json.loads((self.run / 'progress.json').read_text(encoding='utf-8'))['status'], 'complete')

    def test_the_dashboard_flow_s_watch_gives_the_same_chips_as_every_flow(self):
        # Its chat carried checkpoints only, with a "Still writing..." line after two quiet minutes. The person chose our
        # chat for it (2026-11-18): so-far chips while a step runs, and never "still".
        out = io.StringIO()
        engine = self.run / 'engine'
        (engine / 'answers' / 'x3').mkdir(parents=True, exist_ok=True)
        import side_view
        import threading
        import time
        with mock.patch.object(drafts, '_under_way', return_value=True), mock.patch.object(side_view, 'due', return_value=[]):
            watch = threading.Thread(target=lambda: drafts.follow(self.run, 'engine', seconds=1.0, idle=10, interval=0.05,
                                                                  so_far_every=0, every_chip=True, out=out))
            watch.start()
            time.sleep(0.2)
            (engine / 'answers' / 'x3' / '001.json').write_text(json.dumps({'metrics': [{'name': 'Gross margin'}]}))
            watch.join(5)
        self.assertIn('visual progress', out.getvalue())
        self.assertNotIn('Still', out.getvalue())
        guide = (SCRIPTS.parent / 'references' / 'setup' / 'dashboard-flow.md').read_text(encoding='utf-8')
        for gone in ('--heartbeat', 'Never a running count, a so-far chip', 'final, kept back', 'Still writing'):
            self.assertNotIn(gone, guide)
        self.assertIn('--every-chip', guide)


if __name__ == '__main__':
    unittest.main()
