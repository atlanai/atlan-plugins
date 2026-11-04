"""Dashboard capture -> Registry drafts -> local, evidence-bound parity results.

The host supplies browser evidence; this module never connects to a BI service.
"""
import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlunsplit

from .metric_contract import validate_definition
from .evaluation import make_plan, results as planned_results, next_task, suggestions, answer_plan, readiness

MAX_BYTES = 4_000_000
HANDLE = re.compile(r'job_[a-f0-9]{24}\Z')
UUID = r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def text(value, maximum=2000):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum, 'A required text field is missing or too long.')
    require(not any(ord(c) < 32 and c not in '\n\t\r' for c in value), 'Control characters are not supported.')
    return value.strip()


def clean(value):
    raw = canonical(value)
    require(len(raw) <= MAX_BYTES, 'Capture is too large; reduce the selected scope.')
    require(not re.search(rb'(?i)(bearer\s+[a-z0-9._-]{10,}|(?:password|access_token|client_secret|api_key)\s*[=:]\s*[^\s,}]{4,}|-----BEGIN .*PRIVATE KEY)', raw), 'Credential-shaped content is not accepted.')
    return json.loads(raw)


def source_link(url):
    parts = urlsplit(text(url, 4096))
    require(parts.scheme == 'https' and parts.hostname == 'app.powerbi.com' and parts.port in (None, 443) and not parts.username and not parts.password and not parts.fragment, 'This version supports HTTPS Power BI report links on app.powerbi.com.')
    match = re.fullmatch(r'/groups/(' + UUID + r')/reports/(' + UUID + r')(?:/([A-Za-z0-9_-]+))?/?', parts.path)
    require(match is not None, 'Use a Power BI report link containing the workspace and report identifiers.')
    require(set(parse_qs(parts.query, keep_blank_values=True)) <= {'experience', 'filter', 'pageName', 'bookmarkGuid'}, 'This link has unsupported parameters; use its normal report link.')
    return {'vendor': 'powerbi', 'url': urlunsplit(('https', 'app.powerbi.com', parts.path, parts.query, '')),
            'workspace_ref': match[1].lower(), 'report_ref': match[2].lower(), 'page_ref': match[3],
            'url_filter_present': 'filter' in parse_qs(parts.query)}


class Service:
    def __init__(self, root):
        root = Path(root).expanduser().absolute()
        require(not any(p.is_symlink() for p in (root, *root.parents)), 'Use an ordinary local workspace directory.')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root

    def path(self, job, filename):
        require(isinstance(job, str) and HANDLE.fullmatch(job), 'Invalid dashboard handle.')
        folder = self.root / job
        require(not folder.is_symlink(), 'Invalid workspace path.')
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / filename
        require(not path.is_symlink(), 'Invalid workspace path.')
        return path

    def write(self, path, value):
        data = canonical(value)
        require(len(data) <= MAX_BYTES, 'Result exceeds the local bundle limit.')
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
        try:
            with os.fdopen(fd, 'wb') as out:
                os.fchmod(out.fileno(), 0o600)
                out.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @contextmanager
    def lock(self):
        path = self.root / '.lock'
        require(not path.is_symlink(), 'Invalid workspace lock.')
        with open(path, 'a') as out:
            os.chmod(path, 0o600)
            fcntl.flock(out, fcntl.LOCK_EX)
            yield

    def load(self, job):
        path = self.path(job, 'state.json')
        require(path.is_file() and path.stat().st_size <= MAX_BYTES, 'Dashboard was not found or exceeds the state limit.')
        return json.loads(path.read_bytes())

    def dispatch(self, name, args):
        clean(args)
        with self.lock():
            if name == 'dashboard_begin':
                source = source_link(args['url'])
                job = 'job_' + secrets.token_hex(12)
                state = {'job_id': job, 'revision': 0, 'source': source, 'status': 'needs_capture', 'metrics': [], 'evals': [],
                         'started_at': datetime.now(timezone.utc).isoformat(), 'scope_complete': False, 'capture_hash': None}
                self.write(self.path(job, 'state.json'), state)
                return self.view(state)
            state = self.load(args['job_id'])
            if name == 'dashboard_snowflake_execute':
                from .snowflake_cli import execute
                require(bool(state.get('snowflake_connection')), 'Connect Snowflake CLI for this job first.')
                plan = answer_plan(state, args['metric_id'], args['context_hash'])
                require(plan['state'] == 'execute_warehouse' and plan['query']['dialect'] == 'snowflake', 'A Snowflake SQL plan at the exact scope is required.')
                query = plan['query']
                rows = execute(state['snowflake_connection'], query['sql'], query['parameters'])
                require(len(rows) == 1 and len(rows[0]) == 1, 'Expected exactly one scalar warehouse result.')
                observation = {'executor':'warehouse','value':next(iter(rows[0].values())), 'context_hash':state['context_hash'],
                               'query_hash':query['query_hash'], 'evidence_ref':'snowflake-cli:' + secrets.token_hex(12)}
                self.write(self.path(state['job_id'], 'warehouse-receipt.json'),
                           {'observation':observation,'observed_at':datetime.now(timezone.utc).isoformat(),'data_cutoff_verified':False})
                return {'observation':observation,'data_cutoff_verified':False,'validation':plan['validation']}
            if name in ('dashboard_read', 'dashboard_review'):
                return self.view(state, args.get('include_definitions', False), args.get('definition_offset',0), args.get('definition_limit',1))
            if name == 'dashboard_next_check':
                return {'job_id':state['job_id'],'revision':state['revision'],'task': next_task(state, args.get('case_id'))}
            if name == 'dashboard_answer_plan':
                return {'job_id':state['job_id'],'revision':state['revision'],'answer_plan': answer_plan(state, args['metric_id'], args['context_hash'])}
            require(args.get('expected_revision') == state['revision'], 'Dashboard changed; read its current revision before continuing.')
            if name == 'dashboard_snowflake_connect':
                from .snowflake_cli import probe
                connection = clean(args['connection'])
                probe(connection)
                previous = state.get('snowflake_connection')
                scope_keys = ('account','connection','user','role','warehouse','database','schema')
                changed_scope = previous and any(previous.get(k) != connection.get(k) for k in scope_keys)
                state['snowflake_connection'] = connection
                if changed_scope and state.get('eval_plan'):
                    # Preserve the frozen questions/generation and historical evidence.
                    state['prior_connection_evals'] = state['evals']
                    state['evals'] = []
                    state['status'] = 'eval_planned'
                    for metric in state['metrics']:
                        metric['warehouse_portable'] = False
                        if metric['runtime'] == 'warehouse': metric['readiness'] = 'sql_candidate'
            elif name == 'dashboard_identify':
                require(not state['capture_hash'], 'Dashboard already extracted; keep its current inventory.')
                require(args['source_url'] == state['source']['url'], 'Identification must match the selected report URL.')
                state.update(title=text(args['title'], 256), identification_evidence=text(args['evidence_ref'], 1000),
                             status='identified')
            elif name == 'dashboard_import_file':
                from .powerbi_file import inspect_file, capture_from_inventory
                from .contracts import validate
                require(bool(state.get('title')), 'Identify the actual dashboard before importing its file.')
                inventory = inspect_file(args['path'])
                capture = capture_from_inventory(inventory,state,args)
                validate('dashboard_capture',{'job_id':args['job_id'],'expected_revision':args['expected_revision'],'capture':capture})
                self.capture(state,capture)
                filename = 'source-inventory.json'
                self.write(self.path(state['job_id'],filename),inventory)
                state['file_import'] = {'decoder':inventory['decoder'],'file_sha256':inventory['file_sha256'],
                    'counts':inventory['counts'],'inventory_file':str(self.path(state['job_id'],filename)),
                    'reference_values_included':False}
            elif name == 'dashboard_capture':
                self.capture(state, args['capture'])
            elif name == 'dashboard_translate':
                self.translate(state, args)
            elif name == 'dashboard_evaluate':
                require(not state.get('eval_plan'), 'Use dashboard_record_check with the current generation for a planned evaluation.')
                self.evaluate(state, args['cases'])
            elif name == 'dashboard_plan_evals':
                require(bool(state['metrics']) and all('translation' in m for m in state['metrics']),
                        'Build warehouse metric definitions before planning checks.')
                make_plan(state, digest, args.get('questions'))
            elif name == 'dashboard_record_check':
                self.record_check(state, args)
            elif name == 'dashboard_export':
                return self.export(state, args.get('workspace_id'))
            else:
                raise ValueError('Unsupported operation.')
            state['revision'] += 1
            self.write(self.path(state['job_id'], 'state.json'), state)
            return self.view(state, definitions=name == 'dashboard_translate')

    def capture(self, state, capture):
        capture = clean(capture)
        state.pop('file_import',None)
        require(capture['source_url'] == state['source']['url'], 'Capture must belong to the exact selected report URL.')
        model = text(capture['model_ref'], 256)
        require(capture['mode'] in ('browser', 'connector', 'export'), 'Unsupported capture method.')
        context = capture['context']
        require(isinstance(context, dict) and all(k in context for k in ('filters', 'page', 'data_as_of', 'security_scope')), 'Capture page, filters, business date and caller security scope.')
        require(isinstance(context['filters'], list), 'Filters must be an explicit list, including an empty list if verified.')
        for key in ('page', 'data_as_of', 'security_scope'):
            text(context[key], 500)
        complete = capture['scope_complete']
        require(type(complete) is bool, 'Declare whether capture scope is complete.')
        metrics, seen = [], set()
        for measure in capture['measures']:
            identity = text(measure['source_id'], 512)
            require(identity not in seen, 'Duplicate source measure identity.')
            seen.add(identity)
            table, label = text(measure['table'], 256), text(measure['name'], 256)
            expression = text(measure['expression'], 50000)
            kind = measure['datatype']
            require(kind in {'integer', 'decimal', 'string', 'boolean', 'date', 'timestamp'}, 'Use the source-declared measure datatype.')
            stable = measure['identity_kind'] == 'upstream_id'
            require(measure['identity_kind'] in ('upstream_id', 'name_fallback'), 'Declare the source identity strength.')
            source_id = 'powerbi:' + digest({'model': model, 'measure': identity})
            artifact_name = 'dashboard-metric-' + digest(source_id)[:24]
            definition = {'spec_version': '1.0-draft.4', 'version': 1, 'dialect': 'dax', 'formula': expression,
                'aggregation': {'type': 'custom', 'reaggregation': 'none'},
                'output': {'alias': 'value', 'datatype': kind},
                'source': {'entity': table, 'table': table, 'alias': table},
                'extensions': {'trident_dashboard': {'format': 1, 'source_language': 'dax',
                    'execution': {'backend': 'powerbi_semantic_model', 'model_ref': model, 'measure': label, 'table': table,
                                  'warehouse_sql_available': False, 'required_capability': 'powerbi_readonly_query'},
                    'source_url': state['source']['url'], 'captured_context': context,
                    'identity_kind': measure['identity_kind'], 'source_evidence': measure['evidence_ref'],
                    'source_description': measure.get('description', ''),
                    'scope_complete': complete, 'certification': 'not_asserted'}}}
            business = measure.get('business_context', {})
            for field in ('population', 'grain', 'exclusions'):
                if field in business:
                    definition[field] = copy.deepcopy(business[field])
            if business.get('unit'):
                definition['output']['unit'] = business['unit']
            if business:
                definition['extensions']['trident_dashboard']['business_context_evidence'] = business['evidence_ref']
            definition['provenance'] = {'source': 'powerbi_report', 'proposed_by': 'trident-dashboard',
                                        'derived_from': [measure['evidence_ref']]}
            validation = validate_definition(definition)
            require(validation['valid'], 'Generated definition failed the Registry structural contract.')
            metrics.append({'id': artifact_name, 'name': label, 'description': measure.get('description', ''), 'source_id': source_id, 'definition': definition,
                'definition_hash': digest(definition), 'identity_stable': stable, 'validation': validation,
                'readiness': 'source_bound', 'runtime': 'powerbi', 'warehouse_portable': False})
            metrics[-1]['source_definition'] = copy.deepcopy(definition)
        require(bool(metrics), 'No measures were captured; show the access or source gap.')
        state.update(metrics=metrics, context=context, scope_complete=complete, capture_hash=digest(capture),
                     context_hash=digest(context), status='definitions_ready', evals=[], title=capture['title'],
                     capture_mode=capture['mode'], model_ref=model)
        state.pop('eval_plan', None)
        # Recapture invalidates every previous result, even if a display label alone changed.

    def translate(self, state, args):
        from .dax_sql import translate, TranslationGap
        require(bool(state['capture_hash']), 'Capture source definitions before SQL conversion.')
        require(state['scope_complete'], 'Complete the selected scope before SQL conversion.')
        mappings = {}
        for mapping in args['mappings']:
            require(mapping['semantic_table'] not in mappings, 'Duplicate semantic table mapping.')
            require(1 <= len(mapping['relation']) <= 3, 'Map a physical relation with one to three identifier parts.')
            mappings[mapping['semantic_table']] = mapping
        for metric in state['metrics']:
            original = metric.setdefault('source_definition', copy.deepcopy(metric['definition']))
            table = original['source']['table']
            metric.update(definition=copy.deepcopy(original), runtime='powerbi', readiness='needs_sql', warehouse_portable=False)
            metric.pop('translation', None)
            try:
                if table not in mappings:
                    raise TranslationGap('Missing physical warehouse rowset and column mapping.')
                result = translate(original['formula'], mappings[table], state['context'], args['dialect'])
                definition = copy.deepcopy(original)
                definition.update(dialect=args['dialect'], formula=result['sql'],
                    source={'entity': result['relation'], 'table':result['relation'], 'alias':'s',
                        'parameters':[{'name':name,'datatype': 'boolean' if type(value) is bool else 'decimal' if type(value) in (int,float) else 'string', 'default':value} for name,value in result['parameters'].items()]},
                    examples=[{'source':'dashboard_translation', 'sql':result['sql'], 'parameters':result['parameters']}])
                if result.get('source_sql'):
                    definition['source'].pop('table')
                    definition['source']['sql'] = result['source_sql']
                    definition['source']['entity'] = table
                ext = definition['extensions']['trident_dashboard']
                ext.update(original_dax=original['formula'], mapping_evidence=mappings[table]['evidence_ref'],
                    mapping_hash=digest(mappings[table]), sql_validation='execution_evidence_is_separate',
                    execution={'backend':'warehouse', 'required_capability':'warehouse_readonly_bound_query',
                               'dialect':args['dialect'], 'sql':result['sql'], 'parameters':result['parameters'],
                               'binding_style':result['binding_style'], 'warehouse_sql_available':True,
                               'result_shape':'scalar_query', 'allowed_relations':[result['relation']]})
                validation = validate_definition(definition)
                require(validation['valid'], 'Converted SQL definition failed structural validation.')
                metric.update(definition=definition, validation=validation, runtime='warehouse', readiness='sql_candidate',
                              translation={**result, 'query_hash':digest({'sql':result['sql'],'parameters':result['parameters']})})
            except TranslationGap as exc:
                metric['translation'] = {'status':'needs_authoring', 'gap':str(exc)}
            metric['definition_hash'] = digest(metric['definition'])
        state.update(status='sql_prepared', evals=[])
        if state.get('eval_plan'):
            make_plan(state, digest)

    def record_check(self, state, args):
        require(bool(state.get('eval_plan')), 'Create the evaluation plan first.')
        require(args.get('plan_generation') == state['eval_plan']['generation'],
                'The evaluation plan changed; fetch the current check before recording.')
        case = next((c for c in state['eval_plan']['cases'] if c['case_id'] == args['case_id']), None)
        require(case is not None, 'Unknown planned check.')
        require(args['capture_hash'] == case['capture_hash'] and args['definition_hash'] == case['definition_hash'],
                'Check task is stale; fetch the current task before recording.')
        observations = {}
        for side in ('reference', 'actual'):
            wrapper = args[side]
            require(len(wrapper) == 1 and ('observation' in wrapper or 'unavailable' in wrapper),
                    'Supply either an observation or an unavailable reason for each side.')
            observations[side] = wrapper.get('observation') or {
                'unavailable': wrapper['unavailable'], 'context_hash': state['context_hash'],
                'evidence_ref': 'unavailable:' + wrapper['unavailable']}
        path = self.path(state['job_id'], 'grader-only.json')
        existing = json.loads(path.read_bytes()) if path.exists() and path.stat().st_size <= MAX_BYTES else {}
        previous = existing.get('cases', []) if existing.get('generation') == state['eval_plan']['generation'] else []
        updated = [c for c in previous if c['case_id'] != case['case_id']]
        updated.append({k: case[k] for k in ('case_id', 'metric_id', 'question', 'capture_hash', 'definition_hash')} |
                       observations | {'absolute_tolerance': args.get('absolute_tolerance', '0')})
        self.evaluate(state, updated)

    def evaluate(self, state, cases):
        require(bool(state['capture_hash']), 'Capture source definitions before evaluating.')
        require(1 <= len(cases) <= 500, 'Use 1–500 reference cases.')
        metrics = {m['id']: m for m in state['metrics']}
        results, seen = [], set()
        for case in cases:
            cid = text(case['case_id'], 100)
            require(cid not in seen, 'Duplicate evaluation case.')
            seen.add(cid)
            measure = metrics.get(case['metric_id'])
            require(measure is not None, 'Evaluation names an unknown metric.')
            if state.get('eval_plan'):
                planned = next((c for c in state['eval_plan']['cases'] if c['case_id'] == cid), None)
                require(planned is not None and planned['metric_id'] == case['metric_id'] and planned['question'] == case['question'],
                        'Evaluation must use the frozen planned question and metric.')
            reference, actual = case['reference'], case['actual']
            reason = None
            if case['capture_hash'] != state['capture_hash'] or case['definition_hash'] != measure['definition_hash']:
                reason = 'stale_definition'
            elif not state['scope_complete']:
                reason = 'incomplete_scope'
            elif reference['context_hash'] != state['context_hash'] or actual['context_hash'] != state['context_hash']:
                reason = 'context_mismatch'
            elif reference.get('unavailable') or actual.get('unavailable'):
                reason = reference.get('unavailable') or actual.get('unavailable')
            elif state.get('eval_plan') and reference.get('executor') != 'powerbi':
                reason = 'source_reference_not_bound'
            elif state.get('eval_plan') and measure['runtime'] != 'warehouse':
                reason = 'needs_sql'
            elif measure['runtime'] == 'warehouse' and (actual.get('executor') != 'warehouse' or actual.get('query_hash') != measure['translation']['query_hash']):
                reason = 'warehouse_query_not_bound'
            elif not reference.get('evidence_ref') or not actual.get('evidence_ref'):
                reason = 'missing_execution_evidence'
            elif reference.get('error') or 'value' not in reference:
                reason = 'invalid_reference'
            elif actual.get('error'):
                reason = 'execution_error'
            elif 'value' not in actual:
                reason = 'missing_actual'
            matched = False if reason else equal(reference['value'], actual.get('value'), case.get('absolute_tolerance', '0'))
            results.append({'case_id': cid, 'question': case['question'], 'metric_id': measure['id'],
                'verdict': reason or ('pass' if matched else 'wrong_result'),
                'reference_evidence': reference.get('evidence_ref'), 'answer_evidence': actual.get('evidence_ref'),
                'basis': 'host_reported_dashboard_parity', 'gold_digest': digest(reference),
                'capture_hash': state['capture_hash'], 'definition_hash': measure['definition_hash']})
        # Gold values and queries are grader-only; read/exported runtime context never returns them.
        self.write(self.path(state['job_id'], 'grader-only.json'), {'capture_hash': state['capture_hash'],
                   'generation': state.get('eval_plan', {}).get('generation'), 'cases': cases})
        state['evals'] = results
        complete_results = planned_results(state)
        for metric in state['metrics']:
            if metric['runtime'] == 'warehouse':
                checked = [r for r in complete_results if r['metric_id'] == metric['id']]
                passed = bool(checked) and all(r['verdict'] == 'pass' for r in checked)
                metric['warehouse_portable'] = passed
                metric['readiness'] = 'sql_parity_checked' if passed else 'sql_candidate'
        state['status'] = 'evaluating' if any(r['verdict'] == 'pending' for r in complete_results) else 'evaluated'

    def view(self, state, definitions=False, definition_offset=0, definition_limit=1):
        cases = planned_results(state)
        passed = sum(c['verdict'] == 'pass' for c in cases)
        measured = sum(c['verdict'] in ('pass', 'wrong_result', 'execution_error') for c in cases)
        total = len(cases)
        out = {'kind': 'dashboard_workflow', 'job_id': state['job_id'], 'revision': state['revision'],
            'status': state['status'], 'title': state.get('title', 'Your Power BI report'),
            'source_url': state['source']['url'], 'capture_hash': state['capture_hash'],
            'file_import':state.get('file_import'),
            'context_hash': state.get('context_hash'), 'scope_complete': state['scope_complete'],
            'context': state.get('context'), 'metrics': [{**{k: m[k] for k in ('id', 'name', 'definition_hash', 'readiness', 'runtime', 'warehouse_portable', 'identity_stable')},
                'table': m.get('source_definition', m['definition'])['source']['table'],
                'translation': {k:v for k,v in m.get('translation',{}).items() if k in ('status','gap','query_hash','dialect','compiler','warnings')}} for m in state['metrics']],
            'summary': {'measures': len(state['metrics']), 'structurally_valid': len(state['metrics']),
                        'sql_candidates': sum(m['runtime']=='warehouse' for m in state['metrics']),
                        'warehouse_portable': sum(m['warehouse_portable'] for m in state['metrics']),
                        'planned_cases': len(state.get('eval_plan', {}).get('cases', [])),
                        'pending_cases': sum(c['verdict'] == 'pending' for c in cases),
                        'checked_cases': len(state['evals']), 'measured_cases': measured,
                        'passed_cases': passed, 'unmeasured_cases': total-measured,
                        'parity_percent': round(passed / measured * 100, 1) if measured else None,
                        'heldout_accuracy_percent': None, 'independence': 'not_attested'},
            'evals': [{k:v for k,v in c.items() if k in ('case_id','metric_id','question','verdict','basis')} for c in cases], 'suggested_questions': suggestions(state)[:3],
            'answerable_questions': suggestions(state), 'agent_readiness': readiness(state),
            'metric_contract': {'app': 'metric', 'kind': 'metric', 'spec_version': '1.0-draft.4'},
            'warehouse_connection': {'connected':bool(state.get('snowflake_connection')),'transport':'snowflake_cli'},
            'eval_plan': {k:v for k,v in state['eval_plan'].items() if k != 'cases'} if state.get('eval_plan') else None, 'registry_status': 'local_drafts_not_uploaded',
            'next_action': ('capture_source' if not state['metrics']
                            else 'map_and_translate_to_sql' if not all('translation' in m for m in state['metrics'])
                            else 'create_eval_plan' if not state.get('eval_plan')
                            else 'map_and_translate_to_sql' if not any(m['runtime']=='warehouse' for m in state['metrics'])
                            else 'run_next_check' if any(c['verdict']=='pending' for c in cases)
                            else 'ask_questions' if any(m['warehouse_portable'] for m in state['metrics']) else 'resolve_check_gaps'),
            'capture_request': {'vendor': 'powerbi', 'report_url': state['source']['url'],
                'read_only': True, 'prefer': 'authorized_file_download_then_targeted_metadata_and_reference_queries',
                'required': ['source measure identities and types', 'complete selected filters including virtualized options',
                             'page and visual scope', 'semantic model reference', 'business data-as-of', 'caller security scope'],
                'do_not': ['extract cookies', 'infer hidden DAX from screenshots', 'apply model edits', 'widen to other workspaces']}}
        if definitions:
            selected = state['metrics'][definition_offset:definition_offset+definition_limit]
            out['definitions'] = [{'id': m['id'], 'definition': m['definition']} for m in selected]
            out['definition_page'] = {'offset':definition_offset,'returned':len(selected),'total':len(state['metrics']),
                'next_offset':definition_offset+len(selected) if definition_offset+len(selected)<len(state['metrics']) else None}
            # Large exact definitions remain in the local export; never silently truncate SQL.
            if len(json.dumps(out['definitions'])) > 16000:
                self.write(self.path(state['job_id'], 'requested-definitions.json'), out.pop('definitions'))
                out['definition_file'] = str(self.path(state['job_id'], 'requested-definitions.json'))
        return out

    def export(self, state, workspace_id):
        require(bool(state['metrics']), 'Capture definitions before exporting.')
        workspace = text(workspace_id, 128) if workspace_id else None
        runtime = {'format': 'trident-dashboard-runtime/1', 'source': state['source'], 'capture_hash': state['capture_hash'],
                   'context': state['context'], 'metrics': state['metrics'], 'status': 'local_provisional',
                   'consumer_rules': ['Use converted SQL with the declared warehouse executor and separate bound parameters.',
                       'Do not execute source DAX with a SQL executor; unresolved metrics are migration gaps.',
                       'SQL candidates are not validated until warehouse/reference comparisons pass.',
                       'Preserve filters, security scope and freshness.', 'Read released Registry versions after publication; never silently fall back to draft head.']}
        envelopes = []
        for metric in state['metrics']:
            envelope = {'name': metric['id'], 'display_name': metric['name'], 'source_type': 'external',
                'source_id': metric['source_id'], 'source_uri': state['source']['url'],
                'source_version': state['capture_hash'], 'auto_release': False,
                'description': metric.get('description', metric['definition']['extensions']['trident_dashboard'].get('source_description', '')),
                'definition': metric['definition'], 'source_metadata': {'producer': 'trident-dashboard', 'capture_hash': state['capture_hash']}}
            if workspace:
                envelope['workspace_id'] = workspace
            envelopes.append(envelope)
        dataset = [{'name': 'dashboard-case-' + digest({'capture': state['capture_hash'], 'id': c['case_id']})[:24],
            'input': {'question': c['question']}, 'expected': None, 'source_kind': 'external',
            'source_ref': 'dashboard-eval:' + digest({'source': state['source']['report_ref'], 'case': c['case_id']}),
            'extra': {'gold_digest': c.get('gold_digest'), 'evaluation_kind': 'dashboard_parity', 'capture_hash': state['capture_hash']}}
            for c in planned_results(state)]
        outputs = {'runtime-context.json': runtime, 'metric-envelopes.json': envelopes,
                   'eval-record-drafts.json': dataset, 'evaluation-report.json': self.view(state)}
        if state.get('file_import'):
            outputs['source-inventory.json'] = json.loads(self.path(state['job_id'],'source-inventory.json').read_text())
        for filename, payload in outputs.items():
            self.write(self.path(state['job_id'], filename), payload)
        return {**self.view(state), 'export': {'files': {name: str(self.path(state['job_id'], name)) for name in outputs},
            'destination_selected': bool(workspace), 'remote_writes': False,
            'import_status': 'requires_live_contract_and_identity_reconciliation',
            'notes': 'Draft envelope templates only. Resolve existing source IDs, preserve semantic versions/names, check destination contract and executor capability, then obtain exact save approval. Gold remains grader-only.'}}


def equal(expected, actual, tolerance):
    require(isinstance(tolerance, str) and len(tolerance) <= 40, 'Use a bounded decimal tolerance.')
    try:
        tol = Decimal(tolerance)
        require(tol.is_finite() and tol >= 0, 'Tolerance must be finite and nonnegative.')
        # Datatypes matter: a boolean does not equal 0/1, and text stays text.
        if isinstance(expected, bool) or isinstance(actual, bool):
            return type(expected) is type(actual) and expected == actual
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            return abs(Decimal(str(expected)) - Decimal(str(actual))) <= tol
        return type(expected) is type(actual) and expected == actual
    except InvalidOperation:
        raise ValueError('Invalid numeric comparison.') from None
