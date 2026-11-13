"""The run's side page: a page on this machine, beside the chat, that follows the build and shows how each metric
was reached.

`snapshot` reads the run's own files every time it is asked, so the page is never behind and nothing has to push to
it. `serve` holds the page at 127.0.0.1 behind a random key in its address, for this machine only, and stops once
nobody has looked at it for a while. `open_page` starts it once per run and reuses it after. Nothing leaves the
machine, so the page carries the same detail on a demo and on own data: names, values, questions and SQL.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:
    fcntl = None
from urllib.parse import urlsplit
import http.client

from run_facts import BI_TOOLS, RunFacts, count, number, read_json, read_lines, sentence

PAGE = Path(__file__).with_name('side_page.html')
NEVER_RAN = 'Its check never ran.'
IDLE_SECONDS = 20 * 60
RETRY_SECONDS = 10 * 60
LONGEST_SECONDS = 12 * 60 * 60

# The engine's stages in the order a build passes them, grouped as the page names the work.
WORK = (
    ('group', ('h0_corpus', 'h1_shapes', 'h2_measures', 'h5_verified')),
    ('translate', ('a1_parse_bi', 'a2_question_units', 'a3_translate', 'a3b_emit', 'a3b_ingest', 'a3_recompile', 'a4_witness_bi')),
    ('describe', ('c1_normalise', 'c2_merge', 'c3_emit', 'c3_ingest', 'c4_gates', 'c5_emit', 'm1_merge_pre', 'm2_emit',
                  'm2_ingest', 'm1_merge')),
    ('read', ('d1_candidates', 'x1_emit', 'x1_ingest')),
    ('merge', ('x2_emit', 'x2_ingest')),
    ('rules', ('x3_emit', 'x3_ingest')),
    ('link', ('d4b_consistency', 'x4_emit', 'x4_ingest')),
)
GROUP_OF = {stage: group for group, stages in WORK for stage in stages}
# What each piece of the build is, for the line under the step and the Next line.
WORK_NAME = {
    'tables': 'Load the tables on this machine', 'group': 'Group past queries into questions',
    'translate': "Translate the dashboard's tiles into queries", 'describe': 'Describe each query in plain words',
    'read': 'Read each query for the number it measures', 'merge': 'Merge them into metrics',
    'rules': "Write each metric's rule", 'link': 'Link metrics that build on each other',
    'check': 'Check each metric and query on the data',
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _plain(text):
    """The engine's own words in its reasoning, as a person reads them."""
    for pattern, words in ((r'\bhistory statements\b', 'past queries'), (r'\bhistory (\w+) quer(y|ies)\b', r'past \1 quer\2'),
                           (r'\bhistory quer(y|ies)\b', r'past quer\1'), (r'\bstatements\b', 'queries'), (r'\bstatement\b', 'query'),
                           (r'\bproposals\b', 'metrics'), (r'\bproposal\b', 'metric'), (r'\b(the )?canonical expression\b', 'the general rule'),
                           (r'\bcanonical\b', 'the general form'), (r'\bunscoped over\b', 'across'), (r'\bunscoped\b', 'with no filter'),
                           (r'\bsole query\b', 'the only query')):
        text = re.sub(pattern, words, text)
    return text


def _cut(text, n):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


def _objects(f, name):
    data = f.engine_file(name)
    return [o for o in data.get('objects') or [] if isinstance(o, dict)]


# What the run made, and how ---------------------------------------------------------------------

def _sources(f):
    """Every past query and dashboard tile the engine verified, by id, with its question in plain words."""
    said = f.engine_file('sql_descriptions.json').get('descriptions') or {}
    if isinstance(said, list):
        said = {k: v for k, v in said if isinstance(v, dict)}
    out = {}
    for name in ('verified_queries.bi.json', 'verified_queries.history.json', 'verified_queries.json'):
        for o in _objects(f, name):
            if o.get('vq_id'):
                out[o['vq_id']] = (o, said.get(o['vq_id']) or {})
    return out, said


def _source(src, known, said):
    vid = src.get('vq_id')
    vq, words = known.get(vid) or ({}, said.get(vid) or {})
    prov = vq.get('provenance') if isinstance(vq.get('provenance'), dict) else {}
    bi = prov.get('bi') if isinstance(prov.get('bi'), dict) else {}
    hist = prov.get('history') if isinstance(prov.get('history'), dict) else {}
    tile = prov.get('lane') == 'bi' or str(vid or '').startswith('bi-')
    return {k: v for k, v in (
        ('kind', 'tile' if tile else 'query'),
        ('question', _cut(words.get('question') or vq.get('question') or src.get('name_as_proposed'), 200)),
        ('description', _cut(words.get('description'), 320)),
        ('part', [_cut(_plain(str(c)), 160) for c in src.get('contributed') or []]),
        ('sql', _cut(vq.get('sql'), 1600)),
        ('runs', hist.get('n_exec')), ('analysts', hist.get('n_distinct_users')),
        ('visual', bi.get('visual')), ('page', bi.get('page')), ('report', bi.get('report'))) if v not in (None, '', [])}


def _verdicts(f):
    """Each checked item's state by name: 'checked', 'look' (ran and did not pass), or None while it waits."""
    checks = f.file('context', 'candidate-checks.json')
    out = {}
    for key, entry in checks.items():
        if isinstance(entry, dict) and entry.get('status') != 'schema_checked':
            comp = entry.get('composition') if isinstance(entry.get('composition'), dict) else {}
            ok = (not entry.get('errors') and entry.get('status') in ('executed', 'example_executed')
                  and comp.get('status', 'executed') == 'executed')
            out[key] = {'ok': ok, 'rows': comp.get('rows') if isinstance(comp.get('rows'), list) else [],
                        'why': _cut((entry.get('errors') or [''])[0], 200)}
    return out, bool(checks)


def details(f):
    """The metrics as the engine made them, each with where it came from and why, its rule, and its check; the
    queries; and what the run read."""
    known, said = _sources(f)
    cards = f.engine_file('metric_cards.json')
    cards = cards.get('cards') or cards.get('metrics') or [] if isinstance(cards, dict) else []
    by_card = {str(c.get('name') or '').lower(): c for c in cards if isinstance(c, dict)}
    defined = {str(m.get('name') or '').lower(): m for m in f.file('context', 'metrics.json', kind=list) if isinstance(m, dict)}
    verdicts, checking = _verdicts(f)
    # Once the flow has moved past the checks, an item that never got its verdict needs a look, as the chips count it.
    over = f.checks_over
    # A warehouse's checks run with its own read-only tool: their verdicts never reach the run's files.
    waiting = 'checking' if f.checks_here else 'tool'
    ruled = 'x3_ingest' in f.stages
    metrics = []
    for m in f.engine_file('merged_metrics.json').get('metrics') or []:
        if not isinstance(m, dict) or not m.get('name'):
            continue
        key = str(m['name']).lower()
        card, ctx = by_card.get(key) or {}, defined.get(key) or {}
        d = ctx.get('definition') if isinstance(ctx.get('definition'), dict) else {}
        unit = RunFacts.unit_of(ctx) if ctx else str(m.get('unit') or '')
        v = verdicts.get('metric:' + str(ctx.get('name') or m['name']))
        rows = v['rows'] if v else []
        value = None
        if v and v['ok'] and rows:
            first = rows[0] if isinstance(rows[0], list) else [rows[0]]
            value = number(first[0], unit) if len(rows) == 1 and first else count(len(rows), 'row')
        description = ctx.get('description') or card.get('description')
        metrics.append({k: x for k, x in (
            ('name', sentence(ctx.get('display_name') or card.get('display_name') or m['name'])),
            ('unit', unit),
            ('description', _cut(description, 320)),
            ('rule', bool(ruled or description)),
            ('formula', _cut(d.get('formula') or card.get('formula'), 300)),
            ('population', _cut(card.get('population'), 300)),
            ('leaves_out', [_cut(x, 200) for x in card.get('exclusions') or []][:6]),
            ('reasoning', _cut(_plain(str(m.get('merge_reasoning') or '')), 420)),
            ('sources', [_source(s, known, said) for s in m.get('sources') or [] if isinstance(s, dict)]),
            ('status', ('checked' if v['ok'] else 'look') if v else 'look' if over and ctx else waiting if checking else 'drafted'),
            ('value', value), ('why', v['why'] if v and not v['ok'] else NEVER_RAN if over and ctx and not v else None))
            if x not in (None, '', [])}
                       )
    for m in metrics:
        # Every metric carries its sources, an empty list when the engine named none.
        m.setdefault('sources', [])
    # Metrics from the dashboard first, then by how much evidence each has.
    metrics.sort(key=lambda m: (not any(s['kind'] == 'tile' for s in m.get('sources') or []), -len(m.get('sources') or [])))
    queries = []
    for q in f.file('context', 'queries.json', kind=list):
        if isinstance(q, dict) and q.get('name'):
            d = q.get('definition') if isinstance(q.get('definition'), dict) else {}
            v = verdicts.get('sql_query:' + q['name'])
            queries.append({k: x for k, x in (
                ('name', sentence(q.get('display_name') or q['name'])),
                ('question', _cut(d.get('question') or q.get('display_name'), 200)),
                ('status', ('checked' if v['ok'] else 'look') if v else 'look' if over else waiting if checking else 'drafted'),
                ('why', v['why'] if v and not v['ok'] else NEVER_RAN if over and not v else None)) if x})
    def bi_of(o):
        prov = o.get('provenance') if isinstance(o.get('provenance'), dict) else {}
        return prov.get('bi') if isinstance(prov.get('bi'), dict) else {}
    tiles = [{k: x for k, x in (('question', _cut((said.get(o.get('vq_id')) or {}).get('question') or o.get('question'), 200)),
                                ('visual', bi_of(o).get('visual')), ('page', bi_of(o).get('page'))) if x}
             for o in _objects(f, 'verified_queries.bi.json')]
    return {'metrics': metrics, 'queries': queries, 'tiles': tiles}


def _names(items):
    """The names an answer's proof gives, written as {name: ...} or as plain names."""
    return [str(p.get('name') if isinstance(p, dict) else p) for p in items or [] if (p.get('name') if isinstance(p, dict) else p)] \
        if isinstance(items, list) else []


def _answer(f):
    a = f.file('answer.json')
    if not a.get('headline'):
        return None
    proof = _names(a.get('proof'))
    unit = a.get('unit') or (f.unit_for(proof[0]) if proof else None)
    if str(unit or '').lower() == 'currency':
        # A rule recorded as a bare currency: the answer's own headline says which, as on the chat's card.
        sign = re.search(r'[£$€]', str(a.get('headline')).rpartition(': ')[2])
        unit = {'£': 'GBP', '$': 'USD', '€': 'EUR'}[sign.group(0)] if sign else unit
    rows = [r for r in a.get('rows') or [] if isinstance(r, list) and len(r) >= 2][:12]
    return {k: v for k, v in (
        ('question', a.get('question')), ('headline', a.get('headline')), ('summary', a.get('summary')), ('rule', a.get('rule')),
        ('rows', [[str(r[0]), number(r[1], unit) if isinstance(r[1], (int, float)) else str(r[1]), r[1] if isinstance(r[1], (int, float)) else None]
                  for r in rows]),
        ('highlight', a.get('highlight')),
        ('proof', [sentence(n) for n in proof])) if v}


def _edit(f):
    e = f.file('edit.json')
    values = [v for v in e.get('values') or [] if isinstance(v, list) and len(v) >= 3]
    if not e.get('metric') or not values:
        return None
    name, before, after = values[0][0], values[0][1], values[0][2]
    unit = values[0][3] if len(values[0]) > 3 else f.unit_for(e['metric'])
    return {k: v for k, v in (('metric', sentence(e['metric'])), ('name', name), ('before_value', number(before, unit)),
                              ('after_value', number(after, unit)), ('before', e.get('before')), ('after', e.get('after')),
                              ('added', e.get('added')), ('removed', e.get('removed')), ('note', e.get('note'))) if v}


def _layout(f):
    layout = f.file('layout.json')
    tiles = [sentence(t) for t in layout.get('tiles') or [] if t]
    return {'tiles': tiles, 'more': [sentence(t) for t in layout.get('more') or [] if t]} if tiles else None


# The header: where the run is, as one count and a bar ----------------------------------------------

def _work(f):
    """The piece of the build under way, from the engine's own record of the stages it has passed."""
    passed = [s for s in f.stages if s in GROUP_OF]
    if f.context:
        return 'check'
    return GROUP_OF[passed[-1]] if passed else None


def _offer(f, d, demo):
    parts = [] if demo else [count(len(f.tables), 'table')] if f.tables else []
    if d['tiles']:
        parts.append(count(len(d['tiles']), 'dashboard tile'))
    elif f.dashboard.get('formulas'):
        parts.append(count(f.dashboard['formulas'], 'dashboard formula'))
    if f.history:
        parts.append(count(f.history['statements'], 'past query', 'past queries'))
    return ' · '.join(parts)


def header(f, steps, d, demo):
    """Layout B's top: the step, one big running count, a bar that fills with real progress, and the Next line. A step
    that waits for the person says so instead of counting."""
    at = next((i for i, s in enumerate(steps) if s['status'] == 'now'), len(steps))
    step = steps[at] if at < len(steps) else None
    sid = step['id'] if step else 'done'
    h = {'at': at, 'steps': len(steps), 'step': step['purpose'] if step else 'All %d steps done' % len(steps),
         'id': sid, 'waiting': bool(step and step.get('needs_you'))}
    nxt = next((s['purpose'] for s in steps[at + 1:] if s['status'] != 'done'), None)
    st = f.stage
    metrics, queries = d['metrics'], d['queries']
    if sid == 'intent':
        h.update(title='What do you want to do?', wait='Waiting for your use case')
    elif sid == 'sources':
        found, kinds = f.found
        if demo and f.tables:
            rows = f.table_rows
            h.update(title='Loading the tables on this machine', n=len(f.tables), of=len(f.tables), unit='tables loaded',
                     bar=1, note=('%s rows in all' % '{:,}'.format(rows)) if rows else None)
        else:
            h.update(title="Finding the demo's sources" if demo else 'Surveying your sources', n=found,
                     unit='source found' if found == 1 else 'sources found', bar=1 if found else 0, note=', '.join(kinds) or None)
    elif sid == 'scope':
        h.update(title='What should I build it from?' if demo else 'Build it?',
                 wait='Waiting for your choice of sources' if demo else 'Waiting for your selection', note=_offer(f, d, demo) or None)
    elif sid == 'metrics':
        work = _work(f)
        nxt = None
        if work is None:
            h.update(title='Starting the build', bar=0, note=WORK_NAME['tables'] if not demo and f.tables else None)
            nxt = WORK_NAME['group'] if (f.inputs or {}).get('history') else WORK_NAME['translate']
        elif work == 'group':
            # The questions are counted once the grouping has finished; while it runs, the bar moves with no count.
            kept, shapes = st('h0_corpus', 'kept'), st('h1_shapes', 'canonical_shapes')
            h.update(title='Grouping past queries into questions', note='From %s' % count(kept, 'past query', 'past queries') if kept else None)
            if shapes is not None:
                h.update(n=shapes, unit='distinct question' if shapes == 1 else 'distinct questions', bar=1)
        elif work == 'translate':
            tiles = st('a2_question_units', 'units') or len(d['tiles']) or None
            done = st('a3_translate', 'candidates') or 0
            ran = st('a4_witness_bi', 'witnessed')
            h.update(title="Translating the dashboard's tiles into queries", n=done, of=tiles, unit='tiles translated',
                     bar=done / tiles if tiles else 0, note=('%d of %d ran on the data' % (st('a4_witness_bi', 'verdict_pass') or 0, ran))
                     if ran else None)
        elif work == 'describe':
            total = st('m2_emit', 'queries') or st('m1_merge_pre', 'merged')
            done = st('m2_ingest', 'described') or st('c3_ingest', 'candidates') or 0
            # Before the total is known (the dashboard's tiles are described first), the bar moves with no end to fill to.
            h.update(title='Describing each query in plain words', n=done, of=total, unit='queries described',
                     bar=done / total if total else None)
        elif work == 'read':
            total, done = st('x1_emit', 'queries'), st('x1_ingest', 'answered') or 0
            h.update(title='Reading each query for the number it measures', n=done, of=total, unit='queries read',
                     bar=done / total if total else 0,
                     note=('%s found' % count(st('x1_ingest', 'proposals'), 'possible metric')) if st('x1_ingest', 'proposals') else None)
        elif work == 'merge':
            made, proposed = st('x2_ingest', 'metrics_out'), st('x2_emit', 'proposals') or st('x1_ingest', 'proposals')
            h.update(title='Merging different ways of asking into metrics', n=made or 0, unit='metric made' if made == 1 else 'metrics made',
                     bar=1 if made is not None else 0, note='From %s' % count(proposed, 'possible metric') if proposed else None)
        elif work == 'rules':
            n = len(metrics)
            written = n if 'x3_ingest' in f.stages else 0
            h.update(title="Writing each metric's rule", n=written, of=n, unit='rules written', bar=written / n if n else 0)
        elif work == 'link':
            # The links are counted once the step has finished; while it runs, the bar moves with no count.
            edges = st('x4_ingest', 'edges')
            if edges is None:
                pairs = st('x4_emit', 'candidate_pairs')
                h.update(title='Linking metrics that build on each other',
                         note=('Comparing %s' % count(pairs, 'pair of metrics', 'pairs of metrics')) if pairs else None)
            else:
                h.update(title='Linking metrics that build on each other', n=edges, unit='link found' if edges == 1 else 'links found', bar=1)
        elif not f.checks_here:
            # The warehouse's own tool runs these checks: the bar moves with no count, since no verdict reaches the run.
            h.update(title='Checking each metric and query on the data', note="With your warehouse's own read-only tool")
        else:
            # The count and the bar are the same fact: how many items have their check's verdict.
            c = f.context
            checked = c['metrics_checked'] + c['queries_checked']
            look = c['failed'] if checked < c['total'] else c['need_look']
            h.update(title='Checking each metric and query on the data', n=checked, of=c['total'],
                     unit='metrics and queries checked', bar=checked / c['total'] if c['total'] else 0, ok=checked == c['total'] and not look,
                     note='%d need a look' % look if look else None)
        if work is not None and work != 'check':
            chosen = f.inputs or {'bi': bool(d['tiles'] or f.dashboard), 'history': bool(f.history)}
            will = {'group': chosen['history'], 'translate': chosen['bi']}
            order = [g for g, _ in WORK] + ['check']
            later = [g for g in order[order.index(work) + 1:] if will.get(g, True)]
            nxt = WORK_NAME[later[0]] if later else nxt
        elif work == 'check':
            nxt = next((s['purpose'] for s in steps[at + 1:] if s['status'] != 'done'), None)
    elif sid == 'save':
        planned = f.file('context', 'plan.json').get('counts') or {}
        c = f.context
        if planned:
            # The save records its items only once they are all read back, so it moves with no count until then.
            total = sum(int(v.get('create') or 0) + int(v.get('revise') or 0) for v in planned.values() if isinstance(v, dict))
            h.update(title='Saving to Atlan Trident', note=('%s planned' % count(total, 'item')) if total else None, waiting=False)
        else:
            ready = '%s and %s ready' % (count(len(metrics), 'metric'), count(len(queries), 'query', 'queries'))
            h.update(title='Where should this context live?', wait='Waiting for where to save',
                     note=(ready + (' · %d of %d checked' % (c['passed'], c['total']) + (' · %d need a look' % c['need_look'] if c['need_look'] else '')
                                    if f.counts_known else " · checks run with the warehouse's own tool"))
                     if c else None)
    elif sid in ('ask', 'dashboard', 'done'):
        a, layout = d.get('answer'), d.get('layout')
        if sid == 'done' and layout:
            h.update(title='Dashboard laid out', n=len(layout['tiles']), unit='tiles on the dashboard', bar=1, ok=True,
                     note='%d more on offer' % len(layout['more']) if layout['more'] else None)
        elif a and sid != 'ask':
            label, _, value = str(a['headline']).rpartition(': ')
            h.update(title=a.get('question') or 'Answer', value=value or a['headline'], value_label=label or None, note=a.get('summary'),
                     wait='Waiting for your dashboard choice' if sid == 'dashboard' else None)
        else:
            saved = f.publication
            h.update(title='Saved to Atlan Trident' if saved and not f.local else 'Kept on this machine', n=len(metrics),
                     unit='metric ready' if len(metrics) == 1 else 'metrics ready', bar=1, ok=True,
                     note=('In the %s workspace' % saved['workspace']) if saved and saved.get('workspace') and not f.local
                     else 'And %s. Not saved to Atlan.' % count(len(queries), 'query', 'queries') if f.local else None,
                     wait='Waiting for your question' if sid == 'ask' else None)
    e = d.get('edit')
    # A changed rule leads once the context is in use, until a newer answer or layout takes over.
    if e and sid in ('ask', 'dashboard', 'done') and _newest(f.dir, 'edit.json', ('answer.json', 'layout.json')):
        h.update(title='Rule changed: ' + e['metric'], value=e['after_value'], value_label='%s, was %s' % (e['name'], e['before_value']),
                 n=None, of=None, unit=None, bar=None, note=e.get('note'))
    h['next'] = nxt
    # Something is under way exactly when the step is not waiting for the person and the run is not stopped.
    h['busy'] = bool(step) and not h.get('wait') and not h['waiting']
    n, of, unit = h.pop('n', None), h.pop('of', None), h.pop('unit', None)
    if n is not None:
        h['count'] = {k: v for k, v in (('n', n), ('of', of), ('unit', unit)) if v is not None}
    return {k: v for k, v in h.items() if v is not None}


def _newest(root, name, others):
    try:
        mine = (root / name).stat().st_mtime
    except OSError:
        return False
    return all(not (root / o).is_file() or (root / o).stat().st_mtime <= mine for o in others)


def _demo_facts(run):
    """The demo's own facts file, read as `visual connect --data` would have recorded it."""
    import run_facts
    facts = run_facts.demo_facts(run)
    return {**{k: facts[k] for k in ('firm',) if facts.get(k)}, **(facts.get('connect') or {})}


def _steps(run, f, demo, engine_dir=None):
    """The flow's own steps, as the host's task list shows them, each with what came out of it and how."""
    import side_view
    # The moments the chat has shown say what the person has seen; every check with its verdict ends the checks too.
    kinds = side_view.seen(run) + (['context'] if f.checks_over else [])
    state = {'moments': [{'kind': k} for k in kinds], 'demo': demo}
    return side_view.model(run, state, engine_dir, private=False)


def snapshot(run, engine_dir=None):
    """The whole page as one document, from the run's current files."""
    run = Path(run)
    f = RunFacts(run, engine_dir)
    demo = f.interaction.get('flow') == 'demo'
    # Only the run's journey record makes a demo, so a demo's facts never reach own data.
    facts = (read_json(run / 'view' / 'facts.json', dict, {}) or _demo_facts(run)) if demo else {}
    view = _steps(run, f, demo, engine_dir)
    d = details(f)
    d.update(answer=_answer(f), layout=_layout(f), edit=_edit(f))
    found, kinds = f.found
    c = f.context
    saved = f.publication
    return {
        'v': 1, 'flow': 'demo' if demo else 'build', 'updated': _now(),
        'title': facts.get('title') or (f.use_case and f.use_case[:1].upper() + f.use_case[1:]) or 'Analytics setup',
        'subtitle': facts.get('tagline') or f.purpose or None,
        'firm': facts.get('firm'),
        **({'state': view['state']} if view.get('state') else {}),
        # A run that stopped (paused, blocked, waiting for access) never looks busy.
        'now': dict(header(f, view['steps'], d, demo), **({'busy': False} if view.get('state') else {})),
        'steps': [{k: s[k] for k in ('id', 'name', 'status', 'purpose', 'doing', 'how', 'fact', 'result', 'needs_you') if k in s}
                  for s in view['steps']],
        'read': {k: v for k, v in (('found', found), ('kinds', kinds), ('tables', [[t[0], t[1]] for t in f.tables]),
                                   ('dashboard', f.dashboard.get('tool')), ('history', f.history), ('warehouse', f.warehouse),
                                   ('chosen', f.inputs)) if v},
        'checks': {k: c[k] for k in ('total', 'passed', 'need_look', 'failed', 'metrics_checked', 'queries_checked')}
        if c and f.counts_known else None,
        'saved': {'workspace': saved.get('workspace'), 'items': saved.get('items')} if saved and not f.local else None,
        'local': f.local,
        **d,
    }


# The server -------------------------------------------------------------------------------------

def _fingerprint(run):
    """What changed since the page last looked: the run's files' sizes and times, which is cheap to read every second."""
    run = Path(run)
    h = hashlib.sha256()
    for folder in (run, run / 'context', run / 'engine', run / 'view'):
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.is_file() and not e.name.startswith('.') and e.name != 'seen.json':
                st = e.stat()
                h.update(('%s/%s %d %d\n' % (folder.name, e.name, st.st_size, st.st_mtime_ns)).encode())
    engine = RunFacts(run).engine
    if engine != run / 'engine' and engine.is_dir():
        for e in sorted(os.scandir(engine), key=lambda e: e.name):
            if e.is_file():
                st = e.stat()
                h.update(('%s %d %d\n' % (e.name, st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()


def _page():
    return PAGE.read_text(encoding='utf-8')


class _Handler(BaseHTTPRequestHandler):
    server_version = 'AtlanAnalytics'
    sys_version = ''

    def log_message(self, *args):
        pass

    def _send(self, status, body=b'', kind='text/plain; charset=utf-8', extra=None):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self._safe(extra)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _safe(self, extra=None):
        for k, v in (('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                     ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                      "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'"), *(extra or {}).items()):
            self.send_header(k, v)

    def _host_ok(self):
        # Only this machine's own name for the page: a page on another site that resolves its name here is refused.
        host = (self.headers.get('Host') or '').lower()
        return host in ('127.0.0.1:%d' % self.server.port, 'localhost:%d' % self.server.port)

    def do_POST(self):
        """Stop, asked through the page's own address: works on every system, with no process list."""
        srv = self.server
        if not self._host_ok():
            return self._send(421, b'Wrong host')
        if urlsplit(self.path).path != '/' + srv.key + '/stop':
            return self._send(404, b'Not found')
        self._send(204)
        srv.stopping = True
        threading.Thread(target=srv.shutdown, daemon=True).start()

    def do_GET(self):
        srv = self.server
        if not self._host_ok():
            return self._send(421, b'Wrong host')
        path = urlsplit(self.path).path
        base = '/' + srv.key
        if path == base:
            return self._send(308, extra={'Location': base + '/'})
        if path == base + '/':
            return self._send(200, srv.page, 'text/html; charset=utf-8')
        if path == base + '/ping':
            return self._send(204)
        if path == base + '/state':
            # The page asking for the run keeps it up; the helper's probe and requests without the key do not.
            srv.touched = time.time()
            srv.seen()
            try:
                body = json.dumps(snapshot(srv.run), ensure_ascii=False).encode('utf-8')
            except Exception:  # noqa: BLE001 - a file caught half written; the page asks again
                return self._send(503, b'Try again', extra={'Retry-After': '2'})
            return self._send(200, body, 'application/json; charset=utf-8')
        if path == base + '/events':
            srv.touched = time.time()
            return self._events()
        return self._send(404, b'Not found')

    def _events(self):
        """The page's live feed: a new snapshot whenever the run's files change, and a comment line now and then so a
        quiet build keeps its connection."""
        srv = self.server
        srv.seen()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Connection', 'keep-alive')
        self._safe()
        self.end_headers()
        with srv.lock:
            srv.watchers += 1
        last, quiet = None, time.time()
        try:
            while not srv.stopping:
                mark = _fingerprint(srv.run)
                if mark != last:
                    try:
                        data = json.dumps(snapshot(srv.run), ensure_ascii=False)
                    except Exception:  # noqa: BLE001 - a file caught half written is read again on the next beat
                        time.sleep(1)
                        continue
                    last = mark
                    self.wfile.write(('data: %s\n\n' % data).encode('utf-8'))
                    self.wfile.flush()
                    quiet = time.time()
                elif time.time() - quiet > 15:
                    self.wfile.write(b': still here\n\n')
                    self.wfile.flush()
                    quiet = time.time()
                srv.touched = time.time()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with srv.lock:
                srv.watchers -= 1


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, run, port=0, key=None):
        # A page restarted for the same run keeps its address when it is free, so an open tab reconnects by itself.
        try:
            super().__init__(('127.0.0.1', port), _Handler)
        except OSError:
            super().__init__(('127.0.0.1', 0), _Handler)
        self.run = Path(run)
        self.port = self.server_address[1]
        self.key = key if key and re.fullmatch(r'[A-Za-z0-9_-]{16,64}', key) else secrets.token_urlsafe(18)
        self.page = _page().encode('utf-8')
        self.started = self.touched = time.time()
        self.watchers, self.stopping = 0, False
        self.lock = threading.Lock()

    def seen(self):
        """The first time the page is open, so the helper can stop asking for it."""
        path = self.run / 'view' / 'seen.json'
        if not path.is_file():
            _write(path, json.dumps({'at': _now()}))


def _write(path, text):
    """Replace a file whole, readable by this account only (the page's records hold its key)."""
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


@contextmanager
def _one_at_a_time(run):
    """One start of the run's page at a time, so two helper calls at once never start two servers."""
    lock = Path(run) / 'view' / '.page.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    if not fcntl:
        # No file locks here: a lock file made exclusively, waited for, and taken over when it is stale.
        for _ in range(200):
            try:
                os.close(os.open(str(lock) + '.held', os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                held = Path(str(lock) + '.held')
                if held.is_file() and time.time() - held.stat().st_mtime > 30:
                    held.unlink(missing_ok=True)
                time.sleep(0.05)
        try:
            yield
        finally:
            Path(str(lock) + '.held').unlink(missing_ok=True)
        return
    with open(lock, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _spawn(args, key):
    """Start the server on its own, its key on standard input (a command line is visible to every account)."""
    if hasattr(os, 'posix_spawn'):
        read, write = os.pipe()
        quiet = os.open(os.devnull, os.O_RDWR)
        try:
            os.posix_spawn(args[0], args, dict(os.environ), setsid=True, file_actions=[
                (os.POSIX_SPAWN_DUP2, read, 0), (os.POSIX_SPAWN_DUP2, quiet, 1), (os.POSIX_SPAWN_DUP2, quiet, 2),
                (os.POSIX_SPAWN_CLOSE, write)])
            os.write(write, (key + '\n').encode('ascii'))
        finally:
            for fd in (read, write, quiet):
                os.close(fd)
        return
    spawn = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    spawn.stdin.write((key + '\n').encode('ascii'))
    spawn.stdin.close()


def serve(run, port=0, key=None):
    """Hold the page until nobody has looked at it for a while, or for the longest a build takes."""
    srv = _Server(run, port, key)
    record = srv.run / 'view' / 'server.json'
    url = 'http://127.0.0.1:%d/%s/' % (srv.port, srv.key)
    _write(record, json.dumps({'url': url, 'pid': os.getpid(), 'started': _now()}))
    # The address outlives the server, so a page that stops when idle comes back where an open tab looks for it.
    _write(srv.run / 'view' / 'address.json', json.dumps({**read_json(srv.run / 'view' / 'address.json', dict, {}), 'url': url}))

    def watch():
        while True:
            time.sleep(5)
            idle = time.time() - srv.touched
            if (srv.watchers == 0 and idle > IDLE_SECONDS) or time.time() - srv.started > LONGEST_SECONDS:
                srv.stopping = True
                srv.shutdown()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        srv.server_close()
        current = read_json(record, dict, {})
        if current.get('pid') == os.getpid():
            record.unlink(missing_ok=True)


def _answers(url):
    """Whether the page at this address answers, asked straight over a socket: a proxy in the environment must never
    take a request meant for this machine."""
    found = re.fullmatch(r'http://127\.0\.0\.1:(\d+)(/[A-Za-z0-9_-]+/)', str(url or ''))
    if not found:
        return False
    conn = http.client.HTTPConnection('127.0.0.1', int(found.group(1)), timeout=2)
    try:
        conn.request('GET', found.group(2) + 'ping')
        return conn.getresponse().status == 204
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _ours(pid, run):
    """Whether this process is the run's page server, so nothing else is ever signalled or waited on."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        line = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # The run's own server: its exact folder, then its port, so run_1 never matches run_10's server.
    return 'side_page.py serve %s ' % Path(run).resolve() in line


def running(run):
    """The page's address while its server answers, else None."""
    record = read_json(Path(run) / 'view' / 'server.json', dict, {})
    url = record.get('url')
    return url if url and _answers(url) else None


def open_page(run, host=None, explicit=True):
    """Start the run's page, or find the one already serving it. Returns its address, whether this call opened it in the
    system browser, and whether anyone has looked at it yet."""
    run = Path(run)
    (run / 'view').mkdir(parents=True, exist_ok=True)
    if explicit:
        # Asking for the page undoes a deliberate stop.
        (run / 'view' / 'stopped.json').unlink(missing_ok=True)
    started = moved = False
    failed = run / 'view' / 'start-failed.json'
    with _one_at_a_time(run):
        url = running(run)
        if not url and not explicit and time.time() - float(read_json(failed, dict, {}).get('at') or 0) < RETRY_SECONDS:
            # A page that could not start is not tried again on every helper call: each try waits for it.
            return {'error': 'The side page could not start on this machine.'}
        if not url:
            record = read_json(run / 'view' / 'server.json', dict, {})
            before = record.get('url') or read_json(run / 'view' / 'address.json', dict, {}).get('url') or ''
            last = re.fullmatch(r'http://127\.0\.0\.1:(\d+)/([A-Za-z0-9_-]+)/', before)
            if not _ours(record.get('pid'), run):
                # One server per run: a page still starting is waited for, never started again.
                (run / 'view' / 'server.json').unlink(missing_ok=True)
                _spawn([sys.executable, str(Path(__file__).resolve()), 'serve', str(run.resolve()), last.group(1) if last else '0'],
                       last.group(2) if last else secrets.token_urlsafe(18))
            for _ in range(60):
                time.sleep(0.1)
                url = running(run)
                if url:
                    break
            started = bool(url)
            moved = bool(url and before and url != before)
        if not url:
            _write(failed, json.dumps({'at': time.time()}))
            return {'error': 'The side page could not start on this machine.'}
        failed.unlink(missing_ok=True)
    host = host or os.environ.get('CLAUDE_CODE_ENTRYPOINT') or ''
    opened = False
    record = read_json(run / 'view' / 'address.json', dict, {})
    # A terminal has no pane of its own: the helper opens the page in the system browser, once per run, however
    # often the page restarts.
    if host == 'cli' and not record.get('opened') and not (run / 'view' / 'seen.json').is_file():
        import webbrowser
        try:
            opened = bool(webbrowser.open(url))
        except Exception:  # noqa: BLE001 - no browser leaves the address to print
            opened = False
        if opened:
            _write(run / 'view' / 'address.json', json.dumps({**record, 'opened': _now()}))
    return {'url': url, 'started': started, 'opened': opened, 'moved': moved, 'seen': (run / 'view' / 'seen.json').is_file(),
            'host': host}


def instruction(opened, widget=False):
    """What the agent does with the page it was handed: open it where the host has a pane, or name it once."""
    url = opened.get('url')
    if not url:
        return ('The side page could not start on this machine: carry on. The chips and cards still show each moment; '
                'say nothing about the page.')
    follows = 'It follows the run by itself from the run\'s own files: never reload, rebuild or push to it.'
    if opened.get('host') == 'claude-desktop' or widget:
        return ('Open the side page now in the app\'s browser pane: your browser tool\'s preview or navigate call with url '
                '`%s`. %s Say nothing about it in the chat; the cards point to it.' % (url, follows))
    if opened.get('opened'):
        return ('The side page opened in the person\'s browser at %s. Say nothing about it now: the next chip in a '
                'question names it. %s' % (url, follows))
    return ('Say nothing about the side page now: the next chip in a question names its address, %s. %s' % (url, follows))


def reminder(run):
    """One line for the next helper result when the page needs the agent: it stopped and came back at a new address,
    or, in the desktop app, nobody has opened it yet. None once all is well, or before the run first opened it."""
    run = Path(run)
    moments = read_json(run / 'view' / 'state.json', dict, {}).get('moments') or []
    if not any(isinstance(m, dict) and m.get('kind') == 'connect' for m in moments) or (run / 'view' / 'stopped.json').is_file():
        return None
    opened = {'url': running(run)}
    if not opened['url']:
        opened = open_page(run, explicit=False)
        if not opened.get('url'):
            return None
    host = os.environ.get('CLAUDE_CODE_ENTRYPOINT') or ''
    if opened.get('moved'):
        return 'The side page restarted at a new address. ' + instruction(opened)
    if host == 'claude-desktop' and not (run / 'view' / 'seen.json').is_file():
        return instruction({**opened, 'host': host})
    return None


def stop(run):
    """Stop the run's page: only a process that is this run's page server is ever signalled."""
    record = read_json(Path(run) / 'view' / 'server.json', dict, {})
    found = re.fullmatch(r'http://127\.0\.0\.1:(\d+)(/[A-Za-z0-9_-]+/)', str(record.get('url') or ''))
    if found:
        conn = http.client.HTTPConnection('127.0.0.1', int(found.group(1)), timeout=2)
        try:
            conn.request('POST', found.group(2) + 'stop')
            conn.getresponse()
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
    pid = record.get('pid')
    if _ours(pid, run):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    Path(run, 'view', 'server.json').unlink(missing_ok=True)
    # Stopped on purpose: no helper call starts it again until the page is asked for.
    _write(Path(run) / 'view' / 'stopped.json', json.dumps({'at': _now()}))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'serve':
        serve(sys.argv[2], int(sys.argv[3]), sys.stdin.readline().strip())
    elif len(sys.argv) == 3 and sys.argv[1] == 'state':
        print(json.dumps(snapshot(sys.argv[2]), ensure_ascii=False, indent=1))
    else:
        sys.exit('usage: side_page.py serve|state <run-dir>')
