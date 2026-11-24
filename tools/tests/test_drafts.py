"""The build wait: one line for each thing the session acts on, with the task list's counts from the saved batches."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import test_local_project as fixtures
from context_state import ContextState


class Drafts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = ContextState(Path(self.temp.name).resolve() / 'run')
        self.run.write(self.run.path, {'entrypoint': 'project', 'sources': []})
        self.engine = self.run.root / 'engine'
        self.engine.mkdir()
        self.run.write(self.engine / 'x1_prompt.json', {'tasks': [
            {'domain': 'Sales', 'queries': [{'vq_id': 'q1'}, {'vq_id': 'q2'}, {'vq_id': 'q3'}]},
            {'domain': 'Finance', 'queries': [{'vq_id': 'q4'}]}]})

    def answer(self, gate, name, payload):
        folder = self.engine / 'answers' / gate
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(payload if isinstance(payload, str) else json.dumps(payload))


    def wait(self, due=(), tasks=True, **kw):
        """One wait in a terminal that keeps a task list (or not), with the chips the run's files make due held to `due`,
        so each test sees only what it is about."""
        import io
        import os
        from unittest import mock
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        import side_view
        out = io.StringIO()
        self.run.write(self.run.root / 'progress.json', {'stage': 'build', 'status': 'running'})
        self.run.write(self.run.root / 'interaction.json', {'version': 1, 'mode': 'active', 'focus': None, 'tasks': tasks})
        with mock.patch.object(side_view, 'due', return_value=list(due)), mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            drafts.wait(self.run.root, 'engine', interval=0.05, out=out, **{'timeout': 5, 'quiet': 0.4, 'batch_every': 0, **kw})
        return json.loads(out.getvalue())

    def test_wait_returns_the_task_list_with_the_count_for_each_batch(self):
        # The team, 2026-11-19: once the side page is open, the task list carries the build, counting each batch, and
        # the session stays in one turn: a turn that ends on a task-list update alone is sent back for words.
        self.answer('x1', '01.json', {'statements': [
            {'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]},
            {'vq_id': 'q2', 'verdict': 'none', 'metrics': []}]})
        first = self.wait()
        self.assertEqual(first['event'], 'batch')
        under = [r for r in first['task_list'] if r['status'] == 'in_progress']
        self.assertEqual(len(under), 1, first['task_list'])
        self.assertIn('Build your context: metrics and saved queries · finding metrics: 1 metric in 2 of 4 questions', under[0]['content'])
        self.assertIn('drafts --wait', first['next'], 'the next wait goes in the same message, so the turn goes on')
        self.assertIn('no words of your own', first['next'].lower())
        # d7 got 13 of these and never set its list: the task-list tool was deferred and never loaded.
        self.assertIn('if it is deferred', first['next'])
        # A watched run copied the list's row into the chat at three of its batches.
        self.assertIn("nor the list's rows", first['next'])
        self.assertEqual(self.wait()['event'], 'quiet', 'nothing new: it says so after a quiet spell, and never repeats a batch')
        self.answer('x1', '02.json', {'statements': [
            {'vq_id': 'q3', 'verdict': 'metrics', 'metrics': [{'name': 'Win %'}, {'name': 'Pipeline'}]}]})
        second = [r for r in self.wait()['task_list'] if r['status'] == 'in_progress'][0]['content']
        self.assertIn('finding metrics: 3 metrics in 3 of 4 questions', second)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x1_ingest', 'verdict': 'ok'}]})
        ended = self.wait()
        self.assertEqual(ended['event'], 'step')
        self.assertIn('`visual next`', ended['next'])
        self.assertEqual(self.wait()['event'], 'quiet', 'a step end is reported once')

    def test_the_build_is_done_only_once_its_links_are_in(self):
        # d3, 2026-11-19: told "done" when the definitions were accepted, the session ended its turn on a "still
        # finishing" line and went to the checks question without the structural pass. The links step comes after.
        stages = [{'stage': s, 'verdict': 'ok'} for s in ('m2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest')]
        self.run.write(self.engine / 'manifest.json', {'stages': stages})
        self.run.write(self.run.root / 'drafts-reported.json', {'steps': ['m2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest']})
        self.assertEqual(self.wait()['event'], 'quiet', 'the links step is still to come')
        self.run.write(self.engine / 'manifest.json', {'stages': stages + [{'stage': 'x4_ingest', 'verdict': 'ok'}]})
        done = self.wait()
        self.assertEqual(done['event'], 'done')
        self.assertIn('`PROJECT check`', done['next'], 'the structural pass comes before any question')

    def test_without_a_task_list_a_batch_is_not_reported(self):
        # A terminal with no task list: nothing to update between steps, so the wait goes on until a step ends.
        self.answer('x1', '01.json', {'statements': [{'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        self.assertEqual(self.wait(tasks=False)['event'], 'quiet')
        self.assertEqual(self.wait(tasks=True)['event'], 'batch')

    def test_a_short_step_a_helper_writes_wakes_the_session_when_it_is_accepted(self):
        # A test run: the dashboard flow's helpers wrote the tile review and the tile questions, and when each was accepted
        # nothing woke the wait: the session sat 3 and 4 minutes until its quiet spell before starting the next step.
        for stage in ('a3b_ingest', 'c3_ingest'):
            self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': stage, 'verdict': 'ok'}]})
            self.assertEqual(self.wait(quiet=30)['event'], 'step', stage)

    def test_a_step_counts_from_zero_as_soon_as_its_prompt_is_written(self):
        # A live run showed the build row without a count for two minutes while its helper wrote the first questions.
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        (self.engine / 'x1_prompt.json').unlink()
        self.run.write(self.engine / 'm2_prompt.json', {'tasks': [{'queries': [{'vq_id': 'q1'}, {'vq_id': 'q2'}]}]})
        engine = drafts._Engine(self.run.root, 'engine')
        under = drafts._under_way(engine)
        self.assertEqual((under[0], under[3]), ('m2', []))
        self.assertEqual(drafts._live(engine, under), 'writing questions: 0 of 2')
        self.answer('m2', '01.json', {'queries': [{'vq_id': 'q1'}]})
        self.assertTrue(drafts._live(engine, drafts._under_way(engine)).startswith('writing questions: 1 of 2'))

    def test_a_chip_that_comes_due_is_reported_once(self):
        # The dashboard flow's helpers advance every step, so its tiles, its questions and the side page's opening come
        # due without a step end the session sees.
        first = self.wait(due=['tiles'])
        # A watched run, told "a step ended" when the side page came due, wrote "Possible metrics are in; combining
        # duplicates next" minutes before that step ended. Something to show is not a step's end.
        self.assertEqual(first['event'], 'due')
        self.assertIn('`visual next`', first['next'])
        self.assertIn('no step has ended', first['next'])
        self.assertEqual(self.wait(due=['tiles'])['event'], 'quiet', 'named once')
        self.assertEqual(self.wait(due=['tiles', 'connect'])['event'], 'due', 'the side page is ready to open')

    def test_a_step_end_asks_for_no_words_of_its_own(self):
        # d3: on each step's end the session wrote the next step's Doing line ("Now I'm merging...") under a task list
        # that already said it.
        self.run.write(self.run.root / 'progress.json', {'stage': 'build', 'status': 'running'})
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'm2_ingest', 'verdict': 'ok'}]})
        said = self.wait()['next']
        self.assertIn("Only a step that no chip names gets a start line", said)
        # h18t set the next subagent up first, wrote its own line meanwhile, and dropped the step's chip.
        self.assertLess(said.index('`visual next`'), said.index('subagent'), 'the chip first')

    def test_a_step_under_way_shows_its_count_and_a_time_left_from_its_own_pace(self):
        # The time left follows the step's pace since its prompt was written, so it moves as the work does.
        import os, time
        self.run.write(self.engine / 'x3_prompt.json', {'n_metrics': 4, 'tasks': []})
        # A step set up three hours before its helpers started (a resumed run) still reads its own pace.
        os.utime(self.engine / 'x3_prompt.json', (time.time() - 10800, time.time() - 10800))
        earlier = ['m2_ingest', 'x1_ingest', 'x2_ingest']
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': s, 'verdict': 'ok'} for s in earlier]})
        self.run.write(self.run.root / 'drafts-reported.json', {'steps': earlier})
        now = time.time()
        for i, name in enumerate(('A', 'B', 'C')):
            self.answer('x3', '%02d.json' % i, {'metrics': [{'name': name}]})
            os.utime(self.engine / 'answers' / 'x3' / ('%02d.json' % i), (now - 120 + 60 * i, now - 120 + 60 * i))
        row = [r for r in self.wait()['task_list'] if r['status'] == 'in_progress'][0]['content']
        self.assertTrue(row.endswith('3 of 4 written (about 1 min left)'), row)
        self.answer('x3', '03.json', {'metrics': [{'name': 'D'}]})
        self.assertEqual(self.wait()['event'], 'ready', 'every share back comes first')
        row =[r for r in self.wait()['task_list'] if r['status'] == 'in_progress'][0]['content']
        self.assertTrue(row.endswith('4 of 4 written (nearly done)'), row)


    def test_every_share_of_the_definitions_back_wakes_the_session_at_once(self):
        # A terminal run with no task list sat 3.6 minutes after the last share came back: only this session advances
        # the definitions, and nothing woke its wait until the quiet spell.
        import os, time
        self.run.write(self.engine / 'x3_prompt.json', {'n_metrics': 2, 'tasks': []})
        earlier = ['m2_ingest', 'x1_ingest', 'x2_ingest']
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': s, 'verdict': 'ok'} for s in earlier]})
        self.run.write(self.run.root / 'drafts-reported.json', {'steps': earlier})
        self.answer('x3', '01.json', {'metrics': [{'name': 'A'}]})
        self.assertEqual(self.wait(tasks=False)['event'], 'quiet', 'one share is still out')
        self.answer('x3', '02.json', {'metrics': [{'name': 'B'}]})
        ready = self.wait(tasks=True, quiet=30)
        self.assertEqual(ready['event'], 'ready', 'before any task-list update')
        self.assertIn('`ENGINE advance` once', ready['next'])
        self.assertEqual(self.wait(tasks=False)['event'], 'quiet', 'said once')
        later = time.time() + 5
        os.utime(self.engine / 'answers' / 'x3' / '02.json', (later, later))
        self.assertEqual(self.wait(tasks=False, quiet=30)['event'], 'ready', 'a refused card written again is back')

    def test_the_task_list_moves_at_most_once_a_minute(self):
        # h15t: a task-list update for every batch woke the session 24 times in eight minutes, and after several of them
        # it wrote a line of its own.
        self.answer('x1', '01.json', {'statements': [{'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        self.assertEqual(self.wait(batch_every=60)['event'], 'batch')
        self.answer('x1', '02.json', {'statements': [{'vq_id': 'q2', 'verdict': 'metrics', 'metrics': [{'name': 'Pipeline'}]}]})
        self.assertEqual(self.wait(batch_every=60)['event'], 'quiet', 'the next batch waits for the minute')
        self.assertEqual(self.wait(batch_every=0)['event'], 'batch')

    def test_every_event_says_first_that_no_words_of_its_own_go_in_the_chat(self):
        # Runs on every host wrote their own lines between waits ("the build continues", "3 merged so far").
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        for event, said in drafts.WAITED.items():
            if event != 'quiet':
                self.assertTrue(said.startswith('Add no words of your own'), event)
            self.assertNotIn('still', said.lower(), event)
        # The one exception (PR 205's review: 12 to 18 minute silences): after about 3 quiet minutes, one line, never repeated.
        self.assertIn('about 3 minutes, write one line', drafts.WAITED['quiet'])
        self.assertIn('never a line you already wrote', drafts.WAITED['quiet'])


if __name__ == '__main__':
    unittest.main()
