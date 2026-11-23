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
        self.assertEqual(first('tiles'), '▦ **2 tiles read** · each written as a question with SQL')
        # Each count says where it came from, so 110, 95, 24, 30, 25 and 8 read as one story.
        self.assertEqual(first('described'), '❝ **4 questions written** · in your team\'s words')
        self.assertEqual(first('metrics'), '◆ **2 metrics** · 1 duplicate merged')
        self.assertEqual(first('rules'), '= **Detailed information added to 2 metrics** · what each counts and leaves out')
        self.assertEqual(self.show('links'), '⋈ **1 link between metrics** · e.g. Gross margin uses Rework cost')

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
        self.assertEqual(self.show('drafted').split('\n')[0], '◆ **5 metrics found** · from 5 past queries')
        self.assertIn('<b style="font-weight:400">Gross margin</b><span>in 3 questions</span>', self.show('drafted', widget=True))
        import side_view
        due = side_view.due(self.run)
        self.assertLess(due.index('drafted'), due.index('metrics'))
        self.assertLess(due.index('described'), due.index('drafted'))

    def test_each_metric_step_names_its_task_list_step_and_what_comes_next(self):
        # Team review: the chips show how the steps connect (draft, merge, write), with no progress bar.
        self.drafted()
        # A first-time reader: "the seven-step list never matched the Step 1 of 3 chips". The chips name the list's steps.
        for kind, start in (('described', 'Questions written. Next:'), ('drafted', 'Metrics found. Next: merging duplicates'),
                            ('metrics', 'Duplicates merged. Next: adding detailed information'), ('rules', 'Information added. Next: validating each')):
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
        # 0.9.74: the first build chip pointed at the side page before it had opened.
        self.assertIn('<div class="more">4 more</div>', html)
        self.assertNotIn('side page', html.split('</summary>', 1)[1])
        write(self.run / 'view' / 'server.json', {'url': 'http://127.0.0.1:8123/k/', 'pid': 0})
        self.assertIn('4 more on the side page', self.show('described', widget=True))

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
        self.assertEqual(self.show('links'), '⋈ **3 links between metrics** · e.g. Waste rate = Waste ÷ Delivered cost')
        write(self.run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Rework Cost', 'to': 'Labour Cost', 'relation': 'restriction'}]})
        self.assertEqual(self.show('links'), '⋈ **1 link between metrics** · e.g. Rework cost is part of Labour cost')

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
        self.assertEqual(self.show('context'), '✓ **Every test passed** · on your data · Gross margin £118,681')
        failing = make_run(self.root / 'f', failing=1)
        self.assertEqual(self.show('context', failing), '△ **3 passed** · 1 needs your review · on your data')
        demo = make_run(self.root / 'demo', demo=True)
        self.assertEqual(self.show('context', demo, data=[facts()]), "✓ **Every test passed** · on Fixit's data · Gross margin £118,681")

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
                    # "your agent" is the one the person is building, as the demo's own questions say ("Your job profit agent").
                    self.assertNotRegex(said, r'\b[Yy]our\b(?! agent\b)', (kind, widget))
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
        self.assertEqual(self.show('context'), '✓ **Every test passed** · on your data · Gross margin £118,681\n'
                                               "  Gross margin matches its Gross margin tile's formula.")
        detail = self.show('context', widget=True).split('</summary>', 1)[1]
        self.assertIn("Each metric was tested on your data and returned a number.", detail)
        # The dashboard file holds each tile's formula, not the number it shows (a Power BI template has no data): the
        # comparison says so, so a reader does not take it for the figure on the screen.
        self.assertIn('without the number it shows', detail)
        self.assertIn('<b style="font-weight:400">Gross margin</b><span>£118,681</span><em>Same as the Gross margin '
                      'tile&#x27;s formula</em>', detail)
        self.assertIn('<b style="font-weight:400">Rework cost</b><span>£33,800</span>', detail)

    def test_a_number_that_differs_from_its_tile_says_so(self):
        self.tiled(self.run, '117000.0')
        # 0.9.74, Ticketfuddle: "Every test passed" headed a chip whose own line said Sell-through differed from its tile.
        self.assertEqual(self.show('context').split('\n')[0], '△ **0 of 1 match the dashboard; Gross margin differs** · on your data')
        self.assertEqual(self.show('context').split('\n')[1],
                         "  Gross margin differs from its tile's formula, which gives £117,000.")
        import questions
        asked = questions.save(self.run, widget=True)['question']
        self.assertIn('all passed. 0 of 1 match the dashboard; Gross margin differs.', asked)
        self.assertIn('<em>The Gross margin tile&#x27;s formula gives £117,000</em>', self.show('context', widget=True))

    def test_no_chip_repeats_what_the_person_already_sees(self):
        # A live desktop run drew "Kept on this machine" right after the person chose it, the layout its question then
        # proposed, and the dashboard's file name its reply gave: none said what a step produced.
        local = make_run(self.root / 'l', saved=False, local=True)
        for widget in (False, True):
            self.assertEqual(self.show('saved', local, widget=widget), '', 'the person just chose it')
        self.assertNotIn('built', visuals.KINDS, "the reply names the dashboard's file")

    def test_a_moment_with_nothing_to_show_still_says_what_to_do(self):
        # A watched run asked for the build's card before its checks: the helper printed nothing at all, and the session
        # filled the silence with a line of its own giving the side page's address.
        local = make_run(self.root / 'quiet', saved=False, local=True)
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(local), 'visual', 'saved'],
                              capture_output=True, text=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        self.assertEqual(done.returncode, 0, done.stderr)
        side = json.loads(done.stdout.split('side view: ', 1)[1])
        self.assertIn('Nothing to show for this moment', side['next'])
        self.assertIn('no words about it', side['next'])

    def test_no_chip_shows_progress(self):
        kinds = BUILD_STEPS + ('found', 'sources', 'context')
        for kind in kinds:
            text = self.show(kind)
            self.assertFalse(re.search(r'\d+/\d+', text), (kind, text))
            self.assertFalse(set('○◔◑◕●') & set(text), (kind, text))
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
        self.assertEqual(self.show('tiles'), '▦ **2 tiles read** · each written as a question with SQL')
        html = self.show('tiles', widget=True)
        self.assertIn('Each tile on your Site dashboard', html)
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
        self.assertIn('on the side page', html)
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
        self.assertTrue(second.startswith('▦ **2 tiles read**'), second)
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


class StepChips(unittest.TestCase):
    """The chip that ends a metric step: what it hands the session next, and its title in the person's words."""

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

    def test_in_the_desktop_app_a_step_chip_keeps_the_turn_going(self):
        self.describe()
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        write(manifest, {'stages': stages + [{'stage': 'x1_ingest', 'counts': {'proposals': 1}, 'verdict': 'ok'}]})
        self.batch('x1', '01.json', {'statements': [{'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross margin'}]}]})
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'drafted'],
                              capture_output=True, text=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'})
        side = json.loads(done.stdout.split('side view: ', 1)[1])
        self.assertIn('go back to waiting for the build (`PROJECT drafts --wait`) in this same turn', side['next'])
        self.assertNotIn('end your turn', side['next'])

    def test_a_step_s_own_chip_gives_the_line_to_end_on_and_its_title(self):
        # The person, 2026-11-18: our own wording leaked into the window. After a step's chip the agent wrote "Doing:
        # Combining duplicates, one metric per number." under a chip that said "Next: Combine duplicates, one metric per
        # number."; its widgets were titled "fixit_define_progress_chip_3". The chip's result gives the line to end on
        # (the newest thing made) and a title in the person's words.
        self.batch('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Gross Margin', 'definition': 'What the jobs kept. More.'}]}]})
        manifest = self.run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        # As the engine writes it: the step's own stage accepted ("ok") and the next step already asked for. The desktop
        # run's step chips got an empty line this way, and the turn went back for words.
        write(manifest, {'stages': stages + [{'stage': 'x1_ingest', 'counts': {'proposals': 1}, 'verdict': 'ok'},
                                             {'stage': 'x2_emit', 'counts': {}, 'verdict': 'ok'}]})
        done = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'drafted',
                               '--widget'], capture_output=True, text=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': ''})
        side = json.loads(done.stdout.split('side view: ', 1)[1])
        self.assertIn('with the title `1_metric_found`', side['next'])
        # d7, 2026-11-19: the widget tool was deferred; the session never loaded it and stripped each chip's HTML instead.
        self.assertIn('if it is deferred', side['next'])
        self.assertIn('go back to waiting for the build (`PROJECT drafts --wait`) in this same turn', side['next'])
        self.assertNotIn('Newest possible metric', side['next'])
        self.assertNotIn('Doing', side['next'])


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
