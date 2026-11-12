"""The guided-flow checklist: the steps a person can expect, each with its saved state.

Two flows share it. `analytics-build` (own data) shows seven steps and `analytics-demo` (a
chosen demo) six: the demo shows its metric names before building, not a first metric.
Read-only. Every state and count comes from the saved run, so
the host's task list mirrors what happened and a resumed run starts with its finished steps
already done.
"""
STAGES = ('inspect', 'scope', 'build', 'check', 'preview', 'publish', 'ready')
FINISHED = ('complete', 'complete-with-held-items', 'partial_ready')
STOPPED = ('paused', 'canceled', 'waiting-for-user')
FLOWS = ('build', 'demo')
# (id, content, active, needs_you)
STEPS = {
    'build': (
        # Own data asks what the person is trying to do before it looks at anything, so the
        # survey that follows can be about that.
        ('intent', 'Clarify your use case', 'Waiting for your use case', True),
        ('sources', 'Survey your sources', 'Surveying your sources', False),
        ('scope', 'Select tables and dashboards', 'Waiting for your selection', True),
        ('metrics', 'Generate and check metrics', 'Generating metrics and queries', False),
        ('save', 'Keep local or save to Atlan Trident', 'Waiting for where to save', True),
        ('ask', 'Ask a question', 'Waiting for your question', True),
        ('dashboard', 'Build a dashboard', 'Waiting for your dashboard choice', True),
    ),
    'demo': (
        ('sources', 'Choose and load demo data', 'Loading the demo data', False),
        ('scope', 'Choose what to build from', 'Waiting for your choice of sources', True),
        ('metrics', 'Build and check metrics', 'Building metrics and queries', False),
        ('save', 'Keep local or save to Atlan Trident', 'Waiting for where to save', True),
        ('ask', 'Ask a question', 'Waiting for your question', True),
    ),
}


def _flow(flow):
    return flow if flow in FLOWS else 'build'


def initial(flow='build'):
    """The list before any run exists: the first step under way, nothing claimed."""
    return [{'id': key, 'content': content, 'active': active, 'needs_you': needs_you,
             'status': 'in_progress' if index == 0 else 'pending'}
            for index, (key, content, active, needs_you) in enumerate(STEPS[_flow(flow)])]


def _plural(count, word, many=None):
    return f'{count} {word if count == 1 else many or word + "s"}'


def build(model, flow=None):
    phase, counts = model['phase'], model['counts']
    progress = model.get('progress') or {}
    interaction = model.get('interaction') or {}
    flow = _flow(interaction.get('flow') or flow)
    intent = interaction.get('intent') or {}
    local = interaction.get('mode') == 'local'
    at = STAGES.index(phase['code']) if phase['code'] in STAGES else 0
    finished = phase['status'] in FINISHED
    stopped = phase['status'] in STOPPED or interaction.get('mode') == 'paused'
    action = (model.get('continuation') or {}).get('action')
    total = counts['metrics'] + counts['queries']
    checked = at >= 4 or (at == 3 and finished)
    # Kept on this machine once checked: a demo goes straight to its question, with no preview stage.
    saved = action in ('ask', 'explore-local') or (at == 6 and finished) or (local and checked)
    done = {
        'sources': at > 0 or finished,
        # A run saved before intent was recorded has passed this step once authoring began.
        'intent': bool(intent.get('domain') and intent.get('purpose')) or at >= 2,
        'scope': at >= 2,
        'metrics': checked,
        'save': saved,
        'ask': saved and bool(interaction.get('asked')),
    }
    # A demo can build its dashboard before asking anything, so this step stands on its own.
    done['dashboard'] = saved and bool(interaction.get('dashboard'))
    counted = (phase.get('source') == 'project' and phase['status'] == 'running'
               and type(progress.get('total')) is int and progress['total'] > 0
               and type(progress.get('completed')) is int and progress['completed'] < progress['total'])
    rows, current = [], None
    # The use case is asked in chat before a run exists, and saved once the survey has made one.
    # While that survey runs, it is the step under way: the run is not waiting on the person.
    surveying = flow == 'build' and at == 0 and not finished and not stopped and not done['intent']
    for key, content, active, needs_you in STEPS[flow]:
        status = 'completed' if done[key] else 'pending'
        if surveying and key == 'intent':
            needs_you = False
        elif status == 'pending' and current is None:
            current = key
            status = 'pending' if stopped else 'in_progress'
        if key == 'sources':
            if done[key] and model.get('source_count'):
                content = ('Load demo data: ' if flow == 'demo' else 'Survey ') + _plural(model['source_count'], 'source')
            elif counted and at == 0:
                waiting = next((s['name'] for s in model.get('scope', []) if s['status'] == 'pending_inspection'), '')
                active = f"Reading source {progress['completed'] + 1} of {progress['total']}" + (': ' + waiting if waiting else '')
        elif key == 'intent' and intent.get('domain'):
            content = 'Use case: ' + ' '.join(intent['domain'].split())[:60]
            if not intent.get('purpose'):
                active = 'Waiting for your goal'
        elif key == 'metrics':
            if done[key] and total:
                content = ('Generate and check ' if flow == 'build' else 'Build and check ') + \
                    _plural(counts['metrics'], 'metric') + ' and ' + _plural(counts['queries'], 'query', 'queries')
            elif phase.get('source') == 'engine' and at == 2:
                active = phase['label']
            if counted and at == 3:
                active = f"Checking {progress['completed'] + 1} of {progress['total']}"
        elif key == 'save':
            if local:
                content = 'Kept on this machine'
            elif counts['released']:
                content = 'Save ' + _plural(counts['released'], 'metric or query', 'metrics and queries') + ' to Atlan Trident'
            if at == 5 and not done[key]:
                active = 'Saving to Atlan Trident'
                needs_you = False
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': needs_you and status != 'completed',
                     'status': status})
    state = 'paused' if stopped and current else 'blocked' if phase['status'] in ('blocked', 'failed') else \
        'access_needed' if phase['status'] == 'access_needed' else 'done' if current is None else 'running'
    return {'flow': flow, 'steps': rows, 'state': state,
            'step': next((i + 1 for i, r in enumerate(rows) if r['id'] == current), len(rows)), 'of': len(rows)}
