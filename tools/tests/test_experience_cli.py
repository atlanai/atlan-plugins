"""Presentation commands cannot advance or invalidate an analytics run."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_local_project as fixtures
from context_state import ContextState


class ExperienceCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = ContextState(Path(self.temp.name).resolve() / 'run')
        self.run.write(self.run.path, {'entrypoint': 'project', 'sources': [
            {'path': 'report.bim', 'kind': 'powerbi', 'status': 'available'}]})
        self.run.write(self.run.root / 'progress.json', {'stage': 'preview', 'status': 'running'})
        self.run.write(self.run.context / 'metrics.json', [{
            'name': 'shipped-units', 'display_name': 'Shipped units',
            'description': 'Units on shipped orders.', 'definition': {
                'formula': 'SUM(s.units)', 'mandatory_filters': ["s.status = 'shipped'"],
                'examples': [{'sql': "SELECT SUM(units) FROM shipping.shipments WHERE status = 'shipped'"}],
                'extensions': {'atlan_analytics': {'declared': {'name': 'Shipped units', 'ref': 'report.bim'}}}}}])

    def files(self):
        return {p.relative_to(self.run.root).as_posix(): p.read_bytes()
                for p in self.run.root.rglob('*') if p.is_file()}

    def command(self, *args, run=None):
        return subprocess.run([sys.executable, '-I', str(fixtures.SCRIPTS / 'project.py'),
                               '--run-dir', str(run or self.run.root), 'experience', *args],
                              capture_output=True, text=True)

    def test_chat_and_details_are_visible_without_changing_evidence(self):
        before = self.files()
        compact = self.command()
        detail = self.command('--detail')
        self.assertEqual(compact.returncode, 0, compact.stderr)
        self.assertEqual(detail.returncode, 0, detail.stderr)
        self.assertIn('Shipped units', compact.stdout)
        self.assertIn('SUM(s.units)', compact.stdout)
        self.assertIn("WHERE status = 'shipped'", detail.stdout)
        self.assertNotIn('result_file', compact.stdout)
        self.assertNotRegex(compact.stdout, r'(?m)^(DONE|DONE_WITH_CONCERNS|BLOCKED|NEEDS_CONTEXT)\b')
        self.assertEqual(self.files(), before)

    def test_snapshot_only_writes_its_presentation_artifact(self):
        before = self.files()
        response = self.command('--format', 'html')
        self.assertEqual(response.returncode, 0, response.stderr)
        result = json.loads(response.stdout)
        self.assertEqual(result['snapshot'], str(self.run.root / 'experience.html'))
        self.assertIn('Shipped units', result['chat'])
        after = self.files()
        self.assertEqual(set(after) - set(before), {'experience.html'})
        self.assertEqual({p: after[p] for p in before}, before)

    def test_explicit_host_view_preserves_other_host_recovery(self):
        self.run.write(self.run.root / 'auth-recovery.json', {
            'host': 'claude_code', 'server': 'plugin:atlanai:atlan', 'status': 'login_required', 'started_at': 1000})
        self.run.write(self.run.root / 'registry-readiness.json', {'schema_version': 1, 'observations': [
            {'host': 'codex', 'server': 'atlan', 'status': 'connected', 'read_succeeded': True,
             'read_tool': 'registry__list_workspaces', 'observed_at': 2000}]})
        before = self.files()
        codex = self.command('--host', 'codex', '--server', 'atlan')
        claude = self.command('--host', 'claude_code', '--server', 'plugin:atlanai:atlan')
        self.assertEqual(codex.returncode, 0, codex.stderr)
        self.assertEqual(claude.returncode, 0, claude.stderr)
        self.assertNotIn('Restore the selected Atlan connection', codex.stdout)
        self.assertIn('Restore the selected Atlan connection', claude.stdout)
        invalid = subprocess.run([sys.executable, '-I', str(fixtures.SCRIPTS / 'project.py'),
                                  '--run-dir', str(self.run.root), 'journey', 'resume', '--host', 'codex'],
                                 capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(self.files(), before)
        status = subprocess.run([sys.executable, '-I', str(fixtures.SCRIPTS / 'project.py'),
                                 '--run-dir', str(self.run.root), 'journey', 'status',
                                 '--host', 'codex', '--server', 'atlan'], capture_output=True, text=True)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertNotIn('Restore the selected Atlan connection', json.loads(status.stdout)['chat'])
        self.assertEqual(self.files(), before)

    def test_invalid_optional_engine_does_not_mark_the_pipeline_failed(self):
        before = self.files()
        outside = self.run.root.parent / 'other-engine'
        outside.mkdir()
        result = self.command('--engine-dir', str(outside))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'blocked')
        self.assertEqual(self.files(), before)

    def test_missing_run_does_not_create_an_empty_replacement(self):
        missing = self.run.root.parent / 'missing-run'
        result = self.command(run=missing)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())
        self.assertIn('existing directory', json.loads(result.stdout)['message'])


if __name__ == '__main__':
    unittest.main()
