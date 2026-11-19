"""The progress line for the batches the build saved since the last report.

The describe, propose, merge and card gates save their answers as numbered batch files. This
reads the furthest gate that has any and writes one plain line: the queries described, the
metrics drafted, the drafts a merge combined, or the definitions written, with real counts; the
side page counts from the same files. `follow` wakes the chat when a step it shows ends, and every
few minutes while batches land, for the step's so-far chip. It reads only the run's own files and
records what it reported; the engine still validates every file at its gate.
"""
import json
from pathlib import Path

from context_state import ordinary

MAX_BYTES = 4_000_000
MAX_FILES = 200
LISTED = 8         # names in one line before "and N more": a short line is posted as printed
NAME_CHARS = 80


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    clean = ' '.join(value.split())
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + '…'


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


def _names(names):
    """Names once each, in order, as one comma list."""
    first = {}
    for n in names:
        if n:
            first.setdefault(n.lower(), n)
    unique = list(first.values())
    shown = ', '.join(unique[:LISTED])
    return shown + (f', and {len(unique) - LISTED} more' if len(unique) > LISTED else ''), len(unique)


def _plural(n, word):
    return f'{n} {word}' + ('' if n == 1 else 's')


def _proposal_names(engine):
    """Every draft's name by proposal id, numbered as the engine numbers them: `<query>#<n>`."""
    names = {}
    for path in engine.answers('x1'):
        for row in engine.rows(path, 'statements'):
            metrics = row.get('metrics') if isinstance(row.get('metrics'), list) else []
            for i, m in enumerate(metrics):
                if isinstance(m, dict) and isinstance(row.get('vq_id'), str):
                    names[f"{row['vq_id']}#{i}"] = _text(m.get('name'), NAME_CHARS)
    for row in engine.rows(engine.dir / 'metric_proposals.json', 'proposals'):
        if isinstance(row.get('proposal_id'), str):
            names.setdefault(row['proposal_id'], _text(row.get('name'), NAME_CHARS))
    return names


def _source_ids(row):
    sources = row.get('sources') if isinstance(row.get('sources'), list) else []
    ids = []
    for s in sources:
        pid = s if isinstance(s, str) else s.get('proposal_id') if isinstance(s, dict) else None
        if isinstance(pid, str):
            ids.append(pid)
    return ids


def _metric_names(engine, path):
    return [_text(m.get('name'), NAME_CHARS) for r in engine.rows(path, 'statements')
            for m in (r.get('metrics') if isinstance(r.get('metrics'), list) else []) if isinstance(m, dict)]


def _propose(engine, files, targets):
    statements = [r for p in files for r in engine.rows(p, 'statements')]
    earlier = {n.lower() for p in files if p not in targets for n in _metric_names(engine, p) if n}
    names = [n for t in targets for n in _metric_names(engine, t) if n]
    read = len({r.get('vq_id') for r in statements if isinstance(r.get('vq_id'), str)})
    total = sum(len(t['queries']) for t in engine.json(engine.dir / 'x1_prompt.json').get('tasks', [])
                if isinstance(t, dict) and isinstance(t.get('queries'), list))
    drafted = sum(len(r['metrics']) for r in statements if isinstance(r.get('metrics'), list))
    # The queries that held a number, as the step's own chip counts them ("found in 25 of those 30 queries").
    held = len({r.get('vq_id') for r in statements if isinstance(r.get('vq_id'), str) and r.get('metrics')})
    counts = {'queries_read': read, 'queries_total': total, 'queries_with': held, 'drafts': drafted}
    progress = f'{read} of {total} queries read' if total else f'{read} queries read'
    if not names:
        return f"No metrics in {'this batch' if len(targets) == 1 else 'these batches'} ({progress}).", counts
    added, n_added = _names([n for n in names if n.lower() not in earlier])
    again, n_again = _names([n for n in names if n.lower() in earlier])
    if not n_added:
        return f'No additional metrics ({progress}). Found again: {again}.', counts
    these = ('This' if n_added == 1 else 'These') + (' additional' if earlier else '')
    line = f"{these} {'metric is' if n_added == 1 else 'metrics are'} drafted: {added} ({progress})."
    return line + (f' Found again: {again}.' if n_again else ''), counts


def _merge(engine, files, targets):
    proposals = _proposal_names(engine)
    claimed = {pid for p in files for r in engine.rows(p, 'metrics') for pid in _source_ids(r)}
    merged = sum(len(engine.rows(p, 'metrics')) for p in files)
    lines, kept = [], []
    for row in [r for t in targets for r in engine.rows(t, 'metrics')]:
        ids = _source_ids(row)
        name = _text(row.get('name'), NAME_CHARS)
        if len(ids) > 1:
            fallback = {s.get('proposal_id'): _text(s.get('name_as_proposed'), NAME_CHARS)
                        for s in row.get('sources') if isinstance(s, dict)}
            counted = {}
            for i in ids:
                source = proposals.get(i) or fallback.get(i) or i
                counted.setdefault(source.lower(), [source, 0])[1] += 1
            if list(counted) == [name.lower()]:
                lines.append(f'{name}: {len(ids)} drafts merged into one')
            else:
                lines.append(' + '.join(f'{n} ×{k}' if k > 1 else n for n, k in counted.values()) + f' → {name}')
        elif ids and name:
            kept.append(name)
    if kept:
        lines.append(f"Kept as their own metric ({len(kept)}): {_names(kept)[0]}.")
    if proposals and len(claimed) >= len(proposals):
        lines.append(f"{_plural(len(proposals), 'draft')} {'is' if len(proposals) == 1 else 'are'} now "
                     f"{_plural(merged, 'metric')}.")
    return ' '.join(l if l.endswith('.') else l + '.' for l in lines) or 'No merged metrics in this batch.', {
        'drafts': len(proposals), 'drafts_merged': len(claimed), 'metrics': merged}


def _define(engine, files, targets):
    names = [_text(c.get('name'), NAME_CHARS) for t in targets for c in engine.rows(t, 'metrics')]
    written = sum(len(engine.rows(p, 'metrics')) for p in files)
    total = engine.json(engine.dir / 'x3_prompt.json').get('n_metrics')
    total = total if type(total) is int and total >= written else None
    counts = {'defined': written, 'total': total}
    shown, n = _names(names)
    if not n:
        return 'No definitions in this batch.', counts
    count = f'{written} of {total}' if total else f'{written} in total'
    these = 'This metric is' if n == 1 else 'These metrics are'
    return f'{these} complete: {shown} ({count}).', counts


def _describe(engine, files, targets):
    names = [_text(q.get('name'), NAME_CHARS) for t in targets for q in engine.rows(t, 'queries')]
    described = len({q.get('vq_id') for p in files for q in engine.rows(p, 'queries') if isinstance(q.get('vq_id'), str)})
    total = sum(len(t['queries']) for t in engine.json(engine.dir / 'm2_prompt.json').get('tasks', [])
                if isinstance(t, dict) and isinstance(t.get('queries'), list))
    counts = {'described': described, 'total': total}
    shown, n = _names(names)
    if not n:
        return 'No queries described in this batch.', counts
    count = f'{described} of {total}' if total else f'{described} in total'
    these = 'This query is' if n == 1 else 'These queries are'
    return f'{these} described: {shown} ({count}).', counts


GATES = (('x3', 'define', _define), ('x2', 'merge', _merge), ('x1', 'propose', _propose), ('m2', 'describe', _describe))


def _saved(engine, gate):
    """A gate's complete answer files, oldest first: a file still being written waits, and
    parallel writers finish out of name order, so save time decides."""
    files = [f for f in engine.answers(gate) if engine.json(f)]
    return sorted(files, key=lambda f: (f.stat().st_mtime_ns, f.name))


def counts(root, gate, engine_dir=None):
    """A gate's counts over every batch it has saved, the numbers its chat lines carry, with nothing recorded:
    the side page counts from the same files as the chat."""
    engine = _Engine(root, engine_dir)
    files = _saved(engine, gate)
    return dict(GATES_BY_ID[gate](engine, files, files)[1]) if files else {}


def _items(engine, gate, files):
    """What a gate's batches hold, in the order they were saved, so a list of them adds up to the step's count: each
    query described with the question it answers, each draft metric once with how many times it was found, each metric
    with a definition's text."""
    first = {}
    if gate == 'x1':
        for name in [n for p in files for n in _metric_names(engine, p) if n]:
            first.setdefault(name.lower(), [name, 0])[1] += 1
        return list(first.values())
    for r in [r for p in files for r in engine.rows(p, 'queries' if gate == 'm2' else 'metrics')]:
        name = _text(r.get('name'), NAME_CHARS)
        key = r.get('vq_id') if gate == 'm2' and isinstance(r.get('vq_id'), str) else name.lower()
        question = r.get('question')
        question = question[0] if isinstance(question, list) and question else question
        if name:
            first.setdefault(key, [name, _text(r.get('description'), 600) if gate == 'x3' else
                                   _text(question, 300) if gate == 'm2' else ''])
    return list(first.values())


def _under_way(engine):
    """The furthest step with saved batches while its gate is not yet accepted, as (gate, step, describe, files); None
    before any batch and once that step has ended, when it has nothing left to show so far."""
    stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
    accepted = {s.get('stage') for s in stages if isinstance(s, dict) and s.get('verdict') == 'ok'}
    for gate, step, describe in GATES:
        files = _saved(engine, gate)
        if files:
            return None if gate + '_ingest' in accepted else (gate, step, describe, files)
    return None


def _step(engine, gate):
    """One step's saved batches, ended or not, as (gate, step, describe, files): a step's own chip is drawn once it has
    ended, with the next step already asked for, so its line reads its own step, not the one under way."""
    for g, step, describe in GATES:
        if g == gate:
            files = _saved(engine, g)
            return (g, step, describe, files) if files else None
    return None


def so_far(root, engine_dir=None):
    """The step under way: its counts over all its batches, as the side page counts them, what they hold so far, and
    how many of those the last so-far chip named. Nothing once it has ended. Nothing is recorded."""
    engine = _Engine(root, engine_dir)
    under = _under_way(engine)
    if not under:
        return {}
    gate, step, describe, files = under
    return {'step': step, **describe(engine, files, files)[1], 'items': _items(engine, gate, files),
            'shown': _shown(engine, gate)}


# How many of a step's items its so-far chips have named, so the next names only what landed since: a person lost the
# 11th to the 17th metric between "10 of 20" and "18 of 20", and a reader met the same definitions in every chip.
SHOWN = 'so-far-shown.json'


def _shown(engine, gate):
    named = engine.json(engine.root / SHOWN).get(gate)
    return named if type(named) is int else 0


def mark_shown(root, engine_dir=None):
    """Record that the step under way's so-far chip has named everything it holds so far."""
    engine = _Engine(root, engine_dir)
    under = _under_way(engine)
    if not under:
        return
    record = engine.json(engine.root / SHOWN)
    record[under[0]] = len(_items(engine, under[0], under[3]))
    try:
        path = engine.root / SHOWN
        path.write_text(json.dumps(record))
        path.chmod(0o600)
    except OSError:
        pass  # without the record the next chip names some items again; it never skips one


GATES_BY_ID = {gate: describe for gate, _, describe in GATES}
REPORTED = 'drafts-reported.json'


def _save_record(engine, record):
    try:
        path = engine.root / REPORTED
        path.write_text(json.dumps(record))
        path.chmod(0o600)
    except OSError:
        pass  # without the record the next call repeats a line; it never skips one


def _unreported(engine, record, gate):
    """The gate's saved batches not yet reported, and the stamps that mark them reported."""
    files = _saved(engine, gate)
    done = record.get(gate) if isinstance(record.get(gate), dict) else {}
    stamp = {f.name: f.stat().st_mtime_ns for f in files}
    return files, [f for f in files if done.get(f.name) != stamp[f.name]], stamp


def latest(root, engine_dir=None):
    """One line for every batch saved since the last call, of the furthest gate that has any.

    A batch saved by the same command as another, or rewritten after a refusal, is never
    skipped: each call covers all the batches the previous one did not. With nothing new, the
    newest batch's line is repeated. The only file written is the record of what was reported."""
    engine = _Engine(root, engine_dir)
    record = engine.json(engine.root / REPORTED)
    for gate, step, describe in GATES:
        files, targets, stamp = _unreported(engine, record, gate)
        if not files:
            continue
        line, counts = describe(engine, files, targets or files[-1:])
        record[gate] = stamp
        _save_record(engine, record)
        return {'status': 'ok', 'step': step, 'batches': [t.name for t in targets], 'line': line, 'counts': counts}
    return {'status': 'ok', 'step': None, 'line': '', 'counts': {}}


# The steps whose end the chat shows as a chip: queries described, drafts merged, metrics defined. Their batches show on
# the side page as they land, never in the chat.
WAKES = ('m2_ingest', 'x1_ingest', 'x2_ingest', 'x3_ingest')
DUE = ("Due: run this helper's `visual next` before going on, with the flags of every `visual` call (`--data` for a demo, "
       "`--widget` where you have `show_widget`).")
# A step can run for many minutes: while its batches land, the chat hears how far it has got this often.
SO_FAR_SECONDS = 60
SO_FAR = ("Due: run this helper's `visual progress` before going on, with the flags of every `visual` call (`--data` for a "
          "demo, `--widget` where you have `show_widget`)")
NEWS = {'x3': ('Newest definition: ',), 'x2': ('Newest metric: ',), 'x1': ('Newest possible metric: ',)}


def _news(engine, gate=None):
    """The words a so-far turn ends on: the desktop app sends a turn that ends on a widget alone back for words, so they
    add to the chip, which names what is new: the question the newest described query answers, or the newest draft's,
    metric's or rule's meaning in one sentence. Never that the step is still running."""
    import re
    from run_facts import sentence
    under = _step(engine, gate) if gate else _under_way(engine)
    items = [[sentence(n), d] for n, d in _items(engine, under[0], under[3])] if under else []
    if not items:
        return ''
    gate, (name, said) = under[0], items[-1]
    if gate == 'm2':
        line = '%s answers: %s' % (name, said) if said else '%s is in plain words.' % name
    else:
        # The chip names what is new; the line gives the newest one's meaning, its definition's first sentence.
        if gate != 'x3':
            rows = [m for f in under[3] for m in (
                [m for r in engine.rows(f, 'statements') for m in (r.get('metrics') if isinstance(r.get('metrics'), list) else [])]
                if gate == 'x1' else engine.rows(f, 'metrics')) if isinstance(m, dict)]
            said = next((m.get('definition') for m in rows[::-1] if sentence(_text(m.get('name'), NAME_CHARS)) == name
                         and isinstance(m.get('definition'), str)), '')
        first = re.split(r'(?<=[.!?])\s+(?=[A-Z])', str(said or '').strip(), maxsplit=1)[0]
        # The gist, without the engine's asides: the desktop run's line read "(task type matched ignoring case and
        # trailing spaces)". A bracketed aside goes, and the sentence ends where its detail starts.
        first = re.split(r'[:;]\s', re.sub(r'\s*\([^()]*\)', '', first), maxsplit=1)[0].rstrip(' .,') if first else ''
        first = first + '.' if first else ''
        line = NEWS[gate][0] + name.rstrip('.') + '.' + (' ' + first if first else '')
    return line if len(line) <= 240 else line[:237].rsplit(' ', 1)[0] + '...'


def news(root, engine_dir=None, gate=None):
    """The words a so-far chip's turn ends on, from the same saved batches as the chip, read at the same moment: a line
    the watch wrote before the chip was drawn named an older rule than the chip did."""
    return _news(_Engine(root, engine_dir), gate)


# The desktop app's task list carries the build once the side page is open (the team, 2026-11-19): the step under way counts
# its batches, from the same files the side page counts, with time left from the step's own pace.
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
    c = describe(engine, files, files[-1:])[1]
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
        out.append({'content': content, 'status': r['status']})
    return out


WAITED = {
    'batch': "Set your task list to `task_list`, each step's text and status as given, in the same message as your next "
             "`PROJECT drafts --wait`, so the turn goes on. Write nothing in the chat: the side page shows each batch.",
    'step': "A step ended. Start the next step's subagent if its gate result asks for one, then run `visual next` with the "
            "flags of every `visual` call and do what it says, in this same turn. Write nothing in the chat, not even the "
            "next step's start: the task list names it.",
    'quiet': "Nothing new for a while. If every helper's share of the definitions is back, run `ENGINE advance` once. Then "
             "run `PROJECT drafts --wait` again. Write nothing in the chat.",
    'done': "The build is written. Export it into the run and run `PROJECT check` for the structural pass, as build and "
            "check says, then draw the build's card, the chat's first word since the side page opened, before any question.",
}


def wait(root, engine_dir=None, timeout=540, quiet=240, interval=2.0, out=None):
    """For a host whose task list carries the build once the side page is open: wait in the foreground until the build has
    something to report, print it as one JSON line and return, so the session acts on it and waits again in the same
    turn. A turn that ends on a task-list update alone is sent back for words; a turn that goes on is not. It reports a
    batch once (with the task list), a step's end once (shared with `follow`), and a quiet spell after `quiet` seconds
    with nothing new or at `timeout`, which stays under a foreground command's ten-minute limit."""
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
            return say('step')
        now = sum(len(_saved(engine, gate)) for gate, _, _ in GATES)
        if now != seen_now:
            seen_now, last = now, time.monotonic()
        import visuals
        if now != record.get('waited') and _under_way(engine) and visuals.keeps_tasks(root):
            _save_record(engine, dict(record, waited=now))
            return say('batch', task_list=task_list(root, engine_dir))
        if time.monotonic() - start >= timeout or time.monotonic() - last >= quiet:
            return say('quiet')
        time.sleep(interval)


def follow(root, engine_dir=None, seconds=1800, idle=1200, interval=2.0, so_far_every=SO_FAR_SECONDS, out=None, every_chip=False):
    """Print one line each time a step the chat shows ends, for the session that talks to the person while a subagent
    writes the gates: the line names the chip to draw, and nothing else reaches the chat. While a step runs, a line
    names the so-far chip once `so_far_every` seconds have passed since the last line and a batch has landed since. Stops
    once the definitions are accepted, after `idle` seconds with no batch or step, or after `seconds`; run it again to
    carry on. The record of reported steps is shared, so a restart neither repeats nor skips one. With `every_chip`, for
    a flow whose helpers advance every step, it also names each chip the run can show and has not, once."""
    import sys
    import time
    out = out or sys.stdout
    engine = _Engine(root, engine_dir)
    start = last = spoke = time.monotonic()
    printed, saved, told, chips = 0, None, None, set()
    while time.monotonic() - start < seconds and time.monotonic() - last < idle:
        record = engine.json(engine.root / REPORTED)
        done = set(record.get('steps') if isinstance(record.get('steps'), list) else [])
        stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
        accepted = {s.get('stage') for s in stages if isinstance(s, dict) and s.get('verdict') == 'ok'}
        now = sum(len(_saved(engine, gate)) for gate, _, _ in GATES)
        if now != saved:  # a batch landing is the build working: it keeps the watch going without a line
            saved, last = now, time.monotonic()
        told = now if told is None else told
        for stage in WAKES:
            if stage in accepted and stage not in done:
                print(DUE, file=out, flush=True)
                done.add(stage)
                _save_record(engine, dict(record, steps=sorted(done)))
                printed += 1
                last = spoke = time.monotonic()
                told = now
        if now > told and time.monotonic() - spoke >= so_far_every and _under_way(engine):
            print(SO_FAR + ', then end the turn on the line it gives, unchanged.', file=out, flush=True)
            printed += 1
            spoke, told = time.monotonic(), now
        if every_chip:
            # Helpers advance every step in the dashboard flow, so no step's result reaches the chat to name its chip:
            # the watch names each chip the run can show and has not, once.
            import side_view
            try:
                waiting = [k for k in side_view.due(root, engine_dir) if k not in chips]
            except (OSError, ValueError, KeyError, TypeError):
                waiting = []
            if waiting:
                print(DUE, file=out, flush=True)
                chips.update(waiting)
                printed += 1
                last = spoke = time.monotonic()
        if 'x3_ingest' in accepted:
            break
        time.sleep(interval)
    return f"Stopped watching after {_plural(printed, 'line')}." if printed else 'No step finished while watching.'
