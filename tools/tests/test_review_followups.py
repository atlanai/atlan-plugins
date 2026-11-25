"""Follow-ups from the review of the copy revision and its live runs: numbers a warehouse returns as text, the Power BI
wait's sign-in results, copy said once, time estimates, the links chip and names of reports."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from analytics_run import SCRIPTS, make_run, stop_pages

sys.path.insert(0, str(SCRIPTS))
import run_facts  # noqa: E402
import side_page  # noqa: E402
import visuals  # noqa: E402


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')


class Figures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_whole_digit_text_with_a_unit_reads_as_its_number(self):
        self.assertEqual(run_facts.number('1500', 'USD'), '$1,500')
        self.assertEqual(run_facts.number('1', 'fraction'), '100%')
        self.assertEqual(run_facts.number('1234', 'count'), '1,234')

    def test_whole_digit_text_without_a_unit_or_with_a_leading_zero_stays_as_it_came(self):
        # A year, an id or a zip code.
        self.assertEqual(run_facts.number('2026'), '2026')
        self.assertEqual(run_facts.number('00123', 'USD'), '00123')

    def test_a_warehouse_number_sent_as_text_matches_its_tile(self):
        # A warehouse returned the metric's number as text ("118681") and the tile's query a number (118681.0): the two
        # read "£118681" and "£118,681", so a match was reported as a difference.
        run = make_run(self.root)
        checks = json.loads((run / 'context' / 'candidate-checks.json').read_text())
        checks['metric:Gross Margin']['composition']['rows'] = [['118681']]
        write(run / 'context' / 'candidate-checks.json', checks)
        metrics = json.loads((run / 'context' / 'metrics.json').read_text())
        metrics[0]['definition']['provenance']['derived_from'] = ['bi-q1', 'h-1']
        write(run / 'context' / 'metrics.json', metrics)
        bi = json.loads((run / 'engine' / 'verified_queries.bi.json').read_text())
        bi['objects'][0]['witness'] = {'result': 'pass', 'rows': 1, 'columns': ['gross_margin'], 'sample': [['118681.0']]}
        write(run / 'engine' / 'verified_queries.bi.json', bi)
        self.assertTrue(run_facts.RunFacts(run).context['metrics'][0]['tile']['same'])
        found = {m['name']: m for m in side_page.snapshot(run)['metrics']}
        self.assertTrue(found['Gross margin']['tile']['same'])
        self.assertTrue(run_facts.same_figure('1234', 1234.0))
        self.assertFalse(run_facts.same_figure('1235', 1234.0))


class PowerBIWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / 'task.out'
        import project
        self.wait = lambda **kw: project.wait_task(self.out, timeout=1, interval=0.1, **kw)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_browser_sign_in_address_comes_back_for_when_no_browser_opens(self):
        address = 'https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?client_id=x&state=y'
        self.out.write_text("\nSign in to Power BI in your browser. It opens on its own where it can. If it doesn't, open "
                            "this address:\n%s\n\n" % address)
        got = self.wait()
        self.assertEqual((got['status'], got['address']), ('sign_in', address))
        self.assertIn('only if', got['next'])
        self.assertEqual(self.wait(after=got['after'])['status'], 'waiting')

    def test_a_report_already_read_is_done_not_signed_in(self):
        # The sign-in line and the result were both in when the wait first looked: the report was already read.
        self.out.write_text('Signed in.\n' + json.dumps({'status': 'paused', 'sign_in': 'signed_in'}) + '\n')
        got = self.wait()
        self.assertEqual(got['status'], 'done')
        self.assertEqual(got['result']['sign_in'], 'signed_in')
        self.out.write_text('Signed in.\n')
        self.assertEqual(self.wait()['status'], 'signed_in')


class Wording(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_a_report_is_named_as_a_person_names_it(self):
        self.assertEqual(run_facts.report_name('Box Office Pulse'), 'the Box Office Pulse dashboard')
        self.assertEqual(run_facts.report_name('Sales report'), 'the Sales report')
        import checklist
        self.assertEqual(checklist._picked('Sales: Overview'), 'Overview on the Sales dashboard')
        self.assertEqual(checklist._picked('Sales: all pages'), 'all pages of the Sales dashboard')
        self.assertEqual(visuals._source_line({'tiles': 1, 'questions': 1}, 'Job Margins'), 'From 1 tile on the Job Margins dashboard')
        sys.path.insert(0, str(SCRIPTS.parents[1] / 'accuracy-check' / 'scripts'))
        import check
        gaps = {'demo': False, 'warehouse': None, 'history_used': True, 'history_unreadable': False, 'dashboard': True,
                'report': 'Job Margins', 'pages_left_out': True, 'tables_named': True}
        other = next(o for o in check.next_options(gaps, 8) if o['key'] == 'other_pages')
        self.assertTrue(other['description'].startswith('The pages of the Job Margins dashboard I left out'), other)

    def test_the_links_chip_never_cuts_its_example_mid_formula(self):
        run = make_run(self.root)
        long = 'Net fee revenue per completed primary ticket sold through every channel'
        write(run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': long, 'to': 'Net fee revenue', 'relation': 'derivation', 'role': 'numerator'},
            {'from': long, 'to': 'Completed primary tickets', 'relation': 'derivation', 'role': 'denominator'}]})
        line = visuals.render('links', visuals.build_spec('links', run, []), False).split('\n')[0]
        self.assertNotIn('…', line)
        self.assertNotIn('e.g.', line)
        widget = visuals.render('links', visuals.build_spec('links', run, []), True)
        self.assertNotIn('<span class="t">e.g.', widget)
        write(run / 'engine' / 'metric_relationships.json', {'edges': [
            {'from': 'Margin', 'to': 'Revenue', 'relation': 'derivation', 'role': 'numerator'},
            {'from': 'Margin', 'to': 'Cost', 'relation': 'derivation', 'role': 'denominator'}]})
        self.assertIn('e.g. Margin = Revenue ÷ Cost', visuals.render('links', visuals.build_spec('links', run, []), False))


class HeldBack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_a_failed_metric_the_save_held_back_is_counted_once_as_the_side_page_counts_it(self):
        # The review's run counted one metric both as needing review and as held back; the side page showed it once.
        run = make_run(self.root, failing=3)
        write(run / 'context' / 'publication-holds.json', {'metrics': [
            {'source_id': 's1', 'name': 'Rework Cost', 'status': 'held', 'reason': 'dashboard_parity_failed'}]})
        import questions
        text = questions.ready(run, widget=True)['question']
        self.assertIn('Held back: 1 metric', text)
        self.assertIn("Needs your review: 2 saved queries didn't pass their tests.", text)
        kept = [m for m in side_page.snapshot(run)['metrics'] if m.get('stage') == 'kept']
        self.assertEqual([m['name'] for m in kept], ['Rework cost'])


class OldPython(unittest.TestCase):
    def old(self):
        import shutil
        import subprocess
        old = next((p for p in ('/usr/bin/python3', shutil.which('python3.10'), shutil.which('python3.9')) if p and
                    subprocess.run([p, '-c', 'import sys; sys.exit(sys.version_info >= (3, 11))']).returncode == 0), None)
        if not old:
            self.skipTest('no Python older than 3.11 here')
        return old

    def test_an_old_python_is_told_which_python_to_use_not_shown_a_traceback(self):
        # The dry run, 2026-11-25: a Mac's own python3 is 3.9, and the first helper call ended in a TypeError traceback.
        import subprocess
        old = self.old()
        out = subprocess.run([old, str(SCRIPTS / 'project.py'), 'status'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        said = json.loads(out.stdout)
        self.assertEqual(said['status'], 'blocked')
        self.assertIn('Python 3.11 or later', said['message'])
        self.assertNotIn('Traceback', out.stdout + out.stderr)

    def test_a_check_or_a_set_started_first_says_so_too(self):
        # Dry run B, 2026-11-25: asked how accurate an agent was, the session ran the check on a Mac's python3. It
        # started, lost the chip its helper draws, then failed every known answer with the version message.
        import subprocess
        old, skills = self.old(), SCRIPTS.parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            for script, args in ((skills / 'accuracy-check/scripts/check.py', ['start', '--project', tmp]),
                                 (skills / 'eval-generation/scripts/scope.py', ['inputs', '--project', tmp])):
                out = subprocess.run([old, str(script), *args], capture_output=True, text=True)
                self.assertEqual(out.returncode, 2, script.name)
                self.assertIn('Python 3.11 or later', json.loads(out.stdout)['message'], script.name)


class ChipsCarried(unittest.TestCase):
    """The dry run, 2026-11-25: the session was told on every result to run the chip command, never did, and the person
    saw no chip in 12 minutes. A result now hands over the chip itself."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def test_a_helper_result_hands_over_what_the_run_has_reached_once(self):
        import os
        import subprocess
        import side_view
        run = make_run(self.root, saved=False)
        due = side_view.due(run)
        self.assertTrue(due)
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_ENTRYPOINT'}
        ask = lambda: json.loads(subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(run), 'checklist'],  # noqa: E731
                                                capture_output=True, text=True, env=env).stdout)
        first = ask()
        self.assertIn('show_first', first)
        self.assertIn('side view:', first['show_first'])
        self.assertIn('show_first', first['visual'])
        self.assertNotIn(due[0], side_view.due(run), 'handed over is shown')
        later = [ask().get('show_first', '') for _ in range(4)]
        # Each chip comes once. In a terminal the checks' chip waits for the save question, so only its pointer stays.
        self.assertNotIn(first['show_first'].split('\n')[0], ''.join(later))
        self.assertEqual(side_view.due(run), ['context'])
        self.assertNotIn('**', later[-1])
        self.assertIn('question save', later[-1])

    def test_the_build_wait_hands_over_the_chip_with_the_step_that_ended(self):
        import io
        from unittest import mock
        import drafts
        import side_view
        from context_state import ContextState
        run = ContextState(self.root / 'run')
        run.write(run.path, {'entrypoint': 'project', 'sources': []})
        (run.root / 'engine').mkdir()
        run.write(run.root / 'engine' / 'manifest.json', {'stages': [{'stage': 'm2_ingest', 'verdict': 'ok'}]})
        run.write(run.root / 'interaction.json', {'version': 1, 'mode': 'active', 'tasks': True})
        out = io.StringIO()
        with mock.patch.object(side_view, 'due', return_value=['described']), \
                mock.patch.object(side_view, 'show_due', return_value='CHIP\nside view: {}') as shown:
            drafts.wait(run.root, 'engine', interval=0.05, out=out, timeout=5, quiet=0.4, batch_every=0)
        event = json.loads(out.getvalue())
        self.assertEqual((event['event'], event['show_first']), ('step', 'CHIP\nside view: {}'))
        self.assertLess(event['next'].index('show_first'), event['next'].index('drafts --wait'))
        shown.assert_called_once()


class PathWording(unittest.TestCase):
    """Each template knows how the person got here, and a card takes one line of the session's own words."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root, saved=False, local=True, answered=False)
        meta = json.loads((self.run / 'run.json').read_text())
        write(self.run / 'run.json', {**meta, 'entrypoint': 'project'})  # as a survey records a setup run

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def journey(self, action, answer=None):
        import subprocess
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run), 'journey', action,
                              *(['--answer', answer] if answer else [])], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_build_started_from_a_question_names_it_and_answers_it_first(self):
        q = 'What did each job type make last quarter?'
        self.journey('question', q)
        card = ' '.join(visuals.render('build', visuals.build_spec('build', self.run, []), False).split())
        self.assertIn('Built to answer: ' + q, card)
        self.assertIn('Built to answer: ' + q, visuals.render('build', visuals.build_spec('build', self.run, []), True))
        import questions
        options = questions.ready(self.run, widget=True)['options']
        self.assertEqual((options[0]['label'], options[0]['description']), ('Answer my question', q))
        self.assertLessEqual(len(options), 4)
        self.journey('asked')
        self.assertNotIn('Answer my question', [o['label'] for o in questions.ready(self.run, widget=True)['options']])

    def test_a_build_nobody_asked_a_question_for_says_nothing_of_one(self):
        self.assertNotIn('Built to answer', visuals.render('build', visuals.build_spec('build', self.run, []), False))

    def test_a_card_takes_one_line_of_the_sessions_own_words_never_a_number(self):
        line = 'Built from the dashboard you shared this morning.'
        self.assertIsNone(visuals.check_data('build', {'aside': line}))
        self.assertIn('number', visuals.check_data('build', {'aside': 'Built 3 metrics from it.'}))
        self.assertIn('one line', visuals.check_data('build', {'aside': 'x' * 200}))
        write(self.run / 'build.json', {'aside': line})
        self.assertIn(line, visuals.render('build', visuals.build_spec('build', self.run, []), True))
        self.assertIn(line, ' '.join(visuals.render('build', visuals.build_spec('build', self.run, []), False).split()))


if __name__ == '__main__':
    unittest.main()


class QuietWork(unittest.TestCase):
    """The dry run, 2026-11-25: the session said "Draw the sources chip." before drawing it, narrated its own batches of
    metric descriptions, and said "one needs a judge" while scoring. Each path had no rule where its neighbour had one."""
    SKILLS = SCRIPTS.parent.parent

    def text(self, name):
        return ' '.join((self.SKILLS / name).read_text().split())

    def test_the_sources_chip_step_points_at_the_result_that_carries_it(self):
        flow = self.text('analytics-build/references/setup/dashboard-flow.md')
        self.assertNotIn('Draw the sources chip', flow)
        self.assertIn('sources chip comes with', flow)

    def test_the_session_writing_the_descriptions_itself_writes_no_words_between_them(self):
        progress = self.text('analytics-build/references/setup/progress.md')
        start = progress.index('A host with no background subagent writes the gates in this session')
        self.assertIn('no words of your own', progress[start:start + 300])

    def test_scoring_the_answers_adds_no_words(self):
        run = self.text('accuracy-check/references/run.md')
        self.assertIn('Write nothing between batches or while their answers are scored', run)


class ColumnNamesOnly(unittest.TestCase):
    """The dry run, 2026-11-25: told DESCRIBE was quickest, two of six column-names-only helpers found every schema read
    refused on a local run and answered without seeing a column, so the comparison undercounted that pass."""
    def setUp(self):
        import test_local_project as fixtures
        fixtures.SQLiteFlow.setUp(self)

    def execute(self, sql):
        import subprocess
        request = self.run.root / 'schema.json'
        request.write_text(json.dumps({'sql': sql, 'limit': 5}))
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root), 'execute',
                              str(request)], capture_output=True, text=True)
        return json.loads(out.stdout)

    def test_a_local_run_reads_a_tables_columns_the_way_the_prompt_says(self):
        self.assertEqual(self.execute('DESCRIBE site.work')['status'], 'blocked')
        columns = self.execute('SELECT * FROM site.work LIMIT 0')
        self.assertEqual((columns['status'], columns['rows']), ('ok', []))
        self.assertIn('cost', [c['name'] for c in columns['columns']])
        run = ' '.join((SCRIPTS.parent.parent / 'accuracy-check/references/run.md').read_text().split())
        self.assertIn('`SELECT * FROM <table> LIMIT 0` returns its columns', run)
        self.assertNotIn('schema queries; DESCRIBE TABLE is usually the quickest.', run)


class PageAfterTheBuild(unittest.TestCase):
    """Dry run B, 2026-11-25: a check a day after the build opened the side page with the build's line ("each metric and
    saved query as it's found") and told the session to follow a build that had ended."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def opened(self, run):
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_ENTRYPOINT'}
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(run), 'visual', 'connect',
                              '--widget'], capture_output=True, text=True, env=env).stdout
        return json.loads(out.split('side view: ', 1)[1])['next']

    def test_a_page_opened_during_the_build_follows_the_build(self):
        line = self.opened(make_run(self.root / 'building', saved=False))
        self.assertIn("as it's found", line)
        self.assertIn('drafts --wait', line)

    def test_a_page_opened_after_the_build_names_what_is_there(self):
        import side_view
        run = make_run(self.root / 'built', saved=False)
        side_view.note(run, 'build')
        line = self.opened(run)
        self.assertNotIn('drafts --wait', line)
        self.assertNotIn("as it's found", line)
        self.assertIn("with your agent's metrics and saved queries", line)


class NamesBySource(unittest.TestCase):
    """The person, 2026-11-25: the save card put an orange dot on some metric names and not others, with nothing saying
    what it meant, so the list looked broken and out of line. Each card now names the groups in words."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def words(self, html):
        import re
        return ' '.join(re.sub(r'<style.*?</style>|<[^>]+>', ' ', html, flags=re.S).split())

    def test_the_save_card_says_where_each_metric_came_from(self):
        spec = {'metrics': ['Fee take rate', 'Gross sales', 'Face value sales', 'Sell-through'], 'queries': 2,
                'workspace': 'Sales', 'evidence': [{'name': 'Fee take rate', 'tiles': 1, 'queries': 0},
                                                   {'name': 'Gross sales', 'tiles': 1, 'queries': 3},
                                                   {'name': 'Face value sales', 'tiles': 0, 'queries': 2},
                                                   {'name': 'Sell-through', 'tiles': 1, 'queries': 0}]}
        card = visuals.render('saved', spec, True)
        self.assertNotIn('<i></i>', card)
        self.assertIn('From your dashboard Fee take rate Gross sales Sell-through From past queries Face value sales',
                      self.words(card))

    def test_the_build_card_says_it_the_same_way(self):
        run = make_run(self.root)
        card = visuals.render('build', visuals.build_spec('build', run, []), True)
        self.assertNotIn('<i></i>', card)
        self.assertIn('From your dashboard Gross margin From past queries Rework cost', self.words(card))

    def test_names_with_nothing_known_of_their_source_stay_one_plain_list(self):
        card = visuals.render('saved', {'metrics': ['Fee take rate', 'Gross sales'], 'queries': 0, 'workspace': 'Sales'}, True)
        self.assertIn('Fee take rate Gross sales', self.words(card))
        self.assertNotIn('From ', self.words(card).split('Saved to Atlan')[1].split('Fee take rate')[0])


class CardsCarried(unittest.TestCase):
    """Dry run C, 2026-11-25: asked a question in a built folder, the session answered right and never drew the answer
    card, which only a guide line asked for. The answer, save and edit cards now come with the results that produce them."""
    def setUp(self):
        import test_local_project as fixtures
        fixtures.SQLiteFlow.setUp(self)

    def tearDown(self):
        stop_pages(self.root)

    def call(self, argv, body, desktop=True):
        import os
        import subprocess
        request = self.run.root / 'request.json'
        request.write_text(json.dumps(body))
        env = {**{k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_ENTRYPOINT'},
               **({'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'} if desktop else {})}
        out = subprocess.run([sys.executable, *argv, str(request)], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return json.loads(out.stdout)

    def query(self, body, **kw):
        return self.call([str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root), 'execute'], body, **kw)

    def test_a_query_that_answers_the_person_brings_its_answer_card(self):
        import side_view
        result = self.query({'sql': "SELECT SUM(cost) AS cost FROM site.work WHERE kind = 'rework'",
                             'answer': {'question': 'What did rework cost?', 'metrics': ['Rework cost']}})
        self.assertEqual(result['rows'], [[14.75]])
        self.assertIn('What did rework cost?', result['show_first'])
        self.assertIn('14.8', result['show_first'], 'a value with no unit reads to 3 figures, as on every card')
        self.assertIn('show_first', result['visual'])
        self.assertIn('answer', side_view.seen(self.run.root))

    def test_one_row_of_several_measures_leads_with_the_metric_and_lists_no_other_column(self):
        # Dry run E, 2026-11-25: one row held the rate beside dollars and a ticket count; every column became a row in
        # the rate's unit ("121941250%" for face value), and the session rightly would not show the card.
        import visuals
        result = {'columns': [{'name': 'tickets'}, {'name': 'face_value'}, {'name': 'fee_take_rate'}],
                  'rows': [[14001, 1219412.5, 0.2712]]}
        data = visuals.answer_from({'question': "What's our fee take rate?", 'metrics': ['Fee take rate']}, result)
        self.assertTrue(data['headline'].startswith('Fee take rate: 0.27'), data['headline'])
        self.assertNotIn('rows', data)

    def test_the_answer_card_names_its_metric_once(self):
        # Dry run F, 2026-11-25: "Fee Take Rate" under the number, then "Fee take rate · from 1 tile" right below it.
        import re
        import visuals
        spec = {'question': "What's our fee take rate?", 'headline': 'Fee Take Rate: 27.1%', 'proof': [{'name': 'Fee Take Rate'}],
                'evidence': [{'name': 'Fee take rate', 'tiles': 1, 'queries': 0}]}
        words = ' '.join(re.sub(r'<style.*?</style>|<[^>]+>', ' ', visuals.render('answer', spec, True), flags=re.S).split())
        self.assertEqual(words.lower().count('fee take rate'), 2, words)  # the question, then the sources line
        breakdown = {**spec, 'headline': 'Turbine base: -£6,360', 'proof': [{'name': 'Job margin'}]}
        self.assertIn('Turbine base', visuals.render('answer', breakdown, True), 'a row that answers keeps its label')

    def test_a_breakdown_brings_its_rows(self):
        result = self.query({'sql': 'SELECT kind, SUM(cost) AS cost FROM site.work GROUP BY kind ORDER BY 2 DESC',
                             'answer': {'question': 'What did each kind of work cost?', 'metrics': ['Cost']}})
        self.assertIn('build', result['show_first'])
        self.assertIn('rework', result['show_first'])

    def test_a_warehouse_answer_draws_its_card_from_the_rows_the_warehouse_returned(self):
        # On a warehouse the session runs the SQL in its own tool: one call after it, with what the query call takes.
        import os
        import subprocess
        env = {**{k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_ENTRYPOINT'}, 'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}
        given = {'question': 'What did each kind of work cost?', 'metrics': ['Cost'],
                 'result': {'columns': [{'name': 'kind'}, {'name': 'cost'}], 'rows': [['build', 20], ['rework', 14.75]]}}
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(self.run.root), 'visual', 'answer',
                              '--input', '-'], input=json.dumps(given), capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('What did each kind of work cost?', out.stdout)
        self.assertIn('rework', out.stdout)

    def test_a_query_that_answers_nobody_brings_nothing(self):
        # The accuracy check's answering helpers run the same command: a card handed to one never reaches the person.
        result = self.query({'sql': 'SELECT SUM(cost) AS cost FROM site.work'})
        self.assertNotIn('show_first', result)

    def test_a_rule_change_brings_its_card(self):
        import test_metric_authoring as authoring
        card = authoring.CardExecution.card(self)
        self.run.write(self.run.context / 'metrics.json', [card])
        self.run.write(self.run.context / 'queries.json', [])
        edit = SCRIPTS.parent.parent / 'analytics-demo' / 'scripts' / 'edit_metric.py'
        result = self.call([str(edit), '--run-dir', str(self.run.root)], {
            'name': 'cost', 'before': 'Rework only', 'after': 'Everything but rework',
            'changes': [{'path': 'source.sql', 'value': "(SELECT cost FROM site.work WHERE kind <> 'rework')"},
                        {'path': 'examples', 'value': [{'source': 'declared:example',
                                                        'sql': "SELECT SUM(cost) FROM site.work WHERE kind <> 'rework'"}]}]})
        self.assertEqual(result['version'], 2)
        self.assertIn('Definition changed', result['show_first'])
        self.assertIn('14.8 <s>→</s> 20', result['show_first'], 'the checked value before and after')
        self.assertIn('Everything but rework', result['show_first'])

    def test_a_change_named_from_the_metric_reaches_its_definition(self):
        # Dry run F, 2026-11-25: the session named "definition.formula"; the edit nested a second definition inside the
        # first, its check refused it, and the session spent a minute on "schema errors" out loud.
        import test_metric_authoring as authoring
        sys.path.insert(0, str(SCRIPTS.parent.parent / 'analytics-demo' / 'scripts'))
        import edit_metric
        self.run.write(self.run.context / 'metrics.json', [authoring.CardExecution.card(self)])
        self.run.write(self.run.context / 'queries.json', [])
        edit_metric.edit_metric(self.run, {'name': 'cost', 'changes': [
            {'path': 'definition.source.sql', 'value': "(SELECT cost FROM site.work WHERE kind <> 'rework')"},
            {'path': 'definition.examples', 'value': [{'source': 'declared:example',
                                                       'sql': "SELECT SUM(cost) FROM site.work WHERE kind <> 'rework'"}]}]})
        definition = self.run.read(self.run.context / 'metrics.json')[0]['definition']
        self.assertNotIn('definition', definition)
        self.assertIn("<> 'rework'", definition['source']['sql'])

    def test_a_save_to_atlan_brings_its_card(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest import mock
        import project
        run = make_run(self.root / 'saving', saved=False)
        write(run / 'run.json', {**json.loads((run / 'run.json').read_text()), 'entrypoint': 'project'})
        released = [{'id': 'metric_%d' % i, 'kind': 'metric', 'name': name, 'release_id': 'rel_%d' % i,
                     'version_id': 'ver_%d' % i, 'channel': 'latest', 'definition': {}}
                    for i, name in enumerate(('Gross margin', 'Rework cost'))]
        request = run / 'readbacks.json'
        request.write_text(json.dumps({'workspace_id': 'w1', 'workspace': 'Site finance', 'artifacts': []}))
        out = io.StringIO()
        with mock.patch.object(project.Project, 'releases', return_value=released), \
                mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}), \
                mock.patch.object(sys, 'argv', ['project.py', '--run-dir', str(run), 'publication', str(request)]), \
                redirect_stdout(out):
            self.assertEqual(project.main(), 0)
        result = json.loads(out.getvalue())
        self.assertIn('Saved to Atlan', result['show_first'])
        self.assertIn('Site finance', result['show_first'])


class CardGuides(unittest.TestCase):
    """The guides name the call that brings each card, never a separate step to draw it (dry run C, 2026-11-25)."""
    SKILLS = SCRIPTS.parent.parent

    def text(self, name):
        return ' '.join((self.SKILLS / name).read_text().split())

    def test_the_ask_skill_puts_the_answer_on_the_query_that_answers(self):
        ask = self.text('analytics-ask/SKILL.md')
        self.assertIn('"answer": {"question":', ask)
        self.assertIn('PROJECT visual answer --input -', ask, 'a warehouse answer: one call after the result')
        self.assertNotIn('record its `answer` moment', ask)

    def test_a_run_on_this_machine_is_queried_only_through_the_helper(self):
        # Dry run D, 2026-11-25: the session attached the SQLite file itself; the guide named only DuckDB runs.
        ask = self.text('analytics-ask/SKILL.md')
        self.assertIn('on this machine (SQLite or DuckDB)', ask)
        self.assertIn('never by opening its database file', ask)

    def test_the_save_and_the_edit_name_the_result_that_carries_their_card(self):
        self.assertNotIn('PROJECT visual saved --widget', self.text('analytics-build/references/setup/ready.md'))
        self.assertIn('with `workspace`, the workspace\'s display name', self.text('analytics-build/references/project.md'))
        edit = self.text('analytics-demo/references/edit-metric.md')
        self.assertIn('run `EDIT <that file>`', edit)
        self.assertIn('"before": "<old rule, plain words>"', edit)


class BuildChips(unittest.TestCase):
    """Validation run G, 2026-11-25: the session drove the engine itself, filtered each result to a few keys and never
    ran the build wait, so no chip showed for 14 minutes; the step chips then came in a lump after the build card, and
    none was drawn. The engine now hands them over when no wait is running, and late step chips stay back."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.run = make_run(self.root)
        sys.path.insert(0, str(SCRIPTS.parent.parent / 'metrics-generation'))

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def advance(self):
        import io
        import os
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock
        from modules.metrics import driver
        out, err = io.StringIO(), io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_CODE_ENTRYPOINT'}
        with mock.patch.object(driver, 'invoke', return_value=({'status': 'gate', 'gate': 'x1'}, 3)), \
                mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            driver.main(['--runtime', str(self.root / 'rt'), 'advance', '--run', str(self.run / 'engine'), '--model', 'm'])
        return json.loads(out.getvalue()), err.getvalue()

    def test_a_session_driving_the_build_itself_gets_each_chip_from_the_engine(self):
        import side_view
        due = side_view.due(self.run)
        self.assertTrue(due)
        result, err = self.advance()
        self.assertTrue(result['show_first'])
        self.assertIn('show_first', result['visual'])
        self.assertIn('`PROJECT drafts --wait`', result['visual'], 'and the wait from here on')
        self.assertIn(result['show_first'].splitlines()[0], err, 'a filter on the result leaves stderr')
        self.assertIn(due[0], side_view.seen(self.run))

    def test_while_the_wait_runs_the_engine_leaves_the_chips_to_it(self):
        import side_view
        side_view.mark_waiting(self.run)
        result, err = self.advance()
        self.assertNotIn('show_first', result)
        self.assertIn('`PROJECT drafts --wait`', result['visual'])
        self.assertEqual(err, '')

    def test_a_wait_that_stopped_long_ago_counts_as_none(self):
        import side_view
        side_view.mark_waiting(self.run, at=0)
        self.assertFalse(side_view.waiting(self.run))
        self.assertIn('show_first', self.advance()[0])

    def test_the_build_wait_marks_itself_running(self):
        import io
        from unittest import mock
        import drafts
        import side_view
        with mock.patch.object(side_view, 'due', return_value=['described']), \
                mock.patch.object(side_view, 'show_due', return_value='CHIP'):
            drafts.wait(self.run, 'engine', interval=0.05, out=io.StringIO(), timeout=5, quiet=0.4, batch_every=0)
        self.assertTrue(side_view.waiting(self.run))

    def test_once_the_build_card_is_shown_its_step_chips_stay_back(self):
        import side_view
        side_view.note(self.run, 'build')
        late = set(side_view.due(self.run)) & {'found', 'sources', 'group', 'tiles', 'described', 'drafted', 'metrics',
                                               'rules', 'links'}
        self.assertEqual(late, set())


class WholeResults(unittest.TestCase):
    """Validation run H, 2026-11-25: the session ran the checks through a script of its own that printed only the task
    list, and read the build's result through another that kept only the card: the checks chip and the links chip were
    handed over and never seen."""
    def test_the_build_guides_say_to_read_each_helper_result_whole(self):
        skills = SCRIPTS.parent.parent
        for name in ('analytics-build/SKILL.md', 'analytics-demo/SKILL.md'):
            text = ' '.join((skills / name).read_text().split())
            self.assertIn('Run each helper command on its own and read all it returns: a script or filter of yours drops its chip.',
                          text, name)
