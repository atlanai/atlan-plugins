"""Each step's chip says what that step produced, with its own mark: never how far the run has got (the side page shows
that), never a count out of a total the person never saw."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from analytics_run import ROOT, SCRIPTS, at, facts, make_run, stop_pages

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics/skills/metrics-generation'))
import side_view  # noqa: E402
import visuals  # noqa: E402
from modules.metrics import driver  # noqa: E402

BUILD_STEPS = ('group', 'tiles', 'described', 'metrics', 'rules', 'links')


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


def drop_stages(run, *names):
    manifest = run / 'engine' / 'manifest.json'
    stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
    write(manifest, {'stages': [s for s in stages if s['stage'] not in names]})


class Chips(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root)
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [
            {'name': 'Gross Margin', 'description': 'What the jobs kept: contract value less labour and materials, over jobs '
                                                     'that are not cancelled. The dashboard headline.'}]})
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Gross Margin', 'to': 'Rework Cost', 'relation': 'derivation', 'role': 'component'}]})

    def tearDown(self):
        self.tmp.cleanup()

    def show(self, kind, run=None, widget=False, data=()):
        return visuals.render(kind, visuals.build_spec(kind, run or self.run, list(data)), widget)

    def test_each_build_step_says_what_it_produced(self):
        self.assertEqual(self.show('group'), '» **2 different questions** · from 2 past queries')
        self.assertEqual(self.show('tiles'), '▦ **2 dashboard tiles** · Site, each now a question')
        # The question past queries asked most, in the build's own plain words.
        self.assertEqual(self.show('described'), '❝ “What was the gross margin across jobs?” · +3 more in plain words')
        self.assertEqual(self.show('metrics'), '◆ **2 metrics** · Gross margin, Rework cost')
        self.assertEqual(self.show('rules'), '= **Gross margin** · contract value less labour and materials, over jobs that…',
                         'fitted to one line')
        self.assertEqual(self.show('links'), '⋈ **1 link** · Gross margin uses Rework cost')

    def test_a_ratio_and_a_narrower_metric_read_as_what_they_are(self):
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Rework Cost', 'to': 'Labour Cost', 'relation': 'restriction'},
            {'from': 'Waste Rate', 'to': 'Waste', 'relation': 'derivation', 'role': 'numerator'},
            {'from': 'Waste Rate', 'to': 'Delivered Cost', 'relation': 'derivation', 'role': 'denominator'}]})
        self.assertEqual(self.show('links'), '⋈ **3 links** · Waste rate = Waste ÷ Delivered cost')
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Rework Cost', 'to': 'Labour Cost', 'relation': 'restriction'}]})
        self.assertEqual(self.show('links'), '⋈ **1 link** · Rework cost is part of Labour cost')

    def test_the_rules_chip_reads_the_rules_as_soon_as_they_are_written(self):
        # The engine writes its cards file with the links; right after the rules it holds them in the next gate's prompt.
        (self.run / 'engine' / 'metric_cards.json').unlink()
        write(self.run / 'engine' / 'x4_prompt.json', {'all_metrics': [
            {'metric': 'Gross Margin', 'description': 'What the jobs kept: contract value less labour.'}]})
        self.assertEqual(self.show('rules'), '= **Gross margin** · contract value less labour')

    def test_no_chip_before_its_step_has_finished(self):
        drop_stages(self.run, 'h1_shapes')
        self.assertEqual(self.show('group'), '')
        (self.run / 'engine' / 'verified_queries.bi.json').unlink()
        self.assertEqual(self.show('tiles'), '')
        drop_stages(self.run, 'm2_ingest', 'x2_ingest', 'x3_ingest', 'x4_ingest')
        for kind in ('described', 'metrics', 'rules', 'links'):
            self.assertEqual(self.show(kind), '', kind)

    def test_a_step_the_person_did_not_choose_shows_nothing(self):
        history_only = make_run(self.root / 'h', dashboard=False)
        self.assertEqual(self.show('tiles', history_only), '')
        dashboard_only = make_run(self.root / 'd', history=False)
        self.assertEqual(self.show('group', dashboard_only), '')

    def test_the_chips_that_were_already_there_say_their_outcome(self):
        self.assertEqual(self.show('sources'), '▤ **129 rows** of your data · 2 tables, 3 past queries, Power BI dashboard')
        self.assertEqual(self.show('context'), '✓ **Every metric and query ran** · on your data · Gross margin £118,681')
        failing = make_run(self.root / 'f', failing=1)
        self.assertEqual(self.show('context', failing), '△ **3 ran cleanly** · 1 need a look · on your data')
        local = make_run(self.root / 'l', saved=False, local=True)
        self.assertEqual(self.show('saved', local), '⌂ **Kept on this machine** · nothing sent to Atlan')
        self.assertEqual(self.show('layout'), '▭ **2 tiles** laid out · 1 more metric below · Gross margin, Rework cost')
        demo = make_run(self.root / 'demo', demo=True)
        self.assertEqual(self.show('context', demo, data=[facts()]), "✓ **Every metric and query ran** · on Fixit's data · Gross margin £118,681")

    def test_the_dashboard_built_names_its_file(self):
        # The agent writes the file's name with `visual built --input -`; own data takes nothing from outside the run.
        write(self.run / 'built.json', {'file': 'job-margin-dashboard.html'})
        self.assertEqual(self.show('built'),
                         '▣ **job-margin-dashboard.html** · 2 tiles · opens in your browser')
        self.assertEqual(visuals.check_data('built', {}), 'built.json needs `file`.')

    def test_no_chip_shows_progress(self):
        kinds = BUILD_STEPS + ('found', 'sources', 'context', 'layout')
        for kind in kinds:
            text = self.show(kind)
            self.assertFalse(re.search(r'\d+/\d+', text), (kind, text))
            self.assertFalse(set(visuals.RINGS) & set(text), (kind, text))
            self.assertNotIn('class="rg"', self.show(kind, widget=True), kind)

    def test_each_chip_has_its_own_mark(self):
        kinds = BUILD_STEPS + ('sources', 'context', 'layout')
        marks = {self.show(kind).split(' ', 1)[0] for kind in kinds}
        self.assertEqual(len(marks), len(kinds), 'no two steps share a mark')
        widgets = [self.show(kind, widget=True) for kind in kinds]
        for kind, html in zip(kinds, widgets):
            self.assertIn('class="a oc', html, kind)
            self.assertIn('role="note"', html, kind)
        self.assertEqual(len({re.search(r'<i class="m ([\w-]+)', w).group(1) for w in widgets}), len(kinds))


    def test_the_tiles_chip_names_the_dashboard_page_never_the_side_page(self):
        # Every run with its side page open wrote the page's own address where the dashboard page belonged.
        write(self.run / 'view' / 'server.json', {'url': 'http://127.0.0.1:8123/k/', 'pid': 0})
        self.assertEqual(self.show('tiles'), '▦ **2 dashboard tiles** · Site, each now a question')

    def test_a_step_the_engine_refused_has_not_finished(self):
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        write(manifest, {'stages': stages + [{'stage': 'x4_ingest', 'counts': {}, 'verdict': 'blocked'}]})
        self.assertEqual(self.show('links'), '', 'its latest pass was refused')

    def test_a_rule_keeps_its_abbreviations(self):
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [
            {'name': 'Gross Margin', 'description': 'Jobs that made money (excl. cancelled jobs, e.g. quotes). Not the rest.'}]})
        self.assertEqual(self.show('rules'), '= **Gross margin** · Jobs that made money (excl. cancelled jobs, e.g. quotes)')

    def test_every_chip_fits_one_terminal_line(self):
        long = 'What share of the delivered material value ended up wasted on the jobs that finished late this year?'
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {'h-1': {'question': long}}})
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [{'name': 'Gross Margin', 'description': 'Contract value ' + 'less a cost ' * 12 + '.'}]})
        write(self.run / 'built.json', {'file': 'a-very-long-name-for-the-dashboard-of-job-margins-and-rework-costs-2026.html'})
        for kind in ('described', 'rules', 'built', 'metrics', 'sources', 'layout'):
            line = self.show(kind).replace('**', '')
            self.assertLessEqual(len(line), 76, (kind, line))
        self.assertIn('“What share of the delivered material value ended up wasted', self.show('described'))

    def test_the_question_shown_is_one_that_fits_whole(self):
        # A live run's most-asked question was too long for a chip: it was cut mid-sentence and lost its count.
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {
            'h-1': {'question': 'What share of completed jobs were handed over on time, within a week of the planned end date?'},
            'h-2': {'question': 'How much did rework cost?'}}})
        self.assertEqual(self.show('described'), '❝ “How much did rework cost?” · +1 more in plain words')

    def test_a_layout_in_its_question_has_no_markdown(self):
        # A live terminal run put the layout chip in its question as "▭ **4 tiles**": a question's text shows the stars.
        self.assertIsNotNone(visuals.asked('layout', True))
        self.assertIsNotNone(visuals.asked('layout', False))

    def test_a_fitted_line_breaks_at_a_word(self):
        # A live run's rule chip ended "... at each crew member's or machine's hourly rate" cut as "at ea…".
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [{'name': 'Gross Margin', 'description':
            "Labour Fixit spent putting things right after a snag, at each crew member's or machine's hourly rate."}]})
        line = self.show('rules')
        self.assertTrue(line.endswith('after a snag, at…'), line)

    def test_a_later_answer_with_one_row_has_no_ring(self):
        spec = {'question': 'How much?', 'headline': 'Barn: £1', 'rows': [['Barn', 1]]}
        self.assertEqual(visuals.render('answer', spec, first=False), '▪ **£1** · Barn')


class Due(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root, saved=False, stage=('check', 'running'))
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [{'name': 'Gross Margin', 'description': 'Contract value less costs.'}]})
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [{'from': 'Gross Margin', 'to': 'Rework Cost', 'relation': 'restriction'}]})

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_the_build_steps_are_named_in_order_as_each_finishes(self):
        # Mid-build: the rules are being written, and nothing has been checked yet.
        drop_stages(self.run, 'x3_ingest', 'x4_ingest')
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (self.run / 'context' / name).unlink()
        due = side_view.due(self.run)
        self.assertEqual([k for k in due if k in BUILD_STEPS], ['group', 'tiles', 'described', 'metrics'])
        self.assertNotIn('build', due, 'the build card waits for its last step')

    def test_the_build_card_comes_after_the_last_step(self):
        due = side_view.due(self.run)
        self.assertLess(due.index('links'), due.index('build'))

    def project(self, *args):
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), *args], capture_output=True,
                              text=True, encoding='utf-8', timeout=60, env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        self.assertEqual(done.stderr, '')
        return done.stdout

    def test_visual_next_draws_each_due_moment_in_turn(self):
        for kind in ('connect', 'found', 'sources'):
            side_view.note(self.run, kind)
        first = self.project('visual', 'next')
        self.assertTrue(first.startswith('» **2 different questions**'), first)
        self.assertIn('`visual next` again', first, 'more is due')
        second = self.project('visual', 'next')
        self.assertTrue(second.startswith('▦ **2 dashboard tiles**'), second)
        for _ in range(4):
            self.project('visual', 'next')
        self.assertNotIn('group', side_view.due(self.run))
        widget = self.project('visual', 'next', '--widget')
        self.assertIn('class="a mc"', widget, 'the build card, drawn as a widget')

    def test_a_broken_engine_file_never_stops_the_next_moment(self):
        for kind in ('connect', 'found', 'sources'):
            side_view.note(self.run, kind)
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': 'x'})
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': 7})
        due = side_view.due(self.run)
        self.assertNotIn('described', due)
        self.assertNotIn('links', due)
        self.assertTrue(self.project('visual', 'next').startswith('» **2 different questions**'))

    def test_opening_the_page_first_still_says_more_is_due(self):
        out = self.project('visual', 'next')
        self.assertIn('`visual next` again', out, 'the build chips are due too')

    def test_a_chip_in_a_terminal_opens_the_next_message(self):
        # A live terminal run printed each build chip, then wrote its own words instead: a command's output is folded
        # away, so a chip the agent does not write itself never reaches the person.
        for kind in ('connect', 'found', 'sources'):
            side_view.note(self.run, kind)
        step = json.loads(self.project('visual', 'next').split('side view: ', 1)[1])['next']
        self.assertIn('start your very next message with it', step)
        self.assertIn('before any other words or tool call', step)

    def test_visual_next_with_nothing_due_says_so(self):
        for kind in side_view.due(self.run):
            side_view.note(self.run, kind)
        out = self.project('visual', 'next')
        self.assertIn('Nothing new to show', out)


class Engine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def advance(self, run):
        out = io.StringIO()
        with mock.patch.object(driver, 'invoke', return_value=({'status': 'gate', 'gate': 'x1'}, 3)), redirect_stdout(out):
            code = driver.main(['--runtime', str(self.root / 'rt'), 'advance', '--run', str(run), '--model', 'm'])
        self.assertEqual(code, 3)
        return json.loads(out.getvalue())

    def test_each_gate_reminds_the_agent_to_show_what_the_step_produced(self):
        run = make_run(self.root)
        self.assertIn('visual next', self.advance(run / 'engine')['visual'])

    def test_an_engine_run_outside_a_setup_run_carries_no_reminder(self):
        self.assertNotIn('visual', self.advance(self.root / 'standalone' / 'engine'))


if __name__ == '__main__':
    unittest.main()
