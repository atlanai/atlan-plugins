"""Create a new DuckDB or SQLite database from explicitly selected, surveyed CSVs. No repository code."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_data import DataError


def prepare(root, sources, request):
    from project import safe_path, file_hash
    destination = safe_path(Path(root) / request['database'], root)
    if destination.suffix.lower() not in ('.duckdb', '.sqlite') or destination.exists():
        raise DataError('Choose a new .duckdb or .sqlite path. Existing databases are never replaced.')
    if not destination.parent.is_dir():
        raise DataError('Choose an existing project directory for the new database.')
    selections = request.get('tables')
    if not isinstance(selections, list) or not 1 <= len(selections) <= 32:
        raise DataError('Map 1 to 32 surveyed CSV files to schema-qualified tables.')
    available = {s['source_id']: s for s in sources
                 if s['kind'] == 'data_file' and s['status'] == 'available'}
    tables, names, total = [], set(), 0
    for item in selections:
        source = available.get(item.get('source_id'))
        name = item.get('table', '')
        if not source or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*', name):
            raise DataError('Use surveyed source IDs and simple schema.table names from source evidence.')
        if name.lower() in names:
            raise DataError('Each table mapping must have a distinct name.')
        names.add(name.lower())
        path = safe_path(Path(root) / source['path'], root)
        if path.suffix.lower() != '.csv' or any(c in str(path) for c in '*?[]{}'):
            raise DataError('Local preparation supports ordinary CSV paths, without glob characters.')
        if path.stat().st_size > 32 * 1024 ** 2 or file_hash(path) != source['sha256']:
            raise DataError('A selected source changed or exceeds 32 MiB. Survey it again.')
        total += path.stat().st_size
        tables.append({'path': str(path), 'table': name, 'source_id': source['source_id'],
                       'sha256': source['sha256']})
    if total > 128 * 1024 ** 2:
        raise DataError('Select at most 128 MiB of CSV data for local preparation.')
    # Build privately, then atomically link without replacing a concurrent destination.
    with tempfile.TemporaryDirectory(prefix='.atlan-prepare-', dir=destination.parent) as temporary:
        staged = Path(temporary) / ('prepared' + destination.suffix.lower())
        payload = {'database': str(staged), 'tables': tables}
        try:
            process = subprocess.run([sys.executable, '-I', __file__, '--worker'],
                                     input=json.dumps(payload), capture_output=True, text=True,
                                     timeout=60)
        except subprocess.TimeoutExpired:
            raise DataError('Data preparation timed out. Select a smaller CSV scope.') from None
        if process.returncode or len(process.stdout) > 1_000_000:
            raise DataError('Data preparation failed. Check the selected CSVs and database runtime.')
        try:
            result = json.loads(process.stdout)
        except ValueError:
            raise DataError('Data preparation returned an unsupported result.') from None
        if result.get('status') != 'ok':
            raise DataError(result.get('message', 'Data preparation failed.'))
        for item in tables:
            if file_hash(safe_path(item['path'], root)) != item['sha256']:
                raise DataError('A CSV changed during preparation. Survey again before retrying.')
        staged.chmod(0o600)
        try:
            os.link(staged, destination)
        except FileExistsError:
            raise DataError('The destination now exists. It was not replaced; inspect and bind it.') from None
    return {**result, 'database': str(destination), 'created': True,
            'next': 'Review inferred types and row counts, then bind this database.'}


def sqlite_worker(payload):
    import csv
    import sqlite3
    # A SQLite file is one schema: the tables are stored by name and attached under it on bind.
    schemas = {item['table'].split('.')[0] for item in payload['tables']}
    if len(schemas) != 1:
        raise DataError('A SQLite database holds one schema. Map every CSV to the same schema.')

    def column_type(values):
        for name, cast in (('INTEGER', int), ('REAL', float)):
            try:
                [cast(v) for v in values if v != '']
                return name
            except ValueError:
                pass
        return 'TEXT'

    output = []
    with sqlite3.connect(payload['database']) as con:
        for item in payload['tables']:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*', item['table']):
                raise DataError('Use simple schema.table names from source evidence.')
            table = item['table'].split('.')[1]
            with open(item['path'], newline='') as f:
                header, *rows = list(csv.reader(f))
            if any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9 ]*', h) for h in header) or any(len(r) != len(header) for r in rows):
                raise DataError('CSV preparation failed. Use a header row of simple column names and equal-length rows.')
            types = [column_type([r[i] for r in rows]) for i in range(len(header))]
            con.execute('CREATE TABLE "' + table + '" (' + ', '.join('"' + h + '" ' + t for h, t in zip(header, types)) + ')')
            con.executemany('INSERT INTO "' + table + '" VALUES (' + ', '.join('?' * len(header)) + ')',
                            [[None if v == '' else v for v in r] for r in rows])
            output.append({'source_id': item['source_id'], 'table': item['table'], 'rows': len(rows),
                           'columns': [{'name': h, 'type': t} for h, t in zip(header, types)]})
    return {'status': 'ok', 'kind': 'sqlite', 'schema': schemas.pop(), 'tables': output,
            'types': 'inferred_from_csv', 'remote_writes': False}


def worker(payload):
    if payload['database'].endswith('.sqlite'):
        return sqlite_worker(payload)
    try:
        import duckdb
    except ImportError:
        raise DataError('Select an existing approved Python runtime with DuckDB installed.') from None
    try:
        config = {'autoload_known_extensions': 'false', 'autoinstall_known_extensions': 'false',
                  'allow_community_extensions': 'false', 'threads': '1', 'memory_limit': '256MB',
                  'max_temp_directory_size': '0B', 'python_enable_replacements': 'false'}
        with duckdb.connect(payload['database'], config=config) as con:
            # Configure the exact local allowlist before disabling external access. No data
            # or untrusted SQL runs before the configuration is locked.
            con.execute('SET allowed_paths = ?', [[item['path'] for item in payload['tables']]])
            con.execute('SET enable_external_access = false')
            con.execute('SET lock_configuration = true')
            output = []
            for item in payload['tables']:
                if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*', item['table']):
                    raise DataError('Use simple schema.table names from source evidence.')
                schema, table = item['table'].split('.')
                relation = '"' + schema + '"."' + table + '"'
                con.execute('CREATE SCHEMA IF NOT EXISTS "' + schema + '"')
                con.execute('CREATE TABLE ' + relation + ' AS SELECT * FROM read_csv(?, header=true)',
                            [item['path']])
                rows = con.execute('SELECT COUNT(*) FROM ' + relation).fetchone()[0]
                columns = con.execute('DESCRIBE ' + relation).fetchall()
                output.append({'source_id': item['source_id'], 'table': item['table'], 'rows': rows,
                               'columns': [{'name': c[0], 'type': c[1]} for c in columns]})
        return {'status': 'ok', 'tables': output, 'types': 'inferred_from_csv', 'remote_writes': False}
    except Exception as exc:
        raise DataError('CSV preparation failed (' + type(exc).__name__ +
                        '). Check source format, inferred types and available disk space.') from None


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['--worker']:
            raise DataError('Use project.py prepare with an explicit source-to-table mapping.')
        answer = worker(json.loads(sys.stdin.read(1_000_000)))
    except (ValueError, KeyError, TypeError) as exc:
        answer = {'status': 'blocked', 'message': str(exc) if isinstance(exc, DataError)
                  else 'Invalid data preparation request.'}
    print(json.dumps(answer))
