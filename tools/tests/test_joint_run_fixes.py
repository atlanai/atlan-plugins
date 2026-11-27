"""What the blind joint run of 2026-11-25 showed, each fixed at its cause: helpers that report back one at a time each
woke the session for a message, the session scripted the scoring and edited a score by hand, a large result reached the
scorer as a pointer, the check was not offered after an answer, and recording an answer returned the whole run."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from analytics_run import SCRIPTS, make_run, stop_pages

sys.path.insert(0, str(SCRIPTS))
import analytics_eval  # noqa: E402
import questions  # noqa: E402


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')


def handed_back(path, message, stop=True):
    """A helper's transcript as the desktop app writes it: its last word is a hand-back tool call."""
    lines = [{'type': 'user', 'message': {'role': 'user', 'content': 'the question'}, 'timestamp': '2026-11-25T17:00:00Z'},
             {'type': 'assistant', 'message': {'role': 'assistant', 'model': 'claude-haiku', 'stop_reason': 'tool_use',
                                               'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash',
                                                            'input': {'command': 'python3 project.py execute q.json'}}]},
              'timestamp': '2026-11-25T17:00:10Z'}]
    if stop:
        lines += [{'type': 'assistant', 'message': {'role': 'assistant', 'stop_reason': 'tool_use',
                                                    'content': [{'type': 'tool_use', 'id': 't2', 'name': 'SubagentHandback',
                                                                 'input': {'message': message}}]},
                   'timestamp': '2026-11-25T17:01:00Z'},
                  {'type': 'user', 'toolEndsTurn': True, 'message': {'role': 'user', 'content': [
                      {'type': 'tool_result', 'tool_use_id': 't2', 'content': 'Report delivered to your caller.'}]},
                   'timestamp': '2026-11-25T17:01:01Z'}]
    write(path, ''.join(json.dumps(line) + '\n' for line in lines))
    return path


def said(path, text):
    """A helper's transcript as the terminal writes it: its last word is a message that ends its turn."""
    lines = [{'type': 'user', 'message': {'role': 'user', 'content': 'the question'}, 'timestamp': '2026-11-25T17:00:00Z'},
             {'type': 'assistant', 'message': {'role': 'assistant', 'stop_reason': 'end_turn',
                                               'content': [{'type': 'text', 'text': text}]},
              'timestamp': '2026-11-25T17:00:30Z'}]
    write(path, ''.join(json.dumps(line) + '\n' for line in lines))
    return path


class CollectedInTheForeground(unittest.TestCase):
    """The check's helpers report back in the background, and each report woke the session for a message: 19 lines in 13
    minutes. One command now waits in the foreground, takes each answer from its helper's transcript, runs its SQL and
    scores it, so the reports queue inside one turn and nothing is left for the session to script."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / '.atlan-analytics' / 'box-office'
        write(self.run / 'run.json', {})
        self.ev = self.root / '.atlan-analytics' / 'evals' / 'box-office'
        write(self.ev / 'eval' / 'cases.jsonl', ''.join(json.dumps({'case_id': i, 'question': 'Question %s?' % i}) + '\n'
                                                       for i in ('a', 'b')))
        write(self.ev / 'eval' / 'answers.jsonl', ''.join(json.dumps({'case_id': i, 'gold_sql': 'SELECT %s' % i}) + '\n'
                                                         for i in ('a', 'b')))
        write(self.ev / 'eval' / 'gold' / 'a.json', {'columns': ['g'], 'rows': [[1550066.18]]})
        write(self.ev / 'eval' / 'gold' / 'b.json', {'columns': ['s'], 'rows': [[0.815]]})
        self.ran = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, results):
        def run_local(run, sql, where):
            self.ran.append(sql)
            return results[sql]
        return mock.patch.object(analytics_eval, '_run_local', side_effect=run_local)

    def collect(self, *items, judges=(), via='local', wait=0):
        a = argparse.Namespace(dir=str(self.ev), run=str(self.run), via=via, agent=list(items), judge=list(judges), wait=wait)
        return analytics_eval.cmd_collect(a)

    def rows(self, variant='baseline'):
        path = self.ev / 'hillclimb' / variant / 'results.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_a_finished_answer_is_saved_run_and_scored_in_one_call_and_an_unfinished_one_waits(self):
        a = handed_back(self.root / 't' / 'a.output', 'Gross sales are $1,550,066.\n'
                        '{"answer": "Gross sales are $1,550,066.", "sql": "SELECT sum(x)", "used_context_ids": ["metric_1"]}')
        b = handed_back(self.root / 't' / 'b.output', '', stop=False)
        with self.runner({'SELECT sum(x)': {'columns': ['g'], 'rows': [[1550066.18]]}}):
            first = self.collect('baseline:a=%s' % a, 'baseline:b=%s' % b)
        self.assertEqual(first['status'], 'waiting')
        self.assertEqual([(r['variant'], r['case'], r['class']) for r in first['recorded']], [('baseline', 'a', 'pass')])
        self.assertEqual(first['pending'], ['baseline:b'])
        self.assertIn('again', first['next'])
        saved = json.loads((self.ev / 'hillclimb' / 'baseline' / 'answers' / 'a_rep0.json').read_text())
        self.assertEqual(saved['sql'], 'SELECT sum(x)')
        self.assertEqual([(r['prompt_id'], r['class'], r['isolation']) for r in self.rows()], [('a', 'pass', 'verified')])

        said(b, 'Sell-through is 81.5%.\n{"answer": "81.5%", "sql": "SELECT rate"}')
        with self.runner({'SELECT rate': {'columns': ['s'], 'rows': [[0.815]]}}):
            second = self.collect('baseline:a=%s' % a, 'baseline:b=%s' % b)
        self.assertEqual(second['status'], 'done')
        self.assertEqual(self.ran, ['SELECT sum(x)', 'SELECT rate'], 'an answer already scored is never run again')
        self.assertEqual(sorted(r['prompt_id'] for r in self.rows()), ['a', 'b'])

    def test_an_answer_the_exact_comparison_refuses_goes_to_a_judge_and_its_verdict_is_recorded(self):
        a = handed_back(self.root / 't' / 'a.output', '{"answer": "About 1.5 million.", "sql": "SELECT approx"}')
        with self.runner({'SELECT approx': {'columns': ['g'], 'rows': [[1500000.0]]}}):
            out = self.collect('baseline:a=%s' % a)
        self.assertEqual(out['status'], 'judge')
        judge = out['judge'][0]
        self.assertEqual((judge['variant'], judge['case']), ('baseline', 'a'))
        self.assertTrue(judge['prompt'] and judge['model'] and judge['verdict_file'])
        self.assertEqual(self.rows(), [], 'no row until the judge has ruled')

        ruled = handed_back(self.root / 't' / 'a.judge.output', '{"pass": 0, "reason": "1.5 million is not 1,550,066.18."}')
        with self.runner({'SELECT approx': {'columns': ['g'], 'rows': [[1500000.0]]}}):
            done = self.collect('baseline:a=%s' % a, judges=['baseline:a=%s' % ruled])
        self.assertEqual(done['status'], 'done')
        self.assertEqual([(r['prompt_id'], r['class'], r['graded_by']) for r in self.rows()], [('a', 'wrong_result', 'judge')])

    def test_a_helper_that_ends_without_its_json_is_named_for_one_more_ask(self):
        a = handed_back(self.root / 't' / 'a.output', 'Gross sales are about $1.55 million.')
        with self.runner({}):
            out = self.collect('baseline:a=%s' % a)
        self.assertEqual(out['no_json'], ['baseline:a'])
        self.assertEqual(self.ran, [])

    def test_the_report_reads_the_why_file_where_the_guide_says_to_write_it(self):
        # The dry run after the joint run: the guide named why.json's place, the report read it only with --why, so the
        # misses printed as unexplained and the session searched the code aloud ("It takes `--why`.").
        rows = [{'prompt_id': 'a', 'rep': 0, 'prompt': 'Question a?', 'grade': {'pass': 1}, 'class': 'pass', 'reason': 'match'},
                {'prompt_id': 'b', 'rep': 0, 'prompt': 'Question b?', 'grade': {'pass': 0}, 'class': 'wrong_result',
                 'reason': '0.817 against 0.815'}]
        write(self.ev / 'hillclimb' / 'baseline' / 'results.jsonl', ''.join(json.dumps(r) + '\n' for r in rows))
        write(self.ev / 'hillclimb' / 'baseline' / 'why.json', {'b': {'why': 'Different filter', 'detail': 'It kept comps.'}})
        out = analytics_eval.cmd_report(argparse.Namespace(dir=str(self.ev), variant='baseline', why=None, raw='raw'))
        self.assertEqual(out['unexplained'], [])
        self.assertIn('It kept comps.', out['report'])

    def test_a_large_result_the_runner_moved_to_a_file_is_scored_from_its_rows(self):
        # The joint run's tickets-per-event answer: the runner returned a pointer, the scorer read it as no result.
        kept = self.run / 'results' / 'execute-1c404f873fe6.json'
        write(kept, {'columns': [{'name': 'g'}], 'rows': [[1550066.18]]})
        pointer = {'status': 'ok', 'result_file': str(kept), 'next': 'Read the relevant fields from this private result artifact.'}
        self.assertEqual(analytics_eval.normalize_result(pointer)['rows'], [[1550066.18]])
        self.assertIn('error', analytics_eval.normalize_result({'status': 'ok', 'result_file': str(self.root / 'elsewhere.json')}))


class OneRuleForBothPasses(unittest.TestCase):
    def test_a_row_with_nothing_in_it_is_no_difference_for_either_pass(self):
        # The joint run's tickets-per-event question: an event with 0 tickets passed in the agent's answer and failed
        # the column-names-only one, from two judges reading one rule two ways.
        self.assertIn('a row on either side whose every value the question asks for is zero or empty',
                      ' '.join(analytics_eval.JUDGE_PROMPT.split()))


class AfterAnAnswer(unittest.TestCase):
    """The answer went through the ask skill, which ended on a line of text: the accuracy check went unoffered and the
    person had to ask for it. Recording the answer also returned the whole run, and the session passed it on as three
    sections nobody asked for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root, local=True)
        meta = json.loads((self.run / 'run.json').read_text())
        write(self.run / 'run.json', {**meta, 'entrypoint': 'project'})  # as a survey records a setup run

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def project(self, *args):
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), *args],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return json.loads(out.stdout)

    def test_the_question_after_an_answer_offers_the_check_until_it_has_run(self):
        asked = self.project('question', 'next', '--accuracy')
        labels = [o['label'] for o in asked['options']]
        self.assertIn('Check how accurate it is', labels)
        self.assertEqual(labels[-1], "I'm done")
        self.assertLessEqual(len(labels), 4)
        self.assertNotIn('Stored on this machine', asked['question'], 'the ready question already said where it lives')
        self.assertNotIn('Check how accurate it is', [o['label'] for o in self.project('question', 'next')['options']])

    def test_the_check_takes_as_many_minutes_as_the_questions_it_can_draw_on(self):
        # The joint run offered "About 20 minutes", found 11 questions and took 12.
        import run_facts
        queries = run_facts.RunFacts(self.run).context['queries']
        check = next(o for o in questions.ready(self.run, accuracy=True)['options'] if o['label'] == 'Check how accurate it is')
        self.assertIn('About %d minutes.' % min(20, max(queries, 1)), check['description'])

    def test_in_a_terminal_what_comes_next_is_the_answer_s_last_line(self):
        # Text just before a native question folds in a terminal: the answer itself would be hidden behind it.
        import os
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'question', 'next',
                              '--accuracy'], capture_output=True, text=True, env={**os.environ, 'CLAUDE_CODE_ENTRYPOINT': 'cli'})
        asked = json.loads(out.stdout)
        self.assertNotIn('options', asked)
        self.assertTrue(asked['text'].startswith('Next: '))
        self.assertIn('check how accurate it is', asked['text'])

    def test_the_next_question_never_offers_the_one_just_answered(self):
        # A live session answered gross sales, then offered "How much did fans pay in total ... (gross sales or GMV)?"
        # as the next question (2026-11-27).
        before = [o for o in questions.after_answer(self.run)['options'] if o.get('description')]
        self.assertTrue(before, 'the fixture offers a question first')
        first = before[0]
        metric = first['description'].split(' · ')[0]
        (self.run / 'answer.json').write_text(json.dumps({'question': first['label'], 'proof': [{'name': metric}],
                                                          'headline': metric + ': 1'}))
        after = [o['label'] for o in questions.after_answer(self.run)['options']]
        self.assertNotIn(first['label'], after)

    def test_the_check_option_reads_the_same_after_an_answer_as_at_the_ready_question(self):
        ready = next(o for o in questions.ready(self.run, accuracy=True)['options'] if o['label'] == 'Check how accurate it is')
        after = next(o for o in questions.after_answer(self.run, accuracy=True)['options'] if o['label'] == 'Check how accurate it is')
        self.assertEqual(ready, after)

    def test_recording_a_step_the_person_saw_returns_no_summary_to_pass_on(self):
        for step in (('asked',), ('checks', '--answer', 'run'), ('dashboard', '--answer', 'declined')):
            out = self.project('journey', *step)
            self.assertNotIn('chat', out, step[0])
            self.assertEqual(out['status'], 'ok')
            self.assertIn('checklist', out, 'the task list still moves on')
        self.assertIn('chat', self.project('journey', 'status'), 'status is where the summary is asked for')

    def test_the_closing_message_keeps_its_caveats_to_what_the_build_found(self):
        # The joint run closed on its own scoring mistake, a disagreement between judges and an accuracy range it worked out.
        rule = self.project('question', 'close')['next']
        self.assertIn('Nothing about the accuracy check', rule)
        self.assertIn('No number the run', rule)


class RicherAnswerCard(unittest.TestCase):
    """A one-number answer's card showed the number, its metric and "from 1 tile": bland beside a breakdown's. The run
    knows more without the session saying anything: the metric's rule in its own words, and the query's other numbers,
    each in its own unit."""

    QUESTION = "what's our fee take rate on tickets?"
    # The joint run's query: the rate, its two parts, the tickets it covers and the dates it spans.
    RESULT = {'columns': [{'name': c} for c in ('fee_take_rate', 'total_fees', 'face_value', 'primary_tickets',
                                                 'first_order_date', 'last_order_date')],
              'rows': [[0.2711581847816059, 330654.18, 1219412.5, 14001, '2025-06-06', '2026-10-31']]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root, local=True)
        write(self.run / 'context' / 'metrics.json', [
            {'name': 'Fee Take Rate', 'description': 'All fees on primary tickets as a share of their face value: service, '
             'facility and demand fees plus order fees, divided by primary face value.',
             'definition': {'formula': 'SUM(f) / NULLIF(SUM(v), 0)', 'output': {'unit': 'fraction (shown as percent)'}}},
            {'name': 'Gross Sales', 'description': 'What fans paid for primary tickets, in US dollars.',
             'definition': {'formula': 'SUM(v) + SUM(f)', 'output': {'unit': 'USD'}}}])

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def card(self):
        import run_facts
        import visuals
        data = visuals.answer_from({'question': self.QUESTION, 'metrics': ['Fee Take Rate']}, self.RESULT,
                                   run_facts.RunFacts(self.run))
        write(self.run / 'answer.json', data)
        return data, visuals.build_spec('answer', self.run, [])

    def test_the_other_numbers_of_a_one_row_answer_show_each_in_its_own_unit(self):
        data, _ = self.card()
        self.assertEqual(data['headline'], 'Fee Take Rate: 27.1%')
        self.assertEqual(data['parts'], [['Total fees', '$330,654'], ['Face value', '$1,219,413'],
                                         ['Primary tickets', '14,001'], ['Period', '6 Jun 2025 to 31 Oct 2026']])

    def test_the_card_carries_the_metric_s_rule_in_its_own_words_and_the_parts(self):
        import visuals
        _, spec = self.card()
        self.assertEqual(spec['rule'], 'All fees on primary tickets as a share of their face value')
        text = ' '.join(visuals.render('answer', spec, plain=True).split())
        for shown in ('27.1%', 'All fees on primary tickets as a share of their face value', 'Total fees', '$330,654',
                      '14,001', '6 Jun 2025 to 31 Oct 2026'):
            self.assertIn(shown, text)
        html = visuals.render('answer', spec, plain=False)
        self.assertIn('$1,219,413', html)

    def test_a_number_without_a_unit_the_run_knows_stays_a_plain_number(self):
        import run_facts
        import visuals
        result = {'columns': ['fee_take_rate', 'events'], 'rows': [[0.27, 43]]}
        data = visuals.answer_from({'question': self.QUESTION, 'metrics': ['Fee Take Rate']}, result, run_facts.RunFacts(self.run))
        self.assertEqual(data['parts'], [['Events', '43']])
        # A row with no metric named and several numbers still leads with nothing it cannot name.
        self.assertIsNone(visuals.answer_from({'question': self.QUESTION}, {'columns': ['a', 'b'], 'rows': [[1, 2]]}))


if __name__ == '__main__':
    unittest.main()
