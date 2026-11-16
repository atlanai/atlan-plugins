#!/usr/bin/env python3
"""Read-only SQLite tools for one isolated comparison answering process.

Started only for this local check, never registered or installed as a plugin MCP server.
No registry connection, network access or filesystem tool is exposed.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
import time


def execute(database, schema, sql):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', schema):
        raise ValueError('Invalid schema')
    if not re.match(r'^\s*(SELECT|WITH)\b', sql, re.I):
        return {'error': 'Only SELECT or WITH is allowed'}
    db = sqlite3.connect(':memory:', uri=True)
    try:
        db.execute(f'ATTACH DATABASE ? AS "{schema}"', (Path(database).resolve().as_uri() + '?mode=ro',))
        db.execute('PRAGMA query_only=ON')
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        def authorize(action, arg1, arg2, dbname, source):
            if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {'load_extension', 'readfile', 'writefile'}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        deadline = time.monotonic() + 20
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        cursor = db.execute(sql)
        rows = cursor.fetchmany(1001)
        return {'columns': [d[0] for d in cursor.description], 'rows': rows[:1000], 'truncated': len(rows) > 1000}
    except sqlite3.Error as error:
        return {'error': str(error)}
    finally:
        db.close()


TOOLS = [
    {'name': 'read_context', 'description': 'Read bounded pages of schema, raw query history, or metrics. Follow next_offset until null for complete text. Metrics without metric returns the name/description inventory; pass an exact metric name to read its definition. History search is a literal SQL substring; omit search for all original rows. Missing context returns unavailable.',
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
     'inputSchema': {'type': 'object', 'properties': {
         'kind': {'type': 'string', 'enum': ['schema', 'history', 'metrics']},
         'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 3000},
         'metric': {'type': 'string'}, 'search': {'type': 'string'}}, 'required': ['kind'], 'additionalProperties': False}},
    {'name': 'execute_sql', 'description': 'Execute one read-only SQLite SELECT or WITH statement, at most 1000 rows.',
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
     'inputSchema': {'type': 'object', 'properties': {'sql': {'type': 'string'}}, 'required': ['sql'], 'additionalProperties': False}}
]


def _metric_spans(source):
    """Return metric object/value spans without rewriting their original JSON text."""
    decoder = json.JSONDecoder()
    stripped = source.lstrip()
    start = len(source) - len(stripped)
    value, end = decoder.raw_decode(source, start)
    if isinstance(value, list):
        entries = value
        cursor = start + 1
        closing = source.rfind(']', cursor, end)
    elif isinstance(value, dict) and isinstance(value.get('metrics'), list):
        key = json.dumps('metrics')
        key_at = source.find(key, start, end)
        if key_at < 0:
            raise ValueError('Metrics array span cannot be located')
        cursor = source.find('[', key_at + len(key), end) + 1
        entries = value['metrics']
        closing = source.rfind(']', cursor, end)
    else:
        raise ValueError('Metrics must be a JSON array or contain a metrics array')
    spans = []
    for entry in entries:
        while cursor < closing and (source[cursor].isspace() or source[cursor] == ','):
            cursor += 1
        item, item_end = decoder.raw_decode(source, cursor)
        spans.append((item, cursor, item_end))
        cursor = item_end
    if len(spans) != len(entries):
        raise ValueError('Could not index every metric definition')
    return spans


def _page(text, offset, limit):
    end = min(len(text), offset + limit)
    return {'text': text[offset:end], 'offset': offset,
            'next_offset': end if end < len(text) else None, 'total': len(text)}


def read_context(packet, args):
    """Return a bounded, paginated view of the frozen source files."""
    if not isinstance(args, dict) or not isinstance(args.get('kind'), str):
        return {'error': 'kind must be schema, history, or metrics'}
    kind = args['kind']
    if kind not in ('schema', 'history', 'metrics'):
        return {'error': 'Unsupported context kind'}
    allowed = {'kind', 'offset', 'limit'} | ({'metric'} if kind == 'metrics' else set()) | ({'search'} if kind == 'history' else set())
    if set(args) - allowed:
        return {'error': 'Unsupported context arguments for ' + kind}
    offset, limit = args.get('offset', 0), args.get('limit', 2500)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return {'error': 'offset must be a nonnegative integer'}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3000:
        return {'error': 'limit must be between 1 and 3000'}
    if kind == 'metrics' and 'metric' in args and (not isinstance(args['metric'], str) or not args['metric']):
        return {'error': 'metric must be a nonempty exact metric name'}
    if kind == 'history' and 'search' in args and (not isinstance(args['search'], str) or not args['search']):
        return {'error': 'search must be a nonempty literal SQL substring'}

    filename = {'schema': 'schema.json', 'history': 'history.jsonl', 'metrics': 'metrics.json'}[kind]
    path = packet / filename
    if not path.is_file():
        return {'unavailable': kind}
    source = path.read_text()
    if kind == 'metrics':
        try:
            spans = _metric_spans(source)
        except (ValueError, json.JSONDecodeError) as error:
            return {'error': 'Invalid metrics source: ' + str(error)}
        if 'metric' in args:
            selected = [(item, first, last) for item, first, last in spans
                        if isinstance(item, dict) and item.get('name', item.get('metric_name')) == args['metric']]
            if not selected:
                return {'error': 'Unknown metric name'}
            if len(selected) != 1:
                return {'error': 'Metric name is not unique in the source'}
            text = source[selected[0][1]:selected[0][2]]
        else:
            inventory = []
            for item, _, _ in spans:
                if not isinstance(item, dict):
                    return {'error': 'Metric definition is not an object'}
                name = item.get('name', item.get('metric_name'))
                if not isinstance(name, str) or not name:
                    return {'error': 'Metric definition lacks a name'}
                description = item.get('description', '')
                inventory.append({'name': name, 'description': description})
            text = json.dumps(inventory, ensure_ascii=False, separators=(',', ':'))
    elif kind == 'history' and 'search' in args:
        matching = []
        for line in source.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                return {'error': 'Invalid query history JSONL'}
            sql = row.get('sql') if isinstance(row, dict) else None
            if isinstance(sql, str) and args['search'] in sql:
                matching.append(line)
        text = '\n'.join(matching) + ('\n' if matching else '')
    else:
        text = source
    if offset > len(text):
        return {'error': 'offset exceeds context length'}
    return _page(text, offset, limit)


def serve(packet, database, schema, audit):
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get('method')
            if 'id' not in request:
                continue
            if method == 'initialize':
                result = {'protocolVersion': request.get('params', {}).get('protocolVersion', '2024-11-05'),
                          'capabilities': {'tools': {}}, 'serverInfo': {'name': 'paired-sqlite-validation', 'version': '1'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                params = request['params']; args = params.get('arguments', {}); name = params['name']
                if name == 'read_context':
                    value = read_context(packet, args)
                elif name == 'execute_sql' and set(args) == {'sql'} and isinstance(args['sql'], str):
                    value = execute(database, schema, args['sql'])
                else:
                    value = {'error': 'Unsupported tool or arguments'}
                with audit.open('a') as stream:
                    stream.write(json.dumps({'tool': name, 'arguments': args, 'result': value}) + '\n')
                result = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'isError': bool(value.get('error'))}
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'Unsupported method'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
        except (ValueError, KeyError, OSError) as error:
            print(json.dumps({'jsonrpc': '2.0', 'id': request.get('id'), 'error': {'code': -32603, 'message': str(error)}}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--schema', default='main_data')
    parser.add_argument('--audit', type=Path, required=True)
    args = parser.parse_args()
    serve(args.packet, args.database, args.schema, args.audit)
