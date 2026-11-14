#!/usr/bin/env python3
"""Static checks for the plugin's content. Standard library only; exits 1 on any error.

    python3 tools/lint.py

Every plugin is a folder under plugins/; the core is plugins/<[plugin] name>.

Skills (every plugins/<plugin>/skills/<dir>/SKILL.md):
  * a terminated `---` frontmatter block with `name` equal to its directory, lowercase
    letters, digits and single hyphens, at most 64 characters;
  * a non-empty `description` of at most 1024 characters with no XML tags (the line a model
    reads to decide whether to load the skill);
  * a non-empty body.
Every skill is also held to:
  * at most 500 lines and 9 KiB in SKILL.md (longer material goes in references/);
  * a description of at most 250 characters, since every session carries it and hosts cut longer ones;
  * every `references/...` and `skills/...` path they cite exists (`skills/` from the plugin's folder);
  * every backticked `atlan-...`, `analytics-...`, `metrics-generation`, `eval-generation`, `accuracy-check` or `source-survey`
    skill name they cite exists, or is served by the gateway only.
Commands (commands/*.md) and agents (agents/*.md): frontmatter with a `description`; commands
also set `disable-model-invocation: true`.
Skill names are unique across every plugin. Add-ons carry no MCP server, hooks or bin/ of their
own: an add-on uses the core's one `atlan` connection. Mods (`[[mods]]` in plugin.toml) carry no MCP
server or bin/ either, and their hooks/hooks.json names one hooks module and no settings hooks.
Repository: plugin content only under plugins/ (the root is the marketplace), no symlinks, no
CLAUDE.md (plugins do not load it), no bin/ (claude.ai and organization sync refuse it), no hooks
outside a mod (see AGENTS.md), and no credential-shaped strings in any tracked text file.
Voice (every Markdown file at the root and under plugins/, and plugin.toml): no em dash, no
exclamation mark ending a sentence, and none of the filler words AGENTS.md's Voice section bans,
outside code. The words a user reads should say what happens, not how to feel about it.
Drift (every Markdown file under plugins/, code included): spellings the live gateway does not
serve and that a release once shipped by mistake: an app tool without its `extension__` prefix,
a `wrk_` workspace handle, and an `mcp__initialise` example that names one host as the client.
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
CITED_SKILL = re.compile(r"`((?:atlan|analytics)-[a-z0-9-]+|metrics-generation|eval-generation|accuracy-check|source-survey)`")
WHEN = re.compile(r"\bUse (?:when|for)\b")
# Every installed skill's description sits in every session's context, so owned skills keep it
# short; and SKILL.md is read whole whenever the skill loads, so detail goes in references/ that
# the skill names with the condition for opening them. Lower these as skills get leaner.
MAX_OWNED_DESCRIPTION = 250
MAX_SKILL_BYTES = 9 * 1024
# Backticked `atlan-…` words that are not skills in this plugin.
NOT_SKILLS = {
    "atlan-plugins",  # this repository's marketplace name
}
# This repository is public and production's: another environment's hosts and plugin names are
# written on top by that environment's gateway when it serves the pinned plugin (AGENTS.md,
# Environments), so none of them appears in a tracked file. Examples use `dev.example`.
NON_PRODUCTION = re.compile(r"\batlan\.(?:dev|engineering)\b|\batlanai?-dev\b")
# A value that opens like a placeholder, a lookup or a stand-in is not a secret: `<…>`, `${…}`,
# `{{…}}`, `$VAR`, `***`, `%s`, or a word such as `example` or `changeme`.
NOT_SECRET = r"[$<{*%]|(?:synthetic|example|dummy|fake|placeholder|changeme|redacted|your)"
SECRETS = [
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{28}")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{24,}")),
    ("password in a URL", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@<>{}$]+:(?!(?:password|passwd|pass|pwd|pw|secret|x+|\*+)@)(?![$<{%*])[^\s/@<>{}$]+@", re.I)),
    ("password", re.compile(
        r"(?<![A-Za-z0-9])[A-Za-z0-9_]*(?:password|passwd|client_secret|accountkey|sharedaccesskey)[\"']?[ \t]*[:=][ \t]*"
        rf"(?:\"(?!{NOT_SECRET})[^\"\s]{{8,}}\"|'(?!{NOT_SECRET})[^'\s]{{8,}}'"
        rf"|(?!{NOT_SECRET})(?=[^\s\"'(),;&\[\]]*\d)[^\s\"'(),;&\[\]]{{8,}}(?=[\s;,&]|$))",
        re.I,
    )),
]


# Voice: the marks and words that read as filler. AGENTS.md's Voice section says why. Checked
# outside fenced blocks and code spans, so a quoted error message or a tool name is never flagged.
EM_DASH = "\u2014"
SENTENCE_EXCLAIM = re.compile(r"[A-Za-z0-9)]!(?=\s|$)")
FILLER = re.compile(
    r"\b(?:seamless(?:ly)?|leverag(?:e|es|ed|ing)|robust(?:ly|ness)?|empower(?:s|ed|ing|ment)?"
    r"|effortless(?:ly)?|supercharg(?:e|es|ed|ing)|delv(?:e|es|ed|ing)|comprehensive(?:ly)?"
    r"|cutting-edge|streamlin(?:e|es|ed|ing)|game-chang(?:er|ers|ing)|unleash(?:es|ed|ing)?"
    r"|frictionless|great question|happy to help|i'd be happy to|let me know if|hope this helps"
    r"|dive (?:in|into))\b",
    re.IGNORECASE,
)
INLINE_CODE = re.compile(r"`[^`\n]*`")
# Drift: spellings the gateway does not serve. Checked in code too, where tool names live.
APP_TOOL_PREFIXES = ("metric", "ontology", "agent", "knowledge", "repo", "skill", "file", "scorer", "experiment")
DRIFT = [
    (re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(APP_TOOL_PREFIXES) + r")__[a-z][a-z_]*"),
     "an app's tools are served as `extension__<app>__<tool>`"),
    (re.compile(r"\bwrk_01"), "workspace handles start `workspace_`"),
    (re.compile(r'"client":\s*"claude-code"'),
     "name the client as a placeholder; the recipe runs in other tools too"),
]
# What the voice check reads: the Markdown people and models read, and the manifest strings.
VOICE_SUFFIXES = {".md", ".toml"}
VOICE_SKIPPED = ("evals/", ".github/", "assets/")


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
        # The description is what a harness matches a request against and what registry search
        # ranks, so it says when to use the skill, not only what the skill is.
        if description and not WHEN.search(description):
            errors.append(f"{relative}/SKILL.md: description does not say when to use the skill (\"Use when\" or \"Use for\")")
        if len(description) > MAX_OWNED_DESCRIPTION:
            errors.append(f"{relative}/SKILL.md: description is {len(description)} chars (max {MAX_OWNED_DESCRIPTION} for an owned skill)")
        line_count = text.count("\n") + 1
        if line_count > 500:
            errors.append(f"{relative}/SKILL.md: {line_count} lines; move material to references/")
        size = len(text.encode("utf-8"))
        if size > MAX_SKILL_BYTES:
            errors.append(f"{relative}/SKILL.md: {size} bytes (max {MAX_SKILL_BYTES}); move material to references/")
        for path in directory.rglob("*.md"):
            # A skill's `modules/` holds a vendored engine with its own docs and gate skills,
            # whose citations resolve inside the engine, not against this skill.
            if path.relative_to(directory).parts[0] == "modules":
                continue
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
    # A command only loads a skill the model can already see, so it is the person's `/` entry
    # alone: without this, hosts that list commands to the model (claude.ai, Claude Code's Skill
    # tool) show each task twice.
    if path.parent.name == "commands" and parsed[0].get("disable-model-invocation") != "true":
        return [f"{path.relative_to(ROOT)}: needs `disable-model-invocation: true`"]
    return []


def plugin_roots() -> list[Path]:
    """The core's folder, then each add-on's, all under plugins/."""
    with (ROOT / "plugin.toml").open("rb") as handle:
        core = PLUGINS / tomllib.load(handle)["plugin"]["name"]
    return [core, *sorted(p for p in PLUGINS.iterdir() if p.is_dir() and p != core)]


# The top-level keys a Claude plugin.json may hold: those every Claude host reads. The CLI reads
# more (`types`, `experimental`), but the desktop app strips a key it does not know and shows the
# person a warning on install, so a key joins this list only once the desktop app reads it too.
CLAUDE_MANIFEST_KEYS = frozenset({
    "name", "version", "description", "author", "homepage", "repository", "license", "keywords",
    "displayName", "dependencies", "mcpServers", "userConfig", "hooks",
})
# The core's hooks module, named only from its Claude manifest: Codex and Cursor read hooks/ by
# default, and only Claude Code runs a module (AGENTS.md, "Portable, except one hooks module").
CORE_HOOKS = "./claude-hooks/hooks.json"
# The command hook each other harness may name, by manifest, and where in it: Codex reads plugin
# hooks from `extensions.com.openai` in plugin.json (or the legacy overlay), Cursor from its own.
COMMAND_HOOKS = {
    "plugin.json": ("extensions.com.openai.hooks", "./command-hooks/codex.json"),
    ".codex-plugin/plugin.json": ("hooks", "./command-hooks/codex.json"),
    ".cursor-plugin/plugin.json": ("hooks", "./command-hooks/cursor.json"),
}
HOOK_SCRIPT = "command-hooks/publish_hook.py"
# Cursor's only command per event: an unset root exits 1, which Cursor passes over (exit 2 would block).
CURSOR_ENTRIES = {"sessionStart": "session-start", "preToolUse": "pre", "postToolUse": "post", "beforeMCPExecution": "mcp-guard"}
CURSOR_COMMAND = '[ -n "$CURSOR_PLUGIN_ROOT" ] && python3 "$CURSOR_PLUGIN_ROOT/command-hooks/publish_hook.py" {} --host cursor'


def lint_claude_manifest(root: Path, is_core: bool = False) -> list[str]:
    """Every Claude manifest holds only keys every Claude host reads, and only the core's names
    a hooks module, its own claude-hooks/."""
    manifest = root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return []
    document = json.loads(manifest.read_text(encoding="utf-8"))
    extra = sorted(set(document) - CLAUDE_MANIFEST_KEYS)
    errors = [
        f"{manifest.relative_to(ROOT)}: `{key}` is not a key every Claude host reads; the desktop app strips it and warns on install"
        for key in extra
    ]
    if "hooks" in document and not (is_core and document["hooks"] == CORE_HOOKS):
        errors.append(f"{manifest.relative_to(ROOT)}: only the core names a hooks module, as `hooks`: {CORE_HOOKS!r}")
    return errors


def lint_core_hooks(root: Path) -> list[str]:
    """The core's claude-hooks/: one hooks module, no settings hooks, named by no other host's manifest."""
    folder = root / "claude-hooks"
    if not folder.exists():
        return []
    relative = folder.relative_to(ROOT)
    hooks = folder / "hooks.json"
    if not hooks.is_file():
        return [f"{relative}/hooks.json: the core names its hooks module here"]
    errors = []
    document = json.loads(hooks.read_text(encoding="utf-8"))
    modules = document.get("modules")
    if set(document) - {"description", "modules"}:
        errors.append(f"{relative}/hooks.json: the core has no settings hooks, only `modules`")
    if not (isinstance(modules, list) and len(modules) == 1 and (folder / str(modules[0])).is_file()):
        errors.append(f"{relative}/hooks.json: `modules` names exactly one file in claude-hooks/")
    return errors


def lint_command_hooks(root: Path) -> list[str]:
    """Each other harness's manifest names only its own command-hooks file, and every command in
    one runs the publish hook script: no other command, and never claude-hooks/."""
    errors = []
    for name, (field, expected) in COMMAND_HOOKS.items():
        manifest = root / name
        if not manifest.is_file():
            continue
        document = json.loads(manifest.read_text(encoding="utf-8"))
        relative = manifest.relative_to(ROOT)
        if name == "plugin.json" and "hooks" in document:
            errors.append(f"{relative}: Agent Plugins has no top-level `hooks`; Codex's go under extensions.com.openai")
        value = document
        for key in field.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None and value != expected:
            errors.append(f"{relative}: `{field}` may only name {expected!r}")
        if value is not None and not (root / expected).is_file():
            errors.append(f"{relative}: `{field}` names {expected}, which does not exist")
    folder = root / "command-hooks"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        document = json.loads(path.read_text(encoding="utf-8"))
        handlers = [h for groups in document.get("hooks", {}).values() for group in groups for h in group.get("hooks", [group])]
        for handler in handlers:
            command = handler.get("command", "")
            if HOOK_SCRIPT not in command or "claude-hooks" in command or handler.get("type", "command") != "command":
                errors.append(f"{path.relative_to(ROOT)}: every hook runs {HOOK_SCRIPT}, and nothing else")
            if ":-" in command:
                errors.append(f"{path.relative_to(ROOT)}: a hook runs the script from its plugin root, and nothing when that is unset")
        if path.name == "cursor.json":
            shapes = {event: [h.get("command") for h in groups] for event, groups in document.get("hooks", {}).items()}
            if shapes != {event: [CURSOR_COMMAND.format(entry)] for event, entry in CURSOR_ENTRIES.items()}:
                errors.append(f"{path.relative_to(ROOT)}: each Cursor event runs its own entry from $CURSOR_PLUGIN_ROOT, and nothing when that is unset")
    copilot = root / "com.github.copilot" / "hooks" / "hooks.json"
    if copilot.is_file():
        document = json.loads(copilot.read_text(encoding="utf-8"))
        expected = {}
        for event, entry in (("preToolUse", "pre"), ("postToolUse", "post")):
            expected[event] = [{"type": "command",
                "bash": 'python3 "${COPILOT_PLUGIN_ROOT}/command-hooks/publish_hook.py" ' + entry + ' --host copilot',
                "powershell": 'python3 "$env:COPILOT_PLUGIN_ROOT/command-hooks/publish_hook.py" ' + entry + ' --host copilot',
                "timeoutSec": 30 if entry == "pre" else 10}]
        if document != {"version": 1, "hooks": expected}:
            errors.append(f"{copilot.relative_to(ROOT)}: Copilot hooks may only run the shared publication helper")
    return errors


def mod_names() -> set[str]:
    """The plugins plugin.toml declares as mods: the only folders that may hold hooks."""
    with (ROOT / "plugin.toml").open("rb") as handle:
        return {mod["name"] for mod in tomllib.load(handle).get("mods", [])}


def lint_mod(root: Path) -> list[str]:
    """A mod reaches Atlan through the core's server, and its hooks are one hooks module: settings
    hooks (shell commands) are the least portable component and stay out."""
    relative = root.relative_to(ROOT)
    errors = []
    for name in (".mcp.json", "mcp.json", "bin"):
        if (root / name).exists():
            errors.append(f"{relative}/{name}: a mod reaches Atlan through the core plugin's server")
    manifest = root / ".claude-plugin" / "plugin.json"
    if manifest.is_file() and "mcpServers" in json.loads(manifest.read_text(encoding="utf-8")):
        errors.append(f"{relative}/.claude-plugin/plugin.json: a mod declares no mcpServers")
    hooks = root / "hooks" / "hooks.json"
    if not hooks.is_file():
        return errors + [f"{relative}/hooks/hooks.json: a mod names its hooks module here"]
    document = json.loads(hooks.read_text(encoding="utf-8"))
    modules = document.get("modules")
    if set(document) - {"description", "modules"}:
        errors.append(f"{relative}/hooks/hooks.json: a mod has no settings hooks, only `modules`")
    if not (isinstance(modules, list) and len(modules) == 1 and (root / "hooks" / str(modules[0])).is_file()):
        errors.append(f"{relative}/hooks/hooks.json: `modules` names exactly one file in hooks/")
    return errors


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


def voice_scope(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return path.suffix in VOICE_SUFFIXES and not relative.startswith(VOICE_SKIPPED)


def lint_voice(path: Path, text: str) -> list[str]:
    """The marks and words AGENTS.md's Voice section bans, with the line each one is on."""
    relative = path.relative_to(ROOT)
    errors = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        prose = INLINE_CODE.sub("", line)
        if EM_DASH in prose:
            errors.append(f"{relative}:{number}: em dash; use a comma, a colon or a full stop")
        if SENTENCE_EXCLAIM.search(prose):
            errors.append(f"{relative}:{number}: exclamation mark; state it plainly")
        for match in FILLER.finditer(prose):
            errors.append(f"{relative}:{number}: filler {match.group(0)!r}; say what happens instead")
    return errors


def lint_drift(path: Path, text: str) -> list[str]:
    """Spellings of tool names and handles the live gateway does not serve."""
    relative = path.relative_to(ROOT)
    return [
        f"{relative}:{number}: {match.group(0)!r}: {why}"
        for number, line in enumerate(text.splitlines(), 1)
        for pattern, why in DRIFT
        for match in pattern.finditer(line)
    ]


def lint_repository(files: list[Path]) -> list[str]:
    errors = [
        f"{name}: plugin content lives in plugins/<plugin>/; the root is the marketplace"
        for name in PLUGIN_CONTENT
        if (ROOT / name).exists()
    ]
    mods = mod_names()
    for root in (ROOT, *plugin_roots()):
        relative = root.relative_to(ROOT).as_posix()
        if (root / "CLAUDE.md").exists():
            errors.append(f"{relative}/CLAUDE.md is not loaded by any host; use AGENTS.md at the root")
        if (root / "bin").exists():
            errors.append(f"{relative}/bin/ is refused by claude.ai and organization sync")
        if (root / "hooks").exists() and root.name not in mods:
            errors.append(f"{relative}/hooks/ is out of scope for these plugins; see AGENTS.md before adding one")
        if (root / "claude-hooks").exists() and root != plugin_roots()[0]:
            errors.append(f"{relative}/claude-hooks/ belongs to the core alone; a mod keeps its module in hooks/")
        if (root / "command-hooks").exists() and root != plugin_roots()[0]:
            errors.append(f"{relative}/command-hooks/ belongs to the core alone")
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
        for number, line in enumerate(text.splitlines(), 1):
            if match := NON_PRODUCTION.search(line):
                errors.append(f"{path.relative_to(ROOT)}:{number}: {match.group(0)!r} names a non-production "
                              "environment; this public repository names production only (AGENTS.md, Environments)")
        if voice_scope(path):
            errors += lint_voice(path, text)
        if path.suffix == ".md" and path.is_relative_to(PLUGINS):
            errors += lint_drift(path, text)
    return errors


def citable(roots: list[Path]) -> set[str]:
    """The names a skill may cite: every skill, and every subagent a plugin ships."""
    return {p.name for root in roots for p in (root / "skills").glob("*") if p.is_dir()} | {
        p.stem for root in roots for p in (root / "agents").glob("*.md")
    }


def main() -> int:
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
        errors += lint_skill(directory, True, skills, root)
    commands = [path for root in roots for top in ("commands", "agents") for path in sorted((root / top).glob("*.md"))]
    for path in commands:
        errors += lint_command(path)
    mods = mod_names()
    for root in roots[1:]:
        errors += lint_mod(root) if root.name in mods else lint_addon(root)
    for root in roots:
        errors += lint_claude_manifest(root, root == roots[0])
    errors += lint_core_hooks(roots[0])
    for root in roots:
        errors += lint_command_hooks(root)
    errors += lint_repository(tracked_files())

    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(f"ok: {len(skill_dirs)} skills, {len(commands)} commands and agents, {len(roots) - 1 - len(mods)} add-ons, {len(mods)} mods")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
