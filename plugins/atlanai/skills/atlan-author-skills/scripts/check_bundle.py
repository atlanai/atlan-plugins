#!/usr/bin/env python3
"""Check a skill folder before publishing it to Atlan. Standard library only.

    python3 check_bundle.py <skill-folder> [<skill-folder> ...]   # one line per problem
    python3 check_bundle.py --json <skill-folder> ...             # the same, as JSON

Run it on the folder you are about to send as inline `files` in `extension__skill__create`. Several
folders are checked together, because the inline limits (128 files, 256 KiB) are per call. Exit 0
when the folders can be sent as they are, 1 when one cannot, 2 when a folder does not exist.

It catches what the server cannot see or refuses only after a round trip:

- no root `SKILL.md`, frontmatter without `name` or `description`, frontmatter the registry's
  YAML parser refuses (a plain value holding `: `, an unclosed quote or bracket, a tab used to
  indent, a key given twice), or no Markdown body after the closing `---`;
- a path the create refuses (absolute, `..`, a backslash or colon, more than eight levels);
- more than 128 files or 256 KiB in total, a symbolic link, or a file that is not UTF-8 text
  (those go through the ZIP route);
- characters a reader cannot see: direction controls, zero-width characters, a joiner or a
  variation selector outside an emoji, tag characters outside a flag, and control characters.
  A tool call that decodes `\\ufe0f` into the character itself puts one in a file unseen;
- credential-shaped strings and key files, since tool arguments can be kept in audit records.

Each problem names its file, line and column (1-based, in characters) and the fix. Credential
matches are never printed. `.git`, `__pycache__`, `node_modules` and `.DS_Store` are skipped, as
the publish helper skips them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_FILES = 128
MAX_BYTES = 256 * 1024
MAX_DEPTH = 8
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKIPPED_DIRS = {".git", "__pycache__", "node_modules"}
SKIPPED_FILES = {".DS_Store"}
# Files that hold credentials by design: keys, dotenv, package-registry and machine logins, and
# Terraform variables and state.
KEY_FILES = re.compile(
    r"(?:^\.env(?:\..+)?$|\.(?:pem|key|p12|pfx|tfvars|tfstate)$|\.tfvars\.json$|\.tfstate\.backup$"
    r"|^id_(?:rsa|dsa|ecdsa|ed25519)$|^(?:\.npmrc|\.netrc|_netrc|\.pgpass|\.pypirc|\.git-credentials)$)",
    re.I,
)
# A value that opens like a placeholder, a lookup or a stand-in is not a secret: `<…>`, `${…}`,
# `{{…}}`, `$VAR`, `***`, `%s`, or a word such as `example` or `changeme`.
NOT_SECRET = r"[$<{*%]|(?:synthetic|example|dummy|fake|placeholder|changeme|redacted|your)"
# The same patterns tools/lint.py refuses in this repository.
# A word boundary is read as bundle.ts reads it, between ASCII letters only, so a token that
# follows a letter of another script is still found.
SECRETS = [
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", re.ASCII)),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b", re.ASCII)),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", re.ASCII)),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b", re.ASCII)),
    ("API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}\b", re.ASCII)),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", re.ASCII)),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}", re.ASCII)),
    ("Google OAuth client secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{28}", re.ASCII)),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{24,}", re.ASCII)),
    ("password in a URL", re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9+.-]*://[^\s/:@<>{}$]+:(?!(?:password|passwd|pass|pwd|pw|secret|x+|\*+)@)(?![$<{%*])[^\s/@<>{}$]+@", re.I)),
    ("password", re.compile(
        r"(?<![A-Za-z0-9])[A-Za-z0-9_]*(?:password|passwd|client_secret|accountkey|sharedaccesskey)[\"']?[ \t]*[:=][ \t]*"
        rf"(?:\"(?!{NOT_SECRET})[^\"\s]{{8,}}\"|'(?!{NOT_SECRET})[^'\s]{{8,}}'"
        rf"|(?!{NOT_SECRET})(?=[^\s\"'(),;&\[\]]*\d)[^\s\"'(),;&\[\]]{{8,}}(?=[\s;,&]|$))",
        re.I,
    )),
]

DIRECTION = {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
ZERO_WIDTH = {0x200B, 0x200C, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x180E, 0xFEFF}
SEPARATORS = {0x2028, 0x2029}
ZWJ, VS15, VS16, KEYCAP, BLACK_FLAG, CANCEL_TAG = 0x200D, 0xFE0E, 0xFE0F, 0x20E3, 0x1F3F4, 0xE007F


def emoji_base(cp: int) -> bool:
    """Whether `cp` can carry an emoji presentation: pictographs, symbols and keycap bases."""
    return (
        0x1F000 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x2300 <= cp <= 0x23FF
        or 0x2B00 <= cp <= 0x2BFF
        or 0x2190 <= cp <= 0x21FF
        or 0x2100 <= cp <= 0x214F
        or 0x25A0 <= cp <= 0x25FF
        or cp in {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2934, 0x2935, 0x3030, 0x303D, 0x3297, 0x3299}
    )


def hidden(text: str) -> list[tuple[int, str, str]]:
    """Every character in `text` a reader cannot see, as (index, rule, what to do)."""
    found = []
    cps = [ord(c) for c in text]
    tag_run = False
    for i, cp in enumerate(cps):
        prev = cps[i - 1] if i else None
        nxt = cps[i + 1] if i + 1 < len(cps) else None
        if 0xE0020 <= cp <= 0xE007F:
            tag_run = tag_run or prev == BLACK_FLAG
            if not tag_run:
                found.append((i, "tag-character", f"U+{cp:04X} is a tag character outside a flag; delete it"))
            if cp == CANCEL_TAG:
                tag_run = False
            continue
        tag_run = False
        if cp in (VS15, VS16):
            keycap = prev is not None and chr(prev) in "0123456789#*" and nxt == KEYCAP
            if not (prev is not None and (emoji_base(prev) or keycap)):
                found.append((i, "variation-selector",
                              f"U+{cp:04X} follows a character that is not an emoji; delete it"))
        elif 0xFE00 <= cp <= 0xFE0D or 0xE0100 <= cp <= 0xE01EF:
            found.append((i, "variation-selector", f"U+{cp:04X} is a variation selector; delete it"))
        elif cp == ZWJ:
            before = prev is not None and (emoji_base(prev) or prev == VS16 or 0x1F3FB <= prev <= 0x1F3FF)
            if not (before and nxt is not None and emoji_base(nxt)):
                found.append((i, "zero-width-joiner", "U+200D joins characters that are not emoji; delete it"))
        elif cp in DIRECTION:
            found.append((i, "direction-control", f"U+{cp:04X} reorders the text a reader sees; delete it"))
        elif cp in ZERO_WIDTH:
            where = "at the start of the file (a byte order mark)" if cp == 0xFEFF and i == 0 else "here"
            found.append((i, "zero-width", f"U+{cp:04X} is invisible {where}; delete it"))
        elif cp in SEPARATORS:
            found.append((i, "line-separator", f"U+{cp:04X} is an invisible line break; use a newline"))
        elif (cp < 0x20 and cp not in (0x09, 0x0A, 0x0D)) or cp == 0x7F or 0x80 <= cp <= 0x9F:
            found.append((i, "control-character", f"U+{cp:04X} is a control character; delete it"))
    return found


def position(text: str, index: int) -> tuple[int, int]:
    """1-based line and column (in characters) of `text[index]`."""
    line = text.count("\n", 0, index) + 1
    start = text.rfind("\n", 0, index) + 1
    return line, index - start + 1


def credentials(text: str) -> list[tuple[int, int, str]]:
    """Every credential shape in `text`, as (line, column, kind)."""
    return [(*position(text, match.start()), kind) for kind, pattern in SECRETS for match in pattern.finditer(text)]


def frontmatter(text: str) -> dict[str, str] | None:
    """The top-level scalar keys of a `---` block, enough to see `name` and `description`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    keys: dict[str, str] = {}
    block: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            return keys
        if block and (line.startswith((" ", "\t")) or not line.strip()):
            keys[block] = (keys[block] + " " + line.strip()).strip()
            continue
        block = None
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value[:1] in ("|", ">"):
            keys[key], block = "", key
        else:
            keys[key] = "" if value == "~" else value.strip("'\"")
    return None


_KEY = re.compile(r"""^(?:"[^"]*"|'[^']*'|[^\s#'"\[\]{},:&*!|>%@`-][^:#]*?)\s*:(?:[ \t]+(.*)|[ \t]*)$""")


def _closes(value: str, quote: str) -> int | None:
    """The index just past the quoted scalar opening `value`, or None while it is unclosed."""
    i = 1
    while i < len(value):
        c = value[i]
        if quote == '"' and c == "\\":
            i += 2
            continue
        if c == quote:
            if quote == "'" and value[i + 1:i + 2] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return None


def _value_problem(value: str) -> str | None:
    """Why a top-level value, with any continuation lines joined, is not YAML the registry reads."""
    v = value.strip()
    if not v or v[0] == "#":
        return None
    if v[0] in "\"'":
        end = _closes(v, v[0])
        if end is None:
            return f"the {v[0]} quote is never closed"
        rest = v[end:].strip()
        if rest and not rest.startswith("#"):
            return "text follows the closing quote; quote the whole value"
        return None
    if v[0] in "[{":
        depth, quote = 0, None
        for i, c in enumerate(v):
            if quote:
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    rest = v[i + 1:].strip()
                    return None if not rest or rest.startswith("#") else "text follows the closing bracket"
        return f"the {v[0]} is never closed"
    if v[0] in "|>":
        return None if re.match(r"^[|>][-+0-9]*\s*(?:#.*)?$", v) else "a block scalar's indicator line holds text"
    if v[0] in "@`":
        return f"a plain value cannot start with {v[0]}; quote it"
    plain = re.split(r"\s#", v, maxsplit=1)[0]
    if re.search(r":(?:\s|$)", plain):
        return "a plain value holds `: `; put the value in double quotes or use `>`"
    return None


def frontmatter_problems(text: str) -> list[tuple[int, int, str, str]]:
    """What the registry's parser refuses in SKILL.md's frontmatter and body, as (line, column,
    rule, fix). The registry reads the block as YAML with duplicate keys refused, and needs a
    non-empty body; this checks the subset of YAML a SKILL.md uses, not the whole language."""
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if not lines or lines[0].strip() != "---" or end is None:
        return []  # `frontmatter` reports a missing or unterminated block
    found: list[tuple[int, int, str, str]] = []
    seen: dict[str, int] = {}
    block = lines[1:end]
    i = 0
    while i < len(block):
        line, number = block[i], i + 2
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in indent:
            found.append((number, indent.index("\t") + 1, "frontmatter-yaml", "a tab indents this line; YAML indents with spaces"))
            continue
        if indent:
            continue  # inside a block scalar, a list or a map: its key's line was checked
        match = _KEY.match(line)
        if not match:
            found.append((number, 1, "frontmatter-yaml", "is not a `key: value` line; fix it or indent it under its key"))
            continue
        key = line.split(":", 1)[0].strip().strip("\"'")
        if key in seen:
            found.append((number, 1, "frontmatter-yaml", f"`{key}` is given twice (first on line {seen[key]}); keep one"))
        seen.setdefault(key, number)
        value = match.group(1) or ""
        # A quoted or bracketed value may run on over indented lines: join them before judging it.
        while i < len(block) and block[i][:1] in (" ", "\t") and value.strip()[:1] in ("\"", "'", "[", "{"):
            value += " " + block[i].strip()
            i += 1
        why = _value_problem(value)
        if why:
            found.append((number, line.index(":") + 2, "frontmatter-yaml", f"{why}"))
    if not "\n".join(lines[end + 1:]).strip():
        found.append((end + 1, 1, "no-body", "nothing follows the frontmatter; the registry needs Markdown instructions after the closing `---`"))
    return found


def path_problem(rel: str) -> str | None:
    parts = rel.split("/")
    if len(parts) > MAX_DEPTH:
        return f"is {len(parts)} levels deep; the create takes at most {MAX_DEPTH}"
    if any(part in ("", ".", "..") for part in parts) or "\\" in rel or ":" in rel:
        return "is not a plain relative path; rename it"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in rel):
        return "has a control character in its name; rename it"
    return None


def walk(root: Path) -> tuple[list[tuple[str, Path]], list[dict]]:
    """The files the publish helper would send from `root`, and the entries it refuses."""
    files, problems = [], []
    if root.is_symlink():
        return [], [problem(root.name, "symbolic-link", "the folder is a symbolic link; name the real folder")]
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(part in SKIPPED_DIRS for part in path.relative_to(root).parts) or path.name in SKIPPED_FILES:
            continue
        if path.is_symlink():
            problems.append(problem(rel, "symbolic-link", "is a symbolic link; copy the file in, or use the ZIP route"))
        elif path.is_file():
            files.append((rel, path))
    return files, problems


def problem(path: str, rule: str, fix: str, line: int | None = None, column: int | None = None) -> dict:
    return {"path": path, "line": line, "column": column, "rule": rule, "fix": fix}


def check(roots: list[Path]) -> dict:
    folders = []
    for root in roots:
        files, refused = walk(root)
        has_skill_md = (root / "SKILL.md").is_file() and not (root / "SKILL.md").is_symlink()
        folders.append((root.name, [(rel, path.read_bytes()) for rel, path in files], refused, has_skill_md))
    return check_files(folders)


def check_bulk_items(data: bytes, kind: str, workspace: str) -> list:
    """Validate a bounded metric/query batch using the same text and credential checks as skills."""
    import json

    if kind not in ("metric", "sql_query") or not isinstance(workspace, str) or not workspace:
        raise ValueError("Choose a metric or saved-query kind and one workspace.")
    if len(data) > MAX_BYTES:
        raise ValueError("The payload exceeds 256 KiB; prepare smaller batches.")
    items = json.loads(data.decode("utf-8"))
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("A batch must contain 1 to 100 items.")
    identities = set()
    for item in items:
        if (not isinstance(item, dict) or item.get("workspace_id") != workspace
                or not isinstance(item.get("definition"), dict)
                or not isinstance(item.get("name"), str) or not item["name"]
                or not isinstance(item.get("source_id"), str) or not item["source_id"]):
            raise ValueError("Every item needs a definition, name, source identity and the selected workspace.")
        if item["source_id"] in identities:
            raise ValueError("A batch repeats a source identity.")
        identities.add(item["source_id"])

    # Decode JSON escapes before scanning; otherwise an escaped token could evade the scanner.
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for part in value:
                yield from strings(part)
        elif isinstance(value, dict):
            for key, part in value.items():
                yield key
                yield from strings(part)

    for text in (json.dumps(items, ensure_ascii=False, allow_nan=False, separators=(",", ":")), "\n".join(strings(items))):
        checked = check_files([("payload", [("items.txt", text.encode("utf-8"))], [], True)])
        if not checked["ok"]:
            raise ValueError("The payload failed the text or credential checks; inspect it locally.")
    return items


def check_files(folders: list[tuple[str, list[tuple[str, bytes]], list[dict], bool]]) -> dict:
    """The checks on bytes already read: `(name, [(path, bytes)], walk problems, has SKILL.md)` per
    folder. The command hook reads each file once and checks the bytes it sends."""
    problems: list[dict] = []
    count = total = 0
    for name, files, refused, has_skill_md in folders:
        problems += refused
        if not has_skill_md:
            problems.append(problem("SKILL.md", "missing-skill-md", f"{name} has no SKILL.md at its root"))
        for rel, data in files:
            count += 1
            total += len(data)
            label = rel if len(folders) == 1 else f"{name}/{rel}"
            bad = path_problem(rel)
            if bad:
                problems.append(problem(label, "unsafe-path", f"the path {bad}"))
            if KEY_FILES.search(rel.rsplit("/", 1)[-1]):
                problems.append(problem(label, "credential-file", "looks like a credential file; leave it out"))
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(problem(label, "not-text", "is not UTF-8 text; send this bundle through the ZIP route"))
                continue
            for index, rule, fix in hidden(text):
                line, column = position(text, index)
                problems.append(problem(label, rule, fix, line, column))
            for line, column, kind in credentials(text):
                problems.append(problem(label, "credential", f"matches the {kind} pattern; remove it", line, column))
            if rel == "SKILL.md":
                keys = frontmatter(text)
                if keys is None:
                    problems.append(problem(label, "frontmatter", "needs a `---` block with `name` and `description` at the top", 1, 1))
                else:
                    name, description = keys.get("name", ""), keys.get("description", "")
                    if not NAME.match(name) or len(name) > 64:
                        problems.append(problem(label, "frontmatter-name",
                                                "`name` must be lowercase letters, digits and single hyphens, at most 64", 1, 1))
                    if not description:
                        problems.append(problem(label, "frontmatter-description",
                                                "`description` is missing; say what the skill does and when to use it", 1, 1))
                    elif len(description) > 1024:
                        problems.append(problem(label, "frontmatter-description", "`description` is over 1024 characters", 1, 1))
                    for line, column, rule, fix in frontmatter_problems(text):
                        problems.append(problem(label, rule, fix, line, column))
    if count > MAX_FILES:
        problems.append(problem("", "too-many-files", f"{count} files; inline `files` take at most {MAX_FILES} a call, so use the ZIP route"))
    if total > MAX_BYTES:
        problems.append(problem("", "too-large", f"{total} bytes; inline `files` take at most {MAX_BYTES} a call, so use the ZIP route"))
    return {"ok": not problems, "files": count, "bytes": total, "problems": problems}


def render(result: dict) -> str:
    lines = []
    for p in result["problems"]:
        where = ":".join(str(part) for part in (p["path"], p["line"], p["column"]) if part not in (None, ""))
        lines.append(f"{where}: {p['rule']}: {p['fix']}" if where else f"{p['rule']}: {p['fix']}")
    verdict = "ready to send" if result["ok"] else f"{len(result['problems'])} problem(s); fix them before sending"
    lines.append(f"{result['files']} files, {result['bytes']} bytes: {verdict}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    roots = [Path(a) for a in argv if a != "--json"]
    if not roots:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        print(f"not a folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    result = check(roots)
    print(json.dumps(result, indent=2) if as_json else render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
