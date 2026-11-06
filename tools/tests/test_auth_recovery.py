"""Host task observations are distinct from current-host Registry readiness."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_local_project as fixtures
from context_state import ContextState
import auth_recovery


class AuthRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = ContextState(Path(self.temp.name).resolve() / 'run')

    def begin(self, **fields):
        return auth_recovery.advance(self.run, {'operation': 'begin', 'host': 'codex',
                                               'reason': 'sign_in', 'resume_stage': 'publish', **fields}, now=100)

    def observe(self, state, event, now=101, **fields):
        return auth_recovery.advance(self.run, {'operation': 'observe', 'attempt_id': state['attempt_id'],
                                               'event': event, **fields}, now=now)

    def test_host_task_completion_needs_a_current_host_read_before_auto_resume(self):
        state = self.begin()
        self.assertEqual(state['login_argv'], ['codex', 'mcp', 'login', 'atlan'])
        # A controlled child stands in for the host's long-running OAuth task.
        task = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.readline()'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: task.kill() if task.poll() is None else None)
        task_id = str(task.pid)
        state = self.observe(state, 'login_started', task_id=task_id)
        self.assertIsNone(task.poll())
        self.assertEqual(state['next_action'], 'poll_host_task')
        self.assertNotIn('login_argv', state)
        state = self.observe(state, 'browser_opened')
        self.assertTrue(state['browser_opened'])
        task.communicate('\n', timeout=10)
        self.assertEqual(task.returncode, 0)
        state = self.observe(state, 'login_succeeded', task_id=task_id)
        self.assertEqual(state['verification'], 'not_verified')
        self.assertEqual(state['next_action'], 'read_registry_in_current_host')
        with self.assertRaises(ValueError):
            self.observe(state, 'registry_ready', read_tool='registry__list_workspaces', read_succeeded=False)
        state = self.observe(state, 'registry_ready', read_tool='registry__list_workspaces', read_succeeded=True)
        self.assertEqual(state['next_action'], 'resume_saved_stage')
        self.assertEqual(state['resume_stage'], 'publish')
        self.assertFalse(state['remote_writes'])
        self.assertEqual((self.run.root / 'auth-recovery.json').stat().st_mode & 0o777, 0o600)

    def test_resume_keeps_one_task_and_rejects_other_task_completion(self):
        state = self.observe(self.begin(), 'login_started', task_id='job_123')
        repeated = self.begin()
        self.assertEqual(repeated['attempt_id'], state['attempt_id'])
        self.assertEqual(repeated['task_id'], 'job_123')
        self.assertNotIn('login_argv', repeated)
        with self.assertRaises(ValueError):
            self.observe(state, 'login_succeeded', task_id='job_other')
        with self.assertRaises(ValueError):
            self.begin(host='claude_code')

    def test_timeout_cancels_only_owned_task_and_does_not_resume_on_late_success(self):
        state = self.observe(self.begin(timeout_seconds=30), 'login_started', task_id='job_123')
        expired = auth_recovery.advance(self.run, {'operation': 'poll'}, now=131)
        self.assertEqual(expired['status'], 'timed_out')
        self.assertEqual(expired['stop_owned_task_if_running'], 'job_123')
        self.assertEqual(expired['next_action'], 'show_manual_recovery')
        self.assertIn('codex plugin add atlanai@atlan', expired['fallback']['install_commands'])
        with self.assertRaises(ValueError):
            self.observe(state, 'login_succeeded', now=132, task_id='job_123')

    def test_disabled_missing_and_denied_connections_do_not_launch_oauth(self):
        for reason, action in [('disabled', 'request_connection_enablement'),
                               ('plugin_missing', 'install_core_plugin'),
                               ('permission_denied', 'resolve_workspace_permission'),
                               ('tools_missing', 'inspect_host_plugin_and_connection')]:
            with self.subTest(reason=reason):
                state = self.begin(reason=reason)
                self.assertEqual(state['next_action'], action)
                self.assertNotIn('login_argv', state)

    def test_restarting_expired_attempt_retains_task_for_cleanup(self):
        state = self.observe(self.begin(timeout_seconds=30), 'login_started', task_id='job_123')
        expired = auth_recovery.advance(self.run, {'operation': 'begin', 'host': 'codex',
                                                  'reason': 'sign_in'}, now=131)
        self.assertEqual(expired['status'], 'timed_out')
        self.assertEqual(expired['attempt_id'], state['attempt_id'])
        self.assertEqual(expired['stop_owned_task_if_running'], 'job_123')
        self.assertNotIn('login_argv', expired)

    def test_permission_denied_stops_owned_watch_without_retrying_login(self):
        state = self.observe(self.begin(), 'login_started', task_id='job_123')
        state = self.observe(state, 'permission_denied')
        self.assertEqual(state['next_action'], 'resolve_workspace_permission')
        self.assertEqual(state['stop_owned_task_if_running'], 'job_123')
        self.assertNotIn('login_argv', state)

    def test_tools_missing_can_recover_by_read_without_starting_login(self):
        state = self.begin(reason='tools_missing')
        state = self.observe(state, 'registry_ready', read_tool='registry__list_workspaces', read_succeeded=True)
        self.assertEqual(state['status'], 'connected')
        self.assertNotIn('task_id', state)

    def test_new_attempt_rejects_stale_observations_and_preserves_setup_files(self):
        self.run.write(self.run.context / 'metrics.json', [{'name': 'example-metric'}])
        before = (self.run.context / 'metrics.json').read_bytes()
        old = self.observe(self.begin(), 'canceled')
        current = self.begin()
        self.assertNotEqual(old['attempt_id'], current['attempt_id'])
        with self.assertRaises(ValueError):
            self.observe(old, 'login_started', task_id='old_job')
        self.assertEqual(before, (self.run.context / 'metrics.json').read_bytes())

    def test_does_not_store_urls_tokens_raw_output_or_shell_arguments(self):
        for extra in ({'authorization_url': 'https://example.com/oauth'}, {'token': 'example'},
                      {'raw_output': 'example'}, {'server': '--unsafe'}, {'server': 'atlan; command'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.begin(**extra)
        self.assertFalse((self.run.root / 'auth-recovery.json').exists())

    def test_claude_fallback_and_cli_route_use_existing_core_plugin(self):
        request = self.run.root / 'request.json'
        request.write_text(json.dumps({'operation': 'begin', 'host': 'claude_code', 'reason': 'sign_in'}))
        process = subprocess.run([sys.executable, '-I', str(fixtures.SCRIPTS / 'project.py'),
                                  '--run-dir', str(self.run.root), 'auth', str(request)],
                                 text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result['login_argv'], ['claude', 'mcp', 'login', 'plugin:atlanai:atlan'])
        self.assertEqual(result['fallback']['install_commands'],
                         ['claude plugin install atlanai --marketplace atlanai/atlan-plugins'])

    def test_successful_login_with_unloaded_tools_reports_reload_not_connected(self):
        state = self.observe(self.begin(), 'login_started', task_id='job_123')
        state = self.observe(state, 'login_succeeded', task_id='job_123')
        state = self.observe(state, 'tools_not_loaded')
        self.assertEqual(state['status'], 'reload_required')
        self.assertEqual(state['verification'], 'not_verified')
        state = self.observe(state, 'registry_ready', read_tool='registry__list_workspaces', read_succeeded=True)
        self.assertEqual(state['next_action'], 'resume_saved_stage')


if __name__ == '__main__':
    unittest.main()
