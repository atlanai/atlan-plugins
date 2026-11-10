"""The setup checklist: six steps a person can expect, each with its saved state.

Read-only. Every state and count comes from the saved run, so the host's task list
mirrors what happened and a resumed run starts with its finished steps already done.
"""
STAGES = ('inspect', 'scope', 'build', 'check', 'preview', 'publish', 'ready')
FINISHED = ('complete', 'complete-with-held-items', 'partial_ready')
STOPPED = ('paused', 'canceled', 'waiting-for-user')
# content is the task's name; active is what the host shows while it runs.
STEPS = (
    ('sources', 'Read your sources', 'Reading your sources', False),
    ('intent', 'Choose focus and goal', 'Waiting for your focus and goal', True),
    ('build', 'Write metrics and queries', 'Writing metrics and queries', False),
    ('check', 'Check each metric and query', 'Checking metrics and queries', False),
    ('review', 'Review and choose where to save', 'Waiting for your review', True),
    ('ready', 'Ready for questions', 'Preparing questions', False),
)


def initial():
    """The list before any run exists: the first step under way, nothing claimed."""
    return [{'id': key, 'content': content, 'active': active, 'needs_you': needs_you,
             'status': 'in_progress' if index == 0 else 'pending'}
            for index, (key, content, active, needs_you) in enumerate(STEPS)]


def _plural(count, word, many=None):
    return f'{count} {word if count == 1 else many or word + "s"}'


def build(model):
    phase, counts = model['phase'], model['counts']
    progress = model.get('progress') or {}
    interaction = model.get('interaction') or {}
    intent = interaction.get('intent') or {}
    local = interaction.get('mode') == 'local'
    at = STAGES.index(phase['code']) if phase['code'] in STAGES else 0
    finished = phase['status'] in FINISHED
    stopped = phase['status'] in STOPPED or interaction.get('mode') == 'paused'
    action = (model.get('continuation') or {}).get('action')
    total = counts['metrics'] + counts['queries']
    done = {
        'sources': at > 0 or finished,
        # A run saved before intent was recorded has passed this step once authoring began.
        'intent': bool(intent.get('domain') and intent.get('purpose')) or at >= 2,
        'build': at >= 3 or (at == 2 and finished),
        'check': at >= 4 or (at == 3 and finished),
        'review': action in ('ask', 'explore-local') or (at == 6 and finished),
    }
    done['ready'] = done['review']
    counted = (phase.get('source') == 'project' and phase['status'] == 'running'
               and type(progress.get('total')) is int and progress['total'] > 0
               and type(progress.get('completed')) is int and progress['completed'] < progress['total'])
    rows, current = [], None
    for key, content, active, needs_you in STEPS:
        status = 'completed' if done[key] else 'pending'
        if status == 'pending' and current is None:
            current = key
            status = 'pending' if stopped else 'in_progress'
        if key == 'sources':
            if done[key] and model.get('source_count'):
                content = 'Read ' + _plural(model['source_count'], 'source')
            elif counted and at == 0:
                waiting = next((s['name'] for s in model.get('scope', []) if s['status'] == 'pending_inspection'), '')
                active = f"Reading source {progress['completed'] + 1} of {progress['total']}" + (': ' + waiting if waiting else '')
        elif key == 'intent' and intent.get('domain'):
            content = 'Focus: ' + ' '.join(intent['domain'].split())[:60]
            if not intent.get('purpose'):
                active = 'Waiting for your goal'
        elif key == 'build':
            if done[key] and total:
                content = 'Write ' + _plural(counts['metrics'], 'metric') + ' and ' + _plural(counts['queries'], 'query', 'queries')
            elif phase.get('source') == 'engine' and at == 2:
                active = phase['label']
        elif key == 'check':
            if total and (done[key] or at == 3):
                content = 'Check ' + _plural(total, 'metric or query', 'metrics and queries')
            if counted and at == 3:
                active = f"Checking {progress['completed'] + 1} of {progress['total']}"
        elif key == 'review':
            if local:
                content = 'Review, kept local'
            elif counts['released']:
                content = 'Save ' + _plural(counts['released'], 'metric or query', 'metrics and queries') + ' to Atlan Trident'
            if at == 5 and not done[key]:
                active = 'Saving to Atlan'
                needs_you = False
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': needs_you and status != 'completed',
                     'status': status})
    state = 'paused' if stopped and current else 'blocked' if phase['status'] in ('blocked', 'failed') else \
        'access_needed' if phase['status'] == 'access_needed' else 'done' if current is None else 'running'
    return {'steps': rows, 'state': state, 'step': next((i + 1 for i, r in enumerate(rows) if r['id'] == current), len(rows)),
            'of': len(rows)}
