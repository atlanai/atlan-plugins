"""The moments the chat shows for a run, and the run's state for the side page, read from the run's own files.

`note` logs the moments the chat has shown, so the helper names any that are due and not shown yet (`due`). The run's
state is the host's task list's (`checklist`): `stopped` names it when the run is paused, blocked or waiting for access.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None

from run_facts import RunFacts, read_json


# When the saved run cannot be read, the moments seen say how far it got: the step each one finishes.
FINISHED_BY = {'sources': 'sources', 'build': 'scope', 'context': 'metrics', 'saved': 'save', 'answer': 'ask'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _write(path, text):
    """Replace a file whole, so a reader never sees half of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _state(run):
    state = read_json(Path(run) / 'view' / 'state.json', dict, None)
    if state and isinstance(state.get('moments'), list):
        return state
    return {'moments': []}


@contextmanager
def _locked(run):
    """One update at a time per run, where the system offers a file lock."""
    lock = Path(run) / 'view' / '.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, 'a') as handle:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_UN)


def seen(run):
    return [m.get('kind') for m in _state(run)['moments'] if isinstance(m, dict)]


def _answer_key(spec):
    return '%s | %s' % (spec.get('question') or '', spec.get('headline') or '')


def first_answer(run, spec):
    """Whether this answer is the run's first: the same answer shown again (a widget rerun of a text form) stays first."""
    return not any(isinstance(m, dict) and m.get('kind') == 'answer' and m.get('key') != _answer_key(spec)
                   for m in _state(run)['moments'])


# The moments the run's own files can show without a data file from the agent, so the run names them when due.
MIDWAY = ('connect', 'found', 'sources', 'group', 'tiles', 'described', 'drafted', 'metrics', 'rules', 'links', 'build', 'context')


def _shows(kind, run, engine_dir):
    import visuals
    if kind == 'connect':
        import side_page
        return side_page.has_output(RunFacts(run, engine_dir))
    if kind == 'found':
        # Own data only, right before the person chooses what to build from, so it can name a warehouse just connected.
        if _state(run).get('demo') or RunFacts(run).interaction.get('flow') == 'demo':
            return False
        rows, _ = _rows(run, 'build', seen(run), engine_dir)
        if next((r['id'] for r in rows if r['status'] != 'completed'), None) != 'scope':
            return False
    if kind == 'sources' and not (_state(run).get('demo') or RunFacts(run).interaction.get('flow') == 'demo'):
        # On own data the data read is the tables the person chose: it waits for that choice.
        if RunFacts(run).inputs is None:
            return False
    if kind == 'context':
        # Checks are recorded one by one; the moment waits until they are over.
        if not RunFacts(run, engine_dir).checks_over:
            return False
    try:
        return bool(visuals.render(kind, visuals.build_spec(kind, run, engine_dir=engine_dir)))
    except Exception:  # noqa: BLE001 - an engine file in a shape this cannot read leaves its moment unnamed, never the run stuck
        return False


def due(run, engine_dir=None):
    """The moments the run's files can show but the side view has not recorded, in order."""
    run = Path(run)
    if not (run / 'run.json').is_file():
        return []
    done = set(seen(run))
    return [kind for kind in MIDWAY if kind not in done and _shows(kind, run, engine_dir)]


def _rows(run, flow, kinds, engine_dir):
    """The flow's own task list with each step's saved state, as the host's task list shows it, and the run's state
    (running, paused, blocked, access_needed or done)."""
    import checklist
    try:
        import experience
        listed = checklist.build(experience.snapshot(run, engine_dir), flow, compact=True)
        return listed['steps'], listed['state']
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        rows = checklist.initial(flow, compact=True)
        ids = [r['id'] for r in rows]
        last = max((ids.index(FINISHED_BY[k]) for k in kinds if FINISHED_BY.get(k) in ids), default=-1)
        return [dict(r, status='completed' if i <= last else 'in_progress' if i == last + 1 else 'pending') for i, r in enumerate(rows)], 'running'


def stopped(run, engine_dir=None):
    """The run's state when it has stopped (paused, blocked or access_needed), as the host's task list has it, else
    None: the side page says so instead of looking busy."""
    run = Path(run)
    facts = RunFacts(run, engine_dir)
    flow = facts.interaction.get('flow') if facts.interaction.get('flow') in ('demo', 'dashboard') else 'build'
    # A run whose flow never names its engine still has one once the engine has written its manifest in the run.
    named = engine_dir or facts.meta.get('engine_run') or (facts.engine / 'manifest.json').is_file()
    _, state = _rows(run, flow, [], facts.engine if named and facts.engine.is_dir() else None)
    return state if state in ('paused', 'blocked', 'access_needed') else None


# Recording ------------------------------------------------------------------------------------

def note(run, kind, spec=None):
    """Log a moment the chat has shown, so the helper can name the ones still due and tell a first answer from a later
    one. The side page reads the run's own files and needs nothing from here."""
    with _locked(run):
        state = _state(run)
        state['moments'].append({'kind': kind, 'at': _now(), **({'key': _answer_key(spec or {})} if kind == 'answer' else {})})
        _write(Path(run) / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
