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

BANNER = (" *   .  '   *    .   '  *   .    '   *  .",
          "   '   *   .  CONTEXT BUILT  .   *   '",
          " .   *    '   .    *   '    .   *    '  .")
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


def _sources(f):
    """What the context was built from, as the person chose it."""
    chosen = f.inputs or {'bi': bool(f.dashboard), 'history': bool(f.history)}
    return ('dashboard and past queries' if chosen['bi'] and chosen['history'] else 'dashboard' if chosen['bi']
            else 'past queries' if chosen['history'] else 'tables')


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
    demo = _demo(run, data)
    c = f.context
    # A warehouse's checks run with its own tool and leave no verdict here: the agent adds the line of results it holds.
    by_tool = bool(c) and not f.checks_here and not f.checks_over
    if not f.checks_over and not by_tool:
        # Before every item has its verdict, the counts would be false ("0/4 checked, 4 need a look").
        waiting = c['total'] - c['metrics_checked'] - c['queries_checked'] if c else None
        raise ValueError('Ask where the context lives once every check has run%s.' % (
            ': %d of %d still wait for their sample check. Run each; if one cannot run, record `PROJECT progress '
            '--stage preview --status running` and ask again, and it counts as needing a look' % (waiting, c['total'])
            if waiting else ''))
    lines = list(BANNER) + ['', 'You turned %s %s into context.' % (their(demo.get('firm')), _sources(f)),
                            'That was the hard part: each number now has one agreed rule.', '']
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
    lines += _one_metric(run, f, demo)
    lines += ['', 'Where should this context live?']
    return {'header': 'Step 4 of 6' if demo or f.interaction.get('flow') == 'demo' else 'Step 5 of 7',
            'question': '\n'.join(lines), 'options': [{'label': a, 'description': b} for a, b in SAVE_OPTIONS],
            'shown': [k for k, v in (('build', card), ('context', shown)) if v], 'results': by_tool}


def _one_metric(run, f, demo):
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
    page = visuals.page_link(run, 'edit', {'metric': m['name']})
    if came:
        lines.append('  ' + came[:1].upper() + came[1:] + (': ' + page if page else '.'))
    return lines


def _supported(run, f, limit=3):
    """Questions the checked context answers, in the words the build wrote for each: the dashboard's tiles first,
    then the past queries asked most, each only when its metric passed its check."""
    import side_page
    passed = [m for m in side_page.details(f)['metrics'] if m.get('status') == 'checked']
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
    for q in side_page.details(f)['queries']:
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
    lines += ['  ' + ' ' * LABEL + 'from %s,' % _built_from(f), '  ' + ' ' * LABEL + 'checked on %s' % _where(f, demo)]
    where = '%s in Atlan Trident' % saved['workspace'] if saved.get('workspace') else 'Atlan Trident'
    whole = len(c['metrics']) + c['queries']
    if saved and saved.get('items', whole) < whole:
        # A save that stopped part-way says what is where.
        where = '%d of %d in %s, the rest on this machine' % (saved['items'], whole, where)
    lines += _label('Where it lives', where if saved else 'on this machine')
    lines += _label('What it can answer', demo.get('answers') or ('Questions about ' + f.use_case if f.use_case else
                                                                  'Questions about the metrics above'))
    if demo.get('tour'):
        lines += _label('See the environment', demo['tour'])
    import side_page
    made = side_page.details(f)
    held = [] if demo else [x for x in made['metrics'] + made['queries'] if x.get('status') == 'look']
    if held:
        n_metrics = sum(1 for x in held if x in made['metrics'])
        what = ' and '.join(w for w in (count(n_metrics, 'metric') if n_metrics else '',
                                        count(len(held) - n_metrics, 'query', 'queries') if len(held) > n_metrics else '') if w)
        failed = all(str(x.get('why') or '').startswith('SQL execution failed') for x in held)
        lines += _label('Needs a look', '%s: %s did not run on the data. Each, with its reason, is on the side page.'
                        % (what, 'its SQL' if len(held) == 1 else 'their SQL') if failed else
                        '%s did not pass %s check. Each, with its reason, is on the side page.' % (what, 'its' if len(held) == 1 else 'their'))
    lines += ['', 'What would you like to ask?']
    flow_demo = f.interaction.get('flow') == 'demo'
    options = _supported(run, f)
    # The question tool needs two options; the person can always type their own question too.
    for label, description in (('Show what was built', 'Each metric and query with its checked value, and how each was reached'),
                               ("I'm done for now", 'Stop here; the context stays where it was kept')):
        if len(options) < 2:
            options.append({'label': label, 'description': description})
    return {'header': 'Step 5 of 6' if flow_demo else 'Step 6 of 7', 'question': '\n'.join(lines),
            'options': options, 'shown': ['saved'] if shown else []}
