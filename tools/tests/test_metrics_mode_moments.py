"""The engine hands build moments directly to a metrics-only chat."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from analytics_run import SCRIPTS, make_run

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / 'metrics-generation'))
import eval_runs
import side_page
import side_view
from modules.metrics import driver


class MetricsModeMoments(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'sales'
        self.engine = self.run / 'engine'
        self.engine.mkdir(parents=True)
        (self.run / 'state.json').write_text(json.dumps({'mode': 'metrics', 'phase': 'context'}))
        self.enterContext(patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}))

    def engine_outputs(self):
        source = make_run(self.root / 'fixture') / 'engine'
        shutil.copytree(source, self.engine, dirs_exist_ok=True)
        cards = json.loads((source / 'metric_cards.json').read_text())['cards']
        (self.engine / 'metric_cards.json').write_text(json.dumps({'metrics': cards}))
        queries = [{'mode': 'explain', 'result': 'pass'}, {'mode': 'parse', 'result': 'parse_ok'},
                   {'mode': 'execute', 'result': 'pass'}]
        (self.engine / 'verified_queries.json').write_text(json.dumps({'objects': [{'witness': w} for w in queries]}))

    def command(self, command, *args, status='paused', code=3):
        out = io.StringIO()
        with patch.object(driver, 'invoke', return_value=({'status': status}, code)), \
                redirect_stdout(out), redirect_stderr(io.StringIO()):
            actual = driver.main(['--runtime', str(self.root / 'runtime'), command, '--run', str(self.engine), *args])
        self.assertEqual(actual, code)
        return json.loads(out.getvalue())

    def test_advance_hands_over_the_draft_chip_once(self):
        (self.engine / 'manifest.json').write_text(json.dumps({'stages': [
            {'stage': 'x1_ingest', 'counts': {'proposals': 2}}]}))
        result = self.command('advance', '--model', 'm')
        self.assertIn('show_first', result)
        self.assertIn('**2 metrics found**', result['show_first'])
        self.assertEqual(result['show_first'].count('side view: '), 1)
        self.assertNotIn('drafts --wait', result['visual'] + result['show_first'])
        self.assertNotIn('show_first', self.command('advance', '--model', 'm'))

    def test_prepare_recovers_every_finished_step_and_advance_closes_the_build_in_the_terminal(self):
        self.engine_outputs()
        page = side_page.snapshot(self.run)
        self.assertEqual(len(page['metrics']), 2)
        self.assertTrue(page['queries'])
        with patch.object(side_page, 'open_page', return_value={'url': 'http://127.0.0.1/example/'}) as opened:
            result = self.command('prepare', '--config', 'config.json', status='prepared', code=0)
        opened.assert_called_once_with(self.run)
        steps = ['connect', 'group', 'tiles', 'described', 'drafted', 'metrics', 'rules', 'links']
        self.assertEqual(side_view.seen(self.run), steps)
        self.assertEqual(result['show_first'].count('side view: '), len(steps))
        self.assertIn('saved queries written**', result['show_first'])
        self.assertNotIn('drafts --wait', result['show_first'] + result['visual'])
        self.assertNotIn('show_first', self.command('advance', '--model', 'm'))

        (self.engine / 'module.json').write_text(json.dumps({'status': 'complete'}))
        page = side_page.snapshot(self.run)
        self.assertEqual(page['title'], 'Your metrics')
        self.assertTrue(all(step['state'] == 'done' for step in page['build']))
        shown = self.command('advance', '--model', 'm', status='complete', code=0)['show_first']
        self.assertLess(shown.index('Metrics built'), shown.index('Build complete'))
        self.assertIn('1 of 3 saved queries ran on the data', shown)
        self.assertEqual(shown.count('side view: '), 2)
        self.assertNotIn('PROJECT', shown)
        self.assertNotIn('on the side page', shown)
        self.assertEqual(side_view.seen(self.run), steps + ['build', 'context'])
        self.assertNotIn('show_first', self.command('export', '--context', 'context', status='exported', code=0))
        self.assertFalse((self.run / 'run.json').exists())
        self.assertEqual(eval_runs.built_runs(self.root), [])

    def test_export_delivers_the_whole_build_as_widgets_on_a_first_visit(self):
        self.engine_outputs()
        (self.engine / 'module.json').write_text(json.dumps({'status': 'complete'}))
        side_view.mark_waiting(self.run)
        with patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}), \
                patch.object(side_page, 'open_page', return_value={'url': 'http://127.0.0.1/example/'}):
            result = self.command('export', '--context', 'context', status='exported', code=0)
        shown = result['show_first']
        self.assertIn('<details', shown)
        self.assertIn('Metrics built', shown)
        self.assertIn('Build complete', shown)
        self.assertNotIn('PROJECT', shown + result['visual'])
        self.assertEqual(shown.count('side view: '), len(side_view.seen(self.run)))
        self.assertEqual(shown.count('to `show_widget`'), len(side_view.seen(self.run)) - 1)

    def test_a_checked_export_shows_its_context_chip_and_keeps_its_existing_discovery(self):
        self.engine_outputs()
        shutil.copytree(self.root / 'fixture' / 'run' / 'context', self.run / 'context')
        outputs = {'metric_cards.json': 'a' * 64}
        (self.engine / 'module.json').write_text(json.dumps({'status': 'complete', 'outputs': outputs}))
        (self.run / 'context' / 'metrics-source.json').write_text(json.dumps({'outputs': outputs}))
        self.assertEqual(eval_runs.built_runs(self.root), [self.run])
        with patch.object(side_page, 'open_page', return_value={'error': 'unavailable'}):
            shown = self.command('export', '--context', str(self.run / 'context'), status='exported', code=0)['show_first']
        self.assertIn('Every metric works', shown)
        self.assertIn('Metrics built', shown)
        self.assertEqual(eval_runs.built_runs(self.root), [self.run])
        self.assertFalse((self.run / 'run.json').exists())

    def test_new_engine_has_its_own_steps_and_never_opens_another_engines_page(self):
        self.engine_outputs()
        self.engine = self.run / 'second-build'
        self.engine.mkdir()
        self.engine_outputs()
        with patch.object(side_page, 'open_page') as opened:
            shown = self.command('advance', '--model', 'm')['show_first']
        opened.assert_not_called()
        self.assertIn('saved queries written', shown)
        self.assertIn('described', side_view.seen(self.run, self.engine))
        self.assertNotIn('described', side_view.seen(self.run, self.run / 'engine'))

    def test_other_modes_failed_steps_and_status_leave_the_view_alone(self):
        (self.engine / 'manifest.json').write_text(json.dumps({'stages': [
            {'stage': 'x1_ingest', 'counts': {'proposals': 2}}]}))
        self.assertNotIn('show_first', self.command('status', status='paused', code=0))
        self.assertNotIn('show_first', self.command('advance', '--model', 'm', status='blocked', code=2))
        for state in ({'mode': 'eval'}, [], None):
            with self.subTest(state=state):
                (self.run / 'state.json').write_text(json.dumps(state))
                self.assertNotIn('visual', self.command('advance', '--model', 'm'))
        self.assertFalse((self.run / 'view').exists())

    def test_a_setup_run_still_defers_to_its_foreground_wait(self):
        (self.run / 'run.json').write_text('{}')
        side_view.mark_waiting(self.run)
        result = self.command('advance', '--model', 'm')
        self.assertIn('PROJECT drafts --wait', result['visual'])
        self.assertNotIn('show_first', result)
