#!/usr/bin/env python3
"""Survey a local analytics project and retain its data binding and Trident receipts.

Authoring and all Registry calls remain in the existing skills and authenticated MCP.
This helper never publishes, executes repository scripts, installs software or runs evals.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'dashboard_runtime'))
from dashboard_run import Run
from trident.core import digest
from trident.powerbi_file import inspect_file, measure_type
import local_data
import local_prepare
import metrics_mine as mine
from analytics_eval import filter_querylog

SKIP = {'node_modules', 'venv', '__pycache__', 'dist', 'build', 'vendor'}
SENSITIVE = ('credential', 'secret', 'password', 'token', 'keychain')


class ProjectError(ValueError):
    pass


def safe_path(path, root=None):
    p = Path(path).expanduser().absolute()
    if any(x.is_symlink() for x in (p, *p.parents)):
        raise ProjectError('Use an ordinary local path without symlinks.')
    if root and not p.resolve().is_relative_to(Path(root).resolve()):
        raise ProjectError('The selected file is outside this project.')
    return p


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def source_kind(path):
    suffix, name = path.suffix.lower(), path.name.lower()
    if suffix in ('.pbix', '.pbit', '.bim'):
        return 'powerbi'
    if suffix == '.duckdb':
        return 'duckdb'
    if suffix in ('.jsonl', '.json', '.csv') and any(
            s in str(path).lower() for s in ('history', 'querylog', 'query_log')):
        return 'query_history'
    if suffix == '.sql':
        return 'saved_sql'
    if name == 'rowsets.json':
        return 'rowsets'
    if suffix in ('.lkml', '.yml', '.yaml'):
        return 'model_candidate'
    if suffix in ('.csv', '.parquet'):
        return 'data_file'
    return None


class Project(Run):
    """Uses the existing run index and private atomic storage without dashboard jobs."""
    def current(self):
        state = self.manifest()
        if state.get('entrypoint') != 'project':
            raise ProjectError('Select the project setup run, not a dashboard or agent run.')
        return state

    def survey(self, folder):
        root = safe_path(folder)
        if not root.is_dir():
            raise ProjectError('Select an existing local project folder.')
        if self.path.exists() and self.current()['source_root'] != str(root):
            raise ProjectError('This run belongs to another project. Choose its run or a new run directory.')
        sources, inspected, limited = [], 0, False
        model_fingerprints = {}
        # Prefer report files over a BIM sidecar when their semantic contents are identical.
        paths = []
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP
                             and not (Path(directory) / d).is_symlink()
                             and not any(s in d.lower() for s in SENSITIVE)
                             and (Path(directory) / d).resolve() != self.root.resolve())
            for name in sorted(files):
                inspected += 1
                if inspected > 5000:
                    limited = True
                    break
                p = Path(directory) / name
                if name.startswith('.') or any(s in name.lower() for s in SENSITIVE) or p.is_symlink():
                    continue
                kind = source_kind(p.relative_to(root))
                if kind and p.is_file():
                    paths.append((p, kind))
            if limited:
                break
        safe_path(self.context)
        self.context.mkdir(exist_ok=True, mode=0o700)
        for p, kind in sorted(paths, key=lambda item: (item[0].suffix == '.bim', str(item[0]))):
            relative = p.relative_to(root).as_posix()
            item = {'source_id': 'src_' + digest(relative)[:16], 'path': relative, 'kind': kind,
                    'size_bytes': p.stat().st_size, 'status': 'available'}
            try:
                if kind == 'duckdb':
                    item['status'] = 'needs_binding'
                elif p.stat().st_size > (1024 ** 3 if kind == 'powerbi' else 32 * 1024 ** 2):
                    item['status'] = 'held_size_limit'
                else:
                    item['sha256'] = file_hash(p)
                    if kind == 'powerbi':
                        inventory = inspect_file(p)
                        for measure in inventory['measures']:
                            measure['resolved_datatype'] = measure_type(measure, inventory)
                            measure['type_status'] = 'resolved' if measure['resolved_datatype'] else 'needs_review'
                        signature = digest({k: inventory[k] for k in ('tables', 'measures', 'partitions')})
                        item['counts'] = inventory['counts']
                        if signature in model_fingerprints and p.suffix.lower() == '.bim':
                            item['supports_source'] = model_fingerprints[signature]
                            item['status'] = 'model_sidecar'
                        else:
                            model_fingerprints[signature] = item['source_id']
                        self.store.write(self.context / (item['source_id'] + '.json'), inventory)
                    elif kind == 'query_history':
                        item['records'] = len(mine.read_querylog_source(p))
                        if not item['records']:
                            item['status'] = 'held_no_query_records'
                    elif kind == 'model_candidate':
                        item['status'] = 'needs_model_review'
            except (ValueError, OSError, KeyError, TypeError):
                item['status'] = 'held_import'; item['reason'] = 'Inspect this source format separately.'
            sources.append(item)
        previous = self.current() if self.path.exists() else {}
        state = {**previous, 'version': 2, 'entrypoint': 'project',
                 'goal': 'publish_context_and_ask', 'source_root': str(root), 'sources': sources,
                 'survey_limited': limited, 'stages': previous.get('stages', {})}
        changed = previous.get('sources') is not None and previous['sources'] != sources
        if changed:
            state['context_changed'] = True
            state.pop('publication', None)
        self.store.write(self.path, state)
        return {'status': 'ok', 'sources': sources, 'survey_limited': limited,
                'context_changed': changed, 'run_dir': str(self.root), 'remote_writes': False}

    def history(self):
        state = self.current()
        inputs = []
        for item in state['sources']:
            if item['kind'] == 'query_history' and item['status'] == 'available':
                p = safe_path(Path(state['source_root']) / item['path'], state['source_root'])
                if file_hash(p) != item['sha256']:
                    raise ProjectError('A source changed. Survey the project again before mining.')
                inputs.append(str(p))
        if not inputs:
            return {'status': 'not_available', 'next': 'Continue with models or saved SQL.'}
        raw = self.context / 'querylog.jsonl'
        cleaned = self.context / 'querylog.clean.jsonl'
        shapes = self.context / 'shapes.jsonl'
        for p in (raw, cleaned, shapes):
            safe_path(p)
        # Imported records stay local; use the existing identifying-literal filter.
        result = mine.cmd_querylog(SimpleNamespace(src=inputs, dst=raw, append=False, limit=5000))
        privacy = filter_querylog(str(raw), str(cleaned))
        if mine.read_jsonl(cleaned):
            counts = mine.cmd_shapes(SimpleNamespace(src=cleaned, dst=shapes, tables=None, top=5000))
        else:
            shapes.write_text('')
            counts = {'written': 0}
        for p in (raw, cleaned, shapes):
            if p.exists():
                p.chmod(0o600)
        return {'status': 'ok', 'intake': result, 'privacy': privacy, 'shapes': counts,
                'source_ids': [s['source_id'] for s in state['sources'] if s['kind'] == 'query_history'],
                'next': 'Author and reconcile metrics and saved queries using the existing metrics workflow.'}

    def bind(self, database, tables=None):
        state = self.current()
        path = safe_path(database, state['source_root'])
        binding = {'kind': 'duckdb', 'path': str(path), 'python': sys.executable, 'read_only': True}
        metadata = local_data.call(binding, 'inspect')
        selected = tables if tables is not None else metadata['tables']
        if not selected or not set(selected) <= set(metadata['tables']):
            raise ProjectError('Select existing schema-qualified tables from the inspected database.')
        binding.update(tables=selected, schema_hash=metadata['schema_hash'])
        if state.get('execution') and state['execution'] != binding:
            state.pop('publication', None)
            state['context_changed'] = True
        state['execution'] = binding
        self.store.write(self.path, state)
        safe_path(self.context)
        self.store.write(self.context / 'database-schema.json', metadata)
        return {'status': 'ok', 'execution': binding, 'columns': metadata['columns']}

    def prepare(self, request):
        state = self.current()
        result = local_prepare.prepare(state['source_root'], state['sources'], request)
        self.store.write(self.context / 'data-preparation.json', result)
        return result

    def execute(self, request):
        state = self.current()
        if not state.get('execution'):
            raise ProjectError('Bind the selected database before checking SQL or answering.')
        return local_data.call(state['execution'], 'execute', request['sql'], request.get('parameters'),
                               request.get('limit', 100))

    def answer(self, request):
        """Validate full release readbacks and execute the selected released saved SQL."""
        checked = self.retrieved(request['readbacks'])
        query = next((a for a in checked['artifacts'] if a['id'] == request.get('artifact_id')), None)
        if not query or query['kind'] != 'sql_query':
            raise ProjectError('Select a saved query from these fresh released MCP responses.')
        sql = query['definition']['sql']
        # Registry templates become native bindings only outside quoted SQL and comments.
        sql = ''.join(re.sub(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}', r'$\1', text)
                      if kind == 'code' else text for kind, text in mine.scan(sql))
        result = self.execute({'sql': sql, 'parameters': request.get('parameters', {}),
                               'limit': request.get('limit', 100)})
        return {**result, 'retrieval_check': 'passed', 'sql': sql,
                'used_release': {k: query[k] for k in
                                 ('id', 'name', 'kind', 'release_id', 'version_id', 'channel')}}

    def releases(self, payload):
        """Normalize complete MCP released-artifact responses without truncating definitions."""
        if not isinstance(payload, dict):
            raise ProjectError('Supply a workspace and a list of complete MCP artifact responses.')
        workspace = payload.get('workspace_id')
        if not isinstance(workspace, str) or not workspace:
            raise ProjectError('Supply the selected workspace and fresh released-artifact readbacks.')
        if not isinstance(payload.get('artifacts'), list) or not payload['artifacts']:
            raise ProjectError('No released artifacts were read back; publication is pending.')
        seen, normalized = set(), []
        for raw in payload['artifacts']:
            if not isinstance(raw, dict) or not isinstance(raw.get('release'), dict) or not isinstance(raw.get('metadata'), dict):
                raise ProjectError('Supply complete released MCP get_artifact responses, not summaries, search hits or local definitions.')
            release = raw.get('release') or {}
            definition = (raw.get('metadata') or {}).get('definition')
            if (raw.get('is_released') is not True or release.get('is_current') is not True
                    or release.get('release_status') != 'released' or not isinstance(definition, dict)):
                raise ProjectError('Supply complete released MCP get_artifact responses, not summaries, search hits or local definitions.')
            artifact = {**{k: raw.get(k) for k in ('workspace_id', 'id', 'kind', 'name')},
                        **{k: release.get(k) for k in ('release_id', 'version_id', 'channel')},
                        'definition': definition}
            if artifact.get('workspace_id') != workspace or artifact.get('kind') not in ('metric', 'sql_query'):
                raise ProjectError('Every readback must be a metric or query in the selected workspace.')
            if any(not isinstance(artifact.get(k), str) or not artifact[k] for k in
                   ('id', 'name', 'release_id', 'version_id', 'channel')) or not isinstance(artifact.get('definition'), dict):
                raise ProjectError('Read back a released revision, including its definition and release receipt.')
            if artifact['id'] in seen or raw.get('withdrawn') or raw.get('artifact_status') != 'active' or artifact['definition'].get('review_status') == 'rejected':
                raise ProjectError('Readbacks contain a duplicate, withdrawn or rejected artifact.')
            check = mine.check_metric if artifact['kind'] == 'metric' else mine.check_query
            errors, _ = check({'name': artifact['name'], 'display_name': raw.get('display_name'),
                               'description': raw.get('description'), 'definition': definition})
            if errors:
                raise ProjectError('A released definition is incomplete or unsupported. Fetch its complete MCP response and review its schema.')
            seen.add(artifact['id'])
            normalized.append(artifact)
        return normalized

    def publication(self, payload):
        state = self.current()
        artifacts = self.releases(payload)
        receipt = {'workspace_id': payload['workspace_id'], 'artifacts': [
            {**{k: a[k] for k in ('id', 'kind', 'name', 'release_id', 'version_id', 'channel')},
             'definition_hash': digest(a['definition'])} for a in artifacts]}
        state['publication'] = receipt
        state['context_changed'] = False
        self.store.write(self.path, state)
        return {'status': 'ok', 'recorded_from': 'host_mcp_release_readbacks', **receipt,
                'next': 'Suggest supported questions, then retrieve from Trident for the user question.'}

    def retrieved(self, payload):
        state = self.current()
        receipt = state.get('publication')
        if not receipt or payload.get('workspace_id') != receipt['workspace_id']:
            raise ProjectError('Read back the published workspace before retrieving question context.')
        artifacts = self.releases(payload)
        recorded = {a['id']: a for a in receipt['artifacts']}
        for a in artifacts:
            known = recorded.get(a['id'])
            if not known or any(a[k] != known[k] for k in ('release_id', 'version_id', 'channel')) or digest(a['definition']) != known['definition_hash']:
                raise ProjectError('Retrieved context differs from the published receipt. Review the release and refresh the receipt.')
        return {'status': 'ok', 'workspace_id': receipt['workspace_id'], 'artifacts': artifacts,
                'execution': state.get('execution'), 'next': 'Use these retrieved definitions to execute fresh SQL.'}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--run-dir', required=True)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('survey').add_argument('folder')
    sub.add_parser('history')
    sub.add_parser('status')
    b = sub.add_parser('bind'); b.add_argument('database'); b.add_argument('--tables', nargs='+')
    for name in ('prepare', 'execute', 'publication', 'retrieved', 'answer'):
        sub.add_parser(name).add_argument('request', type=Path)
    args = p.parse_args()
    try:
        project = Project(args.run_dir)
        if args.command == 'survey': result = project.survey(args.folder)
        elif args.command == 'history': result = project.history()
        elif args.command == 'status': result = project.current()
        elif args.command == 'bind': result = project.bind(args.database, args.tables)
        else: result = getattr(project, args.command)(project.read(safe_path(args.request)))
        print(json.dumps(result, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, mine.Blocked) as exc:
        print(json.dumps({'status': 'blocked', 'message': str(exc) if isinstance(exc, (ProjectError, local_data.DataError, mine.Blocked))
                          else 'Project step could not complete. Check the selected files, source state and request fields.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
