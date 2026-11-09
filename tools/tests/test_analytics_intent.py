"""Business focus and purpose are sequential, durable native-chat decisions."""
import json
import subprocess
import sys
import unittest

import test_analytics_journey as fixtures
from project import Project


class IntentCheckpoint(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConversationChoices()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.run = self.fixture.run
        self.fixture.fixture.write('progress.json', {'stage': 'scope', 'status': 'complete'})

    def test_clear_scope_still_asks_domain_then_tailored_purpose_after_restart(self):
        before = self.fixture.fixture.files()
        first = self.run.journey()
        self.assertEqual(first['continuation']['action'], 'choose-domain')
        self.assertEqual(first['continuation']['question']['options_from'], 'observed_sources')
        self.assertTrue(first['continuation']['question']['wait_for_answer'])
        self.assertTrue(first['continuation']['question']['allow_free_text'])
        self.assertEqual(before, self.fixture.fixture.files())
        self.run.journey('domain', answer='Shipping time and delays')
        restarted = Project(self.run.root)
        second = restarted.journey()
        self.assertEqual(second['continuation']['action'], 'choose-purpose')
        self.assertEqual(second['continuation']['question']['domain'], 'Shipping time and delays')
        self.assertEqual(second['continuation']['question']['options_from'], 'selected_domain')
        self.assertIn('Shipping time and delays', second['chat'])
        final = restarted.journey('purpose', answer='Find where delays happen')
        self.assertEqual(final['continuation']['action'], 'select-focus')
        self.assertFalse(final['continuation']['needs_input'])
        self.assertIsNone(restarted.experience(format='json')['sample'])
        self.assertEqual(restarted.journey('select', focus='metric-source')['continuation']['action'], 'build')
        self.assertEqual(Project(self.run.root).journey()['interaction']['intent'], {
            'domain': 'Shipping time and delays', 'purpose': 'Find where delays happen'})
        after = self.fixture.fixture.files()
        self.assertEqual(set(after) - set(before), {'interaction.json'})
        self.assertEqual({p: after[p] for p in before}, before)

    def test_changed_domain_clears_old_purpose_but_same_domain_does_not(self):
        self.run.journey('domain', answer='Shipping time')
        self.run.journey('purpose', answer='Locate delays')
        self.run.journey('select', focus='metric-source')
        self.assertEqual(self.run.journey('domain', answer='Shipping time')['continuation']['action'], 'build')
        result = self.run.journey('domain', answer='Capacity utilization')
        self.assertEqual(result['continuation']['action'], 'choose-purpose')
        self.assertNotIn('purpose', result['interaction']['intent'])
        self.assertIsNone(self.run.experience(format='json')['sample'])
        self.assertTrue(result['interaction']['selection_pending'])
        self.assertNotIn('publication', self.run.current())

    def test_pause_local_details_and_resume_preserve_partial_answers(self):
        self.run.journey('domain', answer='Capacity')
        self.run.journey('pause')
        self.assertEqual(self.run.journey()['continuation']['action'], 'resume')
        self.assertNotIn('What would you like', self.run.journey('details')['chat'])
        self.assertEqual(self.run.journey('resume')['continuation']['action'], 'choose-purpose')
        self.run.journey('keep-local')
        self.assertEqual(Project(self.run.root).journey()['interaction']['mode'], 'local')
        self.assertEqual(self.run.journey('resume')['continuation']['action'], 'choose-purpose')

    def test_local_intent_can_be_established_before_registry_recovery(self):
        self.fixture.fixture.write('auth-recovery.json', {'status': 'permission_denied'})
        self.assertEqual(self.run.journey()['continuation']['action'], 'choose-domain')
        self.run.journey('domain', answer='Capacity')
        result = self.run.journey('purpose', answer='Understand the capacity metric')
        self.assertEqual(result['continuation']['action'], 'select-focus')
        self.assertEqual(self.run.journey('select', focus='metric-source')['continuation']['action'], 'recover-access')
        self.assertEqual(self.run.experience(format='json')['auth_status'], 'permission_denied')

    def test_invalid_or_out_of_order_answers_leave_saved_work_unchanged(self):
        before = self.fixture.fixture.files()
        for action, answer in [('purpose', 'Find delays'), ('domain', ''), ('domain', ' '),
                               ('domain', 'x' * 1001), ('domain', None), ('resume', 'Shipping')]:
            with self.subTest(action=action, answer_type=type(answer).__name__), self.assertRaises(ValueError):
                self.run.journey(action, answer=answer)
        self.assertEqual(before, self.fixture.fixture.files())
        self.fixture.fixture.write('interaction.json', {'version': 1, 'mode': 'active', 'intent': {'purpose': 'Missing domain'}})
        with self.assertRaises(ValueError):
            self.run.journey()

    def test_cli_records_free_text_and_returns_next_native_question(self):
        command = [sys.executable, '-I', str(fixtures.fixtures.SCRIPTS / 'project.py'),
                   '--run-dir', str(self.run.root), 'journey', 'domain', '--answer', 'Shipping time & late arrivals']
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data['continuation']['action'], 'choose-purpose')
        self.assertEqual(data['interaction']['intent']['domain'], 'Shipping time & late arrivals')
        self.assertFalse(data['executed'])
        self.assertFalse(data['remote_writes'])


if __name__ == '__main__':
    unittest.main()
