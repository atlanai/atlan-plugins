#!/usr/bin/env python3
"""Run the installed-style dashboard journey in Claude Code against offline test services.

Uses existing Claude authentication. No live BI, warehouse or Registry writes. No global
plugin, MCP, permission or authentication configuration is changed. The manifest copy omits
the core dependency because its connection is replaced by the explicit synthetic adapter.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--resume', action='store_true', help='Resume a completed fixture run in a fresh Claude session.')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=args.resume)
    plugin = out / 'plugin'
    shutil.copytree(ROOT / 'plugins/atlanai-analytics', plugin, dirs_exist_ok=args.resume,
                    ignore=shutil.ignore_patterns('__pycache__'))
    manifest = plugin / '.claude-plugin/plugin.json'
    data = json.loads(manifest.read_text()); data.pop('dependencies', None)
    manifest.write_text(json.dumps(data))
    config = out / 'fixture-mcp.json'
    config.write_text(json.dumps({'mcpServers': {'dashboard_fixture': {
        'command': sys.executable, 'args': [str(HERE / 'fixture_server.py'), '--root', str(out / 'fixture')]}}}))
    prompt = '''Convert this dashboard to an agent
https://app.powerbi.com/groups/11111111-1111-1111-1111-111111111111/reports/22222222-2222-2222-2222-222222222222/Overview

Acceptance test environment: use the installed atlanai-analytics skill, including its new
dashboard run commands. The dashboard_fixture MCP server supplies a sanctioned synthetic
Power BI export, its verified scope and mapping, independent parity references, a read-only
SQLite warehouse and an isolated Registry test adapter. There is no live customer data.
Use fixture_source instead of browsing. Use fixture_query for actual SQL execution.
Use fixture_registry for Registry calls and fixture_release for the test-only release step.
The workspace is workspace_demo. I approve saving and releasing the usable synthetic metrics
and registering the proposed example-agent in that fixture workspace. Keep known incorrect
and unsupported metrics out of ordinary questions. A snapshot mismatch is a warning.
Do not change tools, configuration, permissions or dependencies. Use only this working
directory and the supplied plugin. Stop if the decoder is missing; do not install it.

Complete extraction, conversion, parity checks, usable-metric publication with readback,
agent registration with a durable stage receipt, and one fresh answer in this run.
Use .atlan-analytics/example for the run directory. Keep report and source scope as supplied.
During publication, after the first create succeeds, deliberately do not acknowledge its
write response immediately. Call status and registry-next once to simulate resumption.
Then read the artifact back and recover the pending write without creating it again.
Record the agent request before creation and acknowledge its verified readback.

Independent held-out accuracy is not part of this test and must be shown as Not run.
After publication call fixture_advance once, then answer: What is Total amount now?
Execute fresh SQL. Finish with the final run status and a concise answer with its warning.
Do not ask for confirmation already provided for this synthetic scope. Do not fabricate
receipts or reference results. Do not use diagnostic execution to answer a failed metric.
'''
    if args.resume:
        prompt = '''Resume my dashboard agent in .atlan-analytics/example and answer: What is Total amount now?
Use the atlanai-analytics dashboard skill and its run status. This is the existing synthetic
acceptance environment with the dashboard_fixture tools; no live services are involved.
Use the approved existing SQLite warehouse adapter for fresh SQL. Do not change the data,
re-extract, replan evals, create or release artifacts, or repeat setup. Keep the final reply
using the ready block, workflow.display_summary evidence and the relevant snapshot caveat. Do not
request confirmation for already authorized read-only work.'''
    suffix = '-resume' if args.resume else ''
    (out / ('prompt'+suffix+'.txt')).write_text(prompt)
    # Approve only local Python tool entry points for this synthetic session, including
    # the interpreter preflight may select. Do not bypass host policy or copy credentials.
    python_commands = [sys.executable, 'python3', 'python3.11', 'python3.12', 'python3.13', 'python3.14']
    python_commands += [p for p in (shutil.which(name) for name in python_commands) if p]
    allowed = ','.join(['Read', 'Write', 'Skill', 'mcp__dashboard_fixture__*'] +
                       ['Bash(' + name + ' *)' for name in dict.fromkeys(python_commands)])
    command = ['claude', '-p', prompt, '--setting-sources', '', '--strict-mcp-config',
               '--mcp-config', str(config), '--plugin-dir', str(plugin), '--no-session-persistence',
               '--disallowedTools', 'Agent,Task',
               '--allowedTools', allowed,
               '--output-format', 'stream-json', '--verbose', '--max-budget-usd', '12']
    with (out / ('transcript'+suffix+'.jsonl')).open('w') as log, (out / ('stderr'+suffix+'.log')).open('w') as errors:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=errors)
    print(json.dumps({'exit_code': result.returncode, 'output': str(out)}))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
