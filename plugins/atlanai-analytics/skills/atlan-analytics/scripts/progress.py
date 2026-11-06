"""Durable phase progress from artifacts, with bounded, content-free events."""
from context_state import digest
from metric_preview import candidates

STAGES = ('inspect', 'scope', 'build', 'check', 'preview', 'publish', 'ready')
STATES = ('pending', 'running', 'waiting-for-user', 'complete', 'complete-with-held-items', 'failed', 'canceled')


def update(run, stage, status='running', action=None, completed=None, total=None):
    if stage not in STAGES or status not in STATES:
        raise ValueError('Select a supported setup stage and status.')
    if any(n is not None and (type(n) is not int or n < 0) for n in (completed, total)):
        raise ValueError('Progress counters must be nonnegative integers or unknown.')
    if total is not None and completed is not None and completed > total:
        raise ValueError('Completed progress cannot exceed the known total.')
    # Only known action codes enter progress files, never source text or URLs.
    if action not in (None, 'select_scope', 'select_workspace', 'authorize_plan', 'connect_data', 'review_held', 'resume'):
        raise ValueError('Use a supported action code.')
    path = run.root / 'progress.json'
    previous = run.read(path) if path.exists() else {}
    items = candidates(run)
    sources = run.manifest().get('sources', []) if run.path.exists() else []
    counts = {'draft': 0, 'checked': 0, 'held': 0, 'released': 0}
    for item in items:
        bucket = 'checked' if item['status'] in ('schema_checked', 'executed', 'example_executed') else item['status']
        counts[bucket] += 1
    if run.path.exists():
        receipt = run.manifest().get('publication', {})
        counts['released'] = len(receipt.get('artifacts', []))
    index = STAGES.index(stage) + 1
    state = {'schema_version': 1, 'run_id': 'run_' + digest(str(run.root))[:16],
             'sequence': previous.get('sequence', 0) + 1, 'stage': stage,
             'stage_index': index, 'stage_count': len(STAGES), 'steps_remaining': len(STAGES) - index,
             'status': status, 'completed_items': completed, 'total_items': total,
             'counts': counts, 'metric_count': sum(i['kind'] == 'metric' for i in items),
             'source_counts': {'total': len(sources),
                               'held': sum(s['status'].startswith('held') for s in sources),
                               'needs_review': sum(s['status'] == 'needs_model_review' for s in sources)},
             'query_count': sum(i['kind'] == 'sql_query' for i in items),
             'preview_refs': [i['key'] for i in items[:5]], 'action_required': action,
             'next_stage': STAGES[index] if index < len(STAGES) else None}
    state['summary'] = (f'{stage.capitalize()}: step {index} of {len(STAGES)} ({status}). '
                        + (f'{completed} of {total} items processed. ' if total is not None and completed is not None else '')
                        +
                        f"{counts['draft']} draft, {counts['checked']} checked, {counts['held']} held, {counts['released']} released.")
    run.write(path, state)
    events = run.root / 'progress-events.jsonl'
    lines = events.read_text().splitlines()[-199:] if events.exists() and not events.is_symlink() and events.stat().st_size <= 1_000_000 else []
    import json
    run.write_text(events, '\n'.join(lines + [json.dumps(state, separators=(',', ':'))]) + '\n')
    return state
