"""Usage events for the analytics journey: which stage a run reached and which choices were made.

The add-on has no hooks, so its helpers raise these themselves, from the two places a run's state
changes (progress.update and journey.advance) and from the eval and accuracy helpers. The sending
is the core plugin's: usage_sender.py, telemetry_events.json and telemetry_config.json beside
this file are written by tools/build.py from the core's command-hooks/, so the same closed list
and the same switches hold here.

An event is a name and a few typed values from telemetry_events.json: a stage, a status, a flow,
counts. Never a business area or purpose as the person worded it, a metric's name, a question, a
SQL statement, a path or a result. Nothing is sent while the build carries no write key, under
any opt-out, or from a copy of these scripts that is not the installed atlanai-analytics plugin
(a skill served from Atlan Trident carries no plugin.json, so it stays silent).

Every function here returns None on any fault: a usage event never fails or delays a step.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# skills/analytics-build/scripts/ sits three folders under the plugin's root.
PLUGIN_ROOT = HERE.parents[2]
HARNESSES = ('claude_code', 'codex', 'cursor', 'copilot')
MODES = {'pause': 'paused', 'keep-local': 'local', 'resume': 'active'}
_SENDER = None


def sender():
    """usage_sender.py, loaded once and without writing __pycache__ into the plugin."""
    global _SENDER
    if _SENDER is None:
        spec = importlib.util.spec_from_file_location('atlan_analytics_usage_sender', HERE / 'usage_sender.py')
        module = importlib.util.module_from_spec(spec)
        written, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = written
        _SENDER = module
    return _SENDER


def harness_of(env, host=None):
    """The tool these helpers run in: the host the caller named, else what the environment says."""
    if host in HARNESSES:
        return host
    if env.get('CLAUDECODE') or env.get('CLAUDE_CODE_ENTRYPOINT'):
        return 'claude_code'
    if any(name.startswith('CODEX_') for name in env):
        return 'codex'
    if any(name.startswith('CURSOR_') for name in env):
        return 'cursor'
    if any(name.startswith('COPILOT_') for name in env):
        return 'copilot'
    return 'unknown'


def flow_of(run):
    """The guided flow the run is on, from its saved choices: build until the person chose another."""
    try:
        saved = json.loads((Path(run.root) / 'interaction.json').read_text(encoding='utf-8'))
        return saved.get('flow', 'build') if saved.get('flow', 'build') in ('build', 'demo', 'dashboard') else 'build'
    except Exception:  # noqa: BLE001 - no saved choices yet
        return 'build'


def emit(found, run=None, host=None, **options):
    """Send (name, properties) pairs for one run. Returns the batch, or None. Never raises."""
    try:
        env = options.pop('env', None)
        env = os.environ if env is None else env
        # The run folder's path names the person's project, so only a hash of it, salted with the
        # install id inside the sender, ever leaves.
        seed = str(Path(run.root).resolve()) if run is not None and getattr(run, 'root', None) is not None else None
        options.setdefault('root', PLUGIN_ROOT)
        return sender().emit(found, harness_of(env, host), run=seed, env=env, **options)
    except Exception:  # noqa: BLE001
        return None


def stage(run, previous, state, **options):
    """A run moved to a new stage or a new status of one. The first move of a run also starts it."""
    try:
        if (previous.get('stage'), previous.get('status')) == (state.get('stage'), state.get('status')):
            return None
        flow = flow_of(run)
        counts, sources = state.get('counts', {}), state.get('source_counts', {})
        reached = {'stage': state.get('stage'), 'status': state.get('status'), 'flow': flow,
                   'metrics': state.get('metric_count'), 'queries': state.get('query_count'),
                   'checked': counts.get('checked'), 'held': counts.get('held'), 'released': counts.get('released'),
                   'sources': sources.get('total')}
        found = ([] if previous else [('analytics_run_started', {'flow': flow})]) + [('analytics_stage_reached', reached)]
        return emit(found, run, **options)
    except Exception:  # noqa: BLE001
        return None


def choice(run, action, saved, host=None, **options):
    """A choice the person made was recorded. Which choice, never the words they chose."""
    try:
        if action == 'demo':
            found = [('analytics_flow_chosen', {'flow': 'demo'})]
        elif action == 'agentic':
            found = [('analytics_flow_chosen', {'flow': 'dashboard'})]
        elif action in ('domain', 'purpose'):
            found = [('analytics_intent_recorded', {'topic': action})]
        elif action == 'checks':
            found = [('analytics_checks_chosen', {'choice': saved.get('checks')})]
        elif action == 'asked':
            found = [('analytics_question_asked', {'flow': saved.get('flow', 'build')})]
        elif action == 'dashboard':
            found = [('analytics_dashboard_finished', {'result': saved.get('dashboard_result', 'built')})]
        elif action in MODES:
            found = [('analytics_run_mode_changed', {'mode': MODES[action]})]
        else:
            return None
        return emit(found, run, host, **options)
    except Exception:  # noqa: BLE001
        return None


def evals_generated(questions, **options):
    """An eval set was written: how many questions it holds."""
    try:
        return emit([('analytics_evals_generated', {'questions': questions})], **options)
    except Exception:  # noqa: BLE001
        return None


def accuracy_scored(summary, final=False, **options):
    """An accuracy check was scored: how many questions, and the share answered correctly."""
    try:
        rate = (summary.get('all') or {}).get('pass_rate')
        if rate is None:
            return None
        scored = {'questions': summary.get('cases'), 'pass_percent': int(round(rate * 100)), 'final': bool(final)}
        return emit([('analytics_accuracy_scored', scored)], **options)
    except Exception:  # noqa: BLE001
        return None
