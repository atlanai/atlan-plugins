#!/usr/bin/env python3
"""Run the `atlanai` plugin's evals against the gateway emulator.

    python3 evals/atlan/run.py [--plugin plugins/atlanai] [-- <claude plugin eval options>]

`claude plugin eval` mocks cannot stand in for the Atlan gateway (its tool names contain `__`,
which a mock's name may not), so this copies the plugin to a temporary folder, points its
`atlan` server at `emulator/atlan_emulator.py` over stdio, adds `cases/` as the eval suite and
runs `claude plugin eval` on the copy with real servers on. Results land in
evals/atlan/results/. Each case is a full Claude Code run on your own credential: it costs
money and is not part of `make check`. `--plugin` evaluates another checkout of the plugin,
for a before-and-after comparison.
"""

from __future__ import annotations

import argparse
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
# Claude Code's name for a plugin's MCP tools: mcp__plugin_<plugin>_<server>__<tool>.
TOOLS = f"mcp__plugin_atlanai_{SERVER}__*"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin", default=str(ROOT / "plugins" / "atlanai"), help="the plugin folder to evaluate")
    parser.add_argument("--label", default="", help="a name for this run's results folder")
    parser.add_argument("extra", nargs="*", help="options passed to `claude plugin eval`, after --")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "results" / (f"{stamp}-{args.label}" if args.label else stamp)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="atlan-eval-") as tmp:
        plugin = Path(tmp) / "atlanai"
        shutil.copytree(args.plugin, plugin, ignore=shutil.ignore_patterns("evals"))
        emulator = HERE / "emulator" / "atlan_emulator.py"
        server = {"type": "stdio", "command": sys.executable, "args": [str(emulator)],
                  "env": {"ATLAN_EMULATOR_LOG": str(out / "emulator.jsonl")}}
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {SERVER: server}}, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(HERE / "cases", plugin / "evals")
        command = [
            "claude", "plugin", "eval", str(plugin),
            "--trust-plugin", "--mocks", "off", "--allow-tools", TOOLS, "Write",
            "--ablation", "none", "--no-publish",
            "--json", str(out / "result.json"), "--report", str(out / "report.html"),
            "--output-dir", str(out), *args.extra,
        ]
        print("+", " ".join(command), flush=True)
        code = subprocess.run(command, check=False).returncode
    summarize(out / "result.json")
    return code


def summarize(path: Path) -> None:
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"\noverall {data['aggregates'].get('overallScore', 0):.2f}, "
          f"{data['aggregates'].get('casesPassed', 0)}/{data['aggregates'].get('casesTotal', 0)} cases passed")
    for case in data.get("cases", []):
        runs = case["arms"]["with"]
        failed = sorted({g["name"] for r in runs for g in r.get("graders", []) if not g.get("passed")})
        errors = sorted({r["error"] for r in runs if r.get("error")})
        score = sum(r.get("score", 0) for r in runs) / max(len(runs), 1)
        print(f"  {score:4.2f}  {case['name']:<30} {', '.join(failed + errors)}")


if __name__ == "__main__":
    sys.exit(main())
