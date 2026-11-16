"""The progress line for the batches the build saved since the last report.

The describe, propose, merge and card gates save their answers as numbered batch files. This
reads the furthest gate that has any and writes one plain line for the chat: the queries
described, the metrics drafted, the drafts a merge combined, or the definitions written, with
real counts. It reads only the run's own files and records which batches it reported; the
engine still validates every file at its gate.
"""
import json
from pathlib import Path

from context_state import ordinary

MAX_BYTES = 4_000_000
MAX_FILES = 200
LISTED = 40        # metrics listed in one block before "and N more"
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


def follow(root, engine_dir=None, seconds=1800, idle=1200, interval=2.0, out=None):
    """Print one line per newly saved batch, as it lands, for the session that talks to the
    person while a subagent writes the gates. Stops when the card gate is accepted, after
    `idle` seconds with no new batch, or after `seconds`; run it again to carry on. It shares
    the record with `latest`, so a restart neither repeats nor skips a batch."""
    import sys
    import time
    out = out or sys.stdout
    engine = _Engine(root, engine_dir)
    start = last = time.monotonic()
    printed = 0
    while time.monotonic() - start < seconds and time.monotonic() - last < idle:
        record = engine.json(engine.root / REPORTED)
        for gate, step, describe in reversed(GATES):  # describe, propose, merge, cards: build order
            files, targets, stamp = _unreported(engine, record, gate)
            if targets:
                print(describe(engine, files, targets)[0], file=out, flush=True)
                record[gate] = stamp
                _save_record(engine, record)
                printed += 1
                last = time.monotonic()
        stages = engine.json(engine.dir / 'manifest.json').get('stages') or []
        if any(isinstance(s, dict) and s.get('stage') == 'x3_ingest' and s.get('verdict') == 'ok' for s in stages):
            break
        time.sleep(interval)
    return f"Stopped watching after {_plural(printed, 'line')}." if printed else 'No new batches were saved.'
