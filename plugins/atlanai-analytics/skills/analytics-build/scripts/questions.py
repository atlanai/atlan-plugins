"""The flow's two big questions, composed from the run's own files: where the context lives, after the checks, and
what to ask, once it is ready.

The agent asks exactly what this prints. Every count comes from the run; a demo's words come from its facts file,
and only on a run the journey records as a demo, so they never reach the person's own data. In a terminal the
moment's chips and cards are part of the text, since a question's own text is always shown.
"""
from pathlib import Path
import re

import visuals
from run_facts import RunFacts, count, read_json, sentence, their

SAVE_OPTIONS = (
    ('Save to Atlan Trident (recommended)', 'Agents find the right metric for each question, from any chat or tool. Everyone gets '
     'the same number, and a change to a rule reaches all of them at once. You sign in once.'),
    ('Keep it on this machine', 'Works in this folder only. Other chats, tools and colleagues cannot find it, so each would '
     'work the numbers out its own way.'),
)
# The ready block's label column, as wide as its longest label and two spaces.
LABEL = 22


def _demo(run, data):
    """A demo's facts, merged, or nothing when the journey does not record this run as a demo."""
    if read_json(Path(run) / 'interaction.json', dict, {}).get('flow') != 'demo':
        return {}
    if not data:
        from run_facts import demo_facts
        data = [demo_facts(run)]
    out = {}
    for item in data or []:
        if isinstance(item, dict):
            out.update({k: v for k, v in item.items() if k in ('firm',)})
            out.update(item.get('ready') or {})
    return out


def _where(f, demo):
    return '%s data' % their(demo.get('firm')) if demo.get('firm') else 'your %s data' % f.warehouse if f.warehouse else 'your data'


def _built_from(f):
    """'1 dashboard and 110 past queries', counted from what the build read."""
    chosen = f.inputs or {'bi': bool(f.dashboard), 'history': bool(f.history)}
    parts = (['1 dashboard'] if chosen['bi'] and (f.dashboard or f.funnel.get('tiles')) else []) + (
        [count((f.history or {}).get('statements') or f.query_log, 'past query', 'past queries')]
        if chosen['history'] and ((f.history or {}).get('statements') or f.query_log) else [])
    return ' and '.join(parts) or count(len(f.tables), 'table')


def _label(name, value):
    """One ready-block row: the label, then the value, a long value wrapping onto indented lines."""
    lines = visuals._wrap(value, 76 - LABEL - 2) or ['']
    return ['  ' + name.ljust(LABEL) + lines[0]] + ['  ' + ' ' * LABEL + x for x in lines[1:]]


def save(run, widget=False, data=None, engine_dir=None):
    """The save question, after every check has run."""
    f = RunFacts(run, engine_dir)
    if f.interaction.get('flow') == 'dashboard':
        return _dashboard_save(run, f, widget, data, engine_dir)
    demo = _demo(run, data)
    c = f.context
    # A warehouse's checks run with its own tool, each verdict recorded as it returns: until every one is, the agent adds
    # the line of results it holds.
    by_tool = bool(c) and not f.checks_here and not f.checks_over
    if not f.checks_over and not by_tool:
        # Before every item has its verdict, the counts would be false ("0/4 checked, 4 need a look").
        waiting = c['total'] - c['metrics_checked'] - c['queries_checked'] if c else None
        raise ValueError('Ask where the context lives once every check has run%s.' % (
            ': %d of %d still wait for their sample check. Run each; if one cannot run, record `PROJECT progress '
            '--stage preview --status running` and ask again, and it counts as needing a look' % (waiting, c['total'])
            if waiting else ''))
    # The copy budget (the team, 2026-11-19): only what the person can't already see. The cards say what was built, drawn
    # above on desktop and as this text in a terminal, so no banner or recap line before them.
    lines = []
    shown = '' if widget or by_tool else visuals.render('context', visuals.build_spec('context', run, data, engine_dir), plain=True)
    card = '' if widget else visuals.render('build', visuals.build_spec('build', run, data, engine_dir), plain=True)
    if shown:
        lines += ([card, ''] if card else []) + [shown]
    elif by_tool:
        lines += ([card, ''] if card else []) + ['%s and %s, their sample checks run on %s with its own read-only tool.' % (
            count(len(c['metrics']), 'metric'), count(c['queries'], 'query', 'queries'), _where(f, demo))]
    else:
        # What the checks found, never a count out of a total.
        lines.append('%s and %s, %s on %s.' % (
            count(len(c['metrics']), 'metric'), count(c['queries'], 'query', 'queries'),
            'every one ran' if not c['need_look'] else '%d ran cleanly, %d need a look' % (c['passed'], c['need_look'])
            if c['passed'] else 'none ran cleanly yet', _where(f, demo)))
    lines += _one_metric(run, f, demo, widget)
    lines += ['', 'Where should this context live?']
    # The task list numbers the steps, so the header names this one: a second numbering disagreed with the list.
    return {'header': 'Save',
            'question': '\n'.join(lines), 'options': [{'label': a, 'description': b} for a, b in SAVE_OPTIONS],
            'shown': [k for k, v in (('build', card), ('context', shown)) if v], 'results': by_tool}


def _dashboard_save(run, f, widget, data, engine_dir):
    """The dashboard branch's save question: what was built, whether it ran on the data, what was kept back and why,
    and the tiles that could not be built, one line each. The side page holds every detail."""
    import side_page
    c = f.context
    skipped = f.interaction.get('checks') == 'skip'
    by_tool = bool(c) and not f.checks_here and not f.checks_over
    if not (skipped or by_tool or f.checks_over):
        raise ValueError('Ask where the context lives once the checks have run, or once the person chose to skip them '
                         '(`PROJECT journey checks --answer skip`, their answer, never yours).')
    if not c:
        # d3, 2026-11-19: asked before the structural pass, this said "Built 0 metrics" for a build of 4.
        raise ValueError('Run `PROJECT check` first: the save question counts what the structural pass found, and before it '
                         'there is nothing to count.')
    report = str(f.interaction.get('report') or '').split(':')[0].strip() or 'your dashboard'
    n_metrics, n_queries = (len(c['metrics']), c['queries']) if c else (0, 0)
    where = f.warehouse or 'your data'
    # The same count as the build's card above it: every query built, the ones kept back named on their own line
    # (walk 4: the card said 150 queries and this question 144).
    built = max(n_queries, int((f.funnel or {}).get('questions') or 0))
    lines = ['Built %s and %s from %s.' % (count(n_metrics, 'metric'), count(built, 'query', 'queries'), report)]
    if skipped:
        lines.append('Not run on %s: you chose to skip the checks, so the numbers are untested.' % where)
    elif by_tool:
        lines.append("Run on %s with its own read-only tool." % where)
    else:
        lines.append('Run on %s: %s.' % (where, 'every one ran' if not c['need_look'] else
                                          '%d ran cleanly, %d need a look' % (c['passed'], c['need_look'])))
    kept = _kept(f)
    if kept:
        lines.append('Kept back, not saved: %s.' % kept)
    cant = side_page.cant_build(f)
    if cant:
        lines.append("%s couldn't be built. The side page lists each with why." % count(len(cant), 'tile'))
    card = '' if widget else visuals.render('build', visuals.build_spec('build', run, data, engine_dir), plain=True)
    lines = ([card, ''] if card else []) + lines + ['', 'Where should this context live?']
    return {'header': 'Save', 'question': '\n'.join(lines),
            'options': [{'label': a, 'description': b} for a, b in SAVE_OPTIONS],
            'shown': ['build'] if card else [], 'results': by_tool}


def _kept(f):
    """What the save kept back, each reason with its count: "1 metric and 1 query: 1 didn't match the dashboard's
    number, 1 left out a filter its tile always applies". Empty when nothing was kept back."""
    import side_page
    h = side_page.held(f)
    if not (h['metrics'] or h['queries']):
        return ''
    return '%s: %s' % (' and '.join(x for x in (count(h['metrics'], 'metric') if h['metrics'] else '',
                                                count(h['queries'], 'query', 'queries') if h['queries'] else '') if x), h['why'])


def _one_metric(run, f, demo, widget=False):
    """The collection's review, in the question's own text: one checked metric in full, from the dashboard when there
    is one, with what it means, what it counts, where it came from and its check."""
    import side_page
    made = side_page.details(f)['metrics']
    m = next((x for x in made if x.get('status') == 'checked' and any(s['kind'] == 'tile' for s in (x.get('sources') or []))), None) or \
        next((x for x in made if x.get('status') == 'checked'), None)
    if not m:
        return []
    came = visuals._from({'tiles': sum(s['kind'] == 'tile' for s in (m.get('sources') or [])), 'queries': sum(s['kind'] == 'query' for s in (m.get('sources') or []))})
    lines = ['', 'One metric in full: %s, %s on %s' % (m['name'], m.get('value') or 'checked', _where(f, demo))]
    # Its first sentence, what it leaves out, and where it came from, each kept short: the side page holds the rest.
    first = re.split(r'(?<=[.!?])\s', str(m.get('description') or '').strip(), maxsplit=1)[0]
    lines += ['  ' + x for x in visuals._wrap(visuals._fit(first, 150), 74)]
    if m.get('leaves_out'):
        # Whole items only, on two lines at most: an item cut short reads as broken.
        out = 'Leaves out: ' + str(m['leaves_out'][0]).rstrip('.')
        for x in m['leaves_out'][1:2]:
            if len(out) + 2 + len(str(x).rstrip('.')) <= 140:
                out += '; ' + str(x).rstrip('.')
        lines += [('    ' if i else '  ') + x for i, x in enumerate(visuals._wrap(visuals._fit(out, 140), 72))]
    # A terminal opens the address; in the desktop app a link opens an outside browser, not the page beside the chat.
    page = None if widget else visuals.page_link(run, 'edit', {'metric': m['name']})
    if came:
        lines.append('  ' + came[:1].upper() + came[1:] + (': ' + page if page else '.'))
    return lines


def _supported(made, limit=3):
    """Questions the checked context answers, in the words the build wrote for each: the dashboard's tiles first,
    then the past queries asked most, each only when its metric passed its check."""
    passed = [m for m in made['metrics'] if m.get('status') == 'checked']
    picked, seen = [], set()
    for m in passed:
        # Its dashboard tile's wording first, else the shortest a past query has: an option reads at a glance.
        # A question in words only: an engine label such as "sum:amount:ok" is not one.
        ranked = sorted((s for s in m.get('sources') or [] if str(s.get('question') or '').strip().endswith('?')
                         and len(str(s.get('question')).strip()) <= 90),
                        key=lambda s: (s['kind'] != 'tile', len(str(s.get('question')))))
        for s in ranked[:1]:
            q = str(s.get('question') or '').strip()
            if q.lower() not in seen:
                seen.add(q.lower())
                came = visuals._from({'tiles': sum(x['kind'] == 'tile' for x in (m.get('sources') or [])),
                                      'queries': sum(x['kind'] == 'query' for x in (m.get('sources') or []))})
                picked.append({'label': q, 'description': ' · '.join(x for x in (m['name'], came) if x)})
        if len(picked) == limit:
            break
    # Then the saved queries that passed their check, in their own words.
    for q in made['queries']:
        text = str(q.get('question') or '').strip()
        if len(picked) < limit and q.get('status') == 'checked' and text.endswith('?') and len(text) <= 90 and text.lower() not in seen:
            seen.add(text.lower())
            picked.append({'label': text, 'description': '%s · a saved query' % q['name']})
    return picked


def ready(run, widget=False, data=None, engine_dir=None):
    """The ready question, once the context is saved or kept: what was built, where it lives, what it answers."""
    f = RunFacts(run, engine_dir)
    demo = _demo(run, data)
    c = f.context
    if not c:
        raise ValueError('Ask what to ask once the context is saved or kept.')
    if not f.local and not f.publication:
        # The run must know where the context lives: kept without being recorded, the question loses what was kept.
        raise ValueError('Record where the context lives first: run `PROJECT journey keep-local` when the person kept it on '
                         'this machine, or finish the save to Atlan Trident; then ask again.')
    saved = f.publication if not f.local else {}
    shown = '' if widget else visuals.render('saved', visuals.build_spec('saved', run, data, engine_dir), plain=True)
    lines = ([shown, ''] if shown else []) + ['Your %s is ready.' % (demo.get('agent') or 'agent'), '']
    lines += _label('What you built', '%s and %s,' % (count(len(c['metrics']), 'metric'), count(c['queries'], 'query', 'queries')))
    lines += ['  ' + ' ' * LABEL + 'from %s,' % _built_from(f), '  ' + ' ' * LABEL + (
        'not run on the data: you chose to skip the checks' if f.checks_skipped else 'checked on %s' % _where(f, demo))]
    place = where = '%s in Atlan Trident' % saved['workspace'] if saved.get('workspace') else 'Atlan Trident'
    whole = len(c['metrics']) + c['queries']
    if saved and saved.get('items', whole) < whole:
        # A save that stopped part-way says what is where, by kind when the saved card counts them: "11 of 12" under "8
        # metrics" read as a count that disagreed with the card's "7 metrics" (2026-11-22).
        where = '%d of %d in %s, the rest on this machine' % (saved['items'], whole, place)
        try:
            spec = visuals.build_spec('saved', run, data, engine_dir)
            kept_m, kept_q = len(spec.get('metrics') or []), int(spec.get('queries') or 0)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            kept_m = kept_q = -1
        if kept_m >= 0 and kept_m + kept_q == saved['items']:
            rest_m, rest_q = len(c['metrics']) - kept_m, c['queries'] - kept_q
            both = lambda m, q: ' and '.join(x for x in (count(m, 'metric') if m else '',
                                                        count(q, 'query', 'queries') if q else '') if x)
            where = '%s in %s; %s on this machine' % (both(kept_m, kept_q), place, both(rest_m, rest_q))
    lines += _label('Where it lives', where if saved else 'on this machine')
    lines += _label('What it can answer', demo.get('answers') or ('Questions about ' + f.use_case if f.use_case else
                                                                  'Questions about the metrics above'))
    if demo.get('tour'):
        lines += _label('See the environment', demo['tour'])
    import side_page
    made = side_page.details(f)
    held = [] if f.interaction.get('flow') == 'demo' else [x for x in made['metrics'] + made['queries'] if x.get('status') == 'look']
    if held:
        n_metrics = sum(1 for x in held if x in made['metrics'])
        what = ' and '.join(w for w in (count(n_metrics, 'metric') if n_metrics else '',
                                        count(len(held) - n_metrics, 'query', 'queries') if len(held) > n_metrics else '') if w)
        failed = all(str(x.get('why') or '').startswith('SQL execution failed') for x in held)
        lines += _label('Needs a look', '%s: %s did not run on the data. Each, with its reason, is on the side page.'
                        % (what, 'its SQL' if len(held) == 1 else 'their SQL') if failed else
                        '%s did not pass %s check. Each, with its reason, is on the side page.' % (what, 'its' if len(held) == 1 else 'their'))
    kept = '' if f.interaction.get('flow') == 'demo' else _kept(f)
    if kept:
        lines += _label('Kept back', kept + '. Each, with why, is on the side page.')
    lines += ['', 'What would you like to ask?']
    # Two questions the checked context answers, then the person's own, in words: the host's free-text entry reads as
    # "Other", which a tester did not take for a way to ask (2026-11-19).
    options = _supported(made, limit=2) + [{'label': 'Ask my own question', 'description': 'Type any question; it is '
                                            'answered from the metrics and queries just built'}]
    if len(options) < 3:
        options.append({'label': 'Show what was built', 'description': 'Each metric and query with its checked value, and how each was reached'})
    options.append({'label': "I'm done for now", 'description': 'Stop here; the context stays where it was kept'})
    return {'header': 'Ask', 'question': '\n'.join(lines),
            'options': options, 'shown': ['saved'] if shown else []}
