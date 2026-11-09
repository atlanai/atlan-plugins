"""Deterministic receipt persistence, interrupted-run recovery and live reconciliation contracts."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
import metrics_mine
import publication_receipts
from project import Project
from test_analytics_reconciliation import metric, query, link


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


store = module(ROOT / 'plugins/atlanai/command-hooks/receipt_store.py', 'receipt_store_test')
hook = module(ROOT / 'plugins/atlanai/command-hooks/publish_hook.py', 'receipt_hook_test')


class ReceiptRecording(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'run'
        self.ctx = self.run / 'context'
        self.ctx.mkdir(parents=True)
        self.receipts = self.run / 'receipts'
        self.workspace = 'workspace_example'
        self.metrics = [metric('order-amount')]
        self.queries = [query('paid-order-count')]
        self.links = [link('sql_query', 'paid-order-count', 'order-amount')]
        for name, value in (('metrics', self.metrics), ('queries', self.queries), ('links', self.links), ('existing', [])):
            (self.ctx / (name + '.json')).write_text(json.dumps(value))
        (self.run / 'run.json').write_text(json.dumps({'version': 2, 'entrypoint': 'project', 'sources': [], 'source_root': str(self.root)}))
        metrics_mine.cmd_check(SimpleNamespace(dir=str(self.ctx)))
        self.metrics = json.loads((self.ctx / 'metrics.json').read_text())
        self.artifacts = []
        for kind, bodies in (('metric', self.metrics), ('sql_query', self.queries)):
            for body in bodies:
                ident = kind + '_example'
                self.artifacts.append({**body, 'id': ident, 'workspace_id': self.workspace,
                    'kind': kind, 'is_released': True, 'artifact_status': 'active',
                    'metadata': {'definition': body['definition']},
                    'release': {'release_id': 'rel_' + ident, 'version_id': 'ver_' + ident,
                                'channel': 'latest', 'release_status': 'released', 'is_current': True}})
                self.artifacts[-1].pop('definition', None)

    def capture(self, name, args, response, is_error=False):
        return store.capture({'cwd': str(self.root), 'roots': [str(self.root)], 'receipt_dir': str(self.receipts),
            'request': {'name': name, 'arguments': args}, 'response_text': json.dumps(response), 'is_error': is_error})

    def release(self, artifact):
        return self.capture('registry__get_artifact', {'artifact_id': artifact['id'], 'channel': 'latest'}, artifact)

    def edges(self):
        return self.capture('registry__get_related_artifacts', {'artifact_id': self.artifacts[1]['id']}, {
            'incoming': [], 'outgoing': [{'rel_type': 'source_of', 'to': {'id': self.artifacts[0]['id']},
                                        'attributes': {'why': 'Source evidence'}}], 'count': 1})

    def assemble(self, finalize=False):
        return publication_receipts.assemble(self.run, self.receipts, self.workspace, finalize)

    def test_resume_after_partial_recording_retains_exact_native_bodies_and_finalizes_idempotently(self):
        first = self.release(self.artifacts[0])
        self.assertEqual(first, self.release(self.artifacts[0]))
        self.assertEqual(self.assemble()['status'], 'pending')
        with self.assertRaises(ValueError):
            self.assemble(True)
        self.release(self.artifacts[1])
        self.assertEqual(self.assemble()['status'], 'pending')  # Links still need proof.
        self.edges()
        self.assertTrue(self.assemble(True)['recorded'])
        self.assertTrue(self.assemble(True)['recorded'])
        saved = json.loads((self.run / 'captured-readbacks.json').read_text())
        self.assertEqual({a['id']: a for a in saved['artifacts']}, {a['id']: a for a in self.artifacts})
        receipt = Project(self.run).current()['publication']
        self.assertEqual(len(receipt['artifacts']), 2)
        self.assertTrue(all('definition_hash' in a and 'definition' not in a for a in receipt['artifacts']))

    def test_resume_descriptors_keep_local_receipt_paths_short_without_changing_the_target(self):
        body = {k: self.artifacts[0][k] for k in ('name', 'source_id', 'workspace_id')}
        self.capture('registry__create_artifacts', {'kind': 'metric', 'items': [body]}, {
            'items': [{'index': 0, 'ok': True, 'artifact': self.artifacts[0]}]})
        with patch('os.getcwd', return_value=str(self.root)):
            call = self.assemble()['readback_calls'][0]
        target = Path(call['arguments']['receipt_dir'])
        self.assertFalse(target.is_absolute())
        self.assertEqual(self.root / target, self.receipts)

    def test_new_run_still_reconciles_live_inventory_without_duplicate_creates(self):
        self.release(self.artifacts[0]); self.release(self.artifacts[1]); self.edges(); self.assemble(True)
        live = [{**a, 'definition': a['metadata']['definition']} for a in self.artifacts]
        (self.ctx / 'existing.json').write_text(json.dumps(live))
        result = metrics_mine.cmd_check(SimpleNamespace(dir=str(self.ctx)))
        self.assertEqual(result['counts']['metrics'], {'create': 0, 'revise': 0, 'reuse': 1})
        self.assertEqual(result['counts']['queries'], {'create': 0, 'revise': 0, 'reuse': 1})
        self.assertTrue(self.assemble(True)['recorded'])

    def test_failed_head_wrong_identity_and_conflicting_release_do_not_become_publication(self):
        a = self.artifacts[0]
        with self.assertRaises(ValueError):
            self.capture('registry__get_artifact', {'artifact_id': a['id']}, a)
        with self.assertRaises(ValueError):
            self.capture('registry__get_artifact', {'artifact_id': 'metric_other', 'channel': 'latest'}, a)
        with self.assertRaises(ValueError):
            self.capture('registry__get_artifact', {'artifact_id': a['id'], 'channel': 'latest'}, {'error': 'unavailable'}, True)
        self.release(a)
        changed = copy.deepcopy(a); changed['release']['release_id'] = 'rel_other'
        self.release(changed)
        with self.assertRaises(ValueError):
            self.assemble()

    def test_changed_definition_or_truncated_file_blocks_completion(self):
        changed = copy.deepcopy(self.artifacts[0]); changed['metadata']['definition']['formula'] = 'SUM(other.amount)'
        self.release(changed)
        with self.assertRaises(ValueError):
            self.assemble()
        path = next(self.receipts.glob('*.json')); path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            self.assemble()

    def test_atomic_write_failure_never_exposes_a_completed_receipt_and_can_be_recovered(self):
        with patch.object(store.os, 'link', side_effect=OSError('simulated interruption')):
            with self.assertRaises(OSError):
                self.release(self.artifacts[0])
        self.assertFalse(list(self.receipts.glob('*.json')))
        self.release(self.artifacts[0])
        self.assertEqual(len(list(self.receipts.glob('*.json'))), 1)

    def test_symlink_and_outside_destinations_are_refused_without_writes(self):
        outside = self.root / 'outside'; outside.mkdir()
        linked = self.root / 'linked'; linked.symlink_to(outside, target_is_directory=True)
        payload = {'cwd': str(self.run), 'roots': [str(self.run)], 'request': {
            'name': 'registry__get_artifact', 'arguments': {'artifact_id': 'metric_example', 'channel': 'latest'}}}
        for directory in (str(linked), str(self.root / '.aws'), '../outside'):
            with self.assertRaises((ValueError, OSError)):
                store.directory(directory, payload['cwd'], payload['roots'], True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_command_hook_keeps_local_destination_across_rewritten_post_input_in_both_hosts(self):
        for host in ('codex', 'cursor'):
            event = {'cwd': str(self.root), 'session_id': 'session_example', 'tool_use_id': 'read_' + host,
                     'tool_name': 'mcp__atlan__mcp__call_read_tool' if host == 'codex' else 'MCP:mcp__call_read_tool',
                     'tool_input': {'name': 'registry__get_artifact', 'arguments': {
                         'artifact_id': self.artifacts[0]['id'], 'channel': 'latest', 'receipt_dir': str(self.receipts)}}}
            pre = hook.answer('pre', host, event)
            native = pre['hookSpecificOutput']['updatedInput'] if host == 'codex' else pre['updated_input']
            self.assertNotIn('receipt_dir', native['arguments'])
            event.update(tool_input=native, tool_response={'isError': False, 'content': [
                {'type': 'text', 'text': json.dumps(self.artifacts[0])}]})
            post = hook.answer('post', host, event)
            self.assertIn('Atlan receipt saved', json.dumps(post))
        self.assertEqual(len(list(self.receipts.glob('*.json'))), 1)

    def test_partial_bulk_results_are_saved_but_never_mistaken_for_release_readbacks(self):
        body = {k: self.artifacts[0][k] for k in ('name', 'source_id', 'workspace_id')}
        request = {'kind': 'metric', 'items': [body, {**body, 'name': 'other', 'source_id': 'other'}]}
        response = {'created': 1, 'failed': 1, 'items': [
            {'index': 0, 'ok': True, 'artifact': self.artifacts[0]}, {'index': 1, 'ok': False, 'error': 'refused'}]}
        self.capture('registry__create_artifacts', request, response)
        result = self.assemble()
        self.assertEqual(result['captured_creates'], 1)
        self.assertEqual(result['released_readbacks'], 0)
        self.assertEqual(result['status'], 'pending')
