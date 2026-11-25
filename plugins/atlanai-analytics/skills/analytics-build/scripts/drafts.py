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
    # Counted against the prompt: answers carried from an earlier run are not this step's work.
    asked = _prompt_ids(engine, 'x1')
    statements = [r for p in files for r in engine.rows(p, 'statements') if not asked or r.get('vq_id') in asked]
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
    asked = _prompt_ids(engine, 'm2')
    described = len({q.get('vq_id') for p in files for q in engine.rows(p, 'queries') if isinstance(q.get('vq_id'), str)
                     and (not asked or q.get('vq_id') in asked)})
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
    order = [s.get('stage') for s in stages if isinstance(s, dict)]
    # The descriptions written beside the metric steps are not the step under way: "Find metrics · 5 of 19 queries in
    # plain words" read as the old order (walk 4, 2026-11-23).
    late = 'x1_emit' in order and 'm2_emit' in order[:order.index('x1_emit')]
    for gate, step, describe in GATES:
        if late and gate == 'm2':
            continue
        files = _saved(engine, gate)
        if files:
            return None if gate + '_ingest' in accepted else (gate, step, describe, files)
    return None


REPORTED = 'drafts-reported.json'


def _prompt_ids(engine, gate):
    """The statements a per-statement gate asks about: its prompt's, not the carried ones."""
    return {q.get('vq_id') for t in engine.json(engine.dir / f'{gate}_prompt.json').get('tasks', [])
            if isinstance(t, dict) and isinstance(t.get('queries'), list)
            for q in t['queries'] if isinstance(q, dict) and isinstance(q.get('vq_id'), str)}


def _answered(engine):
    """The gate whose prompt is out, whose answers now cover all of it, and whose ingest has not run: the build waits
    only for `ENGINE advance`. A walkthrough waited seven minutes for a wait to time out after the describe step's
    answers were all saved (2026-11-23). The definitions have their own wake (`ready`); the links one file."""
    stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
    ran = {s.get('stage'): s.get('verdict') for s in stages if isinstance(s, dict)}
    for gate, key in (('m2', 'queries'), ('x1', 'statements'), ('x2', None), ('x4', None)):
        if ran.get(gate + '_emit') is None or ran.get(gate + '_ingest') == 'ok':
            continue
        # The descriptions are written beside the metric steps and taken in after the links: done early, they wait.
        if gate == 'm2' and 'x1_emit' in ran and ran.get('x4_ingest') != 'ok':
            continue
        files = _saved(engine, gate)
        if not files or all(f.name.startswith('carried-') for f in files):
            continue
        if key:
            asked = _prompt_ids(engine, gate)
            said = {r.get('vq_id') for f in files for r in engine.rows(f, key)}
            if asked and asked <= said:
                return gate
        elif gate == 'x2':
            asked = engine.json(engine.dir / 'x2_prompt.json').get('n_proposals')
            claimed = {pid for f in files for r in engine.rows(f, 'metrics') for pid in _source_ids(r)}
            if type(asked) is int and asked and len(claimed) >= asked:
                return gate
        else:
            return gate
    return None


def _load_record(engine):
    """What this build's waits already reported. One record per engine folder: a second build in the same run (its past
    queries added) runs every step again, and a run-wide record hid its step ends and its wakes (2026-11-23)."""
    whole = engine.json(engine.root / REPORTED)
    if 'steps' in whole or 'told' in whole or 'answered' in whole:   # a record from before builds were kept apart
        whole = {'engine': whole}
    mine = whole.get(engine.dir.name)
    return mine if isinstance(mine, dict) else {}


def _save_record(engine, record):
    try:
        path = engine.root / REPORTED
        whole = engine.json(path)
        if 'steps' in whole or 'told' in whole or 'answered' in whole:
            whole = {'engine': whole}
        whole[engine.dir.name] = record
        path.write_text(json.dumps(whole))
        path.chmod(0o600)
    except OSError:
        pass  # without the record the next wait repeats an event; it never skips one


# The steps whose end wakes the session: the tile review and the tile questions (a helper may write them), queries
# described, drafts found, drafts merged, metrics defined.
WAKES = ('c3_ingest', 'm2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest')
# The tile review is written by this session, which advances it itself: its end has nothing to show (walk 4: `step`
# with nothing to show after every advance). The tile questions wake only where a helper still writes them.
# The task list moves at most this often: an update for every batch woke the session 24 times in eight minutes.
BATCH_SECONDS = 60


# Where the session keeps a task list, it carries the build: the step under way counts its batches, from the same files
# the side page counts, with time left from the step's own pace.
ROW_OF = {'m2': 'draft', 'x1': 'draft', 'x2': 'merge', 'x3': 'write'}
SHORT = {'draft': 'Find metrics', 'merge': 'Combine duplicates'}


def _left(files, done, total):
    """Time left from the step's own pace: how long its batches have taken per item so far, for the items still to come.
    None until two batches have landed, when the pace means something."""
    if not total or len(files) < 2:
        return None
    if done >= total:
        return 'nearly done'
    stamps = [f.stat().st_mtime for f in files]
    per_batch, per_items = (stamps[-1] - stamps[0]) / (len(files) - 1), done / len(files)
    seconds = per_batch * (total - done) / per_items if per_items else 0
    return 'nearly done' if seconds < 45 else 'about %d min left' % max(1, round(seconds / 60))


def _live(engine, under):
    """The step under way as its task-list row says it: what has landed so far, and time left when the pace is known."""
    gate, _, describe, files = under
    c = describe(engine, files)
    if gate == 'm2':
        done, total = c.get('described') or 0, c.get('total')
        said = '%d of %d queries in plain words' % (done, total) if total else '%d queries in plain words' % done
    elif gate == 'x1':
        done, total = c.get('queries_read') or 0, c.get('queries_total')
        said = '%s so far, %s' % (_plural(c.get('drafts') or 0, 'possible metric'),
                                  '%d of %d queries read' % (done, total) if total else '%d queries read' % done)
    elif gate == 'x2':
        # How many metrics the drafts make is only known once they are all combined.
        done, total, said = 0, None, '%s so far' % _plural(c.get('metrics') or 0, 'metric')
    else:
        done, total = c.get('defined') or 0, c.get('total')
        said = '%d of %d defined' % (done, total) if total else '%d defined' % done
    left = _left(files, done, total)
    return said + (' (%s)' % left if left else '')


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
            content = '%s · %s' % (SHORT.get(r['id'], content.split(' (about')[0]), _live(engine, under))
        elif under and r['status'] == 'in_progress' and r.get('id') == 'history':
            # The past queries' build runs every step again under one item: it counts as it goes (a walkthrough's
            # list sat on "Add past queries" for 14 minutes with no count, 2026-11-23).
            content = 'Add past queries · ' + _live(engine, under)
        out.append({'content': content, 'status': r['status']})
    return out


WAITED = {
    'batch': "Add no words of your own, nor the list's rows: the person sees the list, and the side page shows each "
             "batch. Set your task list to `task_list`, each step's "
             "text and status as given (load the task-list tool first if it is deferred), in the same message as your "
             "next `PROJECT drafts --wait`, so the turn goes on.",
    'step': "Add no words of your own, not even the next step's start: run `visual next` with the flags of every `visual` "
            "call now (it is quick) and show what it gives in the same message that starts the next step's subagent, "
            "if its gate result asks for one, in this same turn.",
    'due': "Add no words of your own: something is ready to show, and no step has ended. Run `visual next` with the "
           "flags of every `visual` call now and do what it says, then run `PROJECT drafts --wait` again.",
    'ready': "Add no words of your own: every share of the definitions is back. Run `ENGINE advance` once, send each "
             "refused card to the subagent whose share holds its task number, then run `PROJECT drafts --wait` again.",
    'answered': "Add no words of your own: every answer this step asked for is saved, and the build waits on nobody "
                "but you. Run `ENGINE advance` once now, without waiting for the step's subagent to report, then run "
                "`PROJECT drafts --wait` again.",
    'quiet': "Add no words of your own: nothing new for a while. Check that each subagent is working, then run "
             "`PROJECT drafts --wait` again.",
    'done': "Add no words of your own until the build's card: export the build into the run and run `PROJECT check` for "
            "the structural pass, as build and check says, then draw the build's card before any question.",
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
        record = _load_record(engine)
        done = set(record.get('steps') if isinstance(record.get('steps'), list) else [])
        stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
        accepted = {s.get('stage') for s in stages if isinstance(s, dict) and s.get('verdict') == 'ok'}
        named_order = [s.get('stage') for s in stages if isinstance(s, dict)]
        # With the descriptions written last, their step ends just before the build does: `done` says it.
        late = 'm2_emit' in named_order[:named_order.index('x1_emit')] if 'x1_emit' in named_order else False
        for stage in WAKES:
            if late and stage == 'm2_ingest':
                continue
            if stage in accepted and stage not in done:
                _save_record(engine, dict(record, steps=sorted(done | {stage})))
                return say('step')
        # The links step comes after the definitions: the build is done once it has a verdict, kept or skipped.
        # With the descriptions written last (the engine's default), the build ends after they are taken in and the
        # cards refreshed from them: a walkthrough was told "done" while the descriptions were still refused (2026-11-23).
        named = {s.get('stage'): s.get('verdict') for s in stages if isinstance(s, dict)}
        prose_in = 'm2_emit' not in named or 'm2_ingest' in accepted
        refreshed = 'x3_refresh' not in named or bool(named.get('x4_refresh'))
        if ('x3_ingest' in accepted and named.get('x4_ingest') and prose_in and refreshed):
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
        whole = _answered(engine)
        if whole and whole not in (record.get('answered') or []):
            _save_record(engine, dict(record, answered=sorted(set(record.get('answered') or []) | {whole})))
            return say('answered', gate=whole)
        back = _all_back(engine)
        if back and back != record.get('ready'):
            _save_record(engine, dict(record, ready=back))
            return say('ready')
        now = sum(len(_saved(engine, gate)) for gate, _, _ in GATES)
        if now != seen_now:
            seen_now, last = now, time.monotonic()
        import visuals
        moved = record.get('waited_at') if isinstance(record.get('waited_at'), (int, float)) else 0
        if (now != record.get('waited') and time.time() - moved >= batch_every and _under_way(engine)
                and visuals.keeps_tasks(root)):
            _save_record(engine, dict(record, waited=now, waited_at=time.time()))
            return say('batch', task_list=task_list(root, engine_dir))
        if time.monotonic() - start >= timeout or time.monotonic() - last >= quiet:
            return say('quiet')
        time.sleep(interval)
