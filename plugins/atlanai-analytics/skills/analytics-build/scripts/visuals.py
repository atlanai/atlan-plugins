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
from run_facts import RunFacts, count, number, read_json, sentence, table_name, their  # noqa: F401 (re-exported)

KINDS = ('connect', 'found', 'sources', 'build', 'context', 'saved', 'answer', 'layout', 'edit')
# The moments big enough for a mini card. A later answer, and a context kept on this machine, get a chip.
CARDS = ('build', 'saved', 'answer', 'edit')
# In a terminal, the moments that come right before a native question, and where in it their lines go. A question's own
# text is always shown; text written just before it folds into a one-line summary or is left out (live runs, 2026-11-12).
ASKED = {'found': "the \"Build it?\" question's text, first",
         'context': "the save question's text in place of its count line (the banner, the two lines under it and the "
                    "question stay as they are)",
         'saved': "the ready question's text, first"}
# A demo reads its sources right before choosing what to build from; own data reads them mid-build.
ASKED_DEMO = {'sources': "the next native question's text, first"}
# A rule changed on a context saved to Atlan Trident is followed by the question that saves the change.
ASKED_SAVED = {'edit': "the text of the question that asks to save the change, first, before the block"}


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
}
REQUIRED = {'saved': ('workspace',), 'answer': ('question', 'headline'), 'layout': ('tiles',), 'edit': ('metric', 'values')}
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
    return [v for v in value or [] if isinstance(v, (str, list))]


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
    if kind == 'sources' and facts.warehouse and facts.history and facts.scoped_tables:
        # A warehouse is read in place: its past queries and the chosen tables, nothing loaded here.
        return {'warehouse': facts.warehouse, 'history': facts.history, 'scoped': facts.scoped_tables}
    if kind == 'build' and facts.funnel.get('metrics'):
        return {'funnel': facts.funnel, 'merged': [m['name'] for m in facts.merged]}
    if kind == 'context' and facts.context:
        c = facts.context
        return {'ran': c['passed'], 'total': c['total'], 'need_look': c['need_look'], **({'warehouse': facts.warehouse} if facts.warehouse else {})}
    if kind == 'saved':
        if facts.local:
            c = facts.context
            return {'local': True, **({'kept': c['passed']} if c else {})}
        return dict(facts.publication)
    return {}


def build_spec(kind, run=None, data=None, engine_dir=None):
    """The moment's facts: its data file, then supplied facts such as a demo's, then what the run's files prove.

    A demo's facts file holds its firm and a section per kind. Counts that a receipt proves always win."""
    supplied = {}
    for item in (data if isinstance(data, list) else [data] if data else []):
        if not isinstance(item, dict):
            continue
        if any(key in item for key in KINDS):
            item = {**{key: item[key] for key in ('firm', 'flow') if key in item}, **(item.get(kind) if isinstance(item.get(kind), dict) else {})}
        supplied.update(item)
    facts = RunFacts(run, engine_dir) if run else None
    own = read_json(Path(run) / (kind + '.json'), dict, {}) if run and kind in DATA else {}
    spec = {**(own if not check_data(kind, own) else {}), **supplied, **(collect(kind, facts) if facts else {})}
    if kind == 'saved' and facts and not spec.get('local'):
        spec['workspace'] = spec.get('workspace') or own.get('workspace')
    if kind == 'layout':
        values = {m['name'].lower(): m['value'] for m in (facts.context.get('metrics', []) if facts else [])}
        fill = lambda t: t if isinstance(t, list) else [t, values.get(sentence(t).lower(), 'blank')]
        spec['tiles'] = [fill(t) for t in _names(spec.get('tiles'))]
        spec['more'] = [fill(t) for t in _names(spec.get('more'))]
    if kind == 'answer' and facts and str(spec.get('unit') or '').lower() in ('percent', 'percentage', '%'):
        # The answer's own metric (its first rule) says whether a percent is a share from 0 to 1; the agent's file may
        # not. Other rules it lists can hold other units.
        first = next((p.get('name') for p in spec.get('proof') or [] if isinstance(p, dict)), None)
        if first and facts.unit_for(first) == 'fraction':
            spec['unit'] = 'fraction'
    if kind == 'edit':
        unit = facts.unit_for(spec.get('metric')) if facts else None
        words = lambda v, given=None: number(v, unit or given) if isinstance(v, (int, float)) else str(v)
        values = [[words(x, row[3] if len(row) > 3 else None) for x in row[:3]] for row in spec.get('values', []) if isinstance(row, list)]
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
        return RINGS[4]
    low, high = min(values), max(values)
    return ''.join(SPARKS[round((v - low) / (high - low) * 7) if high > low else 3] for v in values)


def _chip(glyph, num, what, tail=''):
    return '%s **%s**%s%s' % (glyph, num, ' ' + what if what else '', ' · ' + tail if tail else '')


def chip_html(kind, s):
    """The chip as a small widget for a host that draws one: the same ring or spark, number and words."""
    t = _ticker(kind, s)
    if not t:
        return ''
    e = k.e
    values = [v for v in (t.get('values') or []) if isinstance(v, (int, float))][:10]
    if len(values) > 1:
        most = max(abs(v) for v in values) or 1
        glyph = '<span class="sk" aria-hidden="true">%s</span>' % ''.join(
            '<i class="%s" style="height:%dpx"></i>' % ('n' if v < 0 else '', max(2, round(abs(v) / most * 16))) for v in values)
    else:
        done, total = t.get('ring') or (1, 1)
        glyph = '<span class="rg" aria-hidden="true" style="background:conic-gradient(var(--sk) %d%%,var(--mu) 0)"></span>' % (
            round(100 * done / total) if total else 0)
    words = ' · '.join(x for x in (t['what'], t['tail']) if x)
    return k.ticker(glyph + '<span class="n">%s</span><span class="d">%s</span>' % (e(_minus(t['num'])), e(words)),
                    '%s %s' % (_minus(t['num']), words))


def _headline(s):
    """An answer's headline as (what, number): 'Turbine base: -£6,360' reads as both."""
    name, sep, value = str(s['headline']).rpartition(': ')
    return (name if sep else s.get('highlight') or ''), value


def _ticker(kind, s):
    """A chip's parts: how far its ring goes or the values its spark draws, the number, then what it counts."""
    if kind == 'found' and s.get('found'):
        return {'num': str(s['found']), 'what': 'sources found' if s['found'] != 1 else 'source found', 'tail': ', '.join(s.get('kinds') or [])}
    if kind == 'sources' and s.get('warehouse') and s.get('history'):
        read = s['history']['statements']
        return {'num': '{:,}'.format(read), 'what': 'past query read' if read == 1 else 'past queries read',
                'tail': '%s, %s' % (s['warehouse'], count(s['scoped'], 'table'))}
    if kind == 'saved' and s.get('local') and s.get('kept'):
        return {'num': str(s['kept']), 'what': 'checked metrics and queries kept on this machine', 'tail': 'nothing saved to Atlan'}
    if kind == 'sources' and s.get('tables'):
        tables = s['tables']
        tool = (s.get('dashboard') or {}).get('tool')
        rows = '{:,} rows'.format(sum(t[1] for t in tables)) if all(t[1] is not None for t in tables) else ''
        if s.get('history'):
            where = ', '.join(x for x in ('%s dashboard' % tool if tool else '', count(len(tables), 'table'), rows) if x)
            read = s['history']['statements']
            return {'num': '{:,}'.format(read), 'what': 'past query read' if read == 1 else 'past queries read', 'tail': where}
        return {'num': str(len(tables)), 'what': 'tables loaded' if len(tables) != 1 else 'table loaded',
                'tail': ', '.join(x for x in (rows, '%s dashboard' % tool if tool else '') if x)}
    if kind == 'context' and s.get('total'):
        where = '%s data' % their(s.get('firm')) if s.get('firm') else 'your %s data' % s['warehouse'] if s.get('warehouse') else 'your data'
        tail = 'on ' + where + (', %d need a look' % s['need_look'] if s.get('need_look') else '')
        return {'ring': (s['ran'], s['total']), 'num': '%d/%d' % (s['ran'], s['total']), 'what': 'metrics and queries checked', 'tail': tail}
    if kind == 'answer' and s.get('headline'):
        label, value = _headline(s)
        return {'values': [r[1] for r in s.get('rows') or [] if isinstance(r, list) and len(r) > 1], 'num': value, 'what': '', 'tail': label}
    if kind == 'layout' and s.get('tiles'):
        return {'num': str(len(s['tiles'])), 'what': 'tiles' if len(s['tiles']) != 1 else 'tile', 'tail': 'dashboard proposed' + (
            ', with %s below them' % count(len(s['more']), 'more metric') if s.get('more') else '')}
    return None


def chip(kind, s):
    """The moment as one line: a glyph, the number in bold, then what it counts. Empty when nothing is true yet."""
    t = _ticker(kind, s)
    if not t:
        return ''
    glyph = spark(t['values']) if 'values' in t else ring(*t['ring']) if 'ring' in t else RINGS[4]
    return _chip(glyph, t['num'], t['what'], t['tail'])


# Mini cards ------------------------------------------------------------------------------------

def _queries_line(f):
    """Where the queries came from, so the counts on the card add up on their face."""
    shown, asked = f.get('tiles', 0), f.get('questions', 0) - f.get('tiles', 0)
    if shown and asked > 0:
        return '%s: %d from the dashboard, %d from past queries' % (count(f['questions'], 'query', 'queries'), shown, asked)
    if shown:
        return '%s built from the dashboard' % count(shown, 'query', 'queries')
    return '%s built from past queries' % count(asked, 'query', 'queries') if asked > 0 else ''


def _parts(kind, s):
    """What a mini card shows, in the same order for HTML and text. None when the run has nothing true to show."""
    if kind == 'build' and (s.get('funnel') or {}).get('metrics'):
        f = s['funnel']
        names = s.get('merged') or []
        return {'icon': 'metric', 'title': 'Built from your sources', 'value': count(f['metrics'], 'metric'), 'line': _queries_line(f),
                'bars': [{'label': label, 'value': f[key], 'shown': '{:,}'.format(f[key])}
                         for label, key in (('Past queries', 'queries'), ('Queries built', 'questions'), ('Metrics', 'metrics')) if f.get(key)],
                'chips': names if len(names) <= NAMES else names[:NAMES - 1] + ['+%d more' % (len(names) - NAMES + 1)],
                'names': names}
    if kind == 'saved' and s.get('metrics') and not s.get('local'):
        return {'icon': 'check-circle', 'title': 'Saved to Atlan Trident',
                'value': '%s, %s' % (count(len(s['metrics']), 'metric'), count(s.get('queries', 0), 'query', 'queries')),
                'line': 'Live in %s, where agents can find them' % (s.get('workspace') or 'your workspace')}
    if kind == 'answer' and s.get('headline'):
        label, value = _headline(s)
        rows = [r for r in s.get('rows') or [] if isinstance(r, list) and len(r) > 1 and isinstance(r[1], (int, float))]
        shown = rows[:BARS] + [r for r in rows[BARS:] if r[0] == s.get('highlight')][:1]
        return {'icon': 'chart', 'title': 'Answer', 'pre': s.get('question', ''), 'value': value, 'neg': value.startswith('-'),
                'line': label, 'why': s.get('summary', ''),
                'bars': [{'label': str(r[0]), 'value': r[1], 'shown': number(r[1], s.get('unit')), 'mark': r[0] == s.get('highlight')}
                         for r in shown],
                'rule': [s['rule']] if s.get('rule') else [],
                'chips': ['Based on ' + ', '.join([sentence(x['name']) for x in s['proof'][:2]]
                                                  + (['+%d more' % (len(s['proof']) - 2)] if len(s['proof']) > 2 else []))]
                if s.get('proof') else []}
    if kind == 'edit' and s.get('values'):
        name, before, after = (s['values'][0] + ['', '', ''])[:3]
        return {'icon': 'edit', 'title': 'Rule changed', 'pre': name, 'value': '%s → %s' % (before, after),
                'rule': [r for r in (s.get('before') and 'Was: ' + s['before'], s.get('after') and 'Now: ' + s['after']) if r],
                'chips': ['+ ' + a for a in s.get('added', [])] + ['- ' + r for r in s.get('removed', [])],
                'warn': s.get('note', '')}
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
    value = e(_minus(p['value'])).replace(' → ', ' <s>→</s> ')
    return k.card(
        '<div class="mh">%s<b>%s</b></div>' % (k.icon(p['icon']), e(p['title']))
        + ('<div class="mp">%s</div>' % e(p['pre']) if p.get('pre') else '')
        + '<div class="mv%s">%s</div>' % (' neg' if p.get('neg') else '', value)
        + ('<div class="mt">%s</div>' % e(p['line']) if p.get('line') else '')
        + ('<div class="my">%s</div>' % e(p['why']) if p.get('why') else '')
        + bars + ''.join('<div class="mu">%s</div>' % e(r) for r in p.get('rule', []))
        + ('<div class="mf">%s</div>' % ' · '.join('<span class="add">%s</span>' % e(c) if c.startswith('+ ') else e(c) for c in p['chips']) if p.get('chips') else '')
        + ('<div class="mw">%s<span>%s</span></div>' % (k.icon('alert-circle'), e(p['warn'])) if p.get('warn') else ''),
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
    return '%s · %s' % (lead, p['title']) if plain else '**%s** · %s' % (_plain(lead), _plain(p['title']))


def _one_line(names, width=WIDTH, sep=' · '):
    """As many names as fit one line, then how many more."""
    for n in range(len(names), 0, -1):
        line = sep.join(names[:n] + (['+%d more' % (len(names) - n)] if n < len(names) else []))
        if len(line) <= width:
            return line
    return '%d metrics' % len(names)


def card_text(p, plain=False):
    """The rest of the card as lines under the headline, indented. A card that can go in a question keeps to six lines,
    headline included; `plain` writes it for a question's text, with nothing escaped."""
    lines = _wrap(p['pre']) if p.get('pre') and p['icon'] != 'edit' else []
    lines += _wrap(p['line']) if p.get('line') else []
    lines += _wrap(p['why']) if p.get('why') else []
    lines += _text_bars(p['bars']) if p.get('bars') else []
    lines += [x for r in p.get('rule', []) for x in _wrap(r)]
    if p.get('names'):
        lines.append(_one_line(p['names']))
    elif p.get('chips'):
        lines += _wrap_items(p['chips'])
    lines += _wrap('! ' + p['warn']) if p.get('warn') else []
    return '\n'.join(('  ' + (_fit(line) if plain else _plain(_fit(line)))).rstrip() for line in lines)


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
