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
from analytics_run import SCRIPTS, at, facts, make_run, stop_pages

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
        self.assertEqual(visuals.number(2.21, 'ratio'), '2.2×', 'a markup of 2.21 is not 221%')
        self.assertEqual(visuals.number(0.271, 'ratio'), '27.1%')
        self.assertEqual(visuals.number(86.09, 'USD per ticket'), '$86.09', 'a currency per unit keeps its sign')
        self.assertEqual(visuals.number(None, 'GBP'), 'blank')

    def test_names_read_as_words(self):
        self.assertEqual(visuals.sentence('On-Time Rate'), 'On-time rate')
        self.assertEqual(visuals.table_name('site.work_logs'), 'work logs')

    def test_a_multiple_reads_as_a_multiple(self):
        self.assertEqual(visuals.number(2.21, 'times face value'), '2.2×')

    def test_glyphs_show_how_far_and_how_much(self):
        self.assertEqual([visuals.ring(d, 4) for d in (0, 1, 2, 3, 4)], ['○', '◔', '◑', '◕', '●'])
        self.assertEqual(visuals.spark([1, 8, 4]), '▁█▄', 'plain text, in the reply\'s own colour: a code span is faint on a light terminal')
        self.assertEqual(visuals.spark([5]), '▪', 'one value is not a series, and no ring')


class Moments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def show(self, kind, run=None, widget=False, first=True, data=()):
        return visuals.render(kind, visuals.build_spec(kind, run or self.run, list(data)), widget, first)

    def test_the_data_chip_leads_with_the_rows_loaded(self):
        self.assertEqual(self.show('sources'), '▤ **129 rows** of your data · 2 tables, 3 past queries, Power BI dashboard')

    def test_sources_without_history_lead_with_the_tables(self):
        run = make_run(self.root / 'b', history=False)
        self.assertEqual(self.show('sources', run), '▤ **129 rows** of your data · 2 tables, Power BI dashboard')

    def test_checks_show_what_ran_and_what_needs_a_look(self):
        self.assertEqual(self.show('context'), '✓ **Every metric and query ran** · on your data · Gross margin £118,681')
        demo = make_run(self.root / 'd', demo=True)
        self.assertEqual(self.show('context', demo, data=[facts()]), "✓ **Every metric and query ran** · on Fixit's data · Gross margin £118,681")
        self.assertEqual(self.show('context', data=[facts()]), '✓ **Every metric and query ran** · on your data · Gross margin £118,681',
                         "a demo's facts never name the person's own data")
        failing = make_run(self.root / 'f', failing=1)
        self.assertEqual(self.show('context', failing), '△ **3 ran cleanly** · 1 need a look · on your data',
                         "the save question's own count: what did not pass")
        warehouse = make_run(self.root / 'w', warehouse='snowflake')
        self.assertIn('on your Snowflake data', self.show('context', warehouse))

    def test_the_data_chip_follows_what_the_person_chose(self):
        dashboard_only = make_run(self.root / 'd', chosen={'bi': True, 'history': False})
        self.assertEqual(self.show('sources', dashboard_only), '▤ **129 rows** of your data · 2 tables, Power BI dashboard',
                         'a history file left out of the choice is not read')
        history_only = make_run(self.root / 'h', chosen={'bi': False, 'history': True})
        self.assertNotIn('dashboard', self.show('sources', history_only))

    def test_a_database_bound_as_it_is_reads_as_its_tables(self):
        run = make_run(self.root / 'bound')
        (run / 'context' / 'data-preparation.json').unlink()
        self.assertEqual(self.show('sources', run), '▤ **2 tables** loaded · 3 past queries, Power BI dashboard',
                         'an existing database bound for the checks is the data read; its rows were never counted')

    def test_the_tables_read_are_the_ones_the_person_chose(self):
        run = make_run(self.root / 'chosen')
        (run / 'context' / 'data-preparation.json').unlink()
        config = run / 'engine-config.json'
        config.write_text(json.dumps({**json.loads(config.read_text(encoding='utf-8')), 'tables': ['SITE.JOBS']}), encoding='utf-8')
        self.assertEqual(self.show('sources', run), '▤ **1 table** loaded · 3 past queries, Power BI dashboard',
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
        self.assertEqual(self.show('found', run), '◇ **5 sources** · Power BI dashboard, 2 data files, 1 database, query history')

    def test_found_names_every_kind_the_survey_records_as_a_person_would(self):
        cases = (([('powerbi_project', 'model/a.tmdl')] * 6, '◇ **6 sources** · Power BI project (6 files)'),
                 ([('tableau', 'a.twb'), ('powerbi', 'b.pbix')], '◇ **2 sources** · Power BI dashboard, Tableau dashboard'),
                 ([('saved_sql', 'q.sql'), ('model_candidate', 'm.yml'), ('data_file', 'd.csv')],
                  '◇ **3 sources** · 1 model file, 1 data file, 1 saved SQL file'))
        for i, (sources, expected) in enumerate(cases):
            run = make_run(self.root / str(i), dashboard=False, history=False)
            meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            meta['sources'] = [{'kind': k, 'path': p, 'status': 'available'} for k, p in sources]
            (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
            (run / 'survey-cache.json').write_text(json.dumps({'sources': meta['sources']}), encoding='utf-8')
            self.assertEqual(self.show('found', run), expected, 'the host task list counts every recorded source')

    def test_a_warehouse_run_has_its_own_chips(self):
        run = make_run(self.root / 'w', warehouse='snowflake', chosen={'bi': False, 'history': True})
        self.assertEqual(self.show('found', run), '◇ **2 sources** · Snowflake warehouse, Power BI dashboard')
        self.assertEqual(self.show('sources', run), '▤ **3 past queries** read · Snowflake, 2 tables', 'read in place, nothing loaded')

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

    def test_nothing_shows_before_the_run_has_it(self):
        empty = self.root / 'empty'
        empty.mkdir()
        for kind in visuals.KINDS:
            self.assertEqual(visuals.render(kind, visuals.build_spec(kind, empty)), '', kind)
        self.assertEqual(self.show('connect'), '', 'the first moment opens the side view and does nothing else')
        self.assertEqual(self.show('found'), '◇ **4 sources** · Power BI dashboard, 2 data files, query history')

    def test_a_widget_card_is_small_and_self_contained(self):
        for kind in visuals.CARDS:
            html = self.show(kind, widget=True)
            self.assertTrue(html.startswith('<style>') and 'light-dark(' in html, kind)
            self.assertNotIn('@import', html, kind + ': a card loads nothing')
            self.assertNotRegex(html, r'https?://', kind)
            self.assertLess(len(html), 6000, kind + ': every character is an output token')

    def test_a_chip_is_a_small_widget_where_the_host_draws_one(self):
        for kind in ('found', 'sources', 'context'):
            html = visuals.render(kind, visuals.build_spec(kind, self.run), widget=True)
            self.assertTrue(html.startswith('<style>') and 'class="a oc"' in html, kind)
            self.assertNotRegex(html, r'https?://', kind)
            self.assertLess(len(html), 2500, kind + ': a chip costs a few seconds of writing, not a card\'s')
        self.assertIn('<b>129 rows</b>', visuals.render('sources', visuals.build_spec('sources', self.run), widget=True), 'the outcome is the chip\'s own')
        self.assertIn('class="m ok"', visuals.render('context', visuals.build_spec('context', self.run), widget=True), 'its own mark, no ring')

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

    def test_an_answer_without_a_unit_takes_its_rule_s_unit(self):
        # A live desktop run's answer file left out the unit: the rows read 201,873 under a $302,118 headline.
        answer = json.loads((self.run / 'answer.json').read_text(encoding='utf-8'))
        answer.pop('unit', None)
        (self.run / 'answer.json').write_text(json.dumps(answer), encoding='utf-8')
        self.assertIn('£16,137', self.show('answer'), "the rows read in the unit of the rule the answer used")

    def test_a_bare_currency_takes_the_headline_s_sign(self):
        answer = json.loads((self.run / 'answer.json').read_text(encoding='utf-8'))
        (self.run / 'answer.json').write_text(json.dumps(dict(answer, unit='currency', headline='Turbine base: -$6,360')), encoding='utf-8')
        self.assertIn('$16,137', self.show('answer'))

    def test_an_answer_card_shows_every_row_its_file_holds(self):
        rows = [['Job %d' % n, n * 100 - 500] for n in range(9)]
        (self.run / 'answer.json').write_text(json.dumps({'question': 'Q', 'headline': 'Job 0: -500', 'rows': rows, 'highlight': 'Job 0'}), encoding='utf-8')
        card = self.show('answer')
        self.assertTrue(all(label in card for label, _ in rows), 'a card that hides rows makes the agent explain the gap')

    def test_an_answer_card_says_how_many_rules_it_does_not_name(self):
        spec = visuals.build_spec('answer', self.run)
        spec['proof'] = [{'name': n} for n in ('Gross margin', 'Rework cost', 'Labour cost', 'Materials cost', 'Waste')]
        card = visuals.render('answer', spec)
        self.assertIn('Gross margin, from 1 dashboard tile and 1 past query', card, 'each rule named says where it came from')
        self.assertIn('Rework cost, from 1 past query', card)
        self.assertIn('+3 more behind this answer', card)

    def test_a_long_line_in_a_card_wraps_and_keeps_every_word(self):
        spec = visuals.build_spec('answer', self.run)
        spec['proof'] = [{'name': 'Gross margin'}, {'name': 'Gross margin by job (Site performance page, dashboard tile)'}]
        card = visuals.render('answer', spec)
        self.assertNotIn('…', card)
        self.assertIn('tile)', card)

    def test_a_query_named_like_a_file_reads_as_words(self):
        spec = visuals.build_spec('answer', self.run)
        spec['proof'] = [{'name': 'Gross Margin'}, {'name': 'gross-margin-by-job'}]
        self.assertIn('Gross margin by job, a saved query', visuals.render('answer', spec))

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
            self.assertIn('3 past queries', self.show('sources', run), shape)

    def test_one_past_query_reads_in_the_singular(self):
        log = self.root / 'source' / 'history' / 'querylog.jsonl'
        log.write_text(json.dumps({'sql': 'SELECT 1 FROM site.jobs', 'exec': 'n/a'}) + '\n', encoding='utf-8')
        self.assertIn('2 tables, 1 past query, Power BI', self.show('sources'))

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
        at(self.run, 'preview')
        self.assertTrue(self.show('context').startswith('△ **3 ran cleanly** · 1 need a look'))

    def test_a_card_shows_the_data_as_written(self):
        spec = visuals.build_spec('answer', self.run)
        spec['question'] = 'Why ``` and *this* here?'
        spec['rows'] = [['order_processing', 5], ['_hidden', 3]]
        card = visuals.render('answer', spec)
        self.assertIn('Why \\`\\`\\` and \\*this\\* here?', card, 'Markdown in the data cannot start code or emphasis')
        self.assertIn('order_processing', card, 'an underscore inside a word needs no escape')
        self.assertIn('\\_hidden', card)

    def test_a_malformed_data_file_is_ignored_not_fatal(self):
        (self.run / 'answer.json').write_text('[1, 2]')
        self.assertEqual(self.show('answer'), '')
        (self.run / 'answer.json').write_text('{"question": "Q", "headline": "H: 1", "rows": "nope"}')
        self.assertEqual(self.show('answer'), '', 'a file that fails its check is not shown')


class Helper(unittest.TestCase):
    """`PROJECT visual` prints what the chat shows, then what to do with it; `connect` opens the side page."""
    host = ''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = make_run(Path(self.tmp.name).resolve())

    def tearDown(self):
        stop_pages(Path(self.tmp.name))
        self.tmp.cleanup()

    def env(self):
        # No host named: the helper opens no browser and asks for no pane, whatever session runs the tests.
        return dict(os.environ, CLAUDE_CODE_ENTRYPOINT=self.host)

    def project(self, *args, stdin=None, run=None):
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(run or self.run), *args], input=stdin,
                              capture_output=True, text=True, encoding='utf-8', timeout=60, env=self.env())
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
        self.assertIn("in the text of the question that asks to save the change, first, in place of the before and after block", line,
                      'saved to Atlan Trident, a question follows: reply text before it never reached a terminal')
        self.run = make_run(Path(self.tmp.name).resolve() / 'kept', local=True)
        self.project('visual', 'connect')
        kept = self.side(self.project('visual', 'edit'))['next']
        self.assertTrue(kept.startswith('Show the text above'), 'kept here, the block is reply text')
        self.assertIn('no code fence', kept, 'a live run wrapped the answer card in a code fence')

    def test_each_moment_prints_its_chip_and_the_side_view_update(self):
        out = self.project('visual', 'connect')
        self.assertTrue(out.startswith('side view: '), 'nothing in the chat: the first moment only opens the side page')
        first = self.side(out)
        self.assertEqual(sorted(first), ['next', 'url'])
        self.assertRegex(first['url'], r'^http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{16,}/$', 'this machine only, behind a key')
        self.assertTrue(first['next'].startswith('Say nothing about the side page now: the next chip in a question names its address'), 'a host with no pane hears it in the next chip')
        self.assertIn('never reload, rebuild or push to it', first['next'], 'the page reads the run itself')
        out = self.project('visual', 'sources')
        self.assertTrue(self.side(out)['next'].startswith('Show the text above'), 'own data reads its data mid-build, so the chip is reply text')
        self.assertIn('--widget', self.side(out)['next'], 'a host that draws widgets learns it at the first chip')
        self.assertTrue(out.startswith('▤ **129 rows** of your data'))

    def test_the_next_line_names_every_duty_the_update_carries(self):
        first = self.side(self.project('visual', 'connect', '--widget'))['next']
        self.assertTrue(first.startswith("Open the side page now in the app's browser pane"), 'the first moment has one duty: the page')
        self.assertNotIn('Draw it now', first, 'nothing to draw')
        meta = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        at(self.run, 'scope')
        (self.run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        later = self.side(self.project('visual', 'sources', '--widget'))['next']
        self.assertTrue(later.startswith('Draw it now'))
        self.assertNotIn('ArtifactData', later, 'nothing is pushed: the page reads the run')

    def test_a_page_nobody_opened_is_named_until_it_is_open(self):
        self.host = 'claude-desktop'
        self.project('visual', 'connect')
        (self.run / 'authorization.json').write_text(json.dumps({'workspace_id': 'w1', 'approved_plans': []}), encoding='utf-8')
        keys = list(json.loads(self.project('checklist')))
        self.assertEqual(keys[0], 'side_page', 'it leads: live runs cut long results short (head -c 200)')
        line = self.side(self.project('visual', 'sources'))['next']
        self.assertTrue(line.startswith('Show the text above'), 'the moment first')
        self.assertIn("Then open the side page now in the app's browser pane", line, 'then the page nobody has opened')
        url = json.loads((self.run / 'view' / 'server.json').read_text(encoding='utf-8'))['url']
        from urllib.request import urlopen
        with urlopen(url + 'state', timeout=10) as page:
            self.assertEqual(json.loads(page.read())['flow'], 'build')
        self.assertNotIn('side_page', json.loads(self.project('checklist')), 'once the page has been read, it is not named again')

    def test_a_page_that_stopped_comes_back_at_its_address(self):
        import side_page
        self.project('visual', 'connect')
        record = json.loads((self.run / 'view' / 'server.json').read_text(encoding='utf-8'))
        url = record['url']
        # The server ends on its own when nobody looks at it (here, as a killed process would).
        os.kill(record['pid'], 15)
        import time
        for _ in range(50):
            if side_page.running(self.run) is None:
                break
            time.sleep(0.1)
        self.assertIsNone(side_page.running(self.run))
        self.assertNotIn('side_page', json.loads(self.project('checklist')), 'the same address: an open tab reconnects by itself')
        self.assertEqual(side_page.running(self.run), url)

    def test_in_a_terminal_the_first_chip_in_a_question_names_the_page(self):
        self.project('visual', 'connect')
        url = json.loads((self.run / 'view' / 'server.json').read_text(encoding='utf-8'))['url']
        found = self.project('visual', 'found').split('\nside view: ')[0].rstrip()
        self.assertTrue(found.startswith('◇ 4 sources'))
        self.assertTrue(found.endswith('↗ Follow the build, and how each metric is reached: ' + url),
                        'the address reaches the person in a question\'s text, which is always shown')
        self.assertNotIn('Follow the build', self.project('visual', 'found', '--widget'), 'a widget host has the page in its pane')

    def test_in_a_terminal_the_question_carries_the_checks_and_saved_lines_once(self):
        for kind in ('connect', 'sources', 'build'):
            self.project('visual', kind)
        checked = self.project('visual', 'context')
        self.assertTrue(checked.startswith('side view: '), 'the lines are part of the save question, printed there once')
        self.assertIn('PROJECT question save', self.side(checked)['next'])
        asked = json.loads(self.project('question', 'save'))['question']
        self.assertEqual(asked.count('Every metric and query ran'), 1)
        self.assertIn('context', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])
        saved = self.project('visual', 'saved', '--input', '-', stdin=json.dumps({'workspace': 'Personal'}))
        self.assertTrue(saved.startswith('side view: '))
        self.assertIn('PROJECT question ready', self.side(saved)['next'])

    def test_view_names_the_page_and_stops_it(self):
        import side_page
        shown = json.loads(self.project('view'))
        self.assertEqual(shown['url'], side_page.running(self.run))
        self.assertEqual(json.loads(self.project('view', '--stop')), {'stopped': True})
        self.assertIsNone(side_page.running(self.run))

    def test_in_a_terminal_the_build_card_and_checks_chip_lead_the_save_question(self):
        for kind in ('connect', 'sources'):
            self.project('visual', kind)
        built = self.project('visual', 'build')
        self.assertTrue(built.startswith('side view: '), 'mid-build a terminal reply reaches no one; the card waits for the question')
        self.assertIn('If you have a `show_widget` tool, run this again with `--widget`', self.side(built)['next'],
                      'a desktop agent that forgot the flag would otherwise never show the build card')
        self.assertIn('build', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])
        line = self.side(self.project('visual', 'context'))['next']
        self.assertIn('PROJECT question save', line, 'in a terminal the checks are part of the question the helper composes')
        import side_view
        self.assertIn('context', side_view.due(self.run), 'not shown until the question is composed: a skipped question is named again')
        asked = json.loads(self.project('question', 'save'))
        shown = asked['question']
        self.assertIn('2 metrics · Built from your sources\n  4 queries: 2 from the dashboard', shown, 'the card leads')
        self.assertIn('\n\n✓ Every metric and query ran · on your data · Gross margin £118,681', shown, 'then the chip')
        self.assertNotIn('**', shown, "a question's text shows no Markdown")
        self.assertIn('never reword or shorten it', asked['next'],
                      'a live run added "7 need a look" from another step to a chip that said every check passed')
        self.assertTrue(shown.startswith(' *   .'), 'the celebration keeps its banner')
        widget = self.project('visual', 'build', '--widget')
        self.assertTrue(widget.startswith('<style>'), 'a widget host draws the card as it happens')

    def test_the_saved_card_goes_in_the_ready_question(self):
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        out = self.project('visual', 'saved', '--input', '-', stdin=json.dumps({'workspace': 'Personal'}))
        self.assertIn('PROJECT question ready', self.side(out)['next'])
        asked = json.loads(self.project('question', 'ready'))['question']
        self.assertTrue(asked.startswith('2 metrics, 2 queries · Saved to Atlan Trident\n  Live in Personal'))
        self.assertLessEqual(len(asked.split('\n\n')[0].splitlines()), 6, 'the card keeps to six lines in a question')

    def test_a_chip_right_before_a_question_goes_in_it(self):
        self.assertEqual(visuals.render('found', visuals.build_spec('found', self.run), plain=True),
                         '◇ 4 sources · Power BI dashboard, 2 data files, query history')
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
        self.assertIn('class="a oc"', out)
        self.assertIn('show_widget', self.side(out)['next'])
        self.assertIn("Never put it in a question's text", self.side(out)['next'])

    def test_a_helper_result_names_the_moments_the_run_has_reached(self):
        import side_view
        self.assertEqual(side_view.due(self.run), ['connect', 'sources', 'group', 'tiles', 'described', 'metrics', 'rules', 'links',
                                                   'build', 'context'], 'in the order the run reached them')
        for kind in ('connect', 'sources', 'group', 'tiles', 'described', 'metrics', 'rules', 'links', 'build', 'context'):
            self.project('visual', kind)
        self.assertEqual(side_view.due(self.run), ['context'], 'in a terminal the checks are shown by the save question')
        self.project('question', 'save')
        self.assertEqual(side_view.due(self.run), [], 'nothing is due once each is shown')
        fresh = Path(self.tmp.name).resolve() / 'fresh'
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(fresh), 'survey', str(self.run.parent / 'source')],
                              capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertIn('`visual connect`', json.loads(done.stdout)['visual'], 'the survey that makes the run names its first moment')

    def test_the_found_chip_waits_for_the_select_decision(self):
        import side_view
        run = make_run(Path(self.tmp.name).resolve() / 'early', saved=False)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        (run / 'engine' / 'manifest.json').rename(run / 'engine' / 'manifest.later.json')
        at(run, 'inspect')
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        self.assertNotIn('found', side_view.due(run), 'the use case comes first; nothing to choose yet')
        (run / 'interaction.json').write_text(json.dumps(interaction), encoding='utf-8')
        self.assertIn('found', side_view.due(run), 'right before choosing what to build from')
        demo = make_run(Path(self.tmp.name).resolve() / 'demo', demo=True, saved=False, stage=('inspect', 'complete'))
        self.assertNotIn('found', side_view.due(demo), 'a demo shows its sources chip instead')

    def test_the_build_moment_waits_for_the_engine_to_finish(self):
        # A live desktop run drew the build card 18 minutes in, while the engine was still linking metrics (22 in all).
        import side_view
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (self.run / 'context' / name).unlink()
        manifest = self.run / 'engine' / 'manifest.json'
        full = json.loads(manifest.read_text(encoding='utf-8'))
        manifest.write_text(json.dumps({'stages': [x for x in full['stages'] if x['stage'] != 'x4_ingest']}), encoding='utf-8')
        self.assertNotIn('build', side_view.due(self.run), 'metrics merged is not the build finished')
        manifest.write_text(json.dumps(full), encoding='utf-8')
        self.assertIn('build', side_view.due(self.run))

    def test_the_checks_chip_waits_for_every_verdict(self):
        # A desktop chip drawn mid-checks read "24/81 checked, 57 need a look" and the final one was never drawn.
        checks = self.run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: (v if k.startswith('metric:') else {'status': 'schema_checked'}) for k, v in entries.items()}),
                          encoding='utf-8')
        at(self.run, 'check', 'running')
        self.assertEqual(self.project('visual', 'connect').count('side view'), 1)
        self.assertNotIn('<style>', self.project('visual', 'context', '--widget'), 'nothing true to show yet')
        moments = json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']
        self.assertNotIn('context', [m['kind'] for m in moments], 'so it is named again once every check has run')

    def test_the_checks_moment_waits_for_the_last_check(self):
        import side_view
        checks = self.run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: v for k, v in entries.items() if k != 'sql_query:q-jobs'}), encoding='utf-8')
        at(self.run, 'check', 'running')
        self.assertNotIn('context', side_view.due(self.run), 'three of four checked is not every check')
        checks.write_text(json.dumps(entries), encoding='utf-8')
        self.assertIn('context', side_view.due(self.run))
        checks.write_text(json.dumps({k: v for k, v in entries.items() if k != 'sql_query:q-jobs'}), encoding='utf-8')
        at(self.run, 'preview')
        self.assertIn('context', side_view.due(self.run), 'once the flow moves on, the one never run needs a look')

    def test_the_checks_wait_for_their_samples_whatever_the_check_step_wrote(self):
        # The structural check writes "check: complete" before any sample runs; a chip drawn then read "0/4 checked,
        # 4 need a look", and the save question was composed mid-checks.
        import side_page
        import side_view
        (self.run / 'context' / 'candidate-checks.json').unlink()
        self.project('check')
        progress = json.loads((self.run / 'progress.json').read_text(encoding='utf-8'))
        self.assertEqual(progress['stage'], 'check', 'the progress the real check step leaves')
        checks = self.run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        # As on a real run: each item waits for its sample after the structural pass, and one sample has run.
        first = next(iter(entries))
        checks.write_text(json.dumps({k: dict(v, status='executed' if k == first else 'schema_checked', errors=[])
                                      for k, v in entries.items()}), encoding='utf-8')
        self.assertNotIn('context', side_view.due(self.run))
        self.assertNotIn('<style>', self.project('visual', 'context', '--widget'))
        self.assertIn('every check has run', self.project('question', 'save'))
        self.assertNotIn('Where should this context live', json.dumps(side_page.snapshot(self.run)))
        at(self.run, 'preview')
        self.assertIn('context', side_view.due(self.run), 'once the flow moves on, what never ran needs a look')

    def test_a_moment_too_early_records_nothing(self):
        self.project('visual', 'connect')
        meta = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        (self.run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'execution'}), encoding='utf-8')
        (self.run / 'context' / 'data-preparation.json').rename(self.run / 'context' / 'later.json')
        self.assertEqual(self.project('visual', 'sources'), '', 'no tables loaded or bound yet: nothing to show, nothing recorded')
        (self.run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        (self.run / 'context' / 'later.json').rename(self.run / 'context' / 'data-preparation.json')
        self.assertTrue(self.project('visual', 'sources').startswith('▤ **129 rows** of your data'))
        moments = json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']
        self.assertEqual([m['kind'] for m in moments], ['connect', 'sources'])

    def test_a_context_kept_local_draws_nothing(self):
        # The person has just chosen to keep it here: the ready question says where it lives, once.
        self.run = make_run(Path(self.tmp.name).resolve() / 'local', local=True)
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        self.assertNotIn('⌂', self.project('visual', 'saved'))
        asked = json.loads(self.project('question', 'ready'))['question']
        self.assertNotIn('⌂', asked)
        self.assertEqual(asked.count('on this machine'), 1, asked)

    def test_a_layout_is_recorded_for_the_side_page_and_draws_nothing(self):
        out = self.project('visual', 'layout', '--input', '-', stdin=json.dumps({'tiles': ['Gross Margin'], 'more': ['Rework Cost']}))
        self.assertNotIn('▭', out, 'the layout question proposes it')
        self.assertEqual(json.loads((self.run / 'layout.json').read_text(encoding='utf-8'))['tiles'], ['Gross Margin'])

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
        self.assertTrue(self.project('visual', 'answer').startswith('▪ **£1**'), 'a different, later answer is a chip, its mark no ring')

    def test_numbers_in_a_data_file_must_be_finite(self):
        blocked = json.loads(self.project('visual', 'answer', '--input', '-', stdin='{"question": "Q", "headline": "H: 1", "rows": [["a", NaN]]}'))
        self.assertIn('`rows`', blocked['message'])

    def test_output_is_utf8_in_any_console(self):
        done = subprocess.run([sys.executable, str(PROJECT), '--run-dir', str(self.run), 'visual', 'sources'], capture_output=True,
                              timeout=60, env=dict(os.environ, PYTHONIOENCODING='cp1252'))
        self.assertTrue(done.stdout.decode('utf-8').startswith('▤ **129 rows** of your data'))

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

if __name__ == '__main__':
    unittest.main()
