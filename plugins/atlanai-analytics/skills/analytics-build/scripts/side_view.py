"""The run's steps for the side page: the flow's own steps, what came out of each and how, read from the run's
own files.

The steps and their status are the host's task list (`checklist`). Every number comes from a file the run wrote,
and every step shows the run's current truth. A step's "how we got there" lists only the work this run will do and
names the part of the plugin that does it. `note` logs the moments the chat has shown, so the helper names any that
are due and not shown yet.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None

from run_facts import BI_TOOLS, RunFacts, count, read_json, table_name


# Who did a piece of work, in the words the person reads.
YOU, DEMO, SETUP, SURVEY, ENGINE, ATLAN, ASK = (
    'You', 'Demo guide', 'Setup helper', 'Source survey', 'Metrics engine', 'Atlan', 'Ask guide')


def node(title, text, by, n=None, always=False, done=None, warn=False):
    """One piece of work. It ticks as soon as the run has finished it: by default, once the run left its count.
    A finished step shows it only when the run left its count, or when it has none to leave. `warn` marks a piece that
    finished with something for a person to look at."""
    return {'t': title, 'd': text, 'by': by, 'n': n, 'always': always, 'done': n is not None if done is None else done, 'warn': warn}


def ratio(a, b, form='%d of %d'):
    return form % (a, b) if isinstance(a, int) and isinstance(b, int) else None


# Each step of the flow's own task list: its pieces of work, and what came out of it --------------

def intent(f, s):
    said = f.interaction.get('intent') or {}
    return {'how': [node('You say what you want to answer', said.get('domain') or 'The use case decides which sources and '
                         'metrics matter.', YOU, always=True, done=bool(said.get('domain'))),
                    node('You name the goal', said.get('purpose') or 'What the agent should help you do.', YOU, always=True,
                         done=bool(said.get('purpose')))],
            'fact': said.get('domain', '')}


def _tables_node(f):
    """Local data loads into a small database on this machine; a warehouse is read where it is."""
    if f.warehouse:
        return node("Read the chosen tables' columns", 'Their names and types, through the read-only connection; no data '
                    'is copied.', SETUP, count(f.scoped_tables, 'table') if f.scoped_tables else None)
    tables, rows = f.tables, f.table_rows
    if tables and rows is None:
        # An existing database, bound as it is: the checks read it without changing it.
        return node('Open the database read-only', 'The checks run on its tables without changing anything.', SETUP,
                    count(len(tables), 'table'))
    return node('Load the tables on this machine', 'Into a small read-only database, so every number can be tested '
                'without touching anything real.', SETUP,
                '%s · %s rows' % (count(len(tables), 'table'), '{:,}'.format(rows)) if tables else None)


def sources(f, s):
    dash = f.dashboard
    # A demo's run starts from its copied files, so both pieces are done once there is a run to show.
    how = ([node('You pick a demo', 'A made-up firm with its own dashboard, past queries and data.', YOU, always=True, done=True),
            node('Copy its files into a working folder', 'Nothing leaves this machine until you choose to save.', DEMO, always=True,
                 done=True)]
           if s['demo'] else [])
    found, kinds = f.found
    how.append(node('Find every source', 'Each file is listed and fingerprinted, so a later change is noticed.', SETUP,
                    count(found, 'source') if found else None))
    if f.warehouse:
        how.append(node('Connect to the warehouse', 'A read-only connection: only single read statements are ever sent.',
                        SETUP, f.warehouse))
    if dash or f.usable(*BI_TOOLS):
        how.append(node('Read the dashboard', 'Its tiles and the formula behind each one.', SURVEY,
                        count(dash['formulas'], 'formula') if dash.get('formulas') else None))
    if f.usable('query_history'):
        history = f.history
        how.append(node('Count the past queries', 'What analysts actually ran is the best evidence of what they measure.', SETUP,
                        '%s · run %s times' % ('{:,}'.format(history['statements']), '{:,}'.format(history['runs'])) if history else None))
    if s['demo']:
        # A demo loads its data in this step; own data loads or reads its tables when the build starts.
        how.append(_tables_node(f))
    tables = f.tables
    return {'how': how, 'fact': ', '.join(kinds),
            'result': ([{'title': 'Found', 'list': [[k, ''] for k in kinds]}] if kinds else [])
            + ([{'title': 'Tables loaded', 'list': [[table_name(t[0]), count(t[1], 'row') if t[1] is not None else ''] for t in tables]}]
               if s['demo'] and tables else [])}


def _chosen(f):
    """What the build reads: the person's choice once the build is configured; before that, what the run could use."""
    if f.inputs:
        return f.inputs
    return {'bi': bool(f.usable(*BI_TOOLS)), 'history': bool(f.usable('query_history') or f.warehouse)}


def scope(f, s):
    inputs = f.inputs
    chosen = None
    if inputs:
        chosen = ('Dashboard and past queries' if inputs['history'] and inputs['bi'] else 'Past queries' if inputs['history']
                  else 'Dashboard only' if inputs['bi'] else 'The chosen tables')
        if f.warehouse and f.scoped_tables:
            chosen += ', ' + count(f.scoped_tables, 'table')
    return {'how': [node('You choose what to build from', 'More sources give fuller context; the dashboard alone is faster.'
                         if s['demo'] else 'The tables and dashboards the metrics come from.', YOU, chosen, always=True,
                         done=bool(inputs))],
            'fact': chosen or ''}


def metrics(f, s):
    st, use = f.stage, _chosen(f)
    how = [] if s['demo'] else [_tables_node(f)]
    if use['history']:
        if f.warehouse:
            history = f.history
            how.append(node('Read the past queries from %s' % f.warehouse, 'Successful read statements on the chosen tables, '
                            'through the read-only connection.', SETUP, count(history['statements'], 'query', 'queries') if history else None))
        raw, scanned, kept, shapes = f.query_log, st('h0_corpus', 'scanned'), st('h0_corpus', 'kept'), st('h1_shapes', 'canonical_shapes')
        read = (f.history or {}).get('statements')
        if read and raw and read > raw:
            how.append(node('Keep the queries the build can use', 'Statements that failed or do not read data are left '
                            "out, and so are any past the build's size and count limits.", SETUP, ratio(read, raw, '%d → %d')))
        if raw and scanned is not None and raw > scanned:
            how.append(node('Set aside queries with names in them', 'A privacy filter holds back any query that looks like it '
                            'names a person or a business.', SETUP, ratio(raw, scanned, '%d → %d')))
        how += [node("Keep queries about the dashboard's tables" if use['bi'] else 'Keep queries about the chosen tables',
                     "Queries about other tables can't explain these numbers.", ENGINE, ratio(scanned, kept, '%d → %d')),
                node('Group repeats into questions', 'Queries that differ only in a value, such as a date, count as one; '
                     'plain table dumps are left out.', ENGINE, ratio(kept, shapes, '%d → %d'))]
    if use['bi']:
        tried = st('a4_witness_bi', 'witnessed')
        how += [node('Translate each tile and chart', 'The formula behind each one becomes a query that runs on the data.', ENGINE,
                     ratio(st('a4_witness_bi', 'verdict_pass') or 0, tried) if tried is not None else None,
                     done='a3_translate' in f.stages or tried is not None),
                node('Review each translation', 'A second read of every translation, before it is used.', ENGINE,
                     count(st('a3b_emit', 'prompted'), 'translation') if st('a3b_emit', 'prompted') and 'a3b_ingest' in f.stages else None)]
    described, none, proposals, made = st('m2_ingest', 'described'), st('x1_ingest', 'no_metric'), st('x1_ingest', 'proposals'), st('x2_ingest', 'metrics_out')
    measuring = described - none if isinstance(described, int) and isinstance(none, int) else None
    what = ("The dashboard's tiles and charts and the past queries" if use['bi'] and use['history']
            else "The dashboard's tiles and charts" if use['bi'] else 'The past queries')
    how += [
        node('Describe every query in plain words', what + ', each said so a person, or another agent, can tell what it '
             'answers.', ENGINE, count(described, 'query', 'queries') if described else None),
        node('Keep the ones that measure a number', 'Lists and row counts stay as saved queries, not metrics.', ENGINE,
             ratio(measuring, described)),
        node('Merge numbers that share a rule', ('Those queries hold %s; numbers worked out the same way become one metric.'
                                                 % count(proposals, 'number')) if proposals else
             'Numbers worked out the same way become one metric.', ENGINE, ratio(proposals, made, '%d → %d')),
        node("Write each metric's full rule", 'What it counts, what it leaves out and how it is worked out.', ENGINE,
             count(made, 'rule') if made and 'x3_ingest' in f.stages else None),
        node('Link related metrics', 'Such as a margin that is built from two costs.', ENGINE,
             count(st('x4_ingest', 'edges'), 'link') if st('x4_ingest', 'edges') is not None else None)]
    c = f.context
    checked = bool(c)
    n_metrics, n_queries = (len(c['metrics']), c['queries']) if checked else (None, None)
    all_metrics = checked and c['metrics_checked'] == n_metrics
    all_queries = checked and c['queries_checked'] == n_queries
    where = 'on %s' % f.warehouse if f.warehouse else 'on the data'
    how += [node('Check each item is complete', 'Every metric and query needs all its parts before it runs.', SETUP,
                 '%s + %s' % (count(n_metrics, 'metric'), count(n_queries, 'query', 'queries')) if checked else None),
            node('Run each metric ' + where, 'Its examples, its full calculation, and a test that its filter leaves '
                 'nothing in when nothing should count.', SETUP,
                 ratio(c['metrics_ran'], n_metrics) if checked and n_metrics and c['metrics_checked'] else None,
                 done=all_metrics, warn=all_metrics and c['metrics_ran'] < n_metrics),
            node('Run each query ' + where, 'Each one runs as written.', SETUP,
                 ratio(c['queries_ran'], n_queries) if checked and n_queries and c['queries_checked'] else None,
                 done=all_queries, warn=all_queries and c['queries_ran'] < n_queries)]
    # While the checks run, what ran and failed; once the step is over, whatever did not pass, as the save question counts.
    # A warehouse's own checks leave no verdict here: only what the run itself held counts.
    held = (c['failed'] if s['open'] or not f.counts_known else c['need_look']) if checked else 0
    if not (all_metrics and all_queries) or held:
        how.append(node('Hold what needs a look', 'Anything that did not pass its check is kept back and listed for a '
                        'person, never shown as a result.', SETUP, '%d need a look' % held if held else None,
                        done=all_metrics and all_queries or not s['open'], warn=bool(held)))

    def came_from(m):
        return ', '.join(x for x in ('%d from the dashboard' % m['tiles'] if m['tiles'] else '',
                                     '%d from past queries' % m['queries'] if m['queries'] else '') if x)
    merged = f.merged
    return {'how': how, 'fact': count(len(merged), 'metric') if merged else '',
            'result': ([{'title': 'Metrics, and what each came from', 'list': [[m['name'], came_from(m)] for m in merged]}] if merged else [])
            + ([{'title': 'Each metric, run on the data', 'list': [[m['name'], m['value']] + ([] if m['ok'] else ['warn']) for m in c['metrics']]}]
               if checked else [])}


def save(f, s):
    if f.local:
        return {'how': [node('You keep the context on this machine', 'Nothing is saved to Atlan; answers use the local copy.', YOU,
                             always=True, done=True)],
                'fact': 'Kept on this machine'}
    # The save's own files land in order, so each piece ticks as it happens: the workspace and its inventory, the plan,
    # each batch as it is created, then the receipt of what was read back as live.
    p = f.publication
    planned = f.file('context', 'plan.json').get('counts') or {}
    picked = f.file('context', 'inventory.json').get('workspace_id') or f.file('authorization.json').get('workspace_id')
    # The save writes its receipt, a list of what was read back as live, once every item is read back.
    saved = len(f.file('context', 'published.json', kind=list))

    def made(kind):
        part = planned.get(kind)
        return sum(int(part.get(x) or 0) for x in ('create', 'revise')) if isinstance(part, dict) else None
    how = [node('You choose where the context lives', 'Keep it on this machine, or save it to Atlan, where your team and its '
                'agents can use it. Saving goes like this:', YOU, always=True, done=bool(picked or p)),
           node('You pick a workspace', 'Where the metrics live in Atlan.', YOU, None if s['private'] else p.get('workspace'),
                always=True, done=bool(picked or p)),
           node('Plan the save', 'What is new, what changed, and what each item links to.', SETUP,
                '%s · %s' % (count(made('metrics'), 'metric'), count(made('queries') or 0, 'query', 'queries')) if made('metrics') is not None else None),
           node('Save each metric and query', 'Each one becomes an item in the workspace.', ATLAN,
                count(p['items'], 'item') if p else '%d saved so far' % saved if saved else None, done=bool(p)),
           node('Link each metric to its sources', 'Queries to the metrics they feed, and metrics to the ones they build on.', ATLAN,
                count(p['links'], 'link') if p and p.get('links') else None),
           node('Read every item back', 'Proof that each one is live, which is what lets agents find it.', ATLAN,
                '%d live' % p['items'] if p else None)]
    return {'how': how, 'fact': '%d live in Atlan' % p['items'] if p else '',
            'result': [{'title': 'Saved to Atlan Trident', 'kv': ([] if s['private'] else [['Workspace', p.get('workspace') or 'Atlan']])
                        + [['Metrics', str(len(p['metrics']))], ['Queries', str(p['queries'])]]}] if p else []}


def ask(f, s):
    a = f.file('answer.json')
    answered = bool(a.get('headline'))
    proof = [p for p in a.get('proof') or [] if isinstance(p, dict)] if isinstance(a.get('proof'), list) else []
    rows = a.get('rows') if isinstance(a.get('rows'), list) else []
    breakdown = a.get('breakdown') if isinstance(a.get('breakdown'), dict) else {}
    how = [node('Read the saved rules', 'Answers use the rules this run saved, not a fresh guess at the tables.', ASK,
                ('%s · %s' % (count(len(proof), 'rule'), 'on this machine' if f.local else 'from Atlan')) if answered and proof else None,
                done=answered),
           node('Run them on the data', 'The query runs fresh, so the number is today\'s.', ASK,
                count(len(rows), 'row') if answered and rows else None, done=answered)]
    if isinstance(breakdown.get('steps'), list) and breakdown['steps']:
        how.append(node('Break the answer down', 'Into the parts that add up to it.', ASK, count(len(breakdown['steps']), 'part')))
    how.append(node('Write the answer', 'In plain words, with the rule behind it.', ASK, '1 answer' if answered else None))
    if s['private']:
        return {'how': how, 'fact': 'Answered in the chat' if answered else ''}
    return {'how': how, 'fact': str(a.get('headline') or '').rpartition(': ')[2],
            'result': [{'title': 'The answer', 'answer': {k: a[k] for k in ('question', 'headline', 'summary', 'rule') if isinstance(a.get(k), str)}}]
            if answered else []}


def dashboard(f, s):
    layout = f.file('layout.json')
    tiles = layout.get('tiles') if isinstance(layout.get('tiles'), list) else []
    return {'how': [node('Propose the tiles', 'A tile for each dashboard metric, the other metrics named under them.', ASK,
                         count(len(tiles), 'tile') if tiles else None),
                    # The step also ends when the person is done without a dashboard: a page is only claimed once tiles
                    # were proposed for it.
                    node('Build the page', 'Every tile runs on the data, so the page and the chat agree.', ASK,
                         always=bool(tiles), done=bool(tiles and f.interaction.get('dashboard')))],
            'fact': count(len(tiles), 'tile') if tiles else ''}


# Each task-list step: its short name on the page, and what it holds.
BUILDERS = {'intent': ('Use case', intent), 'sources': ('Sources', sources), 'scope': ('Select', scope), 'metrics': ('Metrics', metrics),
            'save': ('Save', save), 'ask': ('Ask', ask), 'dashboard': ('Dashboard', dashboard)}
# When the saved run cannot be read, the moments seen say how far it got: the step each one finishes.
FINISHED_BY = {'sources': 'sources', 'build': 'scope', 'context': 'metrics', 'saved': 'save', 'answer': 'ask'}


# The document ---------------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _write(path, text):
    """Replace a file whole, so a reader never sees half of it."""
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


def _state(run):
    state = read_json(Path(run) / 'view' / 'state.json', dict, None)
    if state and isinstance(state.get('moments'), list):
        return state
    return {'moments': []}


@contextmanager
def _locked(run):
    """One update at a time per run, where the system offers a file lock."""
    lock = Path(run) / 'view' / '.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, 'a') as handle:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_UN)


def seen(run):
    return [m.get('kind') for m in _state(run)['moments'] if isinstance(m, dict)]


def _answer_key(spec):
    return '%s | %s' % (spec.get('question') or '', spec.get('headline') or '')


def first_answer(run, spec):
    """Whether this answer is the run's first: the same answer shown again (a widget rerun of a text form) stays first."""
    return not any(isinstance(m, dict) and m.get('kind') == 'answer' and m.get('key') != _answer_key(spec)
                   for m in _state(run)['moments'])


# The moments the run's own files can show without a data file from the agent, so the run names them when due.
MIDWAY = ('connect', 'found', 'sources', 'group', 'tiles', 'described', 'metrics', 'rules', 'links', 'build', 'context')


def _shows(kind, run, engine_dir):
    import visuals
    if kind == 'connect':
        return True
    if kind == 'found':
        # Own data only, right before the person chooses what to build from, so it can name a warehouse just connected.
        if _state(run).get('demo') or RunFacts(run).interaction.get('flow') == 'demo':
            return False
        rows, _ = _rows(run, 'build', seen(run), engine_dir)
        if next((r['id'] for r in rows if r['status'] != 'completed'), None) != 'scope':
            return False
    if kind == 'sources' and not (_state(run).get('demo') or RunFacts(run).interaction.get('flow') == 'demo'):
        # On own data the data read is the tables the person chose: it waits for that choice.
        if RunFacts(run).inputs is None:
            return False
    if kind == 'context':
        # Checks are recorded one by one; the moment waits until they are over.
        if not RunFacts(run, engine_dir).checks_over:
            return False
    try:
        return bool(visuals.render(kind, visuals.build_spec(kind, run, engine_dir=engine_dir)))
    except Exception:  # noqa: BLE001 - an engine file in a shape this cannot read leaves its moment unnamed, never the run stuck
        return False


def due(run, engine_dir=None):
    """The moments the run's files can show but the side view has not recorded, in order."""
    run = Path(run)
    if not (run / 'run.json').is_file():
        return []
    done = set(seen(run))
    return [kind for kind in MIDWAY if kind not in done and _shows(kind, run, engine_dir)]


def _rows(run, flow, kinds, engine_dir):
    """The flow's own task list with each step's saved state, as the host's task list shows it, and the run's state
    (running, paused, blocked, access_needed or done)."""
    import checklist
    try:
        import experience
        listed = checklist.build(experience.snapshot(run, engine_dir), flow)
        return listed['steps'], listed['state']
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        rows = checklist.initial(flow)
        ids = [r['id'] for r in rows]
        last = max((ids.index(FINISHED_BY[k]) for k in kinds if FINISHED_BY.get(k) in ids), default=-1)
        return [dict(r, status='completed' if i <= last else 'in_progress' if i == last + 1 else 'pending') for i, r in enumerate(rows)], 'running'


def model(run, state=None, engine_dir=None, private=None):
    """The whole view as one document: the flow's steps, their status, what came out and how, always from the run's
    current files. On own data the page carries progress only, unless it never leaves this machine (`private` False):
    step and piece names, statuses, counts and the person's own use-case words. Metric names, values, answers and
    workspace names stay in the chat."""
    run = Path(run)
    state = state or _state(run)
    facts = RunFacts(run, engine_dir)
    kinds = [m.get('kind') for m in state['moments'] if isinstance(m, dict)]
    # Only the run's journey record makes a demo.
    flow = 'demo' if state.get('demo') or facts.interaction.get('flow') == 'demo' else 'build'
    private = flow != 'demo' if private is None else private
    # The task list counts the build as started only once the run (or the command) names its engine run; a run that
    # moved keeps finding that engine inside it.
    # A run whose flow never names its engine still has one once the engine has written its manifest in the run.
    named = engine_dir or facts.meta.get('engine_run') or (facts.engine / 'manifest.json').is_file()
    rows, run_state = _rows(run, flow, kinds, facts.engine if named and facts.engine.is_dir() else None)
    c = facts.context
    if c and facts.checks_here and 'context' not in kinds and (c['metrics_checked'] < len(c['metrics']) or c['queries_checked'] < c['queries']):
        # The task list counts the first check pass, which only looks for missing parts, as the checks. The page keeps
        # the step open until every item has run, or the checks moment has been shown.
        # A later step the task list finished means the checks are over; the page never walks one back.
        at = next((i for i, r in enumerate(rows) if r['id'] == 'metrics' and r['status'] == 'completed'), None)
        if at is not None and all(r['status'] != 'completed' for r in rows[at + 1:]):
            rows = [dict(r, status='in_progress', active='Running each metric and query on the data') if i == at
                    else dict(r, status='pending') if i > at else r for i, r in enumerate(rows)]
    if 'answer' in kinds:
        # The person has asked: the journey records it too, but can do so after the answer, in a result no one reads.
        rows = [dict(r, status='completed') if r['id'] == 'ask' else r for r in rows]
    # The step the task list has under way, else the first one still open.
    current = next((i for i, r in enumerate(rows) if r['status'] == 'in_progress'),
                   next((i for i, r in enumerate(rows) if r['status'] != 'completed'), len(rows)))
    steps = []
    for i, row in enumerate(rows):
        name, build = BUILDERS[row['id']]
        # A step can finish ahead of one still open: own data surveys its sources before the use case is saved.
        done, now = row['status'] == 'completed', i == current
        section = build(facts, {'demo': flow == 'demo', 'open': not done, 'private': private})
        nodes = [n for n in section.get('how') or [] if not done or n['n'] or n['always']]
        doing = row['active'] if now else row['content']
        if private and now:
            # A survey names the file it is reading; on own data the page keeps the count, not the path.
            doing = doing.split(':')[0]
        steps.append({'id': row['id'], 'name': name, 'status': 'done' if done else 'now' if now else 'next',
                      'doing': doing, 'purpose': row['content'],
                      **({'needs_you': True} if now and row.get('needs_you') else {}),
                      # The step that is running ticks each piece as it finishes; steps ahead only say how they work.
                      'how': [{k: v for k, v in (('t', n['t']), ('d', n['d']), ('by', n['by']), ('n', n['n'] if done or now else None),
                                                 ('s', 'done' if done or now and n['done'] else 'next'),
                                                 ('w', (done or now) and n['done'] and n['warn'])) if v}
                              for n in nodes],
                      **{k: section[k] for k in ('fact',) if done and section.get(k)},
                      **({'result': section['result']} if done and section.get('result') and not private else {})})
    a = facts.file('answer.json')
    asked = any(s['id'] == 'ask' and s['status'] == 'done' for s in steps)
    return {'title': state.get('title') or 'Analytics setup', 'subject': state.get('subject'), 'demo': flow == 'demo',
            **({'tagline': state.get('tagline') or facts.purpose} if state.get('tagline') or facts.purpose else {}),
            'updated': _now(), 'current': current, 'steps': steps,
            # A run that stopped (paused, blocked, waiting for access) says so instead of looking busy.
            **({'state': run_state} if run_state in ('paused', 'blocked', 'access_needed') else {}),
            **({'answer': {k: a[k] for k in ('question', 'headline', 'summary', 'rule') if isinstance(a.get(k), str)}}
               if asked and a.get('headline') and not private else {})}


# Recording ------------------------------------------------------------------------------------

def note(run, kind, spec=None):
    """Log a moment the chat has shown, so the helper can name the ones still due and tell a first answer from a later
    one. The side page reads the run's own files and needs nothing from here."""
    with _locked(run):
        state = _state(run)
        state['moments'].append({'kind': kind, 'at': _now(), **({'key': _answer_key(spec or {})} if kind == 'answer' else {})})
        _write(Path(run) / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
