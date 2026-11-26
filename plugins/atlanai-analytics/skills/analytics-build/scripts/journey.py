"""Durable conversation choices. Never performs engine, auth, publication or SQL work."""
ACTIONS = ('status', 'select', 'domain', 'purpose', 'pause', 'keep-local', 'resume', 'details', 'demo', 'asked', 'dashboard',
           'agentic', 'report', 'readiness', 'checks', 'history', 'tasks', 'question')
# Actions that record the person's own words, or a helper's one-line result.
ANSWERED = ('domain', 'purpose', 'report', 'readiness', 'dashboard', 'checks', 'history', 'tasks', 'asked', 'question')
# Steps recorded right after the person saw them happen: an answer, the tests, the past queries, the dashboard.
SEEN = ('asked', 'checks', 'history', 'dashboard')
FLOWS = ('build', 'demo', 'dashboard')
# Past queries on a dashboard's tables, offered once the dashboard's metrics are built and checked: the person adds them
# (`add`, then `added` once that build ends), declines (`skip`), or they cannot be read for those tables (`unavailable`).
HISTORY = ('add', 'added', 'skip', 'unavailable')
MODES = ('active', 'paused', 'local')


class JourneyError(ValueError):
    """A request the person or agent can correct: its message says how."""


def validate(value):
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('mode') not in MODES:
        raise JourneyError('The saved conversation choices need repair before continuing.')
    focus = value.get('focus')
    if focus is not None and (not isinstance(focus, str) or not 0 < len(focus) <= 512):
        raise JourneyError('The saved metric selection needs repair before continuing.')
    intent = value.get('intent', {})
    if (not isinstance(intent, dict) or any(k not in ('domain', 'purpose') for k in intent)
            or any(not isinstance(v, str) or not v.strip() or len(v) > 1000 for v in intent.values())
            or ('purpose' in intent and 'domain' not in intent)):
        raise JourneyError('The saved business focus and purpose need repair before continuing.')
    if 'intent_focus_required' in value and type(value['intent_focus_required']) is not bool:
        raise JourneyError('The saved intent selection needs repair before continuing.')
    if value.get('flow', 'build') not in FLOWS:
        raise JourneyError('The saved guided flow needs repair before continuing.')
    if 'question' in value and (not isinstance(value['question'], str) or not 0 < len(value['question']) <= 1000):
        raise JourneyError('The saved question this build answers needs repair before continuing.')
    if 'report' in value and (not isinstance(value['report'], str) or not 0 < len(value['report']) <= 1000):
        raise JourneyError('The saved report and pages need repair before continuing.')
    readiness = value.get('readiness', {})
    if (not isinstance(readiness, dict) or not isinstance(readiness.get('result', ''), str)
            or type(readiness.get('waits', False)) is not bool):
        raise JourneyError('The saved readiness check needs repair before continuing.')
    if value.get('checks', 'run') not in ('run', 'skip'):
        raise JourneyError('The saved choice about the warehouse checks needs repair before continuing.')
    if value.get('asked_result', 'answered') not in ('answered', 'declined'):
        raise JourneyError('The saved question step needs repair before continuing.')
    if value.get('history', 'skip') not in HISTORY:
        raise JourneyError('The saved choice about past queries needs repair before continuing.')
    if value.get('dashboard_result', 'built') not in ('built', 'declined'):
        raise JourneyError('The saved dashboard choice needs repair before continuing.')
    if any(k in value and type(value[k]) is not bool for k in ('asked', 'dashboard', 'tasks')):
        raise JourneyError('The saved query and dashboard steps need repair before continuing.')
    return value


def apply(model, saved, explicit_focus=False):
    """Overlay user intent without changing evidence, check status or release counts."""
    mode = validate(saved)['mode'] if saved else 'active'
    pending = saved.get('intent_focus_required', False) and not explicit_focus
    model['interaction'] = {'mode': mode, 'focus': model.get('focus'), 'intent': dict(saved.get('intent', {})),
                            'selection_pending': pending, 'flow': (saved or {}).get('flow', 'build'),
                            'asked': (saved or {}).get('asked', False),
                            'dashboard': (saved or {}).get('dashboard', False),
                            **{k: (saved or {})[k] for k in ('report', 'readiness', 'dashboard_result', 'checks', 'history',
                                                             'asked_result', 'question') if k in (saved or {})}}
    if pending:
        # Intent is a selection checkpoint. Never silently show the first alphabetic
        # metric or carry an earlier domain's focus into a new business question.
        model['sample'] = None
        model['focus'] = None
    if mode == 'paused':
        model['phase'] = {**model['phase'], 'status': 'paused', 'label': 'Your work is saved'}
        # The snapshot replaces this with where the work is and what to say to pick it up.
        model['next_step'] = 'Your work is saved on this machine.'
        model['next_prompt'] = 'Resume this saved analytics run from its current checkpoint.'
    model['continuation'] = continuation(model)
    if model['continuation']['action'] in ('choose-domain', 'choose-purpose'):
        model['next_step'] = model['continuation']['label']
        model['next_prompt'] = ('Help me choose the business area to focus on from these sources.'
                                if model['continuation']['action'] == 'choose-domain'
                                else 'Help me choose what to accomplish in the business area I selected.')
    elif model['continuation']['action'] == 'select-focus':
        model['next_step'] = model['continuation']['label']
        model['next_prompt'] = 'Select the relevant saved metric for my business focus and purpose, then show its check status.'
    elif model['continuation']['action'] == 'explore-local':
        model['phase'] = {**model['phase'], 'status': 'local', 'label': 'Your metrics and queries are available locally'}
        model['next_step'] = 'Explore a supported question using local data, or inspect the remaining checks.'
        model['next_prompt'] = 'Show a supported question I can explore with this local dataset and its check limitations.'
    return model


def _dashboard_now(model):
    """The dashboard list's item under way, or None once every item is done."""
    import checklist
    try:
        rows = checklist.build(model, 'dashboard', compact=True)['steps']
    except (KeyError, TypeError, ValueError):
        return None
    return next((r for r in rows if r['status'] == 'in_progress'), None)


def continuation(model):
    """One next action for the host, not a fabricated control or an execution result."""
    phase, counts = model['phase'], model['counts']
    mode = model['interaction']['mode']
    intent = model['interaction']['intent']
    # Ask after inspection, before authoring. Later-stage legacy runs retain their
    # checkpoint; an explicitly changed domain reopens the purpose question. A demo's focus is
    # preset, and the dashboard branch's is the report the person picked, so neither asks either
    # question (0.9.76's live run was told to ask "What business area…?" after every journey step).
    intent_due = ((bool(intent) or model.get('intent_checkpoint', False))
                  and model['interaction'].get('flow') not in ('demo', 'dashboard'))
    dashboard = model['interaction'].get('flow') == 'dashboard'
    if mode == 'paused':
        code, label, needs_input = 'resume', 'Resume', True
    elif model['warnings']:
        code, label, needs_input = 'repair', 'Review saved evidence', False
    elif phase['status'] == 'canceled':
        code, label, needs_input = 'resume', 'Resume', True
    elif intent_due and not intent.get('domain'):
        code, label, needs_input = 'choose-domain', 'What business area would you like to focus on?', True
    elif intent_due and not intent.get('purpose'):
        code, label, needs_input = 'choose-purpose', 'What would you like to accomplish in that area?', True
    elif dashboard and _dashboard_now(model):
        # The dashboard branch's next step is its own list's current item: never the shared flow's "confirm what to
        # include", a business area, or "review saved evidence" for the tiles it already kept back (walks 3 and 4).
        now = _dashboard_now(model)
        code, label, needs_input = 'dashboard-' + now['id'], now['active'], bool(now['needs_you'])
    elif model['interaction'].get('selection_pending') and (model['items'] or counts['provisional']):
        code, label, needs_input = 'select-focus', 'Selecting a metric for ' + intent.get('domain', 'your business focus'), False
    elif mode != 'local' and model['auth_status'] and model['auth_status'] != 'connected' and not model.get('local_work_available'):
        code, label, needs_input = 'recover-access', 'Restore access', True
    elif phase['status'] == 'waiting-for-user':
        code, label, needs_input = 'resume', 'Resume', True
    elif (model.get('sample') and model['sample']['status'] in ('held', 'needs_review')) or phase['status'] in ('blocked', 'failed') or (counts['held'] and not any(
            i['status'] != 'held' for i in model['items'])):
        code, label, needs_input = 'repair', 'Review the blocking check', False
    elif phase['code'] == 'ready' and phase['source'] == 'release_receipts' and phase['status'] in ('complete', 'partial_ready'):
        code, label, needs_input = 'ask', 'Choose a supported question', True
    else:
        stage = phase['code']
        if phase['status'] in ('complete', 'complete-with-held-items'):
            stage = {'inspect': 'scope', 'scope': 'build', 'build': 'check', 'check': 'show-preview'}.get(stage, stage)
        code, label, needs_input = {
            'inspect': ('inspect', 'Inspect the selected sources', False),
            'scope': ('scope', 'Confirm what to include', True),
            'build': ('build', 'Continue building metrics and queries', False),
            'check': ('check', 'Check the metrics and queries', False),
            'show-preview': ('preview', 'Show the checked sample', False),
            'preview': ('destination', 'Choose where to save', True),
            'publish': ('publish', 'Continue the approved save plan', False),
            'ready': ('repair', 'Review saved evidence', False),
        }[stage]
        if mode == 'local' and stage in ('preview', 'publish', 'ready') and model['items']:
            code, label, needs_input = 'explore-local', 'Choose a local question', True
    result = {'action': code, 'label': label, 'needs_input': needs_input,
              'surface': 'native_chat', 'host_executes': True}
    if code in ('choose-domain', 'choose-purpose'):
        result['question'] = {'topic': 'domain' if code == 'choose-domain' else 'purpose',
                              'prompt': label, 'domain': intent.get('domain'),
                              'options_from': 'observed_sources' if code == 'choose-domain' else 'selected_domain',
                              'allow_free_text': True, 'wait_for_answer': True}
    return result


def advance(run, action='status', focus=None, host=None, server=None, answer=None, waits=False):
    """Save only explicit user choices; status/details are read-only."""
    import experience
    experience.host_scope(host, server)
    if action not in ACTIONS:
        raise JourneyError('Choose a supported conversation action.')
    run.current()
    path = run.root / 'interaction.json'
    saved = validate(run.read(path)) if path.exists() else {'version': 1, 'mode': 'active', 'focus': None}
    if focus is not None and action != 'select':
        raise JourneyError('Use select to change the focused definition.')
    if answer is not None and action not in ANSWERED:
        raise JourneyError('Use domain, purpose, report, readiness, dashboard, checks, history, asked, tasks or question to '
                           'record an answer.')
    if waits and action != 'readiness':
        raise JourneyError('Only the readiness check can wait for the person.')
    if action == 'select':
        if not isinstance(focus, str) or not 0 < len(focus) <= 512:
            raise JourneyError('Select a definition by its saved id, such as metric:<name> or sql_query:<name>.')
        model = experience.snapshot(run, focus=focus, host=host, server=server)
        if not model['sample']:
            import metric_preview
            raise JourneyError('No saved definition has the id ' + repr(focus) + '. Select one by its saved id, such as '
                              'metric:<name>. Saved: ' + metric_preview.id_hint(i['id'] for i in model['items'])
                              + '. The selection is unchanged.')
        saved['focus'] = model['sample']['id']
        saved['definition_hash'] = model['sample'].get('definition_hash')
        saved['intent_focus_required'] = False
    elif action in ('domain', 'purpose'):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1000:
            raise JourneyError('Record the user\'s business focus or purpose in 1 to 1000 characters.')
        intent = dict(saved.get('intent', {}))
        if action == 'purpose' and not intent.get('domain'):
            raise JourneyError('Record the business area before its purpose.')
        answer = answer.strip()
        if action == 'domain' and intent.get('domain') != answer:
            intent.pop('purpose', None)
        if intent.get(action) != answer:
            saved['intent_focus_required'] = True
        intent[action] = answer
        saved['intent'] = intent
    elif action == 'question':
        # The person's own question, when it is what started the build: the build card names it and the first
        # question offered afterwards is that one.
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1000:
            raise JourneyError("Record the person's question with --answer, in their words, in 1 to 1000 characters.")
        saved['question'] = answer.strip()
    elif action == 'agentic':
        # The person chose "Make my dashboard agentic": the run shows the dashboard branch's ten items from now on.
        saved['flow'] = 'dashboard'
    elif action in ('report', 'readiness'):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1000:
            raise JourneyError('Record the ' + ('chosen report and pages' if action == 'report' else "readiness check's one-line result")
                               + ' with --answer, in 1 to 1000 characters.')
        if action == 'report':
            saved['report'] = answer.strip()
        else:
            # A result that changes the plan waits for the person's go-ahead; record it again without --waits after it.
            saved['readiness'] = {'result': answer.strip(), 'waits': bool(waits)}
    elif action == 'checks':
        # The person's own choice, asked once the build is done: run each metric and query on the warehouse, or skip.
        # Never recorded on the person's behalf.
        if answer not in ('run', 'skip'):
            raise JourneyError('Record the person\'s choice with --answer run or --answer skip.')
        saved['checks'] = answer
    elif action == 'history':
        # Asked once in the dashboard branch, after the checks: add the past queries on the dashboard's tables before
        # saving, or save the dashboard's metrics alone. `add` and `skip` are the person's; `added` and `unavailable`
        # are the build's result and what item 2 found.
        if answer not in HISTORY:
            raise JourneyError('Record the past-queries step with --answer add, added, skip or unavailable.')
        saved['history'] = answer
    elif action == 'tasks':
        # Whether the host keeps a task list the agent can update (the team, 2026-11-19): where it does, the list carries
        # the metric steps; where it does not, each finished step shows its chip. Recorded once, by the agent.
        if answer not in ('yes', 'no'):
            raise JourneyError('Record --answer yes when you have a task-list tool (TaskCreate and TaskUpdate, TodoWrite, or '
                               "the app's plan tool), else --answer no.")
        saved['tasks'] = answer == 'yes'
    elif action == 'demo':
        # The person chose a demo: the run shows the demo's seven steps from now on.
        saved['flow'] = 'demo'
    elif action in ('asked', 'dashboard'):
        # Step 6 ends when a chosen question was answered; step 7 when a dashboard was built
        # or the person declined one. Both are recorded only after they happen.
        if action == 'dashboard' and answer is not None:
            if answer not in ('built', 'declined'):
                raise JourneyError('Record the dashboard step with --answer built or --answer declined.')
            saved['dashboard_result'] = answer
        if action == 'asked' and answer is not None:
            # The person may end the run without a question (walk 4: "Ask a question" stayed open after "conclude here").
            if answer not in ('answered', 'declined'):
                raise JourneyError('Record the question step with --answer answered or --answer declined.')
            saved['asked_result'] = answer
        saved[action] = True
    elif action in ('pause', 'keep-local', 'resume'):
        saved['mode'] = {'pause': 'paused', 'keep-local': 'local', 'resume': 'active'}[action]
        if action == 'resume':
            progress_path = run.root / 'progress.json'
            checkpoint = run.read(progress_path) if progress_path.exists() else {}
            if checkpoint.get('status') in ('canceled', 'waiting-for-user'):
                import progress
                progress.update(run, checkpoint['stage'], 'running', 'resume')
        if action == 'keep-local':
            # Keeping the work here answers the save question, asked once the checked sample was shown.
            progress_path = run.root / 'progress.json'
            checkpoint = run.read(progress_path) if progress_path.exists() else {}
            if checkpoint.get('stage') == 'check' and checkpoint.get('status') in ('complete', 'complete-with-held-items'):
                import progress
                progress.update(run, 'preview', checkpoint['status'])
    if action not in ('status', 'details'):
        run.write(path, saved)
        try:  # A usage event for which choice was recorded, never the words chosen, and never able to fail it.
            import usage_events
            usage_events.choice(run, action, saved, host)
        except Exception:  # noqa: BLE001
            pass
    model = experience.snapshot(run, host=host, server=server)
    # A step the person just saw happen gets no summary back: the joint run of 2026-11-25 passed the whole run on after
    # an answer, as three sections nobody asked for.
    chat = {} if action in SEEN else {'chat': experience.render_chat(model, detail=action == 'details')}
    return {'status': 'ok', **chat,
            'interaction': model['interaction'], 'continuation': model['continuation'],
            'checklist': model['checklist'],
            'remote_writes': False, 'executed': False}
