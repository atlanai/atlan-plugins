"""Independent dashboard parity, bound to accepted engine outputs. No network or gold export."""
import hashlib
import json
from pathlib import Path
import sys


def plans(cards):
    from metrics_agent.lib.card_sql import canonical_statement
    out = []
    for card in cards['metrics']:
        sql = canonical_statement(card)
        out.append({'source_id': card['source_id'], 'name': card['name'], 'sql': sql,
                    'sql_sha256': hashlib.sha256(sql.encode()).hexdigest(),
                    'parameters': {p['name']: p.get('default') for p in card.get('parameters') or []}})
    return out


def assess(plan, check):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills/atlan-analytics/scripts'))
    from analytics_eval import grade
    if check.get('sql_sha256') != plan['sql_sha256']:
        raise ValueError('Parity evidence refers to different metric SQL.')
    if check.get('unavailable'):
        return {'parity': 'unmeasured', 'reasons': [str(check['unavailable'])]}
    reference_scope = check.get('reference_scope')
    actual_scope = check.get('warehouse_scope')
    required = {'filters', 'security', 'cutoff', 'parameters'}
    if not isinstance(reference_scope, dict) or not isinstance(actual_scope, dict) or not all(
            key in scope and scope[key] is not None for scope in (reference_scope, actual_scope) for key in required):
        return {'parity': 'unmeasured', 'reasons': ['incomplete_scope']}
    if reference_scope != actual_scope:
        return {'parity': 'unmeasured', 'reasons': ['snapshot_or_scope_mismatch'],
                'source_cutoff': reference_scope['cutoff']}
    if actual_scope['parameters'] != plan['parameters']:
        raise ValueError('Parity parameter bindings differ from this metric plan.')
    reference, actual = check.get('reference'), check.get('actual')
    if not isinstance(reference, dict) or not all(k in reference for k in ('columns', 'rows')):
        return {'parity': 'unmeasured', 'reasons': ['reference_unavailable']}
    if not isinstance(actual, dict) or not (all(k in actual for k in ('columns', 'rows')) or actual.get('error')):
        return {'parity': 'unmeasured', 'reasons': ['warehouse_result_unavailable']}
    verdict = grade(reference, actual, gold_sql=plan['sql'], agent_sql=plan['sql'])
    category = verdict['class']
    status = 'pass' if category == 'pass' else 'failed' if category in ('wrong_result', 'agent_sql_error') else 'unmeasured'
    # The result values and grader reason can quote business rows; neither enters context.
    return {'parity': status, 'reasons': [category], 'source_cutoff': reference_scope['cutoff'],
            'sql_sha256': plan['sql_sha256']}


def validation(cards, evidence, outputs):
    if evidence is not None and (evidence.get('format') != 1 or evidence.get('outputs') != outputs):
        raise ValueError('Parity evidence does not match the accepted engine outputs.')
    by_id = {plan['source_id']: plan for plan in plans(cards)}
    checks = {}
    for item in (evidence or {}).get('checks') or []:
        source_id = item.get('source_id')
        if source_id not in by_id or source_id in checks:
            raise ValueError('Parity evidence names an unknown or duplicate metric.')
        checks[source_id] = assess(by_id[source_id], item)
    return checks
