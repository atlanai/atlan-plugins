"""The guided-flow checklist: the steps a person can expect, each with its saved state.

Three flows share it. `analytics-build` (own data) shows ten steps, its dashboard branch ("Make my dashboard
agentic") ten of its own, and `analytics-demo` (a chosen demo) eight: in each, the build shows as
its steps (draft, merge, write notes, and on own data and demos the validation that runs with it).
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
        ('intent', 'Choose what your agent answers', 'Waiting for your choice', True),
        ('sources', 'Find your sources', 'Looking at your warehouse', False),
        ('scope', 'Pick tables and dashboards', 'Waiting for your pick', True),
        ('metrics', 'Build your context: metrics and saved queries', 'Building your context', False),
        ('save', 'Choose where to store your context', 'Waiting for your choice', True),
        ('ask', 'Ask your agent a question', 'Waiting for your question', True),
        ('dashboard', 'Build a dashboard from its answers (optional)', 'Waiting for your choice', True),
    ),
    'demo': (
        ('sources', 'Load the demo data', 'Loading the demo data', False),
        ('scope', 'Choose what to build your agent from', 'Waiting for your choice', True),
        ('metrics', 'Build your context: metrics and saved queries', 'Building your context', False),
        ('save', 'Choose where to store your context', 'Waiting for your choice', True),
        ('ask', 'Ask your agent a question', 'Waiting for your question', True),
    ),
    # Own data, "Make my dashboard agentic": the report and pages are picked before anything is checked, the check
    # waits for the person only when it changes the plan, and building and checking are two items.
    # The first item is the one the person already answered in card 1, ticked, so the list they saw keeps its place.
    'dashboard': (
        ('intent', 'Choose what your agent answers', 'Waiting for your choice', True),
        ('scope', 'Pick report and pages', 'Waiting for your report and pages', True),
        ('readiness', 'Check what your agent can use', "Checking the dashboard's tiles and tables", False),
        ('metrics', 'Build your context: metrics and saved queries', 'Building your context', False),
        ('check', 'Validate the context on your data', 'Validating each metric and saved query on your data', False),
        ('save', 'Choose where to store your context', 'Waiting for your choice', True),
        ('ask', 'Ask your agent a question', 'Waiting for your question', True),
        ('dashboard', 'Build a dashboard from its answers (optional)', 'Waiting for your choice', True),
    ),
}
FLOWS = tuple(STEPS)
# The step a paused run stopped at, while it waits.
PAUSED = 'Paused: your work is saved'
# Card 1's answer on the dashboard branch, after the first item's colon.
FROM_DASHBOARD = 'from your dashboard'
# Metric generation as its steps in the host's task list, in the metrics item's place: each ticks as the engine finishes
# its part and estimates its time from the count going in, fitted to measured runs (headless h2 and desktop v8b, Fixit).
THREE = (('draft', 'Build your context: metrics and saved queries', 'Finding metrics and saved queries'),
         ('merge', 'Merge duplicate metrics', 'Merging duplicate metrics'),
         ('write', 'Write detailed notes on each metric and saved query',
          'Writing what each one counts, leaves out and is based on'))
# On own data and demos the validation runs inside the build, with no item of its own in the flow: it shows as the
# build's last step, named as the dashboard branch names its separate item, so every route lists the same steps.
VALIDATE = ('test', 'Validate the context on your data', 'Validating each metric and saved query on your data')
ENDS = {'draft': 'x1_ingest', 'merge': 'x2_ingest'}


def _count(counts, stage, key):
    value = (counts.get(stage) or {}).get(key) if isinstance(counts.get(stage), dict) else None
    return value if type(value) is int else None


def _minutes(key, counts, done=()):
    queries, drafts, metrics = _count(counts, 'm2_emit', 'queries'), _count(counts, 'x1_ingest', 'proposals'), \
        _count(counts, 'x2_ingest', 'metrics_out')
    if key == 'draft' and queries is not None and 'm2_ingest' in done:
        # Once the queries are described, what is left is finding the metrics: 1.9 to 3.1 min for 30 measured.
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
    """The metrics item as its steps, each completed once the engine has accepted its part."""
    engine = model.get('engine') if isinstance(model.get('engine'), dict) else {}
    done, counts = set(engine.get('done') or []), engine.get('counts') if isinstance(engine.get('counts'), dict) else {}
    totals = model.get('counts') or {}
    made = {'draft': _count(counts, 'x1_ingest', 'proposals'), 'merge': _count(counts, 'x2_ingest', 'metrics_out')}
    phase = model.get('phase') or {}
    # Authoring ends when the run moves on to its checks: the notes are written, the validation is under way.
    written = phase.get('code') in STAGES and STAGES.index(phase['code']) >= STAGES.index('check')
    steps = THREE + ((VALIDATE,) if flow != 'dashboard' else ())
    rows, opened = [], False
    for key, content, active in steps:
        if row['status'] == 'completed' or (key in ENDS and ENDS[key] in done) or (key == 'write' and written and steps[-1] is VALIDATE):
            if key == 'draft' and made['draft'] is not None:
                content = 'Build your context: ' + _plural(made['draft'], 'metric') + ' found'
            elif key == 'merge' and made['merge'] is not None:
                content = 'Merge duplicate metrics: ' + _plural(made['merge'], 'metric')
            elif key == 'write' and totals.get('metrics'):
                content = content.split(' each')[0] + ' ' + _plural(totals['metrics'], 'metric') + \
                    ((' and ' + _plural(totals['queries'], 'saved query', 'saved queries')) if totals.get('queries') else '')
            elif key == 'test' and _tested(totals):
                content += ': ' + _tested(totals)
            rows.append({'id': key, 'content': content, 'active': active, 'needs_you': False, 'status': 'completed'})
            continue
        status = 'in_progress' if row['status'] == 'in_progress' and not opened else 'pending'
        opened = opened or status == 'in_progress'
        if status == 'in_progress' and row['active'] == PAUSED:
            active = PAUSED
        elif key in ('write', 'test') and status == 'in_progress' and row['active'] not in dict((s[0], s[2]) for s in STEPS[flow]).values():
            active = row['active']  # the run's own line, such as "Testing 5 of 9"
        minutes = _minutes(key, counts, done)
        if key == 'write' and status == 'in_progress' and 'x3_ingest' in done and 'x4_ingest' not in done:
            # Linking the metrics comes after the definitions and has no step of its own: 279 pairs took 6 minutes in a
            # watched run, with the list standing still.
            cards, pairs = _count(counts, 'x3_ingest', 'cards'), _count(counts, 'x4_emit', 'candidate_pairs')
            content += ' · %s, linking related ones' % ('%d written' % cards if cards else 'all written')
            minutes = 0.5 + 0.02 * pairs if pairs is not None else None
        if minutes is not None:
            content += ' (about %d min)' % max(1, int(minutes + 0.5))
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': False, 'status': status})
    return rows


def _expanded(listed, model, flow):
    """The list as the host shows it: the metrics item as its steps, the step counter recounted."""
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
    first = 1 if _flow(flow) == 'dashboard' else 0
    rows = [{'id': key, 'content': content + (': ' + FROM_DASHBOARD if key == 'intent' and first else ''),
             'active': active, 'needs_you': needs_you and index >= first,
             'status': 'completed' if index < first else 'in_progress' if index == first else 'pending'}
            for index, (key, content, active, needs_you) in enumerate(STEPS[_flow(flow)])]
    return rows if compact else _expanded({'steps': rows}, {}, _flow(flow))['steps']


def _plural(count, word, many=None):
    return f'{count} {word if count == 1 else many or word + "s"}'


def _tested(counts, skipped=False):
    """The validation's result after its colon: how many ran, and how many need a look."""
    total = (counts.get('metrics') or 0) + (counts.get('queries') or 0)
    if skipped:
        return 'skipped, as you chose'
    if not total:
        return ''
    return f"{counts.get('executed', 0)} of {total} tested" + (
        ', ' + _plural(counts['held'], 'needs your review', 'need your review') if counts.get('held') else '')


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
            # A paused run keeps the step it stopped at in progress, so the person sees where it stopped (0.9.74 showed
            # the started build as pending).
            current, status = key, 'in_progress'
        if key == 'sources':
            if done[key] and flow == 'demo':
                content = 'Load the demo data: ' + _plural(model['bound_tables'], 'table')
            elif done[key] and model.get('source_count'):
                content = 'Find your sources: ' + _plural(model['source_count'], 'found', 'found')
            elif counted and at == 0:
                waiting = next((s['name'] for s in model.get('scope', []) if s['status'] == 'pending_inspection'), '')
                active = f"Reading {progress['completed'] + 1} of {progress['total']}" + (': ' + waiting if waiting else '')
        elif key == 'intent' and intent.get('domain'):
            from run_facts import short_name
            content = 'Choose what your agent answers: ' + short_name(intent['domain'])
            if not intent.get('purpose'):
                active = 'Waiting for your choice'
        elif key == 'metrics':
            if done[key] and total:
                content = 'Build your context: ' + \
                    _plural(counts['metrics'], 'metric') + ' and ' + _plural(counts['queries'], 'saved query', 'saved queries')
            elif phase.get('source') == 'engine' and at == 2:
                active = phase['label']
            if counted and at == 3:
                active = f"Testing {progress['completed'] + 1} of {progress['total']}"
        elif key == 'save':
            if local:
                content = 'Choose where to store your context: on this machine'
            elif counts['released']:
                content = 'Save ' + _plural(counts['released'], 'metric or saved query', 'metrics and saved queries') + ' to Atlan'
            if at == 5 and not done[key]:
                active = 'Saving to Atlan'
                needs_you = False
        if stopped and key == current:
            active = PAUSED
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': needs_you and status != 'completed',
                     'status': status})
    state = 'paused' if stopped and current else 'blocked' if phase['status'] in ('blocked', 'failed') else \
        'access_needed' if phase['status'] == 'access_needed' else 'done' if current is None else 'running'
    return {'flow': flow, 'steps': rows, 'state': state,
            'step': next((i + 1 for i, r in enumerate(rows) if r['id'] == current), len(rows)), 'of': len(rows)}


def _dashboard(model):
    """The dashboard branch's eight items (ten in the host's list, the build as three), the first ticked from card 1. Each finished item carries its result after a colon, from the run's own
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
    # The checks end with their own recorded result, or with the person's choice to skip them: never on a save alone, and
    # never on the structural check, which records the check stage complete before any test has run (0.9.74: "0 of 40
    # tested" ticked the moment the person said to run them).
    checked = at >= 4 or (at == 3 and finished and model.get('checks_over', True)) or skipped
    waits = bool(readiness.get('waits'))
    done = {
        # Card 1 is answered before the branch starts: picking the dashboard is that answer.
        'intent': True,
        # A run past its build passed both early items, even one saved before they were recorded. Not one that is only
        # building: a Power BI link's fetch starts the build before the pages are picked or the check is recorded, and
        # 0.9.76's live run ticked "Check what your agent can use" before it ran.
        'scope': bool(report) or at >= 3,
        # A result that waits for the person's go-ahead keeps the item open, even though the check itself advanced the
        # build to its first written step (0.9.74 ticked it and showed the build under way).
        'readiness': not waits and (bool(readiness.get('result')) or at >= 3),
        'metrics': at >= 3 or (at == 2 and finished) or checked or saved,
        'check': checked or (saved and at >= 4),
        'save': saved,
        'ask': saved and bool(interaction.get('asked')),
        'dashboard': saved and bool(interaction.get('dashboard')),
    }
    results = {
        'intent': FROM_DASHBOARD,
        'scope': report,
        'readiness': readiness.get('result') or '',
        'metrics': (_plural(counts['metrics'], 'metric') + ' and ' + _plural(counts['queries'], 'saved query', 'saved queries')) if total else '',
        'check': _tested(counts, skipped),
        'save': 'on this machine' if local else
        (_plural(counts['released'], 'metric or saved query', 'metrics and saved queries') + ', now in Atlan') if counts.get('released') else '',
        'ask': 'answered',
        'dashboard': interaction.get('dashboard_result') or 'done',
    }
    rows, current = [], None
    for key, content, active, needs_you in STEPS['dashboard']:
        status = 'completed' if done[key] else 'pending'
        if status == 'pending' and current is None:
            current, status = key, 'in_progress'
        if key == 'readiness':
            # The check waits for the person only when what it found changes the plan.
            needs_you = waits
            if needs_you:
                active = 'Waiting for your go-ahead: ' + readiness.get('result', '')
        elif key == 'metrics' and phase.get('source') == 'engine' and at == 2:
            active = phase['label']
        elif key == 'save' and at == 5 and not done[key]:
            active, needs_you = 'Saving to Atlan', False
        if stopped and key == current:
            active = PAUSED
        if status == 'completed' and results[key]:
            from run_facts import clip
            content = content.split(':')[0] + ': ' + clip(results[key], 120)
        rows.append({'id': key, 'content': content, 'active': active, 'needs_you': needs_you and status != 'completed',
                     'status': status})
    state = 'paused' if stopped and current else 'blocked' if phase['status'] in ('blocked', 'failed') else \
        'access_needed' if phase['status'] == 'access_needed' else 'done' if current is None else 'running'
    return {'flow': 'dashboard', 'steps': rows, 'state': state,
            'step': next((i + 1 for i, r in enumerate(rows) if r['id'] == current), len(rows)), 'of': len(rows)}
