"""Unit tests for scripts/: run with `python3 -m unittest discover -s tests`."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build  # noqa: E402
import lint  # noqa: E402
import skill_index  # noqa: E402
import sync_upstream  # noqa: E402


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = build.load()

    def test_every_manifest_carries_the_same_name_and_version(self) -> None:
        name, version = self.source["plugin"]["name"], self.source["plugin"]["version"]
        documents = build.outputs(self.source)
        for path in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json"):
            self.assertEqual(documents[path]["name"], name, path)
            self.assertEqual(documents[path]["version"], version, path)
        for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"):
            self.assertEqual([p["name"] for p in documents[path]["plugins"]], [name], path)

    def test_both_mcp_files_point_at_one_url_with_each_tools_transport_label(self) -> None:
        documents = build.outputs(self.source)
        server, url = self.source["mcp"]["server"], self.source["mcp"]["url"]
        self.assertEqual(documents["mcp.json"]["mcpServers"][server], {"type": "streamable-http", "url": url})
        self.assertEqual(documents[".mcp.json"]["mcpServers"][server], {"type": "http", "url": url})

    def test_root_manifests_keep_to_the_closed_agent_plugins_schema(self) -> None:
        documents = build.outputs(self.source)
        allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
        self.assertLessEqual(set(documents["plugin.json"]), allowed)
        self.assertEqual(set(documents["mcp.json"]), {"$schema", "mcpServers"})

    def test_render_is_deterministic(self) -> None:
        first = {k: build.render(v) for k, v in build.outputs(self.source).items()}
        second = {k: build.render(v) for k, v in build.outputs(build.load()).items()}
        self.assertEqual(first, second)

    def test_committed_files_match_the_source(self) -> None:
        self.assertEqual(build.main(["--check"]), 0)

    def test_refuses_bad_names_reserved_prefixes_and_plain_http(self) -> None:
        for field, value in (("name", "Atlan"), ("name", "a--b"), ("name", "claude-atlan"), ("version", "1.0")):
            broken = copy.deepcopy(self.source)
            broken["plugin"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(SystemExit):
                build.validate(broken)
        broken = copy.deepcopy(self.source)
        broken["mcp"]["url"] = "http://api.atlan.com/mcp"
        with self.assertRaises(SystemExit):
            build.validate(broken)


class FrontmatterTest(unittest.TestCase):
    def test_parses_fields_and_body(self) -> None:
        fields, body = lint.frontmatter("---\nname: x\ndescription: Use when y.\n---\n\n# X\n")
        self.assertEqual(fields, {"name": "x", "description": "Use when y."})
        self.assertIn("# X", body)

    def test_rejects_unterminated_or_missing_blocks(self) -> None:
        self.assertIsNone(lint.frontmatter("---\nname: x\n"))
        self.assertIsNone(lint.frontmatter("# no frontmatter\n"))


class SkillLintTest(unittest.TestCase):
    def write_skill(self, root: Path, directory: str, text: str) -> Path:
        path = root / "skills" / directory
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(text, encoding="utf-8")
        return path

    def lint(self, directory: str, text: str, owned: bool = True) -> list[str]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved, lint.ROOT = lint.ROOT, root
            try:
                path = self.write_skill(root, directory, text)
                return lint.lint_skill(path, owned, {directory, "atlan-known"})
            finally:
                lint.ROOT = saved

    def test_accepts_a_well_formed_skill(self) -> None:
        text = "---\nname: atlan-x\ndescription: Use when testing.\n---\n\nSee `atlan-known`.\n"
        self.assertEqual(self.lint("atlan-x", text), [])

    def test_name_must_match_directory(self) -> None:
        errors = self.lint("atlan-x", "---\nname: atlan-y\ndescription: d\n---\nbody\n")
        self.assertTrue(any("differs from its directory" in e for e in errors), errors)

    def test_description_is_required_bounded_and_tag_free(self) -> None:
        self.assertTrue(self.lint("a", "---\nname: a\n---\nbody\n"))
        self.assertTrue(self.lint("a", f"---\nname: a\ndescription: {'x' * 1025}\n---\nbody\n"))
        self.assertTrue(self.lint("a", "---\nname: a\ndescription: Use <b>now</b>\n---\nbody\n"))

    def test_owned_skills_may_not_cite_missing_files_or_skills(self) -> None:
        text = "---\nname: a\ndescription: d\n---\nRead `references/gone.md` and `atlan-missing`.\n"
        errors = self.lint("a", text)
        self.assertTrue(any("cites missing references/gone.md" in e for e in errors), errors)
        self.assertTrue(any("cites unknown skill atlan-missing" in e for e in errors), errors)
        self.assertEqual(self.lint("a", text, owned=False), [])

    def test_secret_patterns_match_real_shapes(self) -> None:
        samples = {
            "GitHub token": "ghp_" + "a" * 36,
            "AWS access key": "AKIA" + "A" * 16,
            "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
        }
        patterns = dict(lint.SECRETS)
        for label, sample in samples.items():
            self.assertRegex(sample, patterns[label])

    def test_repository_content_passes(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lint.main(), 0)


class DashboardPromptTest(unittest.TestCase):
    """The Atlan dashboard's Open in Claude buttons send these; each must reach a skill."""

    # Claude Code gets a command, `/<plugin>:<command> <handle>`; chat gets a sentence naming the skill.
    COMMANDS = {"continue": "atlan-continue", "use-skill": "atlan-use-skill", "get-started": "atlan-get-started"}
    CHAT = {
        "Use the Atlan continue skill on artifact out_123": ("atlan-continue", "out_"),
        "Use the Atlan use-skill skill to load skill_abc and run it": ("atlan-use-skill", "skill_"),
    }

    def test_the_command_prefix_is_the_plugin_name(self) -> None:
        self.assertEqual(build.load()["plugin"]["name"], "atlan")

    def test_each_command_follows_its_skill(self) -> None:
        for command, skill in self.COMMANDS.items():
            with self.subTest(command=command):
                text = (ROOT / "commands" / f"{command}.md").read_text(encoding="utf-8")
                self.assertIn(f"`skills/{skill}/SKILL.md`", text)
                self.assertTrue((ROOT / "skills" / skill / "SKILL.md").is_file())

    def test_each_chat_sentence_names_a_skill_whose_description_matches_it(self) -> None:
        for sentence, (skill, handle) in self.CHAT.items():
            with self.subTest(sentence=sentence):
                self.assertIn(skill.removeprefix("atlan-"), sentence)
                parsed = lint.frontmatter((ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8"))
                self.assertIsNotNone(parsed)
                self.assertIn(handle, parsed[0]["description"])


class SkillIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index, self.served = skill_index.payloads()

    def test_lists_every_skill_under_the_v0_2_schema(self) -> None:
        self.assertEqual(self.index["$schema"], skill_index.SCHEMA)
        names = [entry["name"] for entry in self.index["skills"]]
        self.assertEqual(names, sorted(p.name for p in (ROOT / "skills").iterdir() if p.is_dir()))

    def test_every_digest_is_the_sha256_of_what_is_served(self) -> None:
        for entry in self.index["skills"]:
            data = self.served[entry["url"]]
            self.assertEqual(entry["digest"], "sha256:" + hashlib.sha256(data).hexdigest(), entry["name"])

    def test_archives_hold_skill_md_at_their_root_and_nothing_outside_the_skill(self) -> None:
        archives = [e for e in self.index["skills"] if e["type"] == "archive"]
        self.assertTrue(archives)
        for entry in archives:
            with tarfile.open(fileobj=io.BytesIO(self.served[entry["url"]])) as tar:
                names = tar.getnames()
            self.assertIn("SKILL.md", names, entry["name"])
            files = sorted(p.as_posix() for p in skill_index.skill_files(ROOT / "skills" / entry["name"]))
            self.assertEqual(sorted(names), files, entry["name"])

    def test_output_is_byte_for_byte_reproducible(self) -> None:
        again_index, again_served = skill_index.payloads()
        self.assertEqual(self.index, again_index)
        self.assertEqual(self.served, again_served)


class SyncTest(unittest.TestCase):
    def test_check_passes_on_the_committed_tree(self) -> None:
        self.assertEqual(sync_upstream.check(sync_upstream.load()), [])

    def test_check_reports_an_edited_vendored_file(self) -> None:
        manifest = sync_upstream.load()
        path = next(iter(manifest["files"]))
        manifest["files"][path] = dict(manifest["files"][path], sha256="0" * 64)
        problems = sync_upstream.check(manifest)
        self.assertEqual(len(problems), 1)
        self.assertIn(path, problems[0])

    def test_sync_copies_a_pinned_revision_and_drops_files_upstream_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            upstream, plugin = temp_path / "upstream", temp_path / "plugin"
            (upstream / "app/defaults/skills/atlan-z").mkdir(parents=True)
            (upstream / "app/defaults/skills/atlan-z/SKILL.md").write_text("one\n")
            (upstream / "app/defaults/other.md").write_text("ignored\n")
            git = ["git", "-C", str(upstream), "-c", "user.name=t", "-c", "user.email=t@example.com"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "one"], check=True)
            plugin.mkdir()
            (plugin / "commands").mkdir()
            (plugin / "commands/gone.md").write_text("old\n")

            manifest = {
                "sources": [{"name": "app", "repository": "x", "rev": "HEAD", "path": "app/defaults"}],
                "files": {"commands/gone.md": {"source": "app", "sha256": "x"}},
            }
            saved = sync_upstream.ROOT, sync_upstream.MANIFEST
            sync_upstream.ROOT, sync_upstream.MANIFEST = plugin, plugin / "upstream.json"
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    sync_upstream.sync(manifest, {"app": upstream}, adopt_head=True)
                written = json.loads((plugin / "upstream.json").read_text())
            finally:
                sync_upstream.ROOT, sync_upstream.MANIFEST = saved

            self.assertEqual((plugin / "skills/atlan-z/SKILL.md").read_text(), "one\n")
            self.assertFalse((plugin / "commands/gone.md").exists())
            self.assertFalse((plugin / "other.md").exists())
            self.assertEqual(list(written["files"]), ["skills/atlan-z/SKILL.md"])
            self.assertRegex(written["sources"][0]["rev"], r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
