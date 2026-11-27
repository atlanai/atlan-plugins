"""The Test questions tab, read the way the dashboard's Improve tests read: what the check found in one sentence, why
questions were missed, the misses first, and a question that opens on how it came out. People found the tab "a bunch of
information" with no way to tell what happened (2026-11-26)."""
import json
import shutil
import subprocess
import unittest

from test_side_page_view import DRIVER, PAGE


def row(n, question, right, cause=None, used=(), earlier=None, names=False, metric='Tickets'):
    agent = {'right': right, 'answer': 'An answer.', 'value': '4', 'sql': 'SELECT 1', 'why': 'A has 4 against 5.' if right is False else '',
             'cause': {'shown': cause, 'detail': 'It grouped by date, not by show.'} if cause else None, 'used': list(used),
             'rows': {'columns': ['show', 'tickets'], 'rows': [['A', 4]]}}
    return {'n': n, 'id': 'q%d' % n, 'question': question, 'metric': metric, 'from': 'tile', 'known': '5',
            'known_rows': {'columns': ['show', 'tickets'], 'rows': [['A', 5]]}, 'agent': agent if right is not None else None,
            'names': {'right': names, 'answer': '', 'value': '9', 'sql': '', 'why': '', 'cause': None, 'used': [], 'rows': None},
            'earlier': [{'when': '25 Nov', 'right': earlier, 'question': question}] if earlier is not None else []}


ROWS = [row(1, 'Tickets per show?', False, 'Different filter', ['Primary Tickets Issued'], earlier=True),
        row(2, 'Gross sales?', True, used=['Gross Sales', 'a saved query'], earlier=True, metric='Gross Sales'),
        row(3, 'Sell-through per event?', False, 'Different definition', ['Sell-Through', 'Event Capacity'], earlier=True,
            metric='Sell-Through')]
TESTS = {'total': 3, 'answered': 3, 'state': 'scored', 'rows': ROWS, 'name': 'box-office',
         'score': {'agent': [1, 3], 'names': [0, 3], 'scored_at': '2026-11-26T10:00:00+00:00'}, 'changed_after': False}


@unittest.skipUnless(shutil.which('node'), 'Node renders the page script')
class TestQuestionsTab(unittest.TestCase):
    def render(self, tests=TESTS, **ui):
        result = subprocess.run([shutil.which('node'), '-e', DRIVER, str(PAGE)], capture_output=True, text=True, timeout=60,
                                input=json.dumps({'snapshot': {'flow': 'build', 'tests': tests}, 'ui': dict({'list': 'tests'}, **ui)}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_it_opens_on_what_the_check_found(self):
        html = self.render()
        self.assertIn('Your agent got 1 of 3 right.', html)
        self.assertIn('With column names only, 0 of 3.', html)
        self.assertIn('2 questions got worse since the check on 25 Nov, and none got better.', html)

    def test_the_misses_are_grouped_by_why(self):
        html = self.render()
        self.assertIn('Why 2 were missed', html)
        self.assertIn('data-v="c:Different filter"', html)
        self.assertIn('Question 1', html)
        self.assertIn('Question 3', html)

    def test_the_misses_come_first_each_with_a_plain_second_line(self):
        html = self.render()
        self.assertIn('Questions, missed first', html)
        order = [html.index('data-t="%d"' % n) for n in (1, 3, 2)]
        self.assertEqual(order, sorted(order))
        self.assertIn('Different filter · right on 25 Nov', html)
        self.assertIn('Tests Gross Sales', html)
        self.assertNotIn('Known: 5', html, 'once scored a row says why, not its raw values')

    def test_a_cause_shows_only_its_questions(self):
        html = self.render(tfilter='c:Different definition')
        self.assertIn('data-t="3"', html)
        self.assertNotIn('data-t="1"', html)

    def test_a_question_opens_on_how_it_came_out(self):
        html = self.render(test='1')
        self.assertIn('Missed: different filter. It was right in the check on 25 Nov.', html)
        for panel in ('Why it was missed', 'Your agent’s answer', 'The known answer'):
            self.assertIn(panel, html)
        self.assertIn('It grouped by date, not by show.', html)

    def test_one_source_says_what_a_question_tests(self):
        # The page said "Tests Gross Sales" and "Your agent used Gross Sales, a saved query", read as two sources that
        # disagreed (2026-11-27). What the agent drew on is its own claim: it sits under its answer, joined in words.
        html = self.render(test='2')
        self.assertNotIn('Your agent used', html)
        self.assertIn('What it drew on: Gross Sales and a saved query', html)
        self.assertNotIn('Used Gross Sales', self.render())

    def test_a_miss_without_the_tested_metric_says_so(self):
        # Question 7 tested Paid Sell-Through Against Venue Capacity and the agent answered from Sell-Through.
        self.assertIn('It answered from Primary Tickets Issued, not from Tickets.', self.render(test='1'))
        self.assertNotIn(', not from ', self.render(test='3'), 'question 3 used the metric it tests')

    def test_while_it_runs_it_counts_the_answers(self):
        running = dict(TESTS, state='running', answered=2, score=None)
        self.assertIn('2 of 3 answered so far', self.render(running))


if __name__ == '__main__':
    unittest.main()
