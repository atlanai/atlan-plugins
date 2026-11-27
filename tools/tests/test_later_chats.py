"""What a person sees when they come back in a new chat, from the evaluation after 0.9.91: questions written after a
check, a rule change on a warehouse, the questions chip in a terminal, and the number shown beside a known answer. Each
case was proved by running the helpers before it was fixed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

from analytics_run import SCRIPTS, stop_pages
from test_side_page_view import DRIVER, PAGE

sys.path.insert(0, str(SCRIPTS))
import eval_runs  # noqa: E402


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class NewQuestionsAfterACheck(unittest.TestCase):
    """New questions written after an earlier check showed as the old check's: "3 written" when 1 was new, and the
    page listed the old three."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        dump(self.run / 'context' / 'metrics.json', [{'name': 'Gross Sales'}])
        dump(self.run / 'run.json', {})
        self.old = self.root / '.atlan-analytics' / 'evals' / 'box-office'
        dump(self.old / 'work' / 'spec.json', {'name': 'old-set', 'records': [{'id': i, 'question': 'Old %s?' % i} for i in 'abc']})
        self.gen = self.root / '.atlan-analytics' / 'evals' / 'gen' / 'box-office'

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def age(self, folder, seconds):
        spec = folder / 'work' / 'spec.json'
        then = time.time() - seconds
        os.utime(spec, (then, then))

    def test_a_set_written_after_the_last_check_is_the_one_listed(self):
        dump(self.gen / 'work' / 'spec.json', {'name': 'new-set', 'records': [{'id': 'n1', 'question': 'New one?'}]})
        self.age(self.old, 600)
        self.assertEqual(eval_runs.questions_folder(self.run), self.gen)

    def test_a_check_that_loaded_the_written_set_is_the_one_listed(self):
        dump(self.gen / 'work' / 'spec.json', {'name': 'new-set', 'records': [{'id': 'n1', 'question': 'New one?'}]})
        self.age(self.gen, 600)
        self.assertEqual(eval_runs.questions_folder(self.run), self.old)


class ValueBesideTheKnownAnswer(unittest.TestCase):
    """The Test questions tab showed "Column names only: 12,717" beside a known 0.815: the cell nearest the known value,
    however far, when that agent's own answer was 77.2%."""

    def test_a_cell_far_from_the_known_value_never_stands_for_the_answer(self):
        self.assertEqual(eval_runs.shown_beside({'rows': [[12717, 16480]]}, {'rows': [[0.815]]}), '1 row: 12,717, 16,480')

    def test_a_cell_near_the_known_value_still_reads_as_the_answer(self):
        self.assertEqual(eval_runs.shown_beside({'rows': [[36, 14790, 12083, 0.8170]]}, {'rows': [[0.815]]}), '0.817')

    def test_a_share_written_as_a_percent_is_near_its_fraction(self):
        self.assertEqual(eval_runs.shown_beside({'rows': [[12717, 77.2]]}, {'rows': [[0.815]]}), '77.2')


class PageInALaterChat(unittest.TestCase):
    """A new chat restarted the side page at its old address and nobody was told: in the desktop app the pane was never
    opened again, and in a terminal the questions chip named no page and no table, then asked to test them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        dump(self.run / 'context' / 'metrics.json', [{'name': 'Gross Sales'}])
        dump(self.run / 'run.json', {})
        ev = self.root / '.atlan-analytics' / 'evals' / 'box-office'
        dump(ev / 'work' / 'spec.json', {'name': 'box-office', 'records': [{'id': i, 'question': 'Question %s?' % i} for i in 'ab']})
        (ev / 'eval').mkdir(parents=True)
        (ev / 'eval' / 'cases.jsonl').write_text(''.join(json.dumps({'case_id': i, 'question': 'Question %s?' % i}) + '\n'
                                                         for i in 'ab'))
        # The first chat opened the page and saw it; the page has stopped since.
        dump(self.run / 'view' / 'state.json', {'moments': [{'kind': 'connect'}]})
        dump(self.run / 'view' / 'seen.json', {'at': '2026-11-25T10:00:00+00:00'})

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def seen_ago(self, hours):
        then = time.time() - hours * 3600
        os.utime(self.run / 'view' / 'seen.json', (then, then))

    def test_a_terminal_chat_s_questions_chip_names_the_page_it_restarted(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_ENTRYPOINT'}
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'questions'],
                             capture_output=True, text=True, env=env)
        self.assertIn('2 test questions written', out.stdout)
        self.assertRegex(out.stdout, r'http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]+/#l=tests')

    def test_a_desktop_chat_hours_later_is_told_to_open_the_page_on_the_tests(self):
        import side_page
        self.seen_ago(4)
        with unittest.mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}):
            said = side_page.reminder(self.run, tab='tests')
        self.assertIn("Open the side page now in the app's browser pane", said)
        self.assertRegex(said, r'#l=tests`')

    def test_the_same_sitting_is_not_told_again(self):
        import side_page
        self.seen_ago(0.5)
        with unittest.mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}):
            self.assertIsNone(side_page.reminder(self.run, tab='tests'))

    def test_the_page_lands_on_the_list_its_address_names(self):
        page = (SCRIPTS / 'side_page.html').read_text()
        self.assertIn("p.get('l')", page)
        self.assertIn("p.set('l', UI.list)", page)


class WelcomeBackInAnyRoute(unittest.TestCase):
    """"Your agent", the line that says which agent a later chat is working on, came only when a check started: an
    answer, a save or a rule change in a new chat opened with no word of it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        dump(self.run / 'context' / 'metrics.json', [{'name': 'Gross Sales', 'definition': {'output': {'unit': 'USD'}}}])
        dump(self.run / 'context' / 'queries.json', [{'name': 'sales-by-month'}])
        dump(self.run / 'run.json', {})

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_the_first_moment_of_a_sitting_says_which_agent_it_is_once(self):
        import side_view
        first = side_view.welcome(self.run)
        self.assertIn('Your agent: 1 metric and 1 saved query', first)
        self.assertEqual(side_view.welcome(self.run), '', 'once a sitting')

    def test_a_later_chat_s_first_answer_opens_with_it(self):
        sys.path.insert(0, str(SCRIPTS))
        import project
        result = {'columns': ['gross_sales'], 'rows': [[1550066.18]]}
        out = project._answer_card(self.run, {'question': 'What are gross sales?', 'metrics': ['Gross Sales']}, result)
        self.assertIn('Your agent: 1 metric and 1 saved query', out['show_first'])
        self.assertLess(out['show_first'].index('Your agent'), out['show_first'].index('Gross'))
        self.assertEqual(out['show_first'].count('side view: '), 1)


class AnswerCardOnTheSavedQueryRoute(unittest.TestCase):
    """An answer from a saved query (`PROJECT answer`) never drew its card: only `PROJECT execute` read `answer`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        dump(self.run / 'context' / 'metrics.json', [{'name': 'Gross Sales', 'definition': {'output': {'unit': 'USD'}}}])
        dump(self.run / 'run.json', {})

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_rows_from_a_saved_query_bring_the_card_and_a_plan_for_the_warehouse_does_not(self):
        import project
        asked = {'question': 'What are gross sales?', 'metrics': ['Gross Sales']}
        out = project._with_answer_card(self.run, {'answer': asked}, {'columns': ['gross_sales'], 'rows': [[1550066.18]]})
        self.assertIn('$1,550,066', out['show_first'])
        plan = {'status': 'execute_in_host', 'sql': 'SELECT 1'}
        self.assertEqual(project._with_answer_card(self.run, {'answer': asked}, plan), plan)


class TestQuestionsTab(unittest.TestCase):
    """What the Test questions tab could not say, from the evaluation after 0.9.91: why a question was missed and which
    metric the agent used (both on disk, neither read), the score, how a question did in earlier checks (two checks of
    one build scored 11 then 8 of 11 on reworded, reordered questions), the known rows beside the agent's, and whether
    the metrics changed after the check."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        dump(self.run / 'context' / 'metrics.json', [{'name': 'All Tickets Sold', 'source_id': 'metric:x:1'},
                                                     {'name': 'Paid Primary Tickets Sold', 'source_id': 'metric:x:2'}])
        dump(self.run / 'context' / 'queries.json', [{'name': 'gross-sales', 'display_name': 'Gross sales by month',
                                                      'source_id': 'vq:x:g'}])
        dump(self.run / 'run.json', {})
        self.check(self.root / '.atlan-analytics' / 'evals' / 'box-office', [('m7', 'For each event, how many tickets?', 'SELECT a', True),
                                                               ('m8', 'Gross sales?', 'SELECT  b', True)],
                   '2026-11-24T10:00:00+00:00', age=7200)
        self.ev = self.root / '.atlan-analytics' / 'evals' / 'box-office-2'
        self.check(self.ev, [('q1', 'How many tickets did each completed show sell?', 'select a', False),
                             ('q2', 'What were gross sales?', 'SELECT b', True)], '2026-11-25T10:00:00+00:00', age=3600)
        vd = self.ev / 'hillclimb' / 'baseline'
        dump(vd / 'why.json', {'q1': {'why': 'Different rule', 'detail': 'It used Paid Primary Tickets Sold, not All Tickets Sold.'}})
        # The metrics as they stood when the check ran.
        import datetime
        built = datetime.datetime(2026, 11, 25, 9, tzinfo=datetime.timezone.utc).timestamp()
        os.utime(self.run / 'context' / 'metrics.json', (built, built))

    def check(self, ev, cases, scored_at, age):
        dump(ev / 'work' / 'spec.json', {'name': 'box-office', 'records': [{'id': c, 'question': q} for c, q, _, _ in cases]})
        dump(ev / 'work' / 'selection.json', [{'id': c, 'metric': 'All Tickets Sold'} for c, _, _, _ in cases])
        (ev / 'eval').mkdir(parents=True, exist_ok=True)
        (ev / 'eval' / 'answers.jsonl').write_text(''.join(json.dumps({'case_id': c, 'gold_sql': sql}) + '\n' for c, _, sql, _ in cases))
        for c, _, _, _ in cases:
            dump(ev / 'eval' / 'gold' / ('%s.json' % c), {'columns': ['show', 'tickets'], 'rows': [['A', 5], ['B', 3]]})
        for variant, right in (('baseline', None), ('raw', False)):
            vd = ev / 'hillclimb' / variant
            rows = []
            for c, _, _, ok in cases:
                ok = ok if right is None else right
                dump(vd / 'answers' / ('%s_rep0.json' % c), {'answer': 'An answer.', 'sql': 'SELECT 1'})
                dump(vd / 'agent_results' / ('%s_rep0.json' % c), {'columns': ['show', 'tickets'], 'rows': [['A', 5 if ok else 4], ['B', 3]]})
                rows.append({'prompt_id': c, 'rep': 0, 'grade': {'pass': int(ok)}, 'class': 'pass' if ok else 'wrong_result',
                             'reason': 'match' if ok else 'A has 4 against 5.', 'used_context_ids': ['metric:x:1' if ok else 'metric:x:2']})
            (vd / 'results.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            dump(vd / 'summary.json', {'scored_at': scored_at})
        then = time.time() - age
        for f in ev.rglob('*'):
            os.utime(f, (then, then))

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def read(self):
        return eval_runs.questions(eval_runs.questions_folder(self.run))

    def test_a_miss_says_why_in_plain_words_and_names_the_metric_used(self):
        q1 = self.read()['rows'][0]
        self.assertEqual(q1['agent']['cause'], {'shown': 'Different definition', 'detail': 'It used Paid Primary Tickets Sold, not All Tickets Sold.'})
        self.assertEqual(q1['agent']['used'], ['Paid Primary Tickets Sold'])
        self.assertEqual(q1['agent']['why'], 'A has 4 against 5.', 'the grader\'s own words stay, under how it was graded')
        q2 = self.read()['rows'][1]
        self.assertIsNone(q2['agent']['cause'])
        self.assertEqual(q2['agent']['used'], ['All Tickets Sold'])

    def test_saved_queries_used_are_counted_not_named_by_their_whole_question(self):
        vd = self.ev / 'hillclimb' / 'baseline'
        rows = [json.loads(line) for line in (vd / 'results.jsonl').read_text().splitlines()]
        rows[1]['used_context_ids'] = ['metric:x:1', 'vq:x:g']
        (vd / 'results.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        self.assertEqual(self.read()['rows'][1]['agent']['used'], ['All Tickets Sold', 'a saved query'])

    def test_the_tab_carries_its_score_and_when_it_was_scored(self):
        self.assertEqual(self.read()['score'], {'agent': [1, 2], 'names': [0, 2], 'scored_at': '2026-11-25T10:00:00+00:00'})

    def test_each_question_shows_how_it_did_in_earlier_checks_matched_by_its_known_answer(self):
        rows = self.read()['rows']
        self.assertEqual(rows[0]['earlier'], [{'when': '24 Nov', 'right': True, 'question': 'For each event, how many tickets?'}])
        self.assertEqual(rows[1]['earlier'], [{'when': '24 Nov', 'right': True, 'question': 'Gross sales?'}])

    def test_the_known_rows_sit_beside_the_agent_s(self):
        q1 = self.read()['rows'][0]
        self.assertEqual(q1['known_rows'], {'columns': ['show', 'tickets'], 'rows': [['A', 5], ['B', 3]]})
        self.assertEqual(q1['agent']['rows'], {'columns': ['show', 'tickets'], 'rows': [['A', 4], ['B', 3]]})

    def test_a_question_tested_again_alone_shows_its_new_answer_beside_the_rest(self):
        # After a fix, only question 1 runs again as v1: the tab showed v1 alone, every other question waiting.
        v1 = self.ev / 'hillclimb' / 'v1'
        dump(v1 / 'questions.json', ['q1'])  # as `EVAL todo --questions 1` writes it
        dump(v1 / 'answers' / 'q1_rep0.json', {'answer': 'Now right.', 'sql': 'SELECT 2'})
        dump(v1 / 'agent_results' / 'q1_rep0.json', {'columns': ['show', 'tickets'], 'rows': [['A', 5], ['B', 3]]})
        (v1 / 'results.jsonl').write_text(json.dumps({'prompt_id': 'q1', 'rep': 0, 'grade': {'pass': 1}, 'class': 'pass',
                                                      'reason': 'match', 'used_context_ids': ['metric:x:1']}) + '\n')
        dump(v1 / 'summary.json', {'scored_at': '2026-11-26T10:00:00+00:00'})
        tab = self.read()
        self.assertEqual([(r['agent']['answer'], r['agent']['right']) for r in tab['rows']],
                         [('Now right.', True), ('An answer.', True)])
        self.assertEqual((tab['state'], tab['score']['agent']), ('scored', [2, 2]))

    def test_one_question_runs_again_alone_by_its_number(self):
        import argparse
        import analytics_eval
        (self.ev / 'eval' / 'cases.jsonl').write_text(''.join(json.dumps({'case_id': c, 'question': q}) + '\n'
                                                              for c, q in (('q1', 'A?'), ('q2', 'B?'))))
        todo = lambda q: analytics_eval.cmd_todo(argparse.Namespace(dir=str(self.ev), variant='v1', ids='all', reps=1,  # noqa: E731
                                                                    questions=q))
        self.assertEqual([t['case_id'] for t in todo('2')['todo']], ['q2'])
        self.assertEqual(json.loads((self.ev / 'hillclimb' / 'v1' / 'questions.json').read_text()), ['q2'])
        self.assertEqual([t['case_id'] for t in todo(None)['todo']], ['q1', 'q2'])
        with self.assertRaisesRegex(analytics_eval.Blocked, '1 to 2'):
            todo('3')

    def test_metrics_changed_after_the_check_are_flagged(self):
        self.assertFalse(self.read()['changed_after'])
        os.utime(self.run / 'context' / 'metrics.json', None)
        self.assertTrue(self.read()['changed_after'])


@unittest.skipUnless(shutil.which('node'), 'Node renders the page script')
class TestQuestionView(unittest.TestCase):
    """A missed question's view, rendered at a side pane's width: its two tables of rows ran past the pane and over each
    other, headed by a dashboard's own field codes, and an answer that listed every row ran to a page."""

    def render(self, answer='', **ui):
        row = {'n': 1, 'id': 'q1', 'question': 'Tickets per show?', 'metric': 'Tickets', 'from': 'tile', 'known': '2 rows',
               'known_rows': {'columns': ['none:event_name:nk', 'sum:tickets_issued:qk'], 'rows': [['A', 5]]},
               'agent': {'right': False, 'answer': answer, 'value': None, 'sql': '', 'why': '', 'cause': None, 'used': [],
                         'rows': {'columns': ['EVENT_NAME', 'tickets'], 'rows': [['A', 4]]}},
               'names': None, 'earlier': []}
        snap = {'flow': 'build', 'tests': {'total': 1, 'answered': 1, 'state': 'scored', 'rows': [row], 'name': '',
                                           'score': {'agent': [0, 1], 'names': [0, 0], 'scored_at': None}}}
        result = subprocess.run([shutil.which('node'), '-e', DRIVER, str(PAGE)], capture_output=True, text=True,
                                input=json.dumps({'snapshot': snap, 'ui': dict({'list': 'tests', 'test': '1'}, **ui)}),
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_columns_are_headed_in_words(self):
        html = self.render()
        for words in ('event name', 'tickets issued'):
            self.assertIn('<th>%s</th>' % words, html)
        self.assertNotIn('none:event_name:nk', html)

    def test_each_table_scrolls_in_its_own_box(self):
        self.assertEqual(self.render().count('<div class="prw"><table class="prt">'), 2)

    def test_a_long_answer_shows_its_first_lines_and_the_rest_on_request(self):
        answer = 'Each show. ' * 80 + 'The last line.'
        folded = self.render(answer)
        self.assertNotIn('The last line.', folded)
        self.assertIn('Show the whole answer', folded)
        self.assertIn('The last line.', self.render(answer, tog=['ans:a1']))


class AccuracyCardOpensAQuestion(unittest.TestCase):
    """A question of the check opens on the side page, or by asking for it. The card's marks once sent "Show question N"
    into the chat; the desktop app takes text from a card once, and after that text is cleared it refuses every later
    click with "Nothing was added to the message box. Try again in a few seconds", minutes on (2026-11-27)."""

    def card(self):
        import analytics_eval
        ids = ['a', 'b']
        return analytics_eval._before_after_widget(ids, {'a': 1, 'b': 2}, {'a': 'A?', 'b': 'B?'}, {'a': 0, 'b': 0},
                                                   {'a': 1, 'b': 0}, (0, 1), ('', ''), ['a'], [])

    def test_the_card_draws_each_mark_and_sends_nothing(self):
        w = self.card()
        self.assertEqual(w.count('class="y"') + w.count('class="n"'), 4, 'both passes of both questions')
        self.assertNotIn('sendPrompt', w)
        self.assertNotIn('<script', w)
        self.assertNotIn('data-n=', w)

    DETAILS = {'a': {'n': 1, 'question': 'A?', 'metric': 'Gross Sales', 'known': '1,550,066.18',
                     'agent': {'value': '1,550,066.18', 'right': True, 'cause': None},
                     'names': {'value': '1,557,930.77', 'right': False, 'cause': None}},
               'b': {'n': 2, 'question': 'B?', 'metric': 'Paid Sell-Through', 'known': '36 rows, starting Grumpy, 700, …',
                     'agent': {'value': '35 rows, starting E001, 90, …', 'right': False,
                               'cause': {'shown': 'Different definition', 'detail': 'It used Sell-Through.'}},
                     'names': {'value': None, 'right': False, 'cause': None}}}

    def opened(self, on_page=False, mb=None):
        import analytics_eval
        return analytics_eval._before_after_widget(['a', 'b'], {'a': 1, 'b': 2}, {'a': 'A?', 'b': 'B?'}, {'a': 0, 'b': 0},
                                                   mb or {'a': 1, 'b': 0}, (0, 1), ('', ''), ['a'], [], on_page,
                                                   self.DETAILS)

    def test_a_question_opens_inside_the_card_the_first_miss_first(self):
        # Opening a question stays inside the card, so it never meets the message box or its limit (2026-11-27).
        w = self.opened()
        self.assertNotIn('sendPrompt', w)
        self.assertIn('<div class="qd" data-q="2">', w, 'the first miss is open, even where no script runs')
        self.assertIn('<div class="qd" data-q="1" hidden>', w)
        self.assertEqual(w.count(' on" data-q="2"'), 2, 'its column, marked in both passes')
        for said in ('Tests Paid Sell-Through', 'Known answer', '>36 rows<', '>35 rows<', 'Missed: different definition',
                     'It used Sell-Through.', 'No result', 'Tests Gross Sales', '1,557,930.77', '>Right<'):
            self.assertIn(said, w)
        self.assertNotIn('Grumpy', w, 'a table answer reads as its row count; its rows are on the side page')
        self.assertIn("addEventListener('click'", w)
        self.assertIn('.mc [hidden]{display:none}', w, 'the details grid set display, which showed every closed one')

    def test_the_side_page_is_named_only_while_it_is_open(self):
        line = 'Both answers in full, with their SQL, are on the side page.'
        self.assertNotIn(line, self.opened())
        self.assertEqual(self.opened(on_page=True).count(line), 1, 'once, under whichever question is open')

    def test_with_every_answer_right_no_question_starts_open(self):
        w = self.opened(mb={'a': 1, 'b': 1})
        self.assertEqual(w.count('class="qd"'), 2)
        self.assertEqual(w.count('class="qd" data-q="1" hidden'), 1)
        self.assertEqual(w.count('class="qd" data-q="2" hidden'), 1)

    def test_the_guide_opens_a_question_the_person_asks_for(self):
        run = (SCRIPTS.parents[1] / 'accuracy-check' / 'references' / 'run.md').read_text()
        self.assertNotIn('each mark sends', run)
        self.assertIn('"show question 5"', run)
        self.assertIn('CHECK question --evals <evals-run> --n <N>', run)

    def test_a_question_opens_on_the_page_or_comes_back_whole(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / 'accuracy-check' / 'scripts'))
        import argparse
        import check
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        run = root / '.atlan-analytics' / 'box-office'
        dump(run / 'context' / 'metrics.json', [])
        dump(run / 'run.json', {})
        ev = root / '.atlan-analytics' / 'evals' / 'box-office'
        dump(ev / 'work' / 'spec.json', {'name': 's', 'records': [{'id': 'q1', 'question': 'Gross sales?'},
                                                                   {'id': 'q2', 'question': 'Fee rate?'}]})
        ask = lambda n: check.cmd_question(argparse.Namespace(evals=str(ev), n=n))  # noqa: E731
        with unittest.mock.patch('side_page.running', return_value='http://127.0.0.1:9/k/'):
            self.assertEqual(ask('2')['page'], 'http://127.0.0.1:9/k/#l=tests&t=2')
        with unittest.mock.patch('side_page.running', return_value=None):
            out = ask('2')
        self.assertNotIn('page', out)
        self.assertEqual((out['question']['n'], out['question']['question']), (2, 'Fee rate?'))
        with self.assertRaisesRegex(check.Blocked, '1 to 2'):
            ask('9')


class WarehouseRuleChange(unittest.TestCase):
    """On a warehouse a metric's check hands back the statement to run instead of running it, and the edit took that as
    passed: version 2 was saved unchecked and its card read "14.8 → 14.8", the old check's number on both sides."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS.parents[1] / 'analytics-demo' / 'scripts'))
        from project import Project
        import context_contracts as mine
        self.tmp = tempfile.TemporaryDirectory()
        estate = Path(self.tmp.name).resolve() / 'estate'
        estate.mkdir()
        (estate / 'notes.sql').write_text('SELECT 1')
        self.run = Project(estate / '.atlan-analytics' / 'example')
        self.run.survey(estate)
        (self.run.root / 'state.json').write_text(json.dumps({'warehouse': {'kind': 'snowflake', 'dialect': 'snowflake',
                                                                            'tool': 'sql_exec'}}))
        card = {'name': 'cost', 'display_name': 'Cost', 'description': 'Recorded cost.',
                'definition': {'spec_version': mine.METRIC_SPEC, 'version': 1, 'dialect': 'snowflake',
                               'formula': 'SUM(cost_rows.cost)', 'aggregation': {'type': 'sum'},
                               'output': {'alias': 'cost', 'datatype': 'decimal'},
                               'source': {'entity': 'cost_rows', 'alias': 'cost_rows',
                                          'sql': "(SELECT cost FROM site.work WHERE kind = 'rework')"},
                               'extensions': {'atlan_analytics': {'declared': ['example:Cost']}}, 'mandatory_filters': [],
                               'examples': [{'source': 'declared:example',
                                             'sql': "SELECT SUM(cost) FROM site.work WHERE kind = 'rework'"}]}}
        self.run.write(self.run.context / 'metrics.json', [card])
        self.run.write(self.run.context / 'queries.json', [])
        self.run.check_metric({'name': 'cost'})
        self.run.check_metric({'name': 'cost', 'result': {'columns': ['cost'], 'rows': [[14.75]]}})
        self.change = {'name': 'cost', 'before': 'Rework only', 'after': 'Everything but rework',
                       'changes': [{'path': 'source.sql', 'value': "(SELECT cost FROM site.work WHERE kind <> 'rework')"}]}

    def tearDown(self):
        stop_pages(self.run.root.parent.parent)
        self.tmp.cleanup()

    def definition(self):
        return self.run.read(self.run.context / 'metrics.json')[0]['definition']

    def test_the_change_waits_for_its_statement_to_run_then_shows_both_numbers(self):
        import edit_metric
        out = edit_metric.edit_metric(self.run, self.change)
        self.assertEqual(out['status'], 'run_on_warehouse')
        self.assertIn('kind <> ', out['sql'])
        self.assertNotIn('show_first', out, 'no card before the new rule has run')
        self.assertEqual(self.definition()['version'], 2)
        done = edit_metric.edit_metric(self.run, {'name': 'cost', 'result': {'columns': ['cost'], 'rows': [[20.0]]}})
        self.assertEqual((done['status'], done['version']), ('ok', 2))
        self.assertIn('14.8', done['show_first'])
        self.assertIn('20', done['show_first'])
        self.assertFalse((self.run.root / 'edit-pending.json').exists())

    def test_a_statement_that_fails_on_the_warehouse_puts_the_old_rule_back(self):
        import edit_metric
        from project import ProjectError
        edit_metric.edit_metric(self.run, self.change)
        with self.assertRaisesRegex(ProjectError, 'old rule stays'):
            edit_metric.edit_metric(self.run, {'name': 'cost', 'result': {'error': 'Object SITE.WORK does not exist.'}})
        self.assertEqual(self.definition()['version'], 1)
        self.assertIn("kind = 'rework'", self.definition()['source']['sql'])

    def test_a_result_with_no_change_waiting_is_refused(self):
        import edit_metric
        from project import ProjectError
        with self.assertRaisesRegex(ProjectError, 'No rule change is waiting'):
            edit_metric.edit_metric(self.run, {'name': 'cost', 'result': {'rows': [[1]]}})


if __name__ == '__main__':
    unittest.main()
