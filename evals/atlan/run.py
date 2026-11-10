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

After the groups run, one `suite:` line counts the cases that passed every grader and the cases
that passed every outcome grader, the tone graders (`TONE`) set apart; a group that left no
result is named and fails the run. Then metrics.py prints each run's cost against `budgets.json`
and writes `metrics.csv` beside the results; runs keep their traces (`--keep-temp`) for that.

The LLM graders are judged by `sonnet` unless the options name another `--judge-model`: the
eval's default judge failed replies that met every clause of a rubric.

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
# The model that judges the LLM graders; see the docstring.
JUDGE = "sonnet"
# The graders on how a reply reads, which every case carries; the rest grade what it did.
TONE = {"plain-reply", "reply-length", "one-next-step", "no-process-talk"}
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
    spaced = [extra[i + 1] for i, arg in enumerate(extra) if arg == "--case" and i + 1 < len(extra)]
    return spaced + [arg.split("=", 1)[1] for arg in extra if arg.startswith("--case=")]


def without_case_filters(extra: list[str]) -> list[str]:
    """The options for `claude plugin eval`, less `--case`: the copied suite already holds only the
    selected cases, and `claude plugin eval` keeps only the last `--case` it is given."""
    out, skip = [], False
    for arg in extra:
        if skip:
            skip = False
        elif arg == "--case":
            skip = True
        elif not arg.startswith("--case="):
            out.append(arg)
    return out


def measured(extra: list[str]) -> list[str]:
    """The options with `--keep-temp`, so each run's trace outlives it for metrics.py to read."""
    return extra if "--keep-temp" in extra else ["--keep-temp", *extra]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin", default=str(ROOT / "plugins" / PLUGIN), help="the plugin folder to evaluate")
    parser.add_argument("--label", default="", help="a name for this run's results folder")
    parser.add_argument("--baseline", action="store_true", help="also run every case without the plugin")
    parser.add_argument("--always-load", action="store_true", help="mark the emulated server alwaysLoad, so its tools are not deferred")
    parser.add_argument("extra", nargs="*", help="options passed to `claude plugin eval`, after --")
    args = parser.parse_args()

    selected = groups(HERE / "cases", case_filters(args.extra))
    if not selected:
        print("no case matches", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "results" / (f"{stamp}-{args.label}" if args.label else stamp)
    out.mkdir(parents=True)
    code = 0
    results = {}
    for number, (settings, cases) in enumerate(selected.values()):
        group_out = out if len(selected) == 1 else out / f"group-{number}"
        group_out.mkdir(exist_ok=True)
        code = run_group(args.plugin, settings, cases, group_out, args.extra, args.baseline, args.always_load) or code
        results[group_out / "result.json"] = [case.name for case in cases]
    code = summarize(results) or code
    sys.stdout.flush()
    subprocess.run([sys.executable, str(HERE / "metrics.py"), str(out), "--csv", str(out / "metrics.csv")], check=False)
    return code


def run_group(source: str, settings: dict, cases: list[Path], out: Path, extra: list[str], baseline: bool = False,
              always_load: bool = False) -> int:
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
        if always_load:
            server["alwaysLoad"] = True
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
            "--output-dir", str(out), *scaffold(cases), *judge(extra), *measured(without_case_filters(extra)),
        ]
        print("+", " ".join(command), flush=True)
        return subprocess.run(command, check=False).returncode


def judge(extra: list[str]) -> list[str]:
    """The judge for the LLM graders, unless the caller's own options name one."""
    named = any(arg == "--judge-model" or arg.startswith("--judge-model=") for arg in extra)
    return [] if named else ["--judge-model", JUDGE]


def scaffold(cases: list[Path]) -> list[str]:
    """`--scaffold` when a case stages its workspace with a `context.scaffold_script` of its own,
    such as a skill installed in the project; `claude plugin eval` runs none without it."""
    staged = any("scaffold_script:" in (case / "case.yaml").read_text(encoding="utf-8") for case in cases)
    return ["--scaffold"] if staged else []


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
        tone = " [tone only]" if failed and not errors and set(failed) <= TONE else ""
        lines.append(f"  {arm_score(runs):4.2f}{without}  {case['name']:<30} {', '.join(failed + errors)}{tone}")
    return lines


def right(case: dict) -> bool:
    """Whether every run of the case did the right thing: no error, and no failed grader but tone ones."""
    return all(not r.get("error") and all(g.get("passed") or g["name"] in TONE for g in r.get("graders", []))
               for r in case["arms"]["with"])


def summarize(results: dict[Path, list[str]]) -> int:
    """Each group's summary, then one line for the suite. 1 when a group left no result."""
    total = passed = outcome = 0
    lost = False
    for path, names in results.items():
        total += len(names)
        if not path.is_file():
            lost = True
            print(f"\nno result for {path.parent.name}: {', '.join(names)}")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        print("\n" + "\n".join(summary_lines(data)))
        passed += sum(all(r.get("passed") for r in case["arms"]["with"]) for case in data.get("cases", []))
        outcome += sum(right(case) for case in data.get("cases", []))
    print(f"\nsuite: {passed}/{total} cases passed every grader, {outcome}/{total} passed every outcome grader "
          f"(all but {', '.join(sorted(TONE))})")
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
