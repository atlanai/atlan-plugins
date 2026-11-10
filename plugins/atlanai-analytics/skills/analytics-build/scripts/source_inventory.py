"""Bounded source detection. Detection is not a claim that a format was parsed."""
from pathlib import Path
import os

SKIP = {'node_modules', 'venv', '__pycache__', 'dist', 'build', 'vendor'}
SENSITIVE = ('credential', 'secret', 'password', 'token', 'keychain')


def source_kind(path):
    path = Path(path)
    suffix, name = path.suffix.lower(), path.name.lower()
    if suffix in ('.pbix', '.pbit', '.bim'):
        return 'powerbi'
    if suffix in ('.tmdl', '.pbip', '.pbir') or name in ('page.json', 'visual.json', 'report.json', 'pages.json'):
        return 'powerbi_project'
    if suffix in ('.twb', '.tds', '.twbx', '.tdsx'):
        return 'tableau'
    if suffix == '.duckdb':
        return 'duckdb'
    if suffix in ('.sqlite', '.sqlite3'):
        return 'sqlite'
    if suffix in ('.jsonl', '.json', '.csv') and any(
            s in str(path).lower() for s in ('history', 'querylog', 'query_log')):
        return 'query_history'
    if suffix == '.sql':
        return 'saved_sql'
    if name == 'rowsets.json':
        return 'rowsets'
    if name == 'manifest.json' or suffix in ('.lkml', '.yml', '.yaml'):
        return 'model_candidate'
    if suffix in ('.js', '.ts') and any(p.lower() in ('cubes', 'cube') for p in path.parts[:-1]):
        return 'model_candidate'
    if suffix in ('.html', '.htm'):
        return 'dashboard_html'
    if suffix in ('.csv', '.parquet'):
        return 'data_file'
    return None


def discover(root, excluded=None, limit=5000, selected=None):
    paths, inspected = [], 0
    if selected:
        kind = source_kind(selected)
        return ([(selected, kind)] if kind else []), False
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP
                         and not (Path(directory) / d).is_symlink()
                         and not any(s in d.lower() for s in SENSITIVE)
                         and (Path(directory) / d).resolve() != excluded)
        for name in sorted(files):
            inspected += 1
            if inspected > limit:
                return paths, True
            path = Path(directory) / name
            if name.startswith('.') or any(s in name.lower() for s in SENSITIVE) or path.is_symlink():
                continue
            kind = source_kind(path.relative_to(root))
            if kind and path.is_file():
                paths.append((path, kind))
    return paths, False
