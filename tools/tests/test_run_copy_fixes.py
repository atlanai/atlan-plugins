"""What the overnight runs of 0.9.74 showed a person, each fixed at its root: words cut mid-word, bars in query order,
a dashboard named where there was none, a reason that read wrong after its count, a sidecar counted as a source, and a
past-query statement Snowflake refuses."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

from analytics_run import ROOT, SCRIPTS, make_run

sys.path.insert(0, str(SCRIPTS))
import questions  # noqa: E402
import side_page  # noqa: E402
import source_inventory  # noqa: E402
import visuals  # noqa: E402
from run_facts import clip, short_name  # noqa: E402

SKILLS = ROOT / 'plugins/atlanai-analytics/skills'


class CutAtAWord(unittest.TestCase):
    def test_text_is_cut_at_a_word_with_an_ellipsis(self):
        said = 'Counts every delivery across every working day in the quarter, with delivery fees left out'
        for width in (20, 40, 61):
            cut = clip(said, width)
            self.assertLessEqual(len(cut), width)
            self.assertTrue(cut.endswith('…'))
            self.assertIn(cut[:-1].split()[-1], said.split(), 'the last word is whole: %r' % cut)
        self.assertEqual(clip('short', 20), 'short')
        self.assertTrue(visuals._fit(said, 30).endswith('…'))
        self.assertIn(visuals._fit(said, 30)[:-1].split()[-1], said.split())

    def test_a_long_use_case_reads_as_its_own_short_name(self):
        typed = "EU region targets: what each region's quarterly target is. Table: SALES_OPS.REGION_TARGETS_BY_QUARTER"
        self.assertEqual(short_name(typed), 'EU region targets')
        self.assertEqual(short_name('Job margin'), 'Job margin')
        self.assertEqual(short_name('Revenue by region. Which regions grew fastest over the last four quarters and why'),
                         'Revenue by region')


class AnswerBars(unittest.TestCase):
    def bars(self, rows):
        spec = {'question': 'Q', 'headline': 'Total: 1', 'rows': rows}
        return [b['label'] for b in visuals._parts('answer', spec)['bars']]

    def test_bars_read_largest_first(self):
        # 0.9.74: a region of 39 listed below one of 9, in the query's own order.
        self.assertEqual(self.bars([['Retail', 9], ['North', 39], ['South', 20]]), ['North', 'South', 'Retail'])
        self.assertEqual(self.bars([['Barn', -1200], ['Turbine base', -6360], ['Cat flap', 440]]),
                         ['Turbine base', 'Barn', 'Cat flap'], 'a loss is as long as its size')

    def test_bars_with_an_order_of_their_own_keep_it(self):
        for labels in (['2026-01', '2026-02', '2026-03'], ['Q1 2026', 'Q2 2026', 'Q3 2026'], ['Jan', 'Feb', 'Mar'],
                       ['Week 1', 'Week 2', 'Week 3'], ['Monday', 'Tuesday', 'Wednesday']):
            self.assertEqual(self.bars([[x, v] for x, v in zip(labels, (5, 9, 1))]), labels)
        self.assertEqual(self.bars([['Marketing', 1], ['Sales', 5]]), ['Sales', 'Marketing'], 'Mar… is not March')


class AccuracyOption(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def described(self, run):
        option = next(o for o in questions.ready(run, accuracy=True)['options'] if o['label'] == 'Check how accurate it is')
        return option['description']

    def test_with_no_dashboard_the_check_names_the_teams_questions(self):
        run = make_run(self.root / 'h', dashboard=False, local=True)
        self.assertIn('questions your team already asks', self.described(run))
        self.assertIn('questions your dashboard already answers', self.described(make_run(self.root / 'd', local=True)))


class HeldBackReasons(unittest.TestCase):
    def test_a_reason_after_a_count_reads_right(self):
        # 0.9.74, run 2: "22 didn't match its tile's formula".
        self.assertEqual(side_page.kept_short('compiler_review_wrong', 1), "didn't match its tile's formula")
        self.assertEqual(side_page.kept_short('compiler_review_wrong', 22), "didn't match their tiles' formulas")
        self.assertEqual(side_page.kept_short('privacy_review', 3), 'may show personal details')
        self.assertEqual(side_page.kept_short('unknown', 2), 'needed your review first')


class Sources(unittest.TestCase):
    def test_a_history_pulls_coverage_file_is_not_a_second_source(self):
        # 0.9.74 read "Find your sources: 2 found" for one table and one capture of past queries.
        self.assertEqual(source_inventory.source_kind('history/querylog.jsonl'), 'query_history')
        self.assertIsNone(source_inventory.source_kind('history/querylog.jsonl.coverage.json'))

    def test_a_demos_welcome_counts_past_queries_as_its_chips_do(self):
        # 0.9.74: the sources chip said 75 past queries, the welcome 756.
        lines = (SKILLS / 'analytics-demo/assets/box-office/history/query_history.jsonl').read_text(encoding='utf-8').splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
        distinct = len({re.sub(r'\s+', ' ', r['statement_text']).strip().lower() for r in rows})
        welcome = (SKILLS / 'analytics-demo/references/box-office.md').read_text(encoding='utf-8')
        self.assertIn('Past queries   %d past queries' % distinct, welcome)


class PastQueryStatement(unittest.TestCase):
    def test_no_snowflake_information_schema_history_starts_seven_whole_days_back(self):
        # Snowflake refuses `dateadd('day', -7, …)` there as "more than 7 days ago"; 167 hours is accepted.
        refused = re.compile(r"information_schema\.query_history[\s\S]{0,200}?dateadd\('day'", re.I)
        for path in SKILLS.rglob('*'):
            if path.suffix in ('.md', '.py') and path.is_file():
                self.assertIsNone(refused.search(path.read_text(encoding='utf-8', errors='ignore')), str(path))


if __name__ == '__main__':
    unittest.main()
