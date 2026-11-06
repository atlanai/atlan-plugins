"""Source-grounded evaluation planning and execution handoff; never invent results."""
import copy
import secrets


def make_plan(state, digest, questions=None):
    if questions is None:
        questions = [c for c in state.get('eval_plan', {}).get('cases', []) if c.get('question_evidence')]
        authored = {c['metric_id']: {'question': c['question'], 'evidence_ref': c['question_evidence']} for c in questions}
    else:
        authored = {q['metric_id']: q for q in questions}
        if len(authored) != len(questions) or set(authored) - {m['id'] for m in state['metrics']}:
            raise ValueError('Questions must name distinct captured metrics.')
    cases = []
    for metric in state['metrics']:
        question = authored.get(metric['id'])
        cases.append({'case_id': 'check-' + digest({'metric': metric['id']})[:20],
                      'metric_id': metric['id'],
                      'question': question['question'] if question else 'Show me ' + metric['name'] + ' using the current dashboard filters.',
                      'question_evidence': question['evidence_ref'] if question else None,
                      'capture_hash': state['capture_hash'], 'definition_hash': metric['definition_hash'],
                      'context_hash': state['context_hash'], 'kind': 'dashboard_measure_parity'})
        metric['warehouse_portable'] = False
        if metric['runtime'] == 'warehouse':
            metric['readiness'] = 'sql_candidate'
    state.update(eval_plan={'cases': cases, 'generation': secrets.token_hex(12), 'basis': 'captured_measures',
                           'coverage': 'One scalar check per captured measure at the captured scope.',
                           'heldout_accuracy': 'not_run'}, evals=[], status='eval_planned')


def pending_verdict(state, metric):
    if not state['scope_complete']:
        return 'incomplete_scope'
    if metric['runtime'] != 'warehouse':
        return 'needs_sql'
    return 'pending'


def results(state):
    """Include unrun cases in coverage rather than silently dropping them."""
    observed = {c['case_id']: c for c in state['evals']}
    if not state.get('eval_plan'):
        return list(observed.values())
    metrics = {m['id']: m for m in state['metrics']}
    return [observed.get(c['case_id'], {**c, 'verdict': pending_verdict(state, metrics[c['metric_id']]),
                                      'basis': 'host_reported_dashboard_parity'})
            for c in state['eval_plan']['cases']]


def next_task(state, case_id=None):
    if not state.get('eval_plan'):
        raise ValueError('Create the evaluation plan first.')
    metrics = {m['id']: m for m in state['metrics']}
    available = results(state)
    if case_id:
        selected = next((c for c in available if c['case_id'] == case_id), None)
        if selected is None:
            raise ValueError('Unknown planned check.')
    else:
        selected = next((c for c in available if c['verdict'] == 'pending'), None)
    if selected is None:
        return {'state': 'no_pending_checks', 'remaining': [
            {'case_id': c['case_id'], 'question': c['question'], 'reason': c['verdict']}
            for c in available if c['verdict'] != 'pass']}
    metric = metrics[selected['metric_id']]
    blocker = pending_verdict(state, metric)
    if blocker != 'pending':
        return {'state': 'blocked', 'case_id': selected['case_id'], 'reason': blocker}
    original = metric.get('source_definition', metric['definition'])
    source = original['extensions']['trident_dashboard']['execution']
    return {'state': 'execution_required', 'case_id': selected['case_id'], 'question': selected['question'],
            'plan_generation': state['eval_plan']['generation'],
            'metric_id': metric['id'], 'context_hash': state['context_hash'],
            'capture_hash': state['capture_hash'], 'definition_hash': metric['definition_hash'],
            'reference_request': {'executor': 'powerbi', 'source_url': state['source']['url'],
                                  'model_ref': source['model_ref'], 'table': source['table'],
                                  'measure': source['measure'], 'context': copy.deepcopy(state['context']),
                                  'instruction': 'Observe the named source measure at this exact scope and data cutoff. Do not use candidate SQL to manufacture the reference.'},
            'warehouse_request': {**copy.deepcopy(metric['translation']), 'executor': 'warehouse',
                                  'context': copy.deepcopy(state['context']), 'read_only': True},
            'record_with': 'dashboard_record_check',
            'instruction': 'Use existing authorized host source and warehouse tools. Execute with separate bindings. Submit real values or explicit unavailable reasons. Do not substitute gold for warehouse execution.'}


# These limit the comparison, not execution of an otherwise scoped SQL metric.
PARITY_LIMITATIONS = {'snapshot_mismatch', 'source_value_unavailable', 'source_connection_required'}


def validation_status(state, metric):
    verdicts = [c['verdict'] for c in results(state) if c['metric_id'] == metric['id']]
    passed = bool(verdicts) and all(v == 'pass' for v in verdicts)
    failed = any(v in ('wrong_result', 'execution_error') for v in verdicts)
    return {'parity': 'passed' if passed else 'failed' if failed else 'unmeasured',
            'reasons': sorted(set(v for v in verdicts if v != 'pass')),
            'dashboard_data_as_of': state.get('context', {}).get('data_as_of'),
            'heldout_accuracy': 'not_run'}


def usable_metric(state, metric):
    if not state['scope_complete'] or metric['runtime'] != 'warehouse':
        return False
    verdicts = [c['verdict'] for c in results(state) if c['metric_id'] == metric['id']]
    return bool(verdicts) and all(v == 'pass' or v in PARITY_LIMITATIONS for v in verdicts)


def suggestions(state):
    if state['status'] != 'evaluated':
        return []
    questions = {c['metric_id']: c['question'] for c in state.get('eval_plan', {}).get('cases', [])}
    return [{'metric_id': m['id'], 'label': questions.get(m['id'], 'Show ' + m['name']),
             'question': questions.get(m['id'], 'Show me ' + m['name'] + ' using the current dashboard filters.'),
             'validation': validation_status(state, m)}
            for m in state['metrics'] if usable_metric(state, m) and m['id'] in questions]


def readiness(state):
    cases = results(state)
    metrics = state['metrics']
    ready = bool(metrics and state.get('eval_plan') and state['scope_complete']) and all(
        m['runtime'] == 'warehouse' and m['warehouse_portable'] for m in metrics) and bool(cases) and all(
        c['verdict'] == 'pass' for c in cases)
    usable = suggestions(state)
    return {'state': 'ready' if ready else 'ready_with_warnings' if usable else 'not_ready',
            'scope': 'captured_dashboard_scope', 'evaluation_kind': 'dashboard_parity',
            'usable_metrics': len(usable), 'remaining_metrics': len(metrics) - len(usable),
            'validation_complete': ready, 'deployment': 'local_context',
            'heldout_accuracy': 'not_run'}


def answer_plan(state, metric_id, context_hash, purpose='answer', case_id=None, plan_generation=None):
    metric = next((m for m in state['metrics'] if m['id'] == metric_id), None)
    if metric is None:
        raise ValueError('Unknown metric. Resolve the question against the captured inventory first.')
    if context_hash != state.get('context_hash'):
        return {'state': 'needs_new_scope', 'instruction': 'The requested scope differs from this SQL. Capture the new scope and translate/check it before reusing validation.'}
    if metric['runtime'] != 'warehouse':
        return {'state': 'needs_sql', 'metric_id': metric_id, 'reason': metric.get('translation', {}).get('gap', 'Physical mapping and SQL conversion are required.')}
    if purpose == 'validation':
        if plan_generation != state.get('eval_plan', {}).get('generation') or not case_id:
            raise ValueError('Validation requires the current planned case and generation.')
        task = next_task(state, case_id)
        if task.get('state') != 'execution_required' or task.get('metric_id') != metric_id:
            raise ValueError('Validation requires the current planned case and generation.')
    elif purpose != 'answer':
        raise ValueError('Unsupported execution purpose.')
    elif not usable_metric(state, metric):
        return {'state': 'requires_validation', 'metric_id': metric_id,
                'validation_details': validation_status(state, metric),
                'instruction': 'Use another available metric. Repair and validate this metric before answering with it; diagnostic execution uses the planned validation case.'}
    return {'state': 'execute_warehouse', 'metric_id': metric_id, 'name': metric['name'],
            'validation': 'passed_at_captured_scope' if metric['warehouse_portable'] else 'unverified',
            'validation_details': validation_status(state, metric),
            'data_policy': 'fresh_warehouse_result',
            'context': copy.deepcopy(state['context']), 'context_hash': state['context_hash'],
            'query': copy.deepcopy(metric['translation']), 'read_only': True,
            'instruction': 'Execute through the authorized warehouse with separate bindings. Answer from this fresh result, not the eval references. State filters/data cutoff and validation limits. A snapshot mismatch does not block this query. Report the actual warehouse cutoff when known; never present it as the historical dashboard result. Parity can be checked later against aligned snapshots.'}
