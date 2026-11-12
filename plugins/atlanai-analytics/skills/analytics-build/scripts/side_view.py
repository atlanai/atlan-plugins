"""The run's side view: the flow's own steps, what came out of each and how, read from the run's own files.

The steps and their status are the host's task list (`checklist`). `record` logs a moment and `progress`
follows the run between moments; whenever the page would show something new, either writes one
self-contained document, `view/steps/sNN.json`, for the claude.ai artifact to show, and refreshes
`view/progress.html`, the same page as a local file for hosts without artifacts. Every number comes from a
file the run wrote, and every step shows the run's current truth. A step's "how we got there" lists only the
work this run will do and names the part of the plugin that does it. On own data the page carries progress
only; names and values stay in the chat.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

try:
    import fcntl
except ImportError:
    fcntl = None

from run_facts import BI_TOOLS, RunFacts, count, read_json, table_name
import visual_kit

PAGE = Path(__file__).with_name('side_view.html')
FONT = re.compile(r'@import url\([^)]*\);\n?')

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
    held = (c['failed'] if s['open'] else c['need_look']) if checked else 0
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
    saved = len(f.file('context', 'published.json'))

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
    # A state file that is missing or unreadable starts over, but never reuses a document number the page has seen.
    written = [int(m.group(1)) for d in (Path(run) / 'view' / 'steps').glob('s*.json') if (m := re.fullmatch(r's(\d+)\.json', d.name))]
    return {'moments': [], 'written': max(written, default=0)}


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
MIDWAY = ('connect', 'found', 'sources', 'build', 'context')


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
    if kind == 'context':
        # Checks are recorded one by one; the moment waits for the last.
        c = RunFacts(run, engine_dir).context
        if not c or c['metrics_checked'] < len(c['metrics']) or c['queries_checked'] < c['queries']:
            return False
    return bool(visuals.render(kind, visuals.build_spec(kind, run, engine_dir=engine_dir)))


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


def model(run, state=None, engine_dir=None):
    """The whole view as one document: the flow's steps, their status, what came out and how, always from the run's
    current files. On own data the page carries progress only: step and piece names, statuses, counts and the person's
    own use-case words. Metric names, values, answers and workspace names stay in the chat."""
    run = Path(run)
    state = state or _state(run)
    facts = RunFacts(run, engine_dir)
    kinds = [m.get('kind') for m in state['moments'] if isinstance(m, dict)]
    flow = 'demo' if state.get('demo') else 'build'
    private = flow != 'demo'
    # The task list counts the build as started only once the run (or the command) names its engine run; a run that
    # moved keeps finding that engine inside it.
    named = engine_dir or facts.meta.get('engine_run')
    rows, run_state = _rows(run, flow, kinds, facts.engine if named and facts.engine.is_dir() else None)
    c = facts.context
    if c and 'context' not in kinds and (c['metrics_checked'] < len(c['metrics']) or c['queries_checked'] < c['queries']):
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
    current = next((i for i, r in enumerate(rows) if r['status'] != 'completed'), len(rows))
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

def link(run, url):
    """Remember where the side view was published, so every later update names it. Returns the newest document to
    push, so publishing and the first push are one chain the agent cannot half-finish."""
    if not re.match(r'https://claude\.ai/', str(url)):
        raise ValueError('A side view is a claude.ai artifact address.')
    with _locked(run):
        state = _state(run)
        state['url'] = url
        _write(Path(run) / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
    newest = Path(run) / 'view' / 'steps' / ('s%02d.json' % int(state.get('written') or 0))
    return {'doc': str(newest), 'url': url} if newest.is_file() else {}


def _offer(run, state, out, moment):
    """Name the published page; until there is one, every moment offers it, so a moment that skipped it is caught at
    the next. Updates between moments only push, so a host with no artifact tools hears the offer a few times a run."""
    if state.get('url'):
        out['url'] = state['url']
    elif moment:
        side = run / 'view' / 'side.html'
        out['page'] = str(side if side.is_file() else page(run, state.get('title') or 'Analytics setup'))


def record(run, kind, spec=None, engine_dir=None):
    """Log a moment; when the page would show something new, write a new document and refresh the local page.

    Returns the paths to publish or push: `doc` on every write, then `url` once the page is published, or `page`
    while it is not."""
    with _locked(run):
        return _record(Path(run), kind, spec or {}, engine_dir)


def _record(run, kind, spec, engine_dir):
    state = _state(run)
    first = not state['moments']
    facts = RunFacts(run)
    # A demo's facts name its firm and title; own data takes its title from the person's use case once they give it.
    for key, value in (('subject', spec.get('firm')), ('tagline', spec.get('tagline'))):
        if value:
            state[key] = value
    if spec.get('title'):
        state['title'], state['titled'] = spec['title'], True
    elif not state.get('titled'):
        # A folder's name can be a stray file's; until the person names the use case, the page is the setup.
        state['title'] = facts.use_case or 'Analytics setup'
    if facts.interaction.get('flow') == 'demo':
        state['demo'] = True
    state['moments'].append({'kind': kind, 'at': _now(), **({'key': _answer_key(spec)} if kind == 'answer' else {})})
    out = {}
    # A moment writes a document only when the page would show something new.
    if first or _content(model(run, state, engine_dir)) != state.get('content'):
        doc, path = _emit(run, state, engine_dir)
        if first or not (run / 'view' / 'side.html').is_file():
            page(run, doc['title'])
        out['doc'] = str(path)
    # Every moment offers the page until there is one, with or without a new document.
    _offer(run, state, out, True)
    # Once it exists, every moment names the newest document too: a push line on a result between moments can be cut
    # off with the rest of a long result, and pushing a whole document again changes nothing.
    newest = run / 'view' / 'steps' / ('s%02d.json' % int(state.get('written') or 0))
    if out.get('url') and not out.get('doc') and newest.is_file():
        out['doc'] = str(newest)
    _write(run / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
    return out


def _ticks(doc):
    """What makes a new document worth pushing between moments: a step's status, a piece of work's tick, or a piece
    that now needs a look. Counts that move without any of them ride along in the next one."""
    return [[s['status'], [[n.get('s'), bool(n.get('w'))] for n in s['how']]] for s in doc['steps']]


def _content(doc):
    """Everything the page shows, for a moment to write only what is new: an edit can change a value and no tick."""
    shown = json.dumps({k: v for k, v in doc.items() if k not in ('updated', 'n')}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(shown.encode('utf-8')).hexdigest()


def _emit(run, state, engine_dir=None):
    """The view as a new numbered document for the page, and the same data for the local copy."""
    state['written'] = int(state.get('written') or 0) + 1
    doc = dict(model(run, state, engine_dir), n=state['written'])
    state['ticks'], state['content'] = _ticks(doc), _content(doc)
    path = run / 'view' / 'steps' / ('s%02d.json' % state['written'])
    _write(path, json.dumps(doc, ensure_ascii=False, indent=1))
    _write(run / 'view' / 'progress-data.js', 'window.SIDE_VIEW = %s;\n' % json.dumps(doc, ensure_ascii=False))
    return doc, path


def progress(run, engine_dir=None):
    """A new document when a piece of work has finished or moved on since the last one, so the page ticks it as it
    happens. Returns what to push, as `record` does; nothing when nothing changed or the view has not opened yet."""
    run = Path(run)
    if not (run / 'view' / 'state.json').is_file():
        return {}
    with _locked(run):
        state = _state(run)
        if not state['moments']:
            return {}
        if _ticks(model(run, state, engine_dir)) == state.get('ticks'):
            return {}
        _, path = _emit(run, state, engine_dir)
        _write(run / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
        out = {'doc': str(path)}
        _offer(run, state, out, False)
        return out


def push(update):
    """The one line a helper result carries, so the agent opens or updates the side view without looking anything up."""
    if update.get('url') and update.get('doc'):
        return ('Push it to the side view: `ArtifactData` (load it with ToolSearch first if it is deferred) set, url `%s`, '
                'collection `steps`, doc_id `%s`, file_path `%s`.'
                % (update['url'], Path(update['doc']).stem, update['doc']))
    if update.get('page'):
        return ('Open the side view now: publish `%s` with the `Artifact` tool (read the file in full first), with '
                '`capabilities` `{"db": {}}`, `icon` `chart` and a one-sentence `description` that names no metric, table '
                'or value, then run `PROJECT view --url "<its address>"`: it records the page and names the update to push. '
                'If you already published this page, run only that command, with its address.' % update['page'])
    return None


def page(run, title):
    """The artifact page, and the same page as a local file that reloads its data by itself and loads nothing."""
    run = Path(run)
    tokens = (visual_kit.themes(sorted(visual_kit.TOKENS), ':root')
              + ':root[data-theme=dark]{color-scheme:dark}:root[data-theme=light]{color-scheme:light}')
    words = re.sub(r'\s+', ' ', str(title)).strip()
    body = (PAGE.read_text(encoding='utf-8').replace('<title>Analytics progress</title>', '<title>%s</title>' % escape(
        words if len(words) <= 60 else words[:57].rstrip() + '...')).replace('/*TOKENS*/', tokens))
    _write(run / 'view' / 'side.html', body)
    _write(run / 'view' / 'progress.html', '<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<script>window.SIDE_VIEW_FILE = "progress-data.js";</script></head><body>\n%s\n</body></html>\n' % FONT.sub('', body))
    return run / 'view' / 'side.html'
