"""Stop a helper on a Python older than 3.11 with one line that says which Python to use.

A Mac's own python3 is 3.9: the first call ended in a TypeError traceback, and a check started on it lost its chip
(the dry runs, 2026-11-25). Written for any Python 3, so every helper a session may run first can import it.
"""
import json
import os
import shutil
import sys


def require():
    if sys.version_info >= (3, 11):
        return
    usable = [p for p in (shutil.which('python3.13'), shutil.which('python3.12'), shutil.which('python3.11'),
                          os.path.join('.atlan-analytics', 'runtime', 'venv', 'bin', 'python'),
                          os.path.join('.atlan-metrics', 'runtime', 'venv', 'bin', 'python')) if p and os.path.exists(p)]
    print(json.dumps({'status': 'blocked',
                      'message': 'These helpers need Python 3.11 or later; this one is %d.%d.' % sys.version_info[:2],
                      'fix': ('Run them with %s.' % os.path.abspath(usable[0])) if usable else
                             'Install Python 3.12 (from python.org, or `uv python install 3.12`), then run them with it.'}))
    raise SystemExit(2)
