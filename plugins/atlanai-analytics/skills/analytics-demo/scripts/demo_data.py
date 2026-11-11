#!/usr/bin/env python3
"""Open a demo that lives in this skill: set up its working folder, then a setup run's requests.

    python3 demo_data.py open <demo> <folder>   # sets up <folder>/atlan-analytics-demo/<demo>/
    python3 demo_data.py requests <demo> --run-dir <run>   # after the survey: prepare and scope requests

Each demo lives in a folder under this skill's assets/: its dashboard, CSVs and query history laid
out as a project, plus demo.json naming the dashboard file, the database to prepare and its schema.
A new demo is that folder, its facts file in references/ and a row in the skill's demo choice.
A run works in a folder of the person's, because preparation writes the database inside the folder
it surveyed and the plugin's own folder is not the person's to write in. The run's state is read
and written through the setup helper's Project. Standard library only; nothing is downloaded.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
SKILLS = Path(__file__).resolve().parents[2]
ASSETS = SKILLS / 'analytics-demo' / 'assets'
sys.path.insert(0, str(SKILLS / 'analytics-build' / 'scripts'))
from project import Project, ProjectError  # noqa: E402

MANIFEST = 'demo.json'
FIELDS = ('bi', 'database', 'schema')


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
    return sorted(p.relative_to(source) for p in source.rglob('*') if p.is_file() and p != source / MANIFEST)


def open_demo(demo, folder):
    """Set up the demo's working folder, or bring an earlier one up to date with this skill: files
    the skill no longer ships are removed, and a database prepared from older CSVs is removed since
    its rows no longer match. Files the person added are never touched."""
    facts = manifest(demo)
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise DemoError(f'{folder} is not a folder. Choose an existing working folder.')
    source, target = ASSETS / demo, folder / 'atlan-analytics-demo' / demo
    # The files placed last time, kept beside the working folder so its survey never reads them.
    ledger = target.parent / f'.{demo}-files.json'
    existed = target.is_dir()
    wanted = [r.as_posix() for r in files(demo)]
    try:
        placed = set(json.loads(ledger.read_text(encoding='utf-8'))) if ledger.is_file() else set()
    except ValueError:
        placed = set()
    removed = []
    for relative in sorted(placed - set(wanted)):
        stale = (target / relative).resolve()
        if stale.is_relative_to(target) and stale.is_file():
            stale.unlink()
            removed.append(relative)
    changed, total = [], 0
    for relative in wanted:
        data = (source / relative).read_bytes()
        total += len(data)
        destination = target / relative
        if destination.is_file() and destination.read_bytes() == data:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
        changed.append(relative)
    ledger.write_text(json.dumps(wanted), encoding='utf-8')
    database = target / facts['database']
    if existed and database.exists() and any(r.startswith('data/') and r.endswith('.csv') for r in changed + removed):
        database.unlink()
    status = 'created' if not existed else 'refreshed' if changed or removed else 'current'
    return {'status': status, 'demo': demo, 'working_folder': str(target), 'files': len(wanted),
            'bytes': total, 'database_exists': database.exists()}


def requests(demo, run_dir):
    """Write the setup run's prepare and scope requests from its survey of the working folder, so the
    demo's fixed choices (its own CSVs as `schema.<file>`, the whole dashboard) are never worked out
    again. With the database already prepared, there is no prepare request: bind it."""
    facts = manifest(demo)
    run = Path(run_dir).expanduser().resolve()
    try:
        if not (run / 'run.json').is_file():
            raise ValueError
        project = Project(run)
        state = project.current()
    except ValueError:
        raise DemoError("Survey the demo's working folder into this run first.") from None
    root = Path(state['source_root'])
    if root.parts[-2:] != ('atlan-analytics-demo', demo):
        raise DemoError(f'This run surveyed {root}, not the {demo} working folder. Open the demo and survey it.')
    surveyed = {s['path']: s for s in state['sources']}
    csvs = [r for r in map(Path.as_posix, files(demo)) if r.startswith('data/') and r.endswith('.csv')]
    missing = [p for p in csvs + [facts['bi']]
               if p not in surveyed or (p in csvs and surveyed[p]['status'] != 'available')]
    if missing:
        raise DemoError(f'The survey is missing {", ".join(missing)}. Open the demo again, then survey it.')
    database = root / facts['database']
    result = {'status': 'ok', 'prepare': None, 'scope': None, 'bi': facts['bi'],
              'database': str(database), 'schema': facts['schema'], 'tables': len(csvs)}
    if not database.exists():
        project.write(project.root / 'demo-prepare.json', {'database': facts['database'], 'tables': [
            {'source_id': surveyed[p]['source_id'], 'table': facts['schema'] + '.' + Path(p).stem} for p in csvs]})
        result['prepare'] = str(project.root / 'demo-prepare.json')
    if surveyed[facts['bi']]['kind'] == 'powerbi':
        project.write(project.root / 'demo-scope.json', {'source_id': surveyed[facts['bi']]['source_id'], 'pages': ['all']})
        result['scope'] = str(project.root / 'demo-scope.json')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    place = sub.add_parser('open')
    place.add_argument('demo')
    place.add_argument('folder')
    ask = sub.add_parser('requests')
    ask.add_argument('demo')
    ask.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    try:
        result = open_demo(args.demo, args.folder) if args.command == 'open' else requests(args.demo, args.run_dir)
        print(json.dumps(result))
    except (DemoError, ProjectError, OSError, ValueError) as error:
        print(json.dumps({'status': 'error', 'message': str(error)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
