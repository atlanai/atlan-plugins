"""The guided-flow checklist: the steps a person can expect, each with its saved state.

Three flows share it. `analytics-build` (own data) shows nine steps, its dashboard branch ("Make my dashboard
agentic") ten of its own, and `analytics-demo` (a chosen demo) seven: in each, the build shows as
three steps (draft, merge, write).
Read-only. Every state and count comes from the saved run, so
the host's task list mirrors what happened and a resumed run starts with its finished steps
already done.
"""
STAGES = ('inspect', 'scope', 'build', 'check', 'preview', 'publish', 'ready')
FINISHED = ('complete', 'complete-with-held-items', 'partial_ready')
STOPPED = ('paused', 'canceled', 'waiting-for-user')
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
    # Own data, "Make my dashboard agentic": the report and pages are picked before anything is checked, the check
    # waits for the person only when it changes the plan, and building and checking are two items.
    'dashboard': (
        ('scope', 'Pick report and pages', 'Waiting for your report and pages', True),
        ('readiness', 'Check what can be built', 'Checking the report, its tables and its data', False),
        ('metrics', 'Build metrics and queries', 'Building metrics and queries', False),
        ('check', 'Check against the dashboard', 'Checking each metric and query against the dashboard', False),
        ('history', 'Add past queries (optional)', 'Waiting for your choice', True),
        ('save', 'Save or keep local', 'Waiting for where to save', True),
        ('ask', 'Ask a question', 'Waiting for your question', True),
        ('dashboard', 'Build a page from the metrics (optional)', 'Waiting for your choice', True),
    ),
}
FLOWS = tuple(STEPS)
# Metric generation as three steps in the host's task list, in the metrics item's place: each ticks as the engine finishes
# its part and estimates its time from the count going in, fitted to measured runs (headless h2 and desktop v8b, Fixit).
THREE = (('draft', 'Find metrics in your sources', 'Finding metrics: the numbers each query works out'),
         ('merge', 'Combine duplicates, one metric per number', 'Combining duplicates, one metric per number'),
         ('write', 'Define and test each metric', 'Defining what each metric counts, then testing it on the data'))
ENDS = {'draft': 'x1_ingest', 'merge': 'x2_ingest'}


def _count(counts, stage, key):
    value = (counts.get(stage) or {}).get(key) if isinstance(counts.get(stage), dict) else None
    return value if type(value) is int else None


def _minutes(key, counts, done=()):
    queries, drafts, metrics = _count(counts, 'm2_emit', 'queries'), _count(counts, 'x1_ingest', 'proposals'), \
        _count(counts, 'x2_ingest', 'metrics_out')
    if key == 'draft' and queries is not None and 'm2_ingest' in done:
        # Once the queries are described, what is left is finding the numbers: 1.9 to 3.1 min for 30 measured.
        return 0.6 + 0.06 * queries
    if key == 'draft' and queries is not None:
        return 1.5 + 0.1 * queries
    if key == 'merge' and drafts is not None:
        return 1 + 0.07 * drafts
    if key == 'write' and metrics is not None:
        # Above 20 metrics the definitions are shared among helpers, so each extra one costs less.
        return 2 + 0.6 * min(metrics, 20) + 0.15 * max(0, metrics - 20)
    return None


def _three(row, model, flow):
    """The metrics item as its three steps, each completed once the engine has accepted its part."""
    engine = model.get('engine') if isinstance(model.get('engine'), dict) else {}
    done, counts = set(engine.get('done') or []), engine.get('counts') if isinstance(engine.get('counts'), dict) else {}
    totals = model.get('counts') or {}
    made = {'draft': _count(counts, 'x1_ingest', 'proposals'), 'merge': _count(counts, 'x2_ingest', 'metrics_out')}
    rows, opened = [], False
    for key, content, active in THREE:
        if key == 'write' and flow == 'dashboard':
            content, active = 'Define each metric', 'Defining what each metric counts and leaves out'
        if row['status'] == 'completed' or (key in ENDS and ENDS[key] in done):
            if key == 'draft' and made['draft'] is not None:
                content = 'Find metrics: ' + _plural(made['draft'], 'possible metric')
            elif key == 'merge' and made['merge'] is not None:
                content = 'Combine duplicates: ' + _plural(made['merge'], 'metric')
            elif key == 'write' and totals.get('metrics'):
                content = content.split(' each')[0] + ' ' + _plural(totals['metrics'], 'metric') + \
                    ((' and ' + _plural(totals['queries'], 'query', 'queries')) if totals.get('queries') and flow != 'dashboard' else '')
            rows.append({'id': key, 'content': content, 'active': active, 'needs_you': False, 'status': 'completed'})
            continue
        status = 'in_progress' if row['status'] == 'in_progress' and not opened else 'pending'
        opened = opened or status == 'in_progress'
        if key == 'write' and status == 'in_progress' and row['active'] not in dict((s[0], s[2]) for s in STEPS[flow]).values():
            active = row['active']  # the run's own line, such as "Checking 5 of 9"
        minutes = _minutes(key, counts, done)
        if key == 'write' and status == 'in_progress' and 'x3_ingest' in done and 'x4_ingest' not in done:
            # Linking the metrics comes after the definitions and has no step of its own: 279 pairs took 6 minutes in a
            # watched run, with the list standing still.
            cards, pairs = _count(counts, 'x3_ingest', 'cards'), _count(counts, 'x4_emit', 'candidate_pairs')
            content += ' · %s, linking related ones' % ('%d defined' % cards if cards else 'all defined')
            minutes = 0.5 + 0.02 * pairs if pairs is not None else None
        if minutes is not None:
            content += ' (about %d min)' % max(1, int(minutes + 0.5))
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': False, 'status': status})
    return rows


def _expanded(listed, model, flow):
    """The list as the host shows it: the metrics item as three steps, the step counter recounted."""
    rows = []
    for row in listed['steps']:
        rows.extend(_three(row, model, flow) if row['id'] == 'metrics' else [row])
    current = next((i + 1 for i, r in enumerate(rows) if r['status'] == 'in_progress'),
                   next((i + 1 for i, r in enumerate(rows) if r['status'] != 'completed'), len(rows)))
    return dict(listed, steps=rows, step=current, of=len(rows))


def _flow(flow):
    return flow if flow in FLOWS else 'build'


def initial(flow='build', compact=False):
    """The list before any run exists: the first step under way, nothing claimed."""
    rows = [{'id': key, 'content': content, 'active': active, 'needs_you': needs_you,
             'status': 'in_progress' if index == 0 else 'pending'}
            for index, (key, content, active, needs_you) in enumerate(STEPS[_flow(flow)])]
    return rows if compact else _expanded({'steps': rows}, {}, _flow(flow))['steps']


def _plural(count, word, many=None):
    return f'{count} {word if count == 1 else many or word + "s"}'


def build(model, flow=None, compact=False):
    """The flow's list from the saved run. The host's list shows metric generation as three steps; `compact` keeps the
    one metrics item, for the side view's own steps."""
    listed = _listed(model, flow)
    return listed if compact else _expanded(listed, model, listed['flow'])


def _listed(model, flow=None):
    if _flow((model.get('interaction') or {}).get('flow') or flow) == 'dashboard':
        return _dashboard(model)
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
    # Keeping the context on this machine finishes the save, whatever the journey still waits on (such as a focus the
    # agent never recorded), and so does a save receipt with anything released: a save that held items leaves progress
    # at publish. A changed rule re-runs its check, which leaves progress at the checks again.
    saved = (action in ('ask', 'explore-local') or (at == 6 and finished) or (local and at >= 3)
             or bool(counts.get('released')))
    done = {
        # A demo's first step loads its data, so it ends when that data is bound, not at the survey.
        'sources': (at > 0 or finished) and (flow != 'demo' or bool(model.get('bound_tables'))),
        # A run saved before intent was recorded has passed this step once authoring began.
        'intent': bool(intent.get('domain') and intent.get('purpose')) or at >= 2,
        'scope': at >= 2 or (at == 1 and finished),
        # A saved or kept context was built and checked, even while a changed rule re-runs its check.
        'metrics': checked or saved,
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
            if done[key] and flow == 'demo':
                content = 'Load demo data: ' + _plural(model['bound_tables'], 'table')
            elif done[key] and model.get('source_count'):
                content = 'Survey ' + _plural(model['source_count'], 'source')
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


def _dashboard(model):
    """The dashboard branch's eight items (ten in the host's list, the build as three). Each finished item carries its result after a colon, from the run's own
    record: the report and pages the person picked, the readiness check's line, and the counts that came out."""
    phase, counts = model['phase'], model['counts']
    interaction = model.get('interaction') or {}
    local = interaction.get('mode') == 'local'
    at = STAGES.index(phase['code']) if phase['code'] in STAGES else 0
    finished = phase['status'] in FINISHED
    stopped = phase['status'] in STOPPED or interaction.get('mode') == 'paused'
    action = (model.get('continuation') or {}).get('action')
    total = counts['metrics'] + counts['queries']
    report = interaction.get('report') or ''
    readiness = interaction.get('readiness') or {}
    saved = (action in ('ask', 'explore-local') or (at == 6 and finished) or (local and at >= 3)
             or bool(counts.get('released')))
    skipped = interaction.get('checks') == 'skip'
    # The checks end with their own recorded result, or with the person's choice to skip them: never on a save alone.
    checked = at >= 4 or (at == 3 and finished) or skipped
    done = {
        # A run that is already building passed both early items, even one saved before they were recorded.
        'scope': bool(report) or at >= 2,
        'readiness': (bool(readiness.get('result')) and not readiness.get('waits')) or at >= 2,
        'metrics': at >= 3 or (at == 2 and finished) or checked or saved,
        'check': checked or (saved and at >= 4),
        # Past queries end with the person's no, with their build done, or with none to read: never on a save alone,
        # except a run saved before the step existed.
        'history': interaction.get('history') in ('added', 'skip', 'unavailable') or (saved and 'history' not in interaction),
        'save': saved,
        'ask': saved and bool(interaction.get('asked')),
        'dashboard': saved and bool(interaction.get('dashboard')),
    }
    results = {
        'scope': report,
        'readiness': readiness.get('result') or '',
        'metrics': (_plural(counts['metrics'], 'metric') + ' and ' + _plural(counts['queries'], 'query', 'queries')) if total else '',
        'check': 'skipped, your choice' if skipped else
        (f"{counts.get('executed', 0)} of {total} ran" + (f", {counts['held']} need a look" if counts.get('held') else ''))
        if total else '',
        'history': {'added': 'added', 'skip': 'skipped, your choice',
                    'unavailable': 'none readable for these tables'}.get(interaction.get('history'), ''),
        'save': 'kept on this machine' if local else
        (_plural(counts['released'], 'metric or query', 'metrics and queries') + ' saved to Atlan Trident') if counts.get('released') else '',
        'ask': interaction.get('asked_result') or 'answered',
        'dashboard': interaction.get('dashboard_result') or 'done',
    }
    rows, current = [], None
    for key, content, active, needs_you in STEPS['dashboard']:
        status = 'completed' if done[key] else 'pending'
        if status == 'pending' and current is None:
            current = key
            status = 'pending' if stopped else 'in_progress'
        if key == 'readiness':
            # The check waits for the person only when what it found changes the plan.
            needs_you = bool(readiness.get('waits'))
            if needs_you:
                active = 'Waiting for your go-ahead: ' + readiness.get('result', '')
        elif key == 'metrics' and phase.get('source') == 'engine' and at == 2:
            active = phase['label']
        elif key == 'history' and interaction.get('history') == 'add':
            active, needs_you = 'Adding past queries', False
        elif key == 'save' and at == 5 and not done[key]:
            active, needs_you = 'Saving to Atlan Trident', False
        if status == 'completed' and results[key]:
            content = content + ': ' + ' '.join(str(results[key]).split())[:120]
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': needs_you and status != 'completed',
                     'status': status})
    state = 'paused' if stopped and current else 'blocked' if phase['status'] in ('blocked', 'failed') else \
        'access_needed' if phase['status'] == 'access_needed' else 'done' if current is None else 'running'
    return {'flow': 'dashboard', 'steps': rows, 'state': state,
            'step': next((i + 1 for i, r in enumerate(rows) if r['id'] == current), len(rows)), 'of': len(rows)}
