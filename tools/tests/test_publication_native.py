"""Regression tests for Analytics publication through ordinary native MCP tools."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_receipt_recording as fixtures
import publication_batches as batches
import publication_native as native
import publication_receipts as receipts


class NativePublicationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ReceiptRecording()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run = self.fixture.run
        self.workspace = self.fixture.workspace
        self.prepare()

    def prepare(self):
        ctx = self.fixture.ctx
        sources = ['example-source']
        plan_id, _ = batches.publication_identity(workspace_id=self.workspace, sources=sources,
                                                  documents=batches.checked_documents(ctx))
        result = batches.prepare(ctx, {'intent': 'publish', 'sources': sources, 'workspace_id': self.workspace,
                                     'approved_plans': [plan_id]}, self.workspace, sources, native=True)
        self.path = Path(result['manifest'])
        self.manifest = native.manifest_data(self.path, self.run, self.workspace)
        self.directory = Path(self.manifest['receipt_dir'])
        self.directory.mkdir(parents=True, exist_ok=True)

    def status(self, finalize=False):
        return receipts.assemble(self.run, self.directory, self.workspace, finalize, native=True)

    def claim(self, index):
        return native.claim(self.path, self.manifest, self.directory, index)

    def capture(self, request, response):
        return native.capture(self.directory, request, response, self.workspace)

    def created(self, index):
        artifact = self.fixture.artifacts[index]
        request = self.claim(index)
        self.assertEqual(set(request['arguments']), {'kind', 'items'})
        self.capture(request, {'items': [{'index': 0, 'ok': True, 'artifact': artifact}]})

    def get(self, index):
        return {'name': 'registry__get_artifact',
                'arguments': {'artifact_id': self.fixture.artifacts[index]['id'], 'channel': 'latest'}}

    def releases(self):
        for i, artifact in enumerate(self.fixture.artifacts):
            self.capture(self.get(i), artifact)

    def edges(self, outgoing):
        self.capture(self.status()['link_readback_calls'][0], {'incoming': [], 'outgoing': outgoing})

    def test_finishes_without_hook_arguments_and_reuses_existing_links(self):
        self.created(0)
        self.created(1)
        self.assertEqual(self.status()['readback_batches'], [{'calls': [self.get(0), self.get(1)]}])
        self.releases()
        self.assertEqual(self.status()['link_calls'], [])
        self.edges([])
        writes = self.status()['link_calls']
        self.assertEqual(len(writes), 1)
        [link] = writes[0]['arguments']['links']
        self.edges([{'rel_type': link['rel_type'], 'to': {'id': link['subject_id']}, 'attributes': link['attributes']}])
        self.assertEqual(self.status()['link_calls'], [])
        self.assertTrue(self.status(finalize=True)['recorded'])
        self.assertEqual(self.status(finalize=True)['artifacts'], 2)

    def test_native_phase_groups_requests_and_keeps_valid_receipts_after_a_refusal(self):
        command = [sys.executable, native.__file__, '--run-dir', str(self.run), '--manifest',
                   str(self.path), '--workspace', self.workspace]

        def run(*args, payload=None):
            return json.loads(subprocess.run([*command, *args], input=json.dumps(payload),
                                             capture_output=True, text=True, check=True).stdout)

        initial = run('status')
        self.assertEqual(initial['unsent_batches'], [0, 1])
        self.assertEqual(initial['lookup_calls'], [])
        group = run('claim', '0', '1')
        self.assertEqual([row['index'] for row in group['requests']], [0, 1])
        scratch = self.directory / 'native-results.json'
        scratch.write_text(json.dumps({'results': [
            {'batch': 0, 'reply': {'isError': True}},
            {'batch': 1, 'reply': {'items': [{'index': 0, 'ok': True, 'artifact': self.fixture.artifacts[1]}]}}
        ]}))
        recorded = run('advance', '--input', str(scratch))
        self.assertEqual(recorded['refused_inputs'], [0])
        self.assertEqual(recorded['captured'], 1)
        self.assertEqual(len(recorded['lookup_calls']), 1)
        self.assertEqual(recorded['verification_batches'][0]['calls'], [
            self.get(1), {'name': 'registry__get_related_artifacts',
                          'arguments': {'artifact_id': self.fixture.artifacts[1]['id']}}])
        self.assertEqual(recorded['link_calls'], [])

    def test_lost_create_reply_requires_lookup_and_never_replays(self):
        self.claim(0)
        with self.assertRaisesRegex(ValueError, 'never replay'):
            self.claim(0)
        self.created(1)
        [lookup] = self.status()['lookup_calls']
        self.capture(lookup, {'items': [self.fixture.artifacts[0]]})
        self.assertEqual(self.status()['lookup_calls'], [])
        self.assertIn(self.get(0), self.status()['readback_calls'])
        self.releases()
        self.assertEqual(self.status()['released_readbacks'], 2)

    def test_partial_batch_keeps_success_and_retries_only_missing_read(self):
        self.created(0)
        self.created(1)
        calls = [self.get(0), self.get(1)]
        results = [{'name': 'registry__get_artifact', 'resultType': 'complete', 'isError': False,
                    'content': [{'type': 'text', 'text': json.dumps(self.fixture.artifacts[0])}]},
                   {'name': 'registry__get_artifact', 'resultType': 'complete', 'isError': True,
                    'content': [{'type': 'text', 'text': '{}'}]}]
        self.assertEqual(self.capture({'name': 'mcp__call_read_tools', 'arguments': {'calls': calls}}, {'results': results}),
                         {'captured': 1, 'pending': 1})
        self.assertEqual(self.status()['readback_calls'], [self.get(1)])

    def test_conflict_is_held_and_cannot_create_a_link(self):
        self.created(0)
        self.capture(self.claim(1), {'items': [{'index': 0, 'ok': False, 'code': 'conflict'}]})
        self.capture(self.get(0), self.fixture.artifacts[0])
        result = self.status(finalize=True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['artifacts'], 1)
        self.assertEqual(len(result['held_links']), 1)
        self.assertEqual(result['link_calls'], [])
        self.assertEqual(result['lookup_calls'], [])
        state = json.loads((self.run / 'progress.json').read_text())
        self.assertEqual(state['status'], 'complete-with-held-items')

    def test_cannot_dispatch_after_the_approved_definition_changes(self):
        path = self.fixture.ctx / 'metrics.json'
        value = json.loads(path.read_text())
        value[0]['description'] += ' Changed.'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'changed after approval'):
            native.manifest_data(self.path, self.run, self.workspace)

    def test_reuse_requires_release_proof_and_prepares_zero_writes(self):
        (self.fixture.ctx / 'existing.json').write_text(json.dumps(
            [{**row, 'definition': row['metadata']['definition']} for row in self.fixture.artifacts]))
        fixtures.context_contracts.cmd_check(fixtures.SimpleNamespace(dir=str(self.fixture.ctx)))
        self.prepare()
        self.assertEqual(self.manifest['calls'], [])
        self.assertEqual(self.status()['readback_calls'], [self.get(0), self.get(1)])
