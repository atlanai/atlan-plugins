#!/usr/bin/env python3
"""Open a demo that lives in this skill: set up its working folder, then a setup run's requests.

    python3 demo_data.py open <demo> <folder>   # sets up <folder>/atlan-analytics-demo/<demo>/
    python3 demo_data.py requests <demo> --run-dir <run>   # after the survey: prepare and scope requests
    python3 demo_data.py engine <demo> --run-dir <run> --sources all|dashboard   # the engine's config

Each demo lives in a folder under this skill's assets/: its dashboard, CSVs and query history laid
out as a project, plus demo.json naming the dashboard file, the database to prepare, its schema and
the dialect. Adding a demo is adding a folder. A run works in a folder of the person's, because
preparation writes the database inside the folder it surveyed and the plugin's own folder is not
the person's to write in. Standard library only; nothing is downloaded.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
ASSETS = Path(__file__).resolve().parents[1] / 'assets'
MANIFEST = 'demo.json'
FIELDS = ('bi', 'database', 'schema', 'dialect')


class DemoError(ValueError):
    pass


def demos():
    return sorted(p.name for p in ASSETS.iterdir() if (p / MANIFEST).is_file())


def manifest(demo):
    if demo not in demos():
        raise DemoError(f'No demo named {demo!r}. Choose one of: {", ".join(demos())}.')
    facts = json.loads((ASSETS / demo / MANIFEST).read_text(encoding='utf-8'))
    missing = [f for f in FIELDS if not facts.get(f)]
    if missing:
        raise DemoError(f'{demo}/{MANIFEST} is missing {", ".join(missing)}.')
    return facts


def files(demo):
    source = ASSETS / demo
    return sorted(p.relative_to(source) for p in source.rglob('*') if p.is_file() and p.name != MANIFEST)


def open_demo(demo, folder):
    """Set up the demo's working folder, or bring an earlier one up to date with this skill. A
    database prepared from older files is removed, since its rows no longer match the CSVs; nothing
    else in the folder is touched."""
    facts = manifest(demo)
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise DemoError(f'{folder} is not a folder. Choose an existing working folder.')
    source, target = ASSETS / demo, folder / 'atlan-analytics-demo' / demo
    existed = target.is_dir()
    changed, total = [], 0
    for relative in files(demo):
        data = (source / relative).read_bytes()
        total += len(data)
        destination = target / relative
        if destination.is_file() and destination.read_bytes() == data:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        changed.append(relative.as_posix())
    database = target / facts['database']
    if existed and changed and database.exists():
        database.unlink()
    status = 'created' if not existed else 'refreshed' if changed else 'current'
    return {'status': status, 'demo': demo, 'working_folder': str(target), 'files': len(files(demo)),
            'bytes': total, 'database_exists': database.exists()}


def surveyed(demo, run_dir):
    """The run's survey of this demo's working folder: (facts, run, folder, sources by path)."""
    facts = manifest(demo)
    run = Path(run_dir).expanduser().resolve()
    try:
        state = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise DemoError('Survey the demo\'s working folder into this run first.') from None
    root = Path(state.get('source_root', ''))
    if root.parts[-2:] != ('atlan-analytics-demo', demo):
        raise DemoError(f'This run surveyed {root}, not the {demo} working folder. Open the demo and survey it.')
    sources = {s['path']: s for s in state.get('sources', [])}
    if facts['bi'] not in sources:
        raise DemoError('The survey is missing the demo files. Open the demo again, then survey it.')
    return facts, run, root, sources


def requests(demo, run_dir):
    """Write the setup run's prepare and scope requests from its survey of the working folder, so the demo's
    fixed choices (every CSV as `schema.<file>`, the whole dashboard) are never worked out again."""
    facts, run, root, sources = surveyed(demo, run_dir)
    csvs = [s for p, s in sorted(sources.items())
            if p.startswith('data/') and p.endswith('.csv') and s.get('status') == 'available']
    if not csvs:
        raise DemoError('The survey is missing the demo files. Open the demo again, then survey it.')
    prepare = {'database': facts['database'], 'tables': [
        {'source_id': s['source_id'], 'table': facts['schema'] + '.' + Path(s['path']).stem} for s in csvs]}
    run.joinpath('demo-prepare.json').write_text(json.dumps(prepare, indent=1), encoding='utf-8')
    result = {'status': 'ok', 'prepare': str(run / 'demo-prepare.json'), 'scope': None,
              'bi': str(root / facts['bi']), 'database': str(root / facts['database']),
              'schema': facts['schema'], 'dialect': facts['dialect'], 'tables': len(csvs)}
    bi = sources[facts['bi']]
    if bi.get('kind') == 'powerbi':
        run.joinpath('demo-scope.json').write_text(
            json.dumps({'source_id': bi['source_id'], 'pages': ['all']}), encoding='utf-8')
        result['scope'] = str(run / 'demo-scope.json')
    return result


def engine(demo, run_dir, scope):
    """Write the metrics engine's configuration for this run: the dashboard, the cleaned past queries
    when they are in scope, and the prepared database to check every statement on."""
    facts, run, root, sources = surveyed(demo, run_dir)
    database = root / facts['database']
    history = run / 'context' / 'querylog.clean.jsonl'
    if not database.is_file():
        raise DemoError('Prepare and bind the demo database first.')
    if scope == 'all' and not history.is_file():
        raise DemoError('Run PROJECT history first; the engine reads its cleaned past queries.')
    settings = {'bi': str(root / facts['bi']), 'bi_vendor': sources[facts['bi']]['kind'],
                'database': str(database)}
    if scope == 'all':
        settings['history'] = str(history)
    config = {'version': 1, 'scope': demo, 'dialect': facts['dialect'], 'mode': 'standalone',
              'inputs': {'history': ['local-files'] if scope == 'all' else [], 'schema': [],
                         'bi': ['local-files'], 'sql_check': 'local-files'},
              'settings': {'local-files': settings},
              'witness_mode': 'execute', 'publish': ['files'], 'observers': []}
    run.joinpath('demo-engine.json').write_text(json.dumps(config, indent=1), encoding='utf-8')
    return {'status': 'ok', 'config': str(run / 'demo-engine.json'), 'engine_run': str(run / 'engine')}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    place = sub.add_parser('open')
    place.add_argument('demo')
    place.add_argument('folder')
    ask = sub.add_parser('requests')
    ask.add_argument('demo')
    ask.add_argument('--run-dir', required=True)
    configure = sub.add_parser('engine')
    configure.add_argument('demo')
    configure.add_argument('--run-dir', required=True)
    configure.add_argument('--sources', choices=('all', 'dashboard'), required=True)
    args = parser.parse_args()
    try:
        if args.command == 'open':
            result = open_demo(args.demo, args.folder)
        elif args.command == 'requests':
            result = requests(args.demo, args.run_dir)
        else:
            result = engine(args.demo, args.run_dir, args.sources)
        print(json.dumps(result))
    except (DemoError, OSError, ValueError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
