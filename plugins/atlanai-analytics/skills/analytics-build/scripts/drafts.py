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


def _record(engine):
    """What this build's waits have reported. A rebuild in a new engine folder (a dashboard added after the build) starts
    afresh: the first build's step ends, kept, hid every step end of the second."""
    record = engine.json(engine.root / REPORTED)
    return record if record.get('engine') in (None, engine.dir.name) else {}


def _save_record(engine, record):
    try:
        path = engine.root / REPORTED
        path.write_text(json.dumps(dict(record, engine=engine.dir.name)))
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


def _live(engine, under, with_left=True):
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
    left = _left(engine, gate, done, total) if with_left else None
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


def _to_start(engine):
    """The step whose prompt is written and that nothing has answered yet, as {gate, prompt}: the session starts its helper.
    Told only "a step ended", sessions went back to waiting without starting the next step's helper, twice in one run."""
    under = _under_way(engine)
    if not under or under[3]:
        return None
    out = {'gate': under[0], 'prompt': str(engine.dir / ('%s_prompt.json' % under[0]))}
    if under[0] == 'x3':
        # The definitions are shared out first: one brief per helper, started all at once.
        out['first'] = 'PROJECT split --engine-dir %s' % engine.dir.name
    return out


# The step under way, as the line after a quiet spell names it.
DOING = {'draft': 'Building your context', 'merge': 'Merging duplicate metrics', 'write': 'Adding detailed information'}


def quiet_line(engine):
    """The one line a quiet spell may add, composed here from the same count the task list shows, so the session never
    writes its own ("Run split now." was a session's own note): the step under way and how far it has got. None between
    steps, when there is nothing under way to name."""
    under = _under_way(engine)
    if not under:
        return None
    doing = DOING.get(ROW_OF.get(under[0]))
    if not doing:
        return None
    if under[0] == 'x2':
        return doing + '.'
    # The count only: a time left grows while nothing lands, so the same count would come back with a worse estimate.
    # Nothing done yet is nothing to report ("finding metrics: 0 metrics in 0 of 2 questions").
    if not under[3]:
        return None
    live = _live(engine, under, with_left=False)
    return '%s%s %s.' % (doing, ',' if ':' in live else ':', live) if live else doing + '.'


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
            "if its gate result asks for one, in this same turn. When `start` names a step, its prompt is written and "
            "nothing answers it yet: start that step's subagent on `start.prompt` in that same message unless one is "
            "working on it (when `start.first` is given, run it first "
            "and start one subagent per share it gives), never just wait. "
            "Only a step that no chip names gets a start line, the one its flow's file gives. Then run `PROJECT drafts "
            "--wait` again in that same message, whether or not a step started: never end the turn while the build runs.",
    'due': "Add no words of your own: something is ready to show, and no step has ended. Run `visual next` with the "
           "flags of every `visual` call now and do what it says, then run `PROJECT drafts --wait` again.",
    'ready': "Add no words of your own: every share of the definitions is back. Run `ENGINE advance` once, send each "
             "refused card to the subagent whose share holds its task number, then run `PROJECT drafts --wait` again.",
    'quiet': "Nothing new has reached the screen for about 3 minutes. Check that each subagent is working; when `start` "
             "names a step, nothing has saved an answer to it: start its subagent on `start.prompt` now unless one is "
             "working on it. When `say` "
             "is given, write it word for word as your only words; when it is null, write nothing. Then run "
             "`PROJECT drafts --wait` again.",
    'done': "Add no words of your own: export the build into the run and run `PROJECT check` for the structural pass, "
            "as build and check says, then draw the build's card, `PROJECT visual build --widget` where you have "
            "`show_widget`, before any question: it closes the build.",
    'again': "Add no words of your own: the wait timed out while new things were showing. Run `PROJECT drafts --wait` again.",
}


# A step's end and a moment due, when the event carries what the run reached in `show_first`.
SHOWN = {
    'step': "Show `show_first` first, before any words or call, as its `side view` lines say: the chip names the step "
            "that starts, so add no words of your own. When `start` names a step, its prompt is written and nothing "
            "answers it yet: start that step's subagent on `start.prompt` in that same message unless one is working on "
            "it (when `start.first` is given, run it first and start one subagent per share it gives). Then run `PROJECT "
            "drafts --wait` again in that same message: never end the turn while the build runs.",
    'due': "Show `show_first` first, before any words or call, as its `side view` lines say, then run `PROJECT drafts "
           "--wait` again in that same message.",
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
    # A refusal sends cards back for repair: until the files that hold them are written again, the shares are not back.
    # A live run was told "every share is back" for the files it had just advanced on, and ended its turn mid-build.
    repair = engine.dir / 'x3_repair.json'
    if repair.is_file():
        since = repair.stat().st_mtime_ns
        names = {str(t.get('name', '')).strip().lower() for t in engine.json(repair).get('tasks') or [] if isinstance(t, dict)}
        refused = [f for f in under[3] if names & {str(card.get('name', '')).strip().lower()
                                                   for card in engine.rows(f, 'metrics') if isinstance(card, dict)}]
        if any(f.stat().st_mtime_ns <= since for f in refused) or all(f.stat().st_mtime_ns <= since for f in under[3]):
            return None
    return [len(under[3]), max(f.stat().st_mtime_ns for f in under[3])]


def wait(root, engine_dir=None, timeout=540, quiet=180, interval=2.0, out=None, batch_every=BATCH_SECONDS):
    """Wait in the foreground until the build has something to act on, print it as one JSON line and return, so the
    session acts on it and waits again in the same turn: a turn that ends without words is sent back for them, while a
    turn that keeps going is not. It reports a step's end once, every share of the definitions back once per set of
    files, the task list at most every `batch_every` seconds where the session keeps one, a quiet spell after `quiet`
    seconds with nothing new, counted across calls, and `again` at `timeout`, which a host keeps under its own limit for
    a foreground command."""
    import sys
    import time
    out = out or sys.stdout
    engine = _Engine(root, engine_dir)
    start = time.monotonic()

    def say(event, **extra):
        # Anything shown starts the quiet spell afresh; a timeout shows nothing, so the spell runs on into the next call.
        if event != 'again':
            _save_record(engine, dict(_record(engine), quiet_from=time.time()))
        # Every result carries the list as the run's files now have it, where the session keeps one: the side page reads
        # those files live, and a list moved only by some events sat at "3 of 4" beside a page showing all 4 defined.
        nxt = WAITED[event]
        if event in SHOWN:
            # The moment comes whole with the event: told to run `visual next`, the dry run's session never did.
            import side_view
            try:
                shown = side_view.show_due(root, engine_dir)
            except Exception:  # noqa: BLE001 - the event still goes out; the moment stays due for the next one
                shown = ''
            if shown:
                extra['show_first'], nxt = shown, SHOWN[event]
        if 'task_list' not in extra:
            import visuals
            if visuals.keeps_tasks(root):
                try:
                    extra['task_list'] = task_list(root, engine_dir)
                    nxt += ' Set your task list to `task_list` in the same message.'
                except Exception:  # noqa: BLE001 - the event still goes out; the list catches up at the next one
                    pass
        print(json.dumps({'event': event, **extra, 'next': nxt}, ensure_ascii=False), file=out, flush=True)

    while True:
        import side_view
        try:
            side_view.mark_waiting(root)
        except OSError:  # the wait goes on; the engine may then hand a chip over itself
            pass
        record = _record(engine)
        done = set(record.get('steps') if isinstance(record.get('steps'), list) else [])
        stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
        accepted = {s.get('stage') for s in stages if isinstance(s, dict) and s.get('verdict') == 'ok'}
        for stage in WAKES:
            if stage in accepted and stage not in done:
                # A step named to start is named once: three step ends between waits each named the same next step, so
                # a session started three helpers for it. A quiet spell names it again, with its guard.
                begin = _to_start(engine)
                started = set(record.get('started') if isinstance(record.get('started'), list) else [])
                if begin and begin['gate'] in started:
                    begin = None
                # A step's end starts the quiet line's memory afresh: a step run again may say its count again.
                _save_record(engine, dict(record, steps=sorted(done | {stage}), said=None,
                                          started=sorted(started | ({begin['gate']} if begin else set()))))
                return say('step', start=begin)
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
        # Quiet is measured across calls: a host whose foreground limit is shorter than the spell timed out on every
        # call, so a stalled helper was never noticed and the next step's helper never named.
        now = sum(len(_saved(engine, gate)) for gate, _, _ in GATES)
        if now != record.get('seen') or not isinstance(record.get('quiet_from'), (int, float)):
            record = dict(record, seen=now, quiet_from=time.time())
            _save_record(engine, record)
        import visuals
        moved = record.get('waited_at') if isinstance(record.get('waited_at'), (int, float)) else 0
        # A new batch moves the list; a step that has only started (its prompt written, nothing saved) does not.
        under = _under_way(engine)
        if (now != record.get('waited') and time.time() - moved >= batch_every and under and under[3]
                and visuals.keeps_tasks(root)):
            _save_record(engine, dict(record, waited=now, waited_at=time.time()))
            return say('batch', task_list=task_list(root, engine_dir))
        if time.time() - record['quiet_from'] >= quiet:
            # Where a live task list and side page show the count, a printed line only repeats them and then goes stale
            # in the chat as they move on: the line is for a host with no task list, whose chat is the only progress.
            import visuals
            line = None if visuals.keeps_tasks(root) else quiet_line(engine)
            if line and line == record.get('said'):
                line = None
            if line:
                _save_record(engine, dict(record, said=line))
            return say('quiet', say=line, start=_to_start(engine))
        if time.monotonic() - start >= timeout:
            # A timeout while things still move is not a quiet spell: no line for the person.
            return say('again')
        time.sleep(interval)
