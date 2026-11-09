"""Durable conversation choices. Never performs engine, auth, publication or SQL work."""
ACTIONS = ('status', 'select', 'domain', 'purpose', 'pause', 'keep-local', 'resume', 'details')
MODES = ('active', 'paused', 'local')


def validate(value):
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('mode') not in MODES:
        raise ValueError('The saved conversation choices need repair before continuing.')
    focus = value.get('focus')
    if focus is not None and (not isinstance(focus, str) or not 0 < len(focus) <= 512):
        raise ValueError('The saved metric selection needs repair before continuing.')
    intent = value.get('intent', {})
    if (not isinstance(intent, dict) or any(k not in ('domain', 'purpose') for k in intent)
            or any(not isinstance(v, str) or not v.strip() or len(v) > 1000 for v in intent.values())
            or ('purpose' in intent and 'domain' not in intent)):
        raise ValueError('The saved business focus and purpose need repair before continuing.')
    return value


def apply(model, saved):
    """Overlay user intent without changing evidence, check status or release counts."""
    mode = validate(saved)['mode'] if saved else 'active'
    model['interaction'] = {'mode': mode, 'focus': model.get('focus'), 'intent': dict(saved.get('intent', {}))}
    if mode != 'active':
        model['phase'] = {**model['phase'], 'status': 'paused' if mode == 'paused' else 'local',
                          'label': 'Your work is saved' if mode == 'paused' or model['counts']['released'] else 'Your drafts are saved locally'}
        model['next_step'] = ('Resume here when ready; completed work will be reused.' if mode == 'paused'
                              else 'Continue inspecting locally, or resume when you want to save definitions in Atlan.')
        model['next_prompt'] = 'Resume this saved analytics run from its current checkpoint.'
    model['continuation'] = continuation(model)
    if model['continuation']['action'] in ('choose-domain', 'choose-purpose'):
        model['next_step'] = model['continuation']['label']
        model['next_prompt'] = ('Help me choose the business area to focus on from these sources.'
                                if model['continuation']['action'] == 'choose-domain'
                                else 'Help me choose what to accomplish in the business area I selected.')
    return model


def continuation(model):
    """One next action for the host, not a fabricated control or an execution result."""
    phase, counts = model['phase'], model['counts']
    mode = model['interaction']['mode']
    intent = model['interaction']['intent']
    # Ask after inspection, before authoring. Later-stage legacy runs retain their
    # checkpoint; an explicitly changed domain reopens the purpose question.
    intent_due = bool(intent) or model.get('intent_checkpoint', False)
    if mode != 'active':
        code, label, needs_input = 'resume', 'Resume', True
    elif model['warnings']:
        code, label, needs_input = 'repair', 'Review saved evidence', False
    elif phase['status'] == 'canceled':
        code, label, needs_input = 'resume', 'Resume', True
    elif intent_due and not intent.get('domain'):
        code, label, needs_input = 'choose-domain', 'What business area would you like to focus on?', True
    elif intent_due and not intent.get('purpose'):
        code, label, needs_input = 'choose-purpose', 'What would you like to accomplish in that area?', True
    elif model['auth_status'] and model['auth_status'] != 'connected':
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
            'build': ('build', 'Continue building definitions', False),
            'check': ('check', 'Check the definitions', False),
            'show-preview': ('preview', 'Show the checked sample', False),
            'preview': ('destination', 'Choose where to save', True),
            'publish': ('publish', 'Continue the approved save plan', False),
            'ready': ('repair', 'Review saved evidence', False),
        }[stage]
    result = {'action': code, 'label': label, 'needs_input': needs_input,
              'surface': 'native_chat', 'host_executes': True}
    if code in ('choose-domain', 'choose-purpose'):
        result['question'] = {'topic': 'domain' if code == 'choose-domain' else 'purpose',
                              'prompt': label, 'domain': intent.get('domain'),
                              'options_from': 'observed_sources' if code == 'choose-domain' else 'selected_domain',
                              'allow_free_text': True, 'wait_for_answer': True}
    return result


def advance(run, action='status', focus=None, host=None, server=None, answer=None):
    """Save only explicit user choices; status/details are read-only."""
    import experience
    experience.host_scope(host, server)
    if action not in ACTIONS:
        raise ValueError('Choose a supported conversation action.')
    run.current()
    path = run.root / 'interaction.json'
    saved = validate(run.read(path)) if path.exists() else {'version': 1, 'mode': 'active', 'focus': None}
    if focus is not None and action != 'select':
        raise ValueError('Use select to change the focused definition.')
    if answer is not None and action not in ('domain', 'purpose'):
        raise ValueError('Use domain or purpose to record an intent answer.')
    if action == 'select':
        if not isinstance(focus, str) or not 0 < len(focus) <= 512:
            raise ValueError('Supply the exact saved definition identity.')
        model = experience.snapshot(run, focus=focus, host=host, server=server)
        if not model['sample']:
            raise ValueError('That definition is unavailable or ambiguous. Keep the existing selection.')
        saved['focus'] = model['sample']['id']
        saved['definition_hash'] = model['sample'].get('definition_hash')
    elif action in ('domain', 'purpose'):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1000:
            raise ValueError('Record the user\'s business focus or purpose in 1 to 1000 characters.')
        intent = dict(saved.get('intent', {}))
        if action == 'purpose' and not intent.get('domain'):
            raise ValueError('Record the business area before its purpose.')
        answer = answer.strip()
        if action == 'domain' and intent.get('domain') != answer:
            intent.pop('purpose', None)
        intent[action] = answer
        saved['intent'] = intent
    elif action in ('pause', 'keep-local', 'resume'):
        saved['mode'] = {'pause': 'paused', 'keep-local': 'local', 'resume': 'active'}[action]
        if action == 'resume':
            progress_path = run.root / 'progress.json'
            checkpoint = run.read(progress_path) if progress_path.exists() else {}
            if checkpoint.get('status') in ('canceled', 'waiting-for-user'):
                import progress
                progress.update(run, checkpoint['stage'], 'running', 'resume')
    if action not in ('status', 'details'):
        run.write(path, saved)
    model = experience.snapshot(run, host=host, server=server)
    return {'status': 'ok', 'chat': experience.render_chat(model, detail=action == 'details'),
            'interaction': model['interaction'], 'continuation': model['continuation'],
            'remote_writes': False, 'executed': False}
