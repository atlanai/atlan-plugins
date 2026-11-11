"""Settle a partly successful save: what was created, what failed, what is unknown, and what next.

Standard library only. No Registry calls. Reads the batch descriptors `publication_batches.py`
returned, the host's saved result of each create call, and any ids already resolved, then
prints three lists of call descriptors for the host to send:

- `lookup_calls`: reads that resolve an item whose slot the result did not report (a result
  can be cut to its first rows) or a link end with no id yet;
- `link_calls`: the approved `source_of`/`uses_metric` links whose two ends both exist;
- `retry`: failed items whose code says a resend can succeed (an internal or timeout error,
  confirmed absent by a lookup first). A `conflict` is never retried: it is reported and held.

`depends_on` links are not written here: the metric app derives them from each metric's
definition, and a metric whose dependency failed keeps that edge pending.
"""
import argparse
import json
from pathlib import Path

NO_RETRY = {'conflict', 'invalid_arguments', 'forbidden', 'not_found', 'unknown_kind'}


def _load_result(path):
    """A create result as the host saved it: raw JSON, an MCP content list, or a runner wrapper."""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    for _ in range(4):
        if isinstance(value, list) and value and isinstance(value[0], dict) and 'text' in value[0]:
            value = json.loads(value[0]['text'])
        elif isinstance(value, dict) and 'content' in value and isinstance(value['content'], list):
            value = json.loads(value['content'][0]['text'])
        elif isinstance(value, dict) and 'results' in value and len(value['results']) == 1:
            value = value['results'][0]
        else:
            break
    if not isinstance(value, dict) or 'items' not in value:
        raise ValueError(f'{path} is not a create_artifacts result.')
    return value


def settle(batches, results, resolved, links, workspace, names):
    """Return the per-item outcome, and the reads, links and retries that follow from it."""
    outcome = {}
    for call, path in zip(batches['calls'], results):
        kind = call['arguments']['kind']
        result = _load_result(path) if path else {'items': [], 'created': None}
        reported = {int(r.get('index', -1)): r for r in result.get('items') or []}
        # A RESULT CUT TO ITS FIRST ROWS STILL CARRIES ITS TOTALS. When every failure the total
        # counts is among the reported rows, every unreported slot was created: only its id is
        # unknown, and only a link that needs it pays for a lookup.
        failed_total = result.get('failed')
        failed_seen = sum(1 for r in reported.values() if not r.get('ok'))
        all_failures_seen = isinstance(failed_total, int) and failed_total == failed_seen
        for index, source_id in enumerate(call['source_ids']):
            key = (kind, names.get(source_id, source_id))
            row = reported.get(index)
            if row is None:
                outcome[key] = {'source_id': source_id,
                                'status': 'created' if all_failures_seen else 'unreported'}
            elif row.get('ok'):
                art = row.get('artifact') or {}
                outcome[key] = {'source_id': source_id, 'status': 'created',
                                'id': art.get('id') or row.get('id')}
            else:
                outcome[key] = {'source_id': source_id, 'status': 'failed',
                                'code': row.get('code') or 'unknown', 'error': row.get('error')}
    for key_text, artifact_id in (resolved or {}).items():
        kind, name = key_text.split(':', 1)
        entry = outcome.setdefault((kind, name), {'status': 'existing'})
        entry['id'] = artifact_id
        if entry.get('status') == 'unreported':
            entry['status'] = 'created'

    def lookup(kind, name):
        return {'name': 'registry__list_artifacts',
                'arguments': {'kind': kind, 'workspace_id': workspace, 'name': name, 'limit': 1}}

    lookups, seen = [], set()
    for (kind, name), entry in outcome.items():
        if entry['status'] == 'unreported' or (entry['status'] == 'failed' and entry['code'] not in NO_RETRY):
            lookups.append(lookup(kind, name))
            seen.add((kind, name))
    link_calls, held_links = [], []
    for link in links:
        if link.get('rel_type') == 'depends_on':
            continue
        ends = [(link[e]['kind'], link[e]['name']) for e in ('from', 'to')]
        states = [outcome.get(end, {'status': 'existing'}) for end in ends]
        if any(s['status'] == 'failed' for s in states):
            held_links.append({'from': ends[0][1], 'to': ends[1][1], 'reason': 'an end failed to save'})
            continue
        if all(s.get('id') for s in states):
            link_calls.append({'name': 'registry__relate_artifact',
                               'arguments': {'artifact_id': states[0]['id'], 'subject_id': states[1]['id'],
                                             'rel_type': link['rel_type'],
                                             'attributes': link.get('attributes') or {}}})
        else:
            for end, state in zip(ends, states):
                if not state.get('id') and end not in seen:
                    lookups.append(lookup(*end))
                    seen.add(end)
    retry = [{'kind': k, 'name': n, 'source_id': e['source_id'], 'code': e['code']}
             for (k, n), e in outcome.items() if e['status'] == 'failed' and e['code'] not in NO_RETRY]
    held = [{'kind': k, 'name': n, 'code': e['code'], 'error': e.get('error')}
            for (k, n), e in outcome.items() if e['status'] == 'failed' and e['code'] in NO_RETRY]
    counts = {}
    for entry in outcome.values():
        counts[entry['status']] = counts.get(entry['status'], 0) + 1
    return {'counts': counts, 'held': held, 'retry_after_lookup': retry,
            'lookup_calls': lookups, 'link_calls': link_calls, 'held_links': held_links,
            'next': ('Send lookup_calls, record each found id in the resolved file, and run this '
                     'again. Retry an item only after its lookup shows it absent, through a new '
                     'checked plan for that item alone. Never resend a conflict.')}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--batches', type=Path, required=True,
                        help="publication_batches.py's printed result, saved to a file")
    parser.add_argument('--result', action='append', default=[],
                        help="the host's saved result of each create call, in the batches' order; "
                             "pass '' for a call whose result was lost")
    parser.add_argument('--resolved', type=Path,
                        help='a JSON object of "<kind>:<name>": "<artifact id>" already confirmed')
    args = parser.parse_args()
    try:
        batches = json.loads(args.batches.read_text(encoding='utf-8'))
        if len(args.result) != len(batches['calls']):
            raise ValueError('Pass one --result per batch call, in order.')
        names = {}
        for lane in ('metrics', 'queries'):
            for body in json.loads((args.context / (lane + '.json')).read_text(encoding='utf-8')):
                names[body['source_id']] = body['name']
        links = json.loads((args.context / 'links.json').read_text(encoding='utf-8'))
        resolved = json.loads(args.resolved.read_text(encoding='utf-8')) if args.resolved else {}
        # Reused artifacts were never created: their ids come from the checked plan.
        plan = json.loads((args.context / 'plan.json').read_text(encoding='utf-8'))
        for lane, kind in (('metrics', 'metric'), ('queries', 'sql_query')):
            for item in plan.get(lane) or []:
                if item.get('action') == 'reuse' and item.get('match_id'):
                    resolved.setdefault(f"{kind}:{item['name']}", item['match_id'])
        print(json.dumps(settle(batches, args.result, resolved, links, args.workspace, names)))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'REFUSED', 'reason': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
