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
        first = lambda kind: self.show(kind).split('\n')[0]  # a metric step's second line places it in the three steps
        self.assertEqual(first('group'), '» **2 questions your team asks** · found in 2 of your 3 past queries')
        self.assertEqual(first('tiles'), '▦ **2 dashboard tiles read** · each turned into a query')
        # Each count says where it came from, so 110, 95, 24, 30, 25 and 8 read as one story.
        self.assertEqual(first('described'), '❝ **4 queries in plain words** · your 2 questions and 2 dashboard tiles')
        self.assertEqual(first('metrics'), '◆ **2 metrics found** · from 3 possible metrics, one per number')
        self.assertEqual(first('rules'), '= **2 metrics defined** · what each number counts and leaves out')
        self.assertEqual(self.show('links'), '⋈ **1 link between metrics** · Gross margin uses Rework cost')

    def drafted(self):
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        for s in stages:
            if s['stage'] == 'x1_ingest':
                s['counts'] = {'answered': 4, 'proposals': 5, 'no_metric': 1}
        write(manifest, {'stages': stages})
        write(self.run / 'engine' / 'metric_proposals.json', {'proposals': [
            {'proposal_id': 'h-%d#0' % i, 'vq_id': 'h-%d' % i, 'name': name} for i, name in enumerate(
                ['Gross Margin', 'Gross Margin', 'Gross Margin', 'Rework Cost', 'Job Count'])]})

    def test_the_draft_step_ends_on_its_own_milestone(self):
        # Live (h2), the chat went 3.4 minutes from the queries described to the merge with nothing new: the draft step
        # had no chip of its own.
        self.drafted()
        self.assertEqual(self.show('drafted').split('\n')[0], '◆ **5 possible metrics** · found in 3 of those 4 queries')
        self.assertIn('<b style="font-weight:400">Gross margin</b><span>in 3 queries</span>', self.show('drafted', widget=True))
        import side_view
        due = side_view.due(self.run)
        self.assertLess(due.index('drafted'), due.index('metrics'))
        self.assertLess(due.index('described'), due.index('drafted'))

    def test_each_metric_step_names_its_task_list_step_and_what_comes_next(self):
        # Team review: the chips show how the steps connect (draft, merge, write), with no progress bar.
        self.drafted()
        # A first-time reader: "the seven-step list never matched the Step 1 of 3 chips". The chips name the list's steps.
        for kind, start in (('described', 'Finding metrics, under way: next,'), ('drafted', 'Done: Find metrics. Next: Combine duplicates'),
                            ('metrics', 'Done: Combine duplicates. Next: Define and test'), ('rules', 'Definitions written. Next: test each one')):
            lines = self.show(kind).split('\n')
            self.assertTrue(len(lines) > 1 and lines[1].strip().startswith(start), (kind, lines))
            self.assertIn(start, self.show(kind, widget=True))

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

    def test_a_demo_s_chips_name_the_firm_never_the_person(self):
        # The demo's welcome says "Made-up data; nothing of yours is touched"; a fresh reader of h8 then met "your 110 past
        # queries", "your team's words" and "each number your team uses": a contradiction. Own data keeps "your".
        demo = make_run(self.root / 'demo', demo=True)
        shown = 0
        for kind in ('group', 'described', 'drafted', 'metrics', 'context'):
            for widget in (False, True):
                said = self.show(kind, demo, widget=widget, data=[facts()])
                if said:
                    shown += 1
                    self.assertNotRegex(said, r'\b[Yy]our\b', (kind, widget))
        self.assertGreaterEqual(shown, 8)
        self.assertIn('your', self.show('described', widget=True), 'own data is the person\'s')

    def tiled(self, run, figure):
        # Gross margin made from the dashboard's Gross margin tile, whose own query returned `figure` on the data.
        metrics = json.loads((run / 'context' / 'metrics.json').read_text())
        metrics[0]['definition']['provenance']['derived_from'] = ['bi-q1', 'h-1']
        write(run / 'context' / 'metrics.json', metrics)
        bi = json.loads((run / 'engine' / 'verified_queries.bi.json').read_text())
        bi['objects'][0]['witness'] = {'result': 'pass', 'rows': 1, 'columns': ['gross_margin'], 'sample': [[figure]]}
        write(run / 'engine' / 'verified_queries.bi.json', bi)

    def test_the_checks_chip_sets_each_number_beside_its_dashboard_tile(self):
        # A first-time reader of a live run never saw the dashboard's own figure beside the agent's: nothing showed why
        # to trust either. Across 40 earlier runs, 151 of 159 such pairs matched and 8 did not.
        self.tiled(self.run, '118681.0')
        self.assertEqual(self.show('context'), '✓ **Every metric and query ran** · on your data · Gross margin £118,681\n'
                                               '  The dashboard\u2019s own formula for its Gross margin tile gives the same.')
        detail = self.show('context', widget=True).split('</summary>', 1)[1]
        self.assertIn("Each metric\u2019s definition ran on your data and returned a number.", detail)
        # The dashboard file holds each tile's formula, not the number it shows (a Power BI template has no data): the
        # comparison says so, so a reader does not take it for the figure on the screen.
        self.assertIn('not the number it shows', detail)
        self.assertIn('<b style="font-weight:400">Gross margin</b><span>£118,681</span><em>Same as the dashboard\u2019s '
                      'formula for its Gross margin tile</em>', detail)
        self.assertIn('<b style="font-weight:400">Rework cost</b><span>£33,800</span>', detail)

    def test_a_number_that_differs_from_its_tile_says_so(self):
        self.tiled(self.run, '117000.0')
        self.assertEqual(self.show('context').split('\n')[1],
                         '  Gross margin differs from its dashboard tile\u2019s formula: £117,000.')
        self.assertIn('<em>The dashboard\u2019s formula for its Gross margin tile gives £117,000</em>', self.show('context', widget=True))

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
        self.assertNotIn('127.0.0.1', html, 'the side page is never linked from a widget')

    def test_a_widget_names_the_side_page_in_words_and_links_nowhere(self):
        # Live, "Open on the side page" did nothing: a widget's link opens the host's link dialog and then a browser,
        # never the side panel. The words stay; the link goes.
        write(self.run / 'view' / 'server.json', {'url': 'http://127.0.0.1:8123/k/', 'pid': 0})
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {
            'h-%d' % i: {'question': 'How much did job %d make?' % i} for i in range(12)}})
        for kind in BUILD_STEPS:
            self.assertNotIn('<a ', self.show(kind, widget=True), kind)
        self.assertIn('4 more on the side page', self.show('described', widget=True))

    def test_a_card_names_the_side_page_without_looking_like_a_link(self):
        # Link blue and an outward arrow on a line that opens nothing read as a broken link.
        write(self.run / 'view' / 'server.json', {'url': 'http://127.0.0.1:8123/k/', 'pid': 0})
        html = self.show('build', widget=True)
        self.assertIn('on the side page beside the chat', html)
        self.assertNotIn('M9 6.65032C9 6.65032 15.9383', html, 'no outward arrow')
        self.assertNotIn('.mo{display:flex;gap:6px;align-items:center;font-size:12px;line-height:16px;color:var(--bt)', html)

    def test_the_tiles_chip_opens_to_each_tile_with_its_question(self):
        detail = self.show('tiles', widget=True).split('</summary>', 1)[1]
        self.assertIn('<b style="font-weight:400">Gross margin</b><span></span><em>What is the gross margin in pounds?</em>', detail)
        # Live, the chip comes before the queries are described: the tile's own question is there, its short name never shows.
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {}})
        write(self.run / 'engine' / 'verified_queries.bi.json', {'objects': [
            {'vq_id': 'bi-q1', 'question': 'What gross margin did Fixit make?', 'provenance': {'lane': 'bi', 'bi': {'visual': 'Gross margin', 'page': 'Site'}}},
            {'vq_id': 'bi-q2', 'question': 'margin_by_job', 'provenance': {'lane': 'bi', 'bi': {'visual': 'Margin by job', 'page': 'Site'}}}]})
        detail = self.show('tiles', widget=True).split('</summary>', 1)[1]
        self.assertIn('<em>What gross margin did Fixit make?</em>', detail)
        self.assertIn('Margin by job', detail)
        self.assertNotIn('margin_by_job', detail)

    def test_before_the_queries_are_described_each_group_reads_as_words(self):
        # Live, the list opened to "On time rate", "Measure", "On time rate": each query's column name, two alike.
        def query(vid, name, by, tables, runs):
            return {'vq_id': vid, 'question': name, 'group_by': by, 'tables': tables,
                    'provenance': {'lane': 'history', 'history': {'n_exec': runs, 'n_distinct_users': 1}}}
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {}})
        write(self.run / 'engine' / 'verified_queries.history.json', {'objects': [
            query('h-1', 'on time rate', [], ['SITE.SITE.PROJECTS'], 36),
            query('h-2', 'on time rate', ['project_type'], ['SITE.SITE.PROJECTS'], 14),
            query('h-3', 'measure', ['project_id'], ['SITE.SITE.PROJECTS', 'SITE.SITE.WORK_LOGS'], 34),
            query('h-4', 'on time rate', [], ['SITE.SITE.PROJECTS'], 3)]})
        detail = self.show('group', widget=True).split('</summary>', 1)[1]
        rows = re.findall(r'<b style="font-weight:400">([^<]*)</b><span>([^<]*)</span>', detail)
        self.assertEqual(rows, [('On time rate', '2 queries, run 39 times'),
                                ('A question about projects and work logs, by project', 'run 34 times'),
                                ('On time rate by project type', 'run 14 times')])

    def test_before_the_queries_are_described_their_short_names_are_spelled_out(self):
        # A first-time reader of a live run stopped at "Waste pct", "Rework cost gbp by project name" and "A number by
        # project id": column shorthand they were never taught.
        def query(vid, name, by, runs):
            return {'vq_id': vid, 'question': name, 'group_by': by, 'tables': ['SITE.SITE.PROJECTS'],
                    'provenance': {'lane': 'history', 'history': {'n_exec': runs, 'n_distinct_users': 1}}}
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {}})
        write(self.run / 'engine' / 'verified_queries.history.json', {'objects': [
            query('h-1', 'waste_pct', ['material'], 50), query('h-2', 'rework_cost_gbp', ['project_name'], 40),
            query('h-3', 'billable_utilisation', ['crew_name', 'month_dt'], 30), query('h-4', 'avg_job_value', [], 20),
            query('h-5', 'num_jobs', ['site_id'], 15), query('h-6', 'total', ['project_id'], 10),
            query('h-7', 'cnt', [], 5), query('h-8', 'qty_delivered', ['yr'], 4)]})
        detail = self.show('group', widget=True).split('</summary>', 1)[1]
        rows = [r for r, _ in re.findall(r'<b style="font-weight:400">([^<]*)</b><span>([^<]*)</span>', detail)]
        self.assertEqual(rows, ['Waste percentage by material', 'Rework cost by project', 'Billable utilisation by crew and month',
                                'Average job value', 'Number of jobs by site', 'A total from projects, by project',
                                'A count from projects', 'Quantity delivered by year'])

    def test_a_step_the_engine_refused_has_not_finished(self):
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        write(manifest, {'stages': stages + [{'stage': 'x4_ingest', 'counts': {}, 'verdict': 'blocked'}]})
        self.assertEqual(self.show('links'), '', 'its latest pass was refused')

    def test_a_rule_keeps_its_abbreviations(self):
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [
            {'name': 'Gross Margin', 'description': 'Jobs that made money (excl. cancelled jobs, e.g. quotes). Not the rest.'}]})
        self.assertIn('Jobs that made money (excl. cancelled jobs, e.g. quotes).', self.show('rules', widget=True))

    def test_every_chip_line_fits_a_terminal(self):
        long = 'What share of the delivered material value ended up wasted on the jobs that finished late this year?'
        write(self.run / 'engine' / 'sql_descriptions.json', {'descriptions': {'h-1': {'question': long}}})
        write(self.run / 'engine' / 'metric_cards.json', {'cards': [{'name': 'Gross Margin', 'description': 'Contract value ' + 'less a cost ' * 12 + '.'}]})
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Delivered Material Waste Rate', 'to': 'Delivered Material Waste', 'relation': 'derivation', 'role': 'numerator'},
            {'from': 'Delivered Material Waste Rate', 'to': 'Delivered Materials Cost', 'relation': 'derivation', 'role': 'denominator'}]})
        for kind in BUILD_STEPS + ('sources',):
            for line in self.show(kind).replace('**', '').split('\n'):
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
        for _ in range(5):  # described, drafted, metrics, rules, links
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

    def test_each_step_names_what_it_made_and_keeps_its_count_inside(self):
        # It lands mid-step. Led by a count, it read as a counter ("8 of 30 queries", "1 metric so far"); and a count in
        # the chat goes stale while the side page moves on, so the next chip jumped (h8 desktop: "1 of 8", then "4 of 8").
        # The person's decisions (2026-11-18): the headline names what is new, the count sits inside the opened chip
        # with no time of day (it confused), and the step's own chip gives the total.
        import drafts
        self.describe()
        self.assertEqual(self.show(), '❝ **Rework cost by month and Margin by job** in plain words\n'
                                      '  Finding metrics: first, each query gets the question it answers.')
        self.assertEqual(drafts.counts(self.run, 'm2'), {'described': 2, 'total': 4}, 'the side page counts the same batches')
        self.assertIn('>2 of 4 queries in plain words.<', self.show(widget=True))
        self.batch('m2', '02.json', {'queries': [{'vq_id': 'bi-q1', 'name': 'Gross Margin'}]})
        # Three names only when they fit a card's width; else the newest and how many more.
        self.assertTrue(self.show().startswith('❝ **Gross margin** and 2 more in plain words\n'), self.show())
        write(self.run / 'engine' / 'x1_prompt.json', QUERIES)
        self.batch('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}]},
            {'vq_id': 'h-2', 'verdict': 'metrics', 'metrics': [{'name': 'Rework Cost'}, {'name': 'Rework Hours'}]}]})
        self.assertEqual(self.show(), '◆ **Rework hours, Rework cost and Gross margin** found\n'
                                      '  Finding metrics: every number a query works out could be one.')
        # A fresh reader of h9 met "found in 30 of 30 queries", then "found in 25 of those 30": both count the queries
        # that held a number now, out of those read.
        self.assertIn('>3 possible metrics found in 2 of the 2 queries read.<', self.show(widget=True))
        self.batch('x2', '01.json', {'metrics': [{'name': 'Gross Margin', 'sources': ['h-1#0']},
                                                 {'name': 'Rework Cost', 'sources': ['h-2#0', 'h-2#1']}]})
        self.assertEqual(self.show(), '◆ **Rework cost and Gross margin** combined\n'
                                      '  Combining duplicates: the same number in many queries becomes one metric.')
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 2, 'tasks': []})
        self.batch('x3', '001.json', {'metrics': [{'name': 'Gross Margin', 'description': 'What the jobs kept. The headline.'}]})
        self.assertEqual(self.show(), '= **Gross margin** defined\n'
                                      '  Defining: so each number is worked out the same way every time.')
        self.assertIn('>1 of 2 metrics defined.<', self.show(widget=True))
        self.assertNotRegex(self.show(widget=True), r'\d\d:\d\d', 'no time of day anywhere in the chip')
        self.assertNotRegex(self.show(), r'\d+ of \d+', 'no count in the chat line')

    def test_more_than_three_new_names_the_newest_and_how_many_more(self):
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 12, 'tasks': []})
        for i, name in enumerate(('Gross Margin', 'Rework Cost', 'Labour Cost', 'Late Jobs')):
            self.batch('x3', '%03d.json' % (i + 1), {'metrics': [{'name': name, 'description': name + ' per job.'}]})
        self.assertTrue(self.show().startswith('= **Late jobs** and 3 more defined\n'), self.show())

    def test_a_long_name_still_fits_a_terminal_line(self):
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 12, 'tasks': []})
        self.batch('x3', '001.json', {'metrics': [{'name': 'Average contract value of completed residential extension '
                                                           'jobs per quarter', 'description': 'Per job.'}]})
        self.batch('x3', '002.json', {'metrics': [{'name': 'Material waste rate across every delivered order this '
                                                           'quarter', 'description': 'Per order.'}]})
        line = self.show().split('\n')[0]
        self.assertLessEqual(len(line.replace('**', '')), 76, line)
        self.assertTrue(line.endswith(' defined'), line)

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
        self.assertIn('<span class="ws">Defining: so each number is worked out the same way every time.</span>', summary)
        self.assertIn('class="oc two"', summary, 'the chip lays its reason out on a second row')
        self.assertNotIn('Defining: so each number', detail, 'opened, it does not say the reason again')
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
        for reason, name, html in zip(('Finding metrics: first, each query', 'Finding metrics: every number', 'Combining duplicates: the same number'),
                                      ('Margin by job', 'Gross margin', 'Gross margin'), steps):
            self.assertIn('<span class="ws">' + reason, html)
            self.assertIn(name, html)
            # "Draft" is the task list's own step and a metric's first state (team decision, 2026-11-18), not engine talk.
            for internal in ('vq_id', 'h-1', 'bi-q1', 'x1', 'm2', 'proposal'):
                self.assertNotIn(internal, html, (reason, internal))

    def test_a_so_far_chip_is_the_whole_turn(self):
        # Live, every so-far chip was followed by "The merge is still running in the background.": the woken turn closed
        # on a status line of its own. The chip is the update, and the watch wakes the chat again.
        self.describe()
        for flags in (['--widget'], []):
            done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'progress']
                                  + flags, capture_output=True, text=True, encoding='utf-8', timeout=60,
                                  env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
            self.assertIn('end your turn on it', done.stdout, flags)
            self.assertIn('watch wakes you', done.stdout, flags)
            # The desktop sends a turn that ends on a widget alone back for words: the watch's news line closes it.
            self.assertIn('then this line, unchanged, and nothing else: ', done.stdout, flags)
            self.assertNotIn('no line after it', done.stdout, flags)

    def test_the_line_to_end_on_comes_with_the_chip_and_names_what_it_names(self):
        # h8 at 13.6 drew "Delivered materials cost written" over the watch's older line "Newest rule: Project labour
        # cost": the watch wrote its line, another rule landed, then the chip was drawn. Both now come from one call.
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 3, 'tasks': []})
        self.batch('x3', '001.json', {'metrics': [{'name': 'Gross Margin', 'description': 'What the jobs kept. More.'}]})
        self.batch('x3', '002.json', {'metrics': [{'name': 'Rework Cost', 'description': 'Labour on snags. More.'}]})
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'progress'],
                              capture_output=True, text=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        self.assertTrue(done.stdout.startswith('= **Rework cost and Gross margin** defined'), done.stdout)
        side = json.loads(done.stdout.split('side view: ', 1)[1])
        self.assertIn('then this line, unchanged, and nothing else: Newest definition: Rework cost. Labour on snags.', side['next'])

    def test_a_step_s_own_chip_gives_the_line_to_end_on_and_its_title(self):
        # The person, 2026-11-18: our own wording leaked into the window. After a step's chip the agent wrote "Doing:
        # Combining duplicates, one metric per number." under a chip that said "Next: Combine duplicates, one metric per
        # number."; its widgets were titled "fixit_define_progress_chip_3". The chip's result gives the line to end on
        # (the newest thing made) and a title in the person's words.
        self.batch('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin', 'definition': 'What the jobs kept. More.'}]}]})
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        write(manifest, {'stages': stages + [{'stage': 'x1_ingest', 'counts': {'proposals': 1}, 'verdict': 'accepted'}]})
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'drafted',
                               '--widget'], capture_output=True, text=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        side = json.loads(done.stdout.split('side view: ', 1)[1])
        self.assertIn('with the title `1_possible_metric`', side['next'])
        self.assertIn('end your turn on it, then this line, unchanged, and nothing else: Newest possible metric: Gross margin. '
                      'What the jobs kept.', side['next'])
        self.assertNotIn('Doing', side['next'])
        progress = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual',
                                   'progress', '--widget'], capture_output=True, text=True, encoding='utf-8', timeout=60,
                                  env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        self.assertNotIn('chip`', progress.stdout.split('side view: ', 1)[-1].split('title `', 1)[-1][:60])

    def test_each_so_far_chip_names_only_what_landed_since_the_last_one(self):
        # The person, 2026-11-18: "we say 10 out of 20, then 18 out of 20: we lost the 11th to the 17th". And a fresh
        # reader of h8 met the same definitions in every chip. Each chip names what is new since the one before, so
        # across a step every metric is named once.
        def progress():
            done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual',
                                   'progress', '--widget'], capture_output=True, text=True, encoding='utf-8', timeout=60,
                                  env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
            html, side = done.stdout.split('side view: ', 1)
            return html.split('</summary>', 1), json.loads(side)
        write(self.run / 'engine' / 'x3_prompt.json', {'n_metrics': 5, 'tasks': []})
        for i, name in enumerate(('Gross Margin', 'Rework Cost')):
            self.batch('x3', '%03d.json' % (i + 1), {'metrics': [{'name': name, 'description': name + ' per job. More.'}]})
        (head, body), side = progress()
        self.assertIn('<b>Rework cost and Gross margin</b> defined', head)
        self.assertIn('Gross Margin per job.', body)
        for i, name in enumerate(('Labour Cost', 'Late Jobs', 'Waste Rate')):
            self.batch('x3', '%03d.json' % (i + 3), {'metrics': [{'name': name, 'description': name + ' per job. More.'}]})
        (head, body), side = progress()
        self.assertIn('<b>Waste rate, Late jobs and Labour cost</b> defined', head)
        self.assertRegex(body, r'>5 of 5 metrics defined\.<')
        for new in ('Waste rate', 'Late jobs', 'Labour cost'):
            self.assertIn(new, body)
        for old in ('Gross margin', 'Rework cost'):
            self.assertNotIn(old, body, 'named by the chip before')
        self.assertIn('Newest definition: Waste rate. Waste Rate per job.', side['next'])
        # Drawn again with nothing new, it shows the newest rather than nothing.
        (head, body), side = progress()
        self.assertIn('<b>Waste rate</b> and 4 more defined', head)

    def test_a_step_already_accepted_draws_no_so_far_chip(self):
        # Drawn after its step ended, a so-far chip said a finished step was still "finding the numbers".
        self.describe()
        write(self.run / 'engine' / 'manifest.json', {'stages': [{'stage': 'm2_ingest', 'verdict': 'ok'}]})
        self.assertEqual(self.show(), '')
        self.assertEqual(self.show(widget=True), '')

    def test_its_list_adds_up_to_its_count(self):
        # Rendered from a real run, "41 possible metrics found" opened to 30 names: a metric found in several queries
        # was listed once with nothing to say so.
        write(self.run / 'engine' / 'x1_prompt.json', QUERIES)
        self.batch('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}]},
            {'vq_id': 'h-2', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin'}, {'name': 'Rework Cost'}]},
            {'vq_id': 'bi-q1', 'verdict': 'metrics', 'metrics': [{'name': 'gross margin'}]}]})
        html = self.show(widget=True)
        # A fresh reader met "10 draft metrics found" opening to 7 names: the count says how many are different.
        self.assertIn('4 possible metrics, 2 different, found in', html)
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
        self.assertTrue(lines[0].startswith('◆ **Metric 1233** and 1,233 more combined'), lines[0])

    def test_a_demo_a_local_run_and_a_warehouse_draw_it_the_same(self):
        for run, data in ((make_run(self.root / 'demo', demo=True), [facts()]), (make_run(self.root / 'l', saved=False, local=True), []),
                          (make_run(self.root / 'w', warehouse='snowflake', chosen={'bi': False, 'history': True}), [])):
            self.describe(run)
            self.assertTrue(self.show(run, data=data).startswith('❝ **Rework cost by month and Margin by job** in plain words'))
            self.assertIn('<details', self.show(run, widget=True, data=data))

    def test_the_helper_draws_it_without_recording_it_or_naming_it_due(self):
        # It is drawn as often as the watch asks: recorded, each one would pile up; named due, it would stop the build's flow.
        self.describe()
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'progress'],
                              capture_output=True, text=True, encoding='utf-8', timeout=60, env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        self.assertEqual(done.stderr, '')
        self.assertTrue(done.stdout.startswith('❝ **Rework cost by month and Margin by job** in plain words'), done.stdout)
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
