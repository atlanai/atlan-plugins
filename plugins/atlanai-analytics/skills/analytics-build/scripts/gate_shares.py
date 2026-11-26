"""The two per-statement steps (describing each query, finding the metrics in it) shared among helpers.

Each statement is judged on its own, so the shares need no roster. Measured on one dashboard with its past queries
(2026-11-23): one helper took 12 minutes over 161 statements; three helpers of about 60 took 4 to 5 minutes. So one
helper takes up to 60 statements; above that, equal shares of about 60, at most four at once. Each share is a file of
statement ids; its helper saves its batches as `p<k>-01.json`, `p<k>-02.json`, …, so shares never overwrite each other.
"""
import json
import math
from pathlib import Path

from context_state import ordinary

ONE_HELPER_UP_TO = 60
AT_ONCE = 4
GATES = {'m2': 'describe-verified-sql', 'x1': 'propose-metrics'}


class SharesError(ValueError):
    pass


def sizes(n):
    helpers = 1 if n <= ONE_HELPER_UP_TO else min(AT_ONCE, math.ceil(n / ONE_HELPER_UP_TO))
    size, extra = divmod(n, helpers)
    return [size + (1 if k < extra else 0) for k in range(helpers)]


def split(root, engine_dir, gate):
    if gate not in GATES:
        raise SharesError('Share the describe step (m2) or the find-metrics step (x1); the other steps have their own.')
    root = ordinary(root)
    engine = Path(engine_dir or 'engine')
    engine = ordinary(engine if engine.is_absolute() else root / engine, root)
    prompt = engine / f'{gate}_prompt.json'
    if not prompt.is_file():
        raise SharesError(f'The build has not asked for this step yet: {prompt.name} is missing. Advance the build first.')
    tasks = json.loads(prompt.read_text()).get('tasks') or []
    queries = [q for t in tasks if isinstance(t, dict) for q in (t.get('queries') or [])
               if isinstance(q, dict) and isinstance(q.get('vq_id'), str)]
    # Like with like: statements over the same tables with the same opening go to one helper, so two that would earn
    # the same name are named by one author who sees both (three shares wrote "Average AI Readiness Score", 2026-11-23).
    queries.sort(key=lambda q: (sorted(str(t).upper() for t in q.get('tables') or []),
                                ' '.join(str(q.get('sql') or '').split()).upper()[:160]))
    ids = [q['vq_id'] for q in queries]
    folder = engine / 'shares'
    folder.mkdir(exist_ok=True, mode=0o700)
    # The names already given, by answers carried from an earlier build: a new statement never repeats one.
    taken = sorted({str(r.get('name')).strip() for f in (engine / 'answers' / gate).glob('carried-*.json')
                    for r in (json.loads(f.read_text()).get('queries') or []) if isinstance(r, dict) and r.get('name')})
    taken_file = None
    if gate == 'm2' and taken:
        taken_file = folder / 'm2-taken-names.txt'
        taken_file.write_text('\n'.join(taken) + '\n')
    out, at = [], 0
    for k, size in enumerate(sizes(len(ids)) if ids else [], start=1):
        part = ids[at:at + size]
        at += size
        path = folder / f'{gate}-{k}.txt'
        path.write_text('\n'.join(part) + '\n')
        out.append({'share': k, 'statements': len(part), 'ids': str(path), 'answers_as': f'p{k}-NN.json'})
    names = (' Names are unique across the whole build, counting case and punctuation as the same: give each helper '
             + (f'the names already taken ({taken_file}) and ' if taken_file else '')
             + 'tell it to name a statement by what sets it apart (its table, its filter, its shape) whenever its plain '
             'subject could be another statement\'s too.') if gate == 'm2' else ''
    return {'status': 'ok', 'gate': gate, 'statements': len(ids), 'shares': out,
            'answers': str(engine / 'answers' / gate), **({'taken_names': str(taken_file)} if taken_file else {}),
            'next': 'Start one helper per share, all at once, each with the step instructions, its ids file and its own '
                    'answer file prefix. Each answers only its ids, each exactly once.' + names}
