#!/usr/bin/env python3
"""Survey a local analytics project and retain its data binding and Atlan receipts.

The metrics engine owns authoring; Registry calls use authenticated MCP.
This helper never publishes, executes repository scripts, installs software or runs evals.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
import uuid
from types import SimpleNamespace

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'dashboard_runtime'))
from context_state import ContextState, resolve_run
import retrieval
import page_scope
import metric_preview
import progress
import acquisition
import auth_recovery
from source_inventory import discover, source_kind, SENSITIVE
from trident.core import digest
from trident.powerbi_file import inspect_file, measure_type
import local_data
import local_prepare
import context_contracts as mine
from analytics_eval import filter_querylog

# Local engines the helper executes itself; SQLite needs only the standard library.
_card_path = Path(__file__).resolve().parents[2] / 'analytics-metrics/modules/metrics/engine/src/metrics_agent/lib/card_sql.py'
_card_spec = importlib.util.spec_from_file_location('analytics_card_sql', _card_path)
_card_sql = importlib.util.module_from_spec(_card_spec)
_card_spec.loader.exec_module(_card_sql)

LOCAL = ('duckdb', 'sqlite')

class ProjectError(ValueError):
    pass


def safe_path(path, root=None):
    p = Path(path).expanduser().absolute()
    if any(x.is_symlink() for x in (p, *p.parents)):
        raise ProjectError('Use an ordinary local path without symlinks.')
    if root and not p.resolve().is_relative_to(Path(root).resolve()):
        raise ProjectError('The selected file is outside this project.')
    return p.resolve()


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

class Project(ContextState):
    """Keeps compatible project run state without creating dashboard or agent jobs."""
    def current(self):
        state = self.manifest()
        if state.get('entrypoint') != 'project':
            raise ProjectError('Select the project setup run, not a dashboard or agent run.')
        return state

    def auth(self, request):
        return auth_recovery.advance(self, request)

    def journey(self, action='status', focus=None, host=None, server=None, answer=None):
        import journey
        return journey.advance(self, action, focus, host=host, server=server, answer=answer)

    def experience(self, engine_dir=None, format='chat', detail=False, focus=None, host=None, server=None):
        """Present existing evidence without advancing the engine or publication."""
        import experience
        model = experience.snapshot(self, engine_dir, focus=focus, host=host, server=server)
        if format == 'json':
            return model
        chat = experience.render_chat(model, detail=detail)
        if format == 'chat':
            return chat
        try:
            self.write_text(self.root / 'experience.html', experience.render_html(model))
        except (ValueError, OSError):
            return {'status': 'text_fallback', 'chat': chat,
                    'next': 'Show this metric in chat; the optional snapshot is unavailable.'}
        return {'status': 'ok', 'snapshot': str(self.root / 'experience.html'), 'chat': chat,
                'next': 'Show the chat summary. Open this snapshot only in an available, requested host surface.'}

    def archive_candidates(self):
        """Keep stale local work reviewable, but never present it as current scope."""
        folder = safe_path(self.root / 'previous-context' / uuid.uuid4().hex)
        for name in ('metrics.json', 'queries.json', 'links.json', 'candidate-checks.json', 'plan.json'):
            path = safe_path(self.context / name)
            if path.exists():
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.rename(folder / name)

    def survey(self, folder, budget=50):
        started = time.monotonic()
        source = safe_path(folder)
        selected = source if source.is_file() else None
        root = source.parent if selected else source
        if not root.is_dir() or (selected and (selected.name.startswith('.') or any(s in selected.name.lower() for s in SENSITIVE))):
            raise ProjectError('Select an existing analytics folder or supported file.')
        if self.path.exists() and (self.current()['source_root'] != str(root)
                                  or self.current().get('selected_file') != (str(selected) if selected else None)):
            raise ProjectError('This run belongs to another source. Choose its run or a new run directory.')
        previous = self.current() if self.path.exists() else {}
        cache_path = self.root / 'survey-cache.json'
        checkpoint = self.read(cache_path) if cache_path.exists() else {}
        cached = {s['source_id']: s for s in (checkpoint.get('sources', [])
                  if checkpoint.get('source_root') == str(root) else previous.get('sources', []))}
        sources, model_fingerprints = [], {}
        paths, limited = discover(root, self.root.resolve(), selected=selected)
        if selected and not paths:
            raise ProjectError('This file format is not recognized. Select its model export or containing folder.')
        progress.update(self, 'inspect', completed=0, total=len(paths))
        safe_path(self.context)
        self.context.mkdir(exist_ok=True, mode=0o700)
        pending = False
        for p, kind in sorted(paths, key=lambda item: (item[0].suffix == '.bim', str(item[0]))):
            relative = p.relative_to(root).as_posix()
            item = {'source_id': 'src_' + digest(relative)[:16], 'path': relative, 'kind': kind,
                    'size_bytes': p.stat().st_size, 'status': 'available'}
            try:
                if kind in LOCAL:
                    item['status'] = 'needs_binding'
                elif p.stat().st_size > (1024 ** 3 if kind == 'powerbi' else 32 * 1024 ** 2):
                    item['status'] = 'held_size_limit'
                else:
                    item['sha256'] = file_hash(p)
                    if kind == 'powerbi':
                        cache = self.context / (item['source_id'] + '.json')
                        old = cached.get(item['source_id'], {})
                        saved = self.read(cache) if cache.exists() else None
                        if (old.get('sha256') == item['sha256'] and saved is not None
                                and old.get('inventory_hash') == digest(saved)):
                            inventory = saved
                        elif time.monotonic() - started >= budget:
                            item['status'] = 'pending_inspection'
                            pending = True
                            sources.append(item)
                            continue
                        else:
                            inventory = inspect_file(p, allow_report_only=True)
                        if inventory.get('file_sha256', item['sha256']) != item['sha256']:
                            raise ProjectError('The source changed during inspection. Survey it again.')
                        for measure in inventory['measures']:
                            measure['resolved_datatype'] = measure_type(measure, inventory)
                            measure['type_status'] = 'resolved' if measure['resolved_datatype'] else 'needs_review'
                        signature = digest({k: inventory.get(k) for k in
                                            ('tables', 'measures', 'partitions', 'relationships', 'security', 'power_query')})
                        item['counts'] = inventory['counts']
                        if inventory.get('model_metadata_available') is False:
                            item['status'] = 'held_missing_model'
                            item['reason'] = 'Page layout is available. Retrieve the linked model or use the approved decoder before authoring measures.'
                        elif signature in model_fingerprints and p.suffix.lower() == '.bim' and not inventory.get('security_roles_present'):
                            item['supports_source'] = model_fingerprints[signature]
                            item['status'] = 'model_sidecar'
                        else:
                            model_fingerprints[signature] = item['source_id']
                        self.store.write(self.context / (item['source_id'] + '.json'), inventory)
                        item['inventory_hash'] = digest(inventory)
                    elif kind == 'query_history':
                        item['records'] = len(mine.read_querylog_source(p))
                        if not item['records']:
                            item['status'] = 'held_no_query_records'
                    elif kind in ('model_candidate', 'powerbi_project', 'tableau', 'dashboard_html'):
                        item['status'] = 'needs_model_review'
                        item['reason'] = 'Use the source reader for this format; detection is not parsed coverage.'
            except (ValueError, OSError, KeyError, TypeError):
                item['status'] = 'held_import'; item['reason'] = 'Inspect this source format separately.'
            sources.append(item)
            self.write(cache_path, {'source_root': str(root), 'sources': sources})
            progress.update(self, 'inspect', completed=len(sources), total=len(paths))
        state = {**previous, 'version': 2, 'entrypoint': 'project',
                 'goal': 'publish_context_and_ask', 'source_root': str(root), 'sources': sources,
                 'selected_file': str(selected) if selected else None,
                 'survey_limited': limited, 'stages': previous.get('stages', {})}
        # Adding cache integrity metadata to an older run is not a source change.
        old_sources = {s['source_id']: s for s in previous.get('sources', [])}
        new_sources = {s['source_id']: s for s in sources}
        def comparable(item):
            return {k: v for k, v in item.items() if k != 'inventory_hash'}
        changed = previous.get('sources') is not None and (
            {k: comparable(v) for k, v in old_sources.items()} != {k: comparable(v) for k, v in new_sources.items()}
            or any(old_sources[k].get('inventory_hash') and old_sources[k]['inventory_hash'] != v.get('inventory_hash')
                   for k, v in new_sources.items() if k in old_sources))
        if changed:
            self.archive_candidates()
            state['context_changed'] = True
            state.pop('publication', None)
            state.pop('scope', None)
            state.pop('scopes', None)
            self.store.write(self.context / 'candidate-checks.json', {})
        self.store.write(self.path, state)
        held = any(s['status'].startswith('held') or s['status'] == 'needs_model_review' for s in sources)
        progress.update(self, 'inspect', 'running' if pending else 'complete-with-held-items' if held else 'complete',
                        completed=sum(s['status'] != 'pending_inspection' for s in sources), total=len(paths))
        return {'status': 'continue' if pending else 'ok', 'sources': sources, 'survey_limited': limited,
                'context_changed': changed, 'run_dir': str(self.root), 'remote_writes': False}

    def scope(self, request):
        state = self.current()
        source = next((s for s in state['sources'] if s['source_id'] == request.get('source_id')), None)
        if not source or source['kind'] != 'powerbi':
            raise ProjectError('Select a parsed Power BI source from the inventory; other readers retain explicit scope in authoring evidence.')
        if file_hash(safe_path(Path(state['source_root']) / source['path'], state['source_root'])) != source.get('sha256'):
            raise ProjectError('A source changed. Survey it again before selecting pages.')
        inventory = self.read(self.context / (source['source_id'] + '.json'))
        result = page_scope.select(inventory, request.get('pages'))
        tables = request.get('history_tables', [])
        if not isinstance(tables, list) or any(not isinstance(t, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*){1,2}', t) for t in tables):
            raise ProjectError('Supply evidenced, qualified physical table names for history scope.')
        scope = {'source_id': source['source_id'], 'source_hash': source['sha256'],
                 'selection': result, 'history_tables': tables,
                 'narrowed': request.get('pages') not in (None, [], ['all'])}
        scopes = state.get('scopes', {})
        if not scopes and state.get('scope'):
            scopes[state['scope']['source_id']] = state['scope']
        if scopes.get(source['source_id']) != scope:
            self.archive_candidates()
            state.pop('publication', None)
            state['context_changed'] = True
            self.write(self.context / 'candidate-checks.json', {})
        state['scope'] = scope
        scopes[source['source_id']] = scope
        state['scopes'] = scopes
        self.write(self.path, state)
        progress.update(self, 'scope', 'complete-with-held-items' if result['held'] else 'complete')
        return {'status': 'ok', **scope}

    def preview(self, limit=5, format='markdown', kind='metric'):
        # This is a read-only action. A failed rich renderer cannot consume the choice.
        if format == 'chat':
            return metric_preview.chat(self, limit)
        text = metric_preview.markdown(self, limit, kind)
        if format == 'markdown':
            return text
        path = self.root / 'progress.json'
        snapshot = self.read(path) if path.exists() else {'summary': 'Local candidates; not yet shared.'}
        try:
            self.write_text(self.root / 'preview.html', metric_preview.html_snapshot(self, snapshot))
            return {'status': 'ok', 'snapshot': str(self.root / 'preview.html'), 'markdown': text}
        except Exception:
            return {'status': 'text_fallback', 'markdown': text}


    def check(self):
        items = metric_preview.candidates(self)
        # Preserve not-yet-visited execution evidence if this pass is canceled.
        checks = {item['key']: item['check'] for item in items if item['check']}
        progress.update(self, 'check', completed=0, total=len(items))
        for index, item in enumerate(items):
            checker = mine.check_metric if item['kind'] == 'metric' else mine.check_query
            errors, warnings = checker(item['body'])
            prior = item['check']
            failed = any(s.get('status') == 'failed' for s in prior.get('samples', {}).values())
            if failed:
                errors.append('A parameter sample still fails SQL execution.')
            checks[item['key']] = {**prior, 'definition_hash': item['definition_hash'],
                'status': 'held' if errors else prior['status'] if prior.get('status') in ('executed', 'example_executed') else 'schema_checked',
                'errors': errors, 'warnings': warnings}
            self.write(self.context / 'candidate-checks.json', checks)
            progress.update(self, 'check', completed=index + 1, total=len(items))
        return progress.update(self, 'check', 'complete-with-held-items' if any(c['errors'] for c in checks.values()) else 'complete',
                               completed=len(items), total=len(items))

    def check_query(self, request):
        return self.check_execution(request, 'sql_query')

    def check_metric(self, request):
        return self.check_execution(request, 'metric')

    def check_execution(self, request, kind):
        items = metric_preview.candidates(self)
        item = next((i for i in items if i['key'] == kind + ':' + request.get('name', '')), None)
        if not item:
            raise ProjectError('Select an authored metric or saved query by name.')
        checker = mine.check_query if kind == 'sql_query' else mine.check_metric
        errors, warnings = checker(item['body'])
        if errors:
            raise ProjectError('Correct the candidate schema errors before executing SQL.')
        definition = item['body']['definition']
        if kind == 'metric':
            index = request.get('example_index', 0)
            examples = definition.get('examples', [])
            if type(index) is not int or not 0 <= index < len(examples) or not examples[index].get('sql'):
                raise ProjectError('Select an authored SQL example by its zero-based example_index.')
            source_sql = examples[index]['sql']
        else:
            source_sql = definition['sql']
        binding = self.execution_binding()
        if not binding or binding['kind'] not in LOCAL or definition['dialect'].lower() != binding['kind']:
            raise ProjectError('Use the recorded read-only warehouse tool for this dialect and retain its check evidence.')
        marker = '$' if binding['kind'] == 'duckdb' else ':'
        sql = ''.join(re.sub(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}', marker + r'\1', text)
                      if token_kind == 'code' else text for token_kind, text in mine.scan(source_sql))
        path = self.context / 'candidate-checks.json'
        checks = self.read(path) if path.exists() else {}
        prior = checks.get(item['key'], {})
        samples = prior.get('samples', {}) if prior.get('definition_hash') == item['definition_hash'] else {}
        sample_id = digest({'example_index': index, 'parameters': request.get('parameters', {})}
                           if kind == 'metric' else request.get('parameters', {}))
        composition = None
        try:
            example_query = {'sql': sql, 'parameters': request.get('parameters', {}),
                             'limit': request.get('limit', 100)}
            if kind == 'metric':
                card_sql = _card_sql.canonical_statement(definition)
                card_sql = ''.join(re.sub(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}', marker + r'\1', text)
                                   if token_kind == 'code' else text for token_kind, text in mine.scan(card_sql))
                code = ''.join(text for token_kind, text in mine.scan(card_sql) if token_kind == 'code')
                needed = set(re.findall(r'(?<![:\w])[:$]([A-Za-z_][A-Za-z_0-9]*)\b', code))
                available = {p['name']: p['default'] for p in definition['source'].get('parameters', [])}
                available.update(request.get('parameters', {}))
                if not needed <= available.keys():
                    raise local_data.DataError('Supply the named parameters required by the metric calculation.')
                calculation_query = {'sql': card_sql, 'parameters': {k: available[k] for k in needed},
                                     'limit': request.get('limit', 100)}
                empty_query = {'sql': card_sql + ('\nAND 1=0' if definition.get('mandatory_filters') else '\nWHERE 1=0'),
                               'parameters': {k: available[k] for k in needed}, 'limit': 100}
                result, calculated, empty = local_data.call_batch(
                    binding, [example_query, calculation_query, empty_query])
                composition = {'status': 'executed', 'query_id': calculated['query_id'],
                               'sql_hash': calculated['sql_hash'], 'rows': calculated['rows'],
                               'coverage': 'formula_source_mandatory_filters'}
                composition['empty_population'] = {'query_id': empty['query_id'], 'rows': empty['rows']}
            else:
                result = self.execute(example_query)

        except local_data.DataError:
            samples[sample_id] = {'status': 'failed'}
            checks[item['key']] = {'definition_hash': item['definition_hash'], 'status': 'held',
                                   'samples': samples,
                                   'errors': ['SQL execution failed; review the selected parameters and source mapping.']}
            self.write(path, checks)
            progress.update(self, 'check', 'complete-with-held-items')
            raise
        samples[sample_id] = {'status': 'executed', 'query_id': result['query_id'], 'sql_hash': result['sql_hash']}
        failed = any(s['status'] == 'failed' for s in samples.values())
        checks[item['key']] = {'definition_hash': item['definition_hash'],
                               'status': 'held' if failed else 'example_executed' if kind == 'metric' else 'executed',
                               'samples': samples, 'query_id': result['query_id'], 'sql_hash': result['sql_hash'],
                               **({'composition': composition} if composition is not None else {}),
                               'errors': ['A parameter sample still fails SQL execution.'] if failed else [],
                               'coverage': 'authored_example_only' if kind == 'metric' else 'observed_parameter_samples',
                               'warnings': warnings}
        self.write(path, checks)
        progress.update(self, 'check')
        return result

    def history(self):
        state = self.current()
        inputs = []
        for item in state['sources']:
            if item['kind'] == 'query_history' and item['status'] == 'available':
                p = safe_path(Path(state['source_root']) / item['path'], state['source_root'])
                if file_hash(p) != item['sha256']:
                    raise ProjectError('A source changed. Survey the project again before capturing history.')
                inputs.append(str(p))
        scopes = list(state.get('scopes', {}).values()) or [state.get('scope', {})]
        tables = sorted({t for scope in scopes for t in scope.get('history_tables', [])})
        if any(scope.get('narrowed') and not scope.get('history_tables') for scope in scopes):
            return {'status': 'needs_table_mapping', 'next': 'Map the selected pages to physical tables before capturing history.'}
        if tables:
            self.write_text(self.context / 'history-tables.txt', '\n'.join(tables))
        if not inputs:
            return {'status': 'not_available', 'next': 'Continue with models or saved SQL.'}
        raw = self.context / 'querylog.jsonl'
        cleaned = self.context / 'querylog.clean.jsonl'
        for p in (raw, cleaned):
            safe_path(p)
        result = mine.cmd_querylog(SimpleNamespace(src=inputs, dst=raw, append=False, limit=5000))
        privacy = filter_querylog(str(raw), str(cleaned))
        for p in (raw, cleaned):
            if p.exists():
                p.chmod(0o600)
        return {'status': 'ok', 'intake': result, 'privacy': privacy, 'history': str(cleaned), 'tables': tables,
                'source_ids': [s['source_id'] for s in state['sources'] if s['kind'] == 'query_history'],
                'next': 'Pass the captured history to the metrics engine local-files input. The engine owns shaping and authoring.'}

    def bind(self, database, tables=None, schema=None):
        state = self.current()
        # Binding is an explicit input independent of the report's filesystem location.
        path = safe_path(database)
        kind = local_data.KINDS.get(path.suffix.lower(), 'duckdb')
        binding = {'kind': kind, 'path': str(path), 'python': sys.executable, 'read_only': True}
        if kind == 'sqlite':
            binding['schema'] = schema or path.stem
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
        if not state.get('execution') or state['execution'].get('kind') not in LOCAL:
            raise ProjectError('Use the selected warehouse tool, or bind the local database before local execution.')
        return local_data.call(state['execution'], 'execute', request['sql'], request.get('parameters'),
                               request.get('limit', 100))

    def execution_binding(self):
        state = self.current()
        if state.get('execution'):
            return state['execution']
        legacy = self.root / 'state.json'
        warehouse = self.read(legacy).get('warehouse') if legacy.exists() else None
        if warehouse and warehouse.get('tool'):
            return {**warehouse, 'kind': 'warehouse', 'dialect': warehouse.get('dialect') or warehouse['kind']}
        return None

    def answer(self, request):
        """Validate once, execute locally or hand the plan to the authenticated host."""
        receipt = self.current().get('publication')
        if not receipt:
            raise ProjectError('Read back the published workspace before retrieving question context.')
        plan = retrieval.query_plan(request, self.execution_binding(), receipt)
        if plan['execution']['kind'] == 'warehouse':
            return {**plan, 'status': 'execute_in_host',
                    'next': 'Use the recorded read-only tool with separate native parameter bindings; report its fresh result.'}
        result = self.execute(plan)
        return {**result, 'retrieval_check': 'passed', 'sql': plan['sql'], 'used_release': plan['used_release']}

    def releases(self, payload):
        return retrieval.releases(payload)

    def publication(self, payload):
        state = self.current()
        artifacts = self.releases(payload)
        receipt = {'workspace_id': payload['workspace_id'], 'artifacts': [
            {**{k: a[k] for k in ('id', 'kind', 'name', 'release_id', 'version_id', 'channel')},
             'definition_hash': digest(a['definition'])} for a in artifacts]}
        state['publication'] = receipt
        state['context_changed'] = False
        self.store.write(self.path, state)
        progress.update(self, 'publish', 'complete')
        return {'status': 'ok', 'recorded_from': 'host_mcp_release_readbacks', **receipt,
                'next': 'Suggest supported questions, then retrieve from Atlan for the user question.'}

    def retrieved(self, payload):
        state = self.current()
        receipt = state.get('publication')
        if not receipt or payload.get('workspace_id') != receipt['workspace_id']:
            raise ProjectError('Read back the published workspace before retrieving question context.')
        checked = retrieval.validate(payload, receipt)
        return {**checked, 'execution': self.execution_binding(),
                'next': 'Use these retrieved definitions to execute fresh SQL.'}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--run-dir')
    p.add_argument('--full', action='store_true', help='Return full structured results instead of a compact artifact reference.')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('survey').add_argument('folder')
    sub.add_parser('history')
    sub.add_parser('status')
    sub.add_parser('check')
    location = sub.add_parser('location')
    location.add_argument('--workspace', default='.')
    location.add_argument('--permitted-cache')
    location.add_argument('--home-permitted', action='store_true')
    preview = sub.add_parser('preview')
    preview.add_argument('--limit', type=int, default=5)
    preview.add_argument('--all', action='store_true')
    preview.add_argument('--format', choices=('markdown', 'html', 'chat'), default='markdown')
    preview.add_argument('--kind', choices=('metric', 'sql_query'), default='metric')
    experience = sub.add_parser('experience')
    experience.add_argument('--engine-dir', type=Path)
    experience.add_argument('--format', choices=('chat', 'html', 'json'), default='chat')
    experience.add_argument('--detail', action='store_true')
    experience.add_argument('--focus', help='Exact saved metric identity to retain across refreshes')
    experience.add_argument('--host', choices=('claude_code', 'codex'))
    experience.add_argument('--server', help='Observed Atlan server identifier in that host')
    import journey
    conversation = sub.add_parser('journey')
    conversation.add_argument('action', choices=journey.ACTIONS, nargs='?', default='status')
    conversation.add_argument('--focus')
    conversation.add_argument('--answer', help='User-supplied business area or purpose; never an unsubmitted suggestion')
    conversation.add_argument('--host', choices=('claude_code', 'codex'))
    conversation.add_argument('--server', help='Observed Atlan server identifier in that host')
    steps = sub.add_parser('checklist', help='The guided-flow steps (seven, or six for a demo) with their saved state, for the host task list')
    steps.add_argument('--engine-dir', type=Path)
    steps.add_argument('--flow', choices=('build', 'demo'), help='The guided flow when the run has not recorded one')
    steps.add_argument('--host', choices=('claude_code', 'codex'))
    steps.add_argument('--server', help='Observed Atlan server identifier in that host')
    step = sub.add_parser('progress')
    step.add_argument('--stage', choices=progress.STAGES)
    step.add_argument('--status', choices=progress.STATES, default='running')
    step.add_argument('--action')

    b = sub.add_parser('bind'); b.add_argument('database'); b.add_argument('--tables', nargs='+'); b.add_argument('--schema')
    publication = sub.add_parser('publication')
    publication.add_argument('request', type=Path, help='Readback JSON file, or - for complete inline MCP responses on stdin')
    for name in ('prepare', 'execute', 'retrieved', 'answer', 'scope', 'check-query', 'check-metric', 'acquisition', 'auth'):
        sub.add_parser(name).add_argument('request', type=Path)
    args = p.parse_args()
    try:
        if args.command == 'location':
            print(json.dumps({'run_dir': str(resolve_run(args.workspace, args.run_dir, args.permitted_cache, args.home_permitted)),
                              'created': False}))
            return 0
        if not args.run_dir:
            raise ProjectError('Select a run directory, or use location to choose a permitted one.')
        if args.command == 'checklist' and not (safe_path(args.run_dir) / 'run.json').is_file():
            import checklist
            # Nothing is saved yet: the list starts at its first step and claims no progress.
            print(json.dumps({'flow': args.flow or 'build', 'steps': checklist.initial(args.flow or 'build'), 'state': 'running',
                              'step': 1, 'of': len(checklist.initial(args.flow or 'build')), 'remote_writes': False}))
            return 0
        if args.command in ('experience', 'journey') and not safe_path(args.run_dir).is_dir():
            raise ProjectError('The saved run is unavailable. Supply its existing directory to resume.')
        project = Project(args.run_dir)
        if args.command == 'survey': result = project.survey(args.folder)
        elif args.command == 'history': result = project.history()
        elif args.command == 'status': result = project.current()
        elif args.command == 'bind': result = project.bind(args.database, args.tables, args.schema)
        elif args.command == 'check': result = project.check()
        elif args.command == 'preview': result = project.preview(None if args.all else args.limit, args.format, args.kind)
        elif args.command == 'experience': result = project.experience(args.engine_dir, args.format, args.detail, args.focus, args.host, args.server)
        elif args.command == 'checklist':
            import checklist, experience
            model = experience.snapshot(project, args.engine_dir, host=args.host, server=args.server)
            result = {**checklist.build(model, args.flow), 'remote_writes': False}
        elif args.command == 'journey': result = project.journey(args.action, args.focus, args.host, args.server, args.answer)
        elif args.command == 'progress':
            result = (progress.update(project, args.stage, args.status, args.action) if args.stage
                      else project.read(project.root / 'progress.json'))
        elif args.command == 'publication':
            if str(args.request) == '-':
                raw = sys.stdin.read(4_000_001)
                if len(raw.encode()) > 4_000_000:
                    raise ProjectError('Readback input exceeds the local artifact limit.')
                result = project.publication(json.loads(raw))
            else:
                result = project.publication(project.read(safe_path(args.request)))
        elif args.command == 'acquisition': result = acquisition.cached(project, project.read(safe_path(args.request)))
        else: result = getattr(project, args.command.replace('-', '_'))(project.read(safe_path(args.request)))
        if isinstance(result, str):
            print(result)
        else:
            output = json.dumps(result, allow_nan=False)
            if len(output.encode()) > 4096 and not args.full:
                target = project.root / (args.command + '-result.json')
                project.write(target, result)
                compact = {'status': result.get('status', 'ok'), 'result_file': str(target),
                           'next': 'Read the relevant fields from this private result artifact.'}
                if args.command == 'preview':
                    if result.get('snapshot'):
                        compact['snapshot'] = result['snapshot']
                    compact['next'] = 'Display actual samples in chat with preview --format chat; a file link alone is not a preview.'
                elif args.command == 'experience':
                    if result.get('snapshot'):
                        compact['snapshot'] = result['snapshot']
                    compact['chat'] = result.get('chat', '')
                    if 'phase' in result:
                        import experience as presentation
                        compact['chat'] = presentation.render_chat(result)[:1800]
                        compact['focus'] = result.get('focus')
                        sample = result.get('sample')
                        compact['sample'] = ({k: sample[k] for k in ('id', 'name', 'status', 'status_label')}
                                             if sample else None)
                    compact['next'] = 'Display the concise experience summary in chat; retain details in this artifact.'
                output = json.dumps(compact)
            print(output)
        return 0
    except KeyboardInterrupt:
        if 'project' in locals() and args.command not in ('experience', 'journey'):
            path = project.root / 'progress.json'
            stage = project.read(path)['stage'] if path.exists() else 'inspect'
            progress.update(project, stage, 'canceled', 'resume')
        print(json.dumps({'status': 'canceled', 'next': 'Resume the same run; completed artifacts are retained.'}))
        return 130
    except (ValueError, OSError, KeyError, TypeError, mine.Blocked) as exc:
        if 'project' in locals() and args.command not in ('experience', 'journey'):
            try:
                path = project.root / 'progress.json'
                if path.exists():
                    progress.update(project, project.read(path)['stage'], 'failed', 'review_held')
            except (ValueError, OSError, KeyError, TypeError):
                pass  # A broken optional progress artifact cannot hide the original safe error.
        print(json.dumps({'status': 'blocked', 'message': str(exc) if isinstance(exc, (ProjectError, local_data.DataError, retrieval.RetrievalError, mine.Blocked))
                          else 'Project step could not complete. Check the selected files, source state and request fields.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
