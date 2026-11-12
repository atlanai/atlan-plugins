"""The chat's side of a run: a one-line chip at each moment and a mini card at the four big ones."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, at, facts, make_run

sys.path.insert(0, str(SCRIPTS))
import visuals

PROJECT = SCRIPTS / 'project.py'


class Numbers(unittest.TestCase):
    def test_values_read_in_their_unit(self):
        self.assertEqual(visuals.number(118681.0, 'GBP'), '£118,681')
        self.assertEqual(visuals.number(-6360, 'GBP'), '-£6,360')
        self.assertEqual(visuals.number(0.0438946, 'fraction (0 to 1)'), '4.4%')
        self.assertEqual(visuals.number(1234567.4, 'USD'), '$1,234,567')
        self.assertEqual(visuals.number(2, 'jobs'), '2')
        self.assertEqual(visuals.number(None, 'GBP'), 'blank')

    def test_names_read_as_words(self):
        self.assertEqual(visuals.sentence('On-Time Rate'), 'On-time rate')
        self.assertEqual(visuals.table_name('site.work_logs'), 'work logs')

    def test_glyphs_show_how_far_and_how_much(self):
        self.assertEqual([visuals.ring(d, 4) for d in (0, 1, 2, 3, 4)], ['○', '◔', '◑', '◕', '●'])
        self.assertEqual(visuals.spark([1, 8, 4]), '▁█▄', 'plain text, in the reply\'s own colour: a code span is faint on a light terminal')
        self.assertEqual(visuals.spark([5]), '●', 'one value is not a series')


class Moments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def show(self, kind, run=None, widget=False, first=True, data=()):
        return visuals.render(kind, visuals.build_spec(kind, run or self.run, list(data)), widget, first)

    def test_sources_lead_with_the_past_queries_read(self):
        self.assertEqual(self.show('sources'), '● **3** past queries read · Power BI dashboard, 2 tables, 129 rows')

    def test_sources_without_history_lead_with_the_tables(self):
        run = make_run(self.root / 'b', history=False)
        self.assertEqual(self.show('sources', run), '● **2** tables loaded · 129 rows, Power BI dashboard')

    def test_checks_show_a_ring_and_what_needs_a_look(self):
        self.assertEqual(self.show('context'), '● **4/4** metrics and queries checked · on your data')
        demo = make_run(self.root / 'd', demo=True)
        self.assertEqual(self.show('context', demo, data=[facts()]), "● **4/4** metrics and queries checked · on Fixit's data")
        self.assertEqual(self.show('context', data=[facts()]), '● **4/4** metrics and queries checked · on your data',
                         "a demo's facts never name the person's own data")
        failing = make_run(self.root / 'f', failing=1)
        self.assertEqual(self.show('context', failing), '◕ **3/4** metrics and queries checked · on your data, 1 need a look',
                         "the save question's own count: what did not pass")
        warehouse = make_run(self.root / 'w', warehouse='snowflake')
        self.assertTrue(self.show('context', warehouse).endswith('on your Snowflake data'))

    def test_the_data_chip_follows_what_the_person_chose(self):
        dashboard_only = make_run(self.root / 'd', chosen={'bi': True, 'history': False})
        self.assertEqual(self.show('sources', dashboard_only), '● **2** tables loaded · 129 rows, Power BI dashboard',
                         'a history file left out of the choice is not read')
        history_only = make_run(self.root / 'h', chosen={'bi': False, 'history': True})
        self.assertNotIn('dashboard', self.show('sources', history_only))

    def test_a_database_bound_as_it_is_reads_as_its_tables(self):
        run = make_run(self.root / 'bound')
        (run / 'context' / 'data-preparation.json').unlink()
        self.assertEqual(self.show('sources', run), '● **3** past queries read · Power BI dashboard, 2 tables',
                         'an existing database bound for the checks is the data read; its rows were never counted')

    def test_the_tables_read_are_the_ones_the_person_chose(self):
        run = make_run(self.root / 'chosen')
        (run / 'context' / 'data-preparation.json').unlink()
        config = run / 'engine-config.json'
        config.write_text(json.dumps({**json.loads(config.read_text(encoding='utf-8')), 'tables': ['SITE.JOBS']}), encoding='utf-8')
        self.assertEqual(self.show('sources', run), '● **3** past queries read · Power BI dashboard, 1 table',
                         'a live run showed the 10 tables bound while its question said the 6 chosen')
        import side_view
        pieces = {n['t']: n for s in side_view.model(run)['steps'] for n in s['how']}
        self.assertEqual(pieces['Open the database read-only']['n'], '1 table')
        unchosen = make_run(self.root / 'early', saved=False, stage=('scope', 'running'))
        (unchosen / 'engine-config.json').unlink()
        self.assertNotIn('sources', side_view.due(unchosen), 'on own data the data read waits for what the person chose')

    def test_found_counts_only_what_it_names(self):
        run = make_run(self.root / 'db')
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        meta['sources'].append({'kind': 'sqlite', 'path': 'data/site.sqlite', 'status': 'needs_binding'})
        (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        (run / 'survey-cache.json').write_text(json.dumps({'sources': meta['sources']}), encoding='utf-8')
        self.assertEqual(self.show('found', run), '● **5** sources found · Power BI dashboard, 2 data files, 1 database, query history')

    def test_found_names_every_kind_the_survey_records_as_a_person_would(self):
        cases = (([('powerbi_project', 'model/a.tmdl')] * 6, '● **6** sources found · Power BI project (6 files)'),
                 ([('tableau', 'a.twb'), ('powerbi', 'b.pbix')], '● **2** sources found · Power BI dashboard, Tableau dashboard'),
                 ([('saved_sql', 'q.sql'), ('model_candidate', 'm.yml'), ('data_file', 'd.csv')],
                  '● **3** sources found · 1 model file, 1 data file, 1 saved SQL file'))
        for i, (sources, expected) in enumerate(cases):
            run = make_run(self.root / str(i), dashboard=False, history=False)
            meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            meta['sources'] = [{'kind': k, 'path': p, 'status': 'available'} for k, p in sources]
            (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
            (run / 'survey-cache.json').write_text(json.dumps({'sources': meta['sources']}), encoding='utf-8')
            self.assertEqual(self.show('found', run), expected, 'the host task list counts every recorded source')

    def test_a_warehouse_run_has_its_own_chips(self):
        run = make_run(self.root / 'w', warehouse='snowflake', chosen={'bi': False, 'history': True})
        self.assertEqual(self.show('found', run), '● **2** sources found · Snowflake warehouse, Power BI dashboard')
        self.assertEqual(self.show('sources', run), '● **3** past queries read · Snowflake, 2 tables', 'read in place, nothing loaded')

    def test_a_card_at_each_big_moment(self):
        cards = {kind: self.show(kind) for kind in visuals.CARDS}
        heads = {kind: card.split('\n', 1)[0] for kind, card in cards.items()}
        self.assertEqual(heads, {'build': '**2 metrics** · Built from your sources', 'saved': '**2 metrics, 2 queries** · Saved to Atlan Trident',
                                 'answer': '**\u2212£6,360** · Answer', 'edit': '**Late jobs 2 → 3** · Rule changed'},
                         'the number leads, bold, outside the block')
        self.assertIn('4 queries: 2 from the dashboard, 2 from past queries', cards['build'], 'counts that add up on their face')
        self.assertNotIn('question', cards['build'])
        self.assertIn('Live in Test workspace, where agents can find them', cards['saved'])
        self.assertNotIn('!', cards['saved'], 'one need-a-look number, said once, at the checks')
        self.assertIn('▶ Turbine base', cards['answer'], 'the row that answers is marked')
        for kind, card in cards.items():
            body = card.splitlines()[1:]
            self.assertNotIn('```', card, kind + ': plain lines, which a terminal prints as written in its own colours')
            self.assertTrue(all(line.startswith('  ') and len(line) <= visuals.WIDTH + 4 for line in body),
                            kind + ': indented under the headline, narrow enough for a split pane')
        for kind in ('build', 'saved'):
            self.assertLessEqual(len(cards[kind].splitlines()), 6, kind + ': a terminal shows six lines before a question whole')

    def test_cards_carry_no_step_label(self):
        for kind in visuals.CARDS:
            self.assertNotIn('step', self.show(kind), 'the chat line before a card already names its step')

    def test_a_later_answer_is_a_chip(self):
        self.assertEqual(self.show('answer', first=False), '▁▃█ **-£6,360** · Turbine base')

    def test_a_context_kept_local_is_a_chip(self):
        run = make_run(self.root / 'l', local=True)
        self.assertEqual(self.show('saved', run), '● **4/4** checked metrics and queries kept on this machine · nothing saved to Atlan')
        self.assertTrue(self.show('saved', make_run(self.root / 'lf', local=True, failing=1)).startswith('◕ **3/4** checked'),
                        'what passed, out of everything kept, which is what the ready block counts')

    def test_a_dashboard_proposal_is_a_chip(self):
        self.assertEqual(self.show('layout'), '● **2** tiles · dashboard proposed, with 1 more metric below them')
        (self.run / 'layout.json').write_text(json.dumps({'tiles': ['Gross Margin', 'Rework Cost'], 'more': ['Rework Cost', 'Q Margin']}),
                                              encoding='utf-8')
        self.assertEqual(self.show('layout'), '● **2** tiles · dashboard proposed, with 1 more metric and 1 query below them',
                         'a live run listed two queries there, which the chip called metrics')

    def test_nothing_shows_before_the_run_has_it(self):
        empty = self.root / 'empty'
        empty.mkdir()
        for kind in visuals.KINDS:
            self.assertEqual(visuals.render(kind, visuals.build_spec(kind, empty)), '', kind)
        self.assertEqual(self.show('connect'), '', 'the first moment opens the side view and does nothing else')
        self.assertEqual(self.show('found'), '● **4** sources found · Power BI dashboard, 2 data files, query history')

    def test_a_widget_card_is_small_and_self_contained(self):
        for kind in visuals.CARDS:
            html = self.show(kind, widget=True)
            self.assertTrue(html.startswith('<style>') and 'light-dark(' in html, kind)
            self.assertNotIn('@import', html, kind + ': a card loads nothing')
            self.assertNotRegex(html, r'https?://', kind)
            self.assertLess(len(html), 6000, kind + ': every character is an output token')

    def test_a_chip_is_a_small_widget_where_the_host_draws_one(self):
        for kind in ('found', 'sources', 'context', 'saved', 'layout'):
            html = visuals.render(kind, visuals.build_spec(kind, make_run(self.root / 'l', local=True) if kind == 'saved' else self.run), widget=True)
            self.assertTrue(html.startswith('<style>') and 'class="a c3"' in html, kind)
            self.assertNotRegex(html, r'https?://', kind)
            self.assertLess(len(html), 2500, kind + ': a chip costs a few seconds of writing, not a card\'s')
        self.assertIn('>3<', visuals.render('sources', visuals.build_spec('sources', self.run), widget=True), 'the number is the chip\'s own')
        self.assertIn('conic-gradient', visuals.render('context', visuals.build_spec('context', self.run), widget=True), 'a ring shows how far')

    def test_a_widget_escapes_what_it_shows(self):
        spec = visuals.build_spec('answer', self.run)
        spec['question'] = '<script>alert(1)</script>'
        html = visuals.render('answer', spec, widget=True)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_answer_bars_keep_their_sign(self):
        card = self.show('answer')
        self.assertIn('█│', card, 'a loss grows left of the zero line')
        self.assertIn('│█', card, 'a gain grows right of it')
        self.assertIn('\u2212£6,360', card, 'a minus sign, read aloud as minus')
        html = self.show('answer', widget=True)
        self.assertIn('class="n" style="right:', html)

    def test_an_answer_card_shows_every_row_its_file_holds(self):
        rows = [['Job %d' % n, n * 100 - 500] for n in range(9)]
        (self.run / 'answer.json').write_text(json.dumps({'question': 'Q', 'headline': 'Job 0: -500', 'rows': rows, 'highlight': 'Job 0'}), encoding='utf-8')
        card = self.show('answer')
        self.assertTrue(all(label in card for label, _ in rows), 'a card that hides rows makes the agent explain the gap')

    def test_an_answer_card_says_how_many_rules_it_does_not_name(self):
        spec = visuals.build_spec('answer', self.run)
        spec['proof'] = [{'name': n} for n in ('Gross margin', 'Rework cost', 'Labour cost', 'Materials cost', 'Waste')]
        self.assertIn('Based on Gross margin, Rework cost, +3 more', visuals.render('answer', spec))

    def test_a_long_line_in_a_card_wraps_and_keeps_every_word(self):
        spec = visuals.build_spec('answer', self.run)
        spec['proof'] = [{'name': 'Gross margin'}, {'name': 'Gross margin by job (Site performance page, dashboard tile)'}]
        card = visuals.render('answer', spec)
        self.assertNotIn('…', card)
        self.assertIn('tile)', card)

    def test_a_query_named_like_a_file_reads_as_words(self):
        spec = visuals.build_spec('answer', self.run)
        spec['proof'] = [{'name': 'Gross Margin'}, {'name': 'gross-margin-by-job'}]
        self.assertIn('Based on Gross margin, Gross margin by job', visuals.render('answer', spec))

    def test_a_long_job_name_keeps_its_words_in_the_bars(self):
        spec = visuals.build_spec('answer', self.run)
        spec['rows'] = [['Buttercup Vale Wind Turbine Base', -6360], ['Mrs Kettle', 3203]]
        self.assertIn('Buttercup Vale Wind Turbine', visuals.render('answer', spec))

    def test_data_files_name_the_field_that_is_wrong(self):
        self.assertIsNone(visuals.check_data('answer', {'question': 'Q', 'headline': 'H: 1', 'rows': [['a', 1]]}))
        self.assertEqual(visuals.check_data('answer', {'question': 'Q'}), 'answer.json needs `headline`.')
        self.assertIn('`proof`', visuals.check_data('answer', {'question': 'Q', 'headline': 'H', 'proof': ['Gross Margin']}))
        self.assertIn('`rows`', visuals.check_data('answer', {'question': 'Q', 'headline': 'H', 'rows': [{'a': 1}]}))
        self.assertEqual(visuals.check_data('answer', ['not', 'an', 'object']), "A moment's data is one JSON object.")
        self.assertIn('Only the', visuals.check_data('sources', {}))

    def test_query_history_reads_in_any_export_shape(self):
        for shape in ('csv', 'upper'):
            run = make_run(self.root / shape, history_format=shape)
            self.assertTrue(self.show('sources', run).startswith('● **3** past queries read'), shape)

    def test_one_past_query_reads_in_the_singular(self):
        log = self.root / 'source' / 'history' / 'querylog.jsonl'
        log.write_text(json.dumps({'sql': 'SELECT 1 FROM site.jobs', 'exec': 'n/a'}) + '\n', encoding='utf-8')
        self.assertTrue(self.show('sources').startswith('● **1** past query read'))

    def test_a_percent_reads_as_its_metric_defines_it(self):
        metrics = json.loads((self.run / 'context' / 'metrics.json').read_text(encoding='utf-8'))
        metrics.append({'name': 'On-Time Rate', 'definition': {'output': {'unit': 'percent'}, 'formula': 'SUM(a) / SUM(b)'}})
        (self.run / 'context' / 'metrics.json').write_text(json.dumps(metrics), encoding='utf-8')
        (self.run / 'edit.json').write_text(json.dumps({'metric': 'On-Time Rate', 'values': [['On-time rate', 0.25, 0.5, 'percent']]}), encoding='utf-8')
        self.assertTrue(self.show('edit').startswith('**On-time rate 25% → 50%**'), 'a share from 0 to 1, as the check shows it')
        answer = {'question': 'Q', 'headline': 'Job 1: 25%', 'unit': 'percent', 'rows': [['Job 1', 0.25]], 'proof': [{'name': 'On-Time Rate'}]}
        (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
        self.assertIn('25%', self.show('answer'), "the answer's own metric reads its share as a percent")
        answer.update(rows=[['Job 1', 57.1], ['Job 2', 42.9]], highlight='Job 1', headline='Job 1: 57.1%')
        (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
        card = self.show('answer')
        self.assertIn('57.1%', card, 'rows already in percent keep the scale the headline shows')
        self.assertNotIn('5710%', card)
        answer.update(rows=[['build', 80.0], ['repair', 60.0]], highlight='build', headline='On-time rate: 71.4%')
        (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
        card = self.show('answer')
        self.assertIn('80%', card, 'an overall headline over per-type rows: the rows say their own scale')
        self.assertNotIn('8000%', card)
        # The scale under which the rows sit nearest the headline's own number.
        for rows, headline, shown in (([['a', 0.35], ['b', 0.2], ['c', -1.2]], 'c: -120%', '−120%'),
                                      ([['a', 1.04], ['b', 0.7]], 'Overall: 87%', '104%'),
                                      ([['a', 0.4], ['b', 0.8]], 'b: 0.8%', '0.4%')):
            answer.update(rows=rows, highlight=rows[0][0], headline=headline)
            (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
            self.assertIn(shown, self.show('answer'), headline)
        answer.update(rows=[['Job 1', 0.25]], headline='Job 1: 25%')
        answer.pop('highlight')
        metrics.append({'name': 'Fee Take Rate', 'definition': {'output': {'unit': 'fraction of face value'}, 'formula': 'SUM(a) / SUM(b)'}})
        (self.run / 'context' / 'metrics.json').write_text(json.dumps(metrics), encoding='utf-8')
        answer.update(rows=[['Venue 1', 0.271]], headline='Venue 1: 27.1%', proof=[{'name': 'Fee Take Rate'}])
        (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
        self.assertIn('27.1%', self.show('answer').split('\n', 3)[3], 'a share named in words is still a share')
        answer.update(unit='GBP', rows=[['Job 1', 3804]], headline='Job 1: £3,804', proof=[{'name': 'On-Time Rate'}, {'name': 'Gross Margin'}])
        (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
        card = self.show('answer')
        self.assertIn('£3,804', card, 'pounds stay pounds when a listed rule is a share')
        self.assertNotIn('380400%', card)

    def test_each_changed_rule_reads_in_its_own_unit(self):
        metrics = json.loads((self.run / 'context' / 'metrics.json').read_text(encoding='utf-8'))
        metrics += [{'name': 'On-Time Rate', 'definition': {'output': {'unit': 'percent'}, 'formula': 'SUM(a) / SUM(b)'}},
                    {'name': 'Late Jobs', 'definition': {'output': {'unit': 'jobs'}, 'formula': 'COUNT(*)'}}]
        (self.run / 'context' / 'metrics.json').write_text(json.dumps(metrics), encoding='utf-8')
        (self.run / 'edit.json').write_text(json.dumps({'metric': 'On-Time Rate', 'values': [
            ['Late Jobs', 2, 3], ['On-Time Rate', 0.714, 0.571]]}), encoding='utf-8')
        self.assertTrue(self.show('edit').startswith('**Late Jobs 2 → 3**'), 'a live-run shape read "Late Jobs 200% → 300%"')
        self.assertEqual(visuals.build_spec('edit', self.run)['values'][1], ['On-Time Rate', '71.4%', '57.1%'])

    def test_a_check_entry_that_is_not_an_object_counts_as_not_run(self):
        checks = self.run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        entries['metric:Gross Margin'] = 'broken'
        checks.write_text(json.dumps(entries), encoding='utf-8')
        self.assertTrue(self.show('context').startswith('◕ **3/4** metrics and queries checked'))

    def test_a_card_shows_the_data_as_written(self):
        spec = visuals.build_spec('answer', self.run)
        spec['question'] = 'Why ``` and *this* here?'
        spec['rows'] = [['order_processing', 5], ['_hidden', 3]]
        card = visuals.render('answer', spec)
        self.assertIn('Why \\`\\`\\` and \\*this\\* here?', card, 'Markdown in the data cannot start code or emphasis')
        self.assertIn('order_processing', card, 'an underscore inside a word needs no escape')
        self.assertIn('\\_hidden', card)

    def test_an_empty_name_below_the_tiles_is_left_out(self):
        (self.run / 'layout.json').write_text(json.dumps({'tiles': ['Gross Margin'], 'more': [[], 'Rework Cost']}), encoding='utf-8')
        self.assertEqual(self.show('layout'), '● **1** tile · dashboard proposed, with 1 more metric below them')

    def test_a_malformed_data_file_is_ignored_not_fatal(self):
        (self.run / 'answer.json').write_text('[1, 2]')
        self.assertEqual(self.show('answer'), '')
        (self.run / 'answer.json').write_text('{"question": "Q", "headline": "H: 1", "rows": "nope"}')
        self.assertEqual(self.show('answer'), '', 'a file that fails its check is not shown')


class Helper(unittest.TestCase):
    """`PROJECT visual` prints what the chat shows, then names the side-view file to publish or push."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.tmp.name).resolve())

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, *args, stdin=None):
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run), *args], input=stdin,
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(done.stderr, '')
        return done.stdout

    def start_engine(self, run):
        """What `PROJECT engine-config` and the first engine gates leave: the engine run named, its manifest written."""
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({**meta, 'engine_run': str(run / 'engine')}), encoding='utf-8')
        (run / 'engine').mkdir()
        (run / 'engine' / 'manifest.json').write_bytes((self.run / 'engine' / 'manifest.json').read_bytes())

    def side(self, out):
        line = [l for l in out.splitlines() if l.startswith('side view: ')]
        return json.loads(line[0][len('side view: '):]) if line else None

    def test_a_rule_changed_leads_its_block_wherever_the_block_goes(self):
        self.project('visual', 'connect')
        line = self.side(self.project('visual', 'edit'))['next']
        self.assertIn("in the text of the question that asks to save the change, first, before the block", line,
                      'saved to Atlan Trident, a question follows: reply text before it never reached a terminal')
        self.run = make_run(Path(self.tmp.name).resolve() / 'kept', local=True)
        self.project('visual', 'connect')
        self.assertIn('Then show the text above', self.side(self.project('visual', 'edit'))['next'], 'kept here, the block is reply text')

    def test_each_moment_prints_its_chip_and_the_side_view_update(self):
        out = self.project('visual', 'connect')
        self.assertTrue(out.startswith('side view: '), 'nothing in the chat: the first moment only opens the side view')
        first = self.side(out)
        self.assertEqual(sorted(first), ['doc', 'next', 'page'])
        self.assertTrue(first['page'].endswith('side.html') and first['doc'].endswith('s01.json'))
        self.assertTrue(first['next'].startswith('Open the side view now'), 'one duty, so it is not dropped for another')
        out = self.project('visual', 'sources')
        self.assertIn('show the text above', self.side(out)['next'], 'own data reads its data mid-build, so the chip is reply text')
        self.assertIn('--widget', self.side(out)['next'], 'a host that draws widgets learns it at the first chip')
        self.assertTrue(out.startswith('● **3** past queries read'))
        self.assertNotIn('doc', self.side(out), 'the finished run already shows it: no new document')

    def test_the_next_line_names_every_duty_the_update_carries(self):
        first = self.side(self.project('visual', 'connect', '--widget'))['next']
        self.assertTrue(first.startswith('Open the side view now: publish `'), 'the first moment has one duty: the page')
        self.assertIn('side.html', first)
        self.assertNotIn('Draw it now', first, 'nothing to draw')
        meta = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        self.project('view', '--url', 'https://claude.ai/artifact/abc')
        at(self.run, 'scope')
        (self.run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        later = self.side(self.project('visual', 'sources', '--widget'))['next']
        self.assertTrue(later.startswith('Draw it now'))
        self.assertNotIn('before any other work', later)
        self.assertIn('Then push it to the side view: `ArtifactData` (load it with ToolSearch first if it is deferred) set, url `https://claude.ai/artifact/abc`', later)
        self.assertNotIn('Open the side view', later, 'the page is published once')

    def test_a_result_leads_with_its_side_view_lines(self):
        self.project('visual', 'connect')
        self.project('view', '--url', 'https://claude.ai/artifact/abc')
        (self.run / 'authorization.json').write_text(json.dumps({'workspace_id': 'w1', 'approved_plans': []}), encoding='utf-8')
        keys = list(json.loads(self.project('checklist')))
        side = [k for k in keys if k in ('side_view', 'visual')]
        self.assertTrue(side)
        self.assertEqual(keys[:len(side)], side, 'live runs cut long results short (head -c 200), and the push went with them')

    def test_a_skipped_page_is_offered_again_at_the_next_moment(self):
        fresh = Path(self.tmp.name).resolve() / 'fresh'

        def helper(*args):
            return subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), *args],
                                  capture_output=True, text=True, encoding='utf-8', timeout=60).stdout
        helper('survey', str(self.run.parent / 'source'))
        helper('visual', 'connect')
        self.start_engine(fresh)
        result = json.loads(helper('survey', str(self.run.parent / 'source')))
        self.assertNotIn('side_view', result, 'between moments there is nothing to push yet, and no offer to repeat')
        (fresh / 'context').mkdir(exist_ok=True)
        (fresh / 'context' / 'data-preparation.json').write_bytes((self.run / 'context' / 'data-preparation.json').read_bytes())
        line = self.side(helper('visual', 'sources'))['next']
        self.assertTrue(line.startswith('Open the side view now'), 'the next moment offers the page again, before its chip')
        self.assertIn('Then show the text above', line, 'then the chip, in the same reply')
        self.assertNotIn('view --local', line)

    def test_in_a_terminal_the_build_card_and_checks_chip_lead_the_save_question(self):
        for kind in ('connect', 'sources'):
            self.project('visual', kind)
        built = self.project('visual', 'build')
        self.assertTrue(built.startswith('side view: '), 'mid-build a terminal reply reaches no one; the card waits for the question')
        self.assertIn('If you have a `show_widget` tool, run this again with `--widget`', self.side(built)['next'],
                      'a desktop agent that forgot the flag would otherwise never show the build card')
        self.assertIn('build', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])
        checked = self.project('visual', 'context')
        shown = checked.split('\nside view: ')[0]
        self.assertTrue(shown.startswith('2 metrics · Built from your sources\n  4 queries: 2 from the dashboard'), 'the card leads')
        self.assertIn('\n\n● 4/4 metrics and queries checked · on your data', shown, 'then the chip, as the count line')
        self.assertNotIn('**', shown, "a question's text shows no Markdown")
        line = self.side(checked)['next']
        self.assertIn("in the save question's text in place of its count line", line, "a question's own text is always shown")
        self.assertIn('never reword them or add a number to them', line,
                      'a live run added "7 need a look" from another step to a chip that said every check passed')
        self.assertIn('the banner, the two lines under it and the question stay', line,
                      'the celebration keeps its banner (a live run read "after its first two lines" as "replace the top")')
        widget = self.project('visual', 'build', '--widget')
        self.assertTrue(widget.startswith('<style>'), 'a widget host draws the card as it happens')

    def test_the_saved_card_goes_in_the_ready_question(self):
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        out = self.project('visual', 'saved', '--input', '-', stdin=json.dumps({'workspace': 'Personal'}))
        self.assertTrue(out.startswith('2 metrics, 2 queries · Saved to Atlan Trident\n  Live in Personal'))
        self.assertLessEqual(len(out.split('\nside view: ')[0].splitlines()), 6)
        self.assertIn("in the ready question's text, first", self.side(out)['next'])

    def test_a_chip_right_before_a_question_goes_in_it(self):
        self.assertEqual(visuals.render('found', visuals.build_spec('found', self.run), plain=True),
                         '● 4 sources found · Power BI dashboard, 2 data files, query history')
        self.assertEqual(visuals.asked('found', False), "the \"Build it?\" question's text, first",
                         'one place, the one the skill names: a live run also put it in the use-case question before it')
        self.assertEqual(visuals.asked('sources', True), "the next native question's text, first", "a demo's sources come right before its choice")
        self.assertIsNone(visuals.asked('sources', False), 'own data reads its data mid-build')
        self.assertIsNone(visuals.asked('answer', False), 'an answer ends its turn, which a terminal never folds')
        card = visuals.render('answer', visuals.build_spec('answer', self.run), plain=True)
        self.assertNotIn('\\', card, "nothing escaped in a question's text")

    def test_a_widget_goes_to_the_inline_tool(self):
        for kind in ('connect', 'sources', 'build'):
            self.project('visual', kind)
        out = self.project('visual', 'context', '--widget')
        self.assertTrue(out.startswith('<style>'))
        self.assertIn('class="a c3"', out)
        self.assertIn('show_widget', self.side(out)['next'])
        self.assertIn("Never put it in a question's text", self.side(out)['next'])

    def test_a_helper_result_names_the_moments_the_run_has_reached(self):
        import side_view
        self.assertEqual(side_view.due(self.run), ['connect', 'sources', 'build', 'context'], 'in the order the run reached them')
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        self.assertEqual(side_view.due(self.run), [], 'nothing is due once each is recorded')
        fresh = Path(self.tmp.name).resolve() / 'fresh'
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), 'survey', str(self.run.parent / 'source')],
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertIn('`visual connect`', json.loads(done.stdout)['visual'], 'the survey that makes the run names its first moment')

    def test_the_found_chip_waits_for_the_select_decision(self):
        import side_view
        run = make_run(Path(self.tmp.name).resolve() / 'early', saved=False)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        at(run, 'inspect')
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        self.assertNotIn('found', side_view.due(run), 'the use case comes first; nothing to choose yet')
        (run / 'interaction.json').write_text(json.dumps(interaction), encoding='utf-8')
        self.assertIn('found', side_view.due(run), 'right before choosing what to build from')
        demo = make_run(Path(self.tmp.name).resolve() / 'demo', demo=True, saved=False, stage=('inspect', 'complete'))
        self.assertNotIn('found', side_view.due(demo), 'a demo shows its sources chip instead')

    def test_the_checks_moment_waits_for_the_last_check(self):
        import side_view
        checks = self.run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: v for k, v in entries.items() if k != 'sql_query:q-jobs'}), encoding='utf-8')
        self.assertNotIn('context', side_view.due(self.run), 'three of four checked is not every check')
        checks.write_text(json.dumps(entries), encoding='utf-8')
        self.assertIn('context', side_view.due(self.run))

    def test_a_helper_result_names_a_newer_side_view_update_to_push(self):
        fresh = Path(self.tmp.name).resolve() / 'fresh'

        def helper(*args):
            return subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), *args],
                                  capture_output=True, text=True, encoding='utf-8', timeout=60).stdout
        helper('survey', str(self.run.parent / 'source'))
        helper('visual', 'connect')
        (fresh / 'context').mkdir(exist_ok=True)
        (fresh / 'context' / 'data-preparation.json').write_bytes((self.run / 'context' / 'data-preparation.json').read_bytes())
        helper('visual', 'sources')
        helper('view', '--url', 'https://claude.ai/artifact/abc')
        self.start_engine(fresh)
        push = json.loads(helper('survey', str(self.run.parent / 'source')))['side_view']
        self.assertIn('https://claude.ai/artifact/abc', push)
        self.assertRegex(push, r'doc_id `s02`', 'the next document, for the page to tick the engine gates that just finished')
        self.assertNotIn('side_view', json.loads(helper('survey', str(self.run.parent / 'source'))), 'nothing new: nothing to push')

    def test_a_moment_too_early_records_nothing(self):
        self.project('visual', 'connect')
        meta = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        (self.run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'execution'}), encoding='utf-8')
        (self.run / 'context' / 'data-preparation.json').rename(self.run / 'context' / 'later.json')
        self.assertEqual(self.project('visual', 'sources'), '', 'no tables loaded or bound yet: nothing to show, nothing recorded')
        (self.run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        (self.run / 'context' / 'later.json').rename(self.run / 'context' / 'data-preparation.json')
        self.assertTrue(self.project('visual', 'sources').startswith('● **3** past queries read'))
        moments = json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']
        self.assertEqual([m['kind'] for m in moments], ['connect', 'sources'])

    def test_a_context_kept_local_shows_its_chip(self):
        self.run = make_run(Path(self.tmp.name).resolve() / 'local', local=True)
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        out = self.project('visual', 'saved')
        self.assertTrue(out.startswith('● 4/4 checked metrics and queries kept on this machine'), "plain, for the ready question's text")
        self.assertIn('saved', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])

    def test_an_answer_without_its_file_shows_and_records_nothing(self):
        for kind in ('connect', 'sources', 'build', 'context', 'saved'):
            self.project('visual', kind)
        (self.run / 'answer.json').unlink()
        self.assertEqual(self.project('visual', 'answer'), '')
        self.assertNotIn('answer', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])

    def test_a_widget_rerun_keeps_the_first_answer_card(self):
        for kind in ('connect', 'sources', 'build', 'context', 'saved'):
            self.project('visual', kind)
        self.assertTrue(self.project('visual', 'answer').startswith('**'), 'the first answer is a card')
        self.assertIn('class="a mc"', self.project('visual', 'answer', '--widget'),
                      'the same answer drawn again as a widget is still the card, not a chip')
        (self.run / 'answer.json').write_text(json.dumps({'question': 'Another', 'headline': 'Barn: £1', 'rows': [['Barn', 1]]}), encoding='utf-8')
        self.assertTrue(self.project('visual', 'answer').startswith('● ') or self.project('visual', 'answer').startswith('▁'),
                        'a different, later answer is a chip')

    def test_numbers_in_a_data_file_must_be_finite(self):
        blocked = json.loads(self.project('visual', 'answer', '--input', '-', stdin='{"question": "Q", "headline": "H: 1", "rows": [["a", NaN]]}'))
        self.assertIn('`rows`', blocked['message'])

    def test_output_is_utf8_in_any_console(self):
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run), 'visual', 'sources'], capture_output=True,
                              timeout=60, env=dict(os.environ, PYTHONIOENCODING='cp1252'))
        self.assertTrue(done.stdout.decode('utf-8').startswith('● **3** past queries read'))

    def test_a_folder_that_is_not_a_run_is_refused(self):
        other = Path(self.tmp.name) / 'elsewhere'
        other.mkdir()
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(other), 'visual', 'sources'], capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(json.loads(done.stdout)['status'], 'blocked')
        self.assertFalse((other / 'view').exists())

    def test_a_bad_data_file_is_refused_before_it_is_written(self):
        blocked = json.loads(self.project('visual', 'answer', '--input', '-', stdin=json.dumps({'question': 'Q', 'proof': ['x']})))
        self.assertEqual(blocked['message'], 'answer.json needs `headline`.')
        self.assertEqual(json.loads((self.run / 'answer.json').read_text(encoding='utf-8'))['headline'], 'Turbine base: -£6,360', 'the earlier file stays')

    def test_a_moment_data_file_arrives_on_stdin(self):
        out = self.project('visual', 'answer', '--widget', '--input', '-', stdin=json.dumps({'question': 'Q', 'headline': 'Barn: £1', 'rows': [['Barn', 1]]}))
        self.assertTrue(out.startswith('<style>'))
        self.assertEqual(json.loads((self.run / 'answer.json').read_text(encoding='utf-8'))['headline'], 'Barn: £1')

    def test_the_published_address_is_remembered(self):
        meta = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        (self.run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        at(self.run, 'scope')
        self.project('visual', 'connect')
        named = json.loads(self.project('view', '--url', 'https://claude.ai/artifact/abc'))['side_view']
        self.assertTrue(named.startswith('Push it to the side view') and 'doc_id `s01`' in named, 'recording the page names the first push')
        (self.run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        at(self.run, 'check')
        self.assertEqual(self.side(self.project('visual', 'build'))['url'], 'https://claude.ai/artifact/abc', 'the next update names it')
        blocked = json.loads(self.project('view', '--url', 'https://example.com/x'))
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('claude.ai', blocked['message'])


if __name__ == '__main__':
    unittest.main()
