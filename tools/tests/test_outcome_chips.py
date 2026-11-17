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

    def test_each_build_step_says_what_it_produced_and_why_it_matters(self):
        # A person asked what "28 drafts became 10 metrics" or "+29 more in plain words" meant to them: each chip says what
        # the step produced in their terms, and its tail says why that matters.
        self.assertEqual(self.show('group'), '» **2 questions your team asks** · found in 2 of your 3 past queries')
        self.assertEqual(self.show('tiles'), '▦ **2 dashboard tiles read** · each turned into a query')
        self.assertEqual(self.show('described'), '❝ **4 queries in plain words** · so a question finds the right one')
        self.assertEqual(self.show('metrics'), '◆ **2 metrics found** · one number, one rule')
        self.assertEqual(self.show('rules'), '= **2 metrics defined** · what each number counts and leaves out')
        self.assertEqual(self.show('links'), '⋈ **1 link between metrics** · Gross margin uses Rework cost')

    def test_each_build_step_opens_to_what_it_did_and_what_it_produced(self):
        # Where the host draws widgets, a chip opens with no script: one sentence on what the step did, then its list.
        for kind in BUILD_STEPS:
            html = self.show(kind, widget=True)
            self.assertIn('<details', html, kind)
            self.assertNotIn('<script', html, kind)
            detail = html.split('</summary>', 1)[1]
            self.assertRegex(detail, r'<div class="mt">[A-Z][^<]{40,}\.</div>', kind)
            self.assertIn('<div class="ls"><div>', detail, kind)
        self.assertIn('Gross margin', self.show('metrics', widget=True).split('</summary>', 1)[1])
        self.assertIn('What was the gross margin across jobs?', self.show('described', widget=True))
        self.assertIn('run 12 times', self.show('described', widget=True), 'most run first, with how often')
        self.assertIn('What the jobs kept: contract value less labour and materials', self.show('rules', widget=True))
        self.assertIn('Gross margin uses Rework cost', self.show('links', widget=True))

    def test_a_long_list_stops_at_eight_and_says_how_many_more(self):
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {
            'h-%d' % i: {'question': 'How much did job %d make?' % i} for i in range(12)}})
        html = self.show('described', widget=True)
        self.assertEqual(html.count('How much did job'), 8)
        self.assertIn('4 more on the side page', html)

    def test_no_list_shows_the_engines_own_names_or_notes(self):
        # A link's own label read "a component this metric sums, see `differs`": the engine's note to itself.
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Gross Margin', 'to': 'Labour Cost', 'relation': 'derivation', 'role': 'component',
             'label': 'a component this metric sums, see `differs`'}]})
        self.assertNotIn('differs', self.show('links', widget=True))
        for kind in BUILD_STEPS:
            html = self.show(kind, widget=True)
            for internal in ('sum:margin', 'sum:rework', 'vq_id', 'bi-q1', 'h-1', 'x3_', 'm2_'):
                self.assertNotIn(internal, html, (kind, internal))

    def test_a_ratio_and_a_narrower_metric_read_as_what_they_are(self):
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Rework Cost', 'to': 'Labour Cost', 'relation': 'restriction'},
            {'from': 'Waste Rate', 'to': 'Waste', 'relation': 'derivation', 'role': 'numerator'},
            {'from': 'Waste Rate', 'to': 'Delivered Cost', 'relation': 'derivation', 'role': 'denominator'}]})
        self.assertEqual(self.show('links'), '⋈ **3 links between metrics** · Waste rate = Waste ÷ Delivered cost')
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Rework Cost', 'to': 'Labour Cost', 'relation': 'restriction'}]})
        self.assertEqual(self.show('links'), '⋈ **1 link between metrics** · Rework cost is part of Labour cost')

    def test_the_rules_chip_reads_the_rules_as_soon_as_they_are_written(self):
        # The engine writes its cards file with the links; right after the rules it holds them in the next gate's prompt.
        (self.run / 'engine' / 'metric_cards.json').unlink()
        write(self.run / 'engine' / 'x4_prompt.json', {'all_metrics': [
            {'metric': 'Gross Margin', 'description': 'What the jobs kept: contract value less labour.'}]})
        self.assertIn('What the jobs kept: contract value less labour.', self.show('rules', widget=True))

    def test_no_chip_before_its_step_has_finished(self):
        drop_stages(self.run, 'h1_shapes')
        self.assertEqual(self.show('group'), '')
        (self.run / 'engine' / 'verified_queries.bi.json').unlink()
        self.assertEqual(self.show('tiles'), '')
        drop_stages(self.run, 'm2_ingest', 'x2_ingest', 'x3_ingest', 'x4_ingest')
        for kind in ('described', 'metrics', 'rules', 'links'):
            self.assertEqual(self.show(kind), '', kind)

    def test_a_warehouse_build_says_the_same_and_opens_the_same(self):
        warehouse = make_run(self.root / 'w', warehouse='snowflake', chosen={'bi': False, 'history': True})
        self.assertEqual(self.show('group', warehouse), '» **2 questions your team asks** · found in 2 of your 3 past queries')
        self.assertEqual(self.show('tiles', warehouse), '', 'the dashboard was not chosen')
        for kind in ('group', 'described', 'metrics', 'links'):
            self.assertIn('<details', self.show(kind, warehouse, widget=True), kind)

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
        demo = make_run(self.root / 'demo', demo=True)
        self.assertEqual(self.show('context', demo, data=[facts()]), "✓ **Every metric and query ran** · on Fixit's data · Gross margin £118,681")

    def test_no_chip_repeats_what_the_person_already_sees(self):
        # A live desktop run drew "Kept on this machine" right after the person chose it, the layout its question then
        # proposed, and the dashboard's file name its reply gave: none said what a step produced.
        local = make_run(self.root / 'l', saved=False, local=True)
        for widget in (False, True):
            self.assertEqual(self.show('saved', local, widget=widget), '', 'the person just chose it')
            self.assertEqual(self.show('layout', widget=widget), '', 'the layout question says it')
        self.assertNotIn('built', visuals.KINDS, "the reply names the dashboard's file")

    def test_no_chip_shows_progress(self):
        kinds = BUILD_STEPS + ('found', 'sources', 'context')
        for kind in kinds:
            text = self.show(kind)
            self.assertFalse(re.search(r'\d+/\d+', text), (kind, text))
            self.assertFalse(set(visuals.RINGS) & set(text), (kind, text))
            self.assertNotIn('class="rg"', self.show(kind, widget=True), kind)

    def test_a_narrow_chip_cuts_its_tail_before_its_outcome(self):
        # Scaled down in a live run, the rule chip read "Rework co… · rework hours logged by crew members, at each o…": the
        # bold part shrank a fraction of a pixel, and any shrink ellipsizes. A fixed cap then clipped a chip whose mark is
        # wider than the cap assumed (a desktop review, 2026-11-17). As a grid, the outcome keeps its whole width while the
        # tail has room to give, and either one ends in an ellipsis rather than a clipped edge.
        import visual_kit
        self.assertRegex(visual_kit.OUTCOME, r'\.oc\{[^}]*grid-template-columns:auto minmax\(0,max-content\)[^}]*'
                                             r'grid-auto-columns:minmax\(0,1fr\)', 'mark, then the outcome, then the tail, which gives way first')
        self.assertNotIn('calc(100% - 26px)', visual_kit.OUTCOME, 'no cap that assumes one width of mark')
        self.assertRegex(visual_kit.OUTCOME, r'\.oc\{[^}]*overflow:hidden', 'nothing spills past the chip')

    def test_each_chip_has_its_own_mark(self):
        kinds = BUILD_STEPS + ('sources', 'context')
        marks = {self.show(kind).split(' ', 1)[0] for kind in kinds}
        self.assertEqual(len(marks), len(kinds), 'no two steps share a mark')
        widgets = [self.show(kind, widget=True) for kind in kinds]
        for kind, html in zip(kinds, widgets):
            self.assertRegex(html, r'class="(a )?oc', kind)
            self.assertIn('aria-label=', html, kind)
        self.assertEqual(len({re.search(r'<i class="m ([\w-]+)', w).group(1) for w in widgets}), len(kinds))


    def test_the_tiles_chip_names_the_dashboard_page_never_the_side_page(self):
        # Every run with its side page open wrote the page's own address where the dashboard page belonged.
        write(self.run / 'view' / 'server.json', {'url': 'http://127.0.0.1:8123/k/', 'pid': 0})
        self.assertEqual(self.show('tiles'), '▦ **2 dashboard tiles read** · each turned into a query')
        html = self.show('tiles', widget=True)
        self.assertIn('Each tile on the Site dashboard', html)
        self.assertIn('href="http://127.0.0.1:8123/k/"', html, 'its link opens the side page')

    def test_a_step_the_engine_refused_has_not_finished(self):
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        write(manifest, {'stages': stages + [{'stage': 'x4_ingest', 'counts': {}, 'verdict': 'blocked'}]})
        self.assertEqual(self.show('links'), '', 'its latest pass was refused')

    def test_a_rule_keeps_its_abbreviations(self):
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [
            {'name': 'Gross Margin', 'description': 'Jobs that made money (excl. cancelled jobs, e.g. quotes). Not the rest.'}]})
        self.assertIn('Jobs that made money (excl. cancelled jobs, e.g. quotes).', self.show('rules', widget=True))

    def test_every_chip_fits_one_terminal_line(self):
        long = 'What share of the delivered material value ended up wasted on the jobs that finished late this year?'
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {'h-1': {'question': long}}})
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [{'name': 'Gross Margin', 'description': 'Contract value ' + 'less a cost ' * 12 + '.'}]})
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Delivered Material Waste Rate', 'to': 'Delivered Material Waste', 'relation': 'derivation', 'role': 'numerator'},
            {'from': 'Delivered Material Waste Rate', 'to': 'Delivered Materials Cost', 'relation': 'derivation', 'role': 'denominator'}]})
        for kind in BUILD_STEPS + ('sources',):
            line = self.show(kind).replace('**', '')
            self.assertLessEqual(len(line), 76, (kind, line))
        self.assertIn(long, self.show('described', widget=True), 'whole in the list, which wraps')

    def test_the_question_shown_is_one_that_fits_whole(self):
        # A live run's most-asked question was too long for a chip: it was cut mid-sentence and lost its count.
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {
            'h-1': {'question': 'What share of completed jobs were handed over on time, within a week of the planned end date?'},
            'h-2': {'question': 'How much did rework cost?'}}})
        html = self.show('described', widget=True)
        self.assertLess(html.index('What share of completed jobs'), html.index('How much did rework cost?'), 'run 12 times before 5')

    def test_a_description_holding_several_questions_shows_its_first(self):
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {
            'h-1': {'question': 'How much did rework cost? What share of it was billed?'}}})
        html = self.show('described', widget=True)
        self.assertIn('How much did rework cost?', html)
        self.assertNotIn('What share of it was billed?', html)

    def test_a_fitted_line_breaks_at_a_word(self):
        # A live run's rule chip ended "... at each crew member's or machine's hourly rate" cut as "at ea…".
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Delivered Material Waste Rate', 'to': 'Delivered Material Waste', 'relation': 'derivation', 'role': 'numerator'},
            {'from': 'Delivered Material Waste Rate', 'to': 'Delivered Materials Cost', 'relation': 'derivation', 'role': 'denominator'}]})
        line = self.show('links')
        self.assertTrue(line.endswith('rate = Delivered…'), line)

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
        self.assertTrue(first.startswith('» **2 questions your team asks**'), first)
        self.assertIn('`visual next` again', first, 'more is due')
        second = self.project('visual', 'next')
        self.assertTrue(second.startswith('▦ **2 dashboard tiles read**'), second)
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
        self.assertTrue(self.project('visual', 'next').startswith('» **2 questions your team asks**'))

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


QUERIES = {'tasks': [{'queries': [{'vq_id': 'h-1'}, {'vq_id': 'h-2'}, {'vq_id': 'bi-q1'}, {'vq_id': 'bi-q2'}]}]}


class SoFar(unittest.TestCase):
    """While a step runs, the chip the watch asks for every few minutes: how far the step's batches have got, counted as
    the side page counts them, and the first of what they hold."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root)

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def batch(self, gate, name, body, run=None):
        write((run or self.run) / 'engine' / 'answers' / gate / name, body)

    def describe(self, run=None):
        write((run or self.run) / 'engine' / 'm2_prompt.json', QUERIES)
        self.batch('m2', '01.json', {'queries': [
            {'vq_id': 'h-1', 'name': 'Margin by Job', 'question': 'What did each job keep?'},
            {'vq_id': 'h-2', 'name': 'Rework Cost by Month', 'question': 'How much did rework cost each month?'}]}, run)

    def show(self, run=None, widget=False, data=()):
        return visuals.render('progress', visuals.build_spec('progress', run or self.run, list(data)), widget)

    def test_nothing_shows_before_a_batch_lands(self):
        self.assertEqual(self.show(), '')
        self.assertEqual(self.show(widget=True), '')

    def test_each_step_says_how_far_what_it_is_doing_and_why(self):
        # It lands mid-step, after minutes of quiet: the count, what the step is doing, then a line on why it matters.
        import drafts
        self.describe()
        self.assertEqual(self.show(), '❝ **2 of 4 queries** · putting them in plain words\n'
                                      '  Each query gets the question it answers, so the agent finds it when asked.')
        self.assertEqual(drafts.counts(self.run, 'm2'), {'described': 2, 'total': 4}, 'the side page counts the same batches')
        self.batch('m2', '02.json', {'queries': [{'vq_id': 'bi-q1', 'name': 'Gross Margin'}]})
        self.assertTrue(self.show().startswith('❝ **3 of 4 queries** · putting them in plain words\n'), self.show())
        write(self.run / 'engine' / 'x1_prompt.json', QUERIES)
        self.batch('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}]},
            {'vq_id': 'h-2', 'verdict': 'metrics', 'metrics': [{'name': 'Rework Cost'}, {'name': 'Rework Hours'}]}]})
        self.assertEqual(self.show(), '◆ **2 of 4 queries** · finding the numbers they measure\n'
                                      '  Every number a query works out could become a metric your team asks for.')
        self.batch('x2', '01.json', {'metrics': [{'name': 'Gross Margin', 'sources': ['h-1#0']},
                                                 {'name': 'Rework Cost', 'sources': ['h-2#0', 'h-2#1']}]})
        self.assertEqual(self.show(), '◆ **2 metrics so far** · combining 3 possible ones, one per number\n'
                                      '  The same number found in many queries becomes one metric, with one rule.')
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 2, 'tasks': []})
        self.batch('x3', '001.json', {'metrics': [{'name': 'Gross Margin', 'description': 'What the jobs kept. The headline.'}]})
        self.assertEqual(self.show(), '= **1 of 2 metrics** · writing what each one counts and leaves out\n'
                                      '  So the agent works out each number the same way every time you ask.')

    def test_its_reason_shows_without_opening_and_opens_to_the_newest_first(self):
        names = ['Gross Margin', 'Rework Cost', 'Labour Cost', 'Material Cost', 'Contract Value', 'Late Jobs', 'On-Time Rate',
                 'Waste Rate', 'Jobs Completed', 'Crew Hours']
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 12, 'tasks': []})
        for i, name in enumerate(names):
            self.batch('x3', '%03d.json' % i, {'metrics': [{'name': name, 'description': name + ' counted per job. Not the rest.'}]})
        html = self.show(widget=True)
        self.assertIn('<details', html)
        self.assertNotIn('<script', html)
        summary, detail = html.split('</summary>', 1)
        self.assertIn('<span class="ws">So the agent works out each number the same way every time you ask.</span>', summary)
        self.assertIn('class="oc two"', summary, 'the chip lays its reason out on a second row')
        self.assertNotIn('class="mt"', detail, 'opened, it does not say the reason again')
        self.assertIn('Crew hours', detail, 'the newest first')
        self.assertIn('Crew Hours counted per job.', detail, "the definition's first sentence")
        self.assertNotIn('Gross Margin counted per job.', detail, 'the oldest are the ones left to the side page')
        self.assertNotIn('Not the rest', detail)
        self.assertEqual(detail.count('counted per job.'), 8)
        self.assertIn('2 more on the side page', detail)

    def test_each_step_opens_in_its_own_words_with_no_engine_names(self):
        self.describe()
        steps = [self.show(widget=True)]
        write(self.run / 'engine' / 'x1_prompt.json', QUERIES)
        self.batch('x1', '01.json', {'statements': [{'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}]}]})
        steps.append(self.show(widget=True))
        self.batch('x2', '01.json', {'metrics': [{'name': 'Gross Margin', 'sources': ['h-1#0']}]})
        steps.append(self.show(widget=True))
        for reason, name, html in zip(('Each query gets', 'Every number a query', 'The same number found'),
                                      ('Margin by job', 'Gross margin', 'Gross margin'), steps):
            self.assertIn('<span class="ws">' + reason, html)
            self.assertIn(name, html)
            for internal in ('vq_id', 'h-1', 'bi-q1', 'x1', 'm2', 'proposal', 'draft'):
                self.assertNotIn(internal, html, (reason, internal))

    def test_its_list_adds_up_to_its_count(self):
        # Rendered from a real run, "41 possible metrics found" opened to 30 names: a metric found in several queries
        # was listed once with nothing to say so.
        write(self.run / 'engine' / 'x1_prompt.json', QUERIES)
        self.batch('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}]},
            {'vq_id': 'h-2', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}, {'name': 'Rework Cost'}]},
            {'vq_id': 'bi-q1', 'verdict': 'metrics', 'metrics': [{'name': 'gross margin'}]}]})
        html = self.show(widget=True)
        self.assertIn('4 possible metrics found', html)
        self.assertIn('<b style="font-weight:400">Gross margin</b><span>found 3 times</span>', html)
        self.assertIn('<b style="font-weight:400">Rework cost</b><span></span>', html)
        described = make_run(self.root / 'd')
        write(described / 'engine' / 'm2_prompt.json', QUERIES)
        self.batch('m2', '01.json', {'queries': [{'vq_id': 'h-1', 'name': 'Gross Margin'}, {'vq_id': 'bi-q1', 'name': 'Gross Margin'}]},
                   described)
        self.assertEqual(self.show(described, widget=True).count('<b style="font-weight:400">Gross margin</b>'), 2,
                         'two queries, though they share a name')

    def test_every_terminal_line_fits(self):
        write(self.run / 'engine' / 'x2_prompt.json', {})
        self.batch('x2', '01.json', {'metrics': [{'name': 'Metric %d' % i, 'sources': ['h-%d#0' % i]} for i in range(1234)]})
        lines = self.show().split('\n')
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertLessEqual(len(line.replace('**', '')), 76, line)
        self.assertTrue(lines[0].startswith('◆ **1,234 metrics so far** · combining'), lines[0])

    def test_a_demo_a_local_run_and_a_warehouse_draw_it_the_same(self):
        for run, data in ((make_run(self.root / 'demo', demo=True), [facts()]), (make_run(self.root / 'l', saved=False, local=True), []),
                          (make_run(self.root / 'w', warehouse='snowflake', chosen={'bi': False, 'history': True}), [])):
            self.describe(run)
            self.assertTrue(self.show(run, data=data).startswith('❝ **2 of 4 queries** · putting them in plain words\n'))
            self.assertIn('<details', self.show(run, widget=True, data=data))

    def test_the_helper_draws_it_without_recording_it_or_naming_it_due(self):
        # It is drawn as often as the watch asks: recorded, each one would pile up; named due, it would stop the build's flow.
        self.describe()
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'progress'],
                              capture_output=True, text=True, encoding='utf-8', timeout=60, env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        self.assertEqual(done.stderr, '')
        self.assertTrue(done.stdout.startswith('❝ **2 of 4 queries** · putting them in plain words'), done.stdout)
        self.assertIn('start your very next message with it', done.stdout)
        self.assertNotIn('progress', side_view.seen(self.run))
        self.assertNotIn('progress', side_view.due(self.run))


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
        self.assertIn('no words of your own', self.advance(run / 'engine')['visual'], 'the chip is the update')

    def test_an_engine_run_outside_a_setup_run_carries_no_reminder(self):
        self.assertNotIn('visual', self.advance(self.root / 'standalone' / 'engine'))


if __name__ == '__main__':
    unittest.main()
