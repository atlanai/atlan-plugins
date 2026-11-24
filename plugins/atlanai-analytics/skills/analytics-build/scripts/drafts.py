"""Where the build has got to, from the batches it saved, for the session that talks to the person.

The describe, propose, merge and card gates save their answers as numbered batch files. `wait` keeps that session in one
turn while the build runs: it returns one line for each thing to act on (a step's end, the task list's counts, every
share of the definitions back, a quiet spell, the build done), counted from the same files as the side page. It reads
only the run's own files and records what it reported; the engine still validates every file at its gate.
"""
import json
from pathlib import Path

from context_state import ordinary

MAX_BYTES = 4_000_000
MAX_FILES = 200


class _Engine:
    def __init__(self, root, engine_dir):
        self.root = ordinary(root)
        state = self.json(self.root / 'run.json')
        chosen = Path(engine_dir or state.get('engine_run') or 'engine')
        self.dir = ordinary(chosen if chosen.is_absolute() else self.root / chosen, self.root)

    def json(self, path):
        """A bounded read; a missing, oversized or half-written file reads as empty."""
        try:
            path = ordinary(path, self.root)
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                return {}
            value = json.loads(path.read_bytes())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, RecursionError):
            return {}

    def answers(self, gate):
        folder = self.dir / 'answers' / gate
        try:
            return sorted(folder.glob('*.json'))[:MAX_FILES] if folder.is_dir() else []
        except OSError:
            return []

    def rows(self, path, key):
        found = self.json(path).get(key)
        return [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []


def _plural(n, word):
    return f'{n} {word}' + ('' if n == 1 else 's')


def _proposals(engine):
    """Every draft's id, numbered as the engine numbers them: `<query>#<n>`."""
    ids = set()
    for path in engine.answers('x1'):
        for row in engine.rows(path, 'statements'):
            metrics = row.get('metrics') if isinstance(row.get('metrics'), list) else []
            if isinstance(row.get('vq_id'), str):
                ids.update(f"{row['vq_id']}#{i}" for i, m in enumerate(metrics) if isinstance(m, dict))
    ids.update(r['proposal_id'] for r in engine.rows(engine.dir / 'metric_proposals.json', 'proposals')
               if isinstance(r.get('proposal_id'), str))
    return ids


def _source_ids(row):
    sources = row.get('sources') if isinstance(row.get('sources'), list) else []
    ids = []
    for s in sources:
        pid = s if isinstance(s, str) else s.get('proposal_id') if isinstance(s, dict) else None
        if isinstance(pid, str):
            ids.append(pid)
    return ids


def _propose(engine, files):
    statements = [r for p in files for r in engine.rows(p, 'statements')]
    read = len({r.get('vq_id') for r in statements if isinstance(r.get('vq_id'), str)})
    total = sum(len(t['queries']) for t in engine.json(engine.dir / 'x1_prompt.json').get('tasks', [])
                if isinstance(t, dict) and isinstance(t.get('queries'), list))
    drafted = sum(len(r['metrics']) for r in statements if isinstance(r.get('metrics'), list))
    # The queries that held a number, as the step's own chip counts them ("found in 25 of those 30 queries").
    held = len({r.get('vq_id') for r in statements if isinstance(r.get('vq_id'), str) and r.get('metrics')})
    return {'queries_read': read, 'queries_total': total, 'queries_with': held, 'drafts': drafted}


def _merge(engine, files):
    claimed = {pid for p in files for r in engine.rows(p, 'metrics') for pid in _source_ids(r)}
    return {'drafts': len(_proposals(engine)), 'drafts_merged': len(claimed),
            'metrics': sum(len(engine.rows(p, 'metrics')) for p in files)}


def _define(engine, files):
    written = sum(len(engine.rows(p, 'metrics')) for p in files)
    total = engine.json(engine.dir / 'x3_prompt.json').get('n_metrics')
    return {'defined': written, 'total': total if type(total) is int and total >= written else None}


def _describe(engine, files):
    described = len({q.get('vq_id') for p in files for q in engine.rows(p, 'queries') if isinstance(q.get('vq_id'), str)})
    total = sum(len(t['queries']) for t in engine.json(engine.dir / 'm2_prompt.json').get('tasks', [])
                if isinstance(t, dict) and isinstance(t.get('queries'), list))
    return {'described': described, 'total': total}


GATES = (('x3', 'define', _define), ('x2', 'merge', _merge), ('x1', 'propose', _propose), ('m2', 'describe', _describe))


def _saved(engine, gate):
    """A gate's complete answer files, oldest first: a file still being written waits, and
    parallel writers finish out of name order, so save time decides."""
    files = [f for f in engine.answers(gate) if engine.json(f)]
    return sorted(files, key=lambda f: (f.stat().st_mtime_ns, f.name))


def _under_way(engine):
    """The furthest step with saved batches while its gate is not yet accepted, as (gate, step, describe, files); None
    before any batch and once that step has ended."""
    stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
    accepted = {s.get('stage') for s in stages if isinstance(s, dict) and s.get('verdict') == 'ok'}
    for gate, step, describe in GATES:
        files = _saved(engine, gate)
        if gate + '_ingest' in accepted:
            if files:
                return None
            continue
        # A step whose prompt is written is under way before its first batch lands: its row counts from 0, so the list
        # never sits on a bare step name while its helper writes (a live run showed no count for two minutes).
        if files or (engine.dir / ('%s_prompt.json' % gate)).is_file():
            return (gate, step, describe, files)
    return None


REPORTED = 'drafts-reported.json'


def _save_record(engine, record):
    try:
        path = engine.root / REPORTED
        path.write_text(json.dumps(record))
        path.chmod(0o600)
    except OSError:
        pass  # without the record the next wait repeats an event; it never skips one


# The steps whose end wakes the session: the tile review and the tile questions (a helper may write them), queries
# described, drafts found, drafts merged, metrics defined.
WAKES = ('a3b_ingest', 'c3_ingest', 'm2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest')
# The task list moves at most this often: an update for every batch woke the session 24 times in eight minutes, and
# once a minute left it at "3 of 7" while the side page, which reads the same files live, said 6 of 7 (2026-11-23).
BATCH_SECONDS = 20


# Where the session keeps a task list, it carries the build: the step under way counts its batches, from the same files
# the side page counts, with time left from the step's own pace.
ROW_OF = {'m2': 'draft', 'x1': 'draft', 'x2': 'merge', 'x3': 'write'}
SHORT = {'draft': 'Build your context: metrics and saved queries', 'merge': 'Merge duplicate metrics'}


def _live(engine, under):
    """The step under way as its task-list row says it: what has landed so far, and time left when the pace is known."""
    gate, _, describe, files = under
    c = describe(engine, files)
    # Each part of a step says which part it is, so a new count reads as the next part, never as the count going back
    # ("32 of 50 questions written (nearly done)", then "6 metrics found so far, 8 of 50 questions read"). The time left
    # follows the step's own pace since it started, so it moves as the work does (the person's call, 2026-11-23).
    if gate == 'm2':
        done, total = c.get('described') or 0, c.get('total')
        said = 'writing questions: %d of %d' % (done, total) if total else 'writing questions: %d' % done
    elif gate == 'x1':
        done, total = c.get('queries_read') or 0, c.get('queries_total')
        said = 'finding metrics: %s in %s' % (_plural(c.get('drafts') or 0, 'metric'),
                                              '%d of %d questions' % (done, total) if total else _plural(done, 'question'))
    elif gate == 'x2':
        # How many metrics the drafts make is only known once they are all combined: no count until then.
        return 'combining duplicates'
    else:
        done, total = c.get('defined') or 0, c.get('total')
        said = '%d of %d written' % (done, total) if total else '%d written' % done
    left = _left(engine, gate, done, total)
    return said + (' (%s)' % left if left else '')


def _left(engine, gate, done, total):
    """Time left at the step's pace so far: the time since its first batch landed, per item done since, for the items
    still to come. Measured from the batches, not the step's prompt: a run resumed hours later read "about 480 min left".
    None until a second batch, when there is a pace."""
    import time
    if not total or not done:
        return None
    if done >= total:
        return 'nearly done'
    files = _saved(engine, gate)
    if len(files) < 2:
        return None
    first = files[0]
    head = {'m2': 'queries', 'x1': 'statements'}.get(gate, 'metrics')
    since = done - len(engine.rows(first, head))
    if since <= 0:
        return None
    seconds = (time.time() - first.stat().st_mtime) / since * (total - done)
    return 'nearly done' if seconds < 45 else 'about %d min left' % max(1, round(seconds / 60))


def task_list(root, engine_dir=None):
    """The host's task list as the checklist gives it, with the step under way saying how far it has got."""
    import checklist
    import experience
    rows = checklist.build(experience.snapshot(root, engine_dir))['steps']
    engine = _Engine(root, engine_dir)
    under = _under_way(engine)
    out = []
    for r in rows:
        content = r['content']
        if under and r['status'] == 'in_progress' and r.get('id') == ROW_OF[under[0]]:
            live = _live(engine, under)
            content = '%s · %s' % (SHORT.get(r['id'], content.split(' (about')[0]), live) if live else content
        out.append({'content': content, 'status': r['status']})
    return out


WAITED = {
    'batch': "Add no words of your own, nor the list's rows: the person sees the list, and the side page shows each "
             "batch. Set your task list to `task_list`, each step's "
             "text and status as given (load the task-list tool first if it is deferred), in the same message as your "
             "next `PROJECT drafts --wait`, so the turn goes on.",
    'step': "Add no words of your own beyond what `visual next` gives: run it with the flags of every `visual` "
            "call now (it is quick) and show what it gives in the same message that starts the next step's subagent, "
            "if its gate result asks for one, in this same turn. Only a step that no chip names gets a start line, "
            "the one its flow's file gives.",
    'due': "Add no words of your own: something is ready to show, and no step has ended. Run `visual next` with the "
           "flags of every `visual` call now and do what it says, then run `PROJECT drafts --wait` again.",
    'ready': "Add no words of your own: every share of the definitions is back. Run `ENGINE advance` once, send each "
             "refused card to the subagent whose share holds its task number, then run `PROJECT drafts --wait` again.",
    'quiet': "Nothing new has reached the screen for a while. Check that each subagent is working. When nothing has "
             "shown for about 3 minutes, write one line for the person, in the task list's own words: the step under "
             "way and its count (\"Adding detailed information: 10 of 22 written.\"), never a word about waiting, never what you are about to do "
             "and never a line you already wrote. Then run `PROJECT drafts --wait` again.",
    'done': "Add no words of your own: export the build into the run and run `PROJECT check` for the structural pass, "
            "as build and check says. The build's card comes once, right before the save question.",
    'again': "Add no words of your own: the wait timed out while new things were showing. Run `PROJECT drafts --wait` again.",
}


def _all_back(engine):
    """The definitions' saved files as a stamp once every metric has one and the gate is not yet accepted, else None:
    only the session advances the definitions, once every share is back."""
    under = _under_way(engine)
    if not under or under[0] != 'x3':
        return None
    c = _define(engine, under[3])
    if not c['total'] or c['defined'] < c['total']:
        return None
    return [len(under[3]), max(f.stat().st_mtime_ns for f in under[3])]


def wait(root, engine_dir=None, timeout=540, quiet=240, interval=2.0, out=None, batch_every=BATCH_SECONDS):
    """Wait in the foreground until the build has something to act on, print it as one JSON line and return, so the
    session acts on it and waits again in the same turn: a turn that ends without words is sent back for them, while a
    turn that keeps going is not. It reports a step's end once, every share of the definitions back once per set of
    files, the task list at most every `batch_every` seconds where the session keeps one, and a quiet spell after `quiet`
    seconds with nothing new or at `timeout`, which a host keeps under its own limit for a foreground command."""
    import sys
    import time
    out = out or sys.stdout
    engine = _Engine(root, engine_dir)
    start = last = time.monotonic()
    seen_now = None

    def say(event, **extra):
        print(json.dumps({'event': event, **extra, 'next': WAITED[event]}, ensure_ascii=False), file=out, flush=True)

    while True:
        record = engine.json(engine.root / REPORTED)
        done = set(record.get('steps') if isinstance(record.get('steps'), list) else [])
        stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
        accepted = {s.get('stage') for s in stages if isinstance(s, dict) and s.get('verdict') == 'ok'}
        for stage in WAKES:
            if stage in accepted and stage not in done:
                _save_record(engine, dict(record, steps=sorted(done | {stage})))
                return say('step')
        # The links step comes after the definitions: the build is done once it has a verdict, kept or skipped.
        if 'x3_ingest' in accepted and any(isinstance(s, dict) and s.get('stage') == 'x4_ingest' and s.get('verdict') for s in stages):
            return say('done')
        # A chip that comes due without a step end this session sees (the dashboard flow's helpers advance every step),
        # and the side page once there is something on it: each named once.
        import side_view
        try:
            due = [k for k in side_view.due(root, engine_dir) if k not in (record.get('told') or [])]
        except (OSError, ValueError, KeyError, TypeError):
            due = []
        if due:
            _save_record(engine, dict(record, told=sorted(set(record.get('told') or []) | set(due))))
            return say('due')
        back = _all_back(engine)
        if back and back != record.get('ready'):
            _save_record(engine, dict(record, ready=back))
            return say('ready')
        now = sum(len(_saved(engine, gate)) for gate, _, _ in GATES)
        if now != seen_now:
            seen_now, last = now, time.monotonic()
        import visuals
        moved = record.get('waited_at') if isinstance(record.get('waited_at'), (int, float)) else 0
        # A new batch moves the list; a step that has only started (its prompt written, nothing saved) does not.
        under = _under_way(engine)
        if (now != record.get('waited') and time.time() - moved >= batch_every and under and under[3]
                and visuals.keeps_tasks(root)):
            _save_record(engine, dict(record, waited=now, waited_at=time.time()))
            return say('batch', task_list=task_list(root, engine_dir))
        if time.monotonic() - last >= quiet:
            return say('quiet')
        if time.monotonic() - start >= timeout:
            # A timeout while things still move is not a quiet spell: no line for the person.
            return say('again')
        time.sleep(interval)
