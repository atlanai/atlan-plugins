"""Exercise the release workflow's tag formatter through Bash and Python, without tagging."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReleaseTagFormatting(unittest.TestCase):
    def format_tag(self, manifest):
        workflow = (ROOT / '.github/workflows/release-tags.yml').read_text(encoding='utf-8')
        commands = [line.strip() for line in workflow.splitlines() if line.strip().startswith('tag=')]
        self.assertEqual(len(commands), 1)
        # Execute the actual workflow expression: checking Python alone misses shell quoting.
        # No git command or remote operation is executed by this regression test.
        script = 'manifest="$1"\n' + commands[0] + '\nprintf "%s\\n" "$tag"\n'
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script, 'release-test', str(manifest)],
                                capture_output=True, text=True, timeout=10, check=True)
        return result.stdout.strip()

    def test_formats_every_shipped_plugin_version(self):
        manifests = sorted((ROOT / 'plugins').glob('*/.claude-plugin/plugin.json'))
        self.assertTrue(manifests)
        for manifest in manifests:
            with self.subTest(plugin=manifest.parent.parent.name):
                value = json.loads(manifest.read_text(encoding='utf-8'))
                self.assertEqual(self.format_tag(manifest), value['name'] + '--v' + value['version'])

    def test_quotes_manifest_paths_and_preserves_prerelease_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest with spaces.json'
            manifest.write_text(json.dumps({'name': 'example-plugin', 'version': '1.2.3-rc.1'}), encoding='utf-8')
            self.assertEqual(self.format_tag(manifest), 'example-plugin--v1.2.3-rc.1')


if __name__ == '__main__':
    unittest.main()
