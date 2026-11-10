"""Publishing and deterministic receipts through aliased, native harness hook contracts."""
import copy
import json
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch
import unittest

import test_receipt_recording as fixtures

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import lint
sys.path.insert(0, str(ROOT / 'plugins/atlanai/command-hooks'))
import publish_hook as hook
import publication_batches
import publication_receipts


class PublishingHarnesses(unittest.TestCase):
    def setUp(self):
        fixtures.ReceiptRecording.setUp(self)
        documents = {name: json.loads((Path(self.ctx) / (name + '.json')).read_text())
                     for name in ('plan', 'metrics', 'queries', 'links')}
        plan_id, _ = publication_batches.publication_identity(
            workspace_id=self.workspace, sources=['example-source'], documents=documents)
        self.prepared = publication_batches.prepare(self.ctx,
            {'intent': 'publish', 'workspace_id': self.workspace, 'sources': ['example-source'],
             'approved_plans': [plan_id]},
            self.workspace, ['example-source'], True)

    def event(self, host, request, index, directory, style='rewritten'):
        request = copy.deepcopy(request)
        request['arguments']['receipt_dir'] = str(directory)
        outer = {**request, 'session_id': 'session_example', 'rationale': 'Publish the checked definitions.'}
        event = {'cwd': str(self.root), 'session_id': host + style, 'tool_use_id': str(index),
                 'tool_name': 'mcp__atlan-c360__mcp__call_write_tool', 'tool_input': outer}
        if host == 'cursor':
            event.update(tool_name='MCP:mcp__call_write_tool',
                         mcp_server_name='atlan-c360', mcp_server_url='https://api.atlan.com/mcp')
        if host == 'copilot':
            event = {'cwd': str(self.root), 'sessionId': host + style,
                     'toolName': event['tool_name'], 'toolArgs': outer}
        return event

    def call(self, host, request, response, index, directory, style):
        event = self.event(host, request, index, directory, style)
        pre = hook.answer('pre', host, event)
        self.assertIsNotNone(pre)
        sent = (pre['hookSpecificOutput']['updatedInput'] if host == 'codex'
                else pre['modifiedArgs'] if host == 'copilot' else pre['updated_input'])
        self.assertNotIn('receipt_dir', sent['arguments'])
        self.assertNotIn('items_from', sent['arguments'])
        if host == 'copilot':
            self.assertNotIn('permissionDecision', pre)
            event['toolArgs'] = sent if style == 'rewritten' else event['toolArgs']
            event['toolResult'] = {'resultType': 'success', 'textResultForLlm': json.dumps(response)}
        else:
            event['tool_input'] = sent if style == 'rewritten' else event['tool_input']
            event['tool_response'] = {'content': [{'type': 'text', 'text': json.dumps(response)}]}
        post = hook.answer('post', host, event)
        self.assertIn('Atlan receipt saved', json.dumps(post))
        return sent

    def test_all_adapters_publish_exact_bodies_and_finalize_identical_receipts_under_atlan_c360(self):
        for host in ('codex', 'cursor', 'copilot'):
            for style in ('original', 'rewritten'):
                with self.subTest(host=host, post_arguments=style):
                    directory = self.run / host / style / 'receipts'
                    for index, call in enumerate(self.prepared['calls']):
                        request = {k: call[k] for k in ('name', 'arguments')}
                        expected = json.loads(Path(request['arguments']['items_from']).read_text())
                        response = {'items': [{'index': i, 'ok': True, 'artifact': {
                            **item, 'kind': request['arguments']['kind'],
                            'id': request['arguments']['kind'] + '_example'}} for i, item in enumerate(expected)]}
                        sent = self.call(host, request, response, index, directory, style)
                        self.assertEqual(sent['arguments']['items'], expected)
                    for index, artifact in enumerate(self.artifacts, 10):
                        self.call(host, {'name': 'registry__get_artifact', 'arguments': {
                            'artifact_id': artifact['id'], 'channel': 'latest'}}, artifact,
                            index, directory, style)
                    self.call(host, {'name': 'registry__get_related_artifacts', 'arguments': {
                        'artifact_id': self.artifacts[1]['id']}}, {'incoming': [], 'outgoing': [{
                            'rel_type': 'source_of', 'to': {'id': self.artifacts[0]['id']},
                            'attributes': {'why': 'Source evidence'}}]}, 20, directory, style)
                    self.assertTrue(publication_receipts.assemble(self.run, directory, self.workspace, True)['recorded'])
                    self.assertTrue(publication_receipts.assemble(self.run, directory, self.workspace, True)['recorded'])
                    saved = json.loads((self.run / 'captured-readbacks.json').read_text())
                    self.assertEqual({a['id']: a for a in saved['artifacts']}, {a['id']: a for a in self.artifacts})

    def test_wildcard_accepts_unknown_alias_only_with_an_atlan_endpoint_in_command_hooks(self):
        call = self.prepared['calls'][0]
        event = self.event('codex', {k: call[k] for k in ('name', 'arguments')}, 0, self.run / 'receipts')
        event['tool_name'] = 'mcp__personal_gateway__mcp__call_write_tool'
        self.assertIsNone(hook.answer('pre', 'codex', event))
        event['mcp_server_url'] = 'https://api.atlan.com/mcp'
        self.assertIn('updatedInput', hook.answer('pre', 'codex', event)['hookSpecificOutput'])

    def test_foreign_insecure_and_credential_bearing_urls_never_expand_even_with_an_atlan_name(self):
        call = self.prepared['calls'][0]
        event = self.event('codex', {k: call[k] for k in ('name', 'arguments')}, 0, self.run / 'receipts')
        for url in ('https://unrelated.example/mcp', 'https://atlan.com.unrelated.example/mcp',
                    'http://api.atlan.com/mcp', 'https://user@example.atlan.com/mcp',
                    'https://api.atlan.com/not-mcp', 'https://api.atlan.com/mcp?target=example'):
            with self.subTest(url=url):
                self.assertIsNone(hook.answer('pre', 'codex', {**event, 'mcp_server_url': url}))

    def test_full_mode_native_calls_keep_the_existing_cursor_route(self):
        event = {'tool_name': 'registry__create_artifacts',
                 'tool_input': json.dumps({'kind': 'metric', 'items': []}),
                 'mcp_server_name': 'atlan-c360',
                 'mcp_server_url': 'https://api.atlan.com/mcp?tools=full'}
        self.assertIsNone(hook.answer('mcp-guard', 'cursor', event))

    def test_copilot_failure_cannot_turn_a_result_into_a_completed_receipt(self):
        directory = self.run / 'receipts'
        artifact = self.artifacts[0]
        event = self.event('copilot', {'name': 'registry__get_artifact', 'arguments': {
            'artifact_id': artifact['id'], 'channel': 'latest'}}, 1, directory)
        event['toolArgs'] = hook.answer('pre', 'copilot', event)['modifiedArgs']
        event['toolResult'] = {'resultType': 'failure', 'textResultForLlm': json.dumps(artifact)}
        out = hook.answer('post', 'copilot', event)
        self.assertIn('Receipt recording is pending', json.dumps(out))
        self.assertEqual(list(directory.glob('*.json')), [])

    def test_copilot_command_entrypoint_reads_and_records_the_native_payload(self):
        directory = self.run / 'command-receipts'
        artifact = self.artifacts[0]
        event = self.event('copilot', {'name': 'registry__get_artifact', 'arguments': {
            'artifact_id': artifact['id'], 'channel': 'latest'}}, 1, directory)
        script = ROOT / 'plugins/atlanai/command-hooks/publish_hook.py'
        def invoke(entry, value):
            result = subprocess.run([sys.executable, str(script), entry, '--host', 'copilot'],
                input=json.dumps(value), text=True, capture_output=True, check=True, timeout=10)
            return json.loads(result.stdout)
        event['toolArgs'] = json.dumps(event['toolArgs'])
        modified = invoke('pre', event)
        self.assertNotIn('permissionDecision', modified)
        event['toolArgs'] = modified['modifiedArgs']
        event['toolResult'] = {'resultType': 'success', 'textResultForLlm': json.dumps(artifact)}
        self.assertIn('Atlan receipt saved', json.dumps(invoke('post', event)))
        receipt = json.loads(next(directory.glob('*.json')).read_text())
        self.assertEqual(receipt['response'], artifact)

    def test_copilot_hook_lint_rejects_unrelated_commands(self):
        source = ROOT / 'plugins/atlanai/com.github.copilot/hooks/hooks.json'
        destination = self.root / 'plugin/com.github.copilot/hooks/hooks.json'
        destination.parent.mkdir(parents=True)
        document = json.loads(source.read_text())
        destination.write_text(json.dumps(document))
        with patch.object(lint, 'ROOT', self.root):
            self.assertEqual(lint.lint_command_hooks(self.root / 'plugin'), [])
            document['hooks']['preToolUse'][0]['bash'] = 'echo unexpected-command'
            destination.write_text(json.dumps(document))
            self.assertEqual(len(lint.lint_command_hooks(self.root / 'plugin')), 1)

    def test_copilot_loader_and_commands_use_the_documented_plugin_location(self):
        path = ROOT / 'plugins/atlanai/com.github.copilot/hooks/hooks.json'
        document = json.loads(path.read_text())
        self.assertEqual(document['version'], 1)
        self.assertEqual(set(document['hooks']), {'preToolUse', 'postToolUse'})
        for groups in document['hooks'].values():
            for group in groups:
                self.assertIn('${COPILOT_PLUGIN_ROOT}/command-hooks/publish_hook.py', group['bash'])
                self.assertIn('--host copilot', group['bash'])


if __name__ == '__main__':
    unittest.main()
