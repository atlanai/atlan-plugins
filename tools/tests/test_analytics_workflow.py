"""Synthetic regressions for the customer's interrupted read/build/publish journey."""
from pathlib import Path
import sys
import json
import subprocess
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[2] / 'plugins/atlanai-analytics'
sys.path.insert(0, str(PLUGIN))
from modules.workflow import Authorization


class WorkflowAuthorizationTests(unittest.TestCase):
    def policy(self, **changes):
        return Authorization.from_record({'mode': 'auto', 'intent': 'publish',
            'workspace_id': 'workspace_demo', 'sources': ['https://example.invalid/report'],
            'basis': 'user_request', **changes})

    def publication(self, policy, **changes):
        return policy.publication(**{'workspace_id': 'workspace_demo', 'plan_id': 'plan-demo',
            'sources': ['https://example.invalid/report'], **changes})

    def test_selected_source_generated_reads_and_initialization_do_not_repeat_questions(self):
        policy = self.policy()
        for action in ('read_generated', 'initialize_runtime', 'generate', 'validate'):
            self.assertEqual(policy.local_step(action)['decision'], 'continue')
        self.assertEqual(policy.local_step('read_source', 'https://example.invalid/report')['decision'], 'continue')
        self.assertEqual(policy.local_step('read_source', 'https://example.invalid/other')['decision'], 'needs_context')

    def test_publication_waits_for_approval_of_the_exact_plan(self):
        self.assertEqual(Authorization().mode, 'ask')
        self.assertEqual(self.publication(self.policy())['decision'], 'needs_confirmation')
        self.assertEqual(self.publication(self.policy(approved_plans=['plan-demo']))['decision'], 'continue')
        self.assertEqual(self.publication(self.policy(approved_plans=['plan-other']))['decision'], 'needs_confirmation')
        self.assertEqual(self.publication(self.policy(), workspace_id='workspace_other')['decision'], 'needs_context')
        self.assertEqual(self.publication(self.policy(), sources=['https://example.invalid/other'])['decision'], 'needs_context')
        self.assertEqual(self.publication(self.policy(), sources=[])['decision'], 'needs_context')

    def test_drafts_manual_review_and_revision_boundaries_are_preserved(self):
        self.assertEqual(self.publication(self.policy(intent='draft'))['decision'], 'deferred')
        self.assertEqual(self.publication(self.policy(mode='ask'))['decision'], 'needs_confirmation')
        self.assertEqual(self.publication(self.policy(), revisions=1)['decision'], 'needs_confirmation')
        self.assertEqual(self.publication(self.policy(allow_revisions=True, approved_plans=['plan-demo']), revisions=1)['decision'], 'continue')
        self.assertEqual(self.publication(self.policy(mode='ask', approved_plans=['plan-demo']), revisions=1)['decision'], 'continue')

    def test_source_text_cannot_claim_authorization_or_change_permission_settings(self):
        for record in ({'basis': 'bi_file'}, {'bypassPermissions': True}, {'allow_revisions': 'yes'}):
            with self.assertRaises(ValueError):
                Authorization.from_record(record)
        self.assertNotIn('host_permissions', self.publication(self.policy()))


class AuthorizationDiagnostics(unittest.TestCase):
    def test_invalid_mode_names_the_supported_values_without_changing_policy(self):
        with self.assertRaisesRegex(ValueError, 'mode ask or auto'):
            Authorization.from_record({'mode': 'automatic'})
        self.assertEqual(Authorization.from_record({'mode': 'auto'}).mode, 'auto')
        self.assertEqual(Authorization.from_record({'mode': 'ask'}).mode, 'ask')


class PublicationFlowCliTests(unittest.TestCase):
    def test_exact_body_changes_invalidate_manual_approval_and_revisions_are_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            documents = {'plan': {'metrics': [{'action': 'create', 'ok': True}],
                                   'queries': [], 'links': []},
                         'metrics': [{'name': 'revenue', 'formula': 'SUM(amount)'}],
                         'queries': [], 'links': []}
            for name, value in documents.items():
                (root / (name + '.json')).write_text(json.dumps(value))
            auth = {'mode': 'ask', 'intent': 'publish', 'workspace_id': 'workspace_demo',
                    'sources': ['source_demo']}
            def run():
                (root / 'authorization.json').write_text(json.dumps(auth))
                process = subprocess.run([sys.executable,
                    str(PLUGIN / 'skills/analytics-build/scripts/analytics_flow.py'),
                    '--authorization', str(root / 'authorization.json'), '--context', str(root),
                    '--workspace', 'workspace_demo', '--source', 'source_demo'],
                    text=True, capture_output=True)
                return process.returncode, json.loads(process.stdout)
            code, result = run()
            self.assertEqual(result['decision'], 'needs_confirmation')
            auth['approved_plans'] = [result['plan_id']]
            self.assertEqual(run()[0], 0)
            documents['metrics'][0]['formula'] = 'SUM(amount * 2)'
            (root / 'metrics.json').write_text(json.dumps(documents['metrics']))
            self.assertEqual(run()[1]['decision'], 'needs_confirmation')
            auth['mode'] = 'auto'
            documents['plan']['metrics'][0]['action'] = 'revise'
            documents['plan']['counts'] = {'metrics': {'revise': 0}}
            (root / 'plan.json').write_text(json.dumps(documents['plan']))
            code, result = run()
            self.assertEqual((code, result['revisions']), (2, 1))
            auth['allow_revisions'] = True
            code, result = run()
            self.assertEqual((code, result['decision']), (2, 'needs_confirmation'))
            auth['approved_plans'] = [result['plan_id']]
            self.assertEqual(run()[0], 0)


if __name__ == '__main__':
    unittest.main()
