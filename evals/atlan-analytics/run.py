#!/usr/bin/env python3
"""Run the `atlanai-analytics` plugin's evals against the gateway emulator.

    python3 evals/atlan-analytics/run.py [--plugin plugins/atlanai-analytics] [-- <claude plugin eval options>]

The same setup as evals/atlan/run.py, with two differences. The plugin's `atlanai` dependency is
removed from the copy, because `claude plugin eval` loads one plugin and silently skips one whose
dependency it cannot resolve. In its place the copy gets an `atlan` server pointed at the core
plugin's emulator, which is the connection the dependency would have supplied, with the Metrics
and Ontology apps of the 5 November review's airline sandbox loaded (`ATLAN_EMULATOR_ANALYTICS`). Results land in
evals/atlan-analytics/results/. Each case is a full Claude Code run on your own credential: it
costs money and is not part of `make check`. The LLM graders are judged as evals/atlan/run.py
says.
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
EMULATOR = ROOT / "evals" / "atlan" / "emulator" / "atlan_emulator.py"
PLUGIN = "atlanai-analytics"
SERVER = "atlan"
TOOLS = f"mcp__plugin_{PLUGIN}_{SERVER}__*"

sys.path.insert(0, str(ROOT / "evals" / "atlan"))
from run import judge, summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plugin", default=str(ROOT / "plugins" / PLUGIN), help="the plugin folder to evaluate")
    parser.add_argument("--label", default="", help="a name for this run's results folder")
    parser.add_argument("extra", nargs="*", help="options passed to `claude plugin eval`, after --")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "results" / (f"{stamp}-{args.label}" if args.label else stamp)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="atlan-analytics-eval-") as tmp:
        plugin = Path(tmp) / PLUGIN
        shutil.copytree(args.plugin, plugin, ignore=shutil.ignore_patterns("evals"))
        manifest = plugin / ".claude-plugin" / "plugin.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.pop("dependencies", None)
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        server = {"type": "stdio", "command": sys.executable, "args": [str(EMULATOR)],
                  "env": {"ATLAN_EMULATOR_LOG": str(out / "emulator.jsonl"), "ATLAN_EMULATOR_ANALYTICS": "1"}}
        (plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {SERVER: server}}, indent=2) + "\n", encoding="utf-8")
        shutil.copytree(HERE / "cases", plugin / "evals")
        command = [
            "claude", "plugin", "eval", str(plugin),
            "--trust-plugin", "--mocks", "off", "--allow-tools", TOOLS, "Write",
            "--ablation", "none", "--no-publish",
            "--json", str(out / "result.json"), "--report", str(out / "report.html"),
            "--output-dir", str(out), *judge(args.extra), *args.extra,
        ]
        print("+", " ".join(command), flush=True)
        code = subprocess.run(command, check=False).returncode
    summarize(out / "result.json")
    return code


if __name__ == "__main__":
    sys.exit(main())
