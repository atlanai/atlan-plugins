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

from run_facts import RunFacts, count, first_question, number, read_json, sentence

PAGE = Path(__file__).with_name('side_page.html')
NEVER_RAN = 'Its check never ran.'
IDLE_SECONDS = 20 * 60
RETRY_SECONDS = 10 * 60
# A quiet feed says it is still there this often, so a tab that closed is noticed.
KEEPALIVE = 15
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
    said = f.descriptions
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
        ('id', vid), ('kind', 'tile' if tile else 'query'),
        ('question', _cut(first_question(words.get('question') or vq.get('question') or src.get('name_as_proposed')), 200)),
        ('description', _cut(words.get('description'), 320)),
        ('part', [_cut(_plain(str(c)), 160) for c in src.get('contributed') or []]),
        ('sql', _cut(vq.get('sql'), 1600)),
        ('runs', hist.get('n_exec')), ('analysts', hist.get('n_distinct_users')),
        ('visual', bi.get('visual')), ('page', bi.get('page')), ('report', bi.get('report'))) if v not in (None, '', [])}


def _checked_value(v, unit=None):
    """What a passed check returned: its one number, or how many rows."""
    rows = v['rows'] if v and v['ok'] else []
    if not rows:
        return None
    first = rows[0] if isinstance(rows[0], list) else [rows[0]]
    return number(first[0], unit) if len(rows) == 1 and first else count(len(rows), 'row')


def _table_names(tables):
    """Each table once: a local database's tables come back as SITE.SITE.JOBS beside site.jobs, so a repeated first part
    goes, and a second spelling of the same table is left out."""
    out, seen = [], set()
    for name in tables:
        parts = str(name).split('.')
        while len(parts) > 2 and parts[0].lower() == parts[1].lower():
            parts.pop(0)
        name = '.'.join(parts)
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out[:12]


def _query_profile(vid, known, said, built_on, description=None, sql=None, tables=None, review=None):
    """A saved query's profile, as a metric's: where it came from, what it reads, and the metrics built on it."""
    source = _source({'vq_id': vid}, known, said) if vid else {}
    vq = (known.get(vid) or ({},))[0]
    tables = tables if isinstance(tables, list) else vq.get('tables') if isinstance(vq.get('tables'), list) else []
    # The engine marks a query worth a person's read (`needs_review`), with notes on what it does; saved, that is any
    # review status but verified or reviewed.
    read = vq.get('label') == 'needs_review' if review is None else review not in ('verified', 'reviewed')
    notes = [_cut(n, 200) for n in vq.get('gotchas') or [] if isinstance(n, str) and n.strip()][:4]
    return (('review', 'read' if read else None), ('notes', notes),
            ('kind', source.get('kind')), ('description', _cut(description, 600) or source.get('description')),
            ('sql', _cut(sql, 1600) or source.get('sql')), ('tables', _table_names(tables)),
            ('metrics', list(dict.fromkeys(built_on.get(vid) or []))),
            *((k, source.get(k)) for k in ('runs', 'analysts', 'visual', 'page', 'report')))


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


BATCHED = ('m2', 'x1', 'x2', 'x3')  # the describe, propose, merge and card steps save their answers in numbered batches


def _batches(f, gate, key):
    """The rows of a step's saved answer batches, oldest first. A batch still being written reads as empty."""
    folder = f.engine / 'answers' / gate
    try:
        paths = sorted(folder.glob('*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name)) if folder.is_dir() else []
    except OSError:
        return []
    rows = []
    for path in paths[:400]:
        found = read_json(path, dict, {}).get(key)
        rows += [r for r in found if isinstance(r, dict)] if isinstance(found, list) else []
    return rows


def _drafts(f):
    """Each proposed metric by proposal id (`<query>#<n>`, as the engine numbers them): its name, meaning and query.
    The propose step's accepted file once it has one, else its saved batches."""
    out = {}
    for p in f.engine_file('metric_proposals.json').get('proposals') or []:
        if isinstance(p, dict) and p.get('proposal_id') and p.get('name'):
            out[str(p['proposal_id'])] = {'name': str(p['name']), 'meaning': p.get('definition') or p.get('why_a_metric'),
                                          'vq_id': p.get('vq_id')}
    if out:
        return out
    for row in _batches(f, 'x1', 'statements'):
        for i, m in enumerate(row.get('metrics') if isinstance(row.get('metrics'), list) else []):
            if isinstance(m, dict) and m.get('name') and isinstance(row.get('vq_id'), str):
                out['%s#%d' % (row['vq_id'], i)] = {'name': str(m['name']), 'meaning': m.get('definition') or m.get('why_a_metric'),
                                                    'vq_id': row['vq_id']}
    return out


def has_output(f):
    """Whether the page has a metric to show: a possible metric the build found, a merged one, or a defined one. Before
    that it would open empty (a tester, 2026-11-19)."""
    return bool(f.file('context', 'metrics.json', kind=list) or f.engine_file('merged_metrics.json').get('metrics')
                or _batches(f, 'x2', 'metrics') or _drafts(f))


def _source_of(s, drafts):
    """A merge's source as the object the engine records: a bare proposal id names its query through the draft."""
    if isinstance(s, dict):
        return s
    if isinstance(s, str):
        d = drafts.get(s) or {}
        return {'proposal_id': s, 'vq_id': d.get('vq_id') or s.rsplit('#', 1)[0], 'name_as_proposed': d.get('name')}
    return None


def _merged_from(sources, drafts, merged_name=''):
    """The drafts a merge combined, each name once with how many there were, when it combined more than one: when
    every draft already had the metric's name, just how many drafts."""
    names = {}
    for s in sources:
        name = s.get('name_as_proposed') or (drafts.get(str(s.get('proposal_id'))) or {}).get('name')
        if name:
            entry = names.setdefault(str(name).lower(), [sentence(str(name)), 0])
            entry[1] += 1
    if len(sources) < 2:
        return []
    if list(names) == [sentence(str(merged_name)).lower()]:
        return ['%d drafts' % len(sources)]
    return [n if k == 1 else '%s ×%d' % (n, k) for n, k in names.values()]


TYPES = {'count': 'Count', 'count_distinct': 'Count', 'sum': 'Sum', 'avg': 'Average', 'average': 'Average', 'mean': 'Average',
         'ratio': 'Rate', 'rate': 'Rate', 'min': 'Lowest', 'max': 'Highest', 'median': 'Median'}
# Why an item was kept back, in the person's words, by the engine's reason code.
# Why the save kept an item back, for each reason the export writes: in full on the page, and after a count in the chat.
KEPT = {'compiler_review_wrong': 'The review found the translation of its tile wrong.',
        'required_filter_missing': 'It leaves out a filter its dashboard tile always applies, so its number could differ.',
        'source_query_held': 'A tile it comes from was kept back, so its number could differ from the dashboard.',
        'dashboard_parity_failed': "Its number didn't match the dashboard's own when both ran on the same data.",
        'required_metric_held': 'It is built on another metric that was kept back.',
        'source_of_held_metric': 'A metric made from it was kept back, so it was kept back too.',
        'privacy_review': 'It may show personal details, so it waits for a review before it is saved.',
        'required_member_scope': 'It only works once something is picked first, such as one account.',
        'card_aggregation': "Its calculation doesn't match the tile's."}
KEPT_SHORT = {'compiler_review_wrong': "failed the review of its tile's translation",
              'required_filter_missing': 'left out a filter its tile always applies',
              'source_query_held': 'came from a tile that was kept back',
              'dashboard_parity_failed': "didn't match the dashboard's number",
              'required_metric_held': 'used a metric that was kept back',
              'source_of_held_metric': 'fed a metric that was kept back',
              'privacy_review': 'may show personal details, so wait for a review',
              'required_member_scope': 'only worked with something picked first',
              'card_aggregation': "didn't calculate as its tile does"}
KEPT_ANY = 'It needs a look before it can be saved.'


def held(f):
    """What the save kept back: how many metrics and queries, and each reason with its count, most common first."""
    holds = f.file('context', 'publication-holds.json')
    rows = {k: [h for h in holds.get(k) or [] if isinstance(h, dict)] if isinstance(holds, dict) else [] for k in ('metrics', 'queries')}
    reasons = [str(h.get('reason')) for h in rows['metrics'] + rows['queries']]
    ranked = sorted(set(reasons), key=lambda r: (-reasons.count(r), r))
    return {'metrics': len(rows['metrics']), 'queries': len(rows['queries']),
            'why': ', '.join('%d %s' % (reasons.count(r), KEPT_SHORT.get(r, 'need a look first')) for r in ranked)}


def _type_of(d, card, name):
    """What kind of number a metric is, in one word: its name says a rate or an average before its aggregation does."""
    words = str(name).lower()
    if 'rate' in words.split() or words.endswith(' rate') or '%' in words:
        return 'Rate'
    if 'average' in words or words.startswith('avg'):
        return 'Average'
    agg = (d.get('aggregation') or card.get('aggregation') or {}) if isinstance(d.get('aggregation') or card.get('aggregation') or {}, dict) else {}
    return TYPES.get(str(agg.get('type') or '').lower(), 'Calculated' if agg.get('type') else None)


def _short_table(name):
    return str(name).split('.')[-1] if name else None


def _definition_parts(d, card):
    """The rest of a written rule, for the metric's own view: what it leaves out, its grain, what it breaks down by, the
    table it reads and what it joins, its filters as written, and one worked example."""
    if not d and not card:
        return ()
    source = d.get('source') if isinstance(d.get('source'), dict) else {}
    dims = [x.get('name') for x in d.get('dimensions') or [] if isinstance(x, dict) and x.get('name')]
    joins = [_short_table(j.get('to')) for j in d.get('joins') or [] if isinstance(j, dict) and j.get('to')]
    filters = [_cut(str(x), 400) for x in (d.get('mandatory_filters') or []) + (d.get('filters') or []) if x]
    examples = [e for e in d.get('examples') or [] if isinstance(e, dict) and e.get('sql')]
    leaves = d.get('exclusions') or card.get('exclusions') or []
    return (('leaves_out', [_cut(str(x), 240) for x in leaves][:8]),
            ('population', _cut(d.get('population') or card.get('population'), 1200)),
            ('grain', ', '.join(_short_table(g.strip()) for g in str(d.get('grain') or '').split(',') if g.strip())),
            ('dimensions', dims[:12]), ('table', _short_table(source.get('table') or source.get('entity'))),
            ('joins', [j for j in joins if j][:6]), ('filters', filters[:6]),
            ('example_sql', _cut(examples[0]['sql'], 2400) if examples else None))


def _mark_kept(f, metrics):
    """A metric the save keeps back shows as kept back, with why. The record can name it differently from the merge (the
    rule's own name), so it is matched by the tiles and queries it came from as well as by name."""
    holds = f.file('context', 'publication-holds.json')
    for h in holds.get('metrics') or [] if isinstance(holds, dict) else []:
        if not isinstance(h, dict):
            continue
        ids = set(h.get('query_ids') or [])
        for m in metrics:
            if lc_name(m['name']) == lc_name(h.get('name')) or ids & {s.get('id') for s in m.get('sources') or []}:
                m['stage'] = 'kept'
                m['kept_reason'] = KEPT.get(str(h.get('reason')), KEPT_ANY)
                break


def lc_name(name):
    return sentence(str(name or '')).lower()


def _mark_related(f, metrics):
    """The metrics each one builds on or sits beside, from the run's links between metrics."""
    names = {lc_name(m['name']): m for m in metrics}
    for link in f.file('context', 'links.json', kind=list):
        if not isinstance(link, dict):
            continue
        a, b = link.get('from') or {}, link.get('to') or {}
        if a.get('kind') != 'metric' or b.get('kind') != 'metric':
            continue
        word = 'Builds on' if link.get('rel_type') in ('depends_on', 'derived_from', 'composed_of') else 'Related to'
        for here, there in ((a, b), (b, a)):
            m = names.get(lc_name(here.get('name')))
            if m and lc_name(there.get('name')) in names:
                m.setdefault('related', []).append([word if here is a else 'Used by' if word == 'Builds on' else word,
                                                    names[lc_name(there.get('name'))]['name']])


def drafts_list(f, metrics):
    """Every draft metric the build has found, as it lands: its name, the tile or query it was found in, and, once the
    drafts are merged, the metric it became."""
    known, said = _sources(f)
    into = {}
    for m in metrics:
        for s in m.get('sources') or []:
            into.setdefault(s.get('id'), m['name'])
    out = []
    for pid, d in _drafts(f).items():
        src = _source({'vq_id': d.get('vq_id')}, known, said)
        out.append({k: v for k, v in (('id', pid), ('name', sentence(d['name'])), ('meaning', _cut(d.get('meaning'), 400)),
                                      ('kind', src.get('kind')), ('question', src.get('question')), ('page', src.get('page')),
                                      ('into', into.get(d.get('vq_id')) if any(m.get('stage') != 'draft' for m in metrics) else None))
                    if v not in (None, '', [])})
    return out


# Why a tile could not be turned into a query, in the person's words, by the start of the engine's reason.
REFUSED = (('compiler emitted unparseable', 'The translation came out malformed'),
           ('filter column', 'A filter would have been lost, changing the number'),
           ('filter(s) on', 'A filter would have been lost, changing the number'),
           ('untranslatable', "It uses something that can't be turned into a query"))


def cant_build(f):
    """The dashboard's tiles the build could not turn into queries, each with its page and why."""
    report = f.engine_file('a3_compile_report.json')
    units = f.engine_file('question_units.json')
    units = units.get('units') if isinstance(units, dict) else units if isinstance(units, list) else []
    title = {u.get('unit_id'): u.get('title') for u in units or [] if isinstance(u, dict)}
    out = []
    for r in report.get('refused') or [] if isinstance(report, dict) else []:
        if not isinstance(r, dict):
            continue
        uid = str(r.get('unit_id') or '')
        parts = uid.split('::')
        why = next((w for k, w in REFUSED if str(r.get('reason') or '').startswith(k)), "It couldn't be turned into a query")
        out.append({'tile': _cut(title.get(uid) or (parts[-1] if parts else ''), 120), 'page': parts[2] if len(parts) > 3 else None,
                    'why': why})
    return out


def tiles_by_page(f, pages=None):
    """The report's tiles by page once the build has translated them, for the readiness check: how many became queries
    and each that could not, with why. Before the translation it says so instead of guessing."""
    units = f.engine_file('question_units.json')
    units = units.get('units') if isinstance(units, dict) else units if isinstance(units, list) else []
    if 'a3_translate' not in f.stages or not units:
        return {'status': 'not_translated', 'next': 'Run the build up to its first written step first; the translation takes '
                                                    'seconds and needs no answers.'}
    cant = {}
    for c in cant_build(f):
        cant.setdefault(c.get('page'), []).append({'tile': c['tile'], 'why': c['why']})
    wanted = {str(p).lower() for p in pages or []}
    out = {}
    for u in units:
        if not isinstance(u, dict):
            continue
        parts = str(u.get('unit_id') or '').split('::')
        page = parts[2] if len(parts) > 3 else ''
        if wanted and page.lower() not in wanted:
            continue
        out.setdefault(page, {'page': page, 'tiles': 0, 'cannot': cant.get(page, [])})['tiles'] += 1
    rows = [dict(v, buildable=v['tiles'] - len(v['cannot'])) for v in out.values()]
    return {'status': 'ok', 'pages': rows, 'tiles': sum(r['tiles'] for r in rows),
            'buildable': sum(r['buildable'] for r in rows)}


# The build's checkpoints, in the order a build passes them: (group, name while it waits or runs, name once done).
CHECKPOINTS = (('group', 'Group past queries into questions', 'Grouped past queries'),
               ('translate', "Turn the report's tiles into queries", 'Turned tiles into queries'),
               ('describe', 'Describe each query', 'Described each query'),
               ('read', 'Find metrics', 'Found metrics'),
               ('merge', 'Combine duplicates, one per number', 'Combined duplicates'),
               ('rules', 'Define each metric', 'Defined each metric'),
               ('link', 'Link related metrics', 'Linked related metrics'))


def checkpoints(f, d):
    """The build's steps for the page's checklist: each done with its result, the one under way with its count, and the
    rest still to come. A step the build skips (past queries on a dashboard-only build) is left out."""
    st, chosen = f.stage, f.inputs or {'bi': bool(_objects(f, 'verified_queries.bi.json') or f.dashboard), 'history': bool(f.history)}
    will = {'group': chosen.get('history'), 'translate': chosen.get('bi')}
    work = _work(f)
    order = [g for g, _ in WORK]
    at = len(order) if work == 'check' else order.index(work) if work in order else -1
    results = {
        'group': count(st('h1_shapes', 'canonical_shapes'), 'question') if st('h1_shapes', 'canonical_shapes') else None,
        'translate': '%d of %s' % (st('a3_translate', 'candidates'), count(st('a3_translate', 'units'), 'tile'))
        if st('a3_translate', 'candidates') is not None else None,
        'describe': count(st('m2_ingest', 'described'), 'query', 'queries') if st('m2_ingest', 'described') else None,
        'read': count(st('x1_ingest', 'proposals'), 'draft') if st('x1_ingest', 'proposals') else None,
        'merge': count(st('x2_ingest', 'metrics_out'), 'metric') if st('x2_ingest', 'metrics_out') else None,
        'rules': count(len(d['metrics']), 'rule') if 'x3_ingest' in f.stages else None,
        'link': count(st('x4_ingest', 'edges'), 'link') if st('x4_ingest', 'edges') is not None else None,
    }
    steps = []
    for group, waiting, done in CHECKPOINTS:
        if will.get(group, True) is False:
            continue
        i = order.index(group)
        state = 'done' if i < at else 'now' if i == at else 'next'
        steps.append({k: v for k, v in (('id', group), ('name', done if state == 'done' else waiting), ('state', state),
                                        ('result', results[group] if state == 'done' else None)) if v})
    return steps


def details(f):
    """The metrics as the engine made them, each with where it came from and why, its rule, and its check; and the
    queries."""
    known, said = _sources(f)
    cards = f.engine_file('metric_cards.json')
    cards = cards.get('cards') or cards.get('metrics') or [] if isinstance(cards, dict) else []
    by_card = {str(c.get('name') or '').lower(): c for c in cards if isinstance(c, dict)}
    for c in _batches(f, 'x3', 'metrics'):  # each rule shows as its batch is saved, before the step accepts them
        by_card.setdefault(str(c.get('name') or '').lower(), c)
    drafts = _drafts(f)
    defined = {str(m.get('name') or '').lower(): m for m in f.file('context', 'metrics.json', kind=list) if isinstance(m, dict)}
    verdicts, checking = _verdicts(f)
    # Once the flow has moved past the checks, an item that never got its verdict needs a look, as the chips count it.
    over = f.checks_over
    # A warehouse's checks run with its own read-only tool: a verdict arrives only as each result is recorded. Skipped
    # by the person, an item without a verdict is untested.
    waiting = 'untested' if f.checks_skipped else 'checking' if f.checks_here else 'tool'
    ruled = 'x3_ingest' in f.stages
    metrics = []
    merged = f.engine_file('merged_metrics.json').get('metrics') or []
    pending = not merged  # until the merge step is accepted, its saved batches are the merges so far
    merged = merged or _batches(f, 'x2', 'metrics')
    claimed, built_on = set(), {}
    for m in merged:
        if not isinstance(m, dict) or not m.get('name'):
            continue
        sources = [x for x in (_source_of(s, drafts) for s in m.get('sources') or []) if x]
        claimed.update(str(s.get('proposal_id')) for s in sources)
        key = str(m['name']).lower()
        card, ctx = by_card.get(key) or {}, defined.get(key) or {}
        d = ctx.get('definition') if isinstance(ctx.get('definition'), dict) else {}
        unit = RunFacts.unit_of(ctx) if ctx else str(m.get('unit') or '')
        v = verdicts.get('metric:' + str(ctx.get('name') or m['name']))
        value = _checked_value(v, unit)
        # Beside the dashboard's own figure: the number its tile's query returned, when it returned one.
        derived = (d.get('provenance') or {}).get('derived_from') if isinstance(d.get('provenance'), dict) else []
        tile = next((f.tile_figures[x] for x in derived or [] if x in f.tile_figures), None) if value else None
        tile = {'name': tile[0], 'value': number(tile[1], unit), 'same': number(tile[1], unit) == value} if tile else None
        description = ctx.get('description') or card.get('description')
        metrics.append({k: x for k, x in (
            ('name', sentence(ctx.get('display_name') or card.get('display_name') or m['name'])),
            ('unit', unit),
            # Whole: a fix adds its rule to the end of the meaning, and the list's rows clip it on screen anyway.
            ('description', _cut(description, 2000)),
            ('rule', bool(ruled or description)),
            ('formula', _cut(d.get('formula') or card.get('formula'), 300)),
            ('population', _cut(card.get('population'), 300)),
            ('leaves_out', [_cut(x, 200) for x in card.get('exclusions') or []][:6]),
            ('reasoning', _cut(_plain(str(m.get('merge_reasoning') or '')), 420)),
            ('sources', [_source(s, known, said) for s in sources]),
            ('from', _merged_from(sources, drafts, m['name'])),
            ('status', ('checked' if v['ok'] else 'look') if v else 'look' if over and ctx else waiting if checking
             else 'final' if ruled or description else 'merged'),
            ('value', value), ('tile', tile),
            ('why', v['why'] if v and not v['ok'] else NEVER_RAN if over and ctx and not v else None),
            # Where the metric is in its life, apart from its check: the page's badge.
            ('stage', 'final' if ruled or description else 'merged'),
            ('type', _type_of(d, card, m['name'])),
            *_definition_parts(d, card))
            if x not in (None, '', [])}
                       )
        for s in sources:
            built_on.setdefault(s.get('vq_id'), []).append(metrics[-1]['name'])
    if pending:
        # Drafts no merge has claimed yet: one row per name, from every query that proposed it.
        rows, shown = {}, {m['name'].lower() for m in metrics}
        for pid, d in drafts.items():
            if pid not in claimed and sentence(d['name']).lower() not in shown:  # a merged row of that name already shows it
                rows.setdefault(d['name'].lower(), (d, []))[1].append({'proposal_id': pid, 'vq_id': d['vq_id']})
        for d, sources in rows.values():
            metrics.append({k: x for k, x in (
                ('name', sentence(d['name'])), ('description', _cut(d['meaning'], 320)), ('rule', False),
                ('sources', [_source(s, known, said) for s in sources]), ('status', 'draft'), ('stage', 'draft'))
                if x not in (None, '', [])})
            for s in sources:
                built_on.setdefault(s.get('vq_id'), []).append(metrics[-1]['name'])
    for m in metrics:
        # Every metric carries its sources, an empty list when the engine named none.
        m.setdefault('sources', [])
    _mark_kept(f, metrics)
    _mark_related(f, metrics)
    # Metrics from the dashboard first, then by how much evidence each has.
    metrics.sort(key=lambda m: (not any(s['kind'] == 'tile' for s in m.get('sources') or []), -len(m.get('sources') or [])))
    queries = []
    for q in f.file('context', 'queries.json', kind=list):
        if isinstance(q, dict) and q.get('name'):
            d = q.get('definition') if isinstance(q.get('definition'), dict) else {}
            ext = ((d.get('extensions') or {}).get('metrics_agent') or {}) if isinstance(d.get('extensions'), dict) else {}
            vid = ext.get('vq_id') if isinstance(ext, dict) else None
            v = verdicts.get('sql_query:' + q['name'])
            evidence = ext.get('evidence') if isinstance(ext, dict) and isinstance(ext.get('evidence'), dict) else {}
            queries.append({k: x for k, x in (
                ('id', vid or q['name']),
                ('name', sentence(first_question(q.get('display_name') or q['name']))),
                ('question', _cut(first_question(d.get('question') or q.get('display_name')), 200)),
                ('status', ('checked' if v['ok'] else 'look') if v else 'look' if over else waiting if checking else 'drafted'),
                ('why', v['why'] if v and not v['ok'] else NEVER_RAN if over and not v else None),
                ('value', _checked_value(v)),
                *_query_profile(vid, known, said, built_on, description=q.get('description'), sql=d.get('sql'),
                                tables=evidence.get('tables'), review=d.get('review_status', 'not_recorded'))) if x})
    if not queries:
        # Before the checks, the engine's own queries as it writes them, named as the checked context names them.
        for vid, (o, words) in known.items():
            question = _cut(first_question(words.get('question') or o.get('question')), 200)
            if question:
                queries.append({k: x for k, x in (('id', vid), ('name', sentence(question)), ('question', question),
                                                  ('status', 'drafted'), *_query_profile(vid, known, said, built_on)) if x})
    # A query the save kept back is left out of the saved context: it shows here with why, as a kept-back metric does.
    holds = f.file('context', 'publication-holds.json')
    shown = {q['id'] for q in queries}
    for h in holds.get('queries') or [] if isinstance(holds, dict) else []:
        vid = h.get('query_id') if isinstance(h, dict) else None
        if isinstance(vid, str) and vid not in shown:
            o, words = known.get(vid) or ({}, said.get(vid) or {})
            question = _cut(first_question(words.get('question') or o.get('question')), 200)
            queries.append({k: x for k, x in (
                ('id', vid), ('name', sentence(question or h.get('name') or vid)), ('question', question),
                ('status', 'kept'), ('kept_reason', KEPT.get(str(h.get('reason')), KEPT_ANY)),
                *_query_profile(vid, known, said, built_on)) if x})
            shown.add(vid)
    return {'metrics': metrics, 'queries': queries}


def _work(f):
    """The piece of the build under way, from the engine's own record of the stages it has passed."""
    passed = [s for s in f.stages if s in GROUP_OF]
    if f.context:
        return 'check'
    return GROUP_OF[passed[-1]] if passed else None


def _demo_facts(run):
    """The demo's own facts file, read as `visual connect --data` would have recorded it."""
    import run_facts
    facts = run_facts.demo_facts(run)
    return {**{k: facts[k] for k in ('firm',) if facts.get(k)}, **(facts.get('connect') or {})}


def snapshot(run, engine_dir=None):
    """The whole page as one document, from the run's current files. In every flow the page follows metric generation
    only: each draft as it lands and where the build has got to. The steps before it, the checks, the save, the answers
    and the dashboard are the chat's."""
    import side_view
    run = Path(run)
    f = RunFacts(run, engine_dir)
    flow = f.interaction.get('flow') if f.interaction.get('flow') in ('demo', 'dashboard') else 'build'
    demo = flow == 'demo'
    # Only the run's journey record makes a demo, so a demo's facts never reach own data.
    facts = (read_json(run / 'view' / 'facts.json', dict, {}) or _demo_facts(run)) if demo else {}
    state = side_view.stopped(run, engine_dir)
    d = details(f)
    return {
        'v': 1, 'flow': flow, 'updated': _now(),
        'title': facts.get('title') or (f.use_case and f.use_case[:1].upper() + f.use_case[1:]) or 'Analytics setup',
        'subtitle': facts.get('tagline') or f.purpose or None,
        'firm': facts.get('firm'),
        **({'state': state} if state else {}),
        # A metric's profile says where its data lives and where it was kept.
        'warehouse': f.warehouse,
        'saved': ({'workspace': f.publication.get('workspace')}
                  if f.publication and f.interaction.get('mode') != 'local' else None),
        **d,
        # The page's checklist, the drafts as they land, and the tiles the build could not turn into queries.
        'build': checkpoints(f, d), 'drafts': drafts_list(f, d['metrics']), 'cant': cant_build(f),
    }


# The server -------------------------------------------------------------------------------------

def _fingerprint(run):
    """What changed since the page last looked: the run's files' sizes and times, which is cheap to read every second."""
    run = Path(run)
    h = hashlib.sha256()
    for folder in (run, run / 'context', run / 'engine', run / 'view', *(run / 'engine' / 'answers' / g for g in BATCHED)):
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_file() and not e.name.startswith('.') and e.name != 'seen.json':
                    st = e.stat()
                    h.update(('%s/%s %d %d\n' % (folder.name, e.name, st.st_size, st.st_mtime_ns)).encode())
            except OSError:  # removed while the folder was read, such as a record rewritten
                continue
    engine = RunFacts(run).engine
    try:
        listed = sorted(os.scandir(engine), key=lambda e: e.name) if engine != run / 'engine' and engine.is_dir() else []
    except OSError:  # removed while the run was read
        listed = []
    if listed:
        for folder in (engine, *(engine / 'answers' / g for g in BATCHED)):
            try:
                entries = listed if folder == engine else sorted(os.scandir(folder), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_file():
                        st = e.stat()
                        h.update(('%s/%s %d %d\n' % (folder.name, e.name, st.st_size, st.st_mtime_ns)).encode())
                except OSError:
                    continue
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
                        data = None
                    if data is not None:
                        last = mark
                        self.wfile.write(('data: %s\n\n' % data).encode('utf-8'))
                        self.wfile.flush()
                        quiet = time.time()
                # Also while the run cannot be read: a write is how a closed tab is noticed.
                if time.time() - quiet > KEEPALIVE:
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
            pid = os.posix_spawn(args[0], args, dict(os.environ), setsid=True, file_actions=[
                (os.POSIX_SPAWN_DUP2, read, 0), (os.POSIX_SPAWN_DUP2, quiet, 1), (os.POSIX_SPAWN_DUP2, quiet, 2),
                (os.POSIX_SPAWN_CLOSE, write)])
            os.write(write, (key + '\n').encode('ascii'))
        finally:
            for fd in (read, write, quiet):
                os.close(fd)
        return pid
    spawn = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    spawn.stdin.write((key + '\n').encode('ascii'))
    spawn.stdin.close()
    return spawn.pid


def serve(run, port=0, key=None):
    """Hold the page until nobody has looked at it for a while, or for the longest a build takes."""
    srv = _Server(run, port, key)
    record = srv.run / 'view' / 'server.json'
    url = 'http://127.0.0.1:%d/%s/' % (srv.port, srv.key)
    _write(record, json.dumps({'url': url, 'pid': os.getpid(), 'started': _now()}))
    # Up: no later call waits for it to start.
    (srv.run / 'view' / 'starting.json').unlink(missing_ok=True)
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
    """Whether this process is the run's page server, so nothing else is ever signalled or waited on. None where the
    machine has no process list to tell (Windows)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        line = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # The run's own server: its exact folder, then its port, so run_1 never matches run_10's server.
    return 'side_page.py serve %s ' % Path(run).resolve() in line


def _starting(record):
    """Whether a record names a page still starting: written in the last minute by a process that still runs. Only ever
    used to wait, never to signal."""
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['started'])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= age < 60 and _alive(record.get('pid'))


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        # Never os.kill here: on Windows any signal but Ctrl+C ends the process.
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def running(run):
    """The page's address while its server answers, else None."""
    record = read_json(Path(run) / 'view' / 'server.json', dict, {})
    url = record.get('url')
    return url if url and _answers(url) else None


def open_page(run, host=None, explicit=True):
    """Start the run's page, or find the one already serving it. Returns its address, and whether this call opened it in
    the system browser."""
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
            ours = _ours(record.get('pid'), run)
            starting = read_json(run / 'view' / 'starting.json', dict, {})
            if (ours is False or ours is None and not _starting(record)) and not _starting(starting):
                # One server per run: a page still starting is waited for, never started again.
                (run / 'view' / 'server.json').unlink(missing_ok=True)
                pid = _spawn([sys.executable, str(Path(__file__).resolve()), 'serve', str(run.resolve()), last.group(1) if last else '0'],
                             last.group(2) if last else secrets.token_urlsafe(18))
                _write(run / 'view' / 'starting.json', json.dumps({'pid': pid, 'started': _now()}))
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
    return {'url': url, 'started': started, 'opened': opened, 'moved': moved, 'host': host}


def instruction(opened, widget=False):
    """What the agent does with the page it was handed: open it where the host has a pane, or name it once."""
    url = opened.get('url')
    if not url:
        return ('The side page could not start on this machine: carry on. Tell the person once, in your next message, in '
                "one short line: \"The page beside the chat couldn't start on this machine, so this run shows its "
                'results in the chat only." The chips and cards still show each moment.')
    follows = 'It follows the run by itself from the run\'s own files: never reload, rebuild or push to it.'
    if opened.get('host') == 'claude-desktop' or widget:
        return ('Open the side page now in the app\'s browser pane: your browser tool\'s preview or navigate call with url '
                '`%s`, then bring that pane into view beside the chat (the app\'s show-pane call, pane `preview`): loading '
                'a page does not show a hidden pane. Then check with the app\'s layout call that `preview` is among the '
                'open panes, and show it again if not: the page reports itself visible even in a closed pane, so only the '
                'layout can tell. %s Say nothing about it in the chat; the cards point to it. From here the page carries each '
                'metric step and the task list carries the build: follow the build with `PROJECT drafts --wait` in the '
                'foreground, as progress says.' % (url, follows))
    if opened.get('opened'):
        return ('The side page opened in the person\'s browser at %s. Say nothing about it now: the next chip in a '
                'question names it. %s' % (url, follows))
    return ('Say nothing about the side page now: the next chip in a question names its address, %s. %s' % (url, follows))


def reminder(run, start=True):
    """One line for the next helper result when the page needs the agent: it stopped and came back at a new address,
    or, in the desktop app, nobody has opened it yet. None once all is well, or before the run first opened it. Only a
    moment starts a page that stopped (start): any other helper call would wait for it and undo its idle stop."""
    run = Path(run)
    moments = read_json(run / 'view' / 'state.json', dict, {}).get('moments') or []
    if not any(isinstance(m, dict) and m.get('kind') == 'connect' for m in moments) or (run / 'view' / 'stopped.json').is_file():
        return None
    opened = {'url': running(run)}
    if not opened['url']:
        if not start:
            return None
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
    Path(run, 'view', 'starting.json').unlink(missing_ok=True)
    # Stopped on purpose: no helper call starts it again until the page is asked for.
    _write(Path(run) / 'view' / 'stopped.json', json.dumps({'at': _now()}))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'serve':
        serve(sys.argv[2], int(sys.argv[3]), sys.stdin.readline().strip())
    else:
        sys.exit('usage: side_page.py serve <run-dir> <port>')
