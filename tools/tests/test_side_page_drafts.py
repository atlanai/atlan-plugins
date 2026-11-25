"""The side page's metric list fills in as the build saves each batch: drafts, then merges, then each metric's rule."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
from run_facts import RunFacts
import side_page


class Batches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name).resolve() / 'run'
        self.engine = self.run / 'engine'
        self.engine.mkdir(parents=True)
        (self.run / 'run.json').write_text(json.dumps({'entrypoint': 'project', 'sources': []}))
        self.save('x1', '01.json', {'statements': [
            {'vq_id': 'h-1', 'verdict': 'metrics', 'metrics': [{'name': 'Win rate', 'definition': 'Won deals over closed deals.'}]},
            {'vq_id': 'h-2', 'verdict': 'metrics', 'metrics': [{'name': 'Win %', 'definition': 'Share of deals won.'},
                                                              {'name': 'Pipeline', 'definition': 'Open amount.'}]},
            {'vq_id': 'h-3', 'verdict': 'metrics', 'metrics': [{'name': 'Pipeline'}]}]})

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, gate, name, payload):
        folder = self.engine / 'answers' / gate
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(payload if isinstance(payload, str) else json.dumps(payload))

    def rows(self):
        return {m['name']: m for m in side_page.details(RunFacts(self.run))['metrics']}

    def test_each_proposed_metric_shows_as_a_draft_with_its_meaning_and_queries(self):
        self.save('x1', '02.json', '{"statements": [{"vq_id": "h-4", "verd')  # still being written
        rows = self.rows()
        self.assertEqual(sorted(rows), ['Pipeline', 'Win %', 'Win rate'])
        self.assertEqual({m['status'] for m in rows.values()}, {'draft'})
        self.assertEqual(rows['Win rate']['description'], 'Won deals over closed deals.')
        self.assertFalse(rows['Win rate']['rule'])
        self.assertEqual(len(rows['Pipeline']['sources']), 2)  # one row for a name two queries proposed

    def test_a_saved_merge_replaces_its_drafts_and_names_them(self):
        self.save('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['h-1#0', {'proposal_id': 'h-2#0'}]}]})
        rows = self.rows()
        self.assertEqual(rows['Win rate']['status'], 'merged')
        self.assertEqual(rows['Win rate']['from'], ['Win rate', 'Win %'])
        self.assertNotIn('Win %', rows)
        self.assertEqual(rows['Pipeline']['status'], 'draft')

    def test_drafts_that_already_share_the_name_merge_into_a_count(self):
        self.save('x2', '01.json', {'metrics': [{'name': 'Pipeline', 'sources': ['h-2#1', 'h-3#0']}]})
        self.assertEqual(self.rows()['Pipeline']['from'], ['2 drafts'])

    def test_a_saved_rule_makes_the_metric_final(self):
        self.save('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['h-1#0', 'h-2#0']},
                                                {'name': 'Pipeline', 'sources': ['h-2#1', 'h-3#0']}]})
        self.save('x3', '01.json', {'metrics': [{'name': 'Win rate', 'description': 'Share of closed deals won.',
                                                 'formula': 'AVG(DEALS.WON)'}]})
        rows = self.rows()
        self.assertEqual((rows['Win rate']['status'], rows['Win rate']['rule']), ('final', True))
        self.assertEqual(rows['Win rate']['description'], 'Share of closed deals won.')
        self.assertEqual(rows['Pipeline']['status'], 'merged')

    def test_the_accepted_merge_takes_over_from_its_batches(self):
        self.save('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['h-1#0']}]})
        (self.engine / 'merged_metrics.json').write_text(json.dumps({'metrics': [
            {'name': 'Win rate', 'sources': [{'proposal_id': 'h-1#0'}, {'proposal_id': 'h-2#0'}]},
            {'name': 'Pipeline', 'sources': [{'proposal_id': 'h-2#1'}, {'proposal_id': 'h-3#0'}]}]}))
        rows = self.rows()
        self.assertEqual(sorted(rows), ['Pipeline', 'Win rate'])
        self.assertEqual({m['status'] for m in rows.values()}, {'merged'})

    def test_a_new_batch_changes_what_the_page_follows(self):
        before = side_page._fingerprint(self.run)
        time.sleep(0.01)
        self.save('x2', '01.json', {'metrics': [{'name': 'Win rate', 'sources': ['h-1#0']}]})
        self.assertNotEqual(side_page._fingerprint(self.run), before)

    def test_a_described_batch_changes_what_the_page_follows(self):
        # The describe step's batches never reached the page: its count stood still while the chat posted each one.
        before = side_page._fingerprint(self.run)
        time.sleep(0.01)
        self.save('m2', '001.json', {'queries': [{'vq_id': 'h-1', 'name': 'Win rate by month'}]})
        self.assertNotEqual(side_page._fingerprint(self.run), before)

    def test_the_queries_show_while_the_build_runs(self):
        # A desktop run's Queries list stayed empty for 20 minutes: it read only the checked context.
        (self.engine / 'verified_queries.json').write_text(json.dumps({'objects': [
            {'vq_id': 'h-1', 'question': 'What is the win rate by month?', 'sql': 'SELECT 1'},
            {'vq_id': 'bi-1', 'question': 'What is the pipeline?', 'sql': 'SELECT 2'}]}))
        queries = side_page.details(RunFacts(self.run))['queries']
        self.assertEqual([q['question'] for q in queries], ['What is the win rate by month?', 'What is the pipeline?'])
        self.assertEqual({q['status'] for q in queries}, {'drafted'})

    def test_the_rail_names_the_step_the_top_names(self):
        # The rail read "Checking metric cards" while the top read "Writing each metric's rule" and the chat said it was
        # writing them: the card step writes each metric's definition, and checks nothing.
        import experience
        self.assertEqual([experience.ENGINE_PHASES[g] for g in ('m2', 'x1', 'x2', 'x3')],
                         ['Putting each query in plain words', 'Finding the metrics each query measures',
                          'Merging duplicate metrics', 'Writing what each metric counts and leaves out'])

    def test_each_state_a_metric_passes_has_its_own_plain_name(self):
        # Team decision, 2026-11-18: one badge in plain states, from found to run on the data, so a person can tell
        # at a glance which metrics ran and which need a look. Not tested joined them on 2026-11-19: skipped checks
        # ran nothing, so nothing failed.
        import re
        page = side_page.PAGE.read_text(encoding='utf-8')
        labels = dict(re.findall(r"(\w+): \['([^']+)'", re.search(r'const STATE = \{(.*?)\};', page).group(1)))
        self.assertIn('title="Tested on the data"', page)
        self.assertEqual(list(labels.values()), ['Found', 'Defined', 'Not tested', 'Tested', 'Needs your review', 'Held back'])


if __name__ == '__main__':
    unittest.main()
