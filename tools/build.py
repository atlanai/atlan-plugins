#!/usr/bin/env python3
"""Generate every plugin and marketplace manifest from plugin.toml.

    python3 tools/build.py           # write the generated files
    python3 tools/build.py --check   # exit 1 if any generated file differs from plugin.toml
    python3 tools/build.py --variant variant.toml --out DIR [--check]

Each tool reads a different file, so the same facts are written several times. Keeping one source
is what stops the name, version and MCP URL drifting apart between tools.

The repository is a marketplace: its root holds only the marketplace files, and every plugin is a
folder under plugins/. The core, `[plugin]` in plugin.toml, is plugins/<name>/ and holds the one
`atlan` MCP server. An add-on (`[[addons]]`) is a workload built on the registry, in its own folder
beside the core. It depends on the core and declares no MCP server, so it reaches Atlan through the
core's one connection. Every plugin folder gets the listing logo and the LICENSE copied in, because
a plugin cannot point outside its own folder.

A variant is this marketplace under another name and endpoint, published from another repository:
its TOML file overrides `[plugin]`, `[marketplace]` and `[mcp]` fields and lists `[rewrite]` pairs
for the skills' text. `--variant` writes it into DIR: the marketplace files, and every plugin folder
renamed after the variant, with the URLs, the plugins' names and the repository rewritten. This
repository holds no variant of its own.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
CLAUDE_MARKETPLACE_SCHEMA = "https://code.claude.com/schemas/marketplace.json"

# Agent Plugins 1.0: 1-64 lowercase letters, digits, hyphens and periods, alphanumeric at both ends.
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")

# Where every plugin lives, and the trees a plugin may carry.
PLUGINS = "plugins"
TREES = ("skills", "commands", "agents")
# Files every plugin folder gets a copy of, from the repository root.
SHARED = ("assets/logo.svg", "LICENSE")
# Codex's per-skill metadata, inside each skill folder: written from [skills] in plugin.toml.
SKILL_METADATA = "agents/openai.yaml"
# The longest starter prompt Codex shows in full.
PROMPT_LIMIT = 128
# What a variant repository takes as it is, beside its plugins.
VERBATIM = ("LICENSE",)
# Where a variant repository held its core before plugins moved under plugins/. --variant removes
# them, so a repository written by an older build converges.
LEGACY = ("skills", "commands", "assets", "plugin.json", "mcp.json", ".mcp.json",
          ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json")
# What a variant file may set, by table.
OVERRIDES = {
    "plugin": ("name", "display_name", "description", "short_description", "long_description", "homepage", "repository"),
    "marketplace": ("name", "display_name", "description"),
    "mcp": ("url", "client"),
}


def load(path: Path = ROOT / "plugin.toml") -> dict:
    with path.open("rb") as handle:
        source = tomllib.load(handle)
    validate(source)
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
    names = set()
    for addon in source.get("addons", []):
        check_name(addon["name"], "addons.name")
        # The core's name as a prefix keeps an add-on beside its core in every plugin list, and
        # is how a variant renames it.
        if not addon["name"].startswith(f"{plugin['name']}-"):
            raise SystemExit(f"add-on {addon['name']!r} must be named {plugin['name']}-<workload>")
        if addon["name"] in names:
            raise SystemExit(f"add-on {addon['name']!r} is declared twice")
        names.add(addon["name"])
        if not SEMVER.match(addon["version"]):
            raise SystemExit(f"add-on {addon['name']} version {addon['version']!r} is not SemVer")
    for name, entry in source.get("skills", {}).items():
        if set(entry) != {"display_name", "short_description", "prompt"}:
            raise SystemExit(f"[skills.{name}] needs exactly display_name, short_description and prompt")
        if f"${name}" not in entry["prompt"] or len(entry["prompt"]) > PROMPT_LIMIT:
            raise SystemExit(f"[skills.{name}] prompt must name ${name} in at most {PROMPT_LIMIT} characters")


def load_variant(path: Path, source: dict) -> dict:
    with path.open("rb") as handle:
        overrides = tomllib.load(handle)
    return make_variant(source, overrides)


def make_variant(source: dict, overrides: dict) -> dict:
    """The source with a variant file's overrides applied, checked against the source."""
    variant = copy.deepcopy(source)
    for table, fields in OVERRIDES.items():
        unknown = set(overrides.get(table, {})) - set(fields)
        if unknown:
            raise SystemExit(f"variant [{table}] cannot set {', '.join(sorted(unknown))}")
        variant[table].update(overrides.get(table, {}))
    variant["rewrite"] = dict(overrides.get("rewrite", {}))
    variant["keep"] = list(overrides.get("keep", []))
    # Each add-on follows its core: atlan-analytics becomes <variant>-analytics.
    main, other = source["plugin"], variant["plugin"]
    for addon in variant.get("addons", []):
        addon["name"] = other["name"] + addon["name"].removeprefix(main["name"])
        if addon["display_name"].startswith(main["display_name"]):
            addon["display_name"] = other["display_name"] + addon["display_name"].removeprefix(main["display_name"])
    validate(variant)
    if variant["plugin"]["name"] == source["plugin"]["name"]:
        raise SystemExit("a variant needs its own plugin name, or its commands collide with this one's")
    if variant["marketplace"]["name"] == source["marketplace"]["name"]:
        raise SystemExit("a variant needs its own marketplace name, so both can be added on one machine")
    if not all(url.startswith("https://") for pair in variant["rewrite"].items() for url in pair):
        raise SystemExit("every variant [rewrite] URL must be https")
    return variant


def core_dir(source: dict) -> str:
    return f"{PLUGINS}/{source['plugin']['name']}"


def addon_dir(addon: dict) -> str:
    return f"{PLUGINS}/{addon['name']}"


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
    # Codex's directory asks for both; terms_url stays optional until Atlan names the page.
    for key, field in (("privacy_policy_url", "privacyPolicyURL"), ("terms_url", "termsOfServiceURL")):
        if key in plugin:
            interface[field] = plugin[key]
    return interface


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
            "dependencies": [source["plugin"]["name"]],
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


def addon_readme(source: dict, addon: dict) -> str:
    plugin, market = source["plugin"], source["marketplace"]["name"]
    slug = github_slug(plugin["repository"])
    return f"""# {addon['display_name']}

{addon['long_description']}

## Install

It uses the `{plugin['name']}` plugin's connection to Atlan and has no MCP server of its own.
Claude Code installs `{plugin['name']}` with it:

```
/plugin marketplace add {slug}
/plugin install {addon['name']}@{market}
```

Other hosts install plugins one at a time, so add `{plugin['name']}` first. In Codex:

```
codex plugin marketplace add {slug}
codex plugin add {plugin['name']}@{market}
codex plugin add {addon['name']}@{market}
```

## Privacy

It stores nothing and runs nothing on your machine: it has no hooks and no binaries. See Atlan's
[privacy notice]({plugin['privacy_policy_url']}).

## Source

`tools/build.py` generates this README and the manifests from `[[addons]]` in `plugin.toml`.
The skills, commands and agents in this folder are the source of truth. The Atlan app behind the
workload pins a commit of this repository and serves the same files over Atlan's MCP server.
"""


def plugin_assets(source: dict) -> dict[str, str]:
    """The shared files in every plugin folder, and each add-on's README. The core's README is
    written by hand."""
    shared = {relative: (ROOT / relative).read_text(encoding="utf-8") for relative in SHARED}
    files = {}
    for base in (core_dir(source), *(addon_dir(addon) for addon in source.get("addons", []))):
        files.update({f"{base}/{relative}": text for relative, text in shared.items()})
    for addon in source.get("addons", []):
        files[f"{addon_dir(addon)}/README.md"] = addon_readme(source, addon)
    return files


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


def skill_files(source: dict, target: dict) -> dict[str, str]:
    """Every skill's Codex metadata for `target`, by path. The skills are the folders in this
    repository's plugins; each needs a [skills.<name>] entry, and each entry a skill."""
    files, listed = {}, set()
    for ours, theirs in zip(plugin_dirs(source), plugin_dirs(target)):
        for skill in sorted(path.parent.name for path in (ROOT / ours / "skills").glob("*/SKILL.md")):
            if skill not in target.get("skills", {}):
                raise SystemExit(f"plugin.toml has no [skills.{skill}] for {ours}/skills/{skill}")
            listed.add(skill)
            files[f"{theirs}/skills/{skill}/{SKILL_METADATA}"] = skill_metadata(target, skill)
    unused = set(target.get("skills", {})) - listed
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
    agent_plugin["extensions"] = {"com.openai": {"interface": codex_interface(source)}}

    base = core_dir(source)
    entry = {
        "name": plugin["name"],
        "source": f"./{base}",
        "description": plugin["description"],
        "author": dict(source["author"]),
        "homepage": plugin["homepage"],
        "repository": plugin["repository"],
        "license": plugin["license"],
        "keywords": list(plugin["keywords"]),
        "category": plugin["category"],
    }
    addons = source.get("addons", [])
    claude_addons = [
        {
            "name": addon["name"],
            "source": f"./{addon_dir(addon)}",
            "description": addon["description"],
            "author": dict(source["author"]),
            "homepage": plugin["homepage"],
            "repository": plugin["repository"],
            "license": plugin["license"],
            "keywords": list(addon["keywords"]),
            "category": addon["category"],
        }
        for addon in addons
    ]
    codex_addons = [
        {
            "name": addon["name"],
            "source": {"source": "git-subdir", "url": f"{plugin['repository']}.git", "path": f"./{addon_dir(addon)}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": addon["category"],
        }
        for addon in addons
    ]
    cursor_addons = [
        {"name": addon["name"], "source": f"./{addon_dir(addon)}", "description": addon["description"]}
        for addon in addons
    ]

    documents = {
        # Agent Plugins 1.0: Codex, Cursor, GitHub Copilot, VS Code and other conforming hosts.
        f"{base}/plugin.json": agent_plugin,
        f"{base}/mcp.json": {
            "$schema": AGENT_PLUGINS_MCP_SCHEMA,
            "mcpServers": {mcp["server"]: {"type": "streamable-http", "url": mcp["url"], "headers": headers}},
        },
        # Claude Code (CLI, desktop, IDE extensions) reads only these two.
        f"{base}/.mcp.json": {"mcpServers": {mcp["server"]: {"type": "http", "url": mcp["url"], "headers": dict(headers)}}},
        # No privacyPolicyUrl: claude.ai's organization sync strips it as unknown. The plugin
        # directory reads the privacy link from the README instead.
        f"{base}/.claude-plugin/plugin.json": {
            **core(source),
            "mcpServers": "./.mcp.json",
        },
        ".claude-plugin/marketplace.json": {
            "$schema": CLAUDE_MARKETPLACE_SCHEMA,
            "name": market["name"],
            "description": market["description"],
            "owner": owner,
            "metadata": {"description": market["description"]},
            "plugins": [entry, *claude_addons],
        },
        # Codex builds that predate plugin.json. Ignored when extensions.com.openai exists.
        f"{base}/.codex-plugin/plugin.json": {
            **core(source),
            "skills": "./skills/",
            "mcpServers": "./.mcp.json",
            "interface": codex_interface(source),
        },
        # Codex marketplace. Codex also reads .claude-plugin/marketplace.json.
        ".agents/plugins/marketplace.json": {
            "name": market["name"],
            "interface": {"displayName": market["display_name"]},
            "plugins": [
                {
                    "name": plugin["name"],
                    "source": {"source": "git-subdir", "url": f"{plugin['repository']}.git", "path": f"./{base}"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": plugin["category"],
                },
                *codex_addons,
            ],
        },
        # Cursor: the full component set (skills, commands) and the listing logo.
        f"{base}/.cursor-plugin/plugin.json": {**core(source), "logo": plugin["logo"].removeprefix("./")},
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
    return documents


def render(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def github_slug(url: str) -> str:
    return urlparse(url).path.strip("/").removesuffix(".git")


def rewrites(source: dict, variant: dict) -> dict[str, str]:
    """What a variant's copy replaces: its own pairs, then every way a file names the plugin."""
    main, other = source["plugin"], variant["plugin"]
    market, other_market = source["marketplace"]["name"], variant["marketplace"]["name"]
    pairs = dict(variant["rewrite"])
    names = [(main["name"], other["name"])]
    names += [(a["name"], b["name"]) for a, b in zip(source.get("addons", []), variant.get("addons", []))]
    for old, new in names:
        pairs.update(
            {
                f"plugin:{old}:": f"plugin:{new}:",
                f"mcp__plugin_{old}_": f"mcp__plugin_{new}_",
                f"{old}@{market}": f"{new}@{other_market}",
                f"/plugin install {old} ": f"/plugin install {new} ",
                f"/{old}:": f"/{new}:",
            }
        )
    pairs.update(
        {
            main["repository"]: other["repository"],
            github_slug(main["repository"]): github_slug(other["repository"]),
        }
    )
    # A kept string maps to itself, so the longer match protects it from a shorter pair.
    pairs.update({kept: kept for kept in variant["keep"]})
    return {old: new for old, new in pairs.items() if old}


def rewrite(text: str, pairs: dict[str, str]) -> str:
    """Every pair at once, longest match first, so no replacement is rewritten again."""
    if not pairs:
        return text
    pattern = re.compile("|".join(re.escape(old) for old in sorted(pairs, key=len, reverse=True)))
    return pattern.sub(lambda match: pairs[match.group(0)], text)


def variant_readme(source: dict, variant: dict, variant_file: str) -> str:
    plugin, mcp = variant["plugin"], variant["mcp"]
    slug, market = github_slug(plugin["repository"]), variant["marketplace"]["name"]
    addons = variant.get("addons", [])
    addon_lines = ""
    if addons:
        names = ", ".join(f"`{addon['name']}@{market}`" for addon in addons)
        addon_lines = f"\nAdd-ons install the same way and use this plugin's connection: {names}.\n"
    return f"""# {plugin['display_name']}

{plugin['long_description']}

## Install

Claude Code:

```
/plugin marketplace add {slug}
/plugin install {plugin['name']}@{market}
```

Codex:

```
codex plugin marketplace add {slug}
codex plugin add {plugin['name']}@{market}
```

Then sign in. In Claude Code, run `/mcp`, choose `plugin:{plugin['name']}:{mcp['server']}` and
**Authenticate**. The server is `{mcp['url']}`.
{addon_lines}
## Where this comes from

The plugin's files are generated by `tools/build.py --variant` from
{source['plugin']['repository']}. Change the skills and commands there. Change this
plugin's name and endpoints in `{variant_file}`, the one plugin file written by hand.
"""


def plugin_dirs(source: dict) -> list[str]:
    """Every plugin folder, the core first."""
    return [core_dir(source), *(addon_dir(addon) for addon in source.get("addons", []))]


def variant_files(source: dict, variant: dict, variant_file: str) -> dict[str, str]:
    """Every file of a variant, by path inside its repository."""
    texts = {path: render(document) for path, document in outputs(variant).items()}
    texts.update(plugin_assets(variant))
    pairs = rewrites(source, variant)
    for ours, theirs in zip(plugin_dirs(source), plugin_dirs(variant)):
        base = ROOT / ours
        copied = [p for top in TREES for p in sorted((base / top).rglob("*")) if p.is_file()]
        for path in copied:
            texts[f"{theirs}/{path.relative_to(base).as_posix()}"] = rewrite(path.read_text(encoding="utf-8"), pairs)
        if ours == core_dir(source):
            # The core's README is written by hand; its title is the plugin's display name.
            readme = (base / "README.md").read_text(encoding="utf-8")
            title = f"# {source['plugin']['display_name']}\n"
            if readme.startswith(title):
                readme = f"# {variant['plugin']['display_name']}\n" + readme.removeprefix(title)
            texts[f"{theirs}/README.md"] = rewrite(readme, pairs)
    # After the copies, so the variant's own server URL wins over the rewritten one.
    texts.update(skill_files(source, variant))
    for relative in VERBATIM:
        texts[relative] = (ROOT / relative).read_text(encoding="utf-8")
    texts["README.md"] = variant_readme(source, variant, variant_file)
    return texts


def sync(texts: dict[str, str], root: Path, check: bool, owned: tuple[str, ...] = ()) -> list[str]:
    """Write `texts` under `root`, or list what differs. Files at or under `owned` not in `texts` are stale."""
    drift = []
    for relative, text in texts.items():
        path = root / relative
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            drift.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {relative}")
    for top in owned:
        paths = [root / top] if (root / top).is_file() else sorted(p for p in (root / top).rglob("*") if p.is_file())
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if relative in texts:
                continue
            if check:
                drift.append(relative)
                continue
            path.unlink()
            print(f"removed {relative}")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail on drift instead of writing")
    parser.add_argument("--variant", type=Path, help="a variant file: write that variant instead")
    parser.add_argument("--out", type=Path, help="with --variant: the variant repository's root")
    args = parser.parse_args(argv)

    source = load()
    if args.variant:
        if not args.out:
            parser.error("--variant needs --out")
        if args.out.resolve() == ROOT:
            parser.error("--out must be the variant's own repository, not this one")
        variant = load_variant(args.variant, source)
        texts = variant_files(source, variant, args.variant.name)
        drift = sync(texts, args.out, args.check, owned=(PLUGINS, *LEGACY))
        target = f"python3 tools/build.py --variant {args.variant} --out {args.out}"
    else:
        texts = {path: render(document) for path, document in outputs(source).items()}
        texts.update(plugin_assets(source))
        texts.update(skill_files(source, source))
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
