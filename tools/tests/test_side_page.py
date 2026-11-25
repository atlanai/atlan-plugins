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

    def test_in_the_desktop_app_the_agent_confirms_the_pane_is_open(self):
        # 2026-11-19: a page loaded in a closed pane still reports itself visible, so only the app's layout can say
        # whether the person sees it.
        said = side_page.instruction({'url': 'http://127.0.0.1:1/k/', 'host': 'claude-desktop'})
        self.assertIn('layout', said)
        self.assertIn('`preview`', said)

    def test_in_the_desktop_app_the_task_list_carries_the_build_once_the_page_is_open(self):
        # The team, 2026-11-19: the page carries each metric step from here, so the session waits in the foreground and
        # updates the task list, never ending a turn on an update alone.
        said = side_page.instruction({'url': 'http://127.0.0.1:1/k/', 'host': 'claude-desktop'})
        self.assertIn('`PROJECT drafts --wait`', said)
        self.assertIn('if they are deferred', said, 'd7 never loaded its browser tools and printed the address instead')
        # A watched run whose window was not on screen gave the page's address in the chat instead.
        self.assertIn('window is not on screen, say nothing', said)
        self.assertNotIn('drafts --wait', side_page.instruction({'url': 'http://127.0.0.1:1/k/', 'opened': True}), 'a terminal is unchanged')

    def test_a_metric_says_where_its_data_lives_and_where_it_was_kept(self):
        # A metric's profile read "On this machine" for both rows on a warehouse run that was saved to Atlan Trident.
        page = side_page.snapshot(make_run(self.root, warehouse='snowflake'))
        self.assertEqual(page['warehouse'], 'Snowflake')
        self.assertEqual(page['saved'], {'workspace': 'Test workspace'})
        local = side_page.snapshot(make_run(Path(tempfile.mkdtemp(dir=self.root)), local=True))
        self.assertIsNone(local['warehouse'])
        self.assertIsNone(local['saved'])

    def test_a_page_that_cannot_start_is_named_once_so_nobody_waits_for_it(self):
        # A tester, 2026-11-19: the page never opened, and nothing in the chat said so.
        said = side_page.instruction({'error': 'no free port'})
        self.assertIn("could not start on this machine", said)
        self.assertIn('once', said)
        self.assertNotIn('say nothing', said.lower())

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

    def test_a_page_whose_run_cannot_be_read_still_hears_from_its_server(self):
        # A snapshot that kept failing skipped the keepalive too: a closed tab was never noticed, and the open one
        # stayed on Connecting.
        import threading
        server = side_page._Server(make_run(self.root), 0, None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with mock.patch.object(side_page, 'snapshot', side_effect=ValueError('half written')), \
                    mock.patch.object(side_page, 'KEEPALIVE', 0.5):
                conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=10)
                conn.request('GET', '/%s/events' % server.key)
                line = conn.getresponse().readline()
                conn.close()
            self.assertEqual(line, b': still here\n')
        finally:
            server.shutdown()
            server.server_close()

    def test_a_file_that_vanishes_mid_scan_never_ends_the_feed(self):
        run = make_run(self.root)
        gone = mock.Mock(is_file=lambda: True, stat=mock.Mock(side_effect=FileNotFoundError))
        gone.name = 'server.json'
        with mock.patch.object(side_page.os, 'scandir', return_value=[gone]):
            self.assertTrue(side_page._fingerprint(run))

    def test_an_engine_folder_that_vanishes_mid_scan_never_ends_the_feed(self):
        from run_facts import engine_folder
        run = make_run(self.root)
        (run / 'engine-edit1').mkdir()
        (run / 'engine-edit1' / 'manifest.json').write_text('{}', encoding='utf-8')
        real = Path.glob
        ghost = run / 'engine-gone' / 'manifest.json'
        with mock.patch.object(Path, 'glob', lambda self, pattern: [*real(self, pattern), ghost]):
            self.assertIn(engine_folder(run), ('engine', 'engine-edit1'))
        with mock.patch.object(side_page.os, 'scandir', side_effect=FileNotFoundError), \
                mock.patch.object(side_page.RunFacts, 'engine', run / 'engine-edit1'):
            self.assertTrue(side_page._fingerprint(run))

    def test_a_page_slow_to_start_is_started_once(self):
        # A page that took longer than its wait to answer was started again by the next call, on every platform.
        import subprocess
        run = make_run(self.root)
        slow = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}), \
                    mock.patch.object(side_page, '_spawn', return_value=slow.pid) as spawn, mock.patch.object(side_page.time, 'sleep'):
                self.assertIn('error', side_page.open_page(run))
                side_page.open_page(run)
            self.assertEqual(spawn.call_count, 1, 'the page still starting is waited for')
        finally:
            slow.kill()
            slow.wait()

    def test_without_a_process_list_a_page_still_starting_is_waited_for(self):
        # Without `ps` (Windows) a page slower than its wait to answer was started a second time.
        import subprocess
        run = make_run(self.root)
        starting = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            (run / 'view').mkdir(exist_ok=True)
            (run / 'view' / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:9/k0000000000000000000/',
                                                                  'pid': starting.pid, 'started': side_page._now()}), encoding='utf-8')
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}), \
                    mock.patch.object(side_page.subprocess, 'run', side_effect=FileNotFoundError), \
                    mock.patch.object(side_page, '_spawn', return_value=0) as spawn, mock.patch.object(side_page.time, 'sleep'):
                side_page.open_page(run)
            self.assertEqual(spawn.call_count, 0)
        finally:
            starting.kill()
            starting.wait()

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
                mock.patch.object(side_page, '_spawn', return_value=0) as spawn, mock.patch.object(side_page.time, 'sleep'):
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

    def test_the_page_starts_on_a_python_whose_posix_spawn_has_no_setsid(self):
        # The Pythons uv, mise and rye install refuse setsid, and `visual next` exited 1 on them.
        run = make_run(self.root)
        refuse = mock.Mock(side_effect=NotImplementedError('posix_spawn: setsid unavailable on this platform'))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': ''}), mock.patch.object(side_page.os, 'posix_spawn', refuse):
            url = side_page.open_page(run)['url']
        refuse.assert_called_once()
        with urlopen(url, timeout=10) as page:
            self.assertEqual(page.status, 200)

    def test_a_save_under_way_is_the_chats_and_the_page_shows_what_was_built(self):
        # The page follows metric generation only, in every flow.
        run = make_run(self.root, saved=False, stage=('publish', 'running'))
        (run / 'context' / 'plan.json').write_text(json.dumps({'counts': {'metrics': {'create': 2}, 'queries': {'create': 2}}}),
                                                  encoding='utf-8')
        state = side_page.snapshot(run)
        self.assertEqual(len(state['metrics']), 2)
        self.assertNotIn('Saving', json.dumps(state))

    def test_a_metric_from_a_dashboard_tile_carries_the_tile_s_own_figure(self):
        # Proof a first-time reader can check: the number the tile's own query returned, beside the metric's.
        run = make_run(self.root)
        metrics = json.loads((run / 'context' / 'metrics.json').read_text())
        metrics[0]['definition']['provenance']['derived_from'] = ['bi-q1', 'h-1']
        (run / 'context' / 'metrics.json').write_text(json.dumps(metrics), encoding='utf-8')
        bi = json.loads((run / 'engine' / 'verified_queries.bi.json').read_text())
        bi['objects'][0]['witness'] = {'result': 'pass', 'rows': 1, 'columns': ['gross_margin'], 'sample': [['118681.0']]}
        (run / 'engine' / 'verified_queries.bi.json').write_text(json.dumps(bi), encoding='utf-8')
        found = {m['name']: m for m in side_page.snapshot(run)['metrics']}
        self.assertEqual(found['Gross margin']['tile'], {'name': 'Gross margin', 'value': '£118,681', 'same': True})
        self.assertNotIn('tile', found['Rework cost'], 'made from past queries alone')

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

    def test_the_checklist_says_what_is_done_and_what_is_under_way(self):
        # A live run read as restarted: "Putting each query in plain words, 0 queries" right after 37 tiles translated.
        run = make_run(self.root, saved=False, stage=('build', 'running'))
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (run / 'context' / name).unlink()
        (run / 'engine' / 'manifest.json').write_text(json.dumps({'stages': [
            {'stage': 'a3_translate', 'counts': {'units': 50, 'candidates': 37, 'refused': 13}},
            {'stage': 'a3b_ingest', 'counts': {}}, {'stage': 'c1_normalise', 'counts': {}}]}), encoding='utf-8')
        # What is done shows in the page's checklist, each finished step with its result.
        steps = {s['id']: s for s in side_page.snapshot(run)['build']}
        self.assertEqual((steps['translate']['state'], steps['translate']['result']), ('done', '37 of 50 tiles'))
        self.assertEqual(steps['describe']['state'], 'now')

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
        self.assertNotIn('answer', side_page.snapshot(run), 'the answer is the chat\'s; the page still renders')

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
                mock.patch.object(side_page, '_spawn', return_value=0) as spawn, mock.patch.object(side_page.time, 'sleep'):
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

    def test_while_the_checks_run_the_page_shows_what_was_built(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: (v if k.startswith('metric:') else {'status': 'schema_checked'}) for k, v in entries.items()}),
                          encoding='utf-8')
        state = side_page.snapshot(run)
        # The page follows generation only: once the checks start, generation is over.
        self.assertEqual(len(state['metrics']), 2)
        self.assertEqual({s['state'] for s in state['build']}, {'done'})

    def test_a_build_under_way_shows_even_when_the_flow_never_named_its_engine(self):
        # A live own-data run left progress at the survey and named no engine run while the engine worked in the run.
        run = make_run(self.root, saved=False)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        (run / 'run.json').write_text(json.dumps({k: v for k, v in meta.items() if k != 'engine_run'}), encoding='utf-8')
        at(run, 'inspect')
        for name in ('candidate-checks.json', 'metrics.json', 'queries.json'):
            (run / 'context' / name).unlink()
        state = side_page.snapshot(run)
        self.assertIn('now', [s['state'] for s in state['build']], 'the engine at work in the run means the metrics are under way')

    def test_a_paused_run_says_so(self):
        run = make_run(self.root, saved=False, stage=('check', 'running'))
        interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
        (run / 'interaction.json').write_text(json.dumps(dict(interaction, mode='paused')), encoding='utf-8')
        self.assertEqual(side_page.snapshot(run)['state'], 'paused')


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
        self.assertIn('Validated on your data: all passed.', asked['question'])
        self.assertTrue(asked['question'].endswith('Where do you want to store your context?'), 'the question comes last')
        # The task list numbers the steps (ten on own data, eight in a demo): a second numbering in the header disagreed.
        self.assertEqual(asked['header'], 'Save')
        self.assertEqual([o['label'] for o in asked['options']], ['Save to Atlan Trident (Recommended)', 'Keep it on this machine'])

    def test_the_save_question_carries_the_review(self):
        # A live own-data run wrote the review as a reply and ended its turn, leaving the save question unasked.
        text = questions.save(make_run(self.root, saved=False))['question']
        self.assertIn('One metric in full: Gross margin, £118,681 on your data', text)
        self.assertLess(text.index('One metric in full'), text.index('Where do you want to store your context?'),
                        'the question comes last')

    def test_the_save_question_carries_the_page_address_only_in_a_terminal(self):
        # In the desktop app a link in the question opens an outside browser, never the page beside the chat.
        run = make_run(self.root, saved=False)
        (run / 'view').mkdir(exist_ok=True)
        (run / 'view' / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:5/key/'}), encoding='utf-8')
        self.assertIn('http://127.0.0.1:5/key/#m=', questions.save(run)['question'])
        desktop = questions.save(run, widget=True)['question']
        self.assertNotIn('http', desktop)
        self.assertIn('From 1 tile and 1 past query.', desktop)

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
        self.assertEqual((never['status'], never['why']), ('look', "It wasn't tested."))
        text = ' '.join(questions.ready(run)['question'].split())
        self.assertIn("1 metric and 1 saved query didn't pass their tests", text)

    def test_each_saved_query_carries_its_profile_like_a_metric(self):
        # A query row on the page opened nothing, while each metric opened where it came from, its rule and its check.
        run = make_run(self.root, saved=False, local=True)
        (run / 'context' / 'queries.json').write_text(json.dumps([
            {'name': 'q-margin', 'display_name': 'What gross margin have we made?',
             'description': 'Contract value less labour and materials, for jobs not cancelled.',
             'definition': {'question': 'What gross margin have we made?', 'sql': 'SELECT SUM(margin) FROM site.jobs',
                            'extensions': {'metrics_agent': {'vq_id': 'bi-q1', 'evidence': {'tables': ['SITE.SITE.JOBS', 'site.jobs', 'site.crew']}}}}},
            {'name': 'q-jobs', 'definition': {'question': 'How much went on rework?',
                                              'extensions': {'metrics_agent': {'vq_id': 'h-2'}}}}]), encoding='utf-8')
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        entries['sql_query:q-margin'] = {'status': 'executed', 'errors': [], 'composition': {'status': 'executed', 'rows': [[118681.0]]}}
        entries['sql_query:q-jobs'] = {'status': 'executed', 'errors': [], 'composition': {'status': 'executed', 'rows': [[1], [2], [3]]}}
        checks.write_text(json.dumps(entries), encoding='utf-8')
        at(run, 'preview')
        made = side_page.snapshot(run)
        margin = next(q for q in made['queries'] if q.get('id') == 'bi-q1')
        self.assertEqual(margin['description'], 'Contract value less labour and materials, for jobs not cancelled.')
        self.assertEqual((margin['kind'], margin['visual'], margin['page']), ('tile', 'Gross margin', 'Site'))
        self.assertEqual(margin['sql'], 'SELECT SUM(margin) FROM site.jobs')
        # A local database's tables come back as SITE.SITE.JOBS beside site.jobs: one table, named once.
        self.assertEqual(margin['tables'], ['SITE.JOBS', 'site.crew'])
        self.assertEqual(margin['metrics'], ['Gross margin'], 'the metrics built on it, by the names the page shows')
        self.assertEqual((margin['status'], margin['value']), ('checked', '118,681'))
        jobs = next(q for q in made['queries'] if q.get('id') == 'h-2')
        self.assertEqual((jobs['kind'], jobs['runs'], jobs['analysts']), ('query', 5, 1))
        self.assertEqual((jobs['metrics'], jobs['value']), (['Rework cost'], '3 rows'))
        margin_metric = next(m for m in made['metrics'] if m['name'] == 'Gross margin')
        self.assertIn('bi-q1', [s.get('id') for s in margin_metric['sources']], 'a metric opens the query it came from')

    def test_a_query_marked_for_a_read_carries_its_notes(self):
        # A fresh reader of h9 met "5 queries marked for a review of their sources" in the chat and "Needs a look 0" on
        # the page. The engine marks a query `needs_review` with notes on what it does (its gotchas): the page shows both.
        run = make_run(self.root, saved=False, local=True)
        bi = json.loads((run / 'engine' / 'verified_queries.bi.json').read_text())
        bi['objects'][0].update(label='needs_review', gotchas=['Returns blank rather than 0 when no job is late.'])
        (run / 'engine' / 'verified_queries.bi.json').write_text(json.dumps(bi), encoding='utf-8')
        (run / 'context' / 'queries.json').write_text(json.dumps([
            {'name': 'q-margin', 'definition': {'question': 'What gross margin have we made?', 'review_status': 'unreviewed',
                                                'extensions': {'metrics_agent': {'vq_id': 'bi-q1'}}}},
            {'name': 'q-jobs', 'definition': {'question': 'How much went on rework?', 'review_status': 'verified',
                                              'extensions': {'metrics_agent': {'vq_id': 'h-2'}}}}]), encoding='utf-8')
        at(run, 'preview')
        made = {q['id']: q for q in side_page.snapshot(run)['queries']}
        self.assertEqual((made['bi-q1']['review'], made['bi-q1']['notes']), ('read', ['Returns blank rather than 0 when no job is late.']))
        self.assertNotIn('review', made['h-2'])

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
        self.assertIn('answers from 2 metrics and 2 saved queries, built from your Power BI dashboard and 3 past queries. '
                      'Validated on your Snowflake data, read-only.', ' '.join(asked['question'].split()))
        self.assertNotIn('need your review', asked['question'], 'no count this helper cannot see')
        self.assertTrue(asked['results'], 'the agent adds the one line of results it holds')
        self.assertEqual(asked['shown'], ['build', 'context'], 'the text says, in sentences, what the cards would')
        from run_facts import RunFacts
        self.assertFalse(RunFacts(run).checks_over, 'so no checks chip is drawn')
        made = side_page.snapshot(run)
        self.assertEqual(sorted(q['status'] for q in made['queries']), ['look', 'tool'], 'held, or checked with the warehouse tool')
        self.assertEqual({m['status'] for m in made['metrics']}, {'tool'})
        for stage in ('check', 'preview', 'publish', 'ready'):
            at(run, stage)
            text = json.dumps(side_page.snapshot(run))
            for count in ('saved query passed', 'of 4 passed', '4 need your review', '3 need your review'):
                self.assertNotIn(count, text, 'no count this helper cannot see, at %s' % stage)

    def test_a_saved_warehouse_run_claims_no_check_count(self):
        import visuals
        run = make_run(self.root, warehouse='snowflake')
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: dict(v, status='schema_checked', errors=[]) for k, v in entries.items()}), encoding='utf-8')
        card = visuals.render('saved', visuals.build_spec('saved', run))
        self.assertIn('Saved to Atlan Trident', card)
        self.assertNotIn('need your review', card)
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
        text = questions.save(run)['question']
        self.assertIn('Validated on your data', text)
        self.assertIn('4 need your review', text)

    def test_the_ready_question_waits_for_where_the_context_lives(self):
        # A live run kept its context on this machine without recording it: the ready question could not say where it lives.
        run = make_run(self.root, saved=False)
        with self.assertRaises(ValueError) as refused:
            questions.ready(run)
        self.assertIn('PROJECT journey keep-local', str(refused.exception))
        kept = make_run(self.root / 'kept', saved=False, local=True)
        self.assertIn('this machine, in this folder', questions.ready(kept)['question'])

    def test_a_demo_ready_question_lists_no_held_items_without_its_facts(self):
        run = make_run(self.root / 'demo-held', saved=False, local=True, demo=True, failing=1)
        with mock.patch.object(questions, '_demo', return_value={}):
            self.assertNotIn('Needs your review', questions.ready(run)['question'])

    def test_a_ready_option_is_one_question(self):
        run = make_run(self.root / 'one-q', saved=False, local=True)
        said = run / 'engine' / 'sql_descriptions.json'
        said.write_text(json.dumps({'descriptions': {'h-1': {'question': 'How much did the jobs make? What share was billed?'}}}),
                        encoding='utf-8')
        labels = [o['label'] for o in questions.ready(run)['options']]
        self.assertFalse(any(label.count('?') > 1 for label in labels), labels)

    def test_a_saved_query_named_by_several_questions_reads_as_its_first(self):
        run = make_run(self.root / 'named', saved=False, local=True)
        queries = run / 'context' / 'queries.json'
        listed = json.loads(queries.read_text(encoding='utf-8'))
        for q in listed:
            q['display_name'] = 'How many jobs ran late? What share of them were billed?'
        queries.write_text(json.dumps(listed), encoding='utf-8')
        made = side_page.details(__import__('run_facts').RunFacts(run))
        self.assertTrue(all(q['name'].count('?') <= 1 for q in made['queries']), [q['name'] for q in made['queries']])

    def test_descriptions_in_a_list_never_break_the_page(self):
        run = make_run(self.root / 'listed')
        (run / 'engine' / 'sql_descriptions.json').write_text(json.dumps({'descriptions': [{'vq_id': 'h-1', 'question': 'Q?', 'x': 1}]}),
                                                              encoding='utf-8')
        self.assertIn('metrics', side_page.snapshot(run))

    def test_a_metric_shows_its_whole_meaning(self):
        # A fix appended "A breakdown lists only those with rework hours" to a 400-character meaning, and the page cut it off.
        run = make_run(self.root / 'long')
        metrics = run / 'context' / 'metrics.json'
        listed = json.loads(metrics.read_text(encoding='utf-8'))
        last = 'A breakdown lists only the jobs with a margin, never one at zero.'
        listed[0]['description'] = ' '.join(['What the firm keeps from each job once labour and materials are paid for.'] * 6 + [last])
        metrics.write_text(json.dumps(listed), encoding='utf-8')
        shown = next(m for m in side_page.snapshot(run)['metrics'] if m['name'] == side_page.sentence(listed[0]['name']))
        self.assertTrue(shown['description'].endswith(last), shown['description'][-80:])

    def test_a_metric_named_nothing_has_no_unit(self):
        from run_facts import RunFacts
        run = make_run(self.root / 'unnamed')
        metrics = run / 'context' / 'metrics.json'
        listed = json.loads(metrics.read_text(encoding='utf-8'))
        listed.append({'name': '', 'definition': {'output': {'unit': 'GBP'}}})
        metrics.write_text(json.dumps(listed), encoding='utf-8')
        self.assertIsNone(RunFacts(run).unit_for(''))
        self.assertIsNone(RunFacts(run).unit_for(None))

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
        self.assertIn("on Fixit's data", asked['question'])
        self.assertEqual(asked['header'], 'Save')

    def test_the_save_question_says_only_what_the_person_cant_already_see(self):
        # The copy budget (the team, 2026-11-19 and 2026-11-22): no banner and no "hard part" line. On desktop the build
        # card above says what was built, so the question opens on the validation; in a terminal the text says it in
        # sentences.
        for widget in (True, False):
            text = questions.save(make_run(self.root / str(widget), saved=False, demo=True), widget=widget, data=[facts()])['question']
            for gone in ('CONTEXT BUILT', 'You turned', 'hard part'):
                self.assertNotIn(gone, text)
            self.assertIn('One metric in full', text)
            self.assertTrue(text.endswith('Where do you want to store your context?'))
            if widget:
                self.assertTrue(text.startswith("Validated on Fixit's data: all passed."), text[:80])
            else:
                self.assertTrue(text.startswith("Your job profit agent is built.\nIt answers from 2 metrics and 2 saved queries, "
                                                "built from Fixit's Power BI dashboard and 3 past queries.\n"
                                                "Validated on Fixit's data: all passed."), text[:200])

    def test_a_widget_host_leaves_what_was_built_to_the_card(self):
        asked = questions.save(make_run(self.root, saved=False), widget=True)
        self.assertTrue(asked['question'].startswith('Validated on your data: all passed.'), asked['question'][:80])
        for gone in ('is built', 'answers from', "how it's calculated"):
            self.assertNotIn(gone, asked['question'], 'the card is drawn as a widget instead')
        self.assertEqual(asked['shown'], [])

    def test_the_save_question_draws_no_bars_and_no_next_line(self):
        # 0.9.75: block characters break and wrap in a question's proportional font, and a "Next:" line before the
        # question said the question twice.
        for name, demo, widget in (('own', False, False), ('own-w', False, True), ('demo', True, False), ('demo-w', True, True)):
            with self.subTest(name=name):
                run = make_run(self.root / name, saved=False, demo=demo)
                (run / 'view').mkdir(exist_ok=True)
                (run / 'view' / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:5/key/'}), encoding='utf-8')
                text = questions.save(run, widget=widget, data=[facts()])['question']
                for block in '█░▓':
                    self.assertNotIn(block, text)
                self.assertFalse([x for x in text.splitlines() if x.strip().startswith('Next:')], text)

    def test_the_ready_question_says_where_the_context_is_stored_and_what_to_ask(self):
        run = make_run(self.root, local=True)
        asked = questions.ready(run, data=[facts()])
        text = asked['question']
        self.assertTrue(text.startswith('Stored on this machine, in this folder. To use it later, open a chat in that folder '
                                        'and ask about Job margin.'), text[:120])
        self.assertNotIn('job profit agent', text, 'own data names no demo agent')
        for row in ('- Kept', '- Ask it later', '- What it answers'):
            self.assertNotIn(row, text)
        self.assertNotIn('Tour the demo data', text, 'own data has no tour')
        self.assertTrue(text.endswith('What would you like to ask? Pick one, or type your own in Other.'))
        self.assertEqual([o['label'] for o in asked['options']], ['What is the gross margin in pounds?', 'How much did rework cost?',
                                                                  'Build a dashboard', "I'm done"],
                         "each a question the build wrote for a metric that passed its check, the dashboard's wording first; "
                         "the person's own goes in the free-text entry")
        self.assertEqual(asked['options'][-1]['description'], 'Your context stays stored on this machine.')

    def test_the_ready_question_repeats_the_save_question_only_on_a_recap(self):
        # The save question just said what was built and how it was validated: the ready question says it again only for
        # a resumed run, where the person has not seen the save question in this chat.
        for name, kwargs in (('local', {'local': True}), ('atlan', {}), ('atlan-w', {'widget': True})):
            with self.subTest(name=name):
                widget = kwargs.pop('widget', False)
                run = make_run(self.root / name, **kwargs)
                text = questions.ready(run, widget=widget)['question']
                for said in ('is built', 'is ready', 'It answers from', 'Validated on', 'passed their tests'):
                    self.assertNotIn(said, text)
                recap = questions.ready(run, widget=widget, recap=True)['question']
                self.assertIn('Your agent for Job margin is ready.', recap)
                self.assertIn('It answers from 2 metrics and 2 saved queries', recap)

    def test_the_ready_question_offers_four_options_at_most(self):
        run = make_run(self.root, local=True)
        asked = questions.ready(run, accuracy=True)
        self.assertEqual([o['label'] for o in asked['options']], ['What is the gross margin in pounds?', 'Check how accurate it is',
                                                                  'Build a dashboard', "I'm done"])
        self.assertEqual(len(questions.ready(make_run(self.root / 'atlan'), accuracy=True)['options']), 4)

    def test_a_demo_ready_question_names_its_tour_and_its_agent_on_a_recap(self):
        demo = dict(facts(), ready={'agent': 'job profit agent', 'answers': 'Questions about job margin', 'tour': 'https://example.com/tour/'})
        run = make_run(self.root, local=True, demo=True)
        asked = questions.ready(run, data=[demo])
        self.assertIn('To tour the demo data: https://example.com/tour/ shows the firm and its data.', asked['question'])
        self.assertNotIn('Build a dashboard', [o['label'] for o in asked['options']], 'a demo keeps its own order')
        self.assertIn('Your job profit agent is ready.', questions.ready(run, data=[demo], recap=True)['question'])

    def test_the_closing_message_says_what_was_built_and_how_to_use_it(self):
        local = questions.close(make_run(self.root / 'local', local=True))
        self.assertEqual(list(local), ['text'])
        text = local['text']
        self.assertTrue(text.startswith('Your agent for Job margin is built and ready.'))
        self.assertIn("It's stored on this machine, in this folder.", text)
        self.assertIn('save it to Atlan Trident', text, 'a local run is told how to share it')
        self.assertNotIn('Accuracy', text, 'no score when the check never ran')
        scored = questions.close(make_run(self.root / 'scored', local=True), score=80, scored=10)['text']
        self.assertIn('- Accuracy: 80% on 10 test questions.', scored)
        atlan = questions.close(make_run(self.root / 'atlan'))['text']
        self.assertIn('Saved to Atlan Trident, in Test workspace.', atlan)
        self.assertNotIn('save it to Atlan Trident', atlan)
        self.assertNotIn('Accuracy', atlan)
        one = questions.close(make_run(self.root / 'one'), score=100, scored=1)['text']
        self.assertIn('- Accuracy: 100% on its test question.', one)
        for said in (text, scored, atlan, one):
            self.assertFalse(said.rstrip().endswith('?'), 'it ends on no question')
            for block in '█░▓':
                self.assertNotIn(block, said)

    def test_the_close_command_prints_the_message_and_what_to_do_with_it(self):
        import subprocess
        from analytics_run import SCRIPTS
        run = make_run(self.root, local=True)
        out = subprocess.run([sys.executable, str(SCRIPTS / 'project.py'), '--run-dir', str(run), 'question', 'close',
                              '--score', '80', '--scored', '10'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        said = json.loads(out.stdout)
        self.assertEqual(set(said), {'text', 'next'})
        self.assertIn('- Accuracy: 80% on 10 test questions.', said['text'])
        self.assertIn('Ask no question', said['next'])

    def test_a_demo_that_left_out_its_facts_still_names_its_firm(self):
        # A live desktop run called `visual connect` without `--data`; the demo's working folder names the demo.
        for name, demo in (('demo', True), ('own', False)):
            run = make_run(self.root / name, saved=False, demo=demo)
            meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
            meta['source_root'] = str(self.root / name / 'atlan-analytics-demo' / 'yard')
            (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
            text = questions.save(run)['question'] + json.dumps(side_page.snapshot(run))
            if demo:
                self.assertIn("on Fixit's data", text)
                self.assertEqual(side_page.snapshot(run)['title'], 'Job profit agent for Fixit & Co.')
            else:
                self.assertNotIn('Fixit', text, 'a folder named like a demo never makes own data a demo')

    def test_the_ready_question_always_has_two_options(self):
        run = make_run(self.root, local=True)
        checks = run / 'context' / 'candidate-checks.json'
        entries = json.loads(checks.read_text(encoding='utf-8'))
        checks.write_text(json.dumps({k: ({'status': 'failed', 'errors': ['x']} if k.startswith('metric:') else v) for k, v in entries.items()}),
                          encoding='utf-8')
        # With nothing to pick there is no native question; with any option there are two at least, as the tool needs.
        asked = questions.ready(run)
        self.assertEqual([o['label'] for o in asked['options']], ['Build a dashboard', "I'm done"],
                         'no question passed: own data still offers the dashboard')
        self.assertTrue(asked['question'].endswith("What would you like to ask? Type it in Other, or pick what's next."))
        self.assertEqual([o['label'] for o in questions.ready(run, accuracy=True)['options']],
                         ['Check how accurate it is', 'Build a dashboard', "I'm done"])
        interaction = run / 'interaction.json'
        interaction.write_text(json.dumps(dict(json.loads(interaction.read_text(encoding='utf-8')), dashboard='built')),
                               encoding='utf-8')
        asked = questions.ready(run)
        self.assertEqual(asked['options'], [], 'no question passed, no check and a dashboard built: the person types')
        self.assertTrue(asked['question'].endswith('What would you like to ask?'))

    def test_a_demo_ready_question_is_headed_by_its_step_without_its_facts(self):
        run = make_run(self.root, local=True, demo=True)
        self.assertEqual(questions.ready(run)['header'], 'Ask')

    def test_a_partial_save_says_what_is_where(self):
        run = make_run(self.root)
        meta = json.loads((run / 'run.json').read_text(encoding='utf-8'))
        meta['publication']['artifacts'] = meta['publication']['artifacts'][:1]
        (run / 'run.json').write_text(json.dumps(meta), encoding='utf-8')
        text = ' '.join(questions.ready(run)['question'].split())
        # By kind, so it adds up against the saved card's "1 metric" and the 2 metrics and 2 saved queries built.
        self.assertIn('1 metric saved to Atlan Trident, in Test workspace; 1 metric and 2 saved queries stored on this machine', text)

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
