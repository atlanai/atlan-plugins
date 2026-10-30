#!/usr/bin/env python3
"""Static checks for the plugin's content. Standard library only; exits 1 on any error.

    python3 scripts/lint.py

Skills (every skills/<dir>/SKILL.md):
  * a terminated `---` frontmatter block with `name` equal to its directory, lowercase
    letters, digits and single hyphens, at most 64 characters;
  * a non-empty `description` of at most 1024 characters with no XML tags (the line a model
    reads to decide whether to load the skill);
  * a non-empty body.
Skills this repository owns (not vendored, see upstream.json) are also held to:
  * at most 500 lines in SKILL.md (longer material goes in references/);
  * every `references/...` and `skills/...` path they cite exists;
  * every backticked `atlan-...` skill name they cite exists, or is served by the gateway only.
Commands (commands/*.md): frontmatter with a `description`.
Repository: no symlinks, no root CLAUDE.md (plugins do not load it), no top-level bin/ (claude.ai
and organization sync refuse it), no hooks (out of scope for this plugin, see AGENTS.md), and no
credential-shaped strings in any tracked text file.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def lint_skill(directory: Path, owned: bool, skills: set[str]) -> list[str]:
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
                base = directory if cited.startswith("references/") else ROOT
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


def lint_repository(files: list[Path]) -> list[str]:
    errors = []
    if (ROOT / "CLAUDE.md").exists():
        errors.append("CLAUDE.md at the plugin root is not loaded by any host; use AGENTS.md")
    if (ROOT / "bin").exists():
        errors.append("bin/ at the plugin root is refused by claude.ai and organization sync")
    if (ROOT / "hooks").exists():
        errors.append("hooks/ is out of scope for this plugin; see AGENTS.md before adding one")
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


def main() -> int:
    owned_paths = vendored()
    skill_dirs = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
    skills = {p.name for p in skill_dirs}

    errors = []
    for directory in skill_dirs:
        owned = f"skills/{directory.name}/SKILL.md" not in owned_paths
        errors += lint_skill(directory, owned, skills)
    for path in sorted((ROOT / "commands").glob("*.md")):
        errors += lint_command(path)
    errors += lint_repository(tracked_files())

    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(f"ok: {len(skill_dirs)} skills, {len(list((ROOT / 'commands').glob('*.md')))} commands")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
