"""The test questions an accuracy check writes are outputs, like metrics and saved queries: the side page lists them as
soon as they are written and fills each row as its answers come back, numbered as the accuracy card numbers them. One
record per question feeds the page, the chat's table and the card, so the three never disagree."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from analytics_run import ROOT, SCRIPTS, make_run, stop_pages

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'plugins/atlanai-analytics/skills/accuracy-check/scripts'))
import analytics_eval  # noqa: E402
import check  # noqa: E402
import eval_runs  # noqa: E402
import side_page  # noqa: E402
import visuals  # noqa: E402


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class TestQuestions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        dump(self.run / 'context' / 'metrics.json', [{'name': 'Gross Sales'}])
        dump(self.run / 'run.json', {})
        self.ev = self.root / '.atlan-analytics' / 'evals' / 'box-office'
        ids = ('a', 'b', 'c')
        dump(self.ev / 'work' / 'spec.json', {'name': 'box-office', 'records': [{'id': i, 'question': 'Question %s?' % i} for i in ids]})
        (self.ev / 'eval').mkdir(parents=True)
        (self.ev / 'eval' / 'cases.jsonl').write_text(''.join(json.dumps({'case_id': i, 'question': 'Question %s?' % i}) + '\n' for i in ids))
        dump(self.ev / 'work' / 'selection.json', [{'id': 'a', 'metric': 'Gross Sales', 'from': 'dashboard tile'},
                                                   {'id': 'b', 'metric': 'Sell-Through', 'from': 'past query, used 4 times'}])
        dump(self.ev / 'eval' / 'gold' / 'a.json', {'columns': [{'name': 'g'}], 'rows': [[1550066.18]]})
        dump(self.ev / 'eval' / 'gold' / 'b.json', {'columns': [{'name': 's'}], 'rows': [[0.8150454863540938]]})
        dump(self.ev / 'eval' / 'gold' / 'c.json', {'columns': ['m', 'n'], 'rows': [['2025-06-01', 68732.28], ['2025-07-01', 1]]})

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def answer(self, variant, case, text, rows, sql='SELECT 1'):
        dump(self.ev / 'hillclimb' / variant / 'answers' / ('%s_rep0.json' % case), {'answer': text, 'sql': sql})
        dump(self.ev / 'hillclimb' / variant / 'agent_results' / ('%s_rep0.json' % case), {'columns': [{'name': 'x'}], 'rows': rows})

    def score(self, variant, passed, reasons=None):
        (self.ev / 'hillclimb' / variant / 'results.jsonl').write_text(''.join(json.dumps({
            'prompt_id': i, 'rep': 0, 'prompt': 'Question %s?' % i, 'tags': [], 'grade': {'pass': p, 'exec_ok': 1},
            'class': 'pass' if p else 'wrong_result', 'reason': (reasons or {}).get(i, 'match'),
            'ts': '2026-11-23T10:00:00+00:00', 'isolation': 'verified'}) + '\n' for i, p in passed.items()))

    def scored(self):
        for i, rows in (('a', [[1550066.18]]), ('b', [[0.8150454863540938]]), ('c', [['2025-06-01', 68732.28], ['2025-07-01', 1]])):
            self.answer('baseline', i, 'Answer %s.' % i, rows)
        self.answer('raw', 'a', 'About 1.57 million.', [[1573124.78]])
        self.answer('raw', 'b', 'Sell-through is 81.7%.', [[36, 14790, 12083, 0.8170]])  # extra columns beside the value
        dump(self.ev / 'hillclimb' / 'raw' / 'answers' / 'c_rep0.json', {'answer': 'Not computed.', 'sql': ''})
        self.score('baseline', {'a': 1, 'b': 1, 'c': 1})
        self.score('raw', {'a': 0, 'b': 0, 'c': 0}, {'b': 'The agent gave 0.817 where the trusted value is 0.815.',
                                                     'c': 'the agent gave no SQL'})

    def test_before_the_check_each_question_has_its_number_its_metric_and_its_known_answer(self):
        t = eval_runs.questions(self.ev)
        self.assertEqual((t['state'], t['total'], t['answered']), ('ready', 3, 0))
        self.assertEqual([(r['n'], r['question'], r['metric'], r['known']) for r in t['rows']],
                         [(1, 'Question a?', 'Gross Sales', '1,550,066.18'), (2, 'Question b?', 'Sell-Through', '0.815'),
                          (3, 'Question c?', '', '2 rows, starting 2025-06-01, 68,732.28')])
        self.assertEqual([r['from'] for r in t['rows']], ['tile', 'query', ''])
        self.assertTrue(all(r['agent'] is None and r['names'] is None for r in t['rows']))

    def test_an_answer_shows_the_moment_its_file_lands_and_its_mark_once_it_is_scored(self):
        self.answer('baseline', 'a', 'Gross sales are $1,550,066.18.', [[1550066.18]])
        t = eval_runs.questions(self.ev)
        self.assertEqual((t['state'], t['answered']), ('running', 1))
        self.assertEqual(t['rows'][0]['agent'], {'value': '1,550,066.18', 'answer': 'Gross sales are $1,550,066.18.',
                                                 'sql': 'SELECT 1', 'right': None, 'why': '', 'cause': None, 'used': [],
                                                 'rows': {'columns': ['x'], 'rows': [[1550066.18]]}})
        self.assertIsNone(t['rows'][1]['agent'])

    def test_once_scored_each_pass_is_marked_as_the_card_marks_it(self):
        self.scored()
        t = eval_runs.questions(self.ev)
        self.assertEqual((t['state'], t['answered']), ('scored', 3))
        b = t['rows'][1]
        self.assertEqual((b['agent']['right'], b['names']['right']), (True, False))
        # A wrong answer never reads the same as the known answer it missed, and an answer with extra columns shows the
        # one compared with a single known value.
        self.assertEqual((b['known'], b['names']['value']), ('0.815', '0.817'))
        self.assertEqual(b['names']['why'], 'The agent gave 0.817 where the trusted value is 0.815.')
        self.assertEqual(t['rows'][0]['agent']['why'], '')
        self.assertIsNone(t['rows'][2]['names']['value'])
        self.assertEqual(t['rows'][2]['names']['answer'], 'Not computed.')
        means = analytics_eval.case_means(analytics_eval.read_results(self.ev / 'hillclimb' / 'raw' / 'results.jsonl'))
        self.assertEqual([r['names']['right'] for r in t['rows']], [means[i] == 1 for i in 'abc'])

    def test_the_chat_table_is_written_from_the_same_record(self):
        table = check.cmd_show(argparse.Namespace(evals=str(self.ev)))['table'].splitlines()
        self.assertEqual(table[2:], ['| 1 | Question a? | Gross Sales | 1,550,066.18 |', '| 2 | Question b? | Sell-Through | 0.815 |',
                                     '| 3 | Question c? | No metric yet | 2 rows, starting 2025-06-01, 68,732.28 |'])

    def test_a_run_reads_its_newest_check_and_never_another_runs(self):
        self.assertEqual(eval_runs.check_folder(self.run), self.ev)
        newer = self.root / '.atlan-analytics' / 'evals' / 'box-office-2'
        dump(newer / 'work' / 'spec.json', {'name': 'box-office', 'records': []})
        os.utime(self.ev / 'work' / 'spec.json', (1, 1))
        # Another run's check whose name starts the same way.
        dump(self.root / '.atlan-analytics' / 'evals' / 'box-office-pulse' / 'work' / 'spec.json', {'name': 'x', 'records': []})
        self.assertEqual(eval_runs.check_folder(self.run), newer)
        self.assertEqual(eval_runs.run_of(newer), self.run)
        other = self.root / '.atlan-analytics' / 'box-office-pulse'
        dump(other / 'context' / 'metrics.json', [])
        self.assertEqual(eval_runs.check_folder(other), self.root / '.atlan-analytics' / 'evals' / 'box-office-pulse')
        self.assertIsNone(eval_runs.check_folder(self.root / '.atlan-analytics' / 'never-checked'))

    def test_a_folder_too_shallow_to_be_a_check_belongs_to_no_run(self):
        # The score's self-test runs in a temporary folder, /tmp/<name> on Linux, which has no grandparent: the lookup
        # raised IndexError there and failed CI, while macOS's deeper temporary folders passed (2026-11-27).
        self.assertIsNone(eval_runs.run_of(Path('/tmp/check')))
        self.assertIsNone(eval_runs.run_of(Path('/check')))

    def test_a_question_set_still_being_loaded_is_not_shown(self):
        (self.ev / 'work' / 'spec.json').unlink()
        self.assertIsNone(eval_runs.check_folder(self.run))

    def test_a_set_the_person_brought_is_named_as_theirs(self):
        dump(self.ev / 'work' / 'selection.json', [{'id': i, 'metric': '', 'from': 'eval set finance'} for i in 'abc'])
        self.assertEqual(visuals.render('questions', visuals.build_spec('questions', self.run, []), False),
                         '❝ **3 test questions written** · from your set box-office')

    def test_a_chip_says_how_many_questions_were_written_and_where_they_came_from(self):
        line = visuals.render('questions', visuals.build_spec('questions', self.run, []), False)
        self.assertEqual(line, '❝ **3 test questions written** · 1 from your dashboard, 1 from past queries')
        self.assertIn('3 test questions written', visuals.render('questions', visuals.build_spec('questions', self.run, []), True))
        (self.ev / 'work' / 'selection.json').unlink()
        self.assertEqual(visuals.render('questions', visuals.build_spec('questions', self.run, []), False),
                         '❝ **3 test questions written**')

    def test_a_written_set_counts_its_questions_not_the_spares_drafted_beside_them(self):
        # A Codex run, 2026-11-26: 12 drafted (8 asked for, 4 spares), 8 written to the set, and the side page
        # listed all 12. `emit` records the set's ids; the side page lists those. (The chip counts from scope.json.)
        dump(self.ev / 'work' / 'emitted.json', ['c', 'a'])
        asked = eval_runs.questions(self.ev)
        self.assertEqual((asked['total'], [r['id'] for r in asked['rows']]), (2, ['c', 'a']))

    def test_the_result_that_loads_the_questions_hands_over_their_chip(self):
        # The dry run, 2026-11-25: chips a guide asks for went undrawn. The chip comes with the result the check reads.
        shown = check.show_questions(self.run)
        self.assertIn('3 test questions written', shown['show_first'])
        self.assertIn('show_first', shown['visual'])
        import side_view
        self.assertIn('questions', side_view.seen(self.run), 'handed over is shown')

    def test_a_check_on_an_earlier_build_opens_with_what_the_agent_holds(self):
        # A session that only tests: nothing on screen said which agent was being tested.
        dump(self.run / 'context' / 'queries.json', [{'name': 'sales-by-month'}, {'name': 'fees'}])
        started = check.cmd_start(argparse.Namespace(project=str(self.root), run=None))
        self.assertIn('Your agent: 1 metric and 2 saved queries', started['show_first'])
        self.assertRegex(started['show_first'], r'built \d{1,2} [A-Z][a-z]{2}')
        self.assertIn('show_first', started['visual'])
        # Shown once: a second check in the same sitting, or one right after the build's card, opens without it.
        self.assertNotIn('show_first', check.cmd_start(argparse.Namespace(project=str(self.root), run=None)))

    def test_a_set_written_earlier_is_offered_as_written_not_as_the_persons_own(self):
        # Dry run B, 2026-11-25: a set written the day before was offered as "Your own questions".
        sets = self.root / '.atlan-analytics' / 'evals' / 'sets'
        sets.mkdir(parents=True, exist_ok=True)
        row = {'question': 'Question?', 'sql': 'SELECT 1'}
        (sets / 'written.jsonl').write_text(json.dumps({**row, 'id': 'w', 'generated': True}) + '\n')
        (sets / 'brought.jsonl').write_text(json.dumps({**row, 'id': 'b'}) + '\n')
        started = check.cmd_start(argparse.Namespace(project=str(self.root), run=None, questions=20))
        options = {o['label']: o['description'] for o in started['ask']['options']}
        self.assertEqual(options['brought (1 question)'], 'Your own questions, each with its known answer.')
        self.assertEqual(options['written (1 question)'],
                         'Written earlier from what your team already asks, each with its known answer.')

    def test_questions_written_show_their_chip_once_whoever_shows_it_first(self):
        import shutil
        shutil.rmtree(self.ev)  # written by eval-generation, not loaded by any check yet
        gen = self.root / '.atlan-analytics' / 'evals' / 'gen' / self.run.name
        dump(gen / 'work' / 'spec.json', {'name': 'x', 'records': [{'id': 'a', 'question': 'Question a?'}]})
        self.assertIn('test question written', visuals.render('questions', visuals.build_spec('questions', self.run, []), False))
        first = check.show_questions(self.run)
        self.assertIn('show_first', first)
        self.assertEqual(check.show_questions(self.run, since=0), {}, 'already shown since the set was written')
        self.assertIn('show_first', check.show_questions(self.run), 'asked for outright, it shows')

    def test_without_a_side_page_the_chat_gets_the_table(self):
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'visual', 'questions'],
                             capture_output=True, text=True)
        self.assertIn('3 test questions written', out.stdout)
        self.assertIn('send the table `CHECK show` prints', out.stdout)

    def test_with_the_page_open_the_card_points_to_it_and_keeps_every_mark(self):
        self.scored()
        args = argparse.Namespace(dir=str(self.ev), before='raw', after='baseline')
        off = analytics_eval.cmd_before_after(args)['widget']
        self.assertIn('What changed', off)
        self.assertIn('Question a?', off)
        with mock.patch.object(side_page, 'running', return_value='http://127.0.0.1:1/key/'):
            on = analytics_eval.cmd_before_after(args)['widget']
        # It counts the questions that changed rather than listing them; each opens in the card only when clicked
        # (2026-11-27), from the page's own records.
        self.assertNotIn('What changed', on)
        self.assertIn('<div class="qd" data-q="1" hidden><div class="qh"><b>1.</b> Question a?', on)
        self.assertIn('Tests Gross Sales', on)
        self.assertIn('3 questions right only with your agent', on)
        self.assertIn('side page', on)
        self.assertEqual(on.count('<i class="y" data-q='), 3)
        self.assertEqual(on.count('<i class="y" data-q='), off.count('<i class="y" data-q='))


class OnThePage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root)

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_the_page_lists_the_questions_and_follows_each_answer_as_it_lands(self):
        self.assertNotIn('tests', side_page.snapshot(self.run))
        ev = self.root / '.atlan-analytics' / 'evals' / self.run.name  # a run outside .atlan-analytics keeps its checks beside it
        dump(ev / 'work' / 'spec.json', {'name': 'jobs', 'records': [{'id': 'q1', 'question': 'How many jobs?'}]})
        dump(ev / 'eval' / 'gold' / 'q1.json', {'columns': ['n'], 'rows': [[12]]})
        tests = side_page.snapshot(self.run)['tests']
        self.assertEqual([(r['n'], r['question'], r['known']) for r in tests['rows']], [(1, 'How many jobs?', '12')])
        before = side_page._fingerprint(self.run)
        dump(ev / 'hillclimb' / 'baseline' / 'answers' / 'q1_rep0.json', {'answer': '12 jobs.', 'sql': 'SELECT 12'})
        self.assertNotEqual(side_page._fingerprint(self.run), before)
        self.assertEqual(side_page.snapshot(self.run)['tests']['rows'][0]['agent']['answer'], '12 jobs.')


    def test_questions_written_but_not_yet_tested_are_on_the_page(self):
        # A session that only writes test questions: the set exists before any check loads it.
        gen = self.root / '.atlan-analytics' / 'evals' / 'gen' / self.run.name
        dump(gen / 'work' / 'spec.json', {'name': 'jobs', 'records': [{'id': 'g1', 'question': 'How many jobs ran?'}]})
        dump(gen / 'work' / 'selection.json', [{'id': 'g1', 'metric': 'Jobs', 'from': 'dashboard tile'}])
        tests = side_page.snapshot(self.run)['tests']
        self.assertEqual(tests['state'], 'ready')
        self.assertEqual([(r['n'], r['question'], r['metric'], r['known']) for r in tests['rows']], [(1, 'How many jobs ran?', 'Jobs', '')])
        # Once a check loads them, the check's own folder is what the page reads.
        ev = self.root / '.atlan-analytics' / 'evals' / self.run.name
        dump(ev / 'work' / 'spec.json', {'name': 'jobs', 'records': [{'id': 'q1', 'question': 'How many jobs?'}]})
        dump(ev / 'eval' / 'gold' / 'q1.json', {'columns': ['n'], 'rows': [[12]]})
        self.assertEqual(side_page.snapshot(self.run)['tests']['rows'][0]['known'], '12')

    def test_the_page_names_the_other_builds_in_this_folder_and_a_dashboard_built(self):
        self.assertNotIn('others', side_page.snapshot(self.run))
        other = self.root / '.atlan-analytics' / 'fee-ledger'
        dump(other / 'context' / 'metrics.json', [{'name': 'Fee Take Rate'}, {'name': 'Net Fee Revenue'}])
        dump(other / 'context' / 'queries.json', [{'name': 'fees-by-month'}])
        dump(other / 'interaction.json', {'intent': {'purpose': 'Fee ledger: what each fee brings in'}})
        others = side_page.snapshot(self.run)['others']
        self.assertEqual([(o['name'], o['metrics'], o['queries']) for o in others], [('Fee ledger', 2, 1)])
        self.assertRegex(others[0]['built'], r'^\d{1,2} [A-Z][a-z]{2}$')
        self.assertNotIn('dashboard', side_page.snapshot(self.run))
        interaction = json.loads((self.run / 'interaction.json').read_text())
        dump(self.run / 'interaction.json', {**interaction, 'dashboard': True, 'dashboard_result': 'built'})
        self.assertEqual(side_page.snapshot(self.run)['dashboard'], {'built': True})


if __name__ == '__main__':
    unittest.main()
