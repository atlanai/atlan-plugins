"""What the second live dashboard run (2026-11-23, CL Leaderboard) showed, held here."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from analytics_run import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import checklist  # noqa: E402
import project  # noqa: E402
import side_page  # noqa: E402
import visuals  # noqa: E402
from run_facts import RunFacts  # noqa: E402


class SecondRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name).resolve() / '.atlan-metrics' / 'run'
        (self.run / 'engine' / 'answers' / 'c3').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_picked_pages_read_as_words_not_two_colons(self):
        self.assertEqual(checklist._picked('CL Leaderboard: Coverage'), 'Coverage on CL Leaderboard')
        self.assertEqual(checklist._picked('Sales: all pages'), 'all pages of Sales')
        self.assertEqual(checklist._picked('Sales'), 'Sales')

    def test_the_side_page_opens_with_the_first_drafted_queries(self):
        facts = RunFacts(self.run)
        self.assertFalse(side_page.has_output(facts), 'nothing drafted, nothing to open')
        (self.run / 'engine' / 'answers' / 'c3' / 'batch_01.json').write_text(json.dumps(
            {'items': [{'candidate_id': 'bi-1', 'question': 'How many accounts are covered?'}]}))
        facts = RunFacts(self.run)
        self.assertTrue(side_page.has_output(facts))
        self.assertEqual(side_page._drafted_queries(facts)['bi-1']['question'], 'How many accounts are covered?')

    def test_skipping_the_tests_still_lets_the_build_card_draw(self):
        (self.run / 'interaction.json').write_text(json.dumps({'flow': 'dashboard', 'checks': 'skip'}))
        self.assertTrue(visuals._checked(RunFacts(self.run)))

    def test_the_sign_in_wait_returns_the_result_a_code_or_waiting(self):
        out = Path(self.tmp.name) / 'task.out'
        out.write_text('Opening Power BI sign-in\n')
        self.assertEqual(project.wait_task(out, timeout=1, interval=0.1)['status'], 'waiting')
        out.write_text('To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code X1\n')
        code = project.wait_task(out, timeout=1, interval=0.1)
        self.assertEqual(code['status'], 'sign_in')
        self.assertEqual(project.wait_task(out, after=code['after'], timeout=1, interval=0.1)['status'], 'waiting')
        out.write_text(out.read_text() + json.dumps({'status': 'paused', 'sign_in': 'signed_in'}) + '\n')
        self.assertEqual(project.wait_task(out, after=code['after'], timeout=1, interval=0.1)['result']['sign_in'], 'signed_in')

    def test_the_wait_sees_the_result_past_the_host_s_exit_line_and_stops_on_a_bare_exit(self):
        # Claude Code adds "[exited with code N]" after a background command's last line: the wait looped on it.
        out = Path(self.tmp.name) / 'task.out'
        out.write_text(json.dumps({'status': 'paused', 'sign_in': 'signed_in'}) + '\n\n[exited with code 0]\n')
        self.assertEqual(project.wait_task(out, timeout=1, interval=0.1)['status'], 'done')
        out.write_text('Traceback (most recent call last):\nValueError: no\n[exited with code 1]\n')
        ended = project.wait_task(out, timeout=1, interval=0.1)
        self.assertEqual((ended['status'], ended['code']), ('exited', 1))
        out.write_text('To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter')
        self.assertEqual(project.wait_task(out, timeout=1, interval=0.1)['status'], 'waiting', 'a line still being written')


if __name__ == '__main__':
    unittest.main()
