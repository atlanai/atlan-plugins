#!/usr/bin/env python3
"""Dispatch an Analytics workload module. Each module owns its runtime and state.

python3 analytics_agent.py metrics --help
No module owns Registry publication; it exports the existing context-file contract.
"""
import importlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
MODULES = {'metrics': 'modules.metrics.driver'}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in MODULES:
        print('Available Analytics modules: ' + ', '.join(MODULES))
        return 0 if args in ([], ['--help'], ['-h']) else 2
    if not (PLUGIN / 'modules' / args[0] / 'driver.py').is_file():
        print(json.dumps({'status': 'BLOCKED', 'reason': 'The Analytics module files are missing.',
                          'fix': 'Install or refresh the complete Analytics plugin, including its modules folder.'}))
        return 2
    return importlib.import_module(MODULES[args[0]]).main(args[1:])


if __name__ == '__main__':
    raise SystemExit(main())
