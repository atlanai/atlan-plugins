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
        addons = [addon["name"] for addon in self.source.get("addons", [])]
        for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"):
            self.assertEqual([p["name"] for p in documents[path]["plugins"]], [name, *addons], path)

    def test_both_mcp_files_point_at_one_url_with_each_tools_transport_label(self) -> None:
        documents = build.outputs(self.source)
        server, url = self.source["mcp"]["server"], self.source["mcp"]["url"]
        headers = {"X-Atlan-Client": f"atlan-plugin/{self.source['plugin']['version']}"}
        self.assertEqual(documents["mcp.json"]["mcpServers"][server], {"type": "streamable-http", "url": url, "headers": headers})
        self.assertEqual(documents[".mcp.json"]["mcpServers"][server], {"type": "http", "url": url, "headers": headers})

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
        for field, value in (("url", "http://api.atlan.com/mcp"), ("client", "Atlan Plugin")):
            broken = copy.deepcopy(self.source)
            broken["mcp"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(SystemExit):
                build.validate(broken)


class VariantTest(unittest.TestCase):
    """`build.py --variant`: this plugin under another name and endpoint, for another repository."""

    OVERRIDES = {
        "plugin": {"name": "atlan-staging", "display_name": "Atlan (staging)", "repository": "https://github.com/example/atlan-staging"},
        "marketplace": {"name": "atlan-staging-plugins"},
        "mcp": {"url": "https://api.staging.example/mcp", "client": "atlan-plugin-staging"},
        "rewrite": {"https://api.atlan.com": "https://api.staging.example", "https://app.atlan.com": "https://app.staging.example"},
        "keep": ["https://app.atlan.com/brain/cli/install.sh"],
    }

    def setUp(self) -> None:
        self.source = build.load()
        self.variant = build.make_variant(self.source, copy.deepcopy(self.OVERRIDES))
        self.files = build.variant_files(self.source, self.variant, "variant.toml")

    def test_its_manifests_and_marketplaces_carry_its_names_and_url(self) -> None:
        for path in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json"):
            self.assertEqual(json.loads(self.files[path])["name"], "atlan-staging", path)
        for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"):
            document = json.loads(self.files[path])
            self.assertEqual(document["name"], "atlan-staging-plugins", path)
            self.assertEqual([p["name"] for p in document["plugins"]], ["atlan-staging", "atlan-staging-analytics"], path)
        server, version = self.source["mcp"]["server"], self.source["plugin"]["version"]
        for path in ("mcp.json", ".mcp.json"):
            entry = json.loads(self.files[path])["mcpServers"][server]
            self.assertEqual(entry["url"], "https://api.staging.example/mcp", path)
            self.assertEqual(entry["headers"], {"X-Atlan-Client": f"atlan-plugin-staging/{version}"}, path)

    def test_it_copies_every_skill_and_command_and_rewrites_their_names(self) -> None:
        main = self.source["plugin"]["name"]
        for top in build.COPIED:
            for path in (ROOT / top).rglob("*"):
                if path.is_file():
                    self.assertIn(path.relative_to(ROOT).as_posix(), self.files)
        for path, text in self.files.items():
            if path.endswith(".md"):
                self.assertNotIn(f"/{main}:", text, path)
                self.assertNotIn(f"plugin:{main}:", text, path)
                self.assertNotIn(self.source["mcp"]["url"], text, path)
        install = self.files["skills/atlan-get-started/references/install-and-sign-in.md"]
        self.assertIn("/plugin install atlan-staging@atlan-staging-plugins", install)
        self.assertIn("/plugin marketplace add example/atlan-staging\n", install)

    def test_pairs_apply_once_longest_first_and_kept_strings_stay(self) -> None:
        pairs = build.rewrites(self.source, self.variant)
        text = "See https://github.com/atlanai/atlan-plugin, run https://app.atlan.com/brain/cli/install.sh, open https://app.atlan.com."
        self.assertEqual(
            build.rewrite(text, pairs),
            "See https://github.com/example/atlan-staging, run https://app.atlan.com/brain/cli/install.sh, open https://app.staging.example.",
        )

    def test_writes_into_another_folder_and_removes_stale_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "skills/atlan-gone").mkdir(parents=True)
            (out / "skills/atlan-gone/SKILL.md").write_text("old\n")
            (out / "variant.toml").write_text("kept\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build.sync(self.files, out, check=False, owned=build.COPIED), [])
                self.assertEqual(build.sync(self.files, out, check=True, owned=build.COPIED), [])
            self.assertFalse((out / "skills/atlan-gone/SKILL.md").exists())
            self.assertEqual((out / "variant.toml").read_text(), "kept\n")
            self.assertTrue((out / "README.md").is_file())

    def test_refuses_a_variant_that_reuses_a_name_or_sets_unknown_fields(self) -> None:
        for table, field, value in (
            ("plugin", "name", "atlan"),
            ("marketplace", "name", "atlan-plugins"),
            ("plugin", "version", "9.9.9"),
            ("mcp", "url", "http://plain.example/mcp"),
        ):
            overrides = copy.deepcopy(self.OVERRIDES)
            overrides[table][field] = value
            with self.subTest(field=f"{table}.{field}"), self.assertRaises(SystemExit):
                build.make_variant(self.source, overrides)


class AddonTest(unittest.TestCase):
    """`[[addons]]`: workloads shipped as their own plugins that use the core's connection."""

    def setUp(self) -> None:
        self.source = build.load()
        self.addon = self.source["addons"][0]
        self.documents = build.outputs(self.source)
        self.base = f"{build.ADDONS}/{self.addon['name']}"

    def test_each_addon_depends_on_the_core_and_declares_no_mcp_server(self) -> None:
        claude = self.documents[f"{self.base}/.claude-plugin/plugin.json"]
        self.assertEqual(claude["dependencies"], [self.source["plugin"]["name"]])
        for path in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json"):
            document = self.documents[f"{self.base}/{path}"]
            self.assertEqual((document["name"], document["version"]), (self.addon["name"], self.addon["version"]), path)
            self.assertNotIn("mcpServers", document, path)
        self.assertNotIn(f"{self.base}/.mcp.json", self.documents)
        self.assertNotIn(f"{self.base}/mcp.json", self.documents)

    def test_its_agent_plugins_manifest_keeps_to_the_closed_schema(self) -> None:
        allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
        self.assertLessEqual(set(self.documents[f"{self.base}/plugin.json"]), allowed)

    def test_every_marketplace_entry_points_at_the_addon_folder(self) -> None:
        claude = self.documents[".claude-plugin/marketplace.json"]["plugins"][1]
        cursor = self.documents[".cursor-plugin/marketplace.json"]["plugins"][1]
        codex = self.documents[".agents/plugins/marketplace.json"]["plugins"][1]
        self.assertEqual(claude["source"], f"./{self.base}")
        self.assertEqual(cursor["source"], f"./{self.base}")
        self.assertEqual(codex["source"]["source"], "git-subdir")
        self.assertEqual(codex["source"]["path"], f"./{self.base}")
        self.assertTrue((ROOT / self.base / "skills").is_dir())

    def test_refuses_an_addon_without_the_core_prefix_or_declared_twice(self) -> None:
        broken = copy.deepcopy(self.source)
        broken["addons"][0]["name"] = "analytics"
        with self.assertRaises(SystemExit):
            build.validate(broken)
        broken = copy.deepcopy(self.source)
        broken["addons"].append(copy.deepcopy(broken["addons"][0]))
        with self.assertRaises(SystemExit):
            build.validate(broken)

    def test_a_variant_renames_each_addon_and_its_dependency_and_copies_its_files(self) -> None:
        variant = build.make_variant(self.source, copy.deepcopy(VariantTest.OVERRIDES))
        files = build.variant_files(self.source, variant, "variant.toml")
        base = f"{build.ADDONS}/atlan-staging-analytics"
        claude = json.loads(files[f"{base}/.claude-plugin/plugin.json"])
        self.assertEqual(claude["dependencies"], ["atlan-staging"])
        self.assertTrue(json.loads(files[f"{base}/plugin.json"])["extensions"]["com.openai"]["interface"]["displayName"].startswith("Atlan (staging)"))
        self.assertIn(f"{base}/skills/atlan-analytics/SKILL.md", files)
        self.assertIn(f"{base}/assets/logo.svg", files)
        self.assertFalse(any(path.startswith(f"{self.base}/") for path in files))


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

    def test_an_addon_may_not_carry_its_own_server_or_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            addon = Path(temp) / "atlan-x"
            (addon / ".claude-plugin").mkdir(parents=True)
            (addon / ".claude-plugin/plugin.json").write_text('{"name": "atlan-x", "mcpServers": "./.mcp.json"}')
            (addon / ".mcp.json").write_text("{}")
            saved = lint.ROOT
            lint.ROOT = Path(temp)
            try:
                errors = lint.lint_addon(addon)
            finally:
                lint.ROOT = saved
        self.assertEqual(len(errors), 2)

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

    def test_sync_copies_into_a_folder_and_leaves_excluded_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            upstream, plugin = temp_path / "upstream", temp_path / "plugin"
            (upstream / "app/defaults/skills/atlan-y").mkdir(parents=True)
            (upstream / "app/defaults/skills/atlan-y/SKILL.md").write_text("two\n")
            (upstream / "app/defaults/commands").mkdir()
            (upstream / "app/defaults/commands/rendered.md").write_text("partial\n")
            git = ["git", "-C", str(upstream), "-c", "user.name=t", "-c", "user.email=t@example.com"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "one"], check=True)
            plugin.mkdir()
            manifest = {
                "sources": [{"name": "app", "repository": "x", "rev": "HEAD", "path": "app/defaults",
                             "into": "plugins/atlan-y", "exclude": ["commands/rendered.md"]}],
                "files": {},
            }
            saved = sync_upstream.ROOT, sync_upstream.MANIFEST
            sync_upstream.ROOT, sync_upstream.MANIFEST = plugin, plugin / "upstream.json"
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    sync_upstream.sync(manifest, {"app": upstream}, adopt_head=False)
                written = json.loads((plugin / "upstream.json").read_text())
            finally:
                sync_upstream.ROOT, sync_upstream.MANIFEST = saved

            self.assertEqual(list(written["files"]), ["plugins/atlan-y/skills/atlan-y/SKILL.md"])
            self.assertFalse((plugin / "plugins/atlan-y/commands/rendered.md").exists())


if __name__ == "__main__":
    unittest.main()
