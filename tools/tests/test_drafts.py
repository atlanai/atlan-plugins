"""The drafts line reports each saved batch of metrics, with real counts, and changes nothing."""
import json
from pathlib import Path
import subprocess
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

    def drafts(self):
        before = {p: p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, '-I', str(fixtures.SCRIPTS / 'project.py'), '--run-dir',
                                 str(self.run.root), 'drafts', '--engine-dir', 'engine'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {p: p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        record = self.run.root / 'drafts-reported.json'
        self.assertEqual({p: v for p, v in after.items() if p != record}, {p: v for p, v in before.items() if p != record})
        return result.stdout.strip()

    def propose(self):
        self.answer('x1', '01.json', {'statements': [
            {'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate', 'definition': 'Won over closed. More text.'}]},
            {'vq_id': 'q2', 'verdict': 'none', 'metrics': []}]})
        self.answer('x1', '02.json', {'statements': [
            {'vq_id': 'q3', 'verdict': 'metrics', 'metrics': [{'name': 'Win %', 'definition': 'Share won'},
                                                               {'name': 'Pipeline'}]}]})

    def test_nothing_saved_yet_prints_nothing(self):
        self.assertEqual(self.drafts(), '')

    def test_the_first_batch_with_metrics_is_not_additional(self):
        self.answer('x1', '01.json', {'statements': [{'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        self.assertEqual(self.drafts(), 'This metric is drafted: Win rate (1 of 4 queries read).')

    def test_each_proposal_batch_names_its_additional_drafts_on_one_line(self):
        self.answer('x1', '01.json', {'statements': [
            {'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        self.assertEqual(self.drafts(), 'This metric is drafted: Win rate (1 of 4 queries read).')
        self.answer('x1', '02.json', {'statements': [
            {'vq_id': 'q2', 'verdict': 'metrics', 'metrics': [{'name': 'Win %'}, {'name': 'Pipeline'}]}]})
        self.answer('x1', '03.json', '{"statements": [{"vq_id": "q4", "verd')  # still being written
        self.assertEqual(self.drafts(), 'These additional metrics are drafted: Win %, Pipeline (2 of 4 queries read).')
        self.assertEqual(self.drafts(), 'These additional metrics are drafted: Win %, Pipeline (2 of 4 queries read).')

    def test_described_queries_are_named_with_the_count(self):
        self.run.write(self.engine / 'm2_prompt.json', {'tasks': [{'queries': [{'vq_id': 'q1'}, {'vq_id': 'q2'}, {'vq_id': 'q3'}]}]})
        self.answer('m2', 'm2_01.json', {'queries': [{'vq_id': 'q1', 'name': 'Weekly active accounts'},
                                                    {'vq_id': 'q2', 'name': 'Pipeline by stage'}]})
        self.assertEqual(self.drafts(), 'These queries are described: Weekly active accounts, Pipeline by stage (2 of 3).')

    def test_batches_saved_together_are_reported_together(self):
        self.answer('x1', '01.json', {'statements': [
            {'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        self.drafts()
        self.answer('x1', '02.json', {'statements': [
            {'vq_id': 'q2', 'verdict': 'metrics', 'metrics': [{'name': 'Pipeline'}, {'name': 'win rate'}]}]})
        self.answer('x1', '03.json', {'statements': [
            {'vq_id': 'q3', 'verdict': 'metrics', 'metrics': [{'name': 'Deal size'}, {'name': 'pipeline'}]},
            {'vq_id': 'q4', 'verdict': 'none', 'metrics': []}]})
        self.assertEqual(self.drafts(), 'These additional metrics are drafted: Pipeline, Deal size (4 of 4 queries read). '
                                        'Found again: win rate.')

    def test_a_merge_names_the_drafts_it_combined_and_the_last_file_closes_the_count(self):
        self.propose()
        self.answer('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': [{'proposal_id': 'q1#0'}, 'q3#0']}]})
        self.assertEqual(self.drafts(), 'Win rate + Win % → Win rate.')
        self.answer('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['q1#0', 'q3#0', 'q3#1']}]})
        (self.engine / 'answers/x1/02.json').write_text(json.dumps({'statements': [{'vq_id': 'q3', 'verdict': 'metrics',
            'metrics': [{'name': 'Win %'}, {'name': 'win %'}]}]}))
        self.assertEqual(self.drafts(), 'Win rate + Win % ×2 → Win rate. 3 drafts are now 1 metric.')
        self.answer('x2', '02.json', {'metrics': [{'name': 'Pipeline', 'sources': ['q3#1']}]})
        self.assertEqual(self.drafts(), 'Kept as their own metric (1): Pipeline. 3 drafts are now 2 metrics.')

    def test_drafts_that_already_share_the_name_merge_into_one(self):
        self.answer('x1', '01.json', {'statements': [
            {'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]},
            {'vq_id': 'q2', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        self.answer('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['q1#0', 'q2#0']}]})
        self.assertEqual(self.drafts(), 'Win rate: 2 drafts merged into one. 2 drafts are now 1 metric.')

    def test_definitions_name_each_completed_metric_and_the_running_total(self):
        self.propose()
        self.run.write(self.engine / 'x3_prompt.json', {'n_metrics': 2, 'tasks': []})
        self.answer('x3', '01.json', {'metrics': [{'name': 'Win rate',
                                                   'description': 'Share of closed deals won. Lost deals count against it.'}]})
        self.assertEqual(self.drafts(), 'This metric is complete: Win rate (1 of 2).')


    def test_follow_wakes_the_chat_only_when_a_step_with_a_chip_finishes(self):
        # A desktop run posted 17 batch lines and talked about the helper between them. The batches belong on the side
        # page; the chat hears only that a step finished, to draw that step's chip.
        import io, threading, time
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        out = io.StringIO()
        watch = threading.Thread(target=lambda: out.write(drafts.follow(
            self.run.root, 'engine', seconds=20, idle=10, interval=0.05, out=out)))
        watch.start()
        stages = []

        def finish(stage):
            stages.append({'stage': stage, 'verdict': 'ok'})
            self.run.write(self.engine / 'manifest.json', {'stages': stages})
            time.sleep(0.3)

        self.propose()
        time.sleep(0.3)
        finish('m2_ingest')
        finish('x1_ingest')
        self.answer('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['q1#0', 'q3#0']}]})
        time.sleep(0.3)
        finish('x2_ingest')
        finish('x3_ingest')
        watch.join(5)
        self.assertFalse(watch.is_alive())
        lines = out.getvalue().splitlines()
        # Described, drafted, merged, defined: the draft step's end is a milestone of its own ("26 draft metrics"); without
        # it the chat went 3.4 minutes with nothing new.
        self.assertEqual(len([l for l in lines if l.startswith('Due:')]), 4, lines)
        self.assertTrue(all('`visual next`' in l for l in lines if l.startswith('Due:')), lines)
        self.assertFalse([l for l in lines if 'drafted' in l or '→' in l], 'no batch line reaches the chat')

    def follow(self, so_far_every):
        import io, threading
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        out = io.StringIO()
        watch = threading.Thread(target=lambda: out.write(drafts.follow(
            self.run.root, 'engine', seconds=20, idle=10, interval=0.05, so_far_every=so_far_every, out=out)))
        watch.start()
        return watch, out

    def test_follow_says_how_far_a_step_has_got_once_a_quiet_interval_has_passed_with_a_new_batch(self):
        # A step can run for minutes with nothing in the chat. While its batches land, the watch wakes the chat every few
        # minutes to show what the step holds so far, and never twice without a new batch.
        import time
        watch, out = self.follow(so_far_every=0.3)
        time.sleep(0.8)
        self.assertEqual(out.getvalue(), '', 'nothing has landed yet')
        self.answer('x1', '01.json', {'statements': [{'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        time.sleep(0.8)
        self.assertEqual(len(out.getvalue().splitlines()), 1, 'one line, though the interval passed twice')
        self.answer('x1', '02.json', {'statements': [{'vq_id': 'q2', 'verdict': 'metrics', 'metrics': [{'name': 'Pipeline'}]}]})
        time.sleep(0.8)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x3_ingest', 'verdict': 'ok'}]})
        watch.join(5)
        self.assertFalse(watch.is_alive())
        lines = [l for l in out.getvalue().splitlines() if l.startswith('Due:')]
        self.assertEqual([('`visual progress`' in l, '`visual next`' in l) for l in lines],
                         [(True, False), (True, False), (False, True)], lines)

    def test_each_so_far_line_hands_the_chat_the_newest_thing_by_name(self):
        # In the desktop app a turn that ends on a widget alone is sent back for words. Live, each so-far chip then ended
        # on "Still writing the rules", so the watch gives the words: the newest thing that landed.
        import time
        watch, out = self.follow(so_far_every=0.3)
        time.sleep(0.1)
        self.answer('x1', '01.json', {'statements': [{'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        time.sleep(0.8)
        self.answer('x3', '001.json', {'metrics': [{'name': 'Gross margin', 'description': 'What the jobs kept: contract '
                                                    'value less labour and materials. The headline.'}]})
        time.sleep(0.8)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x3_ingest', 'verdict': 'ok'}]})
        watch.join(5)
        lines = [l for l in out.getvalue().splitlines() if '`visual progress`' in l]
        self.assertEqual(len(lines), 2, out.getvalue())
        # The words come with the chip, from the same files at the same moment (h8: a line written before the chip named
        # an older rule than the chip did).
        for line in lines:
            self.assertTrue(line.endswith('then end the turn on the line it gives, unchanged.'), line)
            self.assertNotIn('Newest', line)

    def test_the_line_says_what_the_chip_s_headline_does_not(self):
        # The chip leads with the newest thing by name, so the line under it adds to it: the question a described query
        # answers, the other newest names, a rule's first sentence. Never the name alone again.
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        news = lambda: drafts._news(drafts._Engine(self.run.root, 'engine'))
        self.answer('m2', '01.json', {'queries': [{'vq_id': 'q1', 'name': 'Win rate by month',
                                                   'question': ['What share of deals did we win each month?', 'Win rate?']}]})
        self.assertEqual(news(), 'Win rate by month answers: What share of deals did we win each month?')
        self.answer('x1', '01.json', {'statements': [
            {'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}, {'name': 'Pipeline'}]},
            {'vq_id': 'q2', 'verdict': 'metrics', 'metrics': [{'name': 'Deal size'}, {'name': 'Win rate'}, {'name': 'Cycle time'}]}]})
        self.assertEqual(news(), 'Newest draft metrics: Cycle time, Deal size and Pipeline.')
        # In the chip's own case: the chip said "Job gross margin" above a line that said "Job Gross Margin".
        self.answer('x2', '01.json', {'metrics': [{'name': 'Win Rate', 'sources': ['q1#0']}]})
        self.assertEqual(news(), 'Newest metric: Win rate.')
        self.answer('x3', '001.json', {'metrics': [{'name': 'On-Time Win Rate', 'description': 'Deals won on time. More.'}]})
        self.assertEqual(news(), 'Newest rule: On-time win rate. Deals won on time.')
        # h7's line read "Newest rule: On-time completion rate. The share of completed Fixit & Co.": cut at the firm's name.
        self.answer('x3', '002.json', {'metrics': [{'name': 'Late jobs', 'description': 'Completed Fixit & Co. jobs handed '
                                                    'over late. More.'}]})
        self.assertEqual(news(), 'Newest rule: Late jobs. Completed Fixit & Co. jobs handed over late.')

    def test_a_heartbeat_keeps_a_step_that_saves_nothing_from_going_quiet(self):
        # Live, the translation review ran five minutes with nothing in the chat: it saves its answers only at its end.
        import io, threading, time
        from unittest import mock
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        out = io.StringIO()
        with mock.patch.object(drafts, 'heartbeat_line', return_value='Still reviewing each translated tile: 37 translations to review.'):
            watch = threading.Thread(target=lambda: out.write(drafts.follow(
                self.run.root, 'engine', seconds=20, idle=10, interval=0.05, heartbeat=0.3, out=out)))
            watch.start()
            time.sleep(0.8)
            self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x3_ingest', 'verdict': 'ok'}]})
            watch.join(5)
        lines = [l for l in out.getvalue().splitlines() if l.startswith(drafts.HEARTBEAT)]
        self.assertTrue(lines, out.getvalue())
        self.assertTrue(lines[0].endswith('Still reviewing each translated tile: 37 translations to review.'))
        # Helpers advance every step in the dashboard branch, so the watch names each chip that becomes due, once.
        import side_view
        out = io.StringIO()
        with mock.patch.object(side_view, 'due', return_value=['tiles']), \
                mock.patch.object(drafts, 'heartbeat_line', return_value=''):
            drafts.follow(self.run.root, 'engine', seconds=0.5, idle=10, interval=0.05, heartbeat=60, out=out)
        self.assertEqual([l for l in out.getvalue().splitlines() if '`visual next`' in l], [drafts.DUE])
        # Without the flag, the watch stays as the other flows know it.
        out = io.StringIO()
        drafts.follow(self.run.root, 'engine', seconds=0.5, idle=10, interval=0.05, out=out)
        self.assertNotIn(drafts.HEARTBEAT, out.getvalue())

    def test_the_so_far_interval_leaves_no_wait_over_ninety_seconds(self):
        # Live, the chat took about a minute from the watch's line to the chip: at three minutes the longest wait was 4.0.
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        self.assertLessEqual(drafts.SO_FAR_SECONDS + 30, 90, 'no gap over 90 seconds once the chat draws it')

    def test_a_step_ending_starts_the_quiet_interval_again(self):
        # The step's own chip has just been shown: the next so-far line waits for a batch that lands after it.
        import time
        watch, out = self.follow(so_far_every=0.6)
        time.sleep(0.1)
        self.answer('m2', '01.json', {'queries': [{'vq_id': 'q1', 'name': 'Win rate by month'}]})
        time.sleep(0.15)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'm2_ingest', 'verdict': 'ok'}]})
        time.sleep(1.4)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'm2_ingest', 'verdict': 'ok'},
                                                                   {'stage': 'x3_ingest', 'verdict': 'ok'}]})
        watch.join(5)
        lines = [l for l in out.getvalue().splitlines() if l.startswith('Due:')]
        self.assertEqual(len(lines), 2, lines)
        self.assertFalse([l for l in lines if '`visual progress`' in l], lines)

    def test_a_step_already_accepted_has_nothing_so_far_to_show(self):
        # A desktop run drew "30 of 30 queries · finding the numbers they measure" after that step had ended and the
        # merge had begun: its last batches had landed inside the interval, and the step has no chip of its own.
        import time
        watch, out = self.follow(so_far_every=0.4)
        time.sleep(0.1)
        self.answer('x1', '01.json', {'statements': [{'vq_id': 'q1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate'}]}]})
        time.sleep(0.1)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x1_ingest', 'verdict': 'ok'}]})
        time.sleep(1.0)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x1_ingest', 'verdict': 'ok'},
                                                                   {'stage': 'x3_ingest', 'verdict': 'ok'}]})
        watch.join(5)
        self.assertFalse([l for l in out.getvalue().splitlines() if '`visual progress`' in l], out.getvalue())

if __name__ == '__main__':
    unittest.main()
