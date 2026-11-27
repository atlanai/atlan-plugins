"""The card a helper hands to the chat, on each route to a moment."""
import argparse
from contextlib import chdir, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from analytics_run import SCRIPTS, make_run, stop_pages
from test_local_project import readbacks
import test_receipt_recording as receipts

sys.path.insert(0, str(SCRIPTS))
import project
import side_page
import side_view


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def cli(*args, data=None, success=True):
    out = io.StringIO()
    with patch.object(sys, 'argv', ['project.py', *map(str, args)]), patch.object(sys, 'stdin', io.StringIO(json.dumps(data))), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = project.main()
    if success and code:
        raise AssertionError(out.getvalue())
    return out.getvalue().strip()


ANSWER = {'question': 'How much did repairs cost?', 'metrics': ['Rework Cost'],
          'result': {'columns': ['rework_cost', 'sales'], 'rows': [[120, 64]]}}
OLD = '2001-01-01T00:00:00+00:00'


class RouteMoments(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.addCleanup(stop_pages, self.root)
        self.run = make_run(self.root / 'a folder', saved=False)
        self.args = ['--run-dir', str(self.run)]
        meta = json.loads((self.run / 'run.json').read_text())
        write(self.run / 'run.json', {**meta, 'entrypoint': 'project'})
        self.state = self.run / 'view' / 'state.json'
        self.terminal = patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''})
        self.terminal.start()
        self.addCleanup(self.terminal.stop)

    def test_location_lists_the_runs_already_in_the_folder(self):
        # A live session asked `location` for "the one run in this folder", as retrieval.md says: it only ever names a new
        # run's folder, so the existing setup was never found (2026-11-27).
        folder = self.root / 'project'
        run = make_run(folder / '.atlan-analytics', saved=False)
        out = json.loads(cli('location', '--workspace', folder))
        self.assertEqual(out['existing'], [str(run)])
        self.assertNotEqual(out['run_dir'], str(run))
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertEqual(json.loads(cli('location', '--workspace', empty))['existing'], [])

    def test_direct_edit_is_one_widget_with_its_own_page_instruction(self):
        with patch.object(side_page, 'reminder', side_effect=lambda run, tab: 'Open the page at #l=' + tab):
            shown = cli(*self.args, 'visual', 'edit', '--widget', '--input', '-',
                        data={'metric': 'Rework Cost', 'values': [['Cost', 10, 20]]})
        self.assertIn('Your agent:', shown)
        self.assertLess(shown.index('Your agent:'), shown.index('Definition changed'))
        self.assertEqual(shown.count('side view: '), 1)
        self.assertIn('#l=metrics', shown)
        self.assertEqual(side_view.seen(self.run), ['agent', 'edit'])

    def test_a_terminal_handoff_keeps_the_welcome_and_the_answer_under_one_instruction(self):
        shown = project._answer_card(self.run, ANSWER, ANSWER['result'])['show_first']
        self.assertEqual(shown.count('side view: '), 1)
        self.assertLess(shown.index('Your agent:'), shown.index(ANSWER['question']))
        self.assertNotIn('<style>', shown)
        self.assertEqual(side_view.seen(self.run), ['agent', 'answer'])

    def test_first_answer_card_resets_each_sitting(self):
        write(self.state, {'moments': [{'kind': 'answer', 'at': OLD, 'key': 'old | old'}]})
        args = [*self.args, 'visual', 'answer', '--widget', '--input', '-']
        first = cli(*args, data={'question': 'First?', 'headline': 'Cost: 1'})
        self.assertIn('class="a mc"', first)
        second = cli(*args, data={'question': 'Second?', 'headline': 'Cost: 2'})
        self.assertNotIn('class="a mc"', second)
        self.assertNotIn('Your agent:', second)

    def test_warehouse_plan_names_the_followup_and_it_hands_over_the_answer(self):
        run = project.Project(self.run)
        run.bind_warehouse('snowflake', 'example_read_query')
        payload = readbacks()
        payload['artifacts'][0]['metadata']['definition']['dialect'] = 'snowflake'
        run.publication(payload)
        request = self.run / 'request.json'
        write(request, {'readbacks': payload, 'artifact_id': 'sql_query_example',
                        'answer': {k: ANSWER[k] for k in ('question', 'metrics')}})
        write(self.state, {'moments': [{'kind': k, 'at': OLD} for k in side_view.MIDWAY]})
        with patch.object(side_page, 'reminder', return_value='Open the page') as reminder:
            plan = json.loads(cli(*self.args, 'answer', request))
        self.assertEqual(plan['status'], 'execute_in_host')
        self.assertNotIn('side_page', plan)
        reminder.assert_not_called()
        follow = 'PROJECT --run-dir %s visual answer --input -' % shlex.quote(str(self.run))
        self.assertIn(follow, plan['next'])
        for field in ('question', 'metrics', 'result', 'columns', 'rows'):
            self.assertIn('"%s"' % field, plan['next'])
        example, _ = json.JSONDecoder().raw_decode(plan['next'][plan['next'].index('{'):])
        self.assertEqual(example['question'], ANSWER['question'])
        self.assertEqual(example['metrics'], ANSWER['metrics'])
        self.assertIsInstance(example['result']['rows'], list)
        self.assertIsInstance(example['result']['columns'], list)
        with patch.object(side_page, 'reminder', side_effect=lambda run, tab: 'Open the page at #l=' + tab):
            out = json.loads(cli(*shlex.split(follow)[1:], data=ANSWER))
        self.assertIn('show_first', out['visual'])
        shown = out['show_first']
        self.assertIn('£120', shown)
        self.assertIn('Your agent:', shown)
        self.assertIn('#l=metrics', shown)
        self.assertEqual(shown.count('side view: '), 1)

    def test_a_text_answer_shows_the_returned_value_with_its_welcome(self):
        given = {'question': 'Which show sold the most?', 'metrics': [],
                 'result': {'columns': ['show_name'], 'rows': [['Winter Lights: Encore']]}}
        shown = project._answer_card(self.run, given, given['result'])
        self.assertIn('show_first', shown)
        self.assertIn('**Winter Lights: Encore**', shown['show_first'])
        self.assertIn(given['question'], shown['show_first'])
        self.assertEqual(side_view.seen(self.run), ['agent', 'answer'])

    def test_a_query_only_publication_and_returning_welcome_count_saved_queries(self):
        payload = readbacks()
        query = payload['artifacts'][0]
        write(self.run / 'context' / 'queries.json', [{'name': query['name'], **query['metadata']}])
        (self.run / 'context' / 'metrics.json').unlink()
        with patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}), \
                patch.object(side_view, 'due', return_value=[]):
            result = json.loads(cli(*self.args, 'publication', '-', data=payload))
            with self.subTest(moment='saved'):
                self.assertIn('Saved to Atlan Trident', result.get('show_first', ''))
                self.assertIn('1 saved query', result['show_first'])
            write(self.state, {'moments': [{'kind': 'saved', 'at': OLD}]})
            with self.subTest(moment='welcome'):
                shown = side_view.welcome(self.run)
                self.assertIn('Your agent:', shown)
                self.assertIn('1 saved query', shown)

    def test_emitting_a_draft_for_an_explicit_run_hands_its_chip_to_that_run(self):
        from test_eval_scope import SCOPE
        write(self.run / 'engine' / 'metric_cards.json', {'metrics': [{'metric_ref': 'cost', 'name': 'Cost'}]})

        def scope(*args):
            proc = subprocess.run([sys.executable, str(SCOPE), *map(str, args)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return json.loads(proc.stdout)

        gen = Path(scope('inputs', '--project', self.root, '--run', self.run)['gen'])
        write(gen / 'work' / 'spec.json', {'records': [{'id': 'a', 'question': 'How much?', 'golden_sql': 'SELECT 1'}]})
        other = make_run(self.root / '.atlan-metrics')
        result = scope('emit', '--gen', gen, '--name', 'cost')
        self.assertIn('1 test question written', result.get('show_first', ''))
        self.assertEqual(side_view.seen(self.run), ['agent', 'questions'])
        self.assertEqual(side_view.seen(other), [])

        from test_eval_scope import check
        loaded = check.cmd_load(argparse.Namespace(project=str(self.root), run=str(self.run), file=result['set']))
        write(Path(loaded['pending'][0]['result_file']), {'columns': ['n'], 'rows': [[1]]})
        scored = check.cmd_gold(argparse.Namespace(evals=loaded['evals'], transcript=[], allow_missing=False))
        self.assertNotIn('show_first', scored)

    def test_trident_only_answer_uses_supplied_units_and_leaves_no_local_state(self):
        folder = self.root / 'empty'
        folder.mkdir()
        with chdir(folder), patch.object(side_page, 'reminder', side_effect=AssertionError('no page without a run')):
            for unit, expected in (({}, '120'), ({'unit': 'EUR'}, '€120')):
                for widget in ([], ['--widget']):
                    out = json.loads(cli('visual', 'answer', '--input', '-', *widget, data={**ANSWER, **unit}))
                    self.assertIn('show_first', out['visual'])
                    shown = out['show_first']
                    self.assertIn(expected, shown)
                    self.assertIn(ANSWER['question'], shown)
                    self.assertEqual('class="a mc"' in shown, bool(widget))
                    self.assertNotRegex(shown, r'[$£]|€64|#l=metrics')
                    self.assertEqual(shown.count('side view: '), 1)
                    # With no run, nothing says where a metric came from, and there is no side page to point to: on
                    # real data the card named Gross Sales "a saved query" and sent the person to a page (2026-11-26).
                    self.assertIn('Rework cost', shown)
                    self.assertNotIn('a saved query', shown)
                    self.assertNotIn('side page', shown.split('side view: ')[0])
        self.assertEqual(list(folder.iterdir()), [])
        for args in (('visual', 'answer'), ('visual', 'edit', '--input', '-'), ('visual', 'next')):
            self.assertEqual(json.loads(cli(*args, data=ANSWER, success=False))['status'], 'blocked')

    def test_a_resume_and_the_checklist_welcome_a_returning_user_once(self):
        # Not `journey status`: agents read it to look around before answering, and a welcome handed over there went
        # undrawn and was marked shown, so the answer card after it came without one (2026-11-26).
        write(self.state, {'moments': [{'kind': k, 'at': OLD} for k in side_view.MIDWAY if k != 'connect']})
        with patch.object(side_view, 'due', return_value=[]):
            self.assertNotIn('show_first', json.loads(cli(*self.args, 'journey', 'status')))
        self.assertNotIn('agent', side_view.seen(self.run))
        for args in (('journey', 'resume'), ('checklist',)):
            with self.subTest(args=args):
                write(self.state, {'moments': [{'kind': k, 'at': OLD} for k in side_view.MIDWAY if k != 'connect']})
                with patch.object(side_view, 'due', return_value=[]):
                    out = json.loads(cli(*self.args, *args))
                    self.assertIn('show_first', out)
                    self.assertIn('Your agent:', out['show_first'])
                    self.assertIn('show_first', out['visual'])
                    self.assertEqual(out['show_first'].count('side view: '), 1)
                    self.assertNotIn('show_first', json.loads(cli(*self.args, *args)))
        with patch.object(side_page, 'reminder', side_effect=lambda run, tab: 'Open the page at #l=' + tab):
            self.assertIn('#l=tests', cli(*self.args, 'visual', 'agent'))

    def test_incomplete_warehouse_rows_ask_for_the_columns(self):
        bad = {**ANSWER, 'metrics': [], 'result': {'rows': [[1]]}}
        out = json.loads(cli('visual', 'answer', '--input', '-', data=bad, success=False))
        self.assertEqual(out['status'], 'blocked')
        self.assertIn('columns', out['message'])

    def test_show_card_returns_the_same_drawn_answer_for_native_rows(self):
        shown = side_view.show_card(self.run, 'answer', ANSWER)
        self.assertIn('Your agent:', shown)
        self.assertTrue(shown.splitlines()[-1].startswith('side view: '))
        self.assertEqual(shown.count('side view: '), 1)

    def test_helper_handoffs_use_the_runs_explicit_widget_choice(self):
        cli(*self.args, 'visual', 'agent', '--widget')
        shown = project._answer_card(self.run, ANSWER, ANSWER['result'])['show_first']
        self.assertIn('class="a mc"', shown)

    def test_a_resumed_checklist_still_welcomes_when_a_build_chip_is_due(self):
        write(self.state, {'moments': [{'kind': 'build', 'at': OLD}]})
        self.assertIn('context', side_view.due(self.run))
        result = json.loads(cli(*self.args, 'checklist'))
        self.assertIn('Your agent:', result['show_first'])
        self.assertEqual(result['show_first'].count('side view: '), 1)
        self.assertIn('show_first', result['visual'])
        self.assertIn('context', side_view.due(self.run))

    def test_rerunning_the_first_text_answer_as_a_widget_keeps_its_welcome(self):
        first = json.loads(cli(*self.args, 'visual', 'answer', '--input', '-', data=ANSWER))['show_first']
        self.assertIn('Your agent:', first)
        shown = cli(*self.args, 'visual', 'answer', '--widget')
        self.assertIn('Your agent:', shown)
        self.assertIn('class="a mc"', shown)
        self.assertEqual(shown.count('side view: '), 1)
        self.assertEqual(side_view.seen(self.run).count('agent'), 1)
        self.assertNotIn('Your agent:', cli(*self.args, 'visual', 'answer', '--widget'))

    def test_terminal_after_desktop_uses_its_own_display(self):
        with patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}):
            cli(*self.args, 'visual', 'agent')
        out = project._answer_card(self.run, ANSWER, ANSWER['result'])
        self.assertNotIn('<style>', out['show_first'])

    def test_deferred_widget_tools_load_silently(self):
        out = cli(*self.args, 'visual', 'agent', '--widget')
        step = json.loads(out.split('side view: ')[-1])['next']
        self.assertRegex(step, r'(silently|without a word|without announcing)')
        self.assertIn('read_me', step)
        # "Got the number. Showing the answer card now." came before the draw: the line said nothing of before (2026-11-27).
        self.assertIn('before or after', step)


class AccuracyMoment(unittest.TestCase):
    def setUp(self):
        import test_test_questions_page as fixtures
        self.check = fixtures.TestQuestions()
        self.check.setUp()
        self.addCleanup(self.check.tearDown)
        self.check.scored()
        self.args = argparse.Namespace(dir=str(self.check.ev), before='raw', after='baseline')

    def test_a_read_only_run_still_hands_over_its_score(self):
        import analytics_eval
        with patch.object(side_view, 'note', side_effect=PermissionError), \
                patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            out = analytics_eval.cmd_before_after(self.args)
        self.assertIn(out['report'], out['show_first'])
        self.assertEqual(out['correct'], {'before': 0, 'after': 3})

    def test_showing_an_earlier_check_records_the_accuracy_moment_on_its_run(self):
        import analytics_eval
        newer = self.check.ev.with_name(self.check.ev.name + '-2')
        write(newer / 'work' / 'spec.json', {'records': [{'id': 'a', 'question': 'Later?'}]})
        out = analytics_eval.cmd_before_after(self.args)
        self.assertIn('Your agent: 1 metric', out['show_first'])
        self.assertEqual(side_view.seen(self.check.run), ['agent', 'accuracy'])

    def test_eval_and_demo_hand_over_the_score_in_the_hosts_format(self):
        import analytics_eval
        check = self.check
        for desktop in (False, True):
            with self.subTest(desktop=desktop), patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop' if desktop else ''}):
                write(check.run / 'view' / 'state.json', {'moments': []})
                with patch.object(side_page, 'reminder', side_effect=lambda run, tab: 'Open the page at #l=' + tab):
                    out = analytics_eval.cmd_before_after(self.args)
                self.assertIn('show_first', out)
                shown = out['show_first']
                self.assertIn(out['widget'] if desktop else out['report'], shown)
                self.assertIn(out['bars'], out['report'])
                self.assertIn('Your agent: 1 metric', shown)
                self.assertIn('#l=tests', shown)
                self.assertEqual(shown.count('side view: '), 1)
                self.assertEqual(side_view.seen(check.run), ['agent', 'accuracy'])
                self.assertIn('show_first', out['visual'])
                write(check.ev / 'eval' / 'split.json', {'test_ids': ['a', 'b', 'c']})
                demo = subprocess.run([sys.executable, str(SCRIPTS.parents[1] / 'analytics-demo/scripts/accuracy.py'),
                                       'result', '--evals', str(check.ev), '--widget'], capture_output=True, text=True)
                self.assertEqual(demo.returncode, 0, demo.stdout + demo.stderr)
                got = json.loads(demo.stdout)
                self.assertIn(out['widget'] if desktop else out['report'], got['show_first'])
                self.assertEqual(got['visual'], out['visual'])
                self.assertEqual(got['widget'], out['widget'])


class SavedQueryRoutes(unittest.TestCase):
    def setUp(self):
        from test_local_project import SQLiteFlow
        SQLiteFlow.setUp(self)
        self.addCleanup(stop_pages, self.root)
        sys.path.insert(0, str(SCRIPTS.parents[1] / 'analytics-demo/scripts'))
        self.addCleanup(sys.path.remove, str(SCRIPTS.parents[1] / 'analytics-demo/scripts'))
        self.query = {'name': 'work-total', 'display_name': 'Work total', 'description': 'Total cost.',
                      'definition': {'spec_version': '1.0-draft.1', 'question': 'What did the work cost?',
                                     'sql': 'SELECT SUM(cost) FROM site.work', 'dialect': 'sqlite',
                                     'extensions': {'metrics_agent': {'vq_id': 'h-work'}}}}
        self.change = {'name': 'Work total', 'changes': [{'path': 'sql', 'value': 'SELECT MAX(cost) FROM site.work'}]}
        write(self.run.context / 'metrics.json', [])
        write(self.run.context / 'queries.json', [self.query])

    def test_an_older_set_shows_after_another_set_and_only_once(self):
        from test_eval_scope import check
        sets = self.estate / '.atlan-analytics' / 'evals' / 'sets'
        for name in ('first', 'older'):
            file = sets / (name + '.jsonl')
            write(file, {'id': 'total', 'question': 'What did the work cost?', 'sql': self.query['definition']['sql']})
            os.utime(file, (1000, 1000) if name == 'first' else (500, 500))

        def load(name):
            return check.cmd_load(argparse.Namespace(project=str(self.estate), run=str(self.run.root),
                                                     file=str(sets / (name + '.jsonl'))))

        self.assertIn('from your set first', load('first')['show_first'])
        self.assertIn('from your set older', load('older').get('show_first', ''))
        self.assertNotIn('show_first', load('first'))
        self.assertNotIn('show_first', load('older'))

    def test_edit_checks_the_saved_query_and_mirrors_its_engine_copy(self):
        import edit_metric
        export = self.run.root / 'engine-export' / 'queries.json'
        layer = self.run.root / 'engine' / 'verified_queries.json'
        write(export, [self.query])
        write(layer, {'objects': [{'vq_id': 'h-work', 'sql': self.query['definition']['sql']}]})
        done = edit_metric.edit_metric(self.run, self.change)
        # The rule change's reply named the old version's file (2026-11-27): the result says to keep it out.
        self.assertIn('never its file', done['next'])
        self.assertEqual(done['status'], 'ok')
        self.assertIn('Definition changed', done['show_first'])
        self.assertIn('34.8', done['show_first'])
        self.assertIn('20', done['show_first'])
        kept = Path(done['previous'])
        self.assertNotIn(self.run.root, kept.parents)
        self.assertEqual(json.loads(kept.read_text())['definition'], self.query['definition'])
        edited = self.run.read(self.run.context / 'queries.json')[0]['definition']
        self.assertEqual(edited['sql'], self.change['changes'][0]['value'])
        self.assertEqual(self.run.read(export)[0]['definition'], edited)
        self.assertEqual(self.run.read(layer)['objects'][0]['sql'], edited['sql'])
        with self.assertRaisesRegex(project.ProjectError, 'old rule stays'):
            edit_metric.edit_metric(self.run, {'name': 'work-total', 'changes': [{'path': 'sql', 'value': 'SELECT missing'}]})
        self.assertEqual(self.run.read(self.run.context / 'queries.json')[0]['definition'], edited)
        self.assertEqual(self.run.read(export)[0]['definition'], edited)

    def test_a_saved_query_warehouse_edit_waits_for_checked_rows_and_rolls_back_on_failure(self):
        import edit_metric
        self.query['definition']['dialect'] = 'snowflake'
        write(self.run.context / 'queries.json', [self.query])
        self.run.bind_warehouse('snowflake', 'example_read_query')
        name = self.query['name']
        self.run.check_query({'name': name, 'result': {'rows': [[35]]}})
        plan = edit_metric.edit_metric(self.run, self.change)
        self.assertEqual((plan['status'], plan['sql']), ('run_on_warehouse', self.change['changes'][0]['value']))
        self.assertNotIn('show_first', plan)
        out = edit_metric.edit_metric(self.run, {'name': name, 'result': {'rows': [[20]]}})
        self.assertIn('35 → 20', out['show_first'])
        before = self.run.read(self.run.context / 'queries.json')
        edit_metric.edit_metric(self.run, {'name': name, 'changes': [{'path': 'sql', 'value': 'SELECT 2'}]})
        with self.assertRaisesRegex(project.ProjectError, 'old rule stays'):
            edit_metric.edit_metric(self.run, {'name': name, 'result': {'error': 'Query failed'}})
        self.assertEqual(self.run.read(self.run.context / 'queries.json'), before)
        self.assertEqual(list(self.run.root.glob('edit-pending*.json')), [])
        self.assertEqual(self.run.read(self.run.context / 'candidate-checks.json')['sql_query:' + name]['checked_value'], 20)


class SavedMoment(unittest.TestCase):
    def test_finalized_receipts_hand_over_the_saved_card(self):
        case = receipts.ReceiptRecording()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for item in case.artifacts:
            case.release(item)
        case.edges()
        self.assertNotIn('show_first', case.assemble())
        with patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}):
            result = case.assemble(True)
        self.assertEqual(result['artifacts'], 2)
        self.assertIn('show_first', result)
        self.assertIn('Saved to Atlan Trident', result['show_first'])
        self.assertNotIn('Your agent:', result['show_first'])
        self.assertIn('show_first', result['visual'])
        self.assertEqual(side_view.seen(case.run), ['saved'])
