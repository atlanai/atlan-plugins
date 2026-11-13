#!/usr/bin/env python3
"""Generate every plugin and marketplace manifest from plugin.toml.

    python3 tools/build.py           # write the generated files
    python3 tools/build.py --check   # exit 1 if any generated file differs from plugin.toml
    python3 tools/build.py --codex-directory DIR      # the core as a hookless package for OpenAI's directory

Each tool reads a different file, so the same facts are written several times. Keeping one source
is what stops the name, version and MCP URL drifting apart between tools.

The repository is a marketplace: its root holds only the marketplace files, and every plugin is a
folder under plugins/. The core, `[plugin]` in plugin.toml, is plugins/<name>/ and holds the one
`atlan` MCP server. An add-on (`[[addons]]`) is a workload built on the registry, in its own folder
beside the core. It depends on the core and declares no MCP server, so it reaches Atlan through the
core's one connection. A mod (`[[mods]]`) is a Claude Code plugin whose hooks module changes how
Claude Code treats the core's tools; it is listed in the Claude marketplace alone, since no other
host loads a hooks module. Every plugin folder gets the listing logo and the LICENSE copied in,
because a plugin cannot point outside its own folder.

Every file here names production (`https://api.atlan.com`, `https://app.atlan.com`). Another
environment is served by that environment's gateway, which vendors the core at a pinned commit and
writes its own name, server URL and hosts on top; nothing here is built per environment.

`--codex-directory` writes the core as the package OpenAI's plugin directory accepts: the portable
manifest, `mcp.json`, the skills and the assets, with no hooks, since the directory refuses a plugin
that carries lifecycle hooks. The skills work without the publish helper: the skill app refuses
`files_from` by name and the skill sends `files`, one call more.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
CLAUDE_MARKETPLACE_SCHEMA = "https://code.claude.com/schemas/marketplace.json"

# Agent Plugins 1.0: 1-64 lowercase letters, digits, hyphens and periods, alphanumeric at both ends.
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")

# Where every plugin lives.
PLUGINS = "plugins"
# The core's hooks, by plugin.toml key: each harness's file, and the folder it must sit in.
HOOK_KEYS = {"claude_hooks": "claude-hooks", "codex_hooks": "command-hooks", "cursor_hooks": "command-hooks"}
# Skill bundles every pre-send check must agree on: check_bundle.py's tests and the publish hook's.
FIXTURES = "tools/tests/fixtures/publish-bundles.json"
# Files every plugin folder gets a copy of, from the repository root.
SHARED = ("assets/logo.svg", "assets/logo.png", "LICENSE", "NOTICE")
# What the directory package takes from the core's folder, beside the manifests it writes.
DIRECTORY_TREES = ("skills", "assets")
DIRECTORY_FILES = ("LICENSE", "NOTICE")
# A fixed timestamp for the directory package's zip entries, so one source gives one archive.
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
# Codex's per-skill metadata, inside each skill folder: written from [skills] in plugin.toml.
SKILL_METADATA = "agents/openai.yaml"
# The longest starter prompt Codex shows in full.
PROMPT_LIMIT = 128
def load(path: Path = ROOT / "plugin.toml") -> dict:
    with path.open("rb") as handle:
        source = tomllib.load(handle)
    validate(source)
    # Checked here, not in validate(): it reads the plugin folder on disk.
    for key, folder in HOOK_KEYS.items():
        hooks = source["plugin"].get(key)
        if hooks and not (hooks.startswith(f"./{folder}/") and (ROOT / core_dir(source) / hooks).is_file()):
            raise SystemExit(f"plugin.{key} {hooks} must be a file under {core_dir(source)}/{folder}/")
    onboarding = source.get("codex", {}).get("onboarding_skill")
    if onboarding and not (ROOT / core_dir(source) / onboarding).is_file():
        raise SystemExit(f"codex.onboarding_skill {onboarding} is not a file in {core_dir(source)}")
    return source


def check_name(name: str, label: str) -> None:
    if not NAME.match(name) or "--" in name or ".." in name:
        raise SystemExit(f"{label} {name!r} is not a valid plugin name")
    if name.startswith(("claude-", "anthropic-")):
        raise SystemExit("plugin names starting claude- or anthropic- are reserved")


def validate(source: dict) -> None:
    plugin = source["plugin"]
    check_name(plugin["name"], "plugin.name")
    if not NAME.match(source["marketplace"]["name"]):
        raise SystemExit("marketplace.name is not a valid name")
    if not SEMVER.match(plugin["version"]):
        raise SystemExit(f"plugin.version {plugin['version']!r} is not SemVer")
    if not NAME.match(source["mcp"]["client"]):
        raise SystemExit("mcp.client is not a valid name")
    for url in (source["mcp"]["url"], plugin["homepage"], plugin["repository"]):
        if not url.startswith("https://"):
            raise SystemExit(f"{url} must be https")
    logo = ROOT / plugin["logo"]
    if not logo.is_file():
        raise SystemExit(f"plugin.logo {plugin['logo']} does not exist")
    for shot in source.get("codex", {}).get("screenshots", []):
        if not re.match(r"^\./assets/[\w.-]+\.(?:png|jpe?g|webp)$", shot):
            raise SystemExit(f"codex.screenshots {shot} must be an image under ./assets/")
    names = set()
    for addon in source.get("addons", []):
        check_name(addon["name"], "addons.name")
        # The core's name as a prefix keeps an add-on beside its core in every plugin list.
        if not addon["name"].startswith(f"{plugin['name']}-"):
            raise SystemExit(f"add-on {addon['name']!r} must be named {plugin['name']}-<workload>")
        if addon["name"] in names:
            raise SystemExit(f"add-on {addon['name']!r} is declared twice")
        names.add(addon["name"])
        if not SEMVER.match(addon["version"]):
            raise SystemExit(f"add-on {addon['name']} version {addon['version']!r} is not SemVer")
    for mod in source.get("mods", []):
        check_name(mod["name"], "mods.name")
        if not mod["name"].startswith(f"{plugin['name']}-"):
            raise SystemExit(f"mod {mod['name']!r} must be named {plugin['name']}-<purpose>")
        if mod["name"] in names:
            raise SystemExit(f"mod {mod['name']!r} is declared twice or shares an add-on's name")
        names.add(mod["name"])
        if not SEMVER.match(mod["version"]):
            raise SystemExit(f"mod {mod['name']} version {mod['version']!r} is not SemVer")
    mod_names = {mod["name"] for mod in source.get("mods", [])}
    for addon in source.get("addons", []):
        companions = addon.get("claude_companions", [])
        if not isinstance(companions, list) or any(not isinstance(name, str) or name not in mod_names for name in companions):
            raise SystemExit(f"add-on {addon['name']} claude_companions must name declared mods")
        if len(companions) != len(set(companions)):
            raise SystemExit(f"add-on {addon['name']} repeats a Claude companion")
    for name, entry in source.get("skills", {}).items():
        if set(entry) != {"display_name", "short_description", "prompt"}:
            raise SystemExit(f"[skills.{name}] needs exactly display_name, short_description and prompt")
        if f"${name}" not in entry["prompt"] or len(entry["prompt"]) > PROMPT_LIMIT:
            raise SystemExit(f"[skills.{name}] prompt must name ${name} in at most {PROMPT_LIMIT} characters")


def core_dir(source: dict) -> str:
    return f"{PLUGINS}/{source['plugin']['name']}"


def addon_dir(addon: dict) -> str:
    return f"{PLUGINS}/{addon['name']}"


def mod_dir(mod: dict) -> str:
    return f"{PLUGINS}/{mod['name']}"


def core(source: dict) -> dict:
    """The fields every manifest format shares, in the order they are written."""
    plugin, author = source["plugin"], source["author"]
    return {
        "name": plugin["name"],
        "version": plugin["version"],
        "description": plugin["description"],
        "author": dict(author),
        "homepage": plugin["homepage"],
        "repository": plugin["repository"],
        "license": plugin["license"],
        "keywords": list(plugin["keywords"]),
    }


def codex_interface(source: dict) -> dict:
    plugin, codex = source["plugin"], source["codex"]
    interface = {
        "displayName": plugin["display_name"],
        "shortDescription": plugin["short_description"],
        "longDescription": plugin["long_description"],
        "developerName": source["author"]["name"],
        "category": plugin["category"],
        "capabilities": list(codex["capabilities"]),
        "websiteURL": source["author"]["url"],
        "defaultPrompt": list(codex["default_prompts"]),
        "brandColor": plugin["brand_color"],
        "composerIcon": plugin["logo"],
        "logo": plugin["logo"],
    }
    # Public MCP review needs support and legal URLs; leave unconfirmed pages out of drafts.
    for key, field in (("privacy_policy_url", "privacyPolicyURL"),
                       ("support_url", "supportURL"), ("terms_url", "termsOfServiceURL")):
        if key in plugin:
            interface[field] = plugin[key]
    if codex.get("screenshots"):
        interface["screenshots"] = list(codex["screenshots"])
    return interface


def codex_onboarding(source: dict) -> dict:
    """Codex runs this skill after install, so the first thing a user sees is the sign-in check."""
    skill = source["codex"].get("onboarding_skill")
    return {"onboardingSkill": skill} if skill else {}


def addon_core(source: dict, addon: dict) -> dict:
    plugin = source["plugin"]
    return {
        "name": addon["name"],
        "version": addon["version"],
        "description": addon["description"],
        "author": dict(source["author"]),
        "homepage": plugin["homepage"],
        "repository": plugin["repository"],
        "license": plugin["license"],
        "keywords": list(addon["keywords"]),
    }


def addon_interface(source: dict, addon: dict) -> dict:
    plugin = source["plugin"]
    return {
        "displayName": addon["display_name"],
        "shortDescription": addon["short_description"],
        "longDescription": addon["long_description"],
        "developerName": source["author"]["name"],
        "category": addon["category"],
        "capabilities": list(source["codex"]["capabilities"]),
        "websiteURL": source["author"]["url"],
        "defaultPrompt": list(addon["default_prompts"]),
        "brandColor": plugin["brand_color"],
        "composerIcon": plugin["logo"],
        "logo": plugin["logo"],
        "privacyPolicyURL": plugin["privacy_policy_url"],
    }


def addon_outputs(source: dict, addon: dict) -> dict[str, dict]:
    """An add-on's manifests, by path in this repository. None declares an MCP server."""
    base = addon_dir(addon)
    interface = addon_interface(source, addon)
    return {
        f"{base}/plugin.json": {
            "$schema": AGENT_PLUGINS_SCHEMA,
            **addon_core(source, addon),
            "extensions": {"com.openai": {"interface": interface}},
        },
        # Only Claude Code installs a dependency. Elsewhere the add-on's skills check for the
        # Atlan tools and say which plugin to add.
        f"{base}/.claude-plugin/plugin.json": {
            **addon_core(source, addon),
            "displayName": addon["display_name"],
            "dependencies": [source["plugin"]["name"], *addon.get("claude_companions", [])],
        },
        f"{base}/.codex-plugin/plugin.json": {
            **addon_core(source, addon),
            "skills": "./skills/",
            "interface": interface,
        },
        f"{base}/.cursor-plugin/plugin.json": {
            **addon_core(source, addon),
            "logo": source["plugin"]["logo"].removeprefix("./"),
        },
    }


def mod_outputs(source: dict, mod: dict) -> dict[str, dict]:
    """A mod's one manifest. Only Claude Code loads a hooks module, so it has no Agent Plugins,
    Codex or Cursor manifest, and it depends on the core for the `atlan` server it acts on."""
    manifest = {
        **addon_core(source, mod),
        "displayName": mod["display_name"],
        "dependencies": [source["plugin"]["name"]],
    }
    if mod.get("user_config"):
        manifest["userConfig"] = copy.deepcopy(mod["user_config"])
    return {f"{mod_dir(mod)}/.claude-plugin/plugin.json": manifest}


def addon_readme(source: dict, addon: dict) -> str:
    plugin, market = source["plugin"], source["marketplace"]["name"]
    slug = github_slug(plugin["repository"])
    installs = f", `{plugin['name']}` and the native Analytics view" if addon.get("claude_companions") else f" and `{plugin['name']}`"
    view_note = "No separate view installation is needed in Claude Code Desktop or CLI.\n" if addon.get("claude_companions") else ""
    view_privacy = "The included Claude view uses native presentation hooks to display saved progress and prepare chat requests.\n" if addon.get("claude_companions") else ""
    return f"""# {addon['display_name']}

{addon['long_description']}

## Install

In Claude Code, one command installs Analytics{installs} automatically:

```
claude plugin install {addon['name']} --marketplace {slug}
```

{view_note}

Other hosts install plugins one at a time, so add `{plugin['name']}` first. In Codex:

```
codex plugin marketplace add {slug}
codex plugin add {plugin['name']}@{market}
codex plugin add {addon['name']}@{market}
```

## Privacy

Analytics itself has no hooks or shipped executable binaries. Its skills run bundled Python helpers and may
set up an isolated metrics-agent environment with pinned dependencies after authorization.
{view_privacy}\
Working files stay in your project. Input plugs use the source connections you select; Registry
writes use the `{plugin['name']}` plugin's connection, signed in as you. See Atlan's
[privacy notice]({plugin['privacy_policy_url']}).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills, commands, agents and scripts in this folder are the only source: nothing else serves
or copies them.
"""


def plugin_assets(source: dict) -> dict[str, str | bytes]:
    """The shared files in every plugin folder, and each add-on's README. The core's and each
    mod's README are written by hand."""
    shared = {relative: (ROOT / relative).read_bytes() for relative in SHARED}
    files = {}
    bases = (core_dir(source), *(addon_dir(a) for a in source.get("addons", [])), *(mod_dir(m) for m in source.get("mods", [])))
    for base in bases:
        files.update({f"{base}/{relative}": text for relative, text in shared.items()})
    for addon in source.get("addons", []):
        files[f"{addon_dir(addon)}/README.md"] = addon_readme(source, addon)
    return files


def latest_manifest_url(source: dict) -> str:
    """The core's Agent Plugins manifest on the repository's main branch: merging to main is the
    release (AGENTS.md, Releasing), so its `version` is the latest released one. The hooks read it
    to say when an install is behind."""
    repository = source["plugin"]["repository"].rstrip("/")
    prefix = "https://github.com/"
    if not repository.startswith(prefix):
        raise SystemExit(f"[plugin] repository must be on {prefix} for the update notice: {repository}")
    return f"https://raw.githubusercontent.com/{repository[len(prefix):]}/main/{core_dir(source)}/plugin.json"


def hook_fixtures(source: dict) -> dict[str, str]:
    """The shared publish fixtures as a module the hooks' tests import, since a test runs with no
    file system. check_bundle.py's tests read the JSON itself."""
    if not source["plugin"].get("claude_hooks"):
        return {}
    cases = json.loads((ROOT / FIXTURES).read_text(encoding="utf-8"))["cases"]
    body = json.dumps(cases, indent=2, ensure_ascii=True)
    plugin = source["plugin"]
    return {
        f"{core_dir(source)}/claude-hooks/release.ts": "// Written by tools/build.py from plugin.toml: edit that file.\n\n"
        "/** This build's version, and where main's manifest says the latest released one. */\n"
        f"export const VERSION = {json.dumps(plugin['version'])}\n"
        f"export const LATEST_URL = {json.dumps(latest_manifest_url(source))}\n",
        f"{core_dir(source)}/claude-hooks/tests/fixtures.ts": f"// Written by tools/build.py from {FIXTURES}: edit that file.\n\n"
        "import type { Case } from './case'\n\n"
        f"export const CASES: readonly Case[] = {body}\n"
    }


def skill_metadata(source: dict, name: str) -> str:
    """Codex's card for one skill, and the MCP server it needs, so a skill installed without the
    plugin can still ask for Atlan's server. JSON strings are YAML double-quoted scalars."""
    entry, mcp = source["skills"][name], source["mcp"]
    quote = lambda text: json.dumps(text, ensure_ascii=False)  # noqa: E731
    return f"""interface:
  display_name: {quote(entry['display_name'])}
  short_description: {quote(entry['short_description'])}
  default_prompt: {quote(entry['prompt'])}
dependencies:
  tools:
    - type: "mcp"
      value: {quote(mcp['server'])}
      description: "Atlan's MCP server, signed in with OAuth"
      transport: "streamable_http"
      url: {quote(mcp['url'])}
"""


def skill_files(source: dict) -> dict[str, str]:
    """Every skill's Codex metadata, by path. The skills are the folders in this repository's
    plugins; each needs a [skills.<name>] entry, and each entry a skill."""
    files, listed = {}, set()
    for folder in plugin_dirs(source):
        for skill in sorted(path.parent.name for path in (ROOT / folder / "skills").glob("*/SKILL.md")):
            if skill not in source.get("skills", {}):
                raise SystemExit(f"plugin.toml has no [skills.{skill}] for {folder}/skills/{skill}")
            listed.add(skill)
            files[f"{folder}/skills/{skill}/{SKILL_METADATA}"] = skill_metadata(source, skill)
    unused = set(source.get("skills", {})) - listed
    if unused:
        raise SystemExit(f"plugin.toml lists skills no plugin has: {', '.join(sorted(unused))}")
    return files


def client_headers(source: dict) -> dict[str, str]:
    """The header that names this install to the gateway, which records it on each session."""
    return {"X-Atlan-Client": f"{source['mcp']['client']}/{source['plugin']['version']}"}


def outputs(source: dict) -> dict[str, dict]:
    plugin, mcp, market = source["plugin"], source["mcp"], source["marketplace"]
    headers = client_headers(source)
    owner = {"name": source["author"]["name"], "url": source["author"]["url"]}

    agent_plugin = {"$schema": AGENT_PLUGINS_SCHEMA, **core(source)}
    # Codex reads its plugin hooks from here; the other Agent Plugins hosts ignore this namespace.
    codex_hooks = {"hooks": plugin["codex_hooks"]} if plugin.get("codex_hooks") else {}
    agent_plugin["extensions"] = {"com.openai": {**codex_onboarding(source), "interface": codex_interface(source), **codex_hooks}}

    base = core_dir(source)
    entry = {
        "name": plugin["name"],
        "displayName": plugin["display_name"],
        "source": f"./{base}",
        "description": plugin["description"],
        "author": dict(source["author"]),
        "homepage": plugin["homepage"],
        "repository": plugin["repository"],
        "license": plugin["license"],
        "keywords": list(plugin["keywords"]),
        "category": plugin["claude_category"],
    }
    addons = source.get("addons", [])
    claude_addons = [
        {
            "name": addon["name"],
            "displayName": addon["display_name"],
            "source": f"./{addon_dir(addon)}",
            "description": addon["description"],
            "author": dict(source["author"]),
            "homepage": plugin["homepage"],
            "repository": plugin["repository"],
            "license": plugin["license"],
            "keywords": list(addon["keywords"]),
            "category": addon["claude_category"],
        }
        for addon in addons
    ]
    codex_addons = [
        {
            "name": addon["name"],
            "source": {"source": "local", "path": f"./{addon_dir(addon)}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": addon["category"],
        }
        for addon in addons
    ]
    cursor_addons = [
        {"name": addon["name"], "source": f"./{addon_dir(addon)}", "description": addon["description"]}
        for addon in addons
    ]
    # Mods are Claude Code's alone: no other host loads a hooks module.
    claude_mods = [
        {
            "name": mod["name"],
            "displayName": mod["display_name"],
            "source": f"./{mod_dir(mod)}",
            "description": mod["description"],
            "author": dict(source["author"]),
            "homepage": plugin["homepage"],
            "repository": plugin["repository"],
            "license": plugin["license"],
            "keywords": list(mod["keywords"]),
            "category": mod["claude_category"],
        }
        for mod in source.get("mods", [])
    ]

    documents = {
        # Agent Plugins 1.0: Codex, Cursor, GitHub Copilot, VS Code and other conforming hosts.
        f"{base}/plugin.json": agent_plugin,
        # No extensions.com.openai.auth on the server: codex-cli 0.160.0 drops a server entry that
        # carries `extensions`, so atlanai shipped no `atlan` server in Codex (atlanai 0.5.7, 0.5.8).
        # Codex finds the OAuth sign-in through the gateway's 401 and protected resource metadata.
        f"{base}/mcp.json": {
            "$schema": AGENT_PLUGINS_MCP_SCHEMA,
            "mcpServers": {mcp["server"]: {"type": "streamable-http", "url": mcp["url"], "headers": headers}},
        },
        # Claude Code (CLI, desktop, IDE extensions) reads only these two. `alwaysLoad` exempts the
        # server from tool search deferral, so its search-mode helpers are listed at full schema
        # on the first turn instead of costing a turn to load: in the 9 November eval that cut the
        # median task from 13.3 s to 11.8 s with no score loss, for about 1k tokens. Search mode
        # lists only the helpers and a few core reads, so this is not the whole catalogue.
        f"{base}/.mcp.json": {"mcpServers": {mcp["server"]: {"type": "http", "url": mcp["url"], "headers": dict(headers), "alwaysLoad": True}}},
        # Directory fields are supported by Claude Code 2.1.281 and later.
        # They belong on the plugin manifest, never the marketplace entry.
        f"{base}/.claude-plugin/plugin.json": {
            **core(source),
            "displayName": plugin["display_name"],
            "icon": plugin["logo"],
            "privacyPolicyUrl": plugin["privacy_policy_url"],
            "documentationUrl": plugin["documentation_url"],
            "mcpServers": "./.mcp.json",
            # Claude Code alone runs a hooks module; no other manifest names it.
            **({"hooks": plugin["claude_hooks"]} if plugin.get("claude_hooks") else {}),
        },
        ".claude-plugin/marketplace.json": {
            "$schema": CLAUDE_MARKETPLACE_SCHEMA,
            "name": market["name"],
            "description": market["description"],
            "owner": owner,
            "metadata": {"description": market["description"]},
            "plugins": [entry, *claude_addons, *claude_mods],
        },
        # Codex builds that predate plugin.json. Ignored when extensions.com.openai exists.
        f"{base}/.codex-plugin/plugin.json": {
            **core(source),
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "interface": codex_interface(source),
            **codex_hooks,
            **({"extensions": {"com.openai": codex_onboarding(source)}} if codex_onboarding(source) else {}),
        },
        # Codex marketplace. Codex also reads .claude-plugin/marketplace.json. Each entry is a
        # local path into the clone `codex plugin marketplace add` makes: Codex lists a git or npm
        # entry without reading its plugin's manifest, so the picker showed the bare name and a
        # stock icon; a local entry is listed from the plugin's own manifest, displayName and logo.
        ".agents/plugins/marketplace.json": {
            "name": market["name"],
            "interface": {"displayName": market["display_name"]},
            "plugins": [
                {
                    "name": plugin["name"],
                    "source": {"source": "local", "path": f"./{base}"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": plugin["category"],
                },
                *codex_addons,
            ],
        },
        # Cursor: the full component set (skills, commands) and the listing logo.
        f"{base}/.cursor-plugin/plugin.json": {
            **core(source),
            "logo": plugin["logo"].removeprefix("./"),
            **({"hooks": plugin["cursor_hooks"]} if plugin.get("cursor_hooks") else {}),
        },
        ".cursor-plugin/marketplace.json": {
            "name": market["name"],
            "owner": owner,
            "metadata": {"description": market["description"]},
            "plugins": [
                {"name": plugin["name"], "source": f"./{base}", "description": plugin["description"]},
                *cursor_addons,
            ],
        },
    }
    for addon in addons:
        documents.update(addon_outputs(source, addon))
    for mod in source.get("mods", []):
        documents.update(mod_outputs(source, mod))
    return documents


def directory_files(source: dict) -> dict[str, bytes]:
    """The core as OpenAI's directory takes it, by path inside the package: the portable manifest
    without hooks, mcp.json, and the skills, assets and licence as they are in this repository.
    Neither .codex-plugin/ (it names the hooks too) nor any other host's manifest goes in."""
    base = core_dir(source)
    documents = outputs(source)
    manifest = copy.deepcopy(documents[f"{base}/plugin.json"])
    manifest["extensions"]["com.openai"].pop("hooks", None)
    mcp = copy.deepcopy(documents[f"{base}/mcp.json"])
    server = mcp["mcpServers"][source["mcp"]["server"]]
    server.pop("headers", None)
    server["url"] = source["submission"]["openai"].get("mcp_url", server["url"])
    server["extensions"] = {"com.openai": {
        "auth": copy.deepcopy(source["submission"]["openai"]["auth"])}}
    files = {"plugin.json": render(manifest).encode(), "mcp.json": render(mcp).encode()}
    folder = ROOT / base
    for tree in DIRECTORY_TREES:
        for path in sorted((folder / tree).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[path.relative_to(folder).as_posix()] = path.read_bytes()
    for name in DIRECTORY_FILES:
        files[name] = (folder / name).read_bytes()
    missing = [shot for shot in source["codex"].get("screenshots", []) if shot.removeprefix("./") not in files]
    if missing:
        raise SystemExit(f"codex.screenshots not in {base}/assets/: {', '.join(missing)}")
    return files


def directory_gaps(source: dict) -> list[str]:
    """What the directory's listing asks for that plugin.toml does not set yet."""
    gaps = []
    if not source["plugin"].get("support_url"):
        gaps.append("plugin.support_url (supportURL)")
    if "terms_url" not in source["plugin"]:
        gaps.append("plugin.terms_url (termsOfServiceURL)")
    if not source.get("codex", {}).get("screenshots"):
        gaps.append("codex.screenshots")
    return gaps


def write_directory(source: dict, out: Path) -> Path:
    """Write the package under out/<name>/ and as out/<name>-<version>.zip, plugin.json at its root."""
    plugin = source["plugin"]
    folder = out / plugin["name"]
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f"{folder} is not empty; remove it first")
    files = directory_files(source)
    for relative, data in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    archive = out / f"{plugin['name']}-{plugin['version']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative in sorted(files):
            entry = zipfile.ZipInfo(relative, ZIP_TIME)
            entry.external_attr = 0o644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry, files[relative])
    return archive


def render(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def github_slug(url: str) -> str:
    return urlparse(url).path.strip("/").removesuffix(".git")



def plugin_dirs(source: dict) -> list[str]:
    """Every plugin folder, the core first."""
    return [core_dir(source), *(addon_dir(addon) for addon in source.get("addons", []))]


def sync(texts: dict[str, str | bytes], root: Path, check: bool) -> list[str]:
    """Write `texts` under `root`, or list what differs."""
    drift = []
    for relative, text in texts.items():
        path = root / relative
        data = text.encode("utf-8") if isinstance(text, str) else text
        current = path.read_bytes() if path.exists() else None
        if current == data:
            continue
        if check:
            drift.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {relative}")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail on drift instead of writing")
    parser.add_argument("--codex-directory", type=Path, metavar="DIR",
                        help="write the core as a hookless package for OpenAI's plugin directory into DIR")
    args = parser.parse_args(argv)

    source = load()
    if args.codex_directory:
        if args.check:
            parser.error("--codex-directory takes no other option")
        texts = {path: render(document) for path, document in outputs(source).items()}
        texts.update(plugin_assets(source))
        texts.update(skill_files(source))
        if sync(texts, ROOT, check=True):
            raise SystemExit("generated files are out of date; run python3 tools/build.py first")
        print(f"wrote {write_directory(source, args.codex_directory)}")
        for gap in directory_gaps(source):
            print(f"the directory listing also asks for {gap}, which plugin.toml does not set", file=sys.stderr)
        return 0
    texts = {path: render(document) for path, document in outputs(source).items()}
    texts.update(plugin_assets(source))
    texts.update(skill_files(source))
    texts.update(hook_fixtures(source))
    drift = sync(texts, ROOT, args.check)
    target = "python3 tools/build.py"

    if drift:
        print(f"generated files differ from their source; run {target}:", file=sys.stderr)
        for relative in drift:
            print(f"  {relative}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
