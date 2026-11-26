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
import time

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


# The moments each build has its own of: a second build in the same run (its past queries added) shows them again
# (walk 4, 2026-11-23: every chip of the second build said "nothing new").
PER_BUILD = ('sources', 'group', 'described', 'drafted', 'metrics', 'rules', 'links', 'build', 'context')


def _build_name(run, engine_dir=None):
    try:
        return RunFacts(Path(run), engine_dir).engine.name
    except Exception:  # noqa: BLE001 - a run with no engine yet has the first build's moments
        return 'engine'


def seen(run, engine_dir=None):
    build = None
    out = []
    for m in _state(run)['moments']:
        if not isinstance(m, dict):
            continue
        if m.get('kind') in PER_BUILD:
            build = build or _build_name(run, engine_dir)
            if m.get('engine', 'engine') != build:
                continue
        out.append(m.get('kind'))
    return out


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


# The build's own steps. Once its card is shown they would come after it, out of order, so they stay back (validation
# run G, 2026-11-25: they came in a lump after the card, and none was drawn).
STEPS = ('found', 'sources', 'group', 'tiles', 'described', 'drafted', 'metrics', 'rules', 'links')


def due(run, engine_dir=None):
    """The moments the run's files can show but the side view has not recorded, in order."""
    run = Path(run)
    if not (run / 'run.json').is_file():
        return []
    done = set(seen(run, engine_dir))
    late = 'build' in done
    return [kind for kind in MIDWAY if kind not in done and not (late and kind in STEPS) and _shows(kind, run, engine_dir)]


# A wait that marked itself this recently is following the build: it looks every few seconds, and a session acts on
# what it prints and waits again within a minute or so.
WAIT_FRESH = 120


def mark_waiting(run, at=None):
    """The build wait says it is running, each time it looks: the engine then leaves the chips to it."""
    _write(Path(run) / 'view' / 'waiting.json', json.dumps({'at': time.time() if at is None else at}))


def waiting(run):
    """Whether a build wait is following the build now. With none, the session drives the engine itself, and the
    engine's own result hands over each chip."""
    at = read_json(Path(run) / 'view' / 'waiting.json', dict, {}).get('at')
    return isinstance(at, (int, float)) and time.time() - at < WAIT_FRESH


# What a result that carries a moment tells the session.
SHOW_FIRST = ('Show `show_first` now, before any other words or call: it is what the run has reached, as the chat shows '
              'it. Pass each widget in it to `show_widget`, or print its lines as they are, and do what each of its '
              '`side view` lines says.')


def handed(shown):
    """The fields a result carries a drawn moment in: nothing when nothing was drawn."""
    return {'show_first': shown, 'visual': SHOW_FIRST} if shown else {}


def show_card(run, kind, data=None):
    """A card drawn from what the result that produced it holds, as `visual <kind> --input -` draws it, and recorded:
    the result carries it whole. A guide line alone left the answer card undrawn (dry run C, 2026-11-25). Empty when it
    cannot be drawn, so that result never fails for it."""
    import subprocess
    import sys
    cmd = [sys.executable, str(Path(__file__).with_name('project.py')), '--run-dir', str(run), 'visual', kind]
    try:
        done = subprocess.run(cmd + (['--input', '-'] if data else []), input=json.dumps(data) if data else None,
                              capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return ''
    return (done.stdout or '').strip() if done.returncode == 0 else ''


def show_due(run, engine_dir=None, limit=4):
    """What the chat must show now, handed over whole: each moment the run has reached and not shown, as `visual next`
    draws it (a widget in the desktop app, a chip's lines in a terminal) with its own `side view` line, and marked
    shown. Empty when nothing is due or a moment cannot be drawn: the result that carries it never fails for it."""
    import subprocess
    import sys
    shown = []
    for _ in range(limit):
        before = due(run, engine_dir)
        if not before:
            break
        cmd = [sys.executable, str(Path(__file__).with_name('project.py')), '--run-dir', str(run), 'visual', 'next']
        try:
            done = subprocess.run(cmd + (['--engine-dir', str(engine_dir)] if engine_dir else []),
                                  capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.SubprocessError):
            break
        text = (done.stdout or '').strip()
        if done.returncode or not text or 'Nothing new to show yet' in text:
            break
        shown.append(text)
        if due(run, engine_dir) == before:
            break  # a moment that drew nothing new is left to its own call, never repeated here
    return '\n\n'.join(shown)


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

def note(run, kind, spec=None, engine_dir=None):
    """Log a moment the chat has shown, so the helper can name the ones still due and tell a first answer from a later
    one. The side page reads the run's own files and needs nothing from here."""
    with _locked(run):
        state = _state(run)
        state['moments'].append({'kind': kind, 'at': _now(), **({'key': _answer_key(spec or {})} if kind == 'answer' else {}),
                                 **({'engine': _build_name(run, engine_dir)} if kind in PER_BUILD else {})})
        _write(Path(run) / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
