"""Focus continuity, truthful state, and inert workbench output."""
import copy
import unittest
import subprocess
import sys
import json
import test_analytics_experience as fixtures
import experience


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalyticsExperience()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_exact_focus_survives_reordering_and_release(self):
        f = self.fixture
        original = f.candidate()
        other = copy.deepcopy(original)
        other.update(name='other', display_name='Other', source_id='other-source')
        f.write('context/metrics.json', [other, original])
        before = f.files()
        model = experience.snapshot(f.run, focus='metric-source')
        self.assertEqual(model['sample']['name'], 'Shipped units')
        self.assertEqual(model['focus'], 'metric:shipped-units')
        self.assertEqual(f.files(), before)
        f.receipt(original)
        f.write('context/metrics.json', [original])
        model = experience.snapshot(f.run, focus=model['focus'])
        self.assertEqual(model['sample']['status'], 'released')
        self.assertIn('Shipped units', model['next_prompt'])
        self.assertIn('Retrieve', model['next_prompt'])

    def test_cli_retains_requested_identity(self):
        f = self.fixture
        body = f.candidate()
        other = copy.deepcopy(body)
        other.update(name='other', source_id='other-source')
        f.write('context/metrics.json', [other, body])
        command = [sys.executable, str(fixtures.SCRIPTS / 'project.py'), '--run-dir', str(f.run.root),
                   'experience', '--format', 'json', '--focus', 'metric-source']
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        model = json.loads(result.stdout)
        self.assertEqual(model['focus'], 'metric:shipped-units')
        self.assertEqual(model['sample']['name'], 'Shipped units')

    def test_missing_or_ambiguous_focus_never_switches_silently(self):
        f = self.fixture
        original = f.candidate()
        model = experience.snapshot(f.run, focus='gone')
        self.assertIsNone(model['sample'])
        self.assertTrue(model['warnings'])
        self.assertNotIn('id="focus-metric"', experience.render_html(model))
        other = copy.deepcopy(original)
        other['name'] = 'duplicate-source'
        f.write('context/metrics.json', [original, other])
        self.assertIsNone(experience.snapshot(f.run, focus='metric-source')['sample'])
        self.assertIsNotNone(experience.snapshot(f.run, focus='metric:shipped-units')['sample'])

    def test_focus_keeps_changed_revision_and_hold_visible(self):
        f = self.fixture
        body = f.candidate()
        first = experience.snapshot(f.run, focus='metric-source')
        body['definition']['formula'] = 'SUM(s.other_units)'
        f.write('context/metrics.json', [body])
        second = experience.snapshot(f.run, focus=first['focus'])
        self.assertNotEqual(first['sample']['definition_hash'], second['sample']['definition_hash'])
        f.write('auth-recovery.json', {'status': 'permission_denied'})
        model = experience.snapshot(f.run, focus=first['focus'])
        self.assertEqual(model['phase']['status'], 'access_needed')
        self.assertNotIn('suggest a supported question', model['next_prompt'])

    def test_focus_render_escapes_untrusted_content_and_preserves_evidence(self):
        f = self.fixture
        body = f.candidate()
        body['description'] = '<script>alert(1)</script>'
        f.write('context/metrics.json', [body])
        before = f.files()
        page = experience.render_html(experience.snapshot(f.run))
        self.assertNotIn('<script>alert(1)</script>', page)
        for value in ('&lt;script&gt;', 'Includes:', 'Snapshot at', 'does not update itself',
                      'Browse saved definitions', 'Current journey stage', 'Definition revision:',
                      'Copy next prompt', 'Content-Security-Policy'):
            self.assertIn(value, page)
        self.assertNotIn('fetch(', page)
        self.assertNotIn('>Publish</button>', page)
        self.assertNotIn('>Pause</button>', page)
        self.assertEqual(before, f.files())


if __name__ == '__main__':
    unittest.main()
