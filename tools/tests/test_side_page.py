"""The side page on this machine, and the two questions the helper composes from the run's own files."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analytics_run import SCRIPTS, at, facts, make_run, stop_pages

sys.path.insert(0, str(SCRIPTS))
import questions
import side_page

DEMO_WORDS = ('Fixit', 'demo', 'made-up', 'tour', 'job profit agent')


class Page(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        stop_pages(self.root)
        self.tmp.cleanup()

    def get(self, url, host=None):
        parts = urlsplit(url)
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        conn.request('GET', parts.path, headers={'Host': host} if host else {})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response, body

    def test_the_page_answers_only_this_machine_behind_its_key(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            url = side_page.open_page(run)['url']
        parts = urlsplit(url)
        self.assertEqual(parts.hostname, '127.0.0.1')
        self.assertGreaterEqual(len(parts.path.strip('/')), 16, 'an address no one can guess')
        response, page = self.get(url)
        self.assertEqual(response.status, 200)
        self.assertIn("default-src 'none'", response.getheader('Content-Security-Policy'), 'the page loads nothing from anywhere')
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertEqual(self.get('http://127.0.0.1:%d/' % parts.port)[0].status, 404, 'no key, no page')
        self.assertEqual(self.get('http://127.0.0.1:%d/wrong-key-0000000000/state' % parts.port)[0].status, 404)
        self.assertEqual(self.get(url + 'state', host='evil.example:%d' % parts.port)[0].status, 421,
                         'a page on another site that resolves its name here is refused')
        state = json.loads(self.get(url + 'state')[1])
        self.assertEqual((state['flow'], len(state['metrics'])), ('build', 2))

    def test_only_the_page_itself_keeps_the_server_up(self):
        # A request with the wrong key, or none, reset the idle timer: any local poller kept the server up for hours.
        import threading
        server = side_page._Server(make_run(self.root), 0, None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = 'http://127.0.0.1:%d/' % server.port
            server.touched = 0
            for path in ('', 'wrong-key-0000000000/state', server.key + '/ping', server.key + '/nothing'):
                self.get(base + path)
            self.assertEqual(server.touched, 0)
            self.get(base + server.key + '/state')
            self.assertGreater(server.touched, 0, 'the page asking for the run keeps it up')
        finally:
            server.shutdown()
            server.server_close()

    def test_a_proxy_setting_never_hides_a_running_page(self):
        # A proxy in the environment sent the helper's probe of 127.0.0.1 elsewhere: every call then waited and
        # started another server.
        run = make_run(self.root)
        proxy = {'HTTP_PROXY': 'http://127.0.0.1:9', 'http_proxy': 'http://127.0.0.1:9', 'NO_PROXY': '', 'no_proxy': '',
                 'CLAUDE_CODE_ENTRYPOINT': ''}
        with mock.patch.dict(os.environ, proxy):
            first = side_page.open_page(run)
            self.assertTrue(first['started'])
            again = side_page.open_page(run)
        self.assertEqual(again['url'], first['url'])
        self.assertFalse(again['started'], 'the page already serving is found, not started twice')

    def test_a_page_that_cannot_start_is_not_retried_on_every_call(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}), \
                mock.patch.object(side_page, '_spawn') as spawn, mock.patch.object(side_page.time, 'sleep'):
            self.assertIn('error', side_page.open_page(run))
            self.assertIn('error', side_page.open_page(run, explicit=False))
        self.assertEqual(spawn.call_count, 1, 'a failed start waits a while before a helper call tries again')

    def test_stopping_never_signals_a_process_that_is_not_the_page(self):
        run = make_run(self.root)
        (run / 'view').mkdir()
        (run / 'view' / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:9/abcdefghijklmnopqrstuvwx/', 'pid': os.getpid()}),
                                                   encoding='utf-8')
        with mock.patch.object(side_page.os, 'kill') as kill:
            side_page.stop(run)
        kill.assert_not_called()

    def test_the_page_key_is_not_in_the_process_list(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}), \
                mock.patch.object(side_page, '_spawn', wraps=side_page._spawn) as spawn:
            url = side_page.open_page(run)['url']
        key = urlsplit(url).path.strip('/')
        self.assertNotIn(key, ' '.join(map(str, spawn.call_args[0][0])), 'any account on the machine can read argv')
        self.assertEqual(spawn.call_args[0][1], key, 'it travels on standard input instead')

    def test_a_save_under_way_moves_without_claiming_a_count(self):
        run = make_run(self.root, saved=False, stage=('publish', 'running'))
        (run / 'context' / 'plan.json').write_text(json.dumps({'counts': {'metrics': {'create': 2}, 'queries': {'create': 2}}}),
                                                  encoding='utf-8')
        now = side_page.snapshot(run)['now']
        self.assertEqual(now['title'], 'Saving to Atlan Trident')
        self.assertNotIn('count', now, 'nothing is recorded item by item, so no "0 of 4"')
        self.assertTrue(now['busy'])
        self.assertIn('4 items', now['note'])

    def test_two_helper_calls_at_once_start_one_page(self):
        # Two parallel helper calls (each names the page through its reminder) started two servers and lost both.
        import threading
        run = make_run(self.root)
        out = []
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            calls = [threading.Thread(target=lambda: out.append(side_page.open_page(run))) for _ in range(3)]
            for c in calls:
                c.start()
            for c in calls:
                c.join()
        self.assertEqual(len({o.get('url') for o in out}), 1, out)
        self.assertTrue(all(o.get('url') for o in out))
        self.assertEqual(side_page.running(run), out[0]['url'])
        self.assertFalse((run / 'view' / 'start-failed.json').exists())

    def test_the_records_holding_the_key_are_private(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            side_page.open_page(run)
        for name in ('server.json', 'address.json'):
            self.assertEqual((run / 'view' / name).stat().st_mode & 0o077, 0, name)

    def test_a_stopped_page_stays_stopped(self):
        import side_view
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-desktop'}):
            side_page.open_page(run)
            side_view.note(run, 'connect')
            side_page.stop(run)
            self.assertIsNone(side_page.reminder(run), 'the person stopped it: no helper call starts it again')
            self.assertIsNone(side_page.running(run))
            self.assertTrue(side_page.open_page(run).get('url'), 'asking for the page again starts it')

    def test_the_next_line_names_the_describing_after_the_dashboard(self):
        run = make_run(self.root, saved=False, stage=('build', 'running'))
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (run / 'context' / name).unlink()
        manifest = run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        cut = next(i for i, x in enumerate(stages) if x['stage'].startswith('a3'))
        manifest.write_text(json.dumps({'stages': stages[:cut + 1]}), encoding='utf-8')
        now = side_page.snapshot(run)['now']
        self.assertEqual((now['title'], now['next']), ("Translating the dashboard's tiles into queries", 'Describe each query in plain words'))

    def test_a_survey_under_way_shows_the_survey(self):
        run = make_run(self.root, saved=False)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        (run / 'engine' / 'manifest.json').unlink()
        at(run, 'inspect', 'running')
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps({k: v for k, v in interaction.items() if k != 'intent'}), encoding='utf-8')
        state = side_page.snapshot(run)
        running = [x['id'] for x in state['steps'] if x['status'] == 'now']
        self.assertEqual(state['now']['id'], 'sources', 'the step the task list has under way, not the first one left open')

    def test_an_edit_rebuilding_in_its_own_engine_keeps_the_metrics(self):
        run = make_run(self.root)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        edit = run / 'engine-edit1'
        edit.mkdir()
        (edit / 'manifest.json').write_text(json.dumps({'stages': [{'stage': 'h0_corpus', 'counts': {}}]}), encoding='utf-8')
        self.assertEqual(len(side_page.snapshot(run)['metrics']), 2, 'until the rebuild has its own metrics, the last build shows')

    def test_a_hand_written_answer_never_breaks_the_page(self):
        run = make_run(self.root)
        answer = json.loads((run / 'answer.json').read_text(encoding='utf-8'))
        (run / 'answer.json').write_text(json.dumps(dict(answer, proof=['Gross margin'])), encoding='utf-8')
        self.assertEqual(side_page.snapshot(run)['answer']['proof'], ['Gross margin'])

    def test_grouping_under_way_claims_no_count(self):
        run = make_run(self.root, saved=False, stage=('build', 'running'))
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (run / 'context' / name).unlink()
        manifest = run / 'engine' / 'manifest.json'
        stages = json.loads(manifest.read_text(encoding='utf-8'))['stages']
        manifest.write_text(json.dumps({'stages': [x for x in stages if x['stage'] == 'h0_corpus']}), encoding='utf-8')
        self.assertNotIn('count', side_page.snapshot(run)['now'])

    def test_stopping_works_without_a_process_list(self):
        # Hosts without `ps` (Windows) could not tell the page's process, so a stop stopped nothing.
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            url = side_page.open_page(run)['url']
        with mock.patch.object(side_page, '_ours', return_value=False):
            side_page.stop(run)
        import time
        for _ in range(50):
            if not side_page._answers(url):
                break
            time.sleep(0.1)
        self.assertFalse(side_page._answers(url), 'the page stops itself when asked through its own address')

    def test_asking_for_the_page_tries_again_after_a_failed_start(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}), \
                mock.patch.object(side_page, '_spawn') as spawn, mock.patch.object(side_page.time, 'sleep'):
            side_page.open_page(run)
            side_page.open_page(run, explicit=False)
            self.assertEqual(spawn.call_count, 1, 'a helper call between moments waits')
            side_page.open_page(run)
            self.assertEqual(spawn.call_count, 2, 'asking for the page tries again')

    def test_a_terminal_opens_the_browser_once_per_run(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'cli'}), mock.patch('webbrowser.open', return_value=True) as browser:
            side_page.open_page(run)
            side_page.stop(run)
            side_page.open_page(run)
        self.assertEqual(browser.call_count, 1, 'a page that restarts never opens another browser tab')

    def test_a_metric_with_no_sources_never_breaks_the_page(self):
        run = make_run(self.root)
        merged = json.loads((run / 'engine' / 'merged_metrics.json').read_text(encoding='utf-8'))
        merged['metrics'][1]['sources'] = []
        (run / 'engine' / 'merged_metrics.json').write_text(json.dumps(merged), encoding='utf-8')
        self.assertEqual(len(side_page.snapshot(run)['metrics']), 2)
        questions.ready(run)
        questions.save(run)

    def test_the_live_feed_sends_the_run_as_it_is(self):
        run = make_run(self.root)
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}):
            url = side_page.open_page(run)['url']
        with urlopen(url + 'events', timeout=10) as feed:
            first = json.loads(feed.readline().decode('utf-8')[len('data: '):])
        self.assertEqual(first['title'], 'Job margin')
        self.assertTrue((run / 'view' / 'seen.json').is_file(), 'someone has looked at the page')

    def test_own_data_shows_the_metrics_how_each_was_reached_and_its_check(self):
        state = side_page.snapshot(make_run(self.root))
        margin = next(m for m in state['metrics'] if m['name'] == 'Gross margin')
        self.assertEqual((margin['status'], margin['value']), ('checked', '£118,681'))
        self.assertTrue(margin['sources'], 'each metric says where it came from')
        self.assertEqual(state['checks']['passed'], 4)
        for word in DEMO_WORDS:
            self.assertNotIn(word, json.dumps(state), 'own data never reads as a demo')

    def test_a_demo_names_its_firm_only_on_a_demo(self):
        run = make_run(self.root / 'own')
        (run / 'view').mkdir()
        (run / 'view' / 'facts.json').write_text(json.dumps({'firm': 'Fixit', 'title': 'Job profit agent'}), encoding='utf-8')
        self.assertNotIn('Fixit', json.dumps(side_page.snapshot(run)), "a demo's facts on own data are ignored")
        demo = make_run(self.root / 'demo', demo=True)
        (demo / 'view').mkdir()
        (demo / 'view' / 'facts.json').write_text(json.dumps({'firm': 'Fixit', 'title': 'Job profit agent'}), encoding='utf-8')
        self.assertEqual(side_page.snapshot(demo)['title'], 'Job profit agent')

    def test_the_header_counts_the_checks_as_they_run(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: (v if k.startswith('metric:') else {'status': 'schema_checked'}) for k, v in entries.items()}),
                          encoding='utf-8')
        now = side_page.snapshot(run)['now']
        self.assertEqual(now['title'], 'Checking each metric and query on the data')
        self.assertEqual(now['count'], {'n': 2, 'of': 4, 'unit': 'metrics and queries checked'})
        self.assertEqual(now['bar'], 0.5, 'the bar fills with the checks that have their verdict')
        self.assertTrue(now['busy'], 'something is under way: the page moves')

    def test_a_build_under_way_shows_even_when_the_flow_never_named_its_engine(self):
        # A live own-data run left progress at the survey and named no engine run while the engine worked in the run.
        run = make_run(self.root, saved=False)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        at(run, 'inspect')
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (run / 'context' / name).unlink()
        state = side_page.snapshot(run)
        self.assertEqual([s['status'] for s in state['steps']][:4], ['done', 'done', 'done', 'now'],
                         'the engine at work in the run means the choice was made and the metrics are under way')
        self.assertTrue(state['now']['busy'])

    def test_a_paused_run_never_looks_busy(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps(dict(interaction, mode='paused')), encoding='utf-8')
        state = side_page.snapshot(run)
        self.assertEqual(state['state'], 'paused')
        self.assertFalse(state['now']['busy'], 'nothing moves on a run that is not moving')

    def test_a_step_that_waits_for_the_person_says_so(self):
        run = make_run(self.root, saved=False)
        at(run, 'check')
        now = side_page.snapshot(run)['now']
        self.assertEqual((now['title'], now['wait']), ('Where should this context live?', 'Waiting for where to save'))
        self.assertFalse(now['busy'], 'nothing moves while the person decides')


class Questions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_save_question_on_own_data_carries_no_demo_words(self):
        run = make_run(self.root, saved=False)
        asked = questions.save(run, data=[facts()])
        for word in DEMO_WORDS:
            self.assertNotIn(word, asked['question'], 'a demo facts file passed on own data is ignored')
        self.assertIn('You turned your dashboard and past queries into context.', asked['question'])
        self.assertIn('✓ Every metric and query ran · on your data', asked['question'])
        self.assertTrue(asked['question'].endswith('Where should this context live?'), 'the question comes last')
        self.assertEqual(asked['header'], 'Step 5 of 7')
        self.assertEqual([o['label'] for o in asked['options']], ['Save to Atlan Trident (recommended)', 'Keep it on this machine'])

    def test_the_save_question_carries_the_review(self):
        # A live own-data run wrote the review as a reply and ended its turn, leaving the save question unasked.
        text = questions.save(make_run(self.root, saved=False))['question']
        self.assertIn('One metric in full: Gross margin, £118,681 on your data', text)
        self.assertLess(text.index('One metric in full'), text.index('Where should this context live?'), 'the question comes last')

    def test_once_the_flow_moves_on_an_item_never_run_needs_a_look(self):
        # The ready chip read "2/4 kept" while its rows named one item to look at, and the page showed the other as
        # "checking" for good.
        run = make_run(self.root, saved=False, local=True)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        entries['sql_query:q-jobs'] = dict(entries['sql_query:q-jobs'], status='schema_checked')
        entries['metric:Rework Cost'] = dict(entries['metric:Rework Cost'], status='failed', errors=['Wrong total'])
        checks.write_text(json.dumps(entries), encoding='utf-8')
        at(run, 'preview')
        made = side_page.snapshot(run)
        never = next(q for q in made['queries'] if q['name'] == 'Q jobs')
        self.assertEqual((never['status'], never['why']), ('look', 'Its check never ran.'))
        text = ' '.join(questions.ready(run)['question'].split())
        self.assertIn('1 metric and 1 query did not pass their check', text)

    def test_a_warehouse_run_is_asked_where_its_context_lives(self):
        # A warehouse's checks run with its own read-only tool, so no verdict reaches the run's files: the save question
        # refused for good, and counting them would read "0/4 checked, 4 need a look".
        run = make_run(self.root, saved=False, warehouse='snowflake')
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        entries = {k: dict(v, status='schema_checked', errors=[]) for k, v in entries.items()}
        entries['sql_query:q-jobs'] = dict(entries['sql_query:q-jobs'], status='held', errors=['definition.sql is required'])
        checks.write_text(json.dumps(entries), encoding='utf-8')
        at(run, 'preview')
        asked = questions.save(run)
        self.assertIn('2 metrics and 2 queries, their sample checks run on your Snowflake data with its own read-only tool',
                      ' '.join(asked['question'].split()))
        self.assertNotIn('need a look', asked['question'], 'no count this helper cannot see')
        self.assertTrue(asked['results'], 'the agent adds the one line of results it holds')
        self.assertNotIn('context', asked['shown'])
        from run_facts import RunFacts
        self.assertFalse(RunFacts(run).checks_over, 'so no checks chip is drawn')
        made = side_page.snapshot(run)
        self.assertEqual(sorted(q['status'] for q in made['queries']), ['look', 'tool'], 'held, or checked with the warehouse tool')
        self.assertEqual({m['status'] for m in made['metrics']}, {'tool'})
        import side_view
        for stage in ('check', 'preview', 'publish', 'ready'):
            at(run, stage)
            made = side_page.snapshot(run)
            text = json.dumps(made) + json.dumps(side_view.model(run))
            self.assertIsNone(made['checks'], stage)
            for count in ('metrics and queries checked', 'of 4 checked', '4 need a look', '3 need a look'):
                self.assertNotIn(count, text, 'no count this helper cannot see, at %s' % stage)

    def test_a_saved_warehouse_run_claims_no_check_count(self):
        import visuals
        run = make_run(self.root, warehouse='snowflake')
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: dict(v, status='schema_checked', errors=[]) for k, v in entries.items()}), encoding='utf-8')
        card = visuals.render('saved', visuals.build_spec('saved', run))
        self.assertIn('Saved to Atlan Trident', card)
        self.assertNotIn('need a look', card)
        self.assertNotIn('checked on', card)

    def test_on_local_data_moving_on_counts_what_never_ran(self):
        # An item whose check cannot run (a dialect the bound file does not speak) must not hold the save question for
        # good: once the flow moves on, it needs a look, and the count says so.
        run = make_run(self.root, saved=False)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: dict(v, status='schema_checked', errors=[]) for k, v in entries.items()}), encoding='utf-8')
        at(run, 'check')
        with self.assertRaises(ValueError) as refused:
            questions.save(run)
        self.assertIn('4 of 4 still wait for their sample check', str(refused.exception))
        self.assertIn('PROJECT progress --stage preview --status running', str(refused.exception),
                      'the way on when a check cannot run')
        at(run, 'preview', 'running')
        self.assertIn('△ 4 need a look · on your data', questions.save(run)['question'])

    def test_the_ready_question_waits_for_where_the_context_lives(self):
        # A live run kept its context on this machine without recording it: the ready question lost its kept chip.
        run = make_run(self.root, saved=False)
        with self.assertRaises(ValueError) as refused:
            questions.ready(run)
        self.assertIn('PROJECT journey keep-local', str(refused.exception))
        kept = make_run(self.root / 'kept', saved=False, local=True)
        self.assertTrue(questions.ready(kept)['question'].startswith('⌂ Kept on this machine'))

    def test_the_review_never_cuts_a_left_out_item(self):
        # A composed save question read "Leaves out: Facility fees are excluded (they belong to the venue); The te…".
        run = make_run(self.root, saved=False)
        real, long = side_page.details, 'Refunds issued by the venue directly, outside the box office, are never counted here either'

        def leaving(out):
            def details(f):
                made = real(f)
                for m in made['metrics']:
                    m['leaves_out'] = out
                return made
            return details

        with mock.patch.object(side_page, 'details', leaving(['Facility fees are excluded (they belong to the venue).',
                                                              'Test events are left out.'])):
            text = ' '.join(questions.save(run)['question'].split())
        self.assertIn('Leaves out: Facility fees are excluded (they belong to the venue); Test events are left out', text)
        with mock.patch.object(side_page, 'details', leaving(['Facility fees are excluded (they belong to the venue).', long])):
            asked = questions.save(run)['question']
        review = asked[asked.index('One metric in full'):]
        self.assertNotIn('…', review, 'an item is shown whole or not at all')
        self.assertNotIn('Refunds issued', review, 'the side page holds what does not fit')
        self.assertTrue(all(len(x) <= 76 for x in review.splitlines()))

    def test_the_save_question_on_a_demo_names_its_firm(self):
        run = make_run(self.root, saved=False, demo=True)
        asked = questions.save(run, data=[facts()])
        self.assertIn("You turned Fixit's dashboard and past queries into context.", asked['question'])
        self.assertIn("on Fixit's data", asked['question'])
        self.assertEqual(asked['header'], 'Step 4 of 6')

    def test_a_widget_host_keeps_the_count_line(self):
        asked = questions.save(make_run(self.root, saved=False), widget=True)
        self.assertIn('2 metrics and 2 queries, every one ran on your data.', asked['question'])
        self.assertNotIn('Built from your sources', asked['question'], 'the card is drawn as a widget instead')

    def test_the_ready_question_hands_over_what_was_built(self):
        run = make_run(self.root, local=True)
        asked = questions.ready(run, data=[facts()])
        text = asked['question']
        self.assertIn('Your agent is ready.', text, 'own data names no demo agent')
        self.assertIn('Where it lives        on this machine', text)
        self.assertNotIn('See the environment', text, 'own data has no tour')
        self.assertTrue(text.endswith('What would you like to ask?'))
        self.assertEqual([o['label'] for o in asked['options']], ['What is the gross margin in pounds?', 'How much did rework cost?'],
                         "each a question the build wrote for a metric that passed its check, the dashboard's wording first")

    def test_a_demo_ready_question_names_its_agent_and_tour(self):
        demo = dict(facts(), ready={'agent': 'job profit agent', 'answers': 'Questions about job margin', 'tour': 'https://example.com/tour/'})
        text = questions.ready(make_run(self.root, local=True, demo=True), data=[demo])['question']
        self.assertIn('Your job profit agent is ready.', text)
        self.assertIn('See the environment   https://example.com/tour/', text)

    def test_a_demo_that_left_out_its_facts_still_names_its_firm(self):
        # A live desktop run called `visual connect` without `--data`; the demo's working folder names the demo.
        for name, demo in (('demo', True), ('own', False)):
            run = make_run(self.root / name, saved=False, demo=demo)
            meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            meta['source_root'] = str(self.root / name / 'atlan-analytics-demo' / 'yard')
            (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
            text = questions.save(run)['question'] + json.dumps(side_page.snapshot(run))
            if demo:
                self.assertIn("You turned Fixit's dashboard", text)
                self.assertEqual(side_page.snapshot(run)['title'], 'Job profit agent for Fixit & Co.')
            else:
                self.assertNotIn('Fixit', text, 'a folder named like a demo never makes own data a demo')

    def test_the_ready_question_always_has_two_options(self):
        run = make_run(self.root, local=True)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: ({'status': 'failed', 'errors': ['x']} if k.startswith('metric:') else v) for k, v in entries.items()}),
                          encoding='utf-8')
        self.assertGreaterEqual(len(questions.ready(run)['options']), 2, 'the question tool needs two options')

    def test_a_demo_ready_question_counts_the_demo_steps_without_its_facts(self):
        run = make_run(self.root, local=True, demo=True)
        self.assertEqual(questions.ready(run)['header'], 'Step 5 of 6')

    def test_a_partial_save_says_what_is_where(self):
        run = make_run(self.root)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        meta['publication']['artifacts'] = meta['publication']['artifacts'][:1]
        (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        text = ' '.join(questions.ready(run)['question'].split())
        self.assertIn('1 of 4 in Test workspace in Atlan Trident, the rest on this machine', text)

    def test_the_save_question_waits_for_every_check(self):
        run = make_run(self.root, saved=False)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: {'status': 'schema_checked'} for k in entries}), encoding='utf-8')
        with self.assertRaises(ValueError, msg='a live preview printed "0/4 checked, 4 need a look" before anything ran'):
            questions.save(run)

    def test_own_data_ignores_any_facts_passed_to_it(self):
        run = make_run(self.root, saved=False)
        import visuals
        self.assertNotIn('Fixit', visuals.render('context', visuals.build_spec('context', run, [{'firm': 'Fixit'}])))

    def test_the_questions_wait_for_the_checks(self):
        run = make_run(self.root, saved=False)
        (run / 'context' / 'candidate-checks.json').unlink()
        with self.assertRaises(ValueError):
            questions.save(run)


if __name__ == '__main__':
    unittest.main()
