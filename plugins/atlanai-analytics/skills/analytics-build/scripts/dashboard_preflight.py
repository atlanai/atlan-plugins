#!/usr/bin/env python3
"""Read-only first-use checks. Runs on Python 3.9+; never installs or signs in."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def probe(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def python_runtime():
    candidates = [sys.executable] + [shutil.which('python3.' + str(v)) for v in range(14, 10, -1)]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        version = probe([candidate, '-I', '-S', '-c',
                         'import sys,json; print(json.dumps(list(sys.version_info[:3])))'])
        try:
            parts = json.loads(version or 'null')
            if isinstance(parts, list) and len(parts) == 3 and all(type(v) is int for v in parts) and parts[:2] >= [3, 11]:
                return {'status': 'available', 'executable': candidate, 'version': '.'.join(map(str, parts))}
        except (ValueError, TypeError):
            pass
    return {'status': 'needs_setup', 'action': 'Install an approved Python 3.11 or newer.'}


def preflight(source='unknown', registry='unknown', warehouse='snowflake'):
    python = python_runtime()
    decoder = Path.home() / '.trident/runtimes/pbixray-0.15.5/bin/python'
    installed = decoder.is_file() and probe([str(decoder), '-I', '-c',
        'from importlib.metadata import version; print(version("pbixray"))']) == '0.15.5'
    snow = shutil.which('snow')
    if not snow:
        local = Path.home() / '.local/bin/snow'
        snow = str(local) if local.is_file() else None
    compatible = False
    if snow:
        # Local help only: no configuration, account discovery or credentials are read.
        help_text = probe([snow, 'sql', '--help'])
        compatible = bool(help_text and '--connection' in help_text and '--format' in help_text)
    checks = {
        'python': python,
        'decoder': {'status': 'available' if installed else 'needs_setup', 'required_for': 'file_import',
                    'action': None if installed else 'Approve the bundled PBIXRay setup once when importing a file.'},
        'source': {'status': 'host_confirmed' if source != 'unknown' else 'needs_access', 'transport': source,
                   'action': None if source != 'unknown' else 'Open the supplied report using an authorized browser/connector, or supply its model export.'},
        'warehouse': {'status': ('available' if compatible else 'needs_setup') if warehouse == 'snowflake' else 'host_confirmed',
                      'transport': warehouse, 'authenticated': 'not_checked',
                      'action': None if compatible or warehouse != 'snowflake' else 'Provide an approved Snowflake CLI with SQL support before validation.'},
        'registry': {'status': registry, 'evidence': 'host_reported', 'required_for': 'publication',
                     'action': None if registry == 'connected' else 'Check the core Atlan connection before publication; local extraction and questions can continue.'},
    }
    actions = [{'component': name, 'action': value['action']} for name, value in checks.items() if value.get('action')]
    return {'status': 'ready' if not actions else 'setup_needed', 'checks': checks,
            'next_setup_action': actions[0] if actions else None, 'setup_actions': actions,
            'helper': str(Path(__file__).with_name('dashboard.py')), 'network_auth_performed': False,
            'instruction': 'Use checks.python.executable for every helper call. Show only the setup action needed by the next stage. This check does not authenticate or verify source or Registry access.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['unknown', 'file', 'browser', 'connector'], default='unknown')
    parser.add_argument('--registry', choices=['unknown', 'connected', 'auth_required', 'unavailable'], default='unknown')
    parser.add_argument('--warehouse', choices=['snowflake', 'existing_adapter'], default='snowflake')
    args = parser.parse_args()
    print(json.dumps(preflight(args.source, args.registry, args.warehouse)))


if __name__ == '__main__':
    main()
