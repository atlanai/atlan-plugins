#!/usr/bin/env python3
"""Run the `atlanai` plugin's evals against the gateway emulator.

    python3 evals/atlan/run.py [--plugin plugins/atlanai] [--baseline] [-- <claude plugin eval options>]

`claude plugin eval` mocks cannot stand in for the Atlan gateway (its tool names contain `__`,
which a mock's name may not), so this copies the plugin to a temporary folder, points its
`atlan` server at `emulator/atlan_emulator.py` over stdio, adds `cases/` as the eval suite and
runs `claude plugin eval` on the copy with real servers on. Results land in
evals/atlan/results/. Each case is a full Claude Code run on your own credential: it costs
money and is not part of `make check`. `--plugin` evaluates another checkout of the plugin,
for a before-and-after comparison. `--baseline` adds a no-plugin arm (`--ablation
with-without`): each case runs again without the plugin, and the summary prints both scores,
so a skill that does no better than no skill at all shows up. Run it before a release.

Cases tagged `negative` are prompts the plugin must leave alone: no Atlan skill loads and no
Atlan tool is called. They keep shorter descriptions from widening what the skills claim.

A case that needs the emulator in another state carries an `emulator.json` beside its
`case.yaml`:

    {"env": {"ATLAN_EMULATOR_HIDE_SERVERS": "extension__agent"},   # the emulator's switches
     "plugin_name": "atlan",                                       # install the copy under this name
     "allow_tools": ["mcp__plugin_atlan_atlan__*"],                # beyond the plugin's own server
     "installed_skills": ["security-review"]}                      # from fixtures/installed-skills/

`installed_skills` copies each named folder from `fixtures/installed-skills/` into the plugin
copy's `skills/`, so the run starts with a skill of that name already installed, as a user who
installed a registry skill earlier would.

The emulator is started once per Claude Code run with the plugin's `.mcp.json` environment, so
cases are grouped by their `emulator.json` and each group is one `claude plugin eval` run over
one copy of the plugin. The cases without one are the default group.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVER = "atlan"
PLUGIN = "atlanai"
SIDECAR_KEYS = {"env", "plugin_name", "allow_tools", "installed_skills"}
INSTALLED = HERE / "fixtures" / "installed-skills"


def tools_pattern(plugin: str) -> str:
    """Claude Code's name for a plugin's MCP tools: mcp__plugin_<plugin>_<server>__<tool>."""
    return f"mcp__plugin_{plugin}_{SERVER}__*"


def groups(cases: Path, wanted: list[str]) -> dict[str, tuple[dict, list[Path]]]:
    """The cases to run, grouped by their `emulator.json` (the default group's key is "")."""
    out: dict[str, tuple[dict, list[Path]]] = {}
    for case in sorted(p for p in cases.iterdir() if (p / "case.yaml").is_file()):
        if wanted and not any(fnmatch.fnmatchcase(case.name, pattern) for pattern in wanted):
            continue
        sidecar = case / "emulator.json"
        settings = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
        unknown = set(settings) - SIDECAR_KEYS
        if unknown:
            sys.exit(f"{sidecar}: unknown keys {sorted(unknown)}")
        key = json.dumps(settings, sort_keys=True) if settings else ""
        out.setdefault(key, (settings, []))[1].append(case)
    return out


def case_filters(extra: list[str]) -> list[str]:
    """The `--case <glob>` filters among the options passed through to `claude plugin eval`."""
    return [extra[i + 1] for i, arg in enumerate(extra) if arg == "--case" and i + 1 < len(extra)]


def without_case_filters(extra: list[str]) -> list[str]:
    """The options for `claude plugin eval`, less `--case`: the copied suite already holds only the
    selected cases, and `claude plugin eval` keeps only the last `--case` it is given."""
    out, skip = [], False
    for arg in extra:
        if skip:
            skip = False
        elif arg == "--case":
            skip = True
        else:
            out.append(arg)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin", default=str(ROOT / "plugins" / PLUGIN), help="the plugin folder to evaluate")
    parser.add_argument("--label", default="", help="a name for this run's results folder")
    parser.add_argument("--baseline", action="store_true", help="also run every case without the plugin")
    parser.add_argument("extra", nargs="*", help="options passed to `claude plugin eval`, after --")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "results" / (f"{stamp}-{args.label}" if args.label else stamp)
    out.mkdir(parents=True)
    selected = groups(HERE / "cases", case_filters(args.extra))
    if not selected:
        print("no case matches", file=sys.stderr)
        return 2
    code = 0
    results = []
    for number, (settings, cases) in enumerate(selected.values()):
        group_out = out if len(selected) == 1 else out / f"group-{number}"
        group_out.mkdir(exist_ok=True)
        code = run_group(args.plugin, settings, cases, group_out, args.extra, args.baseline) or code
        results.append(group_out / "result.json")
    for path in results:
        summarize(path)
    return code


def run_group(source: str, settings: dict, cases: list[Path], out: Path, extra: list[str], baseline: bool = False) -> int:
    name = settings.get("plugin_name", PLUGIN)
    with tempfile.TemporaryDirectory(prefix="atlan-eval-") as tmp:
        plugin = Path(tmp) / name
        shutil.copytree(source, plugin, ignore=shutil.ignore_patterns("evals"))
        if name != PLUGIN:
            manifest = plugin / ".claude-plugin" / "plugin.json"
            manifest.write_text(json.dumps(json.loads(manifest.read_text(encoding="utf-8")) | {"name": name}, indent=2) + "\n",
                                encoding="utf-8")
        for skill in settings.get("installed_skills", []):
            shutil.copytree(INSTALLED / skill, plugin / "skills" / skill)
        emulator = HERE / "emulator" / "atlan_emulator.py"
        env = {"ATLAN_EMULATOR_LOG": str(out / "emulator.jsonl"), **settings.get("env", {})}
        server = {"type": "stdio", "command": sys.executable, "args": [str(emulator)], "env": env}
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {SERVER: server}}, indent=2) + "\n", encoding="utf-8")
        suite = plugin / "evals"
        suite.mkdir()
        for case in cases:
            shutil.copytree(case, suite / case.name)
        command = [
            "claude", "plugin", "eval", str(plugin),
            "--trust-plugin", "--mocks", "off",
            "--allow-tools", tools_pattern(name), *settings.get("allow_tools", []), "Write",
            "--ablation", ablation(baseline), "--no-publish",
            "--json", str(out / "result.json"), "--report", str(out / "report.html"),
            "--output-dir", str(out), *without_case_filters(extra),
        ]
        print("+", " ".join(command), flush=True)
        return subprocess.run(command, check=False).returncode


def ablation(baseline: bool) -> str:
    """`claude plugin eval`'s arms: the plugin alone, or the plugin and a no-plugin baseline."""
    return "with-without" if baseline else "none"


def arm_score(runs: list[dict]) -> float:
    return sum(r.get("score", 0) for r in runs) / max(len(runs), 1)


def summary_lines(data: dict) -> list[str]:
    """One line for the run, then one per case: its score, the baseline's when there is one, and
    the graders and errors that failed."""
    cases = data.get("cases", [])
    baseline = any(case.get("arms", {}).get("without") for case in cases)
    aggregates = data.get("aggregates", {})
    lines = [f"overall {aggregates.get('overallScore', 0):.2f}, "
             f"{aggregates.get('casesPassed', 0)}/{aggregates.get('casesTotal', 0)} cases passed"
             + (", second column is the no-plugin baseline" if baseline else "")]
    for case in cases:
        runs = case["arms"]["with"]
        failed = sorted({g["name"] for r in runs for g in r.get("graders", []) if not g.get("passed")})
        errors = sorted({r["error"] for r in runs if r.get("error")})
        without = f"  {arm_score(case['arms']['without']):4.2f}" if case["arms"].get("without") else ("     -" if baseline else "")
        lines.append(f"  {arm_score(runs):4.2f}{without}  {case['name']:<30} {', '.join(failed + errors)}")
    return lines


def summarize(path: Path) -> None:
    if not path.is_file():
        return
    print("\n" + "\n".join(summary_lines(json.loads(path.read_text(encoding="utf-8")))))


if __name__ == "__main__":
    sys.exit(main())
