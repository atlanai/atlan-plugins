"""A moment of a run in the chat: a one-line chip, or a mini card at the four big moments.

`build_spec` gathers the moment's facts: the moment's data file and a demo's facts file first, then what the
run's own files prove (run_facts.py), which always wins. `render` turns them into what the chat shows: as HTML
for a host's inline visual tool, or as plain reply text a terminal prints as written, in its own colours: a
chip on one line, a card as a bold headline over indented lines. The agent passes the output through; it never
designs or retypes a number.
"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

import visual_kit as k
from run_facts import RunFacts, count, number, read_json, sentence, table_name, their, whole  # noqa: F401 (re-exported)

KINDS = ('connect', 'found', 'sources', 'group', 'tiles', 'described', 'metrics', 'rules', 'links', 'build', 'context', 'saved',
         'answer', 'layout', 'built', 'edit')
# The moments big enough for a mini card. A later answer, and a context kept on this machine, get a chip.
CARDS = ('build', 'saved', 'answer', 'edit')
# In a terminal, the moments that come right before a native question, and where in it their lines go. A question's own
# text is always shown; text written just before it folds into a one-line summary or is left out (live runs, 2026-11-12).
ASKED = {'found': "the \"Build it?\" question's text, first",
         'context': "the save question's text in place of its count line (the banner, the two lines under it and the "
                    "question stay as they are)",
         'saved': "the ready question's text, first",
         'layout': "the text of the question that confirms the layout, first (when this turn also answers a question, the plain "
                   "lines after the answer)"}
# A demo reads its sources right before choosing what to build from; own data reads them mid-build.
ASKED_DEMO = {'sources': "the next native question's text, first"}
# A rule changed on a context saved to Atlan Trident is followed by the question that saves the change.
ASKED_SAVED = {'edit': "the text of the question that asks to save the change, first, in place of the before and after block"}


def asked(kind, demo, saved=False):
    """Where a terminal moment's lines go when a native question follows it, or None for reply text."""
    return ASKED.get(kind) or (ASKED_DEMO.get(kind) if demo else None) or (ASKED_SAVED.get(kind) if saved else None)
# The moments whose data the agent writes, the fields each needs, and the shape of every field.
DATA = {
    'saved': {'workspace': str},
    'answer': {'question': str, 'headline': str, 'summary': str, 'unit': str, 'highlight': str, 'rule': str,
               'rows': 'rows', 'proof': 'names', 'breakdown': dict},
    'layout': {'tiles': 'labels', 'more': 'labels'},
    'edit': {'metric': str, 'before': str, 'after': str, 'note': str, 'values': 'values', 'added': 'labels', 'removed': 'labels'},
    'built': {'file': str},
}
REQUIRED = {'saved': ('workspace',), 'answer': ('question', 'headline'), 'layout': ('tiles',), 'edit': ('metric', 'values'),
            'built': ('file',)}
SHAPES = {'rows': '[label, number] pairs', 'names': 'a list of {"name": ...}', 'labels': 'a list of names',
          'values': 'a list of [name, before, after]', str: 'text', dict: 'an object'}
# A text card's inside width: it fits an 80-column window with room to spare, and a split pane.
WIDTH = 56
RINGS = '○◔◑◕●'
SPARKS = '▁▂▃▄▅▆▇█'
# As many rows as an answer's data file may hold, so a card never hides a row the answer is about.
BARS = 12
# Metric names a build card lists before it says how many more: the metric an answer uses is usually among them.
NAMES = 8


# The moment's facts -----------------------------------------------------------------------------

def _shaped(value, shape):
    if isinstance(shape, type):
        return isinstance(value, shape)
    if shape == 'rows':
        return isinstance(value, list) and all(isinstance(r, list) and len(r) >= 2 and isinstance(r[0], str) and isinstance(r[1], (int, float))
                                               and not isinstance(r[1], bool) and math.isfinite(r[1]) for r in value)
    if shape == 'names':
        return isinstance(value, list) and all(isinstance(p, dict) and isinstance(p.get('name'), str) for p in value)
    if shape == 'labels':
        return isinstance(value, str) or isinstance(value, list) and all(isinstance(x, (str, list)) for x in value)
    return isinstance(value, list) and all(isinstance(r, list) and len(r) >= 3 for r in value)


def check_data(kind, data):
    """Why a moment's data file cannot be used, naming the field, or None when it is fine."""
    if kind not in DATA:
        return 'Only the %s moments take a data file.' % ', '.join(DATA)
    if not isinstance(data, dict):
        return "A moment's data is one JSON object."
    for field in REQUIRED[kind]:
        if not data.get(field):
            return '%s.json needs `%s`.' % (kind, field)
    for field, shape in DATA[kind].items():
        if field in data and not _shaped(data[field], shape):
            return '%s.json: `%s` must be %s.' % (kind, field, SHAPES[shape])
    return None


def _names(value):
    """A list of metric names; a sentence such as 'A, B and C' reads as its names."""
    if isinstance(value, str):
        return [n.strip() for n in value.replace(' and ', ',').split(',') if n.strip()]
    return [v for v in value or [] if isinstance(v, str) or isinstance(v, list) and v]


def collect(kind, facts):
    """What the run's own files say about a moment."""
    if kind == 'found' and facts.found[0]:
        # What the survey and the warehouse connection found, right before the person chooses what to build from.
        return {'found': facts.found[0], 'kinds': facts.found[1]}
    if kind == 'sources' and facts.tables:
        # Only what the person chose to build from: a history file left out of the choice is not read.
        chosen = facts.inputs or {'bi': True, 'history': True}
        return {'tables': [list(t) for t in facts.tables], **({'history': facts.history} if facts.history and chosen['history'] else {}),
                **({'dashboard': facts.dashboard} if facts.dashboard and chosen['bi'] else {})}
    if kind == 'sources' and facts.warehouse and facts.history and facts.scoped_tables and (facts.inputs or {'history': True})['history']:
        # A warehouse is read in place: its past queries and the chosen tables, nothing loaded here.
        return {'warehouse': facts.warehouse, 'history': facts.history, 'scoped': facts.scoped_tables}
    # Built means the engine has finished, its last stage recorded or the checks begun: metrics merged mid-build are
    # not the build's result yet, and its time would be short.
    if kind == 'build' and facts.funnel.get('metrics') and ('x4_ingest' in facts.stages or facts.context):
        return {'funnel': facts.funnel, 'merged': [m['name'] for m in facts.merged], 'evidence': facts.merged, 'took': _took(facts)}
    # The checks are over only when every item has its verdict: a chip before that would count unrun items as needing a look.
    if kind == 'context' and facts.checks_over:
        c = facts.context
        # One checked value as proof: a metric the dashboard shows, else the first that ran.
        shown = next((m for m in c['metrics'] if m['ok'] and m['origin'] == 'tile'), None) or next((m for m in c['metrics'] if m['ok']), None)
        return {'ran': c['passed'], 'total': c['total'], 'need_look': c['need_look'], **({'warehouse': facts.warehouse} if facts.warehouse else {}),
                **({'featured': [shown['name'], shown['value']]} if shown else {})}
    if kind == 'saved':
        c = facts.context
        # Counted only where the run holds every verdict it will get: never a warehouse's own checks.
        checked = {'kept': c['passed'], 'total': c['total'], 'need_look': c['need_look']} if c and facts.checks_over else {}
        if facts.local:
            return {'local': True, **checked}
        featured = next((m for m in c.get('metrics', []) if m['ok'] and m['origin'] == 'tile'), None) if c else None
        return {**dict(facts.publication), **checked, 'evidence': facts.merged,
                **({'featured': [featured['name'], featured['value']]} if featured else {})}
    if kind in ('answer', 'edit'):
        return {'evidence': facts.merged}
    chosen = facts.inputs or {'bi': True, 'history': True}
    if kind == 'group' and chosen['history'] and facts.stage('h1_shapes', 'canonical_shapes'):
        return {'questions': facts.stage('h1_shapes', 'canonical_shapes'), 'read': facts.stage('h0_corpus', 'kept')}
    if kind == 'tiles' and chosen['bi']:
        # The dashboard's tiles, each written up as a question: the engine's file for them appears once they are.
        tiles = [o for o in facts.engine_file('verified_queries.bi.json').get('objects') or [] if isinstance(o, dict)]
        bi = [((o.get('provenance') if isinstance(o.get('provenance'), dict) else {}).get('bi') or {}) for o in tiles]
        if tiles:
            # Its page in the dashboard, kept apart from `page`, which is the side page's own address.
            return {'tiles': len(tiles), 'bi_page': next((str(b['page']) for b in bi if isinstance(b, dict) and b.get('page')), None)}
    if kind == 'described' and 'm2_ingest' in facts.stages:
        return _described(facts)
    if kind == 'metrics' and 'x2_ingest' in facts.stages and facts.merged:
        return {'metrics': [m['name'] for m in facts.merged]}
    if kind == 'rules' and 'x3_ingest' in facts.stages:
        rule = next(((m['name'], r) for m in facts.merged for r in [_rule(facts, m['name'])] if r), None)
        return {'metric': rule[0], 'rule': rule[1]} if rule else {}
    if kind == 'links' and 'x4_ingest' in facts.stages:
        edges = [x for x in facts.engine_file('metric_relationships.json').get('edges') or []
                 if isinstance(x, dict) and x.get('from') and x.get('to')]
        return {'links': len(edges), 'example': _link(edges)} if edges else {}
    if kind == 'built':
        return {'tiles': len(_names(facts.file('layout.json').get('tiles')))}
    return {}


def _described(facts):
    """The question past queries asked most, in the build's own plain words, and how many it wrote."""
    said = facts.engine_file('sql_descriptions.json').get('descriptions') or {}
    if isinstance(said, list):
        said = {str(x.get('vq_id')): x for x in said if isinstance(x, dict)}
    if not isinstance(said, dict):
        return {}
    words = [(vid, str(v.get('question') or '').strip()) for vid, v in said.items() if isinstance(v, dict)]
    asked = [(vid, q) for vid, q in words if q.endswith('?')]
    if not asked:
        return {}
    hist = {o.get('vq_id'): o for o in facts.engine_file('verified_queries.history.json').get('objects') or [] if isinstance(o, dict)}
    def runs(vid):
        prov = (hist.get(vid) or {}).get('provenance')
        history = prov.get('history') if isinstance(prov, dict) else None
        return whole(history.get('n_exec'), 0) if isinstance(history, dict) else 0

    # A chip's one line holds a question of about 46 characters with its count, 70 alone: the one shown fits whole,
    # the most asked among those that do.
    pick = max(asked, key=lambda x: (len(x[1]) <= 46, len(x[1]) <= 70, runs(x[0]), -len(x[1])))
    return {'question': pick[1], 'described': len(words)}


def _rule(facts, name):
    """A metric's rule in plain words: its card's first sentence, the part after a colon. The engine writes its cards file
    with the links; right after the rules it holds them in the next gate's prompt."""
    cards = facts.engine_file('metric_cards.json')
    cards = cards.get('cards') or cards.get('metrics') or [] if isinstance(cards, dict) else []
    texts = [(c.get('name'), c.get('description')) for c in cards if isinstance(c, dict)]
    texts += [(c.get('metric'), c.get('description')) for c in facts.engine_file('x4_prompt.json').get('all_metrics') or [] if isinstance(c, dict)]
    text = next((str(d) for n, d in texts if str(n or '').lower() == name.lower() and d), '')
    # A sentence ends where the next begins with a capital: "excl. cancelled jobs" is one sentence.
    first = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip(), maxsplit=1)[0].rstrip('.')
    return first.split(': ', 1)[1] if ': ' in first else first


def _link(edges):
    """One link, read as what it is: a ratio of two metrics, a metric that is part of another, or one that uses others."""
    named = lambda x: sentence(str(x))
    for source in dict.fromkeys(x['from'] for x in edges):
        roles = {x.get('role'): x['to'] for x in edges if x['from'] == source}
        if 'numerator' in roles and 'denominator' in roles:
            return '%s = %s ÷ %s' % (named(source), named(roles['numerator']), named(roles['denominator']))
    narrower = next((x for x in edges if x.get('relation') == 'restriction'), None)
    if narrower:
        return '%s is part of %s' % (named(narrower['from']), named(narrower['to']))
    source = edges[0]['from']
    return '%s uses %s' % (named(source), ' and '.join(named(x['to']) for x in edges if x['from'] == source))


def _took(facts):
    """How long the build took, from the engine's own record of its first and last stage, in whole minutes."""
    from datetime import datetime
    stages = [s for s in facts.engine_file('manifest.json').get('stages') or [] if isinstance(s, dict) and s.get('started_at')]
    try:
        first = datetime.fromisoformat(str(stages[0]['started_at']).replace('Z', '+00:00'))
        last = datetime.fromisoformat(str(stages[-1]['started_at']).replace('Z', '+00:00'))
    except (IndexError, ValueError):
        return None
    seconds = (last - first).total_seconds() + float(stages[-1].get('duration_s') or 0)
    return max(1, round(seconds / 60)) if seconds > 0 else None


def page_link(run, kind, spec):
    """Where the side page shows this moment in full: the run's page, opened at the metric the moment is about."""
    from urllib.parse import quote
    url = read_json(Path(run) / 'view' / 'server.json', dict, {}).get('url') if run else None
    if not url:
        return None
    name = ((spec.get('proof') or [{}])[0] or {}).get('name') if kind == 'answer' else spec.get('metric') if kind == 'edit' else None
    if name:
        return url + '#m=' + quote(sentence(name))
    return url + '#step=metrics' if kind in ('build', 'context') else url


def build_spec(kind, run=None, data=None, engine_dir=None):
    """The moment's facts: its data file, then supplied facts such as a demo's, then what the run's files prove.

    A demo's facts file holds its firm and a section per kind. Counts that a receipt proves always win."""
    supplied = {}
    # A demo's facts file is for a demo run only: on own data it would put the demo's names on the person's page.
    demo_run = not run or read_json(Path(run) / 'interaction.json', dict, {}).get('flow') == 'demo'
    if run and demo_run and not data:
        # A call that left out the demo's facts still gets them, from the demo's own working folder.
        from run_facts import demo_facts
        data = [demo_facts(run)]
    if not demo_run:
        # Own data takes no facts from outside the run: any passed would put another firm's words on it.
        data = []
    for item in (data if isinstance(data, list) else [data] if data else []):
        if not isinstance(item, dict):
            continue
        if any(key in item for key in KINDS):
            if not demo_run:
                continue
            item = {**{key: item[key] for key in ('firm', 'flow') if key in item}, **(item.get(kind) if isinstance(item.get(kind), dict) else {})}
        supplied.update(item)
    facts = RunFacts(run, engine_dir) if run else None
    own = read_json(Path(run) / (kind + '.json'), dict, {}) if run and kind in DATA else {}
    spec = {**(own if not check_data(kind, own) else {}), **supplied, **(collect(kind, facts) if facts else {})}
    page = page_link(run, kind, spec)
    if page:
        spec['page'] = page
    if kind == 'saved' and facts and not spec.get('local'):
        spec['workspace'] = spec.get('workspace') or own.get('workspace')
    if kind == 'layout':
        values = {m['name'].lower(): m['value'] for m in (facts.context.get('metrics', []) if facts else [])}
        fill = lambda t: t if isinstance(t, list) else [t, values.get(sentence(t).lower(), 'blank')]
        spec['tiles'] = [fill(t) for t in _names(spec.get('tiles'))]
        spec['more'] = [fill(t) for t in _names(spec.get('more'))]
        # The names below the tiles are metrics, as the data file asks, unless one is a query the context holds: the agent
        # can list a breakdown or a chart there.
        key = lambda t: re.sub(r'[\s_-]+', ' ', str(t)).strip().lower()
        named = lambda file: {key(x.get(k)) for x in (facts.file('context', file, kind=list) if facts else [])
                              if isinstance(x, dict) for k in ('name', 'display_name') if x.get(k)}
        metrics, queries = named('metrics.json'), named('queries.json')
        kinds = ['query' if key(t[0]) in queries and key(t[0]) not in metrics else 'metric' for t in spec['more']]
        spec['below'] = [count(kinds.count(k), *words) for k, words in (('metric', ('more metric',)), ('query', ('query', 'queries')))
                         if kinds.count(k)]
    if kind == 'answer' and facts and not spec.get('unit'):
        # An answer that names no unit reads in the unit of the rule it used first.
        first = next((p.get('name') for p in spec.get('proof') or [] if isinstance(p, dict) and p.get('name')), None)
        unit = facts.unit_for(first) if first else None
        if unit:
            spec['unit'] = unit
    if kind == 'answer' and str(spec.get('unit') or '').lower() == 'currency':
        # A rule recorded as a bare currency: the answer's own headline says which.
        sign = re.search(r'[£$€]', str(spec.get('headline') or '').rpartition(': ')[2])
        if sign:
            spec['unit'] = {'£': 'GBP', '$': 'USD', '€': 'EUR'}[sign.group(0)]
    if kind == 'answer' and facts and str(spec.get('unit') or '').lower() in ('percent', 'percentage', '%'):
        # The answer's own metric (its first rule) says whether a percent is a share from 0 to 1; the agent's file may
        # not. Other rules it lists can hold other units.
        first = next((p.get('name') for p in spec.get('proof') or [] if isinstance(p, dict)), None)
        own = (facts.unit_for(first) or '').lower() if first else ''
        # Unless the rows are already in percent: they hold the scale under which they sit nearest the headline's number.
        values = [r[1] for r in spec.get('rows') or [] if isinstance(r, list) and len(r) > 1 and isinstance(r[1], (int, float))]
        found = re.search(r'-?\d[\d,]*(\.\d+)?', str(spec.get('headline') or '').rpartition(': ')[2].replace('\u2212', '-'))
        head = float(found.group(0).replace(',', '')) if found else None
        near = lambda scale: min(abs(v * scale - head) for v in values)
        as_percent = (near(1) < near(100)) if values and head is not None else any(abs(v) > 1 for v in values)
        if ('fraction' in own or own in ('ratio', 'share')) and not as_percent:
            spec['unit'] = 'fraction'
    if kind == 'edit':
        unit = facts.unit_for(spec.get('metric')) if facts else None
        words = lambda v, given=None: number(v, given or unit) if isinstance(v, (int, float)) else str(v)
        # A row that names a metric reads in that metric's unit: one edit can change several rules.
        own = lambda row: (facts.unit_for(row[0]) if facts and row and isinstance(row[0], str) else None) or (row[3] if len(row) > 3 else None)
        values = [[words(x, own(row)) for x in row[:3]] for row in spec.get('values', []) if isinstance(row, list)]
        spec['values'] = [v for v in values if len(v) < 3 or v[1] != v[2]] or values
        for key in ('added', 'removed'):
            spec[key] = [', '.join(words(x) for x in i) if isinstance(i, list) else words(i) for i in _names(spec.get(key))]
    return spec


# Chips -----------------------------------------------------------------------------------------

def ring(done, total):
    """A progress ring in one character: empty, a quarter, a half, three quarters or full."""
    if not total:
        return RINGS[0]
    share = done / total
    return RINGS[4] if share >= 1 else RINGS[3] if share >= .75 else RINGS[2] if share >= .5 else RINGS[1] if share > 0 else RINGS[0]


def spark(values):
    """A row of bars in one line, at most eight, lowest to highest. Plain text, so it takes the reply's own colour."""
    values = [v for v in values if isinstance(v, (int, float))][:8]
    if len(values) < 2:
        return '▪'
    low, high = min(values), max(values)
    return ''.join(SPARKS[round((v - low) / (high - low) * 7) if high > low else 3] for v in values)


def _chip(glyph, num, what, tail=''):
    return '%s **%s**%s%s' % (glyph, num, ' ' + what if what else '', ' · ' + tail if tail else '')


def chip_html(kind, s):
    """The chip as a small widget for a host that draws one: the same mark, outcome and words."""
    o = _outcome(kind, s)
    if o:
        e = k.e
        hero = ('<b>%s</b>' % e(o['bold']) if o['bold'] else '') + (e(o['rest']) if not o.get('quote') else '<em>%s</em>' % e(o['rest']))
        return k.outcome(o['mark'] + '<span class="h">%s</span>%s' % (hero, '<span class="t">%s</span>' % e(o['tail']) if o['tail'] else ''),
                         ' '.join(x for x in (o['bold'], o['rest'], o['tail']) if x))
    t = _ticker(kind, s)
    if not t:
        return ''
    e = k.e
    values = [v for v in (t.get('values') or []) if isinstance(v, (int, float))][:10]
    most = max(abs(v) for v in values) or 1 if values else 1
    glyph = '<span class="sk" aria-hidden="true">%s</span>' % ''.join(
        '<i class="%s" style="height:%dpx"></i>' % ('n' if v < 0 else '', max(2, round(abs(v) / most * 16))) for v in values)
    words = ' · '.join(x for x in (t['what'], t['tail']) if x)
    return k.ticker(glyph + '<span class="n">%s</span><span class="d">%s</span>' % (e(_minus(t['num'])), e(words)),
                    '%s %s' % (_minus(t['num']), words))


def _headline(s):
    """An answer's headline as (what, number): 'Turbine base: -£6,360' reads as both."""
    name, sep, value = str(s['headline']).rpartition(': ')
    return (name if sep else s.get('highlight') or ''), value


def _ticker(kind, s):
    """A later answer's chip: the values its spark draws, the number, then what it is."""
    if kind == 'answer' and s.get('headline'):
        label, value = _headline(s)
        return {'values': [r[1] for r in s.get('rows') or [] if isinstance(r, list) and len(r) > 1], 'num': value, 'what': '', 'tail': label}
    return None


# Each step's own mark in a terminal, and the name of its drawing in a widget.
MARKS = {'found': '◇', 'sources': '▤', 'group': '»', 'tiles': '▦', 'described': '❝', 'metrics': '◆', 'rules': '=', 'links': '⋈',
         'ok': '✓', 'look': '△', 'saved': '⌂', 'layout': '▭', 'built': '▣'}
FOUND_MARKS = {'dashboard': 'dashboard', 'database': 'database', 'query history': 'history'}


def _outcome(kind, s):
    """What a step produced, as its mark, the outcome in bold, the rest of it, and a short tail. Never progress: the side
    page shows how far the run has got. None when the step has produced nothing yet."""
    o = None
    if kind == 'found' and s.get('found'):
        kinds = s.get('kinds') or []
        drawn = [next((v for key, v in FOUND_MARKS.items() if key in str(x)), 'file') for x in kinds][:4]
        o = {'mark': k.marks(drawn or ['file']), 'bold': count(s['found'], 'source'), 'tail': ', '.join(kinds)}
    elif kind == 'sources' and s.get('warehouse') and s.get('history'):
        o = {'mark': 'sources', 'bold': count(s['history']['statements'], 'past query', 'past queries'), 'rest': ' read',
             'tail': '%s, %s' % (s['warehouse'], count(s['scoped'], 'table'))}
    elif kind == 'sources' and s.get('tables'):
        tables = s['tables']
        tool = (s.get('dashboard') or {}).get('tool')
        read = [count(len(tables), 'table')] + ([count(s['history']['statements'], 'past query', 'past queries')] if s.get('history') else []) + (
            ['%s dashboard' % tool] if tool else [])
        if all(t[1] is not None for t in tables):
            o = {'mark': 'sources', 'bold': '{:,} rows'.format(sum(t[1] for t in tables)),
                 'rest': ' of %s data' % their(s.get('firm')), 'tail': ', '.join(read)}
        else:
            o = {'mark': 'sources', 'bold': count(len(tables), 'table'), 'rest': ' loaded', 'tail': ', '.join(read[1:])}
    elif kind == 'group' and s.get('questions'):
        o = {'mark': 'group', 'bold': count(s['questions'], 'different question'),
             'tail': 'from %s' % count(s['read'], 'past query', 'past queries') if s.get('read') else ''}
    elif kind == 'tiles' and s.get('tiles'):
        o = {'mark': k.mark('tiles', min(s['tiles'], 9), min(s['tiles'], 3)), 'bold': count(s['tiles'], 'dashboard tile'),
             'tail': ', '.join(x for x in (s.get('bi_page'), 'each now a question') if x)}
    elif kind == 'described' and s.get('question'):
        more = s.get('described', 1) - 1
        o = {'mark': 'described', 'bold': '', 'rest': '“%s”' % s['question'], 'quote': True,
             'tail': '+%d more in plain words' % more if more > 0 else 'in plain words'}
    elif kind == 'metrics' and s.get('metrics'):
        names = s['metrics']
        o = {'mark': 'metrics', 'bold': count(len(names), 'metric'),
             'tail': ', '.join(names[:3]) + (' +%d' % (len(names) - 3) if len(names) > 3 else '')}
    elif kind == 'rules' and s.get('rule'):
        o = {'mark': 'rules', 'bold': s['metric'], 'tail': _fit(s['rule'], 100)}
    elif kind == 'links' and s.get('links'):
        o = {'mark': 'links', 'bold': count(s['links'], 'link'), 'tail': s.get('example') or ''}
    elif kind == 'context' and s.get('total'):
        where = 'on %s data' % their(s.get('firm')) if s.get('firm') or not s.get('warehouse') else 'on your %s data' % s['warehouse']
        if s.get('need_look') and s.get('ran'):
            o = {'mark': 'look', 'bold': '%d ran cleanly' % s['ran'], 'tail': '%d need a look · %s' % (s['need_look'], where)}
        elif s.get('need_look'):
            o = {'mark': 'look', 'bold': '%d need a look' % s['need_look'], 'tail': where}
        else:
            shown = s.get('featured')
            o = {'mark': 'ok', 'bold': 'Every metric and query ran',
                 'tail': where + (' · %s %s' % (shown[0], _minus(shown[1])) if shown and shown[1] not in (None, 'not run') else '')}
    elif kind == 'saved' and s.get('local'):
        o = {'mark': 'saved', 'bold': 'Kept on this machine', 'tail': 'nothing sent to Atlan'}
    elif kind == 'layout' and s.get('tiles'):
        names = [sentence(t[0] if isinstance(t, list) else t) for t in s['tiles']]
        o = {'mark': k.mark('layout', min(len(names), 6), min(len(names), 6)), 'bold': count(len(names), 'tile'), 'rest': ' laid out',
             # What sits below the tiles first: a fitted line gives up the names, never that.
             'tail': ('%s below · ' % ' and '.join(s['below']) if s.get('below') else '') + ', '.join(names[:2]) + (
                 ' +%d' % (len(names) - 2) if len(names) > 2 else '')}
    elif kind == 'built' and s.get('file'):
        o = {'mark': 'built', 'bold': s['file'], 'tail': '%s · opens in your browser' % (
            count(s['tiles'], 'tile') if s.get('tiles') else 'dashboard')}
    if not o:
        return None
    name = o['mark'] if o['mark'] in MARKS else None
    return {'text_mark': MARKS[name] if name else MARKS[kind if kind in MARKS else 'found'],
            'mark': k.mark(name) if name else o['mark'], 'bold': o['bold'], 'rest': o.get('rest', ''), 'tail': o.get('tail', ''),
            'quote': o.get('quote', False)}


# A chip's line in a terminal, as wide as a card: one line in an 80-column window.
LINE = 76


def _fit_words(text, width):
    """Text cut to a width at a word, never inside one."""
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    space = cut.rfind(' ')
    return (cut[:space] if space > width // 2 else cut).rstrip(' ,;:·') + '…'


def _fit_line(o):
    """The chip's words fitted to one line: the tail gives way first, then the outcome itself."""
    bold, rest, tail = o['bold'], o['rest'], o['tail']
    head = len(o['text_mark']) + 1 + len(bold) + len(rest)
    if tail and head + 3 + len(tail) > LINE:
        room = LINE - head - 3
        tail = _fit_words(tail, room) if room >= 12 else ''
    over = head - LINE
    if over > 0 and o['quote']:
        inner = rest[1:-1]
        rest = '“%s”' % _fit_words(inner, max(8, len(inner) - over))
    elif over > 0 and rest:
        rest = _fit_words(rest, max(8, len(rest) - over))
    elif over > 0:
        bold = _fit_words(bold, max(8, len(bold) - over))
    return bold, rest, tail


def chip(kind, s):
    """The moment as one line: its own mark, then what the step produced. Empty when nothing is true yet."""
    o = _outcome(kind, s)
    if o:
        bold, rest, tail = _fit_line(o)
        return '%s %s%s%s' % (o['text_mark'], '**%s**' % bold if bold else '', rest, ' · ' + tail if tail else '')
    t = _ticker(kind, s)
    if not t:
        return ''
    return _chip(spark(t['values']), t['num'], t['what'], t['tail'])


# Mini cards ------------------------------------------------------------------------------------

def _queries_line(f):
    """Where the queries came from, so the counts on the card add up on their face."""
    shown, asked = f.get('tiles', 0), f.get('questions', 0) - f.get('tiles', 0)
    if shown and asked > 0:
        return '%s: %d from the dashboard, %d from past queries' % (count(f['questions'], 'query', 'queries'), shown, asked)
    if shown:
        return '%s built from the dashboard' % count(shown, 'query', 'queries')
    return '%s built from past queries' % count(asked, 'query', 'queries') if asked > 0 else ''


def _from(e):
    """Where a metric came from, in words: 'from 2 dashboard tiles and 4 past queries'."""
    parts = [x for x in (count(e['tiles'], 'dashboard tile') if e and e.get('tiles') else '',
                         count(e['queries'], 'past query', 'past queries') if e and e.get('queries') else '') if x]
    return 'from ' + ' and '.join(parts) if parts else ''


def _evidence(s, name):
    key = sentence(name or '').lower()
    return next((m for m in s.get('evidence') or [] if m['name'].lower() == key), None)


def _where(s):
    return '%s data' % their(s['firm']) if s.get('firm') else 'your %s data' % s['warehouse'] if s.get('warehouse') else 'your data'


def _parts(kind, s):
    """What a mini card shows, in the same order for HTML and text. None when the run has nothing true to show."""
    if kind == 'build' and (s.get('funnel') or {}).get('metrics'):
        f = s['funnel']
        tiles, asked = f.get('tiles', 0), f.get('questions', 0) - f.get('tiles', 0)
        evidence = s.get('evidence') or []
        most = max(evidence, key=lambda m: m['tiles'] + m['queries'], default=None)
        return {'icon': 'metric', 'title': 'Built from your sources', 'meta': 'in %d min' % s['took'] if s.get('took') else '',
                'value': count(f['metrics'], 'metric'), 'line': _queries_line(f),
                'funnel': [r for r in (('Past queries read', f.get('queries'), [('fp', f.get('queries'))]) if f.get('queries') else None,
                                       ('Queries built', f.get('questions'), [('fq', max(asked, 0)), ('ft', tiles)]) if f.get('questions') else None,
                                       ('Metrics', f['metrics'], [('fm', f['metrics'])])) if r],
                'legend': [x for x in (('fq', '%d from past queries' % asked) if asked > 0 else None,
                                       ('ft', '%d from dashboard tiles' % tiles) if tiles else None) if x] if asked > 0 and tiles else [],
                'most': ['Most merged', most['name'], _from(most)] if most and most['tiles'] + most['queries'] > 1 else None,
                'names': s.get('merged') or [], 'marked': [m['name'] for m in evidence if m['tiles']],
                'more': ['How each metric was reached', s.get('page')]}
    if kind == 'saved' and s.get('metrics') and not s.get('local'):
        featured = s.get('featured')
        return {'icon': 'check-circle', 'title': 'Saved to Atlan Trident',
                'value': '%s, %s' % (count(len(s['metrics']), 'metric'), count(s.get('queries', 0), 'query', 'queries')),
                'line': 'Live in %s, where agents can find them' % (s.get('workspace') or 'your workspace'),
                'note': ('%d of %d checked on %s first' % (s['kept'], s['total'], _where(s))
                         + ('; %d need a look' % s['need_look'] if s.get('need_look') else '')) if s.get('total') else '',
                'kv': [[' · '.join(x for x in (featured[0], _from(_evidence(s, featured[0]))) if x), featured[1]]] if featured else [],
                'names': s['metrics'], 'marked': [m['name'] for m in s.get('evidence') or [] if m['tiles']]}
    if kind == 'answer' and s.get('headline'):
        label, value = _headline(s)
        rows = [r for r in s.get('rows') or [] if isinstance(r, list) and len(r) > 1 and isinstance(r[1], (int, float))]
        shown = rows[:BARS] + [r for r in rows[BARS:] if r[0] == s.get('highlight')][:1]
        proof = [sentence(x['name']) for x in s.get('proof') or [] if isinstance(x, dict) and x.get('name')]
        return {'icon': 'chart', 'title': 'Answer', 'pre': s.get('question', ''), 'value': value, 'neg': value.startswith('-'),
                'line': label, 'why': s.get('summary', ''),
                'bars': [{'label': str(r[0]), 'value': r[1], 'shown': number(r[1], s.get('unit')), 'mark': r[0] == s.get('highlight')}
                         for r in shown],
                'rule': [s['rule']] if s.get('rule') else [],
                'sources': [[n, _from(_evidence(s, n)) or 'a saved query'] for n in proof[:2]],
                'rest': '+%d more behind this answer' % (len(proof) - 2) if len(proof) > 2 else '',
                'more': ['Its rule and sources', s.get('page')]}
    if kind == 'edit' and s.get('values'):
        name, before, after = (s['values'][0] + ['', '', ''])[:3]
        return {'icon': 'edit', 'title': 'Rule changed', 'pre': name, 'value': '%s → %s' % (before, after),
                'ba': [['Before', before, s.get('before') or ''], ['After', after, s.get('after') or '']],
                'chips': ([('+ Now counted too: ' + ', '.join(s['added']))] if s.get('added') else [])
                + ([('- No longer counted: ' + ', '.join(s['removed']))] if s.get('removed') else []),
                'warn': s.get('note', ''), 'more': ['The rule in full', s.get('page')]}
    return None


def _minus(text):
    """A minus sign, not a hyphen, so a screen reader says 'minus'."""
    text = str(text)
    return '−' + text[1:] if text.startswith('-') else text


def _scale(bars):
    """Where zero sits, as a share of the width, and the span the width stands for: losses left of zero, gains right."""
    low = min(min(b['value'] for b in bars), 0)
    high = max(max(b['value'] for b in bars), 0)
    return -low / ((high - low) or 1), (high - low) or 1


def card_html(p):
    e = k.e
    bars = ''
    if p.get('bars'):
        zero, span = _scale(p['bars'])
        rows = []
        for b in p['bars']:
            width = max(2, round(abs(b['value']) / span * 100))
            place = 'right:%.1f%%' % (100 - zero * 100) if b['value'] < 0 else 'left:%.1f%%' % (zero * 100)
            rows.append('<div class="mr%s"><span>%s</span><span class="z"><i class="%s" style="%s;width:%d%%"></i></span><b>%s</b></div>' % (
                ' h' if b.get('mark') else '', e(b['label']), 'n' if b['value'] < 0 else 'p', place, width, e(_minus(b['shown']))))
        bars = '<div class="mb">%s</div>' % ''.join(rows)
    funnel = ''
    if p.get('funnel'):
        most = max(r[1] for r in p['funnel']) or 1
        funnel = '<div class="fn">%s</div>' % ''.join(
            '<span>%s</span><span class="bk">%s</span><b>%s</b>' % (e(label), ''.join(
                '<i class="%s" style="width:%.1f%%"></i>' % (cls, n / most * 100) for cls, n in segs if n), '{:,}'.format(n_all))
            for label, n_all, segs in p['funnel'])
    marked = set(p.get('marked') or [])
    names = p.get('names') or []
    shown = names if len(names) <= NAMES else names[:NAMES - 1]
    value = e(_minus(p['value'])).replace(' → ', ' <s>→</s> ')
    more = p.get('more')
    return k.card(
        '<div class="mh">%s<b>%s</b>%s</div>' % (k.icon(p['icon']), e(p['title']), '<span class="mx">%s</span>' % e(p['meta']) if p.get('meta') else '')
        + ('<div class="mp">%s</div>' % e(p['pre']) if p.get('pre') else '')
        + '<div class="mv%s">%s</div>' % (' neg' if p.get('neg') else '', value)
        + ('<div class="mt">%s</div>' % e(p['line']) if p.get('line') else '')
        + ('<div class="my">%s</div>' % e(p['why']) if p.get('why') else '')
        + funnel
        + ('<div class="lg">%s</div>' % ''.join('<span><i class="%s"></i>%s</span>' % (c, e(t)) for c, t in p['legend']) if p.get('legend') else '')
        + ('<div class="mt">%s: <b>%s</b>, %s</div>' % tuple(e(x) for x in p['most']) if p.get('most') else '')
        + ''.join('<div class="ba"><span>%s</span><b%s>%s</b><span>%s</span></div>' % (e(l), ' class="now"' if l == 'After' else '', e(_minus(v)), e(r))
                  for l, v, r in p.get('ba') or [])
        + bars + ''.join('<div class="mu">%s</div>' % e(r) for r in p.get('rule', []))
        + ('<div class="kv">%s</div>' % ''.join('<span>%s</span><b>%s</b>' % (e(a), e(_minus(b))) for a, b in p['kv']) if p.get('kv') else '')
        + ('<div class="kv">%s</div>' % ''.join('<span><b>%s</b></span><span>%s</span>' % (e(a), e(b)) for a, b in p['sources']) if p.get('sources') else '')
        + ('<div class="mu">%s</div>' % e(p['rest']) if p.get('rest') else '')
        + ('<div class="mu">%s</div>' % e(p['note']) if p.get('note') else '')
        + ('<div class="nm">%s%s</div>' % (''.join('<span>%s%s</span>' % ('<i></i>' if n in marked else '', e(n)) for n in shown),
                                           '<span>+%d more</span>' % (len(names) - len(shown)) if len(names) > len(shown) else '') if names else '')
        + ('<div class="mf">%s</div>' % ' · '.join('<span class="add">%s</span>' % e(c) if c.startswith('+ ') else '<span class="rem">%s</span>' % e(c)
                                                   if c.startswith('- ') else e(c) for c in p['chips']) if p.get('chips') else '')
        + ('<div class="mw">%s<span>%s</span></div>' % (k.icon('alert-circle'), e(p['warn'])) if p.get('warn') else '')
        + ('<div class="mo">%s<span>%s: on the side page beside the chat</span></div>' % (k.icon('arrow-up-right'), e(more[0])) if more else ''),
        '%s: %s' % (p['title'], _minus(p['value'])))


def _fit(text, width=WIDTH):
    text = str(text)
    return text if len(text) <= width else text[:width - 1] + '…'


def _wrap(text, width=WIDTH):
    lines, line = [], ''
    for word in str(text).split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = (line + ' ' + word).strip()
    return lines + ([line] if line else [])


def _wrap_items(items, width=WIDTH, sep=' · '):
    """Items on as few lines as fit, never splitting one that fits a line; a longer one wraps at its spaces."""
    lines, line = [], ''
    for item in items:
        if len(item) > width:
            lines += ([line] if line else []) + _wrap(item, width)
            line = ''
        elif line and len(line) + len(sep) + len(item) > width:
            lines.append(line)
            line = item
        else:
            line = line + sep + item if line else item
    return lines + ([line] if line else [])


def _text_bars(bars):
    """Bars in a fixed-width block: a mark for the answering row, the label, the bar from a zero line, the value."""
    marked = any(b.get('mark') for b in bars)
    label = min(max(len(b['label']) for b in bars), 28)
    shown = [_minus(b['shown']) for b in bars]
    num = max(len(x) for x in shown)
    cells = WIDTH - label - num - 3 - (2 if marked else 0)
    signed = any(b['value'] < 0 for b in bars)
    zero, span = _scale(bars)
    left = round((cells - 1) * zero) if signed else 0
    right = cells - left - (1 if signed else 0)
    out = []
    for b, text in zip(bars, shown):
        n = max(1, round(abs(b['value']) / span * (cells - (1 if signed else 0))))
        if not signed:
            bar = ('█' * n).ljust(cells)
        elif b['value'] < 0:
            bar = ('█' * min(n, left)).rjust(left) + '│' + ' ' * right
        else:
            bar = ' ' * left + '│' + ('█' * min(n, right)).ljust(right)
        out.append(('▶ ' if b.get('mark') else '  ' if marked else '') + '%s  %s %s' % (_fit(b['label'], label).ljust(label), bar, text.rjust(num)))
    return out


def _plain(text):
    """Text that Markdown shows as written: its own stars and backticks are escaped, and an underscore that could
    start or end emphasis. One inside a word, as in a column name, is shown as it is."""
    return re.sub(r'(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])', r'\\_', re.sub(r'([\\`*])', r'\\\1', str(text)))


def card_head(p, plain=False):
    """The card's headline: the number first, then what it is. In bold for a reply; as written for a question's text,
    which shows no Markdown."""
    lead = ' '.join(x for x in (p.get('pre') if p['icon'] == 'edit' else '', _minus(p['value'])) if x)
    title = ' · '.join(x for x in (p['title'], p.get('meta')) if x)
    return '%s · %s' % (lead, title) if plain else '**%s** · %s' % (_plain(lead), _plain(title))


def _one_line(names, width=WIDTH, sep=' · '):
    """As many names as fit one line, then how many more."""
    for n in range(len(names), 0, -1):
        line = sep.join(names[:n] + (['+%d more' % (len(names) - n)] if n < len(names) else []))
        if len(line) <= width:
            return line
    return '%d metrics' % len(names)


SEGMENTS = {'fp': '█', 'fq': '▓', 'ft': '░', 'fm': '█'}


def _text_funnel(funnel, legend):
    """The funnel in a fixed-width block, each kind of query in its own shade, and what the shades mean."""
    label = max(len(r[0]) for r in funnel)
    num = max(len('{:,}'.format(r[1])) for r in funnel)
    cells = WIDTH - label - num - 4
    most = max(r[1] for r in funnel) or 1
    out = []
    for name, n_all, segs in funnel:
        bar = ''.join(SEGMENTS[c] * max(1 if n else 0, round(n / most * cells)) for c, n in segs)[:cells]
        out.append('%s  %s %s' % (name.ljust(label), bar.ljust(cells), '{:,}'.format(n_all).rjust(num)))
    if legend:
        out.append(' · '.join('%s %s' % (SEGMENTS[c], t) for c, t in legend))
    return out


def card_text(p, plain=False):
    """The rest of the card as lines under the headline, indented; `plain` writes it for a question's text, with
    nothing escaped. A card that goes in a question (build, saved) keeps to six lines, headline included: what it can
    spare goes first, and the side page holds it. Where the run has a side page, the last line names it, opened at what
    the card is about."""
    groups = [(_wrap(p['pre']) if p.get('pre') and p['icon'] != 'edit' else [], False),
              (_wrap(p['line']) if p.get('line') else [], False),
              (_wrap(p['why']) if p.get('why') else [], False)]
    if p.get('funnel'):
        groups += [(_text_funnel(p['funnel'], None), False),
                   ([' · '.join('%s %s' % (SEGMENTS[c], t) for c, t in p['legend'])] if p.get('legend') else [], True)]
    groups.append((_wrap('%s: %s, %s' % tuple(p['most'])) if p.get('most') else [], True))
    if p.get('ba'):
        # Before and after, each with its rule, the rule's lines hanging under it.
        width = max(len(label) for label, _, _ in p['ba'])
        lead = width + 2 + max(len(_minus(value)) for _, value, _ in p['ba']) + 2
        ba = []
        for label, value, rule in p['ba']:
            first = ('%s  %s' % (label.ljust(width), _minus(value))).ljust(lead)
            ruled = _wrap(rule, WIDTH - lead) or ['']
            ba += [first + ruled[0]] + [' ' * lead + x for x in ruled[1:]]
        groups.append((ba, False))
    groups += [(_text_bars(p['bars']) if p.get('bars') else [], False),
               ([x for r in p.get('rule', []) for x in _wrap(r)], False),
               ([_fit(left, WIDTH - len(_minus(right)) - 2).ljust(WIDTH - len(_minus(right))) + _minus(right)
                 for left, right in p.get('kv') or []], True),
               ([x for name, why in p.get('sources') or [] for x in _wrap('%s, %s' % (name, why))], False),
               ([p['rest']] if p.get('rest') else [], False),
               (_wrap(p['note']) if p.get('note') else [], False),
               ([_one_line(p['names'])] if p.get('names') else _wrap_items(p['chips']) if p.get('chips') else [], True),
               (_wrap('! ' + p['warn']) if p.get('warn') else [], False)]
    more = p.get('more')
    tail = (['↗ %s: %s' % (more[0], more[1]) if more[1] else '↗ %s: on the side page' % more[0]] if more else [])
    if p['icon'] in ('metric', 'check-circle'):
        while sum(len(g) for g, _ in groups) + len(tail) > 5 and any(opt and g for g, opt in groups):
            at = max(i for i, (g, opt) in enumerate(groups) if opt and g)
            groups[at] = ([], True)
    lines = [line for g, _ in groups for line in g]
    out = [('  ' + (_fit(line) if plain else _plain(_fit(line)))).rstrip() for line in lines]
    # An address is never cut or escaped: a terminal opens it as written.
    return '\n'.join(out + ['  ' + t for t in tail])


def render(kind, spec, widget=False, first=True, plain=False):
    """What the chat shows for a moment: a mini card at a big moment, else a chip; empty when nothing is true yet.
    `plain` writes it for a native question's text: no Markdown."""
    if kind not in KINDS:
        raise ValueError('Unknown moment: %s' % kind)
    parts = _parts(kind, spec) if kind in CARDS and (first or kind != 'answer') else None
    if parts:
        if widget:
            return card_html(parts)
        body = card_text(parts, plain)
        return card_head(parts, plain) + ('\n' + body if body else '')
    if widget:
        return chip_html(kind, spec)
    shown = chip(kind, spec)
    return shown.replace('**', '') if plain else shown


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('kind', choices=KINDS)
    p.add_argument('--widget', action='store_true', help='A mini card as HTML for an inline visual tool')
    p.add_argument('--data', action='append', default=[], help='A JSON data spec, or - for stdin; later files override earlier ones')
    p.add_argument('--run-dir')
    p.add_argument('--engine-dir')
    args = p.parse_args(argv)
    data = [json.loads(sys.stdin.read() if d == '-' else Path(d).read_text(encoding='utf-8')) for d in args.data]
    print(render(args.kind, build_spec(args.kind, args.run_dir, data, args.engine_dir), args.widget))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
