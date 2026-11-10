"""Local bulk publication transport, using synthetic definitions and no network."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / 'plugins/atlanai'
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import publication_batches as batches


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = load(CORE / 'command-hooks/publish_hook.py', 'bulk_hook')


def query(index=0):
    return {'name': 'query-' + str(index), 'display_name': 'Example query', 'source_id': 'example-query-' + str(index),
            'definition': {'spec_version': '1.0-draft.1', 'dialect': 'sqlite',
                           'question': 'How many orders are there?', 'sql': 'SELECT\n  COUNT(*)\nFROM example_orders'}}


class PublicationBatches(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.auth = {'intent': 'publish', 'workspace_id': 'workspace_example', 'sources': ['example-source']}

    def context(self, count=40):
        documents = {'metrics': [], 'queries': [query(i) for i in range(count)], 'links': [],
                     'plan': {'metrics': [], 'links': [], 'queries': [
                         {'source_id': query(i)['source_id'], 'action': 'create', 'ok': True, 'match_id': None}
                         for i in range(count)]}}
        self.write(documents)
        self.approve(documents)
        return documents

    def approve(self, documents):
        """The user's yes to this exact plan, as the skill records it."""
        identity, _ = batches.publication_identity(workspace_id='workspace_example',
            sources=['example-source'], documents=documents)
        self.auth['approved_plans'] = [identity]

    def write(self, documents):
        for name, value in documents.items():
            (self.root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')

    def prepare(self):
        return batches.prepare(self.root, self.auth, 'workspace_example', ['example-source'])

    def event(self, call, host='codex'):
        return {'cwd': str(self.root), 'tool_name': 'mcp__atlan__mcp__call_write_tool' if host == 'codex' else 'MCP:mcp__call_write_tool',
                'tool_input': {'name': call['name'], 'arguments': call['arguments'],
                               'session_id': 'session_example', 'rationale': 'Publish the checked definitions.'}}

    def test_receipt_paths_are_relative_only_when_inside_the_current_directory(self):
        self.context(1)
        with patch('os.getcwd', return_value=str(self.root.parent)):
            result = batches.prepare(self.root, self.auth, 'workspace_example', ['example-source'], True)
        self.assertFalse(Path(result['receipt_dir']).is_absolute())
        self.assertEqual(result['calls'][0]['arguments']['receipt_dir'], result['receipt_dir'])
        self.assertEqual(self.root.parent / result['receipt_dir'], self.root.parent / 'receipts' / result['plan_id'])
        with patch('os.getcwd', return_value='/unrelated-worktree'):
            other = batches.prepare(self.root, self.auth, 'workspace_example', ['example-source'], True)
        self.assertTrue(Path(other['receipt_dir']).is_absolute())
        self.assertNotIn('..', Path(other['receipt_dir']).parts)

    def test_forty_definitions_use_two_calls_and_keep_sql_exact_in_both_hosts(self):
        documents = self.context()
        result = self.prepare()
        self.assertEqual([call['count'] for call in result['calls']], [25, 15])
        for host in ('codex', 'cursor'):
            published = []
            for call in result['calls']:
                event = self.event(call, host)
                output = hook.answer('pre', host, event)
                sent = output['hookSpecificOutput']['updatedInput'] if host == 'codex' else output['updated_input']
                self.assertEqual(sent['session_id'], 'session_example')
                self.assertEqual(set(sent['arguments']), {'kind', 'items'})
                published.extend(sent['arguments']['items'])
            self.assertEqual(published, [{**q, 'workspace_id': 'workspace_example'} for q in documents['queries']])
        self.assertEqual(result, self.prepare())

    def test_no_payload_bodies_in_model_arguments_and_string_arguments_work(self):
        self.context(1)
        call = self.prepare()['calls'][0]
        self.assertNotIn('SELECT', json.dumps(call))
        event = self.event(call)
        event['tool_input']['arguments'] = json.dumps(call['arguments'])
        sent, refused = hook.expand_bulk(event['tool_input'], event)
        self.assertIsNone(refused)
        self.assertIsInstance(sent['arguments'], str)
        self.assertEqual(len(json.loads(sent['arguments'])['items']), 1)

    def test_drafts_changed_scope_and_revisions_are_not_bulk_created(self):
        docs = self.context(3)
        for row, action in zip(docs['plan']['queries'], ('revise', 'reuse', 'create')):
            row['action'] = action
        self.write(docs)
        with self.assertRaises(ValueError):
            self.prepare()  # The changed plan, with its revision, is not yet approved.
        self.approve(docs)
        result = self.prepare()
        self.assertEqual(result['deferred']['revise'], 1)
        self.assertEqual(result['deferred']['reuse'], 1)
        self.assertEqual(result['calls'][0]['source_ids'], ['example-query-2'])
        self.auth['intent'] = 'draft'
        with self.assertRaises(ValueError):
            self.prepare()

    def test_unsafe_or_changed_batches_are_refused_without_exposing_content(self):
        self.context(1)
        call = self.prepare()['calls'][0]
        original = Path(call['arguments']['items_from'])
        for change in ('hash', 'workspace', 'mixed', 'inline', 'symlink', 'parent-link', 'credential-path', 'secret', 'escaped-secret', 'oversize', 'malformed', 'kind'):
            with self.subTest(change=change):
                candidate = copy.deepcopy(call)
                args = candidate['arguments']
                data = original.read_bytes()
                path = self.root / 'payload.json'
                if path.is_symlink():
                    path.unlink()
                path.write_bytes(data)
                args['items_from'] = str(path)
                if change == 'hash':
                    args['items_sha256'] = '0' * 64
                elif change == 'workspace':
                    args['workspace_id'] = 'workspace_other'
                elif change == 'mixed':
                    items = json.loads(data)
                    items.append({**items[0], 'source_id': 'other', 'workspace_id': 'workspace_other'})
                    path.write_text(json.dumps(items))
                elif change == 'inline':
                    args['items'] = json.loads(data)
                elif change == 'symlink':
                    path.unlink()
                    path.symlink_to(original)
                elif change == 'parent-link':
                    parent = self.root / 'linked'
                    parent.symlink_to(original.parent, target_is_directory=True)
                    args['items_from'] = str(parent / original.name)
                elif change == 'credential-path':
                    args['items_from'] = str(self.root / '.aws' / 'payload.json')
                elif change in ('secret', 'escaped-secret'):
                    token = 'sk-' + 'a' * 35
                    items = json.loads(data)
                    items[0]['description'] = token
                    text = json.dumps(items)
                    path.write_text(text.replace(token, '\\u0073' + token[1:]) if change == 'escaped-secret' else text)
                elif change == 'oversize':
                    path.write_bytes(b' ' * (256 * 1024 + 1))
                elif change == 'malformed':
                    path.write_text('{ private malformed content')
                elif change == 'kind':
                    args['kind'] = 'agent'
                if change != 'hash':
                    args['items_sha256'] = hashlib.sha256(Path(args['items_from']).read_bytes()).hexdigest() if Path(args['items_from']).is_file() else '0' * 64
                sent, refused = hook.expand_bulk(self.event(candidate)['tool_input'], self.event(candidate))
                self.assertIsNone(sent)
                self.assertIn('Nothing was sent', refused)
                self.assertNotIn('private malformed', refused)

    def test_other_servers_are_never_expanded_and_bulk_has_no_skill_retry_notice(self):
        self.context(1)
        event = self.event(self.prepare()['calls'][0])
        event['tool_name'] = 'mcp__other__mcp__call_write_tool'
        self.assertIsNone(hook.answer('pre', 'codex', event))
        self.assertIsNotNone(hook.guard({**event, 'mcp_server_name': 'other'}))
        event['tool_name'] = 'mcp__atlan__mcp__call_write_tool'
        event['tool_response'] = {'isError': True, 'content': [{'type': 'text', 'text': '503 may_have_run'}]}
        self.assertIsNone(hook.answer('post', 'codex', event))

    def test_oversize_definition_and_omitted_plan_body_are_refused(self):
        docs = self.context(1)
        docs['queries'][0]['description'] = 'x' * (250 * 1024)
        self.write(docs)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root / 'publish-batches').exists())
        docs = self.context(1)
        docs['plan']['queries'] = []
        self.write(docs)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_manual_approval_is_bound_to_the_exact_bodies_and_engine_holds_are_required(self):
        docs = self.context(1)
        identity, _ = batches.publication_identity(workspace_id='workspace_example',
            sources=['example-source'], documents=docs)
        self.auth.update(mode='ask', approved_plans=[identity])
        self.assertEqual(len(self.prepare()['calls']), 1)
        docs['queries'][0]['description'] = 'A changed definition needs review.'
        self.write(docs)
        with self.assertRaises(ValueError):
            self.prepare()
        self.auth.update(mode='auto')
        (self.root / 'metrics-source.json').write_text(json.dumps({'module': 'metrics'}))
        with self.assertRaises(ValueError):
            self.prepare()
