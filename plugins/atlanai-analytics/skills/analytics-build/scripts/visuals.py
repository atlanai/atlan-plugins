"""A moment of a run in the chat: a one-line chip, or a mini card at the four big moments.

`build_spec` gathers the moment's facts: the moment's data file and a demo's facts file first, then what the
run's own files prove (run_facts.py), which always wins. `render` turns them into what the chat shows: as HTML
for a host's inline visual tool, or as plain reply text a terminal prints as written, in its own colours: a
chip on one line, a card as a bold headline over indented lines. The agent passes the output through; it never
designs or retypes a number.
"""
import json
import math
from pathlib import Path
import re

import visual_kit as k
from run_facts import RunFacts, agent_name, count, first_question, from_dashboard, number, read_json, report_name, sentence, table_name, their, whole  # noqa: F401 (re-exported)

KINDS = ('connect', 'found', 'sources', 'group', 'tiles', 'described', 'drafted', 'metrics', 'rules', 'links', 'build', 'context',
         'saved', 'answer', 'edit', 'questions', 'agent')
# The moments big enough for a mini card. A later answer gets a chip. A context kept on this machine shows nothing: the
# person has just chosen it.
CARDS = ('build', 'saved', 'answer', 'edit')
# In a terminal, the moments that come right before a native question, and where in it their lines go. A question's own
# text is always shown; text written just before it folds into a one-line summary or is left out (live runs, 2026-11-12).
ASKED = {'found': "the next native question's text, first",
         'questions': "the next native question's text, first",
         'context': "the save question's text in place of its count line (the banner, the two lines under it and the "
                    "question stay as they are)",
         'saved': "the ready question's text, first"}
# A demo reads its sources right before choosing what to build from; own data reads them mid-build.
ASKED_DEMO = {'sources': "the next native question's text, first"}
# A rule changed on a context saved to Atlan Trident is followed by the question that saves the change.
ASKED_SAVED = {'edit': "the text of the question that asks to save the change, first, in place of the before and after block"}


def asked(kind, demo, saved=False):
    """Where a terminal moment's lines go when a native question follows it, or None for reply text."""
    return ASKED.get(kind) or (ASKED_DEMO.get(kind) if demo else None) or (ASKED_SAVED.get(kind) if saved else None)
# The moments whose data the agent writes, the fields each needs, and the shape of every field.
DATA = {
    'build': {'aside': str},
    'saved': {'workspace': str, 'aside': str},
    'answer': {'question': str, 'headline': str, 'summary': str, 'unit': str, 'highlight': str, 'rule': str,
               'rows': 'rows', 'proof': 'names', 'breakdown': dict},
    'edit': {'metric': str, 'before': str, 'after': str, 'note': str, 'values': 'values', 'added': 'labels', 'removed': 'labels'},
}
REQUIRED = {'build': (), 'saved': ('workspace',), 'answer': ('question', 'headline'), 'edit': ('metric', 'values')}
# The one line of its own words a card takes, under its counts: why this build, in the person's terms, never a number.
ASIDE = 140
SHAPES = {'rows': '[label, number] pairs', 'names': 'a list of {"name": ...}', 'labels': 'a list of names',
          'values': 'a list of [name, before, after]', str: 'text', dict: 'an object'}
# A text card's inside width: it fits an 80-column window with room to spare, and a split pane.
WIDTH = 56
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


def answer_from(given, result, facts=None, sql=None):
    """The answer card's data from what the session names (the question, the metrics it used, and any words of its own:
    `headline`, `summary`, `rule`, `highlight`, `unit`) and the rows its query returned. Without a headline of its own,
    the card leads with the metric's value (the one value, or the column named for the metric). A breakdown of a metric
    that adds up (a sum or a count) leads with its rows' total, unless its query keeps only some rows (a LIMIT, or a
    result cut short); any other breakdown leads with its first row in the query's own order. None without a question
    or anything to lead with."""
    question = given.get('question')
    if not isinstance(question, str) or not question.strip():
        return None
    names = [n.strip() for n in given.get('metrics') or [] if isinstance(n, str) and n.strip()]
    data = {'question': question.strip(), **({'proof': [{'name': n} for n in names]} if names else {})}
    data.update({k: given[k].strip() for k in ('headline', 'summary', 'rule', 'highlight', 'unit')
                 if isinstance(given.get(k), str) and given[k].strip()})
    unit = data.get('unit') or (facts.unit_for(names[0]) if facts and names else None)
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    key = lambda s: re.sub(r'[^a-z0-9]', '', str(s).lower())  # noqa: E731
    columns = [c.get('name') if isinstance(c, dict) else str(c) for c in result.get('columns') or []]
    rows = [r for r in result.get('rows') or [] if isinstance(r, list) and r]
    if len(rows) == 1:
        at = [i for i, v in enumerate(rows[0]) if num(v)]
        named = [i for i in at if names and i < len(columns) and key(columns[i]) == key(names[0])]
        lead = named[0] if named else at[0] if len(at) == 1 else None
        if lead is not None and 'headline' not in data:
            data['headline'] = '%s: %s' % (names[0] if names else sentence(columns[lead].replace('_', ' ')), number(rows[0][lead], unit))
        elif 'headline' not in data and len(rows[0]) == 1 and isinstance(rows[0][0], str) and rows[0][0].strip():
            data.update(headline=' '.join(rows[0][0].split()), text_answer=True)
        parts = _row_parts(columns, rows[0], lead, facts) if lead is not None else []
        if parts:
            data['parts'] = parts
    elif len(rows) > 1:
        # Columns read from every row: a NULL in the first row (a job with no rework logged) dropped the card.
        value = min((i for r in rows for i, v in enumerate(r) if num(v)), default=None)
        label = min((i for r in rows for i, v in enumerate(r) if v is not None and not num(v)), default=None)
        if value is not None and label is not None:
            kept = [r for r in rows if len(r) > max(value, label) and num(r[value])]
            data['rows'] = [[str(r[label]), r[value]] for r in kept[:12]]
            if data['rows'] and 'headline' not in data:
                # The demo run of 2026-11-26 led net fee revenue by fee type with "$1,677 · delivery", its first row.
                whole = not result.get('truncated') and not LIMITED.search(sql or '')
                if names and whole and facts and facts.adds_up(names[0]):
                    data['headline'] = '%s: %s' % (names[0], number(sum(r[value] for r in kept), unit))
                else:
                    data['headline'] = '%s: %s' % (data['rows'][0][0], number(data['rows'][0][1], unit))
                    data.setdefault('highlight', data['rows'][0][0])
    return data if data.get('headline') else None


# A query that keeps only some of its rows: its rows never add up to the metric's total.
LIMITED = re.compile(r'\b(limit\s+\d|fetch\s+(first|next)\b|top\s*\(?\s*\d)', re.I)
MONEY = re.compile(r'(^|_)(fee|fees|value|sales|revenue|amount|price|cost|paid|spend|income)(_|$)')
DAY = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
# The parts a one-row answer's card lists under its number.
PARTS = 6


def _row_parts(columns, row, lead, facts):
    """A one-row answer's other values, each labelled by its column and read in its own unit, never the lead's (the
    review of 2026-11-24 read face value in the rate's unit: "121941250%"). A column named for one of the run's
    metrics takes that metric's unit, money the run's one currency, a whole number is a count, and a first and a last
    date read as one period."""
    from datetime import date
    parts, days = [], []
    for i, value in enumerate(row):
        if i == lead or i >= len(columns):
            continue
        name = str(columns[i])
        day = DAY.match(value) if isinstance(value, str) else None
        if day:
            days.append(date(*map(int, day.groups())))
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        unit = (facts.unit_for(name.replace('_', ' ')) or (facts.currency if MONEY.search(name.lower()) else None)) if facts else None
        label = name.replace('_', ' ').strip()
        parts.append([label[:1].upper() + label[1:], number(value, unit or ('count' if float(value).is_integer() else None))])
    if len(days) == 2:
        first, last = sorted(days)
        parts.append(['Period', '%d %s to %d %s' % (first.day, first.strftime('%b %Y'), last.day, last.strftime('%b %Y'))])
    return parts[:PARTS]


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
    aside = data.get('aside')
    if isinstance(aside, str) and (len(aside) > ASIDE or '\n' in aside):
        return '%s.json: `aside` is one line of at most %d characters.' % (kind, ASIDE)
    if isinstance(aside, str) and re.search(r'\d', aside):
        return ('%s.json: `aside` holds no number: every number on a card comes from the run. Say it in words, or leave '
                'it out.' % kind)
    unproven = _unproven(data) if kind == 'answer' else None
    if unproven:
        return ('answer.json: the summary says %s, which its rows, headline and question do not show. Leave that number '
                'out of the summary (your reply can carry it), or take it from the rows.' % unproven)
    return None


# A number as written: not part of a word, a date such as 24/7 or an ordinal such as 2nd; 31.7k is thousands.
FIGURE = re.compile(r'(?<![\w./])-?\d[\d,]*(?:\.\d+)?(?:bn|k|m)?(?![\w/])', re.I)
SCALE = {'k': 1e3, 'm': 1e6, 'bn': 1e9}


def _figures(text):
    """Each number written in a text, with how far it may be from what it rounds: 31.7k is 31,700 give or take 50."""
    out = []
    for m in FIGURE.finditer(re.sub(r'[£$€]', '', str(text or '').replace('\u2212', '-'))):
        written = m.group(0).lower()
        suffix = next((s for s in ('bn', 'k', 'm') if written.endswith(s)), '')
        digits = written[:len(written) - len(suffix)].replace(',', '')
        if not suffix and re.fullmatch(r'(19|20)\d\d', digits):
            continue  # a year
        scale = SCALE.get(suffix, 1)
        out.append((m.group(0), float(digits) * scale, 0.5 * 10 ** -len(digits.partition('.')[2]) * scale))
    return out


def _unproven(data):
    """The first number in an answer's summary that the card's own data does not show: a row's or a breakdown step's
    value, its share of the total, how many there are, their total, or a number in the question, the headline, the rule
    or a name. Each is held to the precision it is written to, and a loss may be written without its sign. The agent
    writes the summary; a count it made up reached a card once."""
    real = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    rows = [r for r in data.get('rows') or [] if isinstance(r, list) and len(r) > 1]
    breakdown = data.get('breakdown') if isinstance(data.get('breakdown'), dict) else {}
    steps = [s for s in breakdown.get('steps') or [] if isinstance(s, list) and len(s) > 1]
    row_values = [r[1] for r in rows if real(r[1])]
    values = row_values + [s[1] for s in steps if real(s[1])]
    names = [data.get('question'), data.get('headline'), data.get('rule')] + [x[0] for x in rows + steps] + [
        p.get('name') for p in data.get('proof') or [] if isinstance(p, dict)]
    head = [n for _, n, _ in _figures(str(data.get('headline') or '').rpartition(': ')[2])][:1]
    totals = [t for t in {sum(row_values), *head} if t]
    known = [n for name in names for _, n, _ in _figures(name)] + values + head + [len(rows), len(steps), sum(row_values)]
    known += [v / t * 100 for v in values for t in totals] + [v * 100 for v in values if abs(v) <= 1]
    for text, n, give in _figures(data.get('summary')):
        if not any(abs(abs(n) - abs(k)) <= give + 1e-9 for k in known):
            return text
    return None


def _names(value):
    """A list of metric names; a sentence such as 'A, B and C' reads as its names."""
    if isinstance(value, str):
        return [n.strip() for n in value.replace(' and ', ',').split(',') if n.strip()]
    return [v for v in value or [] if isinstance(v, str) or isinstance(v, list) and v]


def collect(kind, facts):
    """What the run's own files say about a moment."""
    if kind == 'agent':
        # What a later session works on: the agent this folder already holds, before anything else is shown.
        import datetime
        metrics = len(facts.file('context', 'metrics.json', kind=list))
        queries = len(facts.file('context', 'queries.json', kind=list))
        if not (metrics or queries):
            return {}
        file = facts.dir / 'context' / ('metrics.json' if metrics else 'queries.json')
        built = datetime.datetime.fromtimestamp(file.stat().st_mtime)
        return {'metrics': metrics, 'queries': queries,
                'built': '%d %s' % (built.day, built.strftime('%b')), 'saved': bool(facts.publication) and not facts.local}
    if kind == 'questions':
        # An accuracy check's questions, once loaded with their known answers: the side page lists each one.
        import eval_runs
        ev = eval_runs.questions_folder(facts.dir)
        return eval_runs.questions_chip(ev) if ev else {}
    if kind == 'found' and facts.found[0]:
        # What the survey and the warehouse connection found, right before the person chooses what to build from.
        return {'found': facts.found[0], 'kinds': facts.found[1], 'said': facts.found[2]}
    if kind == 'sources' and facts.tables:
        # Only what the person chose to build from: a history file left out of the choice is not read.
        chosen = facts.inputs or {'bi': True, 'history': True}
        return {'tables': [list(t) for t in facts.tables], **({'history': facts.history} if facts.history and chosen['history'] else {}),
                **({'dashboard': facts.dashboard} if facts.dashboard and chosen['bi'] else {})}
    if kind == 'sources' and facts.warehouse and facts.history and facts.scoped_tables and (facts.inputs or {'history': True})['history']:
        # A warehouse is read in place: its past queries and the chosen tables, nothing loaded here.
        return {'warehouse': facts.warehouse, 'history': facts.history, 'scoped': facts.scoped_tables}
    # Built means checked: metrics merged mid-build are not the build's result, and the checks can still hold some back.
    # The card counts the context the checks left, the list the save question counts too.
    # The card closes the build once it is saved into the run and its structural check has run: its counts (metrics,
    # saved queries, held back) are known then, so it comes before the question about testing, not after the tests.
    if kind == 'build' and facts.funnel.get('metrics') and (_checked(facts) or facts.file('context', 'metrics.json', kind=list)):
        kept = [sentence(m.get('display_name') or m['name']) for m in facts.file('context', 'metrics.json', kind=list)
                if isinstance(m, dict) and m.get('name')]
        names = {n.lower() for n in kept}
        saved = sum(1 for q in facts.file('context', 'queries.json', kind=list) if isinstance(q, dict) and q.get('name'))
        return {'funnel': dict(facts.funnel, metrics=len(kept)) if kept else facts.funnel,
                'merged': kept or [m['name'] for m in facts.merged],
                'evidence': [m for m in facts.merged if m['name'].lower() in names] if kept else facts.merged, 'took': _took(facts),
                'saved_queries': saved, 'held': facts.held, 'report': facts.report,
                **({'question': facts.interaction['question']} if facts.interaction.get('question') else {})}
    # The checks are over only when every item has its verdict: a chip before that would count unrun items as needing a look.
    if kind == 'context' and facts.checks_over:
        c = facts.context
        # One checked value as proof: a metric the dashboard shows, else the first that ran.
        shown = next((m for m in c['metrics'] if m['ok'] and m['origin'] == 'tile'), None) or next((m for m in c['metrics'] if m['ok']), None)
        return {'ran': c['passed'], 'total': c['total'], 'need_look': c['need_look'], **({'warehouse': facts.warehouse} if facts.warehouse else {}),
                **({'featured': [shown['name'], shown['value']]} if shown else {}), 'metrics': c['metrics']}
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
        return {'questions': facts.stage('h1_shapes', 'canonical_shapes'), 'read': facts.stage('h0_corpus', 'kept'),
                'total': (facts.history or {}).get('statements') or facts.query_log, **_asked(facts)}
    if kind == 'tiles' and chosen['bi']:
        # The dashboard's tiles, each written up as a question: the engine's file for them appears once they are.
        tiles = [o for o in facts.engine_file('verified_queries.bi.json').get('objects') or [] if isinstance(o, dict)]
        bi = [((o.get('provenance') if isinstance(o.get('provenance'), dict) else {}).get('bi') or {}) for o in tiles]
        if tiles:
            # Its page in the dashboard, kept apart from `page`, which is the side page's own address. Each tile opens to
            # its question: in the build's plain words once described, else its own when it already reads as one. A tile
            # is named by its title on the dashboard, never by the report's internal code for it. The count stays the one
            # the later chips and the save question use (the fetch reads every page; picking pages there is the engine's).
            # A question drafted from the SQL's shape ("What is selected_tier?") is a placeholder until the build words
            # it: a tile shows the words, or none (walk 3, 2026-11-23).
            def drafted(o):
                return any('derived structurally' in str(g) for g in o.get('gotchas') or [])
            asked = [first_question((facts.descriptions.get(o.get('vq_id')) or {}).get('question')
                                    or ('' if drafted(o) else o.get('question'))) for o in tiles]
            rows = []
            for b, q in zip(bi, asked):
                if not isinstance(b, dict) or not b.get('visual'):
                    continue
                widget = (b.get('source_fragments') or {}).get('widget') if isinstance(b.get('source_fragments'), dict) else None
                title = str(facts.engine_file(widget).get('title') or '').strip() if isinstance(widget, str) else ''
                context = (b.get('bi_context') or {}).get('widget') if isinstance(b.get('bi_context'), dict) else None
                title = title or str((context or {}).get('title') or '').strip()
                # The visual's own name where it is one, never a report's internal code for it (a GUID in Power BI).
                title = title or ('' if re.fullmatch(r'[0-9a-fA-F-]{16,}', str(b['visual'])) else str(b['visual']))
                if title or q.endswith('?'):
                    rows.append([title or q, '', q if title and q.endswith('?') else ''])
            # A report of several pages is named by the report, not by whichever page came first.
            pages = {str(b['page']) for b in bi if isinstance(b, dict) and b.get('page')}
            workbook = next((str(b['workbook']) for b in bi if isinstance(b, dict) and b.get('workbook')), None)
            return {'tiles': len(tiles), 'report': facts.report, 'bi_page': (next(iter(pages)) if len(pages) == 1 else workbook),
                    'rows': rows}
    if kind == 'described' and 'm2_ingest' in facts.stages:
        return _described(facts)
    if kind == 'drafted' and 'x1_ingest' in facts.stages and facts.stage('x1_ingest', 'proposals'):
        # The draft step's end: every metric a query works out, each name once with how many queries it came from, and
        # the tiles and past queries the metrics came from.
        names, found_in = {}, set()
        for p in facts.engine_file('metric_proposals.json').get('proposals') or []:
            if isinstance(p, dict) and p.get('name'):
                names.setdefault(sentence(str(p['name'])), set()).add(p.get('vq_id') or p.get('proposal_id'))
                found_in.add(str(p.get('vq_id') or str(p.get('proposal_id') or '').split('#')[0]))
        read, none = facts.stage('x1_ingest', 'answered'), facts.stage('x1_ingest', 'no_metric') or 0
        rows = [[n, count(len(q), 'question').replace('1 question', 'in 1 question') if len(q) == 1 else 'in ' + count(len(q), 'question')]
                for n, q in sorted(names.items(), key=lambda kv: -len(kv[1]))]
        tiles = sum(1 for v in found_in if from_dashboard(v))
        return {'drafted': facts.stage('x1_ingest', 'proposals'), 'read': read, 'with': read - none if read else None, 'rows': rows,
                'from': {'tiles': tiles, 'queries': len(found_in) - tiles}}
    if kind == 'metrics' and 'x2_ingest' in facts.stages and facts.merged:
        return {'metrics': [m['name'] for m in facts.merged], 'rows': [[m['name'], _from(m)] for m in facts.merged],
                'drafts': facts.stage('x2_ingest', 'proposals_in') or facts.stage('x1_ingest', 'proposals')}
    if kind == 'rules' and 'x3_ingest' in facts.stages:
        rows = [[m['name'], '', d] for m in facts.merged for d in [_definition(facts, m['name'])] if d]
        return {'defined': len(facts.merged), 'rows': rows} if rows else {}
    if kind == 'links' and 'x4_ingest' in facts.stages:
        edges = [x for x in facts.engine_file('metric_relationships.json').get('edges') or []
                 if isinstance(x, dict) and x.get('from') and x.get('to')]
        pairs = list(dict.fromkeys('%s uses %s' % (sentence(str(x['from'])), sentence(str(x['to']))) for x in edges))
        return {'links': len(edges), 'example': _link(edges), 'rows': [[pair, ''] for pair in pairs]} if edges else {}
    return {}


def _checked(facts):
    """Whether the checks are behind the build: every verdict in, the person chose to skip them, or the flow moved on
    past them, as it does on a warehouse, whose verdicts arrive only as each tool result is recorded. A skip drew no
    build card at all in a live dashboard run (2026-11-23)."""
    # A person who chose to skip the checks has the build in front of them now: the card comes before the next
    # question (a walkthrough's card showed only after the save question, 2026-11-23).
    return (facts.checks_over or facts.interaction.get('checks') == 'skip' or facts.checks_skipped
            or facts.file('progress.json').get('stage') in ('preview', 'publish', 'ready'))


def _runs(facts):
    """How often each past query ran, by its id, from the engine's own record of the history it read."""
    hist = {o.get('vq_id'): o for o in facts.engine_file('verified_queries.history.json').get('objects') or [] if isinstance(o, dict)}
    def runs(vid):
        prov = (hist.get(vid) or {}).get('provenance')
        history = prov.get('history') if isinstance(prov, dict) else None
        return whole(history.get('n_exec'), 0) if isinstance(history, dict) else 0
    return hist, runs


def _ran(n):
    return 'run %s' % ('once' if n == 1 else '%d times' % n) if n else ''


# A short name the engine gives a question before it is described is shown only when it reads as words: never one
# like "sum:margin" or an id.
WORDS = re.compile(r"[A-Za-z][A-Za-z0-9 '’,&()%/-]*")


# A query's own name for its number that says nothing about it: such a query is named by its tables instead.
VAGUE = {'measure', 'value', 'metric', 'result', 'total', 'amount', 'number', 'count', 'cnt', 'val', 'num', 'n', 'x'}
VAGUE_SAID = {'total': 'A total', 'amount': 'An amount', 'count': 'A count', 'cnt': 'A count', 'num': 'A count', 'n': 'A count'}
# Column shorthand a first-time reader was never taught ("Waste pct", "Rework cost gbp"), spelled out; a currency code or
# a date suffix adds nothing a question needs.
SPELLED = {'pct': 'percentage', 'perc': 'percentage', 'amt': 'amount', 'qty': 'quantity', 'avg': 'average', 'num': 'number of',
           'cnt': 'count', 'yr': 'year', 'mth': 'month', 'hrs': 'hours', 'dt': '', 'gbp': '', 'usd': '', 'eur': ''}
# A column grouped by that ends in one of these is the thing itself: "project id" and "project name" are the project.
LABELS = {'id', 'name', 'code', 'key'}


def _words(names):
    return ' and '.join(filter(None, [', '.join(names[:-1]), names[-1]])) if names else ''


def _spelled(name, grouped=False):
    words = [w for w in (SPELLED.get(w.lower(), w) for w in name.split()) if w]
    if grouped and len(words) > 1 and words[-1].lower() in LABELS:
        words = words[:-1]
    return ' '.join(words) or name


def _before_described(o):
    """A past query in words before the build describes it: the name it gives its number and what it groups by, or,
    for a name that says nothing, the tables it reads. Empty for an engine shorthand or an id."""
    name = ' '.join(str(o.get('question') or o.get('name') or '').replace('_', ' ').split())
    if not WORDS.fullmatch(name) or name.lower().startswith('vq'):
        return ''
    by = _words(list(dict.fromkeys(_spelled(' '.join(str(c).replace('_', ' ').split()), grouped=True)
                                   for c in o.get('group_by') or [] if isinstance(c, str))))
    if name.lower() in VAGUE:
        tables = _words(list(dict.fromkeys(table_name(t).lower() for t in o.get('tables') or [] if isinstance(t, str)))[:3])
        # A fresh reader of h9 could not tell what "A number by project, from crew, material orders and projects" asked.
        said = VAGUE_SAID.get(name.lower())
        return ((said + ' from ' if said else 'A question about ') + tables + (', by ' + by if by else '')) if tables else ''
    name = _spelled(name)
    return name[:1].upper() + name[1:] + (' by ' + by if by else '')


def _asked(facts):
    """The questions past queries ask, most run first: in plain words once described, else in the query's own words
    (its number's name and what it groups by); queries that read the same are one row."""
    hist, runs = _runs(facts)
    rows, short = {}, False
    for vid, o in hist.items():
        said = first_question((facts.descriptions.get(vid) or {}).get('question'))
        if not said:
            said, short = _before_described(o), True
            if not said:
                continue
        row = rows.setdefault(said, [said, 0, 0])
        row[1], row[2] = row[1] + 1, row[2] + runs(vid)
    ordered = sorted(rows.values(), key=lambda r: -r[2])
    return {'rows': [[said, ('%d queries, %s' % (n, _ran(ran)) if n > 1 else _ran(ran))] for said, n, ran in ordered],
            'short': short}


def _described(facts):
    """Every query in the build's own plain words: past queries most run first, then the dashboard's tiles."""
    words = [(vid, first_question(v.get('question'))) for vid, v in facts.descriptions.items()]
    asked = [(vid, q) for vid, q in words if q.endswith('?')]
    if not asked:
        return {}
    _, runs = _runs(facts)
    tiles = [[q, 'dashboard tile'] for vid, q in asked if from_dashboard(vid)]
    past = sorted(([q, _ran(runs(vid)), runs(vid)] for vid, q in asked if not from_dashboard(vid)), key=lambda r: -r[2])
    shown = sum(from_dashboard(vid) for vid, _ in words)
    if shown and shown == len(words) and facts.engine_file('verified_queries.bi.json').get('objects'):
        # Every question came from a tile, and the tiles chip just showed each with its question: a second chip with
        # the same count read as a repeat ("50 tiles read", then "50 questions written", 2026-11-23).
        return {}
    # With the descriptions written beside the metric steps (the engine's default), they land after the metrics: the
    # chip says so instead of announcing a step already done (walk 3, 2026-11-23).
    late = 'x1_ingest' in facts.stages
    return {'described': len(words), 'past': len(words) - shown, 'tiles': shown, 'rows': [r[:2] for r in past] + tiles,
            **({'sub': 'Written beside the metric steps, for their questions.'} if late else {})}


def _definition(facts, name):
    """A metric's definition in plain words: the first sentence its card says, whole."""
    cards = facts.engine_file('metric_cards.json')
    cards = cards.get('cards') or cards.get('metrics') or [] if isinstance(cards, dict) else []
    texts = [(c.get('name'), c.get('description')) for c in cards if isinstance(c, dict)]
    texts += [(c.get('metric'), c.get('description')) for c in facts.engine_file('x4_prompt.json').get('all_metrics') or [] if isinstance(c, dict)]
    return _first_sentence(next((str(d) for n, d in texts if str(n or '').lower() == name.lower() and d), ''))


def _first_sentence(text):
    first = re.split(r'(?<=[.!?])\s+(?=[A-Z])', ' '.join(text.split()), maxsplit=1)[0]
    return first if not first or first[-1] in '.!?' else first + '.'


# The longest example a links chip shows on its line: beside its count it fits a chat pane and an 80-column window.
LINK_EXAMPLE = 40


def _link(edges):
    """One link, read as what it is: a ratio of two metrics, a metric that is part of another, or one that uses others.
    The first kind with an example short enough to show whole, else the shortest of all."""
    named = lambda x: sentence(str(x))
    ratios, parts, uses = [], [], []
    for source in dict.fromkeys(x['from'] for x in edges):
        roles = {x.get('role'): x['to'] for x in edges if x['from'] == source}
        if 'numerator' in roles and 'denominator' in roles:
            ratios.append('%s = %s ÷ %s' % (named(source), named(roles['numerator']), named(roles['denominator'])))
        uses.append('%s uses %s' % (named(source), ' and '.join(named(x['to']) for x in edges if x['from'] == source)))
    parts += ['%s is part of %s' % (named(x['from']), named(x['to'])) for x in edges if x.get('relation') == 'restriction']
    found = ratios + parts + (uses if not (ratios or parts) else [])
    return next((x for x in found if len(x) <= LINK_EXAMPLE), min(found or uses, key=len))


def _took(facts):
    """How long the build took, from the engine's own record of its first and last stage, in whole minutes. It starts
    where the build does, once the tile translations are reviewed: the readiness check before it is not the build
    ("in 8 min" for a 6-minute build, walk 3)."""
    from datetime import datetime
    stages = [s for s in facts.engine_file('manifest.json').get('stages') or [] if isinstance(s, dict) and s.get('started_at')]
    starts = [i for i, s in enumerate(stages) if s.get('stage') in ('a3b_ingest', 'h0_corpus')]
    stages = stages[starts[0]:] if starts else stages
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
    return url


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
    if kind == 'answer' and not run:
        # An answer from Atlan Trident with no run here: nothing says where its metrics came from, and no page holds them.
        spec['standalone'] = True
    if kind == 'build' and facts and spec.get('funnel'):
        # A demo's facts passed on the call name its agent, as the questions after the card do.
        given = [i for i in (data if isinstance(data, list) else [data]) if isinstance(i, dict) and isinstance(i.get('ready'), dict)]
        spec['agent'] = agent_name(facts, given[-1]['ready'] if given else None)
    if kind == 'saved' and facts and not spec.get('local'):
        spec['workspace'] = spec.get('workspace') or own.get('workspace')
    if kind == 'answer' and facts and not spec.get('unit'):
        # An answer that names no unit reads in the unit of the rule it used first.
        first = next((p.get('name') for p in spec.get('proof') or [] if isinstance(p, dict) and p.get('name')), None)
        unit = facts.unit_for(first) if first else None
        if unit:
            spec['unit'] = unit
    if kind == 'answer' and facts and not spec.get('rule'):
        # The rule the answer used, in its metric's own words, when the session gave none.
        first = next((p.get('name') for p in spec.get('proof') or [] if isinstance(p, dict) and p.get('name')), None)
        rule = facts.rule_for(first) if first else None
        if rule:
            spec['rule'] = rule
    if kind == 'answer' and str(spec.get('unit') or '').lower() == 'currency':
        # A rule recorded as a bare currency: the answer's own headline says which.
        sign = re.search(r'[£$€]', str(spec.get('headline') or '').rpartition(': ')[2])
        if sign:
            spec['unit'] = {'£': 'GBP', '$': 'USD', '€': 'EUR'}[sign.group(0)]
    if kind == 'answer' and facts and str(spec.get('unit') or '').lower() in ('percent', 'percentage', '%'):
        # The answer's own metric (its first rule) says whether a percent is a share from 0 to 1; the agent's file may
        # not. Other rules it lists can hold other units.
        first = next((p.get('name') for p in spec.get('proof') or [] if isinstance(p, dict)), None)
        rule_unit = (facts.unit_for(first) or '').lower() if first else ''
        # Unless the rows are already in percent: they hold the scale under which they sit nearest the headline's number.
        values = [r[1] for r in spec.get('rows') or [] if isinstance(r, list) and len(r) > 1 and isinstance(r[1], (int, float))]
        found = re.search(r'-?\d[\d,]*(\.\d+)?', str(spec.get('headline') or '').rpartition(': ')[2].replace('\u2212', '-'))
        head = float(found.group(0).replace(',', '')) if found else None
        near = lambda scale: min(abs(v * scale - head) for v in values)
        as_percent = (near(1) < near(100)) if values and head is not None else any(abs(v) > 1 for v in values)
        if ('fraction' in rule_unit or rule_unit in ('ratio', 'share')) and not as_percent:
            spec['unit'] = 'fraction'
    if kind == 'edit':
        unit = facts.unit_for(spec.get('metric')) if facts else None
        words = lambda v, given=None: number(v, given or unit) if isinstance(v, (int, float)) else str(v)
        # A row that names a metric reads in that metric's unit: one edit can change several rules.
        row_unit = lambda row: (facts.unit_for(row[0]) if facts and row and isinstance(row[0], str) else None) or (row[3] if len(row) > 3 else None)
        values = [[words(x, row_unit(row)) for x in row[:3]] for row in spec.get('values', []) if isinstance(row, list)]
        spec['values'] = [v for v in values if len(v) < 3 or v[1] != v[2]] or values
        for key in ('added', 'removed'):
            spec[key] = [', '.join(words(x) for x in i) if isinstance(i, list) else words(i) for i in _names(spec.get(key))]
    return spec


# Chips -----------------------------------------------------------------------------------------


def spark(values):
    """A row of bars in one line, at most eight, lowest to highest. Plain text, so it takes the reply's own colour."""
    values = [v for v in values if isinstance(v, (int, float))][:8]
    if len(values) < 2:
        return '▪'
    low, high = min(values), max(values)
    return ''.join(SPARKS[round((v - low) / (high - low) * 7) if high > low else 3] for v in values)


def _chip(glyph, num, what, tail=''):
    return '%s **%s**%s%s' % (glyph, num, ' ' + what if what else '', ' · ' + tail if tail else '')


# Rows an opened chip shows; the rest are on the side page.
OPENED_ROWS = 8


def _opened(o, side=False):
    """An opened chip: one sentence on what the step did, then what it produced. The side page is named, never linked:
    a widget's link opens the host's link dialog and then a browser, never the side panel. It is named only once it is
    open (`side`): the first build chip pointed at a page that did not exist yet (0.9.74)."""
    e = k.e
    rows = o['rows'][:OPENED_ROWS]
    listed = ''.join('<div><b style="font-weight:400">%s</b><span>%s</span>%s</div>'
                     % (e(r[0]), e(r[1]) if len(r) > 1 else '', '<em>%s</em>' % e(r[2]) if len(r) > 2 and r[2] else '') for r in rows)
    more = len(o['rows']) - len(rows)
    return (('<div class="mt">%s</div>' % e(o['what']) if o['what'] else '') + '<div class="ls">%s</div>' % listed
            + ('<div class="more">%d more%s</div>' % (more, ' on the side page' if side else '') if more > 0 else ''))


def chip_html(kind, s):
    """The chip as a small widget for a host that draws one: the same mark, outcome and words."""
    o = _outcome(kind, s)
    if o:
        e = k.e
        hero = ('<b>%s</b>' % e(o['bold']) if o['bold'] else '') + (e(o['rest']) if not o.get('quote') else '<em>%s</em>' % e(o['rest']))
        row = o['mark'] + '<span class="h">%s</span>%s' % (hero, '<span class="t">%s</span>' % e(o['tail']) if o['tail'] else '')
        said = ' '.join(x for x in (o['bold'], o['rest'], o['tail'], o['sub']) if x)
        if o['rows']:
            return k.opening(row, _opened(o, bool(s.get('page'))), said, o['sub'])
        return k.outcome(row, said, o['sub'])
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


# Labels with an order of their own: dates, months, quarters, weeks, days and ranks.
_MONTHS = r'(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sept?(ember)?|oct(ober)?|nov(ember)?|dec(ember)?)\.?'
_DAYS = r'(mon|tue(s)?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)(day)?\.?'
NATURAL = re.compile(r'(\d{4}([-/.]\d{1,2}([-/.]\d{1,2})?)?([ t]\d{1,2}:\d{2}(:\d{2})?)?'
                     r'|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}'
                     r'|(fy\s?)?\d{2,4}\s?-?\s?(q|h)[1-4]|(q|h)[1-4](\s?-?\s?(fy\s?)?\d{2,4})?|fy\s?\d{2,4}'
                     r'|' + _MONTHS + r'(\s?-?\s?\d{2,4})?|\d{4}\s?-?\s?' + _MONTHS + r'|' + _DAYS +
                     r'|(week|wk|w)\s?\d{1,2}(\s?-?\s?\d{2,4})?|\d{4}\s?-?\s?w\d{1,2}'
                     r'|#?\d+(st|nd|rd|th)?|(rank|top|bottom|tier|level|stage|step)\s?#?\d+)', re.I)


def _ordered(labels):
    """Whether every label has an order of its own (a date, month, quarter, week, day or rank), so the bars keep it."""
    labels = [str(x).strip() for x in labels]
    return bool(labels) and all(NATURAL.fullmatch(x) for x in labels)


def _headline(s):
    """An answer's headline as (what, number): 'Turbine base: -£6,360' reads as both."""
    if s.get('text_answer'):
        return '', s['headline']
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
         'ok': '✓', 'look': '△', 'questions': '❝'}
FOUND_MARKS = {'dashboard': 'dashboard', 'database': 'database', 'query history': 'history'}
# Where each metric step's own chip sits, in the task list's own step names (Build your context: metrics and saved
# queries, Merge duplicate metrics, Add detailed information to each metric and saved query), and what comes next: one set of names
# in the chat, with no progress bar and no second numbering. The person's words, 2026-11-18: draft, merge, rule and check were the build's.
FLOW = {'described': 'Questions written. Next: the metrics behind each one.',
        'drafted': 'Metrics found. Next: merging duplicates.',
        'metrics': 'Duplicates merged. Next: adding detailed information to each metric.',
        'rules': 'Information added. Next: validating each one on the data.'}


# The metric steps the side page shows once it is open beside the chat where a task list is kept, so no chip repeats them.
ON_PAGE = ('drafted', 'metrics', 'rules', 'links')
ON_PAGE_NEXT = ("The side page shows this step. Write `say` in the chat, word for word, as your only words (nothing when "
                "it is empty), and nothing about the next step: the task list names it. Set your task list to "
                "`task_list`, each step's text and status as given (load the task-list tool first if it is deferred), "
                "in the same message as your next `PROJECT drafts "
                "--wait` (in the foreground), so the turn goes on.")


def plain_line(shown):
    """A chip's first line in plain words, with no mark and no bold: what a step on the side page says in the chat."""
    lines = (shown or '').strip().splitlines()
    return re.sub(r'^[^\w£$€]+', '', lines[0]).replace('**', '').strip() if lines else ''


def desktop():
    """Whether the helper runs in the Claude desktop app, which draws widgets."""
    import os
    return os.environ.get('CLAUDE_CODE_ENTRYPOINT') == 'claude-desktop'


def keeps_tasks(run):
    """Whether the host keeps a task list for the build: the desktop app does; elsewhere, as the agent recorded."""
    return desktop() or read_json(Path(run) / 'interaction.json', dict, {}).get('tasks') is True


def title_of(kind, spec):
    """A widget's title in the person's words, from what its chip says ("27_possible_metrics"): the app shows it, and
    the agent's own read "fixit_define_progress_chip_3"."""
    import re
    shown = render(kind, spec, False) or ''
    head = re.sub(r'[*_`]', '', (shown.strip().split('\n') or [''])[0]).split(' · ')[0]
    words = re.sub(r'[^a-z0-9]+', '_', head.lower()).strip('_')
    return words[:60].rstrip('_') or kind


def mismatch(metrics):
    """'3 of 4 match the dashboard; Sell-through differs', when a metric made from a tile gives another number than the
    tile's own formula, else ''."""
    tiles = [m for m in metrics or [] if isinstance(m, dict) and m.get('tile')]
    differ = [m['name'] for m in tiles if not m['tile'].get('same')]
    if not differ:
        return ''
    return '%d of %d match the dashboard; %s %s' % (len(tiles) - len(differ), len(tiles), _words(differ),
                                                     'differs' if len(differ) == 1 else 'differ')


def _beside_dashboard(metrics, where, need_look=0):
    """The checks' proof: what a check ran, and each number made from a dashboard tile beside the tile's own figure. A
    first-time reader never saw the dashboard's figure beside the agent's, so nothing showed why to trust either."""
    tiles = [m for m in metrics if m.get('tile')]
    differ = [m for m in tiles if not m['tile']['same']]
    if len(differ) == 1:
        sub = '%s differs from its tile\'s formula, which gives %s.' % (differ[0]['name'], _minus(differ[0]['tile']['value']))
    elif differ:
        sub = '%d metrics differ from their tiles\' formulas: %s.' % (len(differ), _words([m['name'] for m in differ]))
    elif len(tiles) == 1:
        sub = '%s matches its %s tile\'s formula.' % (tiles[0]['name'], tiles[0]['tile']['name'])
    else:
        sub = '%d metrics match the dashboard\'s own formulas.' % len(tiles) if tiles else ''
    note = lambda t: ('Same as the %s tile\'s formula' if t['same'] else 'The %s tile\'s formula gives %s') % (
        (t['name'],) if t['same'] else (t['name'], _minus(t['value'])))
    rows = [[m['name'], _minus(m['value']) if m['ok'] else 'needs your review'] + ([note(m['tile'])] if m.get('tile') else [])
            for m in metrics]
    # Once something needs a review, what each state means: "returned a number" is then not true of every metric.
    what = ('Passed means it returned a number %s. Needs your review means the result looks off. A save to Atlan Trident leaves '
            'those out. The side page says why.' % where if need_look else
            'Each metric was tested %s and returned a number.' % where) + (
        ' The dashboard file holds each tile\'s formula, without the number it shows. Each formula was worked out on the same '
        'data, and its number sits beside your agent\'s.' if tiles else '')
    return {'sub': sub, 'rows': rows, 'what': what} if rows else {}


def _outcome(kind, s):
    """What a step produced, as its mark, the outcome in bold, the rest of it, and a short tail. Never progress. None
    when the step has produced nothing yet."""
    o = None
    if kind == 'found' and s.get('found'):
        kinds = s.get('kinds') or []
        drawn = [next((v for key, v in FOUND_MARKS.items() if key in str(x)), 'file') for x in kinds][:4]
        said = s.get('said') if len(s.get('said') or []) == len(kinds) else kinds
        o = {'mark': k.marks(drawn or ['file']), 'bold': count(s['found'], 'source'), 'tail': ', '.join(said)}
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
        kept, total = s.get('read'), s.get('total')
        # A demo's sources are its firm's, never the person's: its welcome says "nothing of yours is touched".
        whose = their(s.get('firm'))
        tail = ('found in %d of %s %s' % (kept, whose, count(total, 'past query', 'past queries')) if kept and total and kept < total
                else 'from %s %s' % (whose, count(kept or total, 'past query', 'past queries')) if kept or total else '')
        what = ('Many past queries ask the same thing in different SQL. These are the distinct questions, most asked first.'
                + (' Each gets plain words once its query is described.' if s.get('short') else '')
                + ((' The other 1 was set aside before grouping.' if total - kept == 1 else
                    ' The other %d were set aside before grouping.' % (total - kept)) if kept and total and kept < total else ''))
        # The count is the rows listed, so it agrees with "N more" below them: two shapes that read the same are one
        # row (the review's run said 39 questions over a list that ended "25 more" after 8).
        asks = len(s['rows']) if s.get('rows') else s['questions']
        o = {'mark': 'group', 'bold': count(asks, 'question') + ' %s team asks' % whose, 'tail': tail, 'what': what,
             'rows': s.get('rows')}
    elif kind == 'tiles' and s.get('tiles'):
        named = s.get('report') or s.get('bi_page')
        # The dashboard's own name, said once: "your Sales dashboard", never "your Sales dashboard dashboard".
        named = named if not named or re.search(r'\b(dashboard|report)$', named, re.I) else named + ' dashboard'
        on = 'on %s %s' % (their(s.get('firm')), named or 'dashboard')
        o = {'mark': k.mark('tiles', min(s['tiles'], 9), min(s['tiles'], 3)), 'bold': count(s['tiles'], 'tile') + ' read',
             'tail': 'each written as a question with SQL', 'rows': s.get('rows'),
             'what': 'Each tile %s is now written as a question, kept with SQL for its number.' % on}
    elif kind == 'described' and s.get('described'):
        # Each count says where it came from, as on main: the review of PR 205 found this chip and the next had lost it.
        # Saved queries, as the build card counts them: "questions written" read as the accuracy check's test questions.
        o = {'mark': 'described', 'bold': count(s['described'], 'saved query', 'saved queries') + ' written',
             # From one source the count would only repeat itself ("5 questions written · from your 5 questions").
             'tail': ('from %s %s and %s' % (their(s.get('firm')), count(s['past'], 'question'), count(s['tiles'], 'dashboard tile'))
                      if s.get('past') and s.get('tiles') else 'from %s dashboard\'s tiles' % their(s.get('firm')) if s.get('tiles')
                      else 'in %s words' % their(s.get('firm'))),
             'rows': s.get('rows'),
             'what': 'When you ask one of these, your agent finds the question that matches and uses its SQL.',
             **({'sub': s['sub']} if s.get('sub') else {})}
    elif kind == 'drafted' and s.get('drafted'):
        # Of the questions just written, how many work out a metric: "found in 3 of those 4 questions", so this count
        # follows the one before it (a count of source ids read "from 5 past queries" after "4 questions written").
        found_in = ('in %d of those %s' % (s['with'], count(s['read'], 'question'))
                    if s.get('read') and s.get('with') is not None else _from(s.get('from')))
        o = {'mark': 'metrics', 'bold': count(s['drafted'], 'metric') + ' found', 'tail': found_in,
             'rows': s.get('rows'),
             'what': ('A metric is a total, count or rate that one of %s questions works out. The same metric often turns up '
                      'in several questions.' % their(s.get('firm')))}
    elif kind == 'metrics' and s.get('metrics'):
        merged = s['drafts'] - len(s['metrics']) if s.get('drafts') else None
        o = {'mark': 'metrics', 'bold': count(len(s['metrics']), 'metric'),
             'tail': '' if merged is None else count(merged, 'duplicate') + ' merged' if merged > 0 else 'no duplicates to merge',
             'rows': s.get('rows'),
             'what': 'Each metric has one definition, used every time. A later test can still hold one back.'}
    elif kind == 'rules' and s.get('rows'):
        o = {'mark': 'rules', 'bold': 'Detailed information added to ' + count(s['defined'], 'metric'), 'tail': 'what each counts and leaves out',
             'rows': s['rows'],
             'what': 'Each note says what the metric counts, what it leaves out and the SQL behind it.'}
    elif kind == 'links' and s.get('links'):
        # An example shows whole or not at all: cut mid-formula it read as a broken sum. Every link is in the opened rows.
        example = s.get('example') or ''
        o = {'mark': 'links', 'bold': count(s['links'], 'link') + ' between metrics',
             'tail': 'e.g. ' + example if example and len(example) <= LINK_EXAMPLE else '',
             'rows': s.get('rows'),
             'what': 'Some metrics are built from others. Change one definition, and the ones built on it follow.'}
    elif kind == 'agent' and (s.get('metrics') or s.get('queries')):
        queries = count(s.get('queries', 0), 'saved query', 'saved queries')
        held = '%s and %s' % (count(s['metrics'], 'metric'), queries) if s.get('metrics') else queries
        o = {'mark': 'ok', 'bold': 'Your agent: ' + held,
             'tail': 'built %s, saved to Atlan Trident' % s['built'] if s.get('saved') else 'built %s' % s['built']}
    elif kind == 'questions' and s.get('questions'):
        where = [x for x in ('%d from your dashboard' % s['tiles'] if s.get('tiles') else '',
                             '%d from past queries' % s['queries'] if s.get('queries') else '') if x]
        o = {'mark': 'described', 'bold': count(s['questions'], 'test question') + ' written',
             'tail': ', '.join(where) or ('from your set %s' % s['set'] if s.get('set') else '')}
    elif kind == 'context' and s.get('total'):
        where = 'on %s data' % their(s.get('firm')) if s.get('firm') or not s.get('warehouse') else 'on your %s data' % s['warehouse']
        review = lambda n: '%d %s your review' % (n, 'needs' if n == 1 else 'need')
        if s.get('need_look') and s.get('ran'):
            o = {'mark': 'look', 'bold': '%d passed' % s['ran'], 'tail': '%s · %s' % (review(s['need_look']), where)}
        elif s.get('need_look'):
            o = {'mark': 'look', 'bold': review(s['need_look']), 'tail': where}
        elif mismatch(s.get('metrics')):
            # Every test ran, but a metric gives another number than its tile: never "Every metric works" over that
            # (0.9.74, Ticketfuddle: Sell-through 76.1% against its tile's 81.5%).
            o = {'mark': 'look', 'bold': mismatch(s.get('metrics')), 'tail': where}
        else:
            shown = s.get('featured')
            # Not "Every test passed": beside the accuracy check's test questions, it read as accuracy already
            # tested (the joint run, 2026-11-25).
            o = {'mark': 'ok', 'bold': 'Every metric works',
                 'tail': where + (' · %s %s' % (shown[0], _minus(shown[1])) if shown and shown[1] not in (None, 'not run') else '')}
        o.update(_beside_dashboard(s.get('metrics') or [], where, s.get('need_look')))
    if not o:
        return None
    name = o['mark'] if o['mark'] in MARKS else None
    return {'text_mark': MARKS[name] if name else MARKS[kind if kind in MARKS else 'found'],
            'mark': k.mark(name) if name else o['mark'], 'bold': o['bold'], 'rest': o.get('rest', ''), 'tail': o.get('tail', ''),
            'quote': o.get('quote', False), 'what': o.get('what', ''), 'rows': o.get('rows') or [],
            'sub': o.get('sub') or FLOW.get(kind, '')}


# A chip's line in a terminal, as wide as a card: one line in an 80-column window.
LINE = 76


def _fit_words(text, width):
    """Text cut to a width at a word, never inside one."""
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    # A cut that ends where a word ends keeps that word.
    space = width - 1 if text[width - 1] == ' ' else cut.rfind(' ')
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


def say_line(kind, s):
    """The moment's line for the chat, whole: the terminal chip cuts it to its width ("is part of…", walk 4)."""
    o = _outcome(kind, s)
    if not o:
        return plain_line(chip(kind, s))
    text = '%s%s%s' % (o['bold'] or '', o.get('rest') or '', ' · ' + o['tail'] if o.get('tail') else '')
    return ' '.join(text.replace('**', '').split())


def chip(kind, s):
    """The moment as one line: its own mark, then what the step produced. Empty when nothing is true yet."""
    o = _outcome(kind, s)
    if o:
        bold, rest, tail = _fit_line(o)
        line = '%s %s%s%s' % (o['text_mark'], '**%s**' % bold if bold else '', rest, ' · ' + tail if tail else '')
        return line + ('\n  ' + _fit_words(o['sub'], LINE - 2) if o['sub'] else '')
    t = _ticker(kind, s)
    if not t:
        return ''
    return _chip(spark(t['values']), t['num'], t['what'], t['tail'])


# Mini cards ------------------------------------------------------------------------------------

def _source_line(f, report=''):
    """Where the questions written came from, so the counts on the card add up on their face: "From 1 tile on the Job
    Margins dashboard" when they all came from a named report, else "From 2 dashboard tiles and 4 past queries"."""
    shown, asked = f.get('tiles', 0), f.get('questions', 0) - f.get('tiles', 0)
    if shown and asked <= 0 and report:
        return 'From %s on %s' % (count(shown, 'tile'), report_name(report))
    parts = [x for x in (count(shown, 'dashboard tile') if shown else '',
                         count(asked, 'past query', 'past queries') if asked > 0 else '') if x]
    return 'From ' + ' and '.join(parts) if parts else ''


def _from(e):
    """Where a metric came from, in words: 'from 2 dashboard tiles and 4 past queries'."""
    parts = [x for x in (count(e['tiles'], 'tile') if e and e.get('tiles') else '',
                         count(e['queries'], 'past query', 'past queries') if e and e.get('queries') else '') if x]
    return 'from ' + ' and '.join(parts) if parts else ''


def _by_source(names, evidence):
    """The names under where each came from, in words: the dashboard's first, then past queries, then any other. None
    when nothing is known of where they came from, so the names stay one plain list."""
    known = {m['name'].lower(): m for m in evidence or [] if isinstance(m, dict) and m.get('name')}
    if not known:
        return None
    groups = [('From your dashboard', []), ('From past queries', []), ('Also', [])]
    for name in names:
        e = known.get(sentence(name).lower()) or known.get(str(name).lower()) or {}
        groups[0 if e.get('tiles') else 1 if e.get('queries') else 2][1].append(name)
    return [g for g in groups if g[1]]


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
        held = s.get('held') or {}
        held = ' and '.join(x for x in (count(held['metrics'], 'metric') if held.get('metrics') else '',
                                        count(held['queries'], 'saved query', 'saved queries') if held.get('queries') else '') if x)
        # The counts carry the headline; the dashboard's name goes in the source line (the person's words, 2026-11-23).
        return {'icon': 'metric', 'title': 'Your agent is built',
                # How the person got here: a build started from their question says so first.
                'pre': 'Built to answer: ' + s['question'] if s.get('question') else '',
                'why': s.get('aside', ''),
                'meta': 'in %d min' % s['took'] if s.get('took') else '',
                'value': count(f['metrics'], 'metric') + (
                    ' and ' + count(s['saved_queries'], 'saved query', 'saved queries') if s.get('saved_queries') else ''),
                'line': _source_line(f, s.get('report') or ''),
                'held': 'Held back: %s. The side page says why.' % held if held else '',
                'funnel': [r for r in (('Past queries read', f.get('queries'), [('fp', f.get('queries'))]) if f.get('queries') else None,
                                       ('Questions written', f.get('questions'), [('fq', max(asked, 0)), ('ft', tiles)]) if f.get('questions') else None,
                                       ('Metrics', f['metrics'], [('fm', f['metrics'])])) if r],
                'legend': [x for x in (('fq', '%d from past queries' % asked) if asked > 0 else None,
                                       ('ft', '%d from dashboard tiles' % tiles) if tiles else None) if x] if asked > 0 and tiles else [],
                'most': ['Found most often', most['name'], _from(most)] if most and most['tiles'] + most['queries'] > 1 else None,
                'names': s.get('merged') or [], 'groups': _by_source(s.get('merged') or [], evidence),
                'more': ['Every metric and how it\'s worked out', s.get('page')]}
    if kind == 'saved' and (s.get('metrics') or s.get('queries')) and not s.get('local'):
        featured = s.get('featured')
        metrics = s.get('metrics') or []
        queries = count(s.get('queries', 0), 'saved query', 'saved queries')
        return {'icon': 'check-circle', 'title': 'Saved to Atlan Trident', 'why': s.get('aside', ''),
                'value': '%s, %s' % (count(len(metrics), 'metric'), queries) if metrics else queries,
                'line': 'In %s, where chats with Atlan Analytics look for %s' % (s.get('workspace') or 'your workspace',
                                                                                'metrics' if metrics else 'saved queries'),
                'note': ('Validated on %s: %d of %d passed' % (_where(s), s['kept'], s['total'])
                         + (', %d %s your review' % (s['need_look'], 'needs' if s['need_look'] == 1 else 'need')
                            if s.get('need_look') else '')) if s.get('total') else '',
                'kv': [[' · '.join(x for x in (featured[0], _from(_evidence(s, featured[0]))) if x), featured[1]]] if featured else [],
                'names': metrics, 'groups': _by_source(metrics, s.get('evidence'))}
    if kind == 'answer' and s.get('headline'):
        label, value = _headline(s)
        rows = [r for r in s.get('rows') or [] if isinstance(r, list) and len(r) > 1 and isinstance(r[1], (int, float))]
        if not _ordered([r[0] for r in rows]):
            # Bars read top to bottom, longest first, unless their labels have an order of their own (0.9.74 kept the
            # query's order: North 39 below Retail 9). A loss is as long as its size.
            rows = sorted(rows, key=lambda r: -abs(r[1]))
        shown = rows[:BARS] + [r for r in rows[BARS:] if r[0] == s.get('highlight')][:1]
        proof = [sentence(x['name']) for x in s.get('proof') or [] if isinstance(x, dict) and x.get('name')]
        return {'icon': 'chart', 'title': 'Answer', 'pre': s.get('question', ''), 'value': value, 'neg': value.startswith('-'),
                # The metric the sources line names is not said again under the number; a row that answers keeps its label.
                'line': '' if proof[:2] and sentence(label).lower() in [n.lower() for n in proof[:2]] else label,
                'why': s.get('summary', ''),
                'bars': [{'label': str(r[0]), 'value': r[1], 'shown': number(r[1], s.get('unit')), 'mark': r[0] == s.get('highlight')}
                         for r in shown],
                'rule': [s['rule']] if s.get('rule') else [],
                'parts': [p for p in s.get('parts') or [] if isinstance(p, list) and len(p) == 2],
                'sources': [[n, _from(_evidence(s, n)) or ('' if s.get('standalone') else 'a saved query')] for n in proof[:2]],
                'rest': '+%d more behind this answer' % (len(proof) - 2) if len(proof) > 2 else '',
                'more': None if s.get('standalone') else ['How this was worked out', s.get('page')]}
    if kind == 'edit' and s.get('values'):
        name, before, after = (s['values'][0] + ['', '', ''])[:3]
        return {'icon': 'edit', 'title': 'Definition changed', 'pre': name, 'value': '%s → %s' % (before, after),
                'ba': [['Before', before, s.get('before') or ''], ['After', after, s.get('after') or '']],
                'chips': ([('+ Now counted: ' + ', '.join(s['added']))] if s.get('added') else [])
                + ([('- No longer counted: ' + ', '.join(s['removed']))] if s.get('removed') else []),
                'warn': s.get('note', ''), 'more': ['The full definition', s.get('page')]}
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


def _names_html(rows, hidden):
    """The names, past the cap ending on how many more. Under their words, in one grid: a group that wraps keeps to its
    own column, and every group's names start in the same place."""
    e = k.e
    spans = lambda items, last: ''.join('<span>%s</span>' % e(n) for n in items) + ('<span>+%d more</span>' % hidden if last and hidden else '')
    if not rows:
        return ''
    if not rows[0][0]:
        return '<div class="nm">%s</div>' % spans(rows[0][1], True)
    return '<div class="ng">%s</div>' % ''.join('<b>%s</b><div class="nm">%s</div>' % (e(label), spans(items, i == len(rows) - 1))
                                                for i, (label, items) in enumerate(rows))


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
    names = p.get('names') or []
    # Each group on its own line, under its words; past the cap, the last line shown says how many more.
    left, rows = (len(names) if len(names) <= NAMES else NAMES - 1), []
    for label, items in p.get('groups') or ([('', names)] if names else []):
        if left > 0:
            rows.append((label, items[:left]))
            left -= len(rows[-1][1])
    hidden = len(names) - sum(len(items) for _, items in rows)
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
        + ('<div class="kv pt">%s</div>' % ''.join('<span>%s</span><b>%s</b>' % (e(a), e(_minus(b))) for a, b in p['parts']) if p.get('parts') else '')
        + ('<div class="kv">%s</div>' % ''.join('<span>%s</span><b>%s</b>' % (e(a), e(_minus(b))) for a, b in p['kv']) if p.get('kv') else '')
        + ('<div class="kv">%s</div>' % ''.join('<span><b>%s</b></span><span>%s</span>' % (e(a), e(b)) for a, b in p['sources']) if p.get('sources') else '')
        + ('<div class="mu">%s</div>' % e(p['rest']) if p.get('rest') else '')
        + ('<div class="mu">%s</div>' % e(p['note']) if p.get('note') else '')
        + _names_html(rows, hidden)
        + ''.join('<div class="mu">%s</div>' % e(p[key]) for key in ('held', 'next') if p.get(key))
        + ('<div class="mf">%s</div>' % ' · '.join('<span class="add">%s</span>' % e(c) if c.startswith('+ ') else '<span class="rem">%s</span>' % e(c)
                                                   if c.startswith('- ') else e(c) for c in p['chips']) if p.get('chips') else '')
        + ('<div class="mw">%s<span>%s</span></div>' % (k.icon('alert-circle'), e(p['warn'])) if p.get('warn') else '')
        + ('<div class="mo"><span>%s: on the side page</span></div>' % e(more[0]) if more else ''),
        '%s: %s' % (p['title'], _minus(p['value'])))


def _fit(text, width=WIDTH):
    """Text cut to a width at a word, with "…"."""
    return _fit_words(str(text), width)


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
    return count(len(names), 'metric')


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
              # The build card's source line gives way after its Next line, before anything held back.
              (_wrap(p['line']) if p.get('line') else [], 0.8 if p['icon'] == 'metric' else False),
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
                 for left, right in (p.get('kv') or []) + (p.get('parts') or [])], True),
               ([x for name, why in p.get('sources') or [] for x in _wrap(', '.join(w for w in (name, why) if w))], False),
               ([p['rest']] if p.get('rest') else [], False),
               (_wrap(p['note']) if p.get('note') else [], False),
               ([_one_line(p['names'])] if p.get('names') else _wrap_items(p['chips']) if p.get('chips') else [], True),
               # On one line: where its pointer to the side page does not fit, the card's last line names the page.
               ([p['held'] if len(p['held']) <= WIDTH else p['held'].split('. ')[0] + '.'] if p.get('held') else [], False),
               # What comes next gives way first among the card's own lines; held back never goes.
               (_wrap(p['next']) if p.get('next') else [], 0.9),
               (_wrap('! ' + p['warn']) if p.get('warn') else [], False)]
    more = p.get('more')
    tail = (['↗ %s: %s' % (more[0], more[1]) if more[1] else '↗ %s: on the side page' % more[0]] if more else [])
    if p['icon'] in ('metric', 'check-circle'):
        while sum(len(g) for g, _ in groups) + len(tail) > 5 and any(opt and g for g, opt in groups):
            # The line that can go first: the highest rank (True outranks 0.5), then the lowest on the card.
            at = max((opt, i) for i, (g, opt) in enumerate(groups) if opt and g)[1]
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


