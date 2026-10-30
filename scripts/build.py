#!/usr/bin/env python3
"""Generate every plugin and marketplace manifest from plugin.toml.

    python3 scripts/build.py           # write the generated files
    python3 scripts/build.py --check   # exit 1 if any generated file differs from plugin.toml

Each tool reads a different file, so the same facts are written several times. Keeping one source
is what stops the name, version and MCP URL drifting apart between tools.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
CLAUDE_MARKETPLACE_SCHEMA = "https://code.claude.com/schemas/marketplace.json"

# Agent Plugins 1.0: 1-64 lowercase letters, digits, hyphens and periods, alphanumeric at both ends.
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def load(path: Path = ROOT / "plugin.toml") -> dict:
    with path.open("rb") as handle:
        source = tomllib.load(handle)
    validate(source)
    return source


def validate(source: dict) -> None:
    plugin = source["plugin"]
    for key in ("name",):
        if not NAME.match(plugin[key]) or "--" in plugin[key] or ".." in plugin[key]:
            raise SystemExit(f"plugin.{key} {plugin[key]!r} is not a valid plugin name")
    if not NAME.match(source["marketplace"]["name"]):
        raise SystemExit("marketplace.name is not a valid name")
    if plugin["name"].startswith(("claude-", "anthropic-")):
        raise SystemExit("plugin names starting claude- or anthropic- are reserved")
    if not SEMVER.match(plugin["version"]):
        raise SystemExit(f"plugin.version {plugin['version']!r} is not SemVer")
    if not source["mcp"]["url"].startswith("https://"):
        raise SystemExit("mcp.url must be https")
    logo = ROOT / plugin["logo"]
    if not logo.is_file():
        raise SystemExit(f"plugin.logo {plugin['logo']} does not exist")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail on drift instead of writing")
    args = parser.parse_args(argv)

    drift = []
    for relative, document in outputs(load()).items():
        path = ROOT / relative
        text = render(document)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            drift.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {relative}")

    if drift:
        print("generated files differ from plugin.toml; run python3 scripts/build.py:", file=sys.stderr)
        for relative in drift:
            print(f"  {relative}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
