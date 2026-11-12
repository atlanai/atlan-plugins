"""Read-only presentation of saved analytics evidence. Never advances or publishes a run."""
import base64
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import sys

from context_state import digest, ordinary
from analytics_eval import pii_reason

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "metrics-generation"))
from modules.metrics.holds import validate as validate_holds

MAX_BYTES = 4_000_000
MAX_ITEMS = 5_000
LABELS = {'draft': 'Draft', 'schema_checked': 'Schema checked; SQL untested',
          'executed': 'SQL checked for recorded samples', 'example_executed': 'SQL example checked',
          'held': 'Needs repair', 'released': 'Saved in Atlan',
          'provisional': 'Provisional; checks still pending', 'needs_review': 'Evidence needs review'}
PHASES = {'inspect': 'Inspecting your sources', 'scope': 'Choosing what to include',
          'build': 'Building your metrics and queries', 'check': 'Checking the metrics and queries',
          'preview': 'Reviewing your metrics', 'publish': 'Saving to Atlan',
          'ready': 'Ready for questions'}
ENGINE_PHASES = {'s0': 'Reading the selected inputs', 'a1': 'Reading dashboard definitions',
                 'a2': 'Finding dashboard questions', 'a3': 'Compiling calculations',
                 'a3b': 'Reviewing the compiled calculations', 'a4': 'Checking dashboard queries',
                 'h0': 'Reading query history', 'h1': 'Finding query patterns',
                 'h2': 'Finding measures in query history', 'h5': 'Checking history queries',
                 'c1': 'Combining source evidence', 'c2': 'Combining matching queries',
                 'c3': 'Writing useful questions', 'c4': 'Checking source coverage',
                 'c5': 'Saving the checked query layer', 'm1': 'Combining source evidence',
                 'm2': 'Describing useful queries', 'd1': 'Finding potential measures',
                 'x1': 'Finding useful metrics', 'x2': 'Combining matching metrics',
                 'x3': 'Checking metric cards', 'x4': 'Connecting related metrics'}


class _Reader:
    def __init__(self, root):
        self.root = ordinary(root)
        self.issues = []
        self.files = {}

    def read(self, relative, default):
        path = ordinary(self.root / relative, self.root)
        if path in self.files:
            return self.files[path]
        if not path.exists():
            return default
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError('Presentation requires bounded ordinary JSON files inside the selected run.')
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Presentation input exceeds the local artifact limit.')
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, type(default)):
                raise ValueError('Unexpected presentation record shape.')
        except (UnicodeError, ValueError, RecursionError):
            self.issues.append('A saved presentation input is unreadable; keep the run and repair that input.')
            value = default
        self.files[path] = value
        return value


def _rows(value):
    if not isinstance(value, list) or len(value) > MAX_ITEMS or any(not isinstance(x, dict) for x in value):
        raise ValueError('Presentation inputs must contain bounded lists of records.')
    return value


def _text(value, limit=800):
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)[:limit]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _sql(value, label='SQL'):
    if not isinstance(value, str) or not value:
        return '', ''
    if len(value) > 12000:
        return '', label + ' is too long for this view; inspect the complete saved evidence in chat.'
    if pii_reason(value):
        return '', label + ' is withheld by the existing query-history privacy filter.'
    if any(ord(c) < 32 and c not in '\n\t\r' for c in value):
        return '', label + ' contains unsupported control characters; inspect the saved evidence.'
    return value, ''


def _provenance(definition, query_names=None):
    ext = _dict(definition.get('extensions'))
    analytics = _dict(ext.get('atlan_analytics'))
    declared = _dict(analytics.get('declared'))
    result = []
    if declared:
        result.append('Model: ' + (_text(declared.get('name')) or _text(declared.get('ref')) or 'declared measure'))
    shapes = analytics.get('shapes')
    if isinstance(shapes, list) and shapes:
        result.append(f'Query history: {len(shapes)} source patterns')
    engine = _dict(ext.get('metrics_agent'))
    refs = engine.get('derived_from') or _dict(engine.get('card')).get('derived_from')
    if isinstance(refs, list) and refs:
        names = list(dict.fromkeys((query_names or {}).get(str(v), '') for v in refs))
        names = [n for n in names if n]
        result.append('Supporting queries: ' + '; '.join(names[:5]) if names else
                      f'Source queries: {len(refs)} supporting statements; ask for their saved evidence')
    if isinstance(engine.get('vq_id'), str):
        name = (query_names or {}).get(engine['vq_id']) or _text(definition.get('question'), 180)
        result.append('Saved query: ' + name if name else 'Saved query with source evidence')
    return result or ['Source evidence not recorded in this preview']


def _population(definition):
    parts = [_text(definition.get('population'))]
    filters = definition.get('mandatory_filters', [])
    if isinstance(filters, list):
        parts.extend(_text(f.get('filter')) if isinstance(f, dict) else _text(f) for f in filters)
    return '; '.join(p for p in parts if p) or 'Population not specified; review source scope'


def _holds(reader):
    source = reader.read('context/metrics-source.json', {})
    document = reader.read('context/publication-holds.json', {})
    if (not source and not document and reader.root / 'context/metrics-source.json' not in reader.files
            and reader.root / 'context/publication-holds.json' not in reader.files):
        return [], True
    source, document = _dict(source), _dict(document)
    try:
        validate_holds(document)
        schema_valid = True
    except (ValueError, TypeError, KeyError):
        schema_valid = False
    valid = (schema_valid and source.get('module') == 'metrics' and document.get('format') == 2
             and document.get('outputs') == source.get('outputs') and bool(source.get('outputs'))
             and digest(document) == source.get('publication_holds_sha256'))
    rows = []
    for kind, key in (('metric', 'metrics'), ('sql_query', 'queries')):
        for row in _rows(document.get(key, [])):
            if row.get('status') != 'held' or not _text(row.get('name')) or not _text(row.get('source_id')):
                valid = False
            rows.append({'kind': kind, **{k: _text(row.get(k)) for k in ('name', 'source_id', 'reason')}})
    if not valid:
        reader.issues.append('Publication hold evidence needs review; this view cannot confirm readiness.')
    return rows, valid


def _context_items(reader, state):
    checks = _dict(reader.read('context/candidate-checks.json', {}))
    coverage = _rows(_dict(reader.read('context/plan.json', {})).get('coverage', []))
    publication = _dict(state.get('publication'))
    receipts = {}
    for row in _rows(publication.get('artifacts', [])):
        if all(_text(row.get(k)) for k in ('id', 'kind', 'name', 'release_id', 'version_id', 'channel', 'definition_hash')):
            receipts[(row['kind'], row['name'], row['definition_hash'])] = row
    holds, hold_evidence_valid = _holds(reader)
    held_names = {(r['kind'], r['name']) for r in holds}
    held_ids = {(r['kind'], r['source_id']) for r in holds}
    items, seen, query_names = [], set(), {}
    for query in _rows(reader.read('context/queries.json', [])):
        definition = _dict(query.get('definition'))
        producer = _dict(_dict(definition.get('extensions')).get('metrics_agent'))
        label = _text(query.get('display_name') or definition.get('question') or query.get('name'), 180)
        for key in (producer.get('vq_id'), query.get('source_id'), query.get('name')):
            if isinstance(key, str) and label:
                query_names[key] = label
    for kind, file in (('metric', 'metrics.json'), ('sql_query', 'queries.json')):
        for body in _rows(reader.read('context/' + file, [])):
            definition = body.get('definition')
            name = _text(body.get('name'), 200)
            if not name or not isinstance(definition, dict) or (kind, name) in seen:
                raise ValueError('Presentation requires uniquely named candidate definitions.')
            seen.add((kind, name))
            revision = digest(definition)
            check = _dict(checks.get(kind + ':' + name))
            status = 'draft'
            if check.get('definition_hash') == revision and isinstance(check.get('status'), str) and check['status'] in LABELS:
                # A local check is not a publication receipt.
                status = check['status'] if check['status'] != 'released' else 'draft'
            execution = status if status in ('executed', 'example_executed') else 'not_recorded'
            review = definition.get('review_status', 'not_recorded')
            if review not in ('verified', 'reviewed', 'needs_review', 'unreviewed', 'rejected'):
                review = 'not_recorded'
            if (publication.get('workspace_id') and not state.get('context_changed')
                    and (kind, name, revision) in receipts):
                status = 'released'
            if ((kind, name) in held_names or (kind, _text(body.get('source_id'))) in held_ids
                    or (check.get('definition_hash') == revision and check.get('status') == 'held')):
                status = 'held'
            elif not hold_evidence_valid:
                status = 'needs_review'
            sql = definition.get('sql') if kind == 'sql_query' else None
            if not sql:
                examples = definition.get('examples')
                if isinstance(examples, list) and examples and isinstance(examples[0], dict):
                    sql = examples[0].get('sql')
            calculation, calculation_note = _sql(definition.get('formula') or definition.get('sql'), 'Calculation')
            sql, sql_note = _sql(sql)
            review_label = {'verified': 'Source review recorded', 'reviewed': 'Source review recorded', 'needs_review': 'Source review required',
                            'unreviewed': 'Source review pending', 'rejected': 'Source review rejected',
                            'not_recorded': 'Source review not recorded'}[review]
            evidence_notes = ['Execution: ' + (LABELS[execution] if execution != 'not_recorded' else 'not recorded for this revision'),
                              'Review: ' + review_label, 'Dashboard value parity: unmeasured',
                              'Publication: ' + ('saved in Atlan Trident' if status == 'released' else 'held' if status == 'held' else 'local')]
            selection_hash = digest({key: body.get(key) for key in
                                     ('name', 'display_name', 'description', 'source_id', 'definition')})
            reuse = next((_dict(row.get('comparison')) for row in coverage
                          if row.get('kind') == kind and row.get('selected_hash') == selection_hash), {})
            if reuse:
                evidence_notes += ['Reuse ' + label + ': ' + _text(reuse.get(key), 600)
                                   for key, label in (('population', 'population'), ('dimensions', 'coverage'),
                                                      ('parameters', 'parameters'), ('limitations', 'limitations'))
                                   if reuse.get(key)]
            items.append({'id': kind + ':' + name, 'kind': kind, 'source_id': _text(body.get('source_id'), 500),
                          'name': _text(body.get('display_name'), 200) or name,
                          'meaning': _text(body.get('description') or definition.get('question')),
                          'population': _population(definition),
                          'calculation': calculation, 'sql': sql, 'sql_label': 'Saved SQL example',
                          'detail_notes': evidence_notes + [n for n in (calculation_note, sql_note) if n],
                          'status': status, 'status_label': LABELS[status] + ('; ' + review_label.lower()
                              if kind == 'sql_query' and review not in ('verified', 'reviewed') else ''),
                          'checks': {'execution': execution, 'review': review, 'parity': 'unmeasured',
                                     'publication': 'released' if status == 'released' else 'held' if status == 'held' else 'local'},
                          'provenance': _provenance(definition, query_names), 'definition_hash': revision,
                          'reuse': reuse, 'evidence_file': 'context/' + file})
    return items, holds


def _blank_times(value):
    if isinstance(value, dict):
        return {k: None if k.endswith('_at') else _blank_times(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank_times(v) for v in value]
    return value


def _without_times(value):
    if isinstance(value, dict):
        return {k: _without_times(v) for k, v in value.items() if not k.endswith('_at')}
    if isinstance(value, list):
        return [_without_times(v) for v in value]
    return value


def _engine(reader, directory):
    manifest = _dict(reader.read(directory / 'manifest.json', {}))
    module = _dict(reader.read(directory / 'module.json', {}))
    stages = _rows(manifest.get('stages', []))
    last = stages[-1] if stages else {}
    stage = _text(last.get('stage'), 100)
    result = {'checkpoint': stage, 'label': ENGINE_PHASES.get(stage.split('_')[0], 'Building metrics and queries'),
              'status': module.get('status') if module.get('status') in ('prepared', 'paused', 'blocked', 'complete') else 'recorded',
              'completed_checkpoints': sum(s.get('verdict') == 'ok' and s.get('notes') != 'awaiting_decision' for s in stages),
              'recorded_at': _text(last.get('started_at'), 50), 'sample': None, 'provisional_count': 0}
    if not stages:
        return result
    # Only known output filenames are read. Manifest paths are compared, never followed.
    def produced(file):
        expected = reader.root / directory / file
        return next((s for s in reversed(stages)
                     if s.get('verdict') == 'ok' and s.get('notes') != 'awaiting_decision'
                     and isinstance(s.get('outputs', []), list)
                     and any(v == file or v == str(expected) for v in s.get('outputs', []) if isinstance(v, str))), None)

    layer = _dict(reader.read(directory / 'verified_queries.json', {}))
    producer = produced('verified_queries.json')
    if not layer or not producer:
        return result
    objects = _rows(layer.get('objects', []))
    # These are the two existing engine protocols, selected by the recorded producer.
    # M1 hashes query objects with timestamps removed; C5 hashes the complete layer
    # with timestamps blanked. A valid M1 layer must not be mistaken for corrupt C5.
    if producer.get('stage') in ('m1_merge_pre', 'm1_merge'):
        canonical = json.dumps(_without_times(objects), sort_keys=True, allow_nan=False)
        layer_hash = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    elif producer.get('stage') == 'c5_emit':
        canonical = _blank_times({k: v for k, v in layer.items() if k not in ('layer_sha', 'generated_at')})
        layer_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()[:16]
    else:
        reader.issues.append('The query-layer producer is unsupported; provisional preview is withheld.')
        return result
    if layer.get('layer_sha') != layer_hash:
        reader.issues.append('The intermediate query layer changed; provisional preview is withheld.')
        return result
    queries = {q.get('vq_id'): q for q in objects if isinstance(q.get('vq_id'), str) and q['vq_id']}
    if len(queries) != len(objects):
        reader.issues.append('Source query identities are missing or duplicated; provisional preview is withheld.')
        return result
    for filename, field in (('merged_metrics.json', 'metrics'), ('metric_proposals.json', 'proposals')):
        if not produced(filename):
            continue
        document = _dict(reader.read(directory / filename, {}))
        if document.get('layer_sha') != layer_hash:
            reader.issues.append('An intermediate metric belongs to a different query layer; preview is withheld.')
            continue
        rows = _rows(document.get(field, []))
        eligible = []
        for row in rows:
            refs = [s.get('vq_id') for s in _rows(row.get('sources', []))] if field == 'metrics' else [row.get('vq_id')]
            if not refs or any(not isinstance(ref, str) or not ref for ref in refs):
                continue
            expression, expression_note = _sql(row.get('expression'), 'Calculation')
            sources = [queries.get(ref) for ref in refs if isinstance(ref, str)]
            if (not _text(row.get('name')) or not expression or not sources or any(not q for q in sources)
                    or not any(expression in q.get('sql', '') for q in sources if isinstance(q.get('sql'), str))):
                continue
            source_query = next(q for q in sources if expression in q.get('sql', ''))
            eligible.append((row, expression, refs, source_query))
        result['provisional_count'] = len(eligible)
        if eligible:
            row, expression, refs, source_query = eligible[0]
            sql, sql_note = _sql(source_query.get('sql'), 'Source SQL')
            result['sample'] = {'id': 'provisional:' + _text(row.get('metric_id') or row.get('proposal_id'), 150),
                                'kind': 'metric', 'name': _text(row['name'], 200),
                                'meaning': _text(row.get('definition') or row.get('why_a_metric')),
                                'population': _population(row),
                                'calculation': expression, 'sql': sql, 'sql_label': 'Source SQL; metric checks pending',
                                'detail_notes': [sql_note] if sql_note else [], 'status': 'provisional',
                                'status_label': LABELS['provisional'],
                                'provenance': ['Query layer: ' + layer_hash, 'Source queries: ' + ', '.join(refs)],
                                'definition_hash': digest(row), 'evidence_file': str(directory / filename)}
            break
    return result


def host_scope(host, server):
    if (host is None) != (server is None):
        raise ValueError('Select both the current host and its Atlan server identifier.')
    if host is None:
        return False
    from auth_recovery import HOSTS, identifier
    if host not in HOSTS:
        raise ValueError('Select claude_code or codex as the current host.')
    identifier(server)
    return True


def snapshot(run, engine_dir=None, focus=None, host=None, server=None):
    """Project evidence into a view, without creating files, reading logs or following source paths."""
    explicit_focus = focus is not None
    reader = _Reader(run if isinstance(run, (str, Path)) else getattr(run, 'root', run))
    state = _dict(reader.read('run.json', {}))
    saved = _dict(reader.read('progress.json', {}))
    auth = _dict(reader.read('auth-recovery.json', {}))
    if host_scope(host, server):
        from auth_recovery import ACTIVE
        recovery = auth if (auth.get('host'), auth.get('server')) == (host, server) else {}
        readiness = _dict(reader.read('registry-readiness.json', {}))
        observations = _rows(readiness.get('observations', [])) if readiness.get('schema_version') == 1 else []
        matches = [o for o in observations if (o.get('host'), o.get('server')) == (host, server)
                   and o.get('status') == 'connected' and o.get('read_tool') == 'registry__list_workspaces'
                   and o.get('read_succeeded') is True and type(o.get('observed_at')) in (int, float)
                   and 0 <= o['observed_at'] < float('inf')]
        observed = max(matches, key=lambda o: o['observed_at'], default=None)
        recovery_time = max((recovery.get(k, 0) for k in ('started_at', 'updated_at')
                             if type(recovery.get(k, 0)) in (int, float)), default=0)
        owned_task = recovery.get('status') in ACTIVE and bool(recovery.get('task_id'))
        auth = (observed if observed and observed['observed_at'] >= recovery_time and not owned_task
                else recovery or {'host': host, 'server': server, 'status': 'unverified'})
    interaction = reader.read('interaction.json', {})
    if interaction:
        from journey import validate
        validate(interaction)
        if focus is None:
            focus = interaction.get('focus')
    items, holds = _context_items(reader, state)
    stage = saved.get('stage') if isinstance(saved.get('stage'), str) and saved['stage'] in PHASES else 'inspect'
    phase = {'code': stage, 'label': PHASES[stage], 'status': saved.get('status', 'not_started'), 'source': 'project'}
    engine = None
    engine_missing = False
    if engine_dir is None and isinstance(state.get('engine_run'), str):
        engine_dir = state['engine_run']
    if engine_dir is not None:
        requested = Path(engine_dir)
        absolute = ordinary(requested if requested.is_absolute() else reader.root / requested, reader.root)
        directory = absolute.relative_to(reader.root)
        if not directory.parts:
            raise ValueError('The engine must have its own directory inside the saved run.')
        if not absolute.exists():
            engine_missing = True
            reader.issues.append('The selected engine directory is missing; relative paths start inside this saved run.')
        elif not absolute.is_dir():
            raise ValueError('Select an engine directory inside the saved run.')
        else:
            engine = _engine(reader, directory)
        if engine and stage in ('inspect', 'scope', 'build') and engine['checkpoint']:
            phase = {'code': 'build', 'label': engine['label'],
                     'status': 'working' if engine['status'] == 'paused' else engine['status'], 'source': 'engine',
                     'checkpoint': engine['checkpoint'], 'completed_checkpoints': engine['completed_checkpoints']}
    counts = {'metrics': sum(i['kind'] == 'metric' for i in items), 'queries': sum(i['kind'] == 'sql_query' for i in items),
              'draft': sum(i['status'] == 'draft' for i in items),
              'checked': sum(i['status'] in ('schema_checked', 'executed', 'example_executed') for i in items),
              'held': len({(h['kind'], h['source_id'] or h['name']) for h in holds}
                          | {(i['kind'], i['source_id'] or i['id'].split(':', 1)[1]) for i in items if i['status'] == 'held'}),
              'released': sum(i['status'] == 'released' for i in items),
              'provisional': engine['provisional_count'] if engine else 0}
    counts.update(executed=sum(i['checks']['execution'] in ('executed', 'example_executed') for i in items),
                  review_required=sum(i['kind'] == 'sql_query' and i['checks']['review'] not in ('verified', 'reviewed') for i in items),
                  source_reviewed=sum(i['checks']['review'] in ('verified', 'reviewed') for i in items))
    sample = next((i for i in items if i['kind'] == 'metric' and i['status'] != 'held'), None)
    sample = sample or (items[0] if items else None) or (engine['sample'] if engine else None)
    sources = _rows(state.get('sources', []))
    scope = [{'name': _text(s.get('path'), 200), 'kind': _text(s.get('kind'), 80),
              'status': _text(s.get('status'), 80)} for s in sources[:100]]
    selection, selection_kind = [], 'pages'
    scopes = state.get('scopes') or ({'selected': state['scope']} if state.get('scope') else {})
    for value in _dict(scopes).values():
        chosen = _dict(_dict(value).get('selection'))
        # A report scope names pages; a model-only scope names measures.
        if 'pages' not in chosen and 'measures' in chosen:
            selection_kind = 'measures'
        pages = chosen.get('pages', chosen.get('measures', []))
        if isinstance(pages, list):
            selection.extend(_text(p, 100) for p in pages[:100] if isinstance(p, str))
    next_text, prompt = 'Inspect the selected sources to find useful metrics.', 'Show what sources are available and what you will use.'
    if sample:
        next_text, prompt = 'Inspect this definition before the next check.', 'Show the SQL and source evidence for the sample metric.'
    if stage == 'preview':
        next_text, prompt = 'Review the existing workspace destination, or keep these definitions local.', 'Show the existing Atlan workspace destination and the save plan, or keep this run local.'
    if counts['held'] and not any(i['status'] != 'held' for i in items):
        next_text, prompt = 'These items need repair before publication. Your work is saved.', 'Explain the held items and the next repair step.'
    if engine and engine['status'] == 'blocked':
        phase['status'] = 'blocked'
        phase['label'] = 'The metrics build needs a repair'
        next_text, prompt = 'The engine needs a repair. Your saved work is preserved.', 'Explain the blocking check and the next repair step.'
    auth_status = auth.get('status')
    if auth_status not in ('connected', 'login_required', 'waiting_auth', 'checking_mcp', 'tools_missing',
                           'plugin_missing', 'disabled', 'permission_denied', 'connection_error', 'reload_required',
                           'failed', 'timed_out', 'canceled', 'unverified'):
        auth_status = None
    local_work_available = (stage in ('build', 'check')
                            and _dict(state.get('execution')).get('kind') in ('sqlite', 'duckdb')
                            and _dict(state.get('execution')).get('read_only') is True
                            and (not items or counts['released'] < len(items)))
    if (auth_status and auth_status != 'connected' and not local_work_available
            and interaction.get('mode') != 'local'):
        phase['status'] = 'access_needed'
        next_text = {'waiting_auth': ('Finish sign-in in the opened browser. The saved run is preserved.' if auth.get('browser_opened') is True
                                      else 'Sign-in is pending. Open the sign-in page issued by the client.'),
                     'checking_mcp': 'Sign-in finished; confirm Registry access before resuming.',
                     'disabled': 'Enable the selected connection in your host to continue.',
                     'permission_denied': 'Choose an accessible existing workspace or request access.',
                     'unverified': 'Check Registry access in the selected host before asking with published context.',
                     'timed_out': 'Sign-in timed out. The saved run is preserved.',
                     'canceled': 'Sign-in stopped. The saved run is preserved.'}.get(auth_status, 'Restore the selected Atlan connection to continue.')
        prompt = 'Resume this saved run after checking the existing Atlan connection.'
    all_released = bool(items) and counts['released'] == len(items) and not counts['held'] and not reader.issues
    if all_released and auth_status and auth_status != 'connected':
        phase['label'] = 'Your metrics and queries are saved in Atlan Trident'
    if stage == 'ready' and not all_released:
        phase['label'] = 'Reviewing saved evidence'
    if (all_released and (not auth_status or auth_status == 'connected')
            and not (engine and engine['status'] == 'blocked')):
        phase = {'code': 'ready', 'label': 'Your metrics and queries are saved in Atlan Trident', 'status': 'complete', 'source': 'release_receipts'}
        next_text, prompt = 'Choose a question supported by these published definitions.', 'Show me questions supported by the definitions we just saved.'
    elif (stage == 'ready' and counts['released'] and counts['held']
          and all(i['status'] in ('released', 'held') for i in items)
          and not reader.issues and (not auth_status or auth_status == 'connected')
          and not (engine and engine['status'] == 'blocked')):
        phase = {'code': 'ready', 'label': 'Questions are ready for the published subset',
                 'status': 'partial_ready', 'source': 'release_receipts'}
        next_text = 'Choose a question supported only by the published definitions. Held items still need repair before publication.'
        prompt = 'Show questions supported only by released definitions; exclude held items.'
    if reader.issues:
        next_text, prompt = 'Some saved evidence is unreadable. Keep the run and repair it before continuing.', 'Show the saved evidence that needs repair.'
        if engine_missing:
            next_text = 'The selected engine directory is missing. Use its absolute path inside this saved run, or a path relative to the run.'
            prompt = 'Check the selected engine path inside this saved run, then refresh the snapshot.'
    if saved.get('status') in ('canceled', 'waiting-for-user'):
        phase['status'] = saved['status']
        phase['label'] = 'Your work is saved'
        next_text = 'This run is paused at a saved checkpoint. Resume here when ready.'
        prompt = 'Resume this saved analytics run from its current checkpoint.'
    bound = _dict(state.get('execution')).get('tables')
    model = {'format': 1, 'snapshot_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'live': False,
            'phase': phase, 'scope': scope, 'source_count': len(sources), 'scope_truncated': len(sources) > 100,
            'bound_tables': len(bound) if isinstance(bound, list) else 0,
            'selected_pages': selection, 'selection_kind': selection_kind, 'counts': counts,
            'progress': {'completed': saved.get('completed_items'), 'total': saved.get('total_items')},
            'sample': sample, 'items': items, 'items_truncated': len(items) > 100,
            'engine': engine, 'auth_status': auth_status if isinstance(auth_status, str) else None,
            'local_work_available': local_work_available,
            'intent_checkpoint': stage == 'scope' or (stage == 'inspect' and saved.get('status') in
                                  ('complete', 'complete-with-held-items', 'waiting-for-user')),
            'auth_host': auth.get('host') if auth.get('host') in ('claude_code', 'codex') else None,
            'auth_server': _text(auth.get('server'), 120),
            'warnings': list(dict.fromkeys(reader.issues)), 'next_step': next_text, 'next_prompt': prompt,
            'remote_writes': False, 'verification': 'saved_evidence_only', 'holds': holds}
    from experience_workbench import focus_sample
    from journey import apply
    model = apply(focus_sample(model, focus), interaction, explicit_focus=explicit_focus)
    model['handoff'] = handoff(model)
    import checklist
    model['checklist'] = checklist.build(model)
    model['items'] = model['items'][:100]
    return model


def handoff(model):
    """Describe usable scope, outstanding evidence and the next action independently."""
    counts = model['counts']
    total = counts['metrics'] + counts['queries']
    local = total - counts['released']
    if counts['released']:
        usable = f"{counts['released']} definitions have matching release receipts in Atlan."
        if local:
            usable += f" {local} remain local."
    elif total:
        usable = f"{total} definitions are available locally; nothing from this run is shared."
    else:
        usable = 'Source work is saved; no completed definition is available yet.'
    if counts['executed']:
        usable += f" {counts['executed']} have recorded execution checks."
    attention = []
    if counts['held']:
        attention.append(f"{counts['held']} items need repair before publication.")
    if counts['review_required']:
        attention.append(f"{counts['review_required']} queries need source review.")
    if total and not counts['executed']:
        attention.append('No execution check is recorded for the current definitions.')
    if total:
        attention.append('Independent dashboard value parity is unmeasured.')
    reuse = [i for i in model['items'] if i.get('reuse')]
    if reuse:
        attention.append(f"{len(reuse)} reused definitions have scope notes; inspect their coverage before using additional breakdowns.")
    if model.get('auth_status') and model['auth_status'] != 'connected' and model['interaction']['mode'] != 'local':
        attention.append('Atlan access still needs attention in this host.' +
                         (' Local preparation can continue.' if model.get('local_work_available') else ''))
    attention += model['warnings']
    return {'usable_now': usable, 'attention': attention, 'next_step': model['next_step'],
            'released': counts['released'], 'local': local, 'coverage_notes': len(reuse)}


def _markdown(value):
    text = re.sub(r'https?://\S+', '[link omitted]', _text(value, 12000))
    return re.sub(r'([\\`*_\[\]()!|#])', r'\\\1', html.escape(text, quote=False).replace('\n', ' '))


def _code(value):
    fence = '`' * max(3, 1 + max((len(m[0]) for m in re.finditer(r'`+', value)), default=0))
    return fence + 'sql\n' + value + '\n' + fence


def render_chat(model, detail=False):
    counts, sample = model['counts'], model['sample']
    metrics = 'metric' if counts['metrics'] == 1 else 'metrics'
    queries = 'query' if counts['queries'] == 1 else 'queries'
    lines = [model['phase']['label'] + '.',
             f"{counts['metrics']} {metrics}, {counts['queries']} {queries}; {counts['released']} saved in Atlan Trident."]
    if not model['items'] and counts['provisional']:
        lines[1] = f"{counts['provisional']} provisional metric ideas; nothing saved in Atlan Trident."
    if model['scope']:
        source_label = 'source' if model['source_count'] == 1 else 'sources'
        lines.append(f"Scope: {model['source_count']} {source_label}" + (', ' + model.get('selection_kind', 'pages') + ': ' + _markdown(', '.join(model['selected_pages'][:3])) if model['selected_pages'] else '') + '.')
    if counts['held']:
        lines.append(f"{counts['held']} items need repair before publication.")
    if counts.get('review_required'):
        lines.append(f"{counts['review_required']} queries need source review, separate from execution checks and publication holds.")
    if model.get('handoff', {}).get('coverage_notes'):
        lines.append(f"{model['handoff']['coverage_notes']} reused definitions have coverage limits retained in details.")
    intent = model.get('interaction', {}).get('intent', {})
    if intent.get('domain'):
        lines.append('Business focus: ' + _markdown(intent['domain']))
    if intent.get('purpose'):
        lines.append('Goal: ' + _markdown(intent['purpose']))
    if sample:
        meaning = sample['meaning']
        if not detail and len(meaning) > 160:
            meaning = meaning[:157].rsplit(' ', 1)[0] + '...'
        lines += ['', '**' + _markdown(sample['name']) + '**: ' + _markdown(meaning),
                  sample['status_label'] + '.']
        if sample['calculation'] and len(sample['calculation']) <= 160:
            lines += ['Calculation:', _code(sample['calculation'])]
        elif sample['calculation']:
            lines.append('The full calculation is available in details.')
        lines += ['Population: ' + _markdown(sample.get('population', 'Review source scope')),
                  'Source: ' + _markdown('; '.join(sample['provenance']))]
        if detail:
            if len(sample['calculation']) > 160:
                lines += ['Calculation:', _code(sample['calculation'])]
            if sample.get('sql') and sample['sql'] != sample['calculation']:
                lines += [sample.get('sql_label', 'SQL') + ':', _code(sample['sql'])]
            lines += sample.get('detail_notes', [])
            lines.append('These are saved checks, not a fresh query execution or proof of dashboard parity.')
    if not detail:
        lines += ['', model['next_step']]
    return '\n'.join(lines)


_SCRIPT = """const search=document.getElementById('filter');
const status=document.getElementById('status-filter');
function filter(){let count=0;document.querySelectorAll('[data-card]').forEach(card=>{
const visible=card.textContent.toLowerCase().includes(search.value.toLowerCase())&&(!status.value||card.dataset.status===status.value);
card.hidden=!visible;if(visible)count++;});document.getElementById('filter-result').textContent=count+' definitions shown';}
search.addEventListener('input',filter);status.addEventListener('change',filter);
document.getElementById('copy-prompt').addEventListener('click',async()=>{const prompt=document.getElementById('next-prompt');
try{await navigator.clipboard.writeText(prompt.value);document.getElementById('copy-result').textContent='Prompt copied. Paste it in chat.';}
catch{prompt.focus();prompt.select();document.getElementById('copy-result').textContent='Select and copy the prompt, then paste it in chat.';}});"""


def _render_html_base(model):
    """A local, timestamped view. Its controls inspect or copy text; none changes the run."""
    esc = lambda value: html.escape(str(value), quote=True)
    items = list(model['items'])
    if model['sample'] and model['sample']['status'] == 'provisional' and not items:
        items.append(model['sample'])
    cards = []
    for index, item in enumerate(items):
        cards.append('<article data-card data-status="' + esc(item['status']) + '"><h2>' + esc(item['name'])
                     + '</h2><p class="status">' + esc(item['status_label']) + '</p><p>' + esc(item['meaning'])
                     + '</p><details><summary>Calculation, SQL and source</summary><pre>' + esc(item['calculation'])
                     + '</pre>' + ('<h3>' + esc(item.get('sql_label', 'SQL')) + '</h3><pre>' + esc(item['sql']) + '</pre>' if item.get('sql') and item['sql'] != item['calculation'] else '')
                     + ''.join('<p>' + esc(note) + '</p>' for note in item.get('detail_notes', []))
                     + '<p>Population: ' + esc(item.get('population', 'Review source scope')) + '</p><p>'
                     + esc('; '.join(item['provenance'])) + '</p><p>Saved evidence, not fresh execution.</p></details></article>')
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    counts = model['counts']
    scope = ', '.join(s['name'] for s in model['scope'][:8]) or 'Source survey not recorded yet'
    options = ''.join('<option value="' + esc(k) + '">' + esc(v) + '</option>' for k, v in LABELS.items())
    return ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
            + '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'sha256-' + script_hash + '\'">'
            + '<title>Analytics setup</title><style>:root{color-scheme:light dark}'
            'body{font:16px/1.5 system-ui;margin:0;background:Canvas;color:CanvasText}'
            'main{max-width:800px;margin:auto;padding:24px}h1{font-size:24px;margin-bottom:8px}h2{font-size:18px}'
            'article,.summary{border-bottom:1px solid GrayText;padding:16px 0;margin:16px 0}'
            '.status,small{font-size:14px}summary,button{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;'
            'font:13px/1.6 ui-monospace,monospace;border-inline-start:2px solid GrayText;padding:12px}'
            'input,select,button,textarea{font:inherit;padding:8px;max-width:100%;box-sizing:border-box}'
            'textarea{display:block;width:100%;margin:12px 0}label{display:block;margin-top:12px}'
            ':focus-visible{outline:2px solid Highlight;outline-offset:3px}[hidden]{display:none}'
            '@media(max-width:480px){main{padding:16px}}'
            '</style><main>'
            + '<h1>' + esc(model['phase']['label']) + '</h1><small>Snapshot at ' + esc(model['snapshot_at'])
            + '. This view does not update itself. Ask in chat for a refreshed snapshot.</small><section class="summary" aria-label="Setup summary"><p>'
            + f"{counts['metrics']} metrics · {counts['queries']} queries · {counts['released']} saved in Atlan Trident · {counts['held']} need repair"
            + '</p><p>Sources: ' + esc(scope) + '</p>'
            + ('<p>Selected ' + esc(model.get('selection_kind', 'pages')) + ': ' + esc(', '.join(model['selected_pages'])) + '</p>' if model['selected_pages'] else '')
            + '<p>' + esc(model['next_step']) + '</p></section>'
            + ''.join('<p role="status">' + esc(w) + '</p>' for w in model['warnings'])
            + '<label for="filter">Find a definition</label><input id="filter" type="search" placeholder="Metric, query or source">'
            + '<label for="status-filter">Check status</label><select id="status-filter"><option value="">All statuses</option>' + options + '</select>'
            + '<p id="filter-result" aria-live="polite">' + str(len(items)) + ' definitions shown</p>'
            + (''.join(cards) or '<p>No definition is ready to preview yet. Source inspection can continue.</p>')
            + ('<p>Showing the first 100 definitions. Ask in chat for the remaining definitions.</p>' if model['items_truncated'] else '')
            + '<section class="summary"><h2>Continue in chat</h2><label for="next-prompt">Suggested next prompt</label><textarea id="next-prompt" readonly rows="2">'
            + esc(model['next_prompt']) + '</textarea><button id="copy-prompt" type="button">Copy next prompt</button>'
            + '<p id="copy-result" aria-live="polite">Copying does not run a command or publish anything.</p></section></main><script>'
            + _SCRIPT + '</script></html>')


def render_html(model):
    """Optional workbench over the same checked presentation model."""
    from experience_workbench import enhance
    return enhance(_render_html_base(model), model)
