"""Record host-scoped Registry reads and host-owned sign-in tasks. No credential access.

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
READ_FIELDS = {'host', 'server', 'status', 'read_tool', 'read_succeeded', 'observed_at'}


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


def record_read(run, request, prior, now):
    """Append current-host evidence without changing another host's recovery checkpoint."""
    if set(request) != {'operation', 'host', 'server', 'read_tool', 'read_succeeded'}:
        raise ValueError('Record only the current host, server and successful Registry read.')
    host, server = request['host'], identifier(request['server'])
    if (host not in HOSTS or request['read_tool'] != 'registry__list_workspaces'
            or request['read_succeeded'] is not True):
        raise ValueError('A successful current-host registry__list_workspaces read is required.')
    path = run.root / 'registry-readiness.json'
    saved = run.read(path) if path.exists() else {'schema_version': 1, 'observations': []}
    if (not isinstance(saved, dict) or set(saved) != {'schema_version', 'observations'}
            or type(saved['schema_version']) is not int or saved['schema_version'] != 1
            or not isinstance(saved['observations'], list)):
        raise ValueError('The saved host readiness observations are invalid; preserve them for review.')
    for entry in saved['observations']:
        if (not isinstance(entry, dict) or set(entry) != READ_FIELDS or entry['host'] not in HOSTS
                or entry['status'] != 'connected' or entry['read_tool'] != 'registry__list_workspaces'
                or entry['read_succeeded'] is not True or type(entry['observed_at']) not in (int, float)
                or not 0 <= entry['observed_at'] < float('inf')):
            raise ValueError('The saved host readiness observations are invalid; preserve them for review.')
        identifier(entry['server'])
    observation = {'host': host, 'server': server, 'status': 'connected',
                   'read_tool': 'registry__list_workspaces', 'read_succeeded': True, 'observed_at': now}
    saved['observations'].append(observation)
    run.write(path, saved)
    reply = {**observation, 'verification': 'host_reported_read', 'remote_writes': False,
             'next_action': 'use_current_host_readiness', 'recovery_unchanged': True}
    if prior:
        reply['preserved_recovery'] = {key: prior[key] for key in
                                      ('host', 'server', 'status', 'started_at', 'updated_at', 'task_id') if key in prior}
        if (prior.get('host'), prior.get('server')) == (host, server) and prior.get('task_id') and prior.get('status') in ACTIVE:
            reply['next_action'] = 'resolve_owned_sign_in_task'
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
    if operation == 'record_read':
        return record_read(run, request, prior, now)
    elif operation == 'begin':
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
                prior['updated_at'] = now
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
        raise ValueError('Use begin, poll or observe for recovery, or record_read for a successful current-host read.')
    state['updated_at'] = now
    run.write(path, state)
    return result(state)
