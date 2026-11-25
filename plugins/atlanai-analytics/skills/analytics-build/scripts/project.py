#!/usr/bin/env python3
"""Survey a local analytics project and retain its data binding and Atlan receipts.

The metrics engine owns authoring; Registry calls use authenticated MCP.
This helper never publishes, executes repository scripts, installs software or runs evals.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import python_version  # noqa: E402

python_version.require()

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
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
from source_inventory import discover, source_kind, summarize, SENSITIVE
from trident.core import digest
from trident.powerbi_file import inspect_file, measure_type
import local_data
from local_data import safe_path, file_hash
import journey
import local_prepare
import context_contracts as mine
from analytics_eval import filter_querylog, set_reviewed_values

# Local engines the helper executes itself; SQLite needs only the standard library.
def _load_engine_helper(name):
    engine = Path(__file__).resolve().parents[2] / 'metrics-generation/modules/metrics/engine'
    spec = importlib.util.spec_from_file_location('analytics_' + name,
                                                  engine / 'src/metrics_agent/lib' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_card_sql = _load_engine_helper('card_sql')
_metric_results = _load_engine_helper('metric_results')

LOCAL = ('duckdb', 'sqlite')
# Survey source kinds the metrics engine reads as BI files; each is also the engine's reader name.
ENGINE_READERS = {'powerbi': 'powerbi', 'tableau': 'tableau',
                  'metric_catalogue': 'snowflake-semantic-view'}
# Survey statuses of a BI file the engine can read (Tableau surveys as needing model review).
USABLE_BI = ('available', 'needs_model_review')


def engine_table(binding, name):
    """A bound table as the engine names it, DB.SCHEMA.TABLE: a SQLite file is attached under its
    schema, so the schema is also its database (local-files' qualify_table); DuckDB's is the file."""
    parts = name.split('.')
    if binding['kind'] == 'sqlite':
        return '.'.join([parts[-2]] + parts[-2:])
    return name if len(parts) == 3 else '.'.join([Path(binding['path']).stem] + parts[-2:])

# Sources the survey recognises as a model or report it does not parse into pages itself.
MODEL_KINDS = ('powerbi_project', 'model_candidate', 'tableau', 'dashboard_html', 'metric_catalogue')


def model_scope(measures):
    """The measure selection of a model-only source: `['all']`, or the exact measure names."""
    if measures in (None, [], ['all']):
        return {'kind': 'model', 'measures': ['all'], 'selected': [], 'held': []}
    if not isinstance(measures, list) or any(not isinstance(m, str) or not m.strip() for m in measures):
        raise ProjectError('List the measures to scope by name, or pass ["all"].')
    return {'kind': 'model', 'measures': sorted(set(m.strip() for m in measures)),
            'selected': sorted(set(m.strip() for m in measures)), 'held': []}


def result_reference(request, result=None):
    """Read a trusted reference accompanying a check, outside the metric definition."""
    result = result or {}
    expected = request.get('expected_value', result.get('expected'))
    tolerance = request.get('expected_tolerance', 0)
    if not (_metric_results.valid_reference(expected, tolerance)
            and (expected is not None or 'expected_tolerance' not in request)):
        raise ProjectError('Give a finite expected value and a non-negative numeric tolerance.')
    return expected, tolerance


def sample_errors(samples):
    return list(dict.fromkeys(sample.get('error') or 'A parameter sample still fails SQL execution.'
                              for sample in samples.values() if sample.get('status') == 'failed'))


RUN_README = '''# Atlan Analytics setup run

This folder is a saved Atlan Analytics setup: the metrics and saved SQL built from your sources,
their checks, and what was saved to Atlan. To show a metric's details, what is ready or the next
step, load the `analytics-build` skill (in Claude Code, `/atlanai-analytics:analytics-build`)
before reading these files. Its helper reads them on this machine and sends nothing to Atlan
unless you ask it to save. A hand edit to these files no longer matches their checks.
'''


def wait_task(output, after=0, timeout=540, interval=2.0):
    """Wait for a background command to finish: its last line is its JSON result. Returns early with a sign-in code or
    address the person must use (a device code), so it can be relayed; returns `waiting` at the timeout. The sign-in
    turn ended while the command ran, and the host's task list went with it (a live run, 2026-11-23)."""
    import time
    start, seen = time.monotonic(), after
    while True:
        try:
            text = Path(output).read_text(encoding='utf-8', errors='replace')
        except OSError:
            text = ''
        # Whole lines only: a sign-in line still being written could lose its code.
        complete = text[:text.rfind('\n') + 1]
        # The result is the last line that is a JSON object; a host may add a line after it when the command exits
        # (Claude Code: "[exited with code N]"), which also ends the wait when no result came. A result in the file
        # comes first: its report is already read, so a sign-in line before it is no longer news.
        lines = [x.strip() for x in complete.splitlines() if x.strip()]
        for line in reversed(lines[-20:]):
            try:
                last = json.loads(line)
            except ValueError:
                continue
            if isinstance(last, dict) and last.get('status'):
                return {'status': 'done', 'result': last, 'next': "Go on as the command's result says, in this same turn."}
        new = complete[seen:] if len(complete) > seen else ''
        held = new.splitlines(keepends=True)
        for i, line in enumerate(held):
            past = seen + sum(len(x) for x in held[:i + 1])
            if line.strip() == 'Signed in.':
                # The person just finished in their browser: say so now, not a minute later when the report is read.
                return {'status': 'signed_in', 'after': past,
                        'say': "You're signed in. Reading your report now, about a minute.",
                        'next': 'Write `say` word for word, then run this command again with --after, in this same turn.'}
            if re.search(r'devicelogin|enter the code|use a web browser to open', line, re.I):
                return {'status': 'sign_in', 'line': line.strip()[:300], 'after': past,
                        'next': 'Relay this line to the person once, then run this command again with --after.'}
            if re.search(r'open this address:\s*$', line, re.I) and i + 1 < len(held) and held[i + 1].strip().startswith('https://'):
                # A browser sign-in opens on its own where it can: its address is only for when it does not.
                return {'status': 'sign_in', 'address': held[i + 1].strip(), 'after': past + len(held[i + 1]),
                        'next': 'The sign-in opens in the person\'s browser on its own. Give them this address only if '
                                'they say no browser opened, then run this command again with --after.'}
        exited = next((re.search(r'exited with code (-?\d+)', x) for x in reversed(lines[-3:])
                       if re.search(r'exited with code -?\d+', x)), None)
        if exited:
            return {'status': 'exited', 'code': int(exited.group(1)), 'tail': lines[-6:],
                    'next': 'The command ended without a result: say what stopped it in one line, from its last lines, '
                            'and the one way forward.'}
        if time.monotonic() - start >= timeout:
            return {'status': 'waiting', 'after': len(complete),
                    'next': 'Run this command again with --after, in this same turn. After two waits with nothing new, '
                            'tell the person in one line that the sign-in is still open in their browser.'}
        time.sleep(interval)


class ProjectError(ValueError):
    pass


class Project(ContextState):
    """Keeps compatible project run state without creating dashboard or agent jobs."""
    def current(self):
        state = self.manifest()
        if state.get('entrypoint') != 'project':
            raise ProjectError('Select the project setup run, not a dashboard or agent run.')
        # The values a person reviewed as business values apply to every privacy check of this run.
        set_reviewed_values(state.get('reviewed_values'))
        return state

    def reviewed_values(self, request):
        """Record the literals a person reviewed as business values, not names (a line of business
        such as 'Variable Spend'). Exact text; an empty list clears them."""
        values = request.get('values')
        if not isinstance(values, list) or len(values) > 200 or not all(
                isinstance(v, str) and v.strip() and len(v) <= 200 for v in values):
            raise ProjectError('Send {"values": [...]}: up to 200 exact values, as the privacy check quoted them.')
        state = self.current()
        state['reviewed_values'] = sorted(set(values))
        self.store.write(self.path, state)
        set_reviewed_values(state['reviewed_values'])
        return {'status': 'ok', 'reviewed_values': state['reviewed_values'],
                'next': 'Run PROJECT history again, then write a new engine configuration.'}

    def auth(self, request):
        return auth_recovery.advance(self, request)

    def journey(self, action='status', focus=None, host=None, server=None, answer=None, waits=False):
        import journey
        import visuals
        result = journey.advance(self, action, focus, host=host, server=server, answer=answer, waits=waits)
        if action == 'readiness' and not waits and isinstance(result, dict):
            # d4, 2026-11-19: the session started the build's helper, ended its turn and said nothing for 8 minutes.
            result['next'] = ("Start the build's helper in the background, as the dashboard flow says, then, in this same "
                              "turn, run `PROJECT drafts --wait --engine-dir engine` in the foreground and do what each line "
                              "says until it says the build is done, never ending the turn between waits.")
        return result

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
            # A run started on one file widens to a folder that holds it, until the build starts: a dashboard run read its
            # workbook alone, then needed the past queries and the database beside it.
            was = self.current().get('selected_file')
            widens = bool(was) and not selected and Path(was).is_relative_to(root) and \
                not (self.root / 'engine-config.json').exists()
            if not widens:
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
        if not (self.root / 'README.md').exists():
            # 2026-11-22 evals: asked about a metric by name, sessions found these files with a search and read the
            # JSON themselves, so the reply listed raw fields and statuses. The note sends them to the skill.
            self.write_text(self.root / 'README.md', RUN_README)
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
                    elif kind in MODEL_KINDS:
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
        """Record a page scope (parsed Power BI report) or a measure scope (any other model)."""
        state = self.current()
        source = next((s for s in state['sources'] if s['source_id'] == request.get('source_id')), None)
        if not source:
            raise ProjectError('Select a source from the survey inventory by its source_id.')
        if file_hash(safe_path(Path(state['source_root']) / source['path'], state['source_root'])) != source.get('sha256'):
            raise ProjectError('A source changed. Survey it again before selecting pages.')
        if source['kind'] == 'powerbi':
            inventory = self.read(self.context / (source['source_id'] + '.json'))
            result = page_scope.select(inventory, request.get('pages'))
        elif source['kind'] in MODEL_KINDS:
            # A MODEL WITHOUT A PARSED PAGE INVENTORY (TMDL, BIM, PBIP, a semantic model, a
            # Tableau file) has no pages to pick here; its scope is the measures the person
            # chose, read later by the engine's own reader. Record them so history mining and
            # resume both see the same bound.
            result = model_scope(request.get('measures'))
        else:
            raise ProjectError('This source holds no model or report to scope. Scope a model, report or BI export from the inventory.')
        tables = request.get('history_tables', [])
        if not isinstance(tables, list) or any(not isinstance(t, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*){1,2}', t) for t in tables):
            raise ProjectError('Supply evidenced, qualified physical table names for history scope.')
        scope = {'source_id': source['source_id'], 'source_hash': source['sha256'],
                 'selection': result, 'history_tables': tables,
                 'narrowed': (request.get('pages') if source['kind'] == 'powerbi'
                              else request.get('measures')) not in (None, [], ['all'])}
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


    def apply_reviewed_values(self):
        """The values a person reviewed as business values, for a check that does not read the run through current()."""
        set_reviewed_values(self.manifest().get('reviewed_values') if self.path.is_file() else None)

    def check(self):
        self.apply_reviewed_values()
        items = metric_preview.candidates(self)
        # Preserve not-yet-visited execution evidence if this pass is canceled.
        checks = {item['key']: item['check'] for item in items if item['check']}
        progress.update(self, 'check', completed=0, total=len(items))
        for index, item in enumerate(items):
            checker = mine.check_metric if item['kind'] == 'metric' else mine.check_query
            errors, warnings = checker(item['body'])
            prior = item['check']
            errors.extend(sample_errors(prior.get('samples', {})))
            checks[item['key']] = {**prior, 'definition_hash': item['definition_hash'],
                'status': 'held' if errors else prior['status'] if prior.get('status') in ('executed', 'example_executed') else 'schema_checked',
                'errors': errors, 'warnings': warnings}
            self.write(self.context / 'candidate-checks.json', checks)
            progress.update(self, 'check', completed=index + 1, total=len(items))
        return progress.update(self, 'check', 'complete-with-held-items' if any(c['errors'] for c in checks.values()) else 'complete',
                               completed=len(items), total=len(items))

    def check_all(self, request=None):
        """Every saved query's and metric's check in one call, on local data. One command per item, 27 in the joint run
        of 2026-11-25, had the session write a script around them, and the chips their results hand over never reached
        the chat. A check that fails is a result too, never a reason to stop."""
        binding = self.execution_binding()
        if not binding or binding['kind'] not in LOCAL:
            raise ProjectError('On a warehouse each check\'s statement runs with its own tool: use check-query and '
                               'check-metric, one at a time, as build-check says.')
        failed, ran = [], 0
        for item in metric_preview.candidates(self):
            name = item['body'].get('name', '')
            ran += 1
            try:
                done = self.check_execution({'name': name, 'parameters': {}, **({'example_index': 0} if item['kind'] == 'metric' else {})},
                                            item['kind'])
            except ValueError as exc:
                done = {'status': 'held', 'errors': [str(exc)]}
            if done.get('status') == 'held' or done.get('error'):
                failed.append({'kind': 'metric' if item['kind'] == 'metric' else 'saved query', 'name': name,
                               'reason': str(done.get('error') or (done.get('errors') or [''])[0])[:200]})
        return {'status': 'ok', 'ran': ran, 'passed': ran - len(failed), 'failed': failed}

    def check_query(self, request):
        return self.check_execution(request, 'sql_query')

    def check_metric(self, request):
        return self.check_execution(request, 'metric')

    def check_execution(self, request, kind):
        # A value the person reviewed passes here as it does in history and export (2026-11-22: four items stayed held).
        self.apply_reviewed_values()
        items = metric_preview.candidates(self)
        keys = [i['key'] for i in items]
        key = metric_preview.resolve_id(request.get('name', ''), keys, kind)
        item = next((i for i in items if i['key'] == key), None)
        if not item:
            raise ProjectError('Select an authored ' + ('metric' if kind == 'metric' else 'saved query')
                               + ' by name. Saved: ' + metric_preview.id_hint(keys, kind) + '.')
        checker = mine.check_query if kind == 'sql_query' else mine.check_metric
        errors, warnings = checker(item['body'])
        if errors:
            raise ProjectError('Correct the candidate schema errors before executing SQL.')
        definition = item['body']['definition']
        binding = self.execution_binding()
        on_warehouse = bool(binding) and binding['kind'] == 'warehouse' and \
            definition['dialect'].lower() == str(binding.get('dialect') or '').lower()
        if 'result' in request:
            if not on_warehouse:
                raise ProjectError('On local data this helper runs it itself: send the request without "result".'
                                   if binding and binding['kind'] in LOCAL else
                                   'Record a result only for the warehouse recorded in state, in its dialect'
                                   + ('.' if binding else ': none is recorded yet. Record it with `PROJECT warehouse`.'))
            return self.record_warehouse_check(item, kind, request, warnings)
        if on_warehouse:
            return self.warehouse_statement(item, kind, request, binding)
        if kind == 'metric':
            index = request.get('example_index', 0)
            examples = definition.get('examples', [])
            if type(index) is not int or not 0 <= index < len(examples) or not examples[index].get('sql'):
                raise ProjectError('Select an authored SQL example by its zero-based example_index.')
            source_sql = examples[index]['sql']
        else:
            source_sql = definition['sql']
        if not binding or binding['kind'] not in LOCAL or definition['dialect'].lower() != binding['kind']:
            raise ProjectError('Use the recorded read-only warehouse tool for this dialect and retain its check evidence. '
                               'No warehouse is recorded for this run yet: record it with `PROJECT warehouse`.'
                               if not binding else
                               'Use the recorded read-only warehouse tool for this dialect and retain its check evidence.')
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
        problem = None
        reference = result_reference(request) if kind == 'metric' else (None, 0)
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
                    raise local_data.ParameterError('Supply the named parameters required by the metric '
                                                    'calculation: ' + ', '.join(sorted(needed)) + '.')
                limit = request.get('limit', 100)
                calculation_query = {'sql': card_sql, 'parameters': {k: available[k] for k in needed},
                                     'limit': 2 if type(limit) is int and limit == 1 else limit}
                empty_query = {'sql': card_sql + ('\nAND 1=0' if definition.get('mandatory_filters') else '\nWHERE 1=0'),
                               'parameters': {k: available[k] for k in needed}, 'limit': 100}
                result, calculated, empty = local_data.call_batch(
                    binding, [example_query, calculation_query, empty_query])
                composition = {'status': 'executed', 'query_id': calculated['query_id'],
                               'sql_hash': calculated['sql_hash'], 'rows': calculated['rows'],
                               'coverage': 'formula_source_mandatory_filters'}
                composition['empty_population'] = {'query_id': empty['query_id'], 'rows': empty['rows']}
                problem = _metric_results.result_problem(
                    calculated['rows'], scalar=not definition.get('dimensions'),
                    expected=reference[0], tolerance=reference[1])
            else:
                result = self.execute(example_query)

        except local_data.ParameterError:
            raise   # a request naming other parameters ran nothing, so it holds nothing
        except local_data.DataError:
            samples[sample_id] = {'status': 'failed'}
            checks[item['key']] = {'definition_hash': item['definition_hash'], 'status': 'held',
                                   'samples': samples,
                                   'errors': ['SQL execution failed; review the selected parameters and source mapping.']}
            self.write(path, checks)
            progress.update_checks(self)
            raise
        samples[sample_id] = {'status': 'failed' if problem else 'executed',
                              'query_id': result['query_id'], 'sql_hash': result['sql_hash'],
                              **({'error': problem} if problem else {})}
        errors = sample_errors(samples)
        checks[item['key']] = {'definition_hash': item['definition_hash'],
                               'status': 'held' if errors else 'example_executed' if kind == 'metric' else 'executed',
                               'samples': samples, 'query_id': result['query_id'], 'sql_hash': result['sql_hash'],
                               **({'composition': composition} if composition is not None else {}),
                               'errors': errors,
                               'coverage': 'authored_example_only' if kind == 'metric' else 'observed_parameter_samples',
                               'warnings': warnings}
        self.write(path, checks)
        progress.update_checks(self)
        if kind == 'metric' and errors:
            return {'status': 'held', 'passed': False, 'name': item['key'], 'errors': errors}
        if kind == 'metric':
            # The metric's value is its card's calculation. An authored example can filter or group (one supplier's rate
            # read as the whole rate, 2026-11-22), so it is returned apart and named by its index.
            return {**calculated, 'value_of': 'the metric: its formula over its source and mandatory filters',
                    'example': {'example_index': index, **result},
                    'empty_population': {'rows': empty['rows']},
                    'next': 'Report `rows` as the metric. `example` is authored example %d, which may filter or group: '
                            'never give its rows as the metric\'s value.' % index}
        return result

    def warehouse_statement(self, item, kind, request, binding):
        """The one statement a warehouse check runs: a saved query's SQL, or a metric's calculation composed from its
        card's formula, source and mandatory filters. The recorded read-only tool runs it; nothing is recorded yet."""
        definition = item['body']['definition']
        sql = _card_sql.canonical_statement(definition) if kind == 'metric' else definition['sql']
        code = ''.join(text for token_kind, text in mine.scan(sql) if token_kind == 'code')
        needed = set(re.findall(r'\{\{\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}\}', code))
        available = {p['name']: p['default'] for p in definition.get('source', {}).get('parameters', []) if 'default' in p}
        available.update(request.get('parameters', {}))
        return {'status': 'run_on_warehouse', 'name': item['key'], 'sql': sql,
                'parameters': {k: available[k] for k in sorted(needed) if k in available},
                'next': 'Run this statement once with the recorded read-only tool (%s), binding each {{parameter}} '
                        'natively. For a metric, request at least two rows. Then run this command again '
                        'with the same name, parameters, any expected value, and "result": '
                        '{"rows": [[...], ...]} as it returned them, or "result": {"error": "<why it failed, in a '
                        'few words>"}.' % binding.get('tool', 'the warehouse query tool')}

    def record_warehouse_check(self, item, kind, request, warnings):
        """A warehouse check's verdict as the recorded read-only tool returned it, bound to the definition it checked."""
        result = request['result']
        rows, error = (result.get('rows'), result.get('error')) if isinstance(result, dict) else (None, None)
        plain = (str, int, float, bool, type(None))
        if ((rows is None) == (error is None)
                or error is not None and not (isinstance(error, str) and error.strip())
                or rows is not None and not (isinstance(rows, list) and len(rows) <= 1000 and all(
                    isinstance(r, list) and len(r) <= 100 and all(isinstance(v, plain) for v in r) for r in rows))):
            raise ProjectError('Give "result" as {"rows": [[...], ...]} (at most 1,000 rows of plain values) or '
                               '{"error": "<why it failed>"}.')
        if not error and kind == 'metric':
            expected, tolerance = result_reference(request, result)
            problem = _metric_results.result_problem(
                rows, scalar=not item['body']['definition'].get('dimensions'),
                expected=expected, tolerance=tolerance)
        else:
            problem = None
        reason = 'SQL execution failed on the warehouse: ' + ' '.join(error.split())[:200] if error else problem
        path = self.context / 'candidate-checks.json'
        checks = self.read(path) if path.exists() else {}
        prior = checks.get(item['key'], {})
        samples = prior.get('samples', {}) if prior.get('definition_hash') == item['definition_hash'] else {}
        samples[digest(request.get('parameters', {}))] = {
            'status': 'failed' if reason else 'executed', **({'error': reason} if reason else {}),
        }
        errors = sample_errors(samples)
        checks[item['key']] = {
            'definition_hash': item['definition_hash'], 'status': 'held' if errors else 'executed',
            'samples': samples, 'executor': 'warehouse_tool',
            **({'composition': {'status': 'executed', 'rows': rows[:20], 'coverage': 'formula_source_mandatory_filters'}}
               if kind == 'metric' and not error else {}),
            'errors': errors,
            'coverage': 'warehouse_tool_result', 'warnings': warnings}
        self.write(path, checks)
        progress.update_checks(self)
        return {'status': 'recorded', 'name': item['key'], 'passed': not errors}

    def history(self):
        state = self.current()
        # Outputs describe this call only: an earlier scope's bound or log never carries over.
        for stale in ('querylog.jsonl', 'querylog.clean.jsonl', 'history-tables.txt'):
            (self.context / stale).unlink(missing_ok=True)
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

    def engine_config(self, request):
        """Write the metrics engine's configuration for a local run from its survey, binding, scope
        and history. Warehouse runs and Power BI project folders configure their own plugs."""
        state = self.current()
        binding = state.get('execution') or {}
        if binding.get('kind') not in LOCAL:
            raise ProjectError('Bind the local database first. A warehouse run writes its engine '
                               'configuration as the metrics module describes.')
        sources = request.get('sources')
        if sources not in ('all', 'dashboard'):
            raise ProjectError('Choose sources "all" (dashboard and past queries) or "dashboard".')
        usable = [s for s in state['sources'] if s['kind'] in ENGINE_READERS and s['status'] in USABLE_BI]
        dashboards = [s for s in usable if s['path'] == request['bi']] if 'bi' in request else usable
        if 'bi' in request and not dashboards or len(dashboards) > 1:
            raise ProjectError('Name one surveyed dashboard file as `bi`, relative to the surveyed folder: '
                               + (', '.join(s['path'] for s in usable) or 'none was surveyed') + '.')
        if sources == 'dashboard' and not dashboards:
            raise ProjectError('No surveyed dashboard file to configure. A Power BI project folder (TMDL) is '
                               'configured as the metrics module describes.'
                               if any(s['kind'] == 'powerbi_project' for s in state['sources'])
                               else 'No surveyed dashboard file to configure; choose sources "all".')
        settings = {'database': binding['path']}
        inputs = {'history': [], 'schema': [], 'bi': [], 'sql_check': 'local-files'}
        if dashboards:
            settings.update(bi=str(safe_path(Path(state['source_root']) / dashboards[0]['path'], state['source_root'])),
                            bi_vendor=ENGINE_READERS[dashboards[0]['kind']])
            inputs['bi'] = ['local-files']
        config = {'version': 1, 'scope': re.sub(r'[^a-z0-9]+', '-', Path(state['source_root']).name.lower()).strip('-') or 'project',
                  'dialect': binding['kind'], 'mode': 'standalone', 'inputs': inputs,
                  'settings': {'local-files': settings}, 'witness_mode': 'execute',
                  'publish': ['files'], 'observers': []}
        if state.get('reviewed_values'):
            config['privacy'] = {'reviewed_values': state['reviewed_values']}
        if sources == 'all':
            cleaned = self.context / 'querylog.clean.jsonl'
            if not cleaned.is_file():
                raise ProjectError('Run PROJECT history first; the engine reads its cleaned past queries.')
            settings['history'] = str(cleaned)
            inputs['history'] = ['local-files']
            # The same bound PROJECT history applied, or the bound tables when no dashboard bounds it.
            scopes = list(state.get('scopes', {}).values()) or [state.get('scope', {})]
            if any(s.get('narrowed') and not s.get('history_tables') for s in scopes):
                raise ProjectError('Map the selected pages to physical tables before configuring history.')
            tables = sorted({t for s in scopes for t in s.get('history_tables', [])}) or (
                [] if dashboards else binding.get('tables', []))
            if tables:
                config['tables'] = [engine_table(binding, t) for t in tables]
        # A prepared engine keeps its configuration; a changed one gets its own directory.
        number = 1
        while True:
            name = 'engine' if number == 1 else f'engine-{number}'
            target = self.root / (name + '-config.json')
            if not (self.root / name).exists() or (target.is_file() and self.read(target) == config):
                break
            number += 1
        self.write(target, config)
        return {'status': 'ok', 'config': str(target), 'engine_run': str(self.root / name),
                'next': 'Run ENGINE prepare with this config and engine_run; do not write another by hand.'}

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


def _due(run, engine_dir=None, hand_over=True):
    """What the chat and the side page still need, named on whatever helper result comes next: the moments the run has
    reached but not shown, handed over whole unless the command only reads (it then names them and writes nothing), and
    a side page nobody has opened yet."""
    import side_page
    import side_view
    try:
        kinds = side_view.due(run, engine_dir)
        page = side_page.reminder(run, start=False)
    except Exception:  # noqa: BLE001 - the view never fails a command whose own work succeeded
        return {}
    out = {'side_page': page} if page else {}
    shown = side_view.show_due(run, engine_dir) if kinds and hand_over else ''
    if shown:
        # The dry run, 2026-11-25: told on every result to run `visual`, the session never did. The moment comes whole.
        out.update(side_view.handed(shown))
    elif kinds:
        out['visual'] = ('Due: run this helper\'s %s before going on, with the flags of every `visual` call (`--data` for a '
                         'demo, `--widget` where you have `show_widget`).' % ', then '.join('`visual %s`' % k for k in kinds))
    return out


def _answer_card(run, given, result):
    """The answer card, with the query that answers the person: built from the question and metrics the session names
    and the rows the query returned. A query run without `answer` (an accuracy check's helper) carries nothing."""
    import side_view
    import visuals
    from run_facts import RunFacts
    try:
        data = visuals.answer_from(given, result, RunFacts(run))
    except Exception:  # noqa: BLE001 - the card never fails the query that answered
        return {}
    return side_view.handed(side_view.show_card(run, 'answer', data)) if data else {}


def _saved_card(run, payload):
    """The card that says what was saved to Atlan, with the receipt that records the save: the workspace by the name
    the session read it by, when the payload carries it."""
    import side_view
    data = {k: payload[k] for k in ('workspace', 'aside') if isinstance(payload.get(k), str) and payload[k].strip()}
    return side_view.handed(side_view.show_card(run, 'saved', data if 'workspace' in data else None))


# Commands that only read the run: they name what is due and write nothing.
READ_ONLY = ('experience', 'preview')
# What a result that carries the moments the run has reached says to do with them.
# The moments that end a metric step or the checks: each carries the host's task list as it now stands.
TASK_MOMENTS = ('described', 'drafted', 'metrics', 'rules', 'links', 'context')
# The chips that end a metric step and start the next.
STEP_CHIPS = ('described', 'drafted', 'metrics', 'rules')


def _ask_for_tasks(root, start=False):
    """In a terminal, until the session records whether it keeps a task list, the checklist and every journey record ask
    for it, on stderr, which a filter on the result leaves: the helper cannot see the agent's tools, and h17t never ran
    the checklist, nor loaded its deferred task tools. The desktop app always keeps one: at the flow's start it is
    asked to fill it (d7 never loaded the app's plan tool)."""
    import visuals
    if visuals.desktop():
        if start:
            print("Next: put the tracker from `PROJECT checklist` in the app's task list now, with its plan tool, loading "
                  'it first if it is deferred, and send the whole list again whenever a step changes.', file=sys.stderr)
        return
    if 'tasks' in visuals.read_json(Path(root) / 'interaction.json', dict, {}):
        return
    print('Next: put the tracker from `PROJECT checklist` in your task list now, with TaskCreate and TaskUpdate (or '
          'TodoWrite), loading them first if they are deferred; then record once whether you keep a task list: '
          '`PROJECT journey tasks --answer yes`, else `--answer no`.', file=sys.stderr)


def main():
    # Chips, cards and data files carry text such as '●', '£' and '−'; a console or pipe in another code page must not break them.
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--run-dir')
    p.add_argument('--full', action='store_true', help='Return full structured results instead of a compact artifact reference.')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('survey').add_argument('folder')
    sub.add_parser('history')
    sub.add_parser('status')
    sub.add_parser('check')
    sub.add_parser('check-all', help='Run every saved query\'s and metric\'s check on local data, in one call')
    found = sub.add_parser('discover', help='Supported sources in a folder, by name and size only. Needs no run and creates nothing.')
    found.add_argument('--workspace', default='.')
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
    conversation.add_argument('--answer', help='User-supplied business area, purpose, report and pages, or dashboard choice '
                              "(built or declined); the readiness check's one-line result. Never an unsubmitted suggestion")
    conversation.add_argument('--waits', action='store_true',
                              help='With readiness: the result changes the plan, so the build waits for the go-ahead')
    conversation.add_argument('--host', choices=('claude_code', 'codex'))
    conversation.add_argument('--server', help='Observed Atlan server identifier in that host')
    steps = sub.add_parser('checklist', help='The guided-flow steps (nine, seven for a demo, the build as three) with their saved state, for the host task list')
    steps.add_argument('--engine-dir', type=Path)
    steps.add_argument('--flow', choices=('build', 'demo', 'dashboard'), help='The guided flow when the run has not recorded one')
    steps.add_argument('--host', choices=('claude_code', 'codex'))
    steps.add_argument('--server', help='Observed Atlan server identifier in that host')
    signin = sub.add_parser('wait-task', help="Wait in this turn for a background command (the Power BI sign-in and read) "
                            "to finish, so the turn, and the host's task list with it, does not end while it runs")
    signin.add_argument('--output', type=Path, required=True, help="The background command's output file")
    signin.add_argument('--after', type=int, default=0, help='Characters already seen, from the last result')
    signin.add_argument('--timeout', type=int, default=540, help='Seconds to wait before returning to run again')
    drafts = sub.add_parser('drafts', help='Follow the build in one turn: what the session acts on next, one line at a time')
    drafts.add_argument('--engine-dir', type=Path)
    drafts.add_argument('--wait', action='store_true', required=True,
                        help="Wait in the foreground for a step's end, the task list's counts, every share of the "
                             'definitions back, a quiet spell or the build done, and print it as one JSON line')
    drafts.add_argument('--timeout', type=int, default=540,
                        help="Return after this many seconds at most (570 at most): keep it under the host's limit for a "
                             'foreground command')
    tiles = sub.add_parser('tiles', help="The report's tiles by page, once the build has translated them: which became "
                           "queries and which could not, with why")
    tiles.add_argument('--engine-dir', type=Path)
    tiles.add_argument('--pages', nargs='*', help='Only these pages, by name')
    shared = sub.add_parser('split', help="How the metric definitions are shared among helpers, with each helper's brief")
    shared.add_argument('--engine-dir', type=Path)
    step = sub.add_parser('progress')
    step.add_argument('--stage', choices=progress.STAGES)
    step.add_argument('--status', choices=progress.STATES, default='running')
    step.add_argument('--action')

    import visuals
    visual = sub.add_parser('visual', help="A moment of the run: its chip or mini card for the chat; connect opens the side page")
    visual.add_argument('kind', choices=visuals.KINDS + ('next',), help='A moment, or `next` for the next one the run has reached')
    # No `choices` on a list: Python 3.10 and 3.11 check its empty default against them and refuse every single moment
    # ("invalid choice: []"), and the build's Python is 3.11 or 3.12. Each moment named is checked after parsing.
    visual.add_argument('also', nargs='*', metavar='kind',
                        help='More moments drawn in the same call, in order (the build card, then the checks chip), each '
                             'printed whole under its own heading, so no script ever has to split them')
    visual.add_argument('--widget', action='store_true', help='A mini card as HTML, for a host with an inline visual tool')
    visual.add_argument('--data', type=Path, action='append', default=[],
                        help="Facts the run's files cannot hold, such as a demo's firm and title; what the run's files prove always wins")
    visual.add_argument('--engine-dir', type=Path)
    visual.add_argument('--input', choices=('-',), help="The moment's data file as JSON on stdin (saved, answer, layout, edit), written to the run first")

    house = sub.add_parser('warehouse', help="Record the warehouse this run reads, once its check statement has run: the "
                           "tests, answers, side page and ready block all read it")
    house.add_argument('--kind', required=True, choices=('snowflake', 'databricks', 'other'))
    house.add_argument('--dialect', help='The SQL dialect, when it is not the kind (another warehouse)')
    house.add_argument('--via', required=True, choices=('atlan', 'host', 'cli'))
    house.add_argument('--tool', required=True, help='The exact read-only query tool name, or snow / databricks for a CLI')
    house.add_argument('--server', help='The Atlan server name, when via is atlan')
    house.add_argument('--runner', help='The runner its run_with names, when via is atlan')
    house.add_argument('--profile', help='The CLI connection or profile name, when via is cli')
    house.add_argument('--bulk-via', choices=('cli', 'tool', 'file'), help='How larger reads go, when not through the tool')

    asking = sub.add_parser('question', help="The save or ready question's text and options, or the closing message, composed from the run's files")
    asking.add_argument('kind', choices=('save', 'ready', 'next', 'close'))
    asking.add_argument('--widget', action='store_true', help='Where the chips and cards are drawn as widgets: the text keeps its count line')
    asking.add_argument('--data', type=Path, action='append', default=[], help="A demo's facts file")
    asking.add_argument('--engine-dir', type=Path)
    asking.add_argument('--accuracy', action='store_true',
                        help='The ready question, or the next one after an answer: the accuracy check is available for this run and has not run yet')
    asking.add_argument('--recap', action='store_true',
                        help='The ready question on a resumed run, where the save question was not shown in this chat')
    asking.add_argument('--score', type=int, help="The closing message: the accuracy check's percentage, when it ran")
    asking.add_argument('--scored', type=int, help='The closing message: how many questions the accuracy check scored')

    page = sub.add_parser('view', help="The run's side page: start it, or find the one already open, and print its address")
    page.add_argument('--stop', action='store_true', help='Stop serving the page')

    b = sub.add_parser('bind'); b.add_argument('database'); b.add_argument('--tables', nargs='+'); b.add_argument('--schema')
    publication = sub.add_parser('publication')
    publication.add_argument('request', type=Path, help='Readback JSON file, or - for complete inline MCP responses on stdin')
    for name in ('prepare', 'execute', 'retrieved', 'answer', 'scope', 'check-query', 'check-metric', 'acquisition', 'auth', 'engine-config',
                 'reviewed-values'):
        sub.add_parser(name).add_argument('request', type=Path)
    args = p.parse_args()
    if args.command == 'visual':
        for kind in args.also:
            if kind not in visuals.KINDS:
                visual.error('argument kind: invalid choice: %r (choose from %s)' % (kind, ', '.join(map(repr, visuals.KINDS))))
    if args.command == 'wait-task':
        print(json.dumps(wait_task(args.output, args.after, max(1, min(args.timeout, 570))), ensure_ascii=False))
        return 0
    try:
        if args.command == 'location':
            print(json.dumps({'run_dir': str(resolve_run(args.workspace, args.run_dir, args.permitted_cache, args.home_permitted)),
                              'created': False}))
            return 0
        if args.command == 'discover':
            print(json.dumps(summarize(safe_path(args.workspace))))
            return 0
        if args.command == 'checklist' and not (args.run_dir and (safe_path(args.run_dir) / 'run.json').is_file()):
            import checklist
            # Nothing is saved yet: the list starts at its first step and claims no progress.
            print(json.dumps({'flow': args.flow or 'build', 'steps': checklist.initial(args.flow or 'build'), 'state': 'running',
                              'step': 1, 'of': len(checklist.initial(args.flow or 'build')), 'remote_writes': False}))
            return 0
        if not args.run_dir:
            raise ProjectError('Select a run directory, or use location to choose a permitted one.')
        if args.command == 'warehouse':
            # The record every warehouse step reads (connect.md, step 5). It was hand-written JSON that no step wrote,
            # and three test runs and 0.9.76's live run were refused at the tests until a helper wrote it by hand.
            if args.via == 'atlan' and not (args.server and args.runner):
                raise ProjectError('A warehouse reached through Atlan needs --server and --runner, as the tool search named them.')
            if args.via == 'cli' and not args.profile:
                raise ProjectError('A warehouse reached through its CLI needs --profile, the connection the check used.')
            if args.kind == 'other' and not args.dialect:
                raise ProjectError('Another warehouse needs --dialect, the SQL dialect its tool speaks.')
            run = safe_path(args.run_dir)
            run.mkdir(parents=True, exist_ok=True)
            record = {'kind': args.kind, 'dialect': (args.dialect or args.kind).lower(), 'via': args.via, 'tool': args.tool,
                      **{k: v for k, v in (('server', args.server), ('runner', args.runner), ('profile', args.profile)) if v},
                      **({'bulk': {'via': args.bulk_via, **({'profile': args.profile} if args.bulk_via == 'cli' else {})}}
                         if args.bulk_via else {})}
            path = run / 'state.json'
            try:
                state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            except ValueError:
                raise ProjectError('The run\'s state.json is not readable JSON: repair it, then record the warehouse again.') from None
            state = state if isinstance(state, dict) else {}
            state['warehouse'] = record
            temp = path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding='utf-8')
            temp.replace(path)
            print(json.dumps({'status': 'ok', 'warehouse': record,
                              'next': 'Recorded: the tests, answers, side page and ready block now read this warehouse.'}))
            return 0
        if args.command == 'visual' and args.also:
            # Two moments in a row each ran as their own call, and sessions joined them with a shell separator (which zsh
            # refuses) or cut them apart with a script, three live runs in a row (2026-11-23). One call prints both.
            # Each moment ends at its own `side view:` line, so what comes before it is that moment, whole: no heading.
            if args.input:
                raise ProjectError('A moment read from stdin is drawn on its own: run `visual <kind> --input -` alone.')
            flags = (['--widget'] if args.widget else []) + [x for d in args.data for x in ('--data', str(d))] + (
                ['--engine-dir', str(args.engine_dir)] if args.engine_dir else [])
            import subprocess
            worst = 0
            for kind in [args.kind] + list(args.also):
                done = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--run-dir', str(args.run_dir),
                                       'visual', kind, *flags], capture_output=True, text=True)
                print((done.stdout or '').rstrip() + ('\n' + done.stderr.rstrip() if done.returncode and done.stderr else ''))
                print()
                worst = max(worst, done.returncode)
            return worst
        if args.command == 'visual':
            import side_page
            import side_view
            run = safe_path(args.run_dir)
            if not run.is_dir() or args.kind != 'connect' and not (run / 'run.json').is_file():
                raise ProjectError('Run this once the survey has created the run directory PROJECT location printed.')
            if args.input:
                raw = sys.stdin.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ProjectError("A moment's data is over 1 MB: keep it to what the moment shows.")
                try:
                    given = json.loads(raw, parse_constant=lambda name: float('nan'))
                except ValueError:
                    raise ProjectError("A moment's data is one JSON object.") from None
                if args.kind == 'answer' and isinstance(given, dict) and isinstance(given.get('result'), dict):
                    # A warehouse's answer: the question, the metrics and the rows the warehouse returned, as a query
                    # that answers the person takes them on this machine.
                    from run_facts import RunFacts
                    given = visuals.answer_from(given, given['result'], RunFacts(run)) or {}
                problem = visuals.check_data(args.kind, given)
                if problem:
                    raise ProjectError(problem)
                side_view._write(run / (args.kind + '.json'), json.dumps(given, indent=1, ensure_ascii=False))
            data = [json.loads(path.read_text(encoding='utf-8')) for path in args.data]
            demo = visuals.read_json(run / 'interaction.json', dict, {}).get('flow') == 'demo'
            # The desktop app draws widgets: a dashboard run there never passed --widget, so every chip came back as text.
            args.widget = args.widget or visuals.desktop()
            following = args.kind == 'next'
            if following:
                # The next moment the run has reached and not shown: during a build, what its last step produced.
                ahead = side_view.due(run, args.engine_dir)
                if not ahead:
                    reminder = side_page.reminder(run)
                    print('side view: ' + json.dumps({'next': 'Nothing new to show yet: carry on.' + (
                        ' Then ' + reminder[0].lower() + reminder[1:] if reminder else '')}))
                    return 0
                args.kind = ahead[0]
            if args.kind == 'connect' and not side_page.has_output(visuals.RunFacts(run, args.engine_dir)):
                print('side view: ' + json.dumps({'next': 'Nothing to show on the side page yet: carry on. It opens once '
                                                          'the first saved queries are drafted, when `visual next` names it.'}))
                return 0
            if args.kind == 'connect':
                # A demo's title and firm name the page; only a run the journey records as a demo takes them.
                if demo:
                    facts = {k: v for item in data if isinstance(item, dict) for k, v in
                             [*((x, item[x]) for x in ('firm',) if item.get(x)), *(item.get('connect') or {}).items()] if v}
                    if facts:
                        side_view._write(run / 'view' / 'facts.json', json.dumps(facts, ensure_ascii=False, indent=1))
                opened = side_page.open_page(run)
                side_view.note(run, 'connect')
                more = ' Then run `visual next` again: more is due.' if following and side_view.due(run, args.engine_dir) else ''
                print('side view: ' + json.dumps({**{k: opened[k] for k in ('url', 'error') if k in opened},
                                                  'next': side_page.instruction(opened, args.widget, run) + more}, ensure_ascii=False))
                return 0
            if not args.widget and visuals.keeps_tasks(run) and args.kind in visuals.ON_PAGE and 'connect' in side_view.seen(run):
                # In a terminal with a task list and the side page open, a metric step's end is one plain line. A host that
                # draws widgets keeps each step's one-line chip in the chat even once the side page is open: the person
                # follows the build by them (their call, 2026-11-23).
                import drafts
                try:
                    say = visuals.plain_line(visuals.render(args.kind, visuals.build_spec(args.kind, run, data, args.engine_dir), False))
                except (ValueError, KeyError, TypeError, AttributeError, OSError):
                    say = ''
                if side_view._shows(args.kind, run, args.engine_dir):
                    side_view.note(run, args.kind)
                more = ' Then run `visual next` again, with the same flags: more is due.' if following and side_view.due(run, args.engine_dir) else ''
                # What to do comes last: a session that reads the result through a filter keeps its tail (d7).
                print('side view: ' + json.dumps({'task_list': drafts.task_list(run, args.engine_dir), 'say': say,
                                                  'next': visuals.ON_PAGE_NEXT + more}, ensure_ascii=False))
                return 0
            try:
                spec = visuals.build_spec(args.kind, run, data, args.engine_dir)
                facts = visuals.RunFacts(run)
                saved = bool(facts.publication) and facts.interaction.get('mode') != 'local'
                asked = None if args.widget else visuals.asked(args.kind, demo, saved)
                shown = visuals.render(args.kind, spec, args.widget, side_view.first_answer(run, spec), plain=bool(asked))
                # In a terminal, the build's card in the middle of a build reaches no one: it leads the save question with
                # the checks chip instead. A widget host draws it as it happens.
                held = args.kind == 'build' and not args.widget and bool(shown)
                if held:
                    shown = ''
                # In a terminal the checks and the save are part of the question the helper composes, which prints
                # them once: printed here too, they would reach that question twice.
                deferred = {'context': 'save', 'saved': 'ready'}.get(args.kind) if asked and shown else None
                if deferred:
                    shown = ''
                # In a terminal the first chip in a question names the side page, so its address reaches the person
                # in text that is always shown, and the agent composes nothing.
                page = side_page.running(run) if asked and shown and args.kind in ('found', 'sources', 'questions') else None
                if page:
                    shown += ('\n↗ Each question with its known answer, and your agent\'s as it comes back: ' if args.kind == 'questions'
                              else '\n↗ Follow the build, and how each metric is reached: ') + page
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                # A file the run wrote in a shape this cannot read leaves the moment unshown, never the run stopped.
                print('side view: ' + json.dumps({'error': 'This moment could not be read: %s' % type(exc).__name__}))
                return 0
            if shown:
                print(shown)
            steps = []
            if shown and args.widget:
                steps.append(("Draw it now: pass the HTML above, as printed and never through a file, to `show_widget` with the title `%s` (load it with your tool "
                              "search first if it is deferred, and run its `read_me` the first time), before your next "
                              "question or reply. Never put it in a question's text. It says what "
                              "the step produced: add no line of your own about it.") % visuals.title_of(args.kind, spec))
            elif shown and asked:
                steps.append("Put the lines above, as they are, in %s: a question's own text is always shown, while text "
                             "written just before it folds. They are counted from the run's files, so never reword them or "
                             "add a number to them. Write nothing else before that question. If you have a "
                             "`show_widget` tool, run this again with `--widget`, pass the HTML to it instead, and add "
                             "`--widget` to every later `visual` call." % asked)
            elif shown:
                steps.append("Show the text above, unchanged, as this moment's result: start your very next message with it, "
                             "on its own lines, before any other words or tool call (this command's output is folded away, so "
                             "only your message shows it), as plain text with no code fence or quote around it. If "
                             "you have a `show_widget` tool, run this again with `--widget`, pass the HTML to it instead, "
                             "and add `--widget` to every later `visual` call.")
            elif held:
                steps.append("In a terminal the card waits for the save question, with the checks: once every check has "
                             "run, `PROJECT question save` prints both in it. If you have a `show_widget` tool, run this "
                             "again with `--widget` and draw it now, and add `--widget` to every later `visual` call.")
            elif deferred:
                steps.append("In a terminal these lines are part of the next question: run `PROJECT question %s` now and "
                             "ask exactly what it prints. Print nothing before it. If you have a `show_widget` tool, run "
                             "this again with `--widget` and draw it instead." % deferred)
            if shown and args.kind in STEP_CHIPS:
                # The person, 2026-11-18: under a chip that said "Next: Combine duplicates" the agent wrote "Doing: Combining
                # duplicates". The chip names the step that starts, and the session goes back to waiting in the same turn.
                steps.append("Start the next step's subagent in the same message as this chip, then go back to waiting for "
                             "the build (`PROJECT drafts --wait`) in this same turn: the chip names the step that starts, so "
                             "add no line of your own.")
            task_list = None
            if shown and args.kind in TASK_MOMENTS:
                # A live run set the host's list once, at the start: no step ever showed under way or its estimate. Each
                # metric step's chip is the moment the agent acts, so the list moves with it.
                import checklist
                import experience
                task_list = [{'content': s['content'], 'status': s['status']}
                             for s in checklist.build(experience.snapshot(run, args.engine_dir))['steps']]
                steps.append("Set your task list to `task_list`, each step's text and status as given, in the same "
                             "message as the chip: in Claude Code's terminal with TodoWrite (or TaskCreate and TaskUpdate), "
                             "in the desktop app's task list with its own plan tool, loading the tool first if it is "
                             "deferred; a host with no task list skips this.")
            # A moment with nothing true to show yet records nothing, so an early call cannot settle a step half done.
            # A moment deferred to its question is recorded when that question is composed, so a skipped question
            # is named again by the next helper result.
            if shown or held:
                try:
                    side_view.note(run, args.kind, spec)
                except Exception:  # noqa: BLE001 - the moment's own result still shows
                    pass
            if following and (shown or held) and side_view.due(run, args.engine_dir):
                steps.append('More is due: run `visual next` again, with the same flags.')
            reminder = side_page.reminder(run)
            if reminder:
                steps.append(reminder)
            if shown and args.kind == 'questions' and not side_page.running(run):
                # The page lists the questions; without one, the chat must, or the person tests questions never seen.
                steps.append("The side page is not open, so nothing lists these questions yet: send the table `CHECK show` "
                             "prints, as a chat message and as it is, before the question that asks to test them.")
            if not steps:
                # A moment with nothing to show still says what to do: given silence, a session filled it with its own line.
                steps.append('Nothing to show for this moment yet: carry on, with no words about it.')
            if steps:
                print(('\n' if shown else '') + 'side view: ' + json.dumps(
                    {**({'task_list': task_list} if task_list else {}),
                     'next': ' '.join([steps[0]] + ['Then ' + s[0].lower() + s[1:] for s in steps[1:]])}, ensure_ascii=False))
            return 0
        if args.command == 'question':
            import questions
            import side_view
            run = safe_path(args.run_dir)
            data = [json.loads(path.read_text(encoding='utf-8')) for path in args.data]
            try:
                extra = {'ready': {'accuracy': args.accuracy, 'recap': args.recap}, 'next': {'accuracy': args.accuracy},
                         'close': {'score': args.score, 'scored': args.scored}}.get(args.kind, {})
                compose = questions.after_answer if args.kind == 'next' else getattr(questions, args.kind)
                asked = compose(run, args.widget, data, args.engine_dir, **extra)
            except ValueError as exc:
                raise ProjectError(str(exc)) from None
            except Exception as exc:  # noqa: BLE001 - a run file in a shape this cannot read: say so, never a traceback
                raise ProjectError('The %s question could not be composed from this run (%s): ask it from the flow\'s own '
                                   'reference instead.' % (args.kind, type(exc).__name__)) from None
            if args.kind == 'close':
                # The flow ends here: a dashboard step still open was declined. Recorded by the run, so the task list
                # says "skipped" and the host's closing call cannot tick it as done (a live run, 2026-11-23).
                try:
                    import drafts
                    interaction = json.loads((run / 'interaction.json').read_text(encoding='utf-8'))
                    if isinstance(interaction, dict) and not interaction.get('dashboard_result'):
                        Project(run).journey('dashboard', answer='declined')
                    # The flow's own list, every step's result, always: after an accuracy check it had replaced the
                    # setup's steps and the progress so far was gone at the end (2026-11-23).
                    asked['task_list'] = drafts.task_list(run, args.engine_dir)
                except Exception:  # noqa: BLE001 - the closing message still goes out; the list stays as it was
                    pass
                asked['next'] = ('Set your task list to `task_list` first, where it is given. ' if asked.get('task_list') else '') + ('Print this `text` exactly as given, with its line breaks, as the last thing in your reply. '
                                 'Then, only when the build found caveats the person should know (a metric that totals across '
                                 'periods, a filter its tile always applies, a review warning), add a blank line, "Good to '
                                 'know" and one "- " line per caveat, each said once in this conversation. Nothing about the '
                                 'accuracy check or how it was scored: its card said it. No number the run\'s files do not '
                                 'hold, such as a range you work out. Ask no question, draw no banner and call no tool after it.')
                print(json.dumps(asked, ensure_ascii=False))
                return 0
            for kind in asked.pop('shown', []):
                try:
                    side_view.note(run, kind)
                except OSError:
                    pass  # A folder this session cannot write still gets its question: the note only spares a repeat.
            results = asked.pop('results', False)
            asked['next'] = ('Ask no native question: there is nothing to pick. Print this `question` text exactly as given, '
                             'with its line breaks, as the last thing in your reply, and wait for the person\'s question.') \
                if args.kind == 'ready' and not asked['options'] else \
                ('Ask one native question now: this `header`, this `question` as its text exactly as given, with its '
                             'line breaks and leading spaces, and these `options` in order%s. Write nothing before it, and never '
                             'reword or shorten it: every count is the run\'s own.%s'
                             % (', with the host\'s free-text entry' if args.kind in ('ready', 'next') else '',
                                ' Add nothing to it: caveats go in the closing message (`PROJECT question close`).'
                                if args.kind == 'ready' else
                                ' This run\'s sample checks ran with the warehouse\'s own tool and none of their results is in '
                                'the run\'s files: add one line before its last, how many passed and how many need a look from '
                                'those results, and nothing else.' if results else ' Add nothing to it.'))
            print(json.dumps(asked, ensure_ascii=False))
            return 0
        if args.command == 'view':
            import side_page
            run = safe_path(args.run_dir)
            if args.stop:
                side_page.stop(run)
                print(json.dumps({'stopped': True}))
                return 0
            opened = side_page.open_page(run)
            print(json.dumps({**{k: opened[k] for k in ('url', 'error') if k in opened},
                              'next': side_page.instruction(opened, False, run)}, ensure_ascii=False))
            return 0
        if args.command in ('experience', 'journey') and not safe_path(args.run_dir).is_dir():
            raise ProjectError('The saved run is unavailable. Supply its existing directory to resume.')
        project = Project(args.run_dir)
        if args.command == 'survey': result = project.survey(args.folder)
        elif args.command == 'history': result = project.history()
        elif args.command == 'status': result = project.current()
        elif args.command == 'bind': result = project.bind(args.database, args.tables, args.schema)
        elif args.command == 'check': result = project.check()
        elif args.command == 'check-all': result = project.check_all()
        elif args.command == 'preview': result = project.preview(None if args.all else args.limit, args.format, args.kind)
        elif args.command == 'experience': result = project.experience(args.engine_dir, args.format, args.detail, args.focus, args.host, args.server)
        elif args.command == 'checklist':
            import checklist, experience
            model = experience.snapshot(project, args.engine_dir, host=args.host, server=args.server)
            result = {**checklist.build(model, args.flow), 'remote_writes': False}
            _ask_for_tasks(project.root, start=True)
        elif args.command == 'journey':
            result = project.journey(args.action, args.focus, args.host, args.server, args.answer, args.waits)
            if args.action != 'tasks':
                _ask_for_tasks(project.root, start=args.action in ('demo', 'agentic'))
            if args.action == 'readiness' and isinstance(result, dict) and 'drafts --wait' in str(result.get('next')):
                # d5, 2026-11-19: a session piped this result through a filter that kept only the checklist, so the step
                # that keeps the build in view never reached it. A filter on the output leaves this line.
                print('Next: ' + result['next'], file=sys.stderr)
        elif args.command == 'drafts':
            import drafts
            drafts.wait(project.root, args.engine_dir, timeout=max(1, min(args.timeout, 570)))
            return 0
        elif args.command == 'tiles':
            import side_page
            from run_facts import RunFacts
            result = side_page.tiles_by_page(RunFacts(project.root, args.engine_dir), args.pages)
        elif args.command == 'split':
            import define_split
            try:
                result = define_split.split(project.root, args.engine_dir)
            except define_split.SplitError as e:
                raise ProjectError(str(e)) from e
        elif args.command == 'progress':
            result = (progress.update(project, args.stage, args.status, args.action) if args.stage
                      else project.read(project.root / 'progress.json'))
        elif args.command == 'publication':
            if str(args.request) == '-':
                raw = sys.stdin.read(4_000_001)
                if len(raw.encode()) > 4_000_000:
                    raise ProjectError('Readback input exceeds the local artifact limit.')
                payload = json.loads(raw)
            else:
                payload = project.read(safe_path(args.request))
            result = project.publication(payload)
            result = {**_saved_card(project.root, payload), **result}
        elif args.command == 'execute':
            request = project.read(safe_path(args.request))
            result = project.execute(request)
            if isinstance(request.get('answer'), dict):
                result = {**_answer_card(project.root, request['answer'], result), **result}
        elif args.command == 'acquisition': result = acquisition.cached(project, project.read(safe_path(args.request)))
        else: result = getattr(project, args.command.replace('-', '_'))(project.read(safe_path(args.request)))
        if isinstance(result, dict) and args.command not in ('status', 'execute'):
            # The side view's lines lead: a result cut short keeps them. A query's result carries none: the accuracy
            # check's answering helpers run it, and a moment handed to a helper never reaches the person.
            reads = args.command in READ_ONLY or args.command == 'journey' and args.action in ('status', 'resume')
            due = _due(project.root, getattr(args, 'engine_dir', None), hand_over=not reads)
            if due.get('show_first') and result.get('show_first'):
                # The steps the run reached come first, then the card this result made.
                result = {**result, 'show_first': due.pop('show_first') + '\n\n' + result['show_first']}
            result = {**due, **{k: v for k, v in result.items() if k not in due}}
        if isinstance(result, str):
            print(result)
        else:
            output = json.dumps(result, allow_nan=False)
            # A moment handed over rides along whatever its size: only the result's own fields move it to a file.
            own = json.dumps({k: v for k, v in result.items() if k not in ('show_first', 'visual')}, allow_nan=False)
            if len(own.encode()) > 4096 and not args.full:
                # A file per call: the accuracy check runs its answers at once, and one file per command let a large
                # result overwrite another, so an answer read another question's rows. A day's results are kept.
                kept = project.root / 'results'
                for old in kept.glob('*.json') if kept.is_dir() else []:
                    if time.time() - old.stat().st_mtime > 86400:
                        old.unlink(missing_ok=True)
                target = kept / ('%s-%s.json' % (args.command, uuid.uuid4().hex[:12]))
                project.write(target, result)
                compact = {**{k: result[k] for k in ('side_page', 'visual', 'show_first') if k in result},
                           'status': result.get('status', 'ok'), 'result_file': str(target),
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
        # A refused query leaves the run as it was, so its progress stays too.
        # A refusal with its own message (a check route this warehouse does not take, a request to correct) changes
        # nothing, so the stage is not marked failed: the side page would read it as the build stopping.
        refused = isinstance(exc, (ProjectError, journey.JourneyError, mine.Blocked))
        if 'project' in locals() and not refused and args.command not in ('experience', 'journey', 'execute'):
            try:
                path = project.root / 'progress.json'
                if path.exists():
                    progress.update(project, project.read(path)['stage'], 'failed', 'review_held')
            except (ValueError, OSError, KeyError, TypeError):
                pass  # A broken optional progress artifact cannot hide the original safe error.
        # A run with no record yet is the common case (a dashboard link fetched before any survey): say what makes one.
        try:
            missing_run = bool(args.run_dir) and Path(args.run_dir).is_dir() and not (Path(args.run_dir) / 'run.json').is_file()
        except (OSError, AttributeError):
            missing_run = False
        print(json.dumps({'status': 'blocked', 'message': str(exc) if isinstance(exc, (ProjectError, local_data.DataError, retrieval.RetrievalError, mine.Blocked,
                                                                                journey.JourneyError))
                          else 'This run has no record yet: run `PROJECT survey` on the working folder first, which creates it.'
                          if missing_run else
                          'Project step could not complete. Check the selected files, source state and request fields.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
