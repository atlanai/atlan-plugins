#!/usr/bin/env python3
"""Place a bundled demo's files in the person's folder, where a setup run can read and load them.

    python3 demo_data.py copy <demo> <folder>   # writes <folder>/atlan-analytics-demo/<demo>/
    python3 demo_data.py requests <demo> --run-dir <run>   # after the survey: prepare and scope requests

Each demo is a folder under this skill's assets/: its dashboard, CSVs and query history laid out
as a project, plus demo.json naming the dashboard file, the database to prepare, its schema and
the dialect. Adding a demo is adding a folder. The copy is needed because preparation writes the
database inside the source folder, and the plugin's own folder is not the person's to write in.
Standard library only; nothing is downloaded.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
ASSETS = Path(__file__).resolve().parents[1] / 'assets'
MANIFEST = 'demo.json'
FIELDS = ('firm', 'bi', 'database', 'schema', 'dialect')


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


def copy(demo, folder):
    """Copy the demo, or bring an earlier copy up to date. The database prepared from an older copy
    is removed, since its rows no longer match the CSVs; nothing else in the folder is touched."""
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
    status = 'copied' if not existed else 'refreshed' if changed else 'current'
    return {'status': status, 'demo': demo, 'source_folder': str(target), 'files': len(files(demo)),
            'bytes': total, 'database_exists': database.exists(),
            **{f: facts[f] for f in FIELDS}}


def requests(demo, run_dir):
    """Write the setup run's prepare and scope requests from its survey of the copy, so the demo's
    fixed choices (every CSV as `schema.<file>`, the whole dashboard) are never worked out again."""
    facts = manifest(demo)
    run = Path(run_dir).expanduser().resolve()
    try:
        state = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise DemoError('Survey the copied folder into this run first.') from None
    root = Path(state.get('source_root', ''))
    if root.parts[-2:] != ('atlan-analytics-demo', demo):
        raise DemoError(f'This run surveyed {root}, not the {demo} copy. Survey the copied folder.')
    sources = {s['path']: s for s in state.get('sources', [])}
    csvs = [s for p, s in sorted(sources.items())
            if p.startswith('data/') and p.endswith('.csv') and s.get('status') == 'available']
    if not csvs or facts['bi'] not in sources:
        raise DemoError('The survey is missing the demo files. Copy the demo again, then survey it.')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    place = sub.add_parser('copy')
    place.add_argument('demo')
    place.add_argument('folder')
    ask = sub.add_parser('requests')
    ask.add_argument('demo')
    ask.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(copy(args.demo, args.folder) if args.command == 'copy'
                         else requests(args.demo, args.run_dir)))
    except (DemoError, OSError, ValueError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
