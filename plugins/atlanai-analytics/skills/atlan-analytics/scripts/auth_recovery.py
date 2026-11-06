"""Record a host-owned sign-in task and its bounded readiness watch. No credential access.

The host starts/polls the login task, opens Chrome and supplies observed results. This
helper never authenticates, launches commands, reads credentials or makes Registry calls.
"""
import re
import time
import uuid

HOSTS = ('claude_code', 'codex')
REASONS = ('sign_in', 'tools_missing', 'plugin_missing', 'disabled', 'permission_denied', 'connection_error')
ACTIVE = ('login_required', 'waiting_auth', 'checking_mcp')
STAGES = ('inspect', 'scope', 'build', 'check', 'preview', 'publish', 'ready')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_:.-]{0,119}', value):
        raise ValueError('Use the observed host task or connection identifier, never a URL or credential.')
    return value


def fallback(host):
    if host == 'claude_code':
        return {'install_commands': ['claude plugin install atlanai --marketplace atlanai/atlan-plugins'],
                'sign_in': '/mcp: select plugin:atlanai:atlan, then Authenticate',
                'reload': '/reload-plugins, then reconnect the existing Atlan server'}
    return {'install_commands': ['codex plugin marketplace add atlanai/atlan-plugins',
                                 'codex plugin add atlanai@atlan'],
            'sign_in': 'codex mcp login atlan',
            'reload': 'Refresh the host tool inventory; if unavailable, open a new chat with the saved run directory'}


def result(state):
    status = state['status']
    next_action = {'login_required': 'start_host_login', 'waiting_auth': 'poll_host_task',
                   'checking_mcp': 'read_registry_in_current_host', 'connected': 'resume_saved_stage',
                   'tools_missing': 'inspect_host_plugin_and_connection', 'plugin_missing': 'install_core_plugin',
                   'disabled': 'request_connection_enablement', 'permission_denied': 'resolve_workspace_permission',
                   'connection_error': 'check_selected_connection', 'reload_required': 'reload_host_tools',
                   'failed': 'show_manual_recovery', 'timed_out': 'show_manual_recovery',
                   'canceled': 'stop_watch'}[status]
    reply = {**state, 'next_action': next_action, 'fallback': fallback(state['host']),
             'verification': 'host_reported_read' if status == 'connected' else 'not_verified',
             'remote_writes': False}
    if status == 'login_required':
        client = 'claude' if state['host'] == 'claude_code' else 'codex'
        reply['login_argv'] = [client, 'mcp', 'login', state['server']]
    if status in ('waiting_auth', 'checking_mcp'):
        reply['poll_after_seconds'] = 5
    if status in ('failed', 'timed_out', 'canceled', 'permission_denied') and state.get('task_id'):
        reply['stop_owned_task_if_running'] = state['task_id']
    return reply


def advance(run, request, now=None):
    """Only allowlisted, content-free observations can enter the private checkpoint."""
    allowed = {'operation', 'host', 'reason', 'server', 'resume_stage', 'timeout_seconds',
               'attempt_id', 'event', 'task_id', 'read_tool', 'read_succeeded'}
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError('Supply only recovery observations; never store URLs, tokens or raw login output.')
    now = time.time() if now is None else now
    path = run.root / 'auth-recovery.json'
    prior = run.read(path) if path.exists() else None
    operation = request.get('operation')
    if operation == 'begin':
        host, reason = request.get('host'), request.get('reason')
        stage, timeout = request.get('resume_stage', 'publish'), request.get('timeout_seconds', 600)
        if host not in HOSTS or reason not in REASONS or stage not in STAGES:
            raise ValueError('Select a supported host, observed connection state and saved setup stage.')
        if type(timeout) is not int or not 30 <= timeout <= 900:
            raise ValueError('Choose a watch timeout between 30 and 900 seconds.')
        server = identifier(request.get('server') or ('plugin:atlanai:atlan' if host == 'claude_code' else 'atlan'))
        if prior and prior['status'] in ACTIVE:
            if now >= prior['deadline']:
                prior['status'] = 'timed_out'
                run.write(path, prior)
                return result(prior)  # Return the owned task for cleanup before another attempt.
            if (prior['host'], prior['server']) != (host, server):
                raise ValueError('Finish or cancel the existing sign-in task before changing connections.')
            return result(prior)  # Do not start duplicate OAuth attempts on resume.
        state = {'schema_version': 1, 'attempt_id': uuid.uuid4().hex, 'host': host, 'server': server,
                 'reason': reason, 'resume_stage': stage, 'started_at': now, 'deadline': now + timeout,
                 'status': 'login_required' if reason == 'sign_in' else reason, 'browser_opened': False}
    elif operation in ('poll', 'observe'):
        if not prior:
            raise ValueError('Begin recovery from observed host state first.')
        state = dict(prior)
        if operation == 'observe' and request.get('attempt_id') != state['attempt_id']:
            raise ValueError('Ignore stale sign-in events; use the current recovery attempt.')
        if state['status'] in ACTIVE and now >= state['deadline']:
            state['status'] = 'timed_out'
        elif operation == 'observe':
            event = request.get('event')
            if event == 'login_started' and state['status'] == 'login_required':
                state.update(status='waiting_auth', task_id=identifier(request.get('task_id')))
            elif event == 'browser_opened' and state['status'] == 'waiting_auth':
                state['browser_opened'] = True
            elif event == 'login_succeeded' and state['status'] == 'waiting_auth':
                if request.get('task_id') != state.get('task_id'):
                    raise ValueError('Observe the same host login task before checking Registry access.')
                state['status'] = 'checking_mcp'
            elif event == 'registry_ready' and state['status'] in ('checking_mcp', 'tools_missing', 'connection_error', 'reload_required'):
                if request.get('read_tool') != 'registry__list_workspaces' or request.get('read_succeeded') is not True:
                    raise ValueError('A successful current-host Registry read is required; CLI login alone is insufficient.')
                state.update(status='connected', read_tool='registry__list_workspaces')
            elif event == 'tools_not_loaded' and state['status'] in ('checking_mcp', 'tools_missing'):
                state['status'] = 'reload_required'
            elif event in ('failed', 'canceled', 'permission_denied') and state['status'] in ACTIVE:
                state['status'] = event
            else:
                raise ValueError('This observation does not apply to the current recovery state.')
    else:
        raise ValueError('Use begin, poll or observe for sign-in recovery.')
    run.write(path, state)
    return result(state)
