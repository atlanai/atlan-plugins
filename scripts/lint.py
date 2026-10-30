#!/usr/bin/env python3
"""Static checks for the plugin's content. Standard library only; exits 1 on any error.

    python3 scripts/lint.py

Every plugin is a folder under plugins/; the core is plugins/<[plugin] name>.

Skills (every plugins/<plugin>/skills/<dir>/SKILL.md):
  * a terminated `---` frontmatter block with `name` equal to its directory, lowercase
    letters, digits and single hyphens, at most 64 characters;
  * a non-empty `description` of at most 1024 characters with no XML tags (the line a model
    reads to decide whether to load the skill);
  * a non-empty body.
Skills this repository owns (not vendored, see upstream.json) are also held to:
  * at most 500 lines in SKILL.md (longer material goes in references/);
  * every `references/...` and `skills/...` path they cite exists (`skills/` from the plugin's folder);
  * every backticked `atlan-...` skill name they cite exists, or is served by the gateway only.
Commands (commands/*.md) and agents (agents/*.md): frontmatter with a `description`.
Skill names are unique across every plugin. Add-ons (every plugin but the core) carry no MCP
server, hooks or bin/ of their own: an add-on uses the core's one `atlan` connection.
Repository: plugin content only under plugins/ (the root is the marketplace), no symlinks, no
CLAUDE.md (plugins do not load it), no bin/ (claude.ai and organization sync refuse it), no hooks
(out of scope, see AGENTS.md), and no credential-shaped strings in any tracked text file.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
# What a plugin folder holds and the repository root must not: the root is the marketplace.
PLUGIN_CONTENT = ("skills", "commands", "agents", "plugin.json", "mcp.json", ".mcp.json",
                  ".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json")

SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
XML_TAG = re.compile(r"<[A-Za-z/][^>]*>")
CITED_PATH = re.compile(r"`((?:skills|references)/[A-Za-z0-9_./-]+\.md)`")
CITED_SKILL = re.compile(r"`(atlan-[a-z0-9-]+)`")
# Backticked `atlan-…` words that are not skills in this plugin.
NOT_SKILLS = {
    "atlan-plugins",  # this repository's marketplace name
    "atlan-dev",  # the suggested server name for the dev environment
    "atlan-author-knowledge",  # served by the gateway as an MCP prompt, not vendored here
}
SECRETS = [
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]


def frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """Parse a leading `---` block of `key: value` lines. Returns (fields, body) or None."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            fields = {}
            for line in lines[1:index]:
                key, sep, value = line.partition(":")
                if sep and key and not key[0].isspace():
                    fields[key.strip()] = value.strip().strip("\"'")
            return fields, "\n".join(lines[index + 1 :])
    return None


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    )
    return [ROOT / name for name in result.stdout.decode().split("\0") if name]


def vendored() -> set[str]:
    return set(json.loads((ROOT / "upstream.json").read_text(encoding="utf-8"))["files"])


def lint_skill(directory: Path, owned: bool, skills: set[str], root: Path = ROOT) -> list[str]:
    relative = directory.relative_to(ROOT)
    skill_md = directory / "SKILL.md"
    if not skill_md.is_file():
        return [f"{relative}: no SKILL.md"]
    text = skill_md.read_text(encoding="utf-8")
    parsed = frontmatter(text)
    if parsed is None:
        return [f"{relative}/SKILL.md: no terminated --- frontmatter"]
    fields, body = parsed
    errors = []

    name = fields.get("name", "")
    if name != directory.name:
        errors.append(f"{relative}/SKILL.md: name {name!r} differs from its directory")
    if not SKILL_NAME.match(name) or len(name) > 64:
        errors.append(f"{relative}/SKILL.md: name {name!r} is not lowercase-hyphenated, <= 64 chars")

    description = fields.get("description", "")
    if not description:
        errors.append(f"{relative}/SKILL.md: description is empty")
    elif len(description) > 1024:
        errors.append(f"{relative}/SKILL.md: description is {len(description)} chars (max 1024)")
    elif XML_TAG.search(description):
        errors.append(f"{relative}/SKILL.md: description contains an XML tag")

    if not body.strip():
        errors.append(f"{relative}/SKILL.md: body is empty")

    if owned:
        line_count = text.count("\n") + 1
        if line_count > 500:
            errors.append(f"{relative}/SKILL.md: {line_count} lines; move material to references/")
        for path in directory.rglob("*.md"):
            content = path.read_text(encoding="utf-8")
            for cited in CITED_PATH.findall(content):
                base = directory if cited.startswith("references/") else root
                if not (base / cited).is_file():
                    errors.append(f"{path.relative_to(ROOT)}: cites missing {cited}")
            for cited in CITED_SKILL.findall(content):
                if cited not in skills and cited not in NOT_SKILLS:
                    errors.append(f"{path.relative_to(ROOT)}: cites unknown skill {cited}")
    return errors


def lint_command(path: Path) -> list[str]:
    parsed = frontmatter(path.read_text(encoding="utf-8"))
    if parsed is None or not parsed[0].get("description"):
        return [f"{path.relative_to(ROOT)}: needs frontmatter with a description"]
    return []


def plugin_roots() -> list[Path]:
    """The core's folder, then each add-on's, all under plugins/."""
    with (ROOT / "plugin.toml").open("rb") as handle:
        core = PLUGINS / tomllib.load(handle)["plugin"]["name"]
    return [core, *sorted(p for p in PLUGINS.iterdir() if p.is_dir() and p != core)]


def lint_addon(root: Path) -> list[str]:
    relative = root.relative_to(ROOT)
    errors = []
    for name in (".mcp.json", "mcp.json", "hooks", "bin"):
        if (root / name).exists():
            errors.append(f"{relative}/{name}: an add-on reaches Atlan through the core plugin's server and runs no hooks")
    manifest = root / ".claude-plugin" / "plugin.json"
    if manifest.is_file() and "mcpServers" in json.loads(manifest.read_text(encoding="utf-8")):
        errors.append(f"{relative}/.claude-plugin/plugin.json: an add-on declares no mcpServers")
    return errors


def lint_repository(files: list[Path]) -> list[str]:
    errors = [
        f"{name}: plugin content lives in plugins/<plugin>/; the root is the marketplace"
        for name in PLUGIN_CONTENT
        if (ROOT / name).exists()
    ]
    for root in (ROOT, *plugin_roots()):
        relative = root.relative_to(ROOT).as_posix()
        if (root / "CLAUDE.md").exists():
            errors.append(f"{relative}/CLAUDE.md is not loaded by any host; use AGENTS.md at the root")
        if (root / "bin").exists():
            errors.append(f"{relative}/bin/ is refused by claude.ai and organization sync")
        if (root / "hooks").exists():
            errors.append(f"{relative}/hooks/ is out of scope for these plugins; see AGENTS.md before adding one")
    for path in files:
        if path.is_symlink():
            errors.append(f"{path.relative_to(ROOT)}: symlinks do not survive every host's install")
            continue
        if not path.is_file() or path.suffix in {".svg", ".png"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRETS:
            if pattern.search(text):
                errors.append(f"{path.relative_to(ROOT)}: looks like it contains a {label}")
    return errors


def citable(roots: list[Path]) -> set[str]:
    """The names a skill may cite: every skill, and every subagent a plugin ships."""
    return {p.name for root in roots for p in (root / "skills").glob("*") if p.is_dir()} | {
        p.stem for root in roots for p in (root / "agents").glob("*.md")
    }


def main() -> int:
    owned_paths = vendored()
    roots = plugin_roots()
    skill_dirs = [
        (root, p) for root in roots if (root / "skills").is_dir() for p in sorted((root / "skills").iterdir()) if p.is_dir()
    ]
    skills = citable(roots)

    errors = []
    seen: dict[str, Path] = {}
    for root, directory in skill_dirs:
        if directory.name in seen:
            errors.append(f"{directory.relative_to(ROOT)}: skill name also used by {seen[directory.name].relative_to(ROOT)}")
        seen[directory.name] = directory
        owned = f"{directory.relative_to(ROOT).as_posix()}/SKILL.md" not in owned_paths
        errors += lint_skill(directory, owned, skills, root)
    commands = [path for root in roots for top in ("commands", "agents") for path in sorted((root / top).glob("*.md"))]
    for path in commands:
        errors += lint_command(path)
    for root in roots[1:]:
        errors += lint_addon(root)
    errors += lint_repository(tracked_files())

    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(f"ok: {len(skill_dirs)} skills, {len(commands)} commands and agents, {len(roots) - 1} add-ons")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
