#!/usr/bin/env python3
"""Carry the registry's own authoring recipes into this plugin, verbatim, at pinned revisions.

The skills and commands under the paths in upstream.json are written by the apps that own the
tools they describe (the registry's files module, the skill app, the agent app). Those apps serve
the same files as MCP prompts and resources and in the registry's Claude Code umbrella plugin.
This plugin carries them so a host that loads skills but not MCP prompts gets them too.

    python3 scripts/sync_upstream.py --check
        Hermetic. Every vendored file exists and matches the sha256 upstream.json records, and
        no file this repository owns collides with one. CI runs this.

    python3 scripts/sync_upstream.py --from skill=../app-skill --from agent=../app-agent ...
        Re-copy each named source at its pinned revision. The checkout needs that commit; it is
        read with `git show`, so the working tree can be on any branch.

    python3 scripts/sync_upstream.py --from skill=../app-skill --adopt-head
        Move the named sources' pins to their checkouts' HEAD, then copy. This is how a pin moves.

Never edit a vendored file here: change it in the owning repository, then move the pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "upstream.json"
# The plugin-relative trees a static contribution may hold (the registry's projection contract).
TREES = ("skills", "commands", "agents")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save(manifest: dict) -> None:
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def git(checkout: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {checkout}: {result.stderr.decode().strip()}")
    return result.stdout


def upstream_files(checkout: Path, rev: str, base: str) -> dict[str, bytes]:
    """Plugin-relative path to content, for every file under the source's trees at `rev`."""
    listing = git(checkout, "ls-tree", "-r", "-z", "--name-only", rev, "--", base).decode()
    files = {}
    for name in filter(None, listing.split("\0")):
        relative = PurePosixPath(name).relative_to(base)
        if relative.parts[0] not in TREES:
            continue
        if ".." in relative.parts:
            raise SystemExit(f"refusing path {name}")
        files[str(relative)] = git(checkout, "show", f"{rev}:{name}")
    return files


def check(manifest: dict) -> list[str]:
    problems = []
    sources = {source["name"] for source in manifest["sources"]}
    for path, entry in sorted(manifest["files"].items()):
        if entry["source"] not in sources:
            problems.append(f"{path}: names unknown source {entry['source']!r}")
        target = ROOT / path
        if not target.is_file():
            problems.append(f"{path}: missing (vendored from {entry['source']})")
        elif sha256(target.read_bytes()) != entry["sha256"]:
            problems.append(
                f"{path}: differs from {entry['source']} at its pin; change it upstream, then re-sync"
            )
    return problems


def sync(manifest: dict, checkouts: dict[str, Path], adopt_head: bool) -> None:
    by_name = {source["name"]: source for source in manifest["sources"]}
    unknown = set(checkouts) - set(by_name)
    if unknown:
        raise SystemExit(f"unknown source(s): {', '.join(sorted(unknown))}")

    for name, checkout in checkouts.items():
        source = by_name[name]
        if adopt_head:
            source["rev"] = git(checkout, "rev-parse", "HEAD").decode().strip()
        files = upstream_files(checkout, source["rev"], source["path"])
        if not files:
            raise SystemExit(f"{name}: nothing under {source['path']} at {source['rev']}")

        previous = {p for p, e in manifest["files"].items() if e["source"] == name}
        for path in previous - set(files):
            (ROOT / path).unlink(missing_ok=True)
            del manifest["files"][path]
            print(f"removed {path}")

        for path, content in sorted(files.items()):
            owner = manifest["files"].get(path, {}).get("source")
            if owner not in (None, name):
                raise SystemExit(f"{path} is vendored from both {owner} and {name}")
            target = ROOT / path
            if owner is None and target.exists():
                raise SystemExit(f"{path} exists here and in {name}; rename one of them")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != content:
                target.write_bytes(content)
                print(f"wrote {path}")
            manifest["files"][path] = {"source": name, "sha256": sha256(content)}

    manifest["files"] = dict(sorted(manifest["files"].items()))
    save(manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify vendored files; no checkouts")
    parser.add_argument(
        "--from",
        dest="checkouts",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="a local git checkout of one source in upstream.json",
    )
    parser.add_argument("--adopt-head", action="store_true", help="move each pin to its checkout's HEAD")
    args = parser.parse_args(argv)

    manifest = load()
    if args.check:
        problems = check(manifest)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0

    if not args.checkouts:
        parser.error("pass --check, or at least one --from NAME=PATH")
    checkouts = {}
    for item in args.checkouts:
        name, _, path = item.partition("=")
        if not path:
            parser.error(f"--from {item!r} is not NAME=PATH")
        checkouts[name] = Path(path).expanduser().resolve()
    sync(manifest, checkouts, args.adopt_head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
