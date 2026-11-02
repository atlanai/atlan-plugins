"""Unit tests for tools/: run with `python3 -m unittest discover -s tools/tests`."""

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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build  # noqa: E402
import lint  # noqa: E402
import skill_index  # noqa: E402

CORE = ROOT / build.core_dir(build.load())


class BuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = build.load()

    def test_every_manifest_carries_the_same_name_and_version(self) -> None:
        name, version = self.source["plugin"]["name"], self.source["plugin"]["version"]
        documents = build.outputs(self.source)
        for path in ("plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json"):
            path = f"plugins/{name}/{path}"
            self.assertEqual(documents[path]["name"], name, path)
            self.assertEqual(documents[path]["version"], version, path)
        addons = [addon["name"] for addon in self.source.get("addons", [])]
        mods = [mod["name"] for mod in self.source.get("mods", [])]
        self.assertEqual([p["name"] for p in documents[".claude-plugin/marketplace.json"]["plugins"]], [name, *addons, *mods])
        for path in (".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"):
            self.assertEqual([p["name"] for p in documents[path]["plugins"]], [name, *addons], path)

    def test_both_mcp_files_point_at_one_url_with_each_tools_transport_label(self) -> None:
        documents = build.outputs(self.source)
        server, url = self.source["mcp"]["server"], self.source["mcp"]["url"]
        headers = {"X-Atlan-Client": f"atlan-plugin/{self.source['plugin']['version']}"}
        self.assertEqual(documents["plugins/atlanai/mcp.json"]["mcpServers"][server], {"type": "streamable-http", "url": url, "headers": headers})
        self.assertEqual(documents["plugins/atlanai/.mcp.json"]["mcpServers"][server], {"type": "http", "url": url, "headers": headers})

    def test_agent_plugins_manifests_keep_to_the_closed_schema(self) -> None:
        documents = build.outputs(self.source)
        allowed = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
        self.assertLessEqual(set(documents["plugins/atlanai/plugin.json"]), allowed)
        self.assertEqual(set(documents["plugins/atlanai/mcp.json"]), {"$schema", "mcpServers"})

    def test_the_root_is_the_marketplace_and_every_plugin_is_a_folder_under_plugins(self) -> None:
        documents = build.outputs(self.source)
        roots = {path for path in documents if not path.startswith("plugins/")}
        self.assertEqual(roots, {".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"})
        names = [self.source["plugin"]["name"], *(addon["name"] for addon in self.source["addons"])]
        claude = documents[".claude-plugin/marketplace.json"]["plugins"]
        cursor = documents[".cursor-plugin/marketplace.json"]["plugins"]
        codex = documents[".agents/plugins/marketplace.json"]["plugins"]
        for index, name in enumerate(names):
            self.assertEqual(claude[index]["source"], f"./plugins/{name}")
            self.assertEqual(cursor[index]["source"], f"./plugins/{name}")
            self.assertEqual(codex[index]["source"], {"source": "local", "path": f"./plugins/{name}"})

    def test_every_plugin_folder_carries_the_logo_license_and_a_readme(self) -> None:
        mods = [mod["name"] for mod in self.source.get("mods", [])]
        for name in [self.source["plugin"]["name"], *(addon["name"] for addon in self.source["addons"]), *mods]:
            for relative in ("assets/logo.svg", "LICENSE", "README.md"):
                self.assertTrue((ROOT / "plugins" / name / relative).is_file(), f"{name}/{relative}")

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
            path = f"plugins/atlan-staging/{path}"
            self.assertEqual(json.loads(self.files[path])["name"], "atlan-staging", path)
        for path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"):
            document = json.loads(self.files[path])
            self.assertEqual(document["name"], "atlan-staging-plugins", path)
            self.assertEqual([p["name"] for p in document["plugins"]], ["atlan-staging", "atlan-staging-analytics"], path)
        server, version = self.source["mcp"]["server"], self.source["plugin"]["version"]
        for path in ("plugins/atlan-staging/mcp.json", "plugins/atlan-staging/.mcp.json"):
            entry = json.loads(self.files[path])["mcpServers"][server]
            self.assertEqual(entry["url"], "https://api.staging.example/mcp", path)
            self.assertEqual(entry["headers"], {"X-Atlan-Client": f"atlan-plugin-staging/{version}"}, path)

    def test_it_copies_every_skill_and_command_and_rewrites_their_names(self) -> None:
        main = self.source["plugin"]["name"]
        for top in build.TREES:
            for path in (CORE / top).rglob("*"):
                if path.is_file():
                    self.assertIn(f"plugins/atlan-staging/{path.relative_to(CORE).as_posix()}", self.files)
        self.assertFalse(any(path.startswith(("plugins/atlanai/", "skills/", "commands/")) for path in self.files))
        for path, text in self.files.items():
            if path.endswith(".md"):
                self.assertNotIn(f"/{main}:", text, path)
                self.assertNotIn(f"plugin:{main}:", text, path)
                self.assertNotIn(f"`{main}:", text, path)
                self.assertNotIn(self.source["mcp"]["url"], text, path)
        self.assertIn("`atlan-staging:atlan-continue`", self.files["plugins/atlan-staging/commands/continue.md"])
        install = self.files["plugins/atlan-staging/skills/atlan-get-started/references/install-and-sign-in.md"]
        self.assertIn("/plugin install atlan-staging@atlan-staging-plugins", install)
        self.assertIn("`/plugin marketplace add example/atlan-staging`", install)
        self.assertIn("claude plugin install atlan-staging --marketplace example/atlan-staging\n", install)
        self.assertIn("(`atlan-staging` and\n`atlan-staging-analytics`)", install)

    def test_pairs_apply_once_longest_first_and_kept_strings_stay(self) -> None:
        pairs = build.rewrites(self.source, self.variant)
        text = "See https://github.com/atlanai/atlan-plugins, run https://app.atlan.com/brain/cli/install.sh, open https://app.atlan.com."
        self.assertEqual(
            build.rewrite(text, pairs),
            "See https://github.com/example/atlan-staging, run https://app.atlan.com/brain/cli/install.sh, open https://app.staging.example.",
        )

    def test_writes_into_another_folder_and_removes_stale_copies_and_the_old_root_layout(self) -> None:
        owned = (build.PLUGINS, *build.LEGACY)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "plugins/atlan-staging/skills/atlan-gone").mkdir(parents=True)
            (out / "plugins/atlan-staging/skills/atlan-gone/SKILL.md").write_text("old\n")
            (out / "skills/atlan-get-started").mkdir(parents=True)
            (out / "skills/atlan-get-started/SKILL.md").write_text("old root layout\n")
            (out / ".claude-plugin").mkdir()
            (out / ".claude-plugin/plugin.json").write_text("{}\n")
            (out / "mcp.json").write_text("{}\n")
            (out / "variant.toml").write_text("kept\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(build.sync(self.files, out, check=False, owned=owned), [])
                self.assertEqual(build.sync(self.files, out, check=True, owned=owned), [])
            for gone in ("plugins/atlan-staging/skills/atlan-gone/SKILL.md", "skills/atlan-get-started/SKILL.md", ".claude-plugin/plugin.json", "mcp.json"):
                self.assertFalse((out / gone).exists(), gone)
            self.assertEqual((out / "variant.toml").read_text(), "kept\n")
            self.assertTrue((out / "README.md").is_file())
            self.assertTrue((out / ".claude-plugin/marketplace.json").is_file())
            self.assertTrue((out / "plugins/atlan-staging/README.md").is_file())

    def test_refuses_a_variant_that_reuses_a_name_or_sets_unknown_fields(self) -> None:
        for table, field, value in (
            ("plugin", "name", "atlanai"),
            ("marketplace", "name", "atlan"),
            ("plugin", "version", "9.9.9"),
            ("mcp", "url", "http://plain.example/mcp"),
        ):
            overrides = copy.deepcopy(self.OVERRIDES)
            overrides[table][field] = value
            with self.subTest(field=f"{table}.{field}"), self.assertRaises(SystemExit):
                build.make_variant(self.source, overrides)

    def test_a_dev_variant_follows_the_gateway_suffix_convention(self) -> None:
        overrides = copy.deepcopy(self.OVERRIDES)
        overrides["plugin"]["name"] = "atlanai-dev"
        overrides["marketplace"]["name"] = "atlan-dev"
        overrides["mcp"]["url"] = "https://api.dev.example/mcp"
        variant = build.make_variant(self.source, overrides)
        files = build.variant_files(self.source, variant, "variant.toml")
        self.assertIn("plugins/atlanai-dev/skills/atlan-get-started/SKILL.md", files)
        self.assertIn("plugins/atlanai-dev-analytics/skills/atlan-analytics/SKILL.md", files)
        mcp = json.loads(files["plugins/atlanai-dev/.mcp.json"])
        self.assertEqual(mcp["mcpServers"]["atlan"]["url"], "https://api.dev.example/mcp")
        self.assertIn("claude plugin install atlanai-dev --marketplace example/atlan-staging\n", files["plugins/atlanai-dev/skills/atlan-get-started/references/install-and-sign-in.md"])


class AddonTest(unittest.TestCase):
    """`[[addons]]`: workloads shipped as their own plugins that use the core's connection."""

    def setUp(self) -> None:
        self.source = build.load()
        self.addon = self.source["addons"][0]
        self.documents = build.outputs(self.source)
        self.base = build.addon_dir(self.addon)

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
        self.assertEqual(codex["source"], {"source": "local", "path": f"./{self.base}"})
        self.assertTrue((ROOT / self.base / "skills").is_dir())

    def test_claude_and_codex_each_get_their_own_label_and_category(self) -> None:
        plugins = [self.source["plugin"], self.addon]
        claude = self.documents[".claude-plugin/marketplace.json"]["plugins"][: len(plugins)]
        codex = self.documents[".agents/plugins/marketplace.json"]["plugins"]
        self.assertEqual([p["displayName"] for p in claude], [p["display_name"] for p in plugins])
        self.assertEqual([p["category"] for p in claude], [p["claude_category"] for p in plugins])
        self.assertEqual([p["category"] for p in codex], [p["category"] for p in plugins])
        for base in (build.core_dir(self.source), self.base):
            self.assertIn("displayName", self.documents[f"{base}/.claude-plugin/plugin.json"], base)

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
        base = "plugins/atlan-staging-analytics"
        claude = json.loads(files[f"{base}/.claude-plugin/plugin.json"])
        self.assertEqual(claude["dependencies"], ["atlan-staging"])
        self.assertTrue(json.loads(files[f"{base}/plugin.json"])["extensions"]["com.openai"]["interface"]["displayName"].startswith("Atlan (staging)"))
        self.assertIn(f"{base}/skills/atlan-analytics/SKILL.md", files)
        self.assertIn(f"{base}/assets/logo.svg", files)
        self.assertFalse(any(path.startswith(f"{self.base}/") for path in files))


class ModTest(unittest.TestCase):
    """`[[mods]]`: Claude Code plugins whose hooks module acts on the core's tools."""

    def setUp(self) -> None:
        self.source = build.load()
        self.mod = self.source["mods"][0]
        self.documents = build.outputs(self.source)
        self.base = build.mod_dir(self.mod)

    def test_a_mod_has_one_claude_manifest_that_depends_on_the_core(self) -> None:
        manifest = self.documents[f"{self.base}/.claude-plugin/plugin.json"]
        self.assertEqual((manifest["name"], manifest["version"]), (self.mod["name"], self.mod["version"]))
        self.assertEqual(manifest["dependencies"], [self.source["plugin"]["name"]])
        self.assertNotIn("mcpServers", manifest)
        self.assertEqual(manifest["userConfig"], self.mod["user_config"])
        for path in ("plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json", ".mcp.json"):
            self.assertNotIn(f"{self.base}/{path}", self.documents, path)

    def test_only_the_claude_marketplace_lists_a_mod(self) -> None:
        claude = [p["name"] for p in self.documents[".claude-plugin/marketplace.json"]["plugins"]]
        self.assertIn(self.mod["name"], claude)
        for path in (".agents/plugins/marketplace.json", ".cursor-plugin/marketplace.json"):
            self.assertNotIn(self.mod["name"], [p["name"] for p in self.documents[path]["plugins"]], path)

    def test_a_variant_carries_no_mod(self) -> None:
        variant = build.make_variant(self.source, copy.deepcopy(VariantTest.OVERRIDES))
        files = build.variant_files(self.source, variant, "variant.toml")
        self.assertFalse(any("-mods" in path for path in files))
        self.assertNotIn("-mods", files[".claude-plugin/marketplace.json"])

    def test_refuses_a_mod_without_the_core_prefix_or_named_like_an_addon(self) -> None:
        broken = copy.deepcopy(self.source)
        broken["mods"][0]["name"] = "mods"
        with self.assertRaises(SystemExit):
            build.validate(broken)
        broken = copy.deepcopy(self.source)
        broken["mods"][0]["name"] = broken["addons"][0]["name"]
        with self.assertRaises(SystemExit):
            build.validate(broken)

    def test_a_mod_may_hold_one_hooks_module_but_no_settings_hooks_or_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            mod = Path(temp) / "atlan-m"
            (mod / "hooks").mkdir(parents=True)
            (mod / "hooks/register.ts").write_text("export const register = () => {}\n")
            (mod / "hooks/hooks.json").write_text('{"modules": ["./register.ts"]}')
            saved = lint.ROOT
            lint.ROOT = Path(temp)
            try:
                self.assertEqual(lint.lint_mod(mod), [])
                (mod / "hooks/hooks.json").write_text('{"modules": ["./register.ts"], "hooks": {"Stop": []}}')
                (mod / ".mcp.json").write_text("{}")
                errors = lint.lint_mod(mod)
            finally:
                lint.ROOT = saved
        self.assertEqual(len(errors), 2)


    def test_a_claude_manifest_holds_only_keys_every_claude_host_reads(self) -> None:
        for path, document in self.documents.items():
            if path.endswith(".claude-plugin/plugin.json"):
                self.assertLessEqual(set(document), lint.CLAUDE_MANIFEST_KEYS, path)
        with tempfile.TemporaryDirectory() as temp:
            mod = Path(temp) / "atlan-m"
            (mod / ".claude-plugin").mkdir(parents=True)
            (mod / ".claude-plugin/plugin.json").write_text('{"name": "atlan-m", "types": "./types/index.d.ts"}')
            saved = lint.ROOT
            lint.ROOT = Path(temp)
            try:
                errors = lint.lint_claude_manifest(mod)
            finally:
                lint.ROOT = saved
        self.assertEqual(len(errors), 1)
        self.assertIn("`types`", errors[0])


class SkillMetadataTest(unittest.TestCase):
    """`[skills]`: Codex's card for each skill and the MCP server it needs."""

    def setUp(self) -> None:
        self.source = build.load()
        self.files = build.skill_files(self.source, self.source)

    def test_every_skill_in_every_plugin_has_a_card_that_invokes_it_and_needs_the_atlan_server(self) -> None:
        skills = [path.parent for base in build.plugin_dirs(self.source) for path in sorted((ROOT / base / "skills").glob("*/SKILL.md"))]
        self.assertEqual(len(self.files), len(skills))
        mcp = self.source["mcp"]
        for skill in skills:
            text = self.files[f"{skill.relative_to(ROOT).as_posix()}/{build.SKILL_METADATA}"]
            self.assertIn(f'default_prompt: "Use ${skill.name} ', text, skill.name)
            self.assertIn(f'      value: "{mcp["server"]}"\n', text, skill.name)
            self.assertIn(f'      url: "{mcp["url"]}"\n', text, skill.name)

    def test_values_are_quoted_so_any_text_stays_one_yaml_scalar(self) -> None:
        source = copy.deepcopy(self.source)
        source["skills"]["atlan-continue"]["short_description"] = 'Say "hi": #1'
        self.assertIn('short_description: "Say \\"hi\\": #1"', build.skill_metadata(source, "atlan-continue"))

    def test_refuses_a_skill_without_an_entry_an_entry_without_a_skill_or_a_prompt_that_does_not_invoke_it(self) -> None:
        missing = copy.deepcopy(self.source)
        del missing["skills"]["atlan-continue"]
        unused = copy.deepcopy(self.source)
        unused["skills"]["atlan-gone"] = dict(self.source["skills"]["atlan-continue"], prompt="Use $atlan-gone.")
        for broken in (missing, unused):
            with self.assertRaises(SystemExit):
                build.skill_files(broken, broken)
        for prompt in ("Catch me up.", "Use $atlan-continue " + "x" * build.PROMPT_LIMIT):
            broken = copy.deepcopy(self.source)
            broken["skills"]["atlan-continue"]["prompt"] = prompt
            with self.subTest(prompt=prompt[:20]), self.assertRaises(SystemExit):
                build.validate(broken)

    def test_a_variant_card_names_the_variants_server_url(self) -> None:
        variant = build.make_variant(self.source, copy.deepcopy(VariantTest.OVERRIDES))
        files = build.variant_files(self.source, variant, "variant.toml")
        text = files[f"plugins/atlan-staging/skills/atlan-continue/{build.SKILL_METADATA}"]
        self.assertIn('url: "https://api.staging.example/mcp"', text)
        self.assertIn("Use $atlan-continue ", text)
        self.assertIn(f"plugins/atlan-staging-analytics/skills/atlan-analytics/{build.SKILL_METADATA}", files)


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
        errors = self.lint("atlan-x", "---\nname: atlan-y\ndescription: Use when testing.\n---\nbody\n")
        self.assertTrue(any("differs from its directory" in e for e in errors), errors)

    def test_description_is_required_bounded_and_tag_free(self) -> None:
        self.assertTrue(self.lint("a", "---\nname: a\n---\nbody\n"))
        self.assertTrue(self.lint("a", f"---\nname: a\ndescription: {'x' * 1025}\n---\nbody\n"))
        self.assertTrue(self.lint("a", "---\nname: a\ndescription: Use <b>now</b>\n---\nbody\n"))

    def test_an_owned_skills_description_says_when_to_use_it(self) -> None:
        what_only = "---\nname: a\ndescription: Create and publish skill bundles.\n---\nbody\n"
        self.assertTrue(any("when to use the skill" in e for e in self.lint("a", what_only)))
        self.assertEqual(self.lint("a", what_only, owned=False), [])
        for when in ("Use when the user asks.", "Use for questions such as x."):
            self.assertEqual(self.lint("a", f"---\nname: a\ndescription: Publish. {when}\n---\nbody\n"), [])

    def test_owned_skills_may_not_cite_missing_files_or_skills(self) -> None:
        text = "---\nname: a\ndescription: Use when testing.\n---\nRead `references/gone.md` and `atlan-missing`.\n"
        errors = self.lint("a", text)
        self.assertTrue(any("cites missing references/gone.md" in e for e in errors), errors)
        self.assertTrue(any("cites unknown skill atlan-missing" in e for e in errors), errors)
        self.assertEqual(self.lint("a", text, owned=False), [])

    def test_a_skill_may_cite_a_subagent_its_plugin_ships(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            addon = Path(temp) / "atlan-x"
            self.write_skill(addon, "atlan-x-run", "")
            (addon / "agents").mkdir()
            (addon / "agents/atlan-x-grader.md").write_text("---\nname: atlan-x-grader\n---\n")
            self.assertEqual(lint.citable([addon]), {"atlan-x-run", "atlan-x-grader"})

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

    def test_the_root_may_not_hold_plugin_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plugin.toml").write_text('[plugin]\nname = "atlan"\n')
            (root / "plugins/atlanai").mkdir(parents=True)
            (root / "skills/atlan-x").mkdir(parents=True)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text("{}")
            (root / ".claude-plugin/marketplace.json").write_text("{}")
            saved = lint.ROOT, lint.PLUGINS
            lint.ROOT, lint.PLUGINS = root, root / "plugins"
            try:
                errors = lint.lint_repository([])
            finally:
                lint.ROOT, lint.PLUGINS = saved
        self.assertEqual(sorted(e.split(":")[0] for e in errors), [".claude-plugin/plugin.json", "skills"])

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
    # The dashboard's prompt builders (an internal repository): each opening routes on words the
    # skill's description also carries, and names the handle it describes.
    CHAT = {
        'Catch me up on Atlan artifact "Q3 churn" (output_01abc), registry acme.': ("atlan-continue", "Catch me up on Atlan artifact", "output_"),
        'Load the "security-review" skill from the Atlan skill registry and then use it.': ("atlan-use-skill", "skill from the Atlan skill registry", "skill_"),
    }

    def test_the_command_prefix_is_the_plugin_name(self) -> None:
        self.assertEqual(build.load()["plugin"]["name"], "atlanai")

    def test_each_command_follows_its_skill(self) -> None:
        for command, skill in self.COMMANDS.items():
            with self.subTest(command=command):
                text = (CORE / "commands" / f"{command}.md").read_text(encoding="utf-8")
                self.assertIn(f"`atlanai:{skill}`", text)
                self.assertNotIn("skills/", text, "a command must name its skill, not a path: hosts resolve paths against the project")
                self.assertTrue((CORE / "skills" / skill / "SKILL.md").is_file())

    def test_each_commands_skill_hides_its_own_slash_entry(self) -> None:
        # Claude Code lists a command and a skill alike in the `/` menu; the skill behind a
        # command sets `user-invocable: false` so the task is listed once, under the command.
        for command, skill in self.COMMANDS.items():
            with self.subTest(command=command):
                parsed = lint.frontmatter((CORE / "skills" / skill / "SKILL.md").read_text(encoding="utf-8"))
                self.assertEqual(parsed[0].get("user-invocable"), "false")

    def test_codex_runs_the_get_started_skill_after_install(self) -> None:
        documents = build.outputs(build.load())
        skill = "./skills/atlan-get-started/SKILL.md"
        self.assertEqual(documents["plugins/atlanai/plugin.json"]["extensions"]["com.openai"]["onboardingSkill"], skill)
        self.assertEqual(documents["plugins/atlanai/.codex-plugin/plugin.json"]["extensions"]["com.openai"]["onboardingSkill"], skill)
        self.assertTrue((CORE / skill).is_file())

    def test_each_chat_sentence_names_a_skill_whose_description_matches_it(self) -> None:
        for sentence, (skill, phrase, handle) in self.CHAT.items():
            with self.subTest(sentence=sentence):
                self.assertIn(phrase, sentence)
                parsed = lint.frontmatter((CORE / "skills" / skill / "SKILL.md").read_text(encoding="utf-8"))
                self.assertIsNotNone(parsed)
                self.assertIn(phrase, parsed[0]["description"])
                self.assertIn(handle, parsed[0]["description"])


class MetricsStudioPromptTest(unittest.TestCase):
    """Metrics Studio (metric service `studio/src/lib/metrics-setup.ts`) sends these to set up metrics."""

    # `/<plugin>:atlan-analytics metrics` in Claude Code, `$<plugin>:atlan-analytics metrics` in
    # Codex, and over MCP alone `atlan://defaults/skills/atlan-analytics/SKILL.md`, metrics mode.
    SKILL = "atlan-analytics"

    def setUp(self) -> None:
        self.addon = ROOT / "plugins" / build.load()["addons"][0]["name"]

    def test_the_skill_keeps_its_name_and_metrics_mode(self) -> None:
        text = (self.addon / "skills" / self.SKILL / "SKILL.md").read_text(encoding="utf-8")
        parsed = lint.frontmatter(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0]["name"], self.SKILL)
        self.assertIn("If the first argument is exactly `metrics`", text)

    def test_no_command_shadows_the_skill(self) -> None:
        self.assertFalse((self.addon / "commands" / f"{self.SKILL}.md").exists())


class SkillIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index, self.served = skill_index.payloads()

    def test_lists_every_skill_under_the_v0_2_schema(self) -> None:
        self.assertEqual(self.index["$schema"], skill_index.SCHEMA)
        names = [entry["name"] for entry in self.index["skills"]]
        self.assertEqual(names, sorted(p.name for p in (CORE / "skills").iterdir() if p.is_dir()))

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
            files = sorted(p.as_posix() for p in skill_index.skill_files(CORE / "skills" / entry["name"]))
            self.assertEqual(sorted(names), files, entry["name"])

    def test_output_is_byte_for_byte_reproducible(self) -> None:
        again_index, again_served = skill_index.payloads()
        self.assertEqual(self.index, again_index)
        self.assertEqual(self.served, again_served)


class VoiceTest(unittest.TestCase):
    """The voice rule in AGENTS.md: no em dashes, no exclamations, no filler, outside code."""

    def voice(self, text: str, name: str = "plugins/atlanai/README.md") -> list[str]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
            saved, lint.ROOT = lint.ROOT, root
            try:
                return lint.lint_voice(path, text) if lint.voice_scope(path) else []
            finally:
                lint.ROOT = saved

    def test_flags_an_em_dash_an_exclamation_and_filler_with_their_lines(self) -> None:
        errors = self.voice("Plain line.\nA pause \u2014 here.\nDone!\nA seamless, robust flow.\n")
        self.assertEqual([e.split(": ", 1)[0] for e in errors], ["plugins/atlanai/README.md:2", "plugins/atlanai/README.md:3", "plugins/atlanai/README.md:4", "plugins/atlanai/README.md:4"])
        self.assertIn("filler 'seamless'", errors[2])

    def test_leaves_code_quoted_text_and_out_of_scope_files_alone(self) -> None:
        self.assertEqual(self.voice("Run `a \u2014 b`.\n\n```\nseamless!\n```\n\nThe reply `Done!` is fine.\n"), [])
        self.assertEqual(self.voice("seamless \u2014 wow!\n", "evals/atlan/cases/x/case.yaml"), [])
        self.assertEqual(self.voice("seamless \u2014 wow!\n", "assets/notes.md"), [])

    def test_the_committed_text_passes(self) -> None:
        errors = [e for p in lint.tracked_files() if p.is_file() and lint.voice_scope(p) for e in lint.lint_voice(p, p.read_text(encoding="utf-8"))]
        self.assertEqual(errors, [])


class EvalCasesTest(unittest.TestCase):
    """The core plugin's eval cases and their `emulator.json` sidecars (evals/atlan/cases/)."""

    CASES = ROOT / "evals" / "atlan" / "cases"
    SWITCHES = {"ATLAN_EMULATOR_HIDE_SERVERS", "ATLAN_EMULATOR_EMPTY", "ATLAN_EMULATOR_RATE_LIMIT", "ATLAN_EMULATOR_CATALOG",
                "ATLAN_EMULATOR_SIGNED_OUT", "ATLAN_EMULATOR_SKILLS"}

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("atlan_eval_run", ROOT / "evals" / "atlan" / "run.py")
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def cases(self) -> list[Path]:
        found = sorted(p for p in self.CASES.iterdir() if (p / "case.yaml").is_file())
        self.assertGreaterEqual(len(found), 23)
        return found

    def test_every_case_is_named_after_its_folder_and_carries_the_plain_reply_grader(self) -> None:
        for case in self.cases():
            text = (case / "case.yaml").read_text(encoding="utf-8")
            self.assertIn(f"name: {case.name}\n", text.splitlines(keepends=True), case.name)
            self.assertIn("  - name: plain-reply\n", text.splitlines(keepends=True), case.name)

    def test_every_sidecar_names_known_keys_and_switches_and_a_plugin_its_tools_pattern_matches(self) -> None:
        sidecars = [case / "emulator.json" for case in self.cases() if (case / "emulator.json").is_file()]
        self.assertGreaterEqual(len(sidecars), 4)
        for sidecar in sidecars:
            settings = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertLessEqual(set(settings), self.runner.SIDECAR_KEYS, sidecar)
            self.assertLessEqual(set(settings.get("env", {})), self.SWITCHES, sidecar)
            for skill in settings.get("installed_skills", []):
                self.assertTrue((self.runner.INSTALLED / skill / "SKILL.md").is_file(), f"{sidecar}: {skill}")
            if "plugin_name" in settings:
                self.assertIn(self.runner.tools_pattern(settings["plugin_name"]), settings.get("allow_tools", []), sidecar)

    def test_the_runner_groups_cases_by_sidecar_and_filters_by_case_glob(self) -> None:
        groups = self.runner.groups(self.CASES, [])
        self.assertEqual(sum(len(cases) for _, cases in groups.values()), len(self.cases()))
        self.assertIn("", groups)
        for key, (settings, cases) in groups.items():
            self.assertEqual(key, json.dumps(settings, sort_keys=True) if settings else "")
            self.assertTrue(cases)
        only = self.runner.groups(self.CASES, ["wrong-server"])
        self.assertEqual([c.name for _, cases in only.values() for c in cases], ["wrong-server"])
        self.assertEqual(self.runner.case_filters(["--runs", "1", "--case", "a", "--case", "b*"]), ["a", "b*"])
        self.assertEqual(self.runner.without_case_filters(["--runs", "1", "--case", "a", "--case", "b*", "-j", "4"]),
                         ["--runs", "1", "-j", "4"])

    def test_the_runner_refuses_an_unknown_sidecar_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "odd"
            case.mkdir()
            (case / "case.yaml").write_text("name: odd\n", encoding="utf-8")
            (case / "emulator.json").write_text('{"environment": {}}', encoding="utf-8")
            with self.assertRaises(SystemExit):
                self.runner.groups(Path(tmp), [])


if __name__ == "__main__":
    unittest.main()
