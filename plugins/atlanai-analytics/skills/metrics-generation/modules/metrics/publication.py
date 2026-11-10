"""Bind review decisions to the metrics publisher's checked context files.

This module computes an identity and revision count; Registry transport stays in the plugin.
"""
import hashlib
import json


def publication_identity(*, workspace_id, sources, documents):
    """Bind approval to the checked plan and exact bodies, including the selected target."""
    required = {'plan', 'metrics', 'queries', 'links'}
    if set(documents) - (required | {'publication_holds'}) or not required <= set(documents):
        raise ValueError('Read the checked plan and all three publication body files.')
    plan = documents['plan']
    if not isinstance(plan, dict):
        raise ValueError('Publication plan must be an object.')
    revisions = 0
    for kind in ('metrics', 'queries', 'links'):
        if not isinstance(documents[kind], list) or not isinstance(plan.get(kind), list):
            raise ValueError('Publication bodies and checked plan entries must be lists.')
        for item in plan[kind]:
            if not isinstance(item, dict) or type(item.get('ok')) is not bool:
                raise ValueError('Use a plan produced by the publication checker.')
            if kind != 'links':
                if item.get('action') not in ('create', 'revise', 'reuse'):
                    raise ValueError('Unknown publication action.')
                revisions += int(item['ok'] and item['action'] == 'revise')
    value = {'workspace_id': workspace_id, 'sources': sorted(sources), 'documents': documents}
    plan_id = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                        ensure_ascii=False).encode()).hexdigest()
    return plan_id, revisions
