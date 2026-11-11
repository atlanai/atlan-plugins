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


# What each detected kind is to a person, and its place in the build order: reports and models
# hold agreed rules, query history shows what people ask, table data lets the rules run.
ROLES = {'powerbi': ('bi', 'Power BI report or model'), 'powerbi_project': ('bi', 'Power BI project'),
         'tableau': ('bi', 'Tableau workbook'), 'model_candidate': ('bi', 'semantic model'),
         'dashboard_html': ('bi', 'HTML dashboard'), 'query_history': ('history', 'query history export'),
         'saved_sql': ('history', 'saved SQL'), 'rowsets': ('data', 'saved row sets'),
         'duckdb': ('data', 'DuckDB database'), 'sqlite': ('data', 'SQLite database'),
         'data_file': ('data', 'data file')}
ORDER = ('bi', 'history', 'data')
# Where Atlan Analytics' own demo lives in a working folder: never part of a person's setup.
DEMO = ('atlan-analytics-demo', 'analytics-agent-accelerator')


def summarize(root, limit=5000, per_role=12):
    """Names and sizes only, grouped by role in build order. Reads no file contents and creates
    nothing, so the setup survey can say what a folder offers before a run exists."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing folder to look in.')
    paths, limited = discover(root, limit=limit)
    groups = {role: [] for role in ORDER}
    demo = 0
    for path, kind in paths:
        if any(part in DEMO for part in path.relative_to(root).parts):
            demo += 1
            continue
        role, label = ROLES.get(kind, ('data', kind))
        groups[role].append({'path': path.relative_to(root).as_posix(), 'kind': kind, 'label': label,
                             'size_bytes': path.stat().st_size})
    return {'folder': str(root), 'limited': limited, 'created': False, 'remote_writes': False,
            'demo_files_skipped': demo,
            'counts': {role: len(items) for role, items in groups.items()},
            'sources': {role: sorted(items, key=lambda i: (-i['size_bytes'], i['path']))[:per_role]
                        for role, items in groups.items()},
            'omitted': {role: max(0, len(items) - per_role) for role, items in groups.items()}}
