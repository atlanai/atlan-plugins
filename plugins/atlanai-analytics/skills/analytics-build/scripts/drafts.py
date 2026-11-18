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
    counts = {'queries_read': read, 'queries_total': total, 'drafts': drafted}
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
NEWS = {'x3': ('Newest rule: ', ''), 'x2': ('Newest metric: ', 'Newest metrics: '),
        'x1': ('Newest draft metric: ', 'Newest draft metrics: ')}


def _news(engine):
    """The words a so-far turn ends on: the desktop app sends a turn that ends on a widget alone back for words, so they
    add to the chip, which leads with the newest thing by name: the question a described query answers, the newest
    names, a rule's first sentence. Never that the step is still running."""
    import re
    from run_facts import sentence
    under = _under_way(engine)
    items = [[sentence(n), d] for n, d in _items(engine, under[0], under[3])] if under else []
    if not items:
        return ''
    gate, (name, said) = under[0], items[-1]
    items = items[_shown(engine, gate):] or items
    if gate == 'm2':
        line = '%s answers: %s' % (name, said) if said else '%s is in plain words.' % name
    elif gate == 'x3':
        # A sentence ends where the next begins with a capital, as the chip's own: "Fixit & Co. jobs" is one sentence.
        first = re.split(r'(?<=[.!?])\s+(?=[A-Z])', str(said or '').strip(), maxsplit=1)[0]
        line = NEWS[gate][0] + name.rstrip('.') + '.' + (' ' + first if first else '')
    else:
        names = [n.rstrip('.') for n, _ in items[::-1][:3]]
        line = NEWS[gate][len(names) > 1] + (', '.join(names[:-1]) + ' and ' + names[-1] if len(names) > 1 else names[0]) + '.'
    return line if len(line) <= 240 else line[:237].rsplit(' ', 1)[0] + '...'


def news(root, engine_dir=None):
    """The words a so-far chip's turn ends on, from the same saved batches as the chip, read at the same moment: a line
    the watch wrote before the chip was drawn named an older rule than the chip did."""
    return _news(_Engine(root, engine_dir))


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
