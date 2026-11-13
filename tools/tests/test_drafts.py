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


    def test_follow_prints_each_batch_as_it_lands_and_stops_when_the_cards_are_accepted(self):
        import io, threading, time
        sys.path.insert(0, str(fixtures.SCRIPTS))
        import drafts
        self.propose()  # saved before the watch: reported once, together
        out = io.StringIO()
        watch = threading.Thread(target=lambda: out.write(drafts.follow(
            self.run.root, 'engine', seconds=20, idle=10, interval=0.05, out=out)))
        watch.start()
        time.sleep(0.4)
        self.answer('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['q1#0', 'q3#0']}]})
        time.sleep(0.4)
        self.run.write(self.engine / 'manifest.json', {'stages': [{'stage': 'x3_ingest', 'verdict': 'ok'}]})
        watch.join(5)
        self.assertFalse(watch.is_alive())
        self.assertEqual(out.getvalue().splitlines(), [
            'These metrics are drafted: Win rate, Win %, Pipeline (3 of 4 queries read).',
            'Win rate + Win % → Win rate.',
            'Stopped watching after 2 lines.'])
        self.assertEqual(self.drafts(), 'Win rate + Win % → Win rate.')  # nothing new: the last line again

if __name__ == '__main__':
    unittest.main()
