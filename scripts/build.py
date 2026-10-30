#!/usr/bin/env python3
"""Generate every plugin and marketplace manifest from plugin.toml.

    python3 scripts/build.py           # write the generated files
    python3 scripts/build.py --check   # exit 1 if any generated file differs from plugin.toml
    python3 scripts/build.py --variant variant.toml --out DIR [--check]

Each tool reads a different file, so the same facts are written several times. Keeping one source
is what stops the name, version and MCP URL drifting apart between tools.

A variant is this plugin under another name and endpoint, published from another repository: its
TOML file overrides `[plugin]`, `[marketplace]` and `[mcp]` fields and lists `[rewrite]` pairs for
the skills' text. `--variant` writes the whole plugin into DIR: its manifests, a one-plugin
marketplace, and a copy of the skills and commands with the URLs, the plugin's name and its
repository rewritten. This repository holds no variant of its own.
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

# What a variant copies from this plugin: rewritten text, then files taken as they are.
COPIED = ("skills", "commands")
VERBATIM = ("assets/logo.svg", "LICENSE")
# What a variant file may set, by table.
OVERRIDES = {
    "plugin": ("name", "display_name", "description", "short_description", "long_description", "homepage", "repository"),
    "marketplace": ("name", "display_name", "description"),
    "mcp": ("url",),
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
    for url in (source["mcp"]["url"], plugin["homepage"], plugin["repository"]):
        if not url.startswith("https://"):
            raise SystemExit(f"{url} must be https")
    logo = ROOT / plugin["logo"]
    if not logo.is_file():
        raise SystemExit(f"plugin.logo {plugin['logo']} does not exist")


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
    validate(variant)
    if variant["plugin"]["name"] == source["plugin"]["name"]:
        raise SystemExit("a variant needs its own plugin name, or its commands collide with this one's")
    if variant["marketplace"]["name"] == source["marketplace"]["name"]:
        raise SystemExit("a variant needs its own marketplace name, so both can be added on one machine")
    if not all(url.startswith("https://") for pair in variant["rewrite"].items() for url in pair):
        raise SystemExit("every variant [rewrite] URL must be https")
    return variant


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


def outputs(source: dict) -> dict[str, dict]:
    plugin, mcp, market = source["plugin"], source["mcp"], source["marketplace"]
    owner = {"name": source["author"]["name"], "url": source["author"]["url"]}

    agent_plugin = {"$schema": AGENT_PLUGINS_SCHEMA, **core(source)}
    agent_plugin["extensions"] = {"com.openai": {"interface": codex_interface(source)}}

    entry = {
        "name": plugin["name"],
        "source": "./",
        "description": plugin["description"],
        "author": dict(source["author"]),
        "homepage": plugin["homepage"],
        "repository": plugin["repository"],
        "license": plugin["license"],
        "keywords": list(plugin["keywords"]),
        "category": plugin["category"],
    }

    return {
        # Agent Plugins 1.0: Codex, Cursor, GitHub Copilot, VS Code and other conforming hosts.
        "plugin.json": agent_plugin,
        "mcp.json": {
            "$schema": AGENT_PLUGINS_MCP_SCHEMA,
            "mcpServers": {mcp["server"]: {"type": "streamable-http", "url": mcp["url"]}},
        },
        # Claude Code (CLI, desktop, IDE extensions) reads only these two.
        ".mcp.json": {"mcpServers": {mcp["server"]: {"type": "http", "url": mcp["url"]}}},
        ".claude-plugin/plugin.json": {
            **core(source),
            "privacyPolicyUrl": plugin["privacy_policy_url"],
            "mcpServers": "./.mcp.json",
        },
        ".claude-plugin/marketplace.json": {
            "$schema": CLAUDE_MARKETPLACE_SCHEMA,
            "name": market["name"],
            "description": market["description"],
            "owner": owner,
            "metadata": {"description": market["description"]},
            "plugins": [entry],
        },
        # Codex builds that predate the root manifest. Ignored when extensions.com.openai exists.
        ".codex-plugin/plugin.json": {
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
                    "source": {"source": "url", "url": f"{plugin['repository']}.git"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": plugin["category"],
                }
            ],
        },
        # Cursor: the full component set (skills, commands) and the listing logo.
        ".cursor-plugin/plugin.json": {**core(source), "logo": plugin["logo"].removeprefix("./")},
        ".cursor-plugin/marketplace.json": {
            "name": market["name"],
            "owner": owner,
            "metadata": {"description": market["description"]},
            "plugins": [
                {"name": plugin["name"], "source": "./", "description": plugin["description"]}
            ],
        },
    }


def render(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def github_slug(url: str) -> str:
    return urlparse(url).path.strip("/").removesuffix(".git")


def rewrites(source: dict, variant: dict) -> dict[str, str]:
    """What a variant's copy replaces: its own pairs, then every way a file names the plugin."""
    main, other = source["plugin"], variant["plugin"]
    market, other_market = source["marketplace"]["name"], variant["marketplace"]["name"]
    pairs = dict(variant["rewrite"])
    pairs.update(
        {
            f"plugin:{main['name']}:": f"plugin:{other['name']}:",
            f"mcp__plugin_{main['name']}_": f"mcp__plugin_{other['name']}_",
            f"{main['name']}@{market}": f"{other['name']}@{other_market}",
            f"/plugin install {main['name']} ": f"/plugin install {other['name']} ",
            f"/{main['name']}:": f"/{other['name']}:",
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

## Where this comes from

The plugin's files are generated by `scripts/build.py --variant` from
{source['plugin']['repository']}. Change the skills and commands there. Change this
plugin's name and endpoints in `{variant_file}`, the one plugin file written by hand.
"""


def variant_files(source: dict, variant: dict, variant_file: str) -> dict[str, str]:
    """Every file of a variant, by path inside its repository."""
    texts = {path: render(document) for path, document in outputs(variant).items()}
    pairs = rewrites(source, variant)
    for top in COPIED:
        for path in sorted(p for p in (ROOT / top).rglob("*") if p.is_file()):
            relative = path.relative_to(ROOT).as_posix()
            texts[relative] = rewrite(path.read_text(encoding="utf-8"), pairs)
    for relative in VERBATIM:
        texts[relative] = (ROOT / relative).read_text(encoding="utf-8")
    texts["README.md"] = variant_readme(source, variant, variant_file)
    return texts


def sync(texts: dict[str, str], root: Path, check: bool, owned: tuple[str, ...] = ()) -> list[str]:
    """Write `texts` under `root`, or list what differs. Files under `owned` not in `texts` are stale."""
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
        for path in sorted(p for p in (root / top).rglob("*") if p.is_file()):
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
        drift = sync(texts, args.out, args.check, owned=COPIED)
        target = f"python3 scripts/build.py --variant {args.variant} --out {args.out}"
    else:
        texts = {path: render(document) for path, document in outputs(source).items()}
        drift = sync(texts, ROOT, args.check)
        target = "python3 scripts/build.py"

    if drift:
        print(f"generated files differ from their source; run {target}:", file=sys.stderr)
        for relative in drift:
            print(f"  {relative}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
