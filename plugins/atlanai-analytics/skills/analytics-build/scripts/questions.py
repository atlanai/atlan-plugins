"""The flow's big questions and its last message, composed from the run's own files: where the context lives, after the checks,
what to ask, once it is ready, and what was built and how to use it, once the flow ends.

The agent asks exactly what this prints. Every count comes from the run; a demo's words come from its facts file,
and only on a run the journey records as a demo, so they never reach the person's own data. Without widgets the
text says, in sentences, what a card would: no bars, since a question's text is set in a proportional font.
"""
from pathlib import Path
import re

import visuals
from run_facts import RunFacts, agent_name, count, read_json, sentence, short_name, their

# `{folder}` is the working folder a chat opens in to find the run.
SAVE_OPTIONS = (
    ('Save to Atlan (Recommended)', 'Ask from any chat that has Atlan Analytics and your warehouse connected. Everyone with '
     'access gets the same metrics. You sign in once.'),
    ('Keep it on this machine', 'Ask from a chat opened in {folder}. Nobody else can use it. Ask me to save it to Atlan '
     'any time.'),
)


def _folder(f):
    """The working folder as the person finds it: its path, or by name when the path would not fit the ready block."""
    path = f.folder
    if not path:
        return 'this folder'
    return path if len(path) <= 40 else 'the %s folder' % Path(path).name


def _save_options(f):
    return [{'label': a, 'description': b.replace('{folder}', _folder(f))} for a, b in SAVE_OPTIONS]


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


def _review(n):
    return '%d %s your review' % (n, 'needs' if n == 1 else 'need')


def _where(f, demo):
    return '%s data' % their(demo.get('firm')) if demo.get('firm') else 'your %s data' % f.warehouse if f.warehouse else 'your data'


def _row(name, value):
    """One ready-block line, "- Label: value", readable in any font: a host shows the question in a proportional one, so
    no column is padded."""
    return ['- %s: %s' % (name, value)]


def _sources(f, demo):
    """What the build read, in words: "your Power BI dashboard and 110 past queries", else its tables."""
    chosen = f.inputs or {'bi': bool(f.dashboard), 'history': bool(f.history)}
    whose = their(demo.get('firm'))
    parts = (['%s %s dashboard' % (whose, f.dashboard['tool']) if f.dashboard.get('tool') else '%s dashboard' % whose]
             if chosen['bi'] and (f.dashboard or f.funnel.get('tiles')) else []) + (
        [count((f.history or {}).get('statements') or f.query_log, 'past query', 'past queries')]
        if chosen['history'] and ((f.history or {}).get('statements') or f.query_log) else [])
    # A warehouse build reads its tables in place, so no table was prepared here: count the ones it was scoped to. The
    # closing message said "from 0 tables" for a build that read one.
    tables = len(f.tables) or (f.scoped_tables if f.warehouse else 0)
    return ' and '.join(parts) or (count(tables, 'table') if tables else '%s data' % whose)


def _tested(f, c, demo):
    """The tests' result as one or two sentences: all passed, some need your review, or skipped; then how many match the
    dashboard's own formulas, when any were compared."""
    if f.checks_skipped:
        return "They aren't tested yet: you skipped the tests."
    where = _where(f, demo)
    if not f.checks_over:
        return 'They were tested on %s.' % where
    passed, look = c['passed'], c['need_look']
    if not look:
        said = 'It passed its test on %s.' % where if passed == 1 else 'Both passed their tests on %s.' % where \
            if passed == 2 else 'All %d passed their tests on %s.' % (passed, where)
    else:
        said = ('None passed yet on %s.' % where if not passed else '1 passed its test on %s.' % where if passed == 1
                else '%d passed their tests on %s.' % (passed, where)) + ' %s.' % _review(look)
    same, differ = _compared(c)
    if same or differ:
        said += ' %d of %d %s the dashboard\'s own formulas.' % (same, same + differ, 'matches' if same == 1 else 'match')
    return said


# The save question's own question, in the step's words ("Choose where to store your context").
STORE = 'Where do you want to store your context?'


def _validated(f, c, demo, by_tool=False, skipped=False):
    """The validation's result as one sentence: all passed, some need your review, read-only, or skipped."""
    where = _where(f, demo)
    if skipped:
        return 'Not validated yet on %s: you skipped the tests.' % where
    if by_tool:
        return 'Validated on %s, read-only.' % where
    return 'Validated on %s: %s.' % (where, 'all passed' if not c['need_look'] else '%d passed, %s' % (
        c['passed'], _review(c['need_look'])) if c['passed'] else 'none passed yet, %s' % _review(c['need_look']))


def _built(run, f, demo, widget, data, engine_dir, source):
    """What was built, said once: the build's headline and what it answers from, then the side page's address. With
    widgets the build card above says all of it, so the text adds none. No bars: a question's text is set in a
    proportional font, where block characters break and wrap (0.9.75's save question)."""
    if widget:
        return [], []
    c = f.context
    spec = visuals.build_spec('build', run, data, engine_dir) or {}
    took = spec.get('took')
    head = ['Your %s is built%s.' % (agent_name(f, demo), ', in %d min' % took if took else ''),
            'It answers from %s and %s, built from %s.' % (count(len(c['metrics']), 'metric'),
                                                         count(c['queries'], 'saved query', 'saved queries'), source)]
    page = spec.get('page')
    return head, (["Every metric and how it's calculated: %s" % page] if page else [])


def save(run, widget=False, data=None, engine_dir=None):
    """The save question, after every check has run: what was built, its validation, then where to store it."""
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
    # The copy budget (the team, 2026-11-22): each fact once, no bars, and no "Next:" line before the question.
    head, link = _built(run, f, demo, widget, data, engine_dir, _sources(f, demo))
    lines = head + [_validated(f, c, demo, by_tool=by_tool)]
    # A metric whose number differs from its tile's is said beside "all passed", never hidden by it.
    if visuals.mismatch(c['metrics']):
        lines[-1] += ' ' + visuals.mismatch(c['metrics']) + '.'
    lines += _one_metric(run, f, demo, widget) + ([''] + link if link else []) + ['', STORE]
    # The task list numbers the steps, so the header names this one: a second numbering disagreed with the list.
    return {'header': 'Save', 'question': '\n'.join(lines), 'options': _save_options(f),
            # Without widgets the text says what the build and checks cards would: they are not drawn again.
            'shown': [] if widget else ['build', 'context'], 'results': by_tool}


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
    report = f.report
    report = 'your dashboard' if not report else 'the %s' % report if re.search(r'\b(dashboard|report)$', report, re.I) \
        else 'the %s dashboard' % report
    tiles = (f.funnel or {}).get('tiles')
    head, link = _built(run, f, {}, widget, data, engine_dir, '%s on %s' % (count(tiles, 'tile'), report) if tiles else report)
    lines = head + [_validated(f, c, {}, by_tool=by_tool, skipped=skipped)]
    match = '' if skipped else _matched(c)
    if match:
        lines.append(match[:1].upper() + match[1:] + '.')
    kept, n_kept = _kept(f)
    if kept:
        lines.append('Held back: %s. No answer uses %s.' % (kept, 'it' if n_kept == 1 else 'these'))
        lines.append('Fixing %s takes a rebuild.' % ('it' if n_kept == 1 else 'them'))
    cant = side_page.cant_build(f)
    if cant:
        lines.append("%s stayed out. The side page says why." % count(len(cant), 'tile'))
    lines += ([''] + link if link else []) + ['', STORE]
    return {'header': 'Save', 'question': '\n'.join(lines), 'options': _save_options(f),
            'shown': [] if widget else ['build'], 'results': by_tool}


def _compared(c):
    """The metrics whose number sits beside a dashboard tile's own formula, as (how many match, how many differ)."""
    tiles = [m for m in (c or {}).get('metrics') or [] if m.get('tile')]
    same = sum(1 for m in tiles if m['tile'].get('same'))
    return same, len(tiles) - same


def _matched(c):
    """'3 match the dashboard's own formulas, 1 differs', or '' when no number was set beside a tile."""
    same, differ = _compared(c)
    if same and differ:
        return '%d %s the dashboard\'s own formulas, %d %s' % (same, 'matches' if same == 1 else 'match', differ,
                                                               'differs' if differ == 1 else 'differ')
    if same:
        return '%d %s the dashboard\'s own formulas' % (same, 'matches' if same == 1 else 'match')
    return '%d %s from the dashboard\'s own formulas' % (differ, 'differs' if differ == 1 else 'differ') if differ else ''


def _kept(f):
    """What the save held back, each reason with its count: "1 metric and 1 saved query (1 didn't match the
    dashboard's number, 1 left out a filter its tile always applies)", and how many items that is. Empty when nothing
    was held back."""
    import side_page
    h = side_page.held(f)
    if not (h['metrics'] or h['queries']):
        return '', 0
    return '%s (%s)' % (' and '.join(x for x in (count(h['metrics'], 'metric') if h['metrics'] else '',
                                                count(h['queries'], 'saved query', 'saved queries') if h['queries'] else '') if x),
                        h['why']), h['metrics'] + h['queries']


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
    lines = ['', 'One metric in full: %s, %s on %s' % (m['name'], m.get('value') or 'tested', _where(f, demo))]
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


# The longest question offered as an option.
LONGEST = 120


def _supported(made, limit=3):
    """Questions the checked context answers, in the words the build wrote for each: the dashboard's tiles first,
    then the past queries asked most, each only when its metric passed its check."""
    passed = [m for m in made['metrics'] if m.get('status') == 'checked']
    picked, seen = [], set()
    for m in passed:
        # Its dashboard tile's wording first, else the shortest a past query has: an option reads at a glance.
        # A question in words only: an engine label such as "sum:amount:ok" is not one.
        # Up to 120 characters: a tile's own question ran 95 (Pod Targets, 2026-11-22) and a 90 cap offered nothing.
        ranked = sorted((s for s in m.get('sources') or [] if str(s.get('question') or '').strip().endswith('?')
                         and len(str(s.get('question')).strip()) <= LONGEST),
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
        if len(picked) < limit and q.get('status') == 'checked' and text.endswith('?') and len(text) <= LONGEST and text.lower() not in seen:
            seen.add(text.lower())
            picked.append({'label': text, 'description': '%s · a saved query' % q['name']})
    return picked


def _split(saved_items, f, run, data, engine_dir):
    """A part-way save by kind, "1 metric in Atlan; 1 metric and 2 saved queries on this machine", when the saved card's
    counts add up to what was saved. A bare "1 of 4" read as a count that disagreed with the card's (2026-11-22)."""
    try:
        spec = visuals.build_spec('saved', run, data, engine_dir)
        kept_m, kept_q = len(spec.get('metrics') or []), int(spec.get('queries') or 0)
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        return None
    if kept_m + kept_q != saved_items:
        return None
    both = lambda m, q: ' and '.join(x for x in (count(m, 'metric') if m else '',
                                                count(q, 'saved query', 'saved queries') if q else '') if x)
    rest_m, rest_q = len(f.context['metrics']) - kept_m, f.context['queries'] - kept_q
    return both(kept_m, kept_q), both(rest_m, rest_q)


def _stored(f, demo, run=None, data=None, engine_dir=None):
    """Where the context lives and how to reach it later, as (where, how), from the run's own record of the save."""
    saved = f.publication if not f.local else {}
    whole = len(f.context['metrics']) + f.context['queries']
    # A demo's topic in its own words ("Questions about job margin"), not its focus label ("Job margin").
    answers = str(demo.get('answers') or '')
    # A dashboard is named as a person would: "ask about the Box Office Pulse dashboard", never without "the".
    report = f.report and ('the %s' % f.report if re.search(r'\b(dashboard|report)$', f.report, re.I) else 'the %s dashboard' % f.report)
    topic = (answers[len('Questions about '):] if answers.startswith('Questions about ') else '') or \
        short_name(f.use_case) or report or '%s data' % their(demo.get('firm'))
    if saved:
        where = 'Saved to Atlan' + (', in %s' % saved['workspace'] if saved.get('workspace') else '')
        if saved.get('items', whole) < whole:
            # A save that stopped part-way says what is where.
            where = '%d of %d saved to Atlan, the rest stored on this machine' % (saved['items'], whole)
            split = _split(saved['items'], f, run, data, engine_dir) if run is not None else None
            if split:
                where = '%s saved to Atlan%s; %s stored on this machine' % (
                    split[0], ', in %s' % saved['workspace'] if saved.get('workspace') else '', split[1])
        return where, ('open a chat with Atlan Analytics, signed in to Atlan, with %s connected,'
                       % (f.warehouse or 'your warehouse')), topic
    return 'Stored on this machine, in %s' % (f.folder or 'this folder'), 'open a chat in that folder', topic


def ready(run, widget=False, data=None, engine_dir=None, accuracy=False, recap=False):
    """The ready question, once the context is stored: where it lives, how to reach it, then what to ask. The save
    question just said what was built, so this says it again only with `recap` (a resumed run, where the person has
    not seen the save question in this chat). With `accuracy`, the accuracy check is available and has not run."""
    f = RunFacts(run, engine_dir)
    demo = _demo(run, data)
    c = f.context
    if not c:
        raise ValueError('Ask what to ask once the context is saved or kept.')
    if not f.local and not f.publication:
        # The run must know where the context lives: kept without being recorded, the question loses what was kept.
        raise ValueError('Record where the context lives first: run `PROJECT journey keep-local` when the person kept it on '
                         'this machine, or finish the save to Atlan; then ask again.')
    spec = visuals.build_spec('saved', run, data, engine_dir)
    # The save question already gave the validation's result: the saved card in this text leaves it out.
    spec.pop('total', None)
    shown = '' if widget else visuals.render('saved', spec, plain=True)
    import side_page
    made = side_page.details(f)
    lines = ([shown, ''] if shown else [])
    if recap:
        lines += ['Your %s is ready.' % agent_name(f, demo),
                  'It answers from %s and %s, built from %s.' % (count(len(c['metrics']), 'metric'),
                                                                 count(c['queries'], 'saved query', 'saved queries'),
                                                                 _sources(f, demo)),
                  _tested(f, c, demo), '']
    where, how, topic = _stored(f, demo, run, data, engine_dir)
    lines.append('%s. To use it later, %s and ask about %s.' % (where, how, topic))
    if demo.get('tour'):
        lines.append('To tour the demo data: %s shows the firm and its data.' % demo['tour'])
    # One the save held back is said once, under "Held back", as the side page counts it: the review's run counted a
    # failed, held-back metric in both lines.
    held = [] if f.interaction.get('flow') == 'demo' else [x for x in made['metrics'] + made['queries']
                                                           if x.get('status') == 'look' and x.get('stage') != 'kept']
    if held:
        n_metrics = sum(1 for x in held if x in made['metrics'])
        what = ' and '.join(w for w in (count(n_metrics, 'metric') if n_metrics else '',
                                        count(len(held) - n_metrics, 'saved query', 'saved queries') if len(held) > n_metrics else '') if w)
        lines.append('Needs your review: %s %s. The side page says why.%s'
                     % (what, 'didn\'t pass its test' if len(held) == 1 else 'didn\'t pass their tests',
                        # Kept on this machine, they stay in what your agent answers from.
                        ' %s can still be used here, so check answers that name %s.' % (
                            ('It', 'it') if len(held) == 1 else ('They', 'them')) if f.local else ''))
    kept, n_kept = ('', 0) if f.interaction.get('flow') == 'demo' else _kept(f)
    if kept:
        lines.append('Held back: %s. No answer uses %s.' % (kept, 'it' if n_kept == 1 else 'these'))
    # At most four options: questions first, then what comes after asking. The person's own question goes in the
    # host's free-text entry ("Other"), which the text names: an "Ask your own question" option led nowhere.
    tiles = f.report or (f.inputs or {}).get('bi') or f.dashboard.get('tool')
    check = [{'label': 'Check how accurate it is', 'description': 'Test your agent on questions %s, and see how many it '
              'gets right. About 8 minutes.' % ('your dashboard already answers' if tiles else 'your team already asks')}] \
        if accuracy else []
    # Own data offers the dashboard here (the person's words, 2026-11-22); a demo keeps its own order, asking first.
    board = [{'label': 'Build a dashboard', 'description': "A dashboard page built from your agent's answers."}] \
        if f.interaction.get('flow') != 'demo' and not f.interaction.get('dashboard') else []
    done = {'label': 'I\'m done', 'description': 'Your context stays %s.' % (
        'saved in Atlan' if f.publication and not f.local else 'stored on this machine')}
    suggested = _supported(made, limit=4 - 1 - len(check) - len(board))
    options = suggested[:1] + check + suggested[1:] + board
    if options:
        options.append(done)
    # With nothing to pick, no native question: the text ends on the question and the person types.
    lines += ['', 'What would you like to ask?' + (' Pick one, or type your own in Other.' if suggested else
                                                   ' Type it in Other, or pick what\'s next.' if options else '')]
    return {'header': 'Ask', 'question': '\n'.join(lines),
            'options': options, 'shown': ['saved'] if shown else []}


def close(run, widget=False, data=None, engine_dir=None, score=None, scored=None):
    """The last message of a finished flow: what was built, and how to use it from now on. No question and no banner:
    the celebration was at the result. `score` and `scored` are the accuracy check's percentage and question count,
    when it ran."""
    f = RunFacts(run, engine_dir)
    demo = _demo(run, data)
    c = f.context
    if not c or not (f.local or f.publication):
        raise ValueError('Close the flow once the context is stored: on this machine or saved to Atlan.')
    import side_page
    made = side_page.details(f)
    validated = ('not validated yet: you skipped the tests' if f.checks_skipped else
                 'validated on %s' % _where(f, demo) + (', %s' % _review(c['need_look']) if c.get('need_look') else ''))
    lines = ['Your %s is built and ready.' % agent_name(f, demo), '', 'What you have',
             '- %s and %s from %s, %s.' % (count(len(c['metrics']), 'metric'),
                                           count(c['queries'], 'saved query', 'saved queries'), _sources(f, demo), validated)]
    if score is not None and scored:
        lines.append('- Accuracy: %d%% on %s.' % (score, 'its test question' if scored == 1 else '%d test questions' % scored))
    # What the flow built after the context is part of what the person has: the closing message left the dashboard out.
    if f.interaction.get('dashboard_result') == 'built':
        lines.append("- A dashboard built from your agent's answers.")
    where, how, topic = _stored(f, demo, run, data, engine_dir)
    example = next(iter(_supported(made, limit=1)), {}).get('label')
    local = f.local or not f.publication
    lines += ['', 'How to use it']
    # A quoted example ends the line on its own question mark, as the person would type it.
    like = ', like "%s"' % example if example else '.'
    if local:
        lines.append("- It's stored on this machine, in %s. Open a Claude session in that folder and ask anything about "
                     '%s%s' % (f.folder or 'this folder', topic, like))
    else:
        lines.append('- %s. To ask it, %s and ask anything about %s%s' % (where, how, topic, like))
    lines.append('- When a question goes beyond its metrics, it says so, writes its own SQL and names what it assumed.')
    if local:
        lines.append('- To share it with your team, save it to Atlan: in that folder, say "save my agent to Atlan".')
    return {'text': '\n'.join(lines)}
