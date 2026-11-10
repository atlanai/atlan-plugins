"""Validate complete host MCP readbacks and prepare backend-neutral saved-query plans.

This module does not contact Atlan. The parent obtains readbacks with its own MCP
connection and dispatches warehouse plans through an existing read-only tool.
"""
import re
import context_contracts as mine
from context_state import digest

class RetrievalError(ValueError):
    pass


def releases(payload):
    """Normalize complete MCP released-artifact responses without truncating definitions."""
    if not isinstance(payload, dict):
        raise RetrievalError('Supply a workspace and a list of complete MCP artifact responses.')
    workspace = payload.get('workspace_id')
    if not isinstance(workspace, str) or not workspace:
        raise RetrievalError('Supply the selected workspace and fresh released-artifact readbacks.')
    if not isinstance(payload.get('artifacts'), list) or not payload['artifacts']:
        raise RetrievalError('No released artifacts were read back; publication is pending.')
    seen, normalized = set(), []
    for raw in payload['artifacts']:
        if not isinstance(raw, dict) or not isinstance(raw.get('release'), dict) or not isinstance(raw.get('metadata'), dict):
            raise RetrievalError('Supply complete released MCP get_artifact responses, not summaries, search hits or local definitions.')
        release = raw.get('release') or {}
        definition = (raw.get('metadata') or {}).get('definition')
        if (raw.get('is_released') is not True or release.get('is_current') is not True
                or release.get('release_status') != 'released' or not isinstance(definition, dict)):
            raise RetrievalError('Supply complete released MCP get_artifact responses, not summaries, search hits or local definitions.')
        artifact = {**{k: raw.get(k) for k in ('workspace_id', 'id', 'kind', 'name')},
                    **{k: release.get(k) for k in ('release_id', 'version_id', 'channel')},
                    'definition': definition}
        if artifact.get('workspace_id') != workspace or artifact.get('kind') not in ('metric', 'sql_query'):
            raise RetrievalError('Every readback must be a metric or query in the selected workspace.')
        if any(not isinstance(artifact.get(k), str) or not artifact[k] for k in
               ('id', 'name', 'release_id', 'version_id', 'channel')) or not isinstance(artifact.get('definition'), dict):
            raise RetrievalError('Read back a released revision, including its definition and release receipt.')
        if artifact['id'] in seen or raw.get('withdrawn') or raw.get('artifact_status') != 'active' or artifact['definition'].get('review_status') == 'rejected':
            raise RetrievalError('Readbacks contain a duplicate, withdrawn or rejected artifact.')
        check = mine.check_metric if artifact['kind'] == 'metric' else mine.check_query
        errors, _ = check({'name': artifact['name'], 'display_name': raw.get('display_name'),
                           'description': raw.get('description'), 'definition': definition})
        if errors:
            raise RetrievalError('A released definition is incomplete or unsupported. Fetch its complete MCP response and review its schema.')
        seen.add(artifact['id'])
        normalized.append(artifact)
    return normalized


def validate(payload, receipt=None):
    artifacts = releases(payload)
    if receipt is not None:
        if payload['workspace_id'] != receipt['workspace_id']:
            raise RetrievalError('Read back the published workspace before retrieving question context.')
        recorded = {a['id']: a for a in receipt['artifacts']}
        for artifact in artifacts:
            known = recorded.get(artifact['id'])
            if (not known or any(artifact[k] != known[k] for k in ('release_id', 'version_id', 'channel'))
                    or digest(artifact['definition']) != known['definition_hash']):
                raise RetrievalError('Retrieved context differs from the published receipt. Review the release and refresh the receipt.')
    return {'status': 'ok', 'workspace_id': payload['workspace_id'], 'artifacts': artifacts}


def query_plan(request, execution, receipt=None):
    checked = validate(request['readbacks'], receipt)
    query = next((a for a in checked['artifacts'] if a['id'] == request.get('artifact_id')), None)
    if not query or query['kind'] != 'sql_query':
        raise RetrievalError('Select a saved query from these fresh released MCP responses.')
    if not execution or execution.get('kind') not in ('duckdb', 'sqlite', 'warehouse'):
        raise RetrievalError('Select an existing local or read-only warehouse connection.')
    if execution.get('read_only') is False or (execution['kind'] == 'warehouse' and not execution.get('tool')):
        raise RetrievalError('Select the existing read-only warehouse query tool.')
    definition = query['definition']
    if definition['dialect'].lower() != execution.get('dialect', execution['kind']).lower():
        raise RetrievalError('The released SQL dialect differs from the selected connection. Select a matching connection or review a new query revision.')
    sql = definition['sql']
    code = ''.join(text for kind, text in mine.scan(sql) if kind == 'code')
    template = r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}'
    names = (set(re.findall(template, code)) | set(re.findall(r'\$([A-Za-z_][A-Za-z_0-9]*)\b', code))
             | set(re.findall(r'(?<!:):([A-Za-z_][A-Za-z_0-9]*)\b', code))
             | set(re.findall(r'%\(([A-Za-z_][A-Za-z_0-9]*)\)s', code)))
    parameters = request.get('parameters', {})
    if not isinstance(parameters, dict) or set(parameters) != names:
        raise RetrievalError('Supply exactly the named parameters in the released SQL.')
    limit = request.get('limit', 100)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise RetrievalError('Use a row limit from 1 to 1000.')
    marker = {'duckdb': '$', 'sqlite': ':'}.get(execution['kind'])
    native = ''.join(re.sub(template, marker + r'\1', text) if kind == 'code' else text
                     for kind, text in mine.scan(sql)) if marker else sql
    return {'status': 'ready', 'retrieval_check': 'passed', 'sql': native,
            'parameters': parameters, 'limit': limit, 'execution': execution,
            'binding_style': {'duckdb': 'duckdb_named', 'sqlite': 'sqlite_named'}.get(execution['kind'], 'host_native'),
            'used_release': {k: query[k] for k in ('id', 'name', 'kind', 'release_id', 'version_id', 'channel')},
            'definition_hash': digest(definition), 'workspace_id': checked['workspace_id']}
