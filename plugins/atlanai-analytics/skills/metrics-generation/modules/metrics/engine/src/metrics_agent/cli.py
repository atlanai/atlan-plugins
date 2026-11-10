# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The `metrics-agent` command.

    metrics-agent run …            the whole pipeline (what `scripts/run.py` runs)
    metrics-agent stage <name> …   one stage, e.g. `metrics-agent stage s0_inputs --run out/r`
    metrics-agent stages           list the stages
    metrics-agent setup …          the set-up interview: writes run.yml (--check tests one)
    metrics-agent plugs [--json]   the installed plugs: kind, custody, capabilities, env names
    metrics-agent --version        the installed version
"""
from __future__ import annotations

import importlib
import json
import sys

from metrics_agent import _paths

USAGE = __doc__


def stage_names() -> list[str]:
    return [n for n in _paths.stage_names() if n != "run"]


def _run_module(name: str, argv: list[str]) -> int:
    module = importlib.import_module(_paths.stage_module(name))
    # A stage reads its own arguments from sys.argv, as it does when run as a script, and
    # names itself in usage and error messages by its historical file name.
    sys.argv = [f"{name}.py", *argv]
    try:
        result = module.main()
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            return exc.code or 0
        # A stage refuses with `SystemExit("why")`: say why, as Python itself would.
        print(exc.code, file=sys.stderr)
        return 1
    return result if isinstance(result, int) else 0


def _plugs(argv: list[str]) -> int:
    from metrics_agent import plugs

    try:
        rows = plugs.listing()
    except plugs.PlugError as exc:
        print(f"metrics-agent plugs: {exc}", file=sys.stderr)
        return 2
    broken = any("error" in r for r in rows)
    if "--json" in argv:
        print(json.dumps(rows, indent=2))
        return 1 if broken else 0
    if not rows:
        print("no plugs installed")
        return 0
    for r in rows:
        head = f"{r['name']}  ({r.get('kind', '?')}, {r['package']} {r['version']})"
        print(head)
        if "error" in r:
            print(f"    error: {r['error']}")
            continue
        if "custody" in r:
            print(f"    custody: {r['custody']}")
            print(f"    capabilities: {', '.join(r['capabilities']) or 'none'}")
        if "lane" in r:
            print(f"    lane: {r['lane']}")
        print(f"    reads: {', '.join(r['requires_env']) or 'no environment variables'}")
    return 1 if broken else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "run":
        return _run_module("run", rest)
    if cmd in ("-V", "--version"):
        from metrics_agent import __version__

        print(f"metrics-agent {__version__}")
        return 0
    if cmd == "stage":
        if not rest or rest[0] not in stage_names():
            if rest:
                # A stage a plug package adds is missing when that package is not installed;
                # the core does not know which package that is, so it says what it has.
                print(f"metrics-agent stage: no stage {rest[0]!r} is installed. It is neither "
                      "a core stage nor one an installed plug package adds; install the plug "
                      "package that provides it.", file=sys.stderr)
            print("usage: metrics-agent stage <name> [args]\nstages: " + ", ".join(stage_names()),
                  file=sys.stderr)
            return 2
        return _run_module(rest[0], rest[1:])
    if cmd == "stages":
        print("\n".join(stage_names()))
        return 0
    if cmd == "plugs":
        return _plugs(rest)
    if cmd == "setup":
        from metrics_agent import setup

        return setup.main(rest)
    print(f"unknown command {cmd!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
