"""Bounded DuckDB execution in a child process; no installs, uploads or repository code.

Use an existing approved Python runtime with DuckDB. Host sandboxing is still required
for hostile SQL; engine settings are defense in depth, not an OS security boundary.
"""
import datetime
import decimal
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


class DataError(ValueError):
    pass


# Bounded analytical SQL. Unknown functions require review rather than inheriting
# the privileges of arbitrary table functions or database-defined macros.
FUNCTIONS = set(('sum avg count min max coalesce nullif abs round floor ceil ceiling '
                 'upper lower trim ltrim rtrim length substring substr replace concat '
                 'cast try_cast date_trunc date_part extract strftime strptime '
                 'datediff date_diff date_add greatest least ifnull isnan isfinite '
                 'row_number rank dense_rank lag lead first_value last_value '
                 'sqrt power median quantile_cont quantile_disc stddev_pop stddev_samp '
                 'variance var_pop var_samp count_if bool_and bool_or instr position '
                 'as in over filter partition by from where and or not select with '
                 'exists values grouping rollup cube on having qualify '
                 'decimal numeric varchar char bigint integer smallint').split())


def check_functions(sql):
    from metrics_mine import scan
    code = ''.join(text if kind == 'code' else text[1:-1] if kind == 'ident' else ' '
                   for kind, text in scan(sql))
    calls = set(re.findall(r'([A-Za-z_][A-Za-z_0-9]*)\s*\(', code.lower()))
    if calls - FUNCTIONS:
        raise DataError('SQL uses an unsupported function. Use reviewed analytical functions and base tables.')
    return calls


def regular_file(path):
    p = Path(path).expanduser().absolute()
    if any(x.is_symlink() for x in (p, *p.parents)) or not p.is_file():
        raise DataError('Select an existing regular DuckDB file; no database was created.')
    if p.suffix.lower() != '.duckdb':
        raise DataError('Select a .duckdb database file.')
    return p


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def call(binding, operation, sql=None, parameters=None, limit=100, timeout=20):
    """The parent enforces a wall-clock bound even if the engine cannot interrupt SQL."""
    regular_file(binding['path'])
    if not 1 <= limit <= 1000 or not 0 < timeout <= 60:
        raise DataError('Use a row limit from 1 to 1000 and a timeout at most 60 seconds.')
    request = {'binding': binding, 'operation': operation, 'sql': sql,
               'parameters': parameters or {}, 'limit': limit}
    try:
        proc = subprocess.run([binding.get('python') or sys.executable, '-B', __file__, '--worker'],
                              input=json.dumps(request), text=True, capture_output=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DataError('Query timed out. Narrow the query or its data scope.') from None
    except OSError:
        raise DataError('The selected Python runtime is unavailable. Rebind an approved runtime.') from None
    if proc.returncode or len(proc.stdout) > 4_000_000:
        raise DataError('Local execution failed or exceeded the result limit. Narrow the query.')
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise DataError('Local execution did not return a supported result.') from None
    if result.get('status') != 'ok':
        raise DataError(result.get('message', 'Local execution failed.'))
    return result


def _value(value):
    if isinstance(value, (decimal.Decimal, datetime.date, datetime.time)):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise DataError('The result contains a non-finite number.')
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and len(value) > 100_000:
            raise DataError('A result cell exceeds the limit; select smaller values.')
        return value
    raise DataError('The result has a nested or binary value; select scalar columns.')


def worker(request):
    try:
        import duckdb
    except ImportError:
        raise DataError('DuckDB is unavailable in this Python runtime. Select an existing approved DuckDB runtime.') from None
    binding = request['binding']
    path = regular_file(binding['path'])
    config = {'enable_external_access': 'false', 'autoload_known_extensions': 'false',
              'autoinstall_known_extensions': 'false', 'allow_community_extensions': 'false',
              'threads': '1', 'memory_limit': '256MB', 'max_temp_directory_size': '0B',
              'python_enable_replacements': 'false'}
    try:
        with duckdb.connect(str(path), read_only=True, config=config) as con:
            con.execute('SET lock_configuration = true')
            columns = con.execute("""SELECT table_catalog, table_schema, table_name, column_name, data_type
                FROM information_schema.columns
                WHERE (table_catalog, table_schema, table_name) IN
                  (SELECT table_catalog, table_schema, table_name FROM information_schema.tables
                   WHERE table_type = 'BASE TABLE')
                ORDER BY table_catalog, table_schema, table_name, ordinal_position""").fetchall()
            schema = [{'catalog': r[0], 'schema': r[1], 'table': r[2], 'column': r[3], 'type': r[4]}
                      for r in columns]
            schema_hash = fingerprint(schema)
            tables = sorted({r[1] + '.' + r[2] for r in columns})
            if request['operation'] == 'inspect':
                return {'status': 'ok', 'kind': 'duckdb', 'version': duckdb.__version__,
                        'tables': tables, 'columns': schema, 'schema_hash': schema_hash}
            if request['operation'] != 'execute':
                raise DataError('Unsupported local data operation.')
            if schema_hash != binding.get('schema_hash'):
                raise DataError('Database schema changed. Inspect and rebind before answering.')
            selected = set(binding.get('tables', []))
            if not selected or not selected <= set(tables):
                raise DataError('Select existing schema-qualified base tables before executing SQL.')
            sql = request['sql']
            if not isinstance(sql, str) or len(sql) > 200_000:
                raise DataError('Supply one bounded SELECT statement.')
            statements = con.extract_statements(sql)
            if len(statements) != 1 or str(statements[0].type) != 'StatementType.SELECT':
                raise DataError('Only one SELECT or WITH query is allowed.')
            parameters = request.get('parameters') or {}
            if not isinstance(parameters, dict) or set(statements[0].named_parameters) != set(parameters):
                raise DataError('Supply exactly the named SQL parameters using $name bindings.')
            calls = check_functions(sql)
            custom = {r[0].lower() for r in con.execute(
                'SELECT function_name FROM duckdb_functions() WHERE NOT internal').fetchall()}
            if calls & custom:
                raise DataError('Database-defined functions require separate review.')
            # Qualified references avoid resolving the same table name in a different schema.
            allowed = {(r[1], r[2]) for r in columns if r[1] + '.' + r[2] in selected}
            allowed.update((r[0], r[1], r[2]) for r in columns if r[1] + '.' + r[2] in selected)
            # get_table_names cannot bind parameters. Analyze a NULL-only copy;
            # the actual query below still uses the original SQL and bound values.
            from metrics_mine import scan
            scope_sql = ''.join(re.sub(r'\$[A-Za-z_0-9]+\b', 'NULL', text) if kind == 'code' else text
                                for kind, text in scan(sql))
            def parts(name):
                ident = r'"(?:[^"]|"")*"|[^.\s"]+'
                match = re.fullmatch(r'(' + '(?:' + ident + r')(?:\.(?:' + ident + r')){0,2})'
                                     r'(?:\s+AS\s+(?:' + ident + r'))?', name, re.I)
                if not match:
                    raise DataError('A table reference is outside the supported qualified-name format.')
                return tuple(p[1:-1].replace('""', '"') if p.startswith('"') else p
                             for p in re.findall(r'"(?:[^"]|"")*"|[^.]+', match[1]))
            referenced = {parts(n) for n in con.get_table_names(scope_sql, qualified=True)}
            if not referenced <= allowed:
                raise DataError('Query tables are outside the selected scope. Use schema-qualified base tables.')
            started = time.monotonic()
            cursor = con.execute(sql, parameters)
            result_columns = [{'name': c[0], 'type': str(c[1])} for c in cursor.description]
            rows = cursor.fetchmany(request['limit'] + 1)
            return {'status': 'ok', 'query_id': 'local_' + uuid.uuid4().hex,
                    'sql_hash': fingerprint({'sql': sql, 'parameters': parameters}),
                    'schema_hash': schema_hash, 'columns': result_columns,
                    'rows': [[_value(v) for v in row] for row in rows[:request['limit']]],
                    'truncated': len(rows) > request['limit'],
                    'duration_ms': round((time.monotonic() - started) * 1000, 3)}
    except DataError:
        raise
    except Exception as exc:
        # Engine diagnostics can include SQL literals and private paths.
        raise DataError('DuckDB could not complete the query (' + type(exc).__name__ +
                        '). Check SQL, bindings, database locks and supported functions.') from None


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['--worker']:
            raise DataError('Use the project helper to select a database and execute a query.')
        result = worker(json.loads(sys.stdin.read(1_000_000)))
    except (ValueError, KeyError, TypeError) as exc:
        result = {'status': 'blocked', 'message': str(exc) if isinstance(exc, DataError)
                  else 'Invalid local execution request.'}
    print(json.dumps(result, allow_nan=False))
