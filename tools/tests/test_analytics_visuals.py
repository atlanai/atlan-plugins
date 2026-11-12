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

    def test_checks_show_a_ring_and_what_needs_review(self):
        self.assertEqual(self.show('context'), '● **4/4** metrics and queries ran · on your data, 1 query for a person to confirm')
        self.assertEqual(self.show('context', data=[facts()]), "● **4/4** metrics and queries ran · on Fixit's data, 1 query for a person to confirm")

    def test_a_card_at_each_big_moment(self):
        cards = {kind: self.show(kind) for kind in visuals.CARDS}
        heads = {kind: card.split('\n', 1)[0] for kind, card in cards.items()}
        self.assertEqual(heads, {'build': '**2 metrics** · Built from your sources', 'saved': '**2 metrics, 2 queries** · Saved to Atlan Trident',
                                 'answer': '**\u2212£6,360** · Answer', 'edit': '**Late jobs 2 → 3** · Rule changed'},
                         'the number leads, bold, outside the block')
        self.assertIn('4 queries: 2 from the dashboard, 2 from past queries', cards['build'], 'counts that add up on their face')
        self.assertNotIn('question', cards['build'])
        self.assertIn('Live in Test workspace, where agents can find them', cards['saved'])
        self.assertIn('! 1 query for a person to confirm', cards['saved'])
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
        self.assertEqual(self.show('saved', run), '● **4** metrics and queries kept on this machine · nothing saved to Atlan')

    def test_a_dashboard_proposal_is_a_chip(self):
        self.assertEqual(self.show('layout'), '● **2** tiles · dashboard proposed, with 1 more metric below them')

    def test_nothing_shows_before_the_run_has_it(self):
        empty = self.root / 'empty'
        empty.mkdir()
        for kind in visuals.KINDS:
            self.assertEqual(visuals.render(kind, visuals.build_spec(kind, empty)), '', kind)
        self.assertEqual(self.show('connect'), '● **4** sources found · Power BI dashboard, 2 data files, query history')

    def test_a_widget_card_is_small_and_self_contained(self):
        for kind in visuals.CARDS:
            html = self.show(kind, widget=True)
            self.assertTrue(html.startswith('<style>') and 'light-dark(' in html, kind)
            self.assertNotIn('@import', html, kind + ': a card loads nothing')
            self.assertNotRegex(html, r'https?://', kind)
            self.assertLess(len(html), 6000, kind + ': every character is an output token')

    def test_a_chip_is_a_small_widget_where_the_host_draws_one(self):
        for kind in ('connect', 'sources', 'context', 'saved', 'layout'):
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

    def test_a_check_entry_that_is_not_an_object_counts_as_not_run(self):
        checks = self.run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        entries['metric:Gross Margin'] = 'broken'
        checks.write_text(json.dumps(entries), encoding='utf-8')
        self.assertTrue(self.show('context').startswith('◕ **3/4** metrics and queries ran'))

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

    def side(self, out):
        line = [l for l in out.splitlines() if l.startswith('side view: ')]
        return json.loads(line[0][len('side view: '):]) if line else None

    def test_each_moment_prints_its_chip_and_the_side_view_update(self):
        out = self.project('visual', 'connect')
        self.assertTrue(out.startswith('● **4** sources found'), 'the chip comes first')
        first = self.side(out)
        self.assertEqual(sorted(first), ['doc', 'local', 'next', 'page'])
        self.assertTrue(first['page'].endswith('side.html') and first['doc'].endswith('s01.json'))
        self.assertIn("Never put it in a question's text", first['next'], 'a terminal draws question text in a colour light themes hide')
        self.assertIn('--widget', first['next'], 'a host that draws widgets learns it at the first moment')
        out = self.project('visual', 'sources')
        self.assertTrue(out.startswith('● **3** past queries read'))
        self.assertEqual(sorted(self.side(out)), ['next'], 'the finished run already shows it: no new document')

    def test_the_next_line_names_every_duty_the_update_carries(self):
        first = self.side(self.project('visual', 'connect', '--widget'))['next']
        self.assertTrue(first.startswith('Draw it now'), 'the widget first, as the very next call')
        self.assertIn('publish `page`', first, 'the first update opens the side view without the agent recalling the reference')
        self.assertIn('doc_id `s01`', first)
        self.assertIn('progress.html', first, 'and says what to do without the artifact tools')
        meta = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        self.project('view', '--url', 'https://claude.ai/artifact/abc')
        at(self.run, 'scope')
        (self.run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        later = self.side(self.project('visual', 'sources', '--widget'))['next']
        self.assertIn('Then push it to the side view: `ArtifactData` set, url `https://claude.ai/artifact/abc`', later)
        self.assertNotIn('publish `page`', later, 'the page is published once')

    def test_the_build_card_and_the_checks_chip_are_reply_text(self):
        for kind in ('connect', 'sources'):
            self.project('visual', kind)
        built = self.project('visual', 'build').split('\nside view: ')[0]
        self.assertTrue(built.startswith('**2 metrics** · Built from your sources\n  4 queries: 2 from the dashboard'), 'shown when it happens')
        self.assertLessEqual(len(built.splitlines()), 6)
        checked = self.project('visual', 'context')
        self.assertTrue(checked.startswith('● **4/4** metrics and queries ran'), 'one line before the save question')
        self.assertNotIn('Built from your sources', checked, 'the card is not shown twice')
        self.assertIn("Never put it in a question's text", self.side(checked)['next'])

    def test_the_saved_card_is_reply_text_before_the_ready_question(self):
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        out = self.project('visual', 'saved', '--input', '-', stdin=json.dumps({'workspace': 'Personal'}))
        self.assertTrue(out.startswith('**2 metrics, 2 queries** · Saved to Atlan Trident\n  Live in Personal'))
        self.assertLessEqual(len(out.split('\nside view: ')[0].splitlines()), 6)

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
        (fresh / 'engine').mkdir()
        (fresh / 'engine' / 'manifest.json').write_bytes((self.run / 'engine' / 'manifest.json').read_bytes())
        push = json.loads(helper('survey', str(self.run.parent / 'source')))['side_view']
        self.assertIn('https://claude.ai/artifact/abc', push)
        self.assertRegex(push, r'doc_id `s02`', 'the next document, for the page to tick the engine gates that just finished')
        self.assertNotIn('side_view', json.loads(helper('survey', str(self.run.parent / 'source'))), 'nothing new: nothing to push')

    def test_a_moment_too_early_records_nothing(self):
        self.project('visual', 'connect')
        (self.run / 'context' / 'data-preparation.json').rename(self.run / 'context' / 'later.json')
        self.assertEqual(self.project('visual', 'sources'), '', 'no tables loaded yet: nothing to show, nothing recorded')
        (self.run / 'context' / 'later.json').rename(self.run / 'context' / 'data-preparation.json')
        self.assertTrue(self.project('visual', 'sources').startswith('● **3** past queries read'))
        moments = json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']
        self.assertEqual([m['kind'] for m in moments], ['connect', 'sources'])

    def test_a_context_kept_local_shows_its_chip(self):
        self.run = make_run(Path(self.tmp.name).resolve() / 'local', local=True)
        for kind in ('connect', 'sources', 'build', 'context'):
            self.project('visual', kind)
        out = self.project('visual', 'saved')
        self.assertTrue(out.startswith('● **4** metrics and queries kept on this machine'))
        self.assertIn('saved', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])

    def test_an_answer_without_its_file_shows_and_records_nothing(self):
        for kind in ('connect', 'sources', 'build', 'context', 'saved'):
            self.project('visual', kind)
        (self.run / 'answer.json').unlink()
        self.assertEqual(self.project('visual', 'answer'), '')
        self.assertNotIn('answer', [m['kind'] for m in json.loads((self.run / 'view' / 'state.json').read_text(encoding='utf-8'))['moments']])

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
        self.assertEqual(self.project('view', '--url', 'https://claude.ai/artifact/abc'), '')
        (self.run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        at(self.run, 'check')
        self.assertEqual(self.side(self.project('visual', 'build'))['url'], 'https://claude.ai/artifact/abc', 'the next update names it')
        blocked = json.loads(self.project('view', '--url', 'https://example.com/x'))
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('claude.ai', blocked['message'])


if __name__ == '__main__':
    unittest.main()
