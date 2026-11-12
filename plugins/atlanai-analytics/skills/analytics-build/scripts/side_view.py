"""The run's side view: the flow's own steps, what came out of each and how, read from the run's own files.

The steps and their status are the host's task list (`checklist`). `record` logs a moment and `progress`
follows the run between moments; whenever the page would show something new, either writes one
self-contained document, `view/steps/sNN.json`, for the claude.ai artifact to show, and refreshes
`view/progress.html`, the same page as a local file for hosts without artifacts. Every number comes from a
file the run wrote. A step's "how we got there" lists only the work this run did and names the part of the
plugin that did it; once a step is done, what it showed is kept, so a later change does not rewrite what an
earlier step found.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
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


def node(title, text, by, n=None, always=False, done=None):
    """One piece of work. It ticks as soon as the run has finished it: by default, once the run left its count.
    A finished step shows it only when the run left its count, or when it has none to leave."""
    return {'t': title, 'd': text, 'by': by, 'n': n, 'always': always, 'done': n is not None if done is None else done}


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


def _found(f):
    return [k for k in ((f.dashboard.get('tool') and '%s dashboard' % f.dashboard['tool'] if f.usable(*BI_TOOLS) else ''),
                        count(len(f.usable('data_file')), 'data file') if f.usable('data_file') else '',
                        'query history' if f.usable('query_history') else '') if k]


def _tables_node(f):
    tables = f.tables
    return node('Load the tables on this machine', 'Into a small read-only database, so every number can be tested '
                'without touching anything real.', SETUP,
                '%s · %s rows' % (count(len(tables), 'table'), '{:,}'.format(sum(t[1] for t in tables))) if tables else None)


def sources(f, s):
    history, dash = f.history, f.dashboard
    # A demo's run starts from its copied files, so both pieces are done once there is a run to show.
    how = ([node('You pick a demo', 'A made-up firm with its own dashboard, past queries and data.', YOU, always=True, done=True),
            node('Copy its files into a working folder', 'Nothing leaves this machine until you choose to save.', DEMO, always=True,
                 done=True)]
           if s['demo'] else [])
    how.append(node('Find every source', 'Each file is listed and fingerprinted, so a later change is noticed.', SETUP,
                    count(len(f.sources), 'source') if f.sources else None))
    if dash or f.usable(*BI_TOOLS):
        how.append(node('Read the dashboard', 'Its tiles and the formula behind each one.', SURVEY,
                        count(dash['formulas'], 'formula') if dash.get('formulas') else None))
    if history or f.usable('query_history'):
        how.append(node('Count the past queries', 'What analysts actually ran is the best evidence of what they measure.', SETUP,
                        '%s · run %s times' % ('{:,}'.format(history['statements']), '{:,}'.format(history['runs'])) if history else None))
    if s['demo']:
        # A demo loads its data in this step; on your own data the tables load when the build starts.
        how.append(_tables_node(f))
    found, tables = _found(f), f.tables
    return {'how': how, 'fact': ', '.join(found),
            'result': ([{'title': 'Found', 'list': [[k, ''] for k in found]}] if found else [])
            + ([{'title': 'Tables loaded', 'list': [[table_name(t[0]), count(t[1], 'row')] for t in tables]}] if s['demo'] and tables else [])}


def scope(f, s):
    config = f.file('engine-config.json')
    inputs = config.get('inputs') if isinstance(config.get('inputs'), dict) else {}
    chosen = ('Dashboard and past queries' if inputs.get('history') and inputs.get('bi') else 'Past queries' if inputs.get('history')
              else 'Dashboard only' if inputs.get('bi') else None) if config else None
    return {'how': [node('You choose what to build from', 'More sources give fuller context; the dashboard alone is faster.'
                         if s['demo'] else 'The tables and dashboards the metrics come from.', YOU, chosen, always=True,
                         done=bool(config))],
            'fact': chosen or ''}


def metrics(f, s):
    st = f.stage
    how = [] if s['demo'] else [_tables_node(f)]
    if f.stages.get('h0_corpus') is not None or s['open'] and f.usable('query_history'):
        raw, scanned, kept, shapes = f.query_log, st('h0_corpus', 'scanned'), st('h0_corpus', 'kept'), st('h1_shapes', 'canonical_shapes')
        if raw and scanned is not None and raw > scanned:
            how.append(node('Set aside queries with names in them', 'A privacy filter holds back any query that looks like it '
                            'names a person or a business.', SETUP, ratio(raw, scanned, '%d → %d')))
        how += [node("Keep queries about the dashboard's tables" if f.dashboard else 'Keep queries about the chosen tables',
                     "Queries about other tables can't explain these numbers.", ENGINE, ratio(scanned, kept, '%d → %d')),
                node('Group repeats into questions', 'Queries that differ only in a value, such as a date, count as one; '
                     'plain table dumps are left out.', ENGINE, ratio(kept, shapes, '%d → %d'))]
    if f.stages.get('a4_witness_bi') is not None or s['open'] and f.usable(*BI_TOOLS):
        tried = st('a4_witness_bi', 'witnessed')
        how += [node("Translate the dashboard's formulas", 'Each tile formula becomes a query that runs on the data.', ENGINE,
                     ratio(st('a4_witness_bi', 'verdict_pass') or 0, tried) if tried is not None else None,
                     done='a3_translate' in f.stages or tried is not None),
                node('Review each translation', 'A second read of every translated formula, before it is used.', ENGINE,
                     count(st('a3b_emit', 'prompted'), 'formula') if st('a3b_emit', 'prompted') and 'a3b_ingest' in f.stages else None)]
    described, none, proposals, made = st('m2_ingest', 'described'), st('x1_ingest', 'no_metric'), st('x1_ingest', 'proposals'), st('x2_ingest', 'metrics_out')
    measuring = described - none if isinstance(described, int) and isinstance(none, int) else None
    how += [
        node('Describe every question in plain words', 'The dashboard tiles and the questions from past queries, each said '
             'so a person, or another agent, can tell what it answers.', ENGINE, count(described, 'question') if described else None),
        node('Keep the ones that measure a number', 'Lists and row counts stay as saved queries, not metrics.', ENGINE,
             ratio(measuring, described)),
        node('Merge numbers that share a rule', ('Those questions hold %s; numbers worked out the same way become one metric.'
                                                 % count(proposals, 'number')) if proposals else
             'Numbers worked out the same way become one metric.', ENGINE, ratio(proposals, made, '%d → %d')),
        node("Write each metric's full rule", 'What it counts, what it leaves out and how it is worked out.', ENGINE,
             count(made, 'rule') if made and 'x3_ingest' in f.stages else None),
        node('Link related metrics', 'Such as a margin that is built from two costs.', ENGINE,
             count(st('x4_ingest', 'edges'), 'link') if st('x4_ingest', 'edges') is not None else None)]
    c = f.context
    checked = bool(c)
    n_metrics, n_queries = (len(c['metrics']), c['queries']) if checked else (None, None)
    how += [node('Check each item is complete', 'Every metric and query needs all its parts before it runs.', SETUP,
                 '%s + %s' % (count(n_metrics, 'metric'), count(n_queries, 'query', 'queries')) if checked else None),
            node('Run each metric on the data', 'Its examples, its full calculation, and a test that its filter leaves '
                 'nothing in when nothing should count.', SETUP, ratio(c['metrics_ran'], n_metrics) if checked and n_metrics else None,
                 done=checked and c['metrics_checked'] == n_metrics),
            node('Run each query', 'Each one runs as written on the loaded tables.', SETUP,
                 ratio(c['queries_ran'], n_queries) if checked and n_queries else None, done=checked and c['queries_checked'] == n_queries)]
    if not checked or c['review']:
        how.append(node('Flag what needs a person', 'Queries the engine could not fully confirm, such as a filter value it '
                        'could not match or a query that came back empty, wait for a person to review.', ENGINE,
                        count(c['review'], 'query', 'queries') if checked else None, done=checked and c['queries_checked'] == n_queries))

    def came_from(m):
        return ', '.join(x for x in (count(m['tiles'], 'dashboard tile') if m['tiles'] else '',
                                     count(m['queries'], 'past query', 'past queries') if m['queries'] else '') if x)
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
           node('You pick a workspace', 'Where the metrics live in Atlan.', YOU, p.get('workspace'), done=bool(picked or p)),
           node('Plan the save', 'What is new, what changed, and what each item links to.', SETUP,
                '%s · %s' % (count(made('metrics'), 'metric'), count(made('queries') or 0, 'query', 'queries')) if made('metrics') is not None else None),
           node('Save each metric and query', 'Each one becomes an item in the workspace.', ATLAN,
                count(p['items'], 'item') if p else '%d saved so far' % saved if saved else None, done=bool(p)),
           node('Link each metric to its sources', 'Queries to the metrics they feed, and metrics to the ones they build on.', ATLAN,
                count(p['links'], 'link') if p and p.get('links') else None),
           node('Read every item back', 'Proof that each one is live, which is what lets agents find it.', ATLAN,
                '%d live' % p['items'] if p else None)]
    return {'how': how, 'fact': '%d live in Atlan' % p['items'] if p else '',
            'result': [{'title': 'Saved to Atlan Trident', 'kv': [['Workspace', p.get('workspace') or 'Atlan'], ['Metrics', str(len(p['metrics']))],
                                                                 ['Queries', str(p['queries'])]]}] if p else []}


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
    if breakdown.get('steps'):
        how.append(node('Break the answer down', 'Into the parts that add up to it.', ASK, count(len(breakdown['steps']), 'part')))
    how.append(node('Write the answer', 'In plain words, with the rule behind it.', ASK, '1 answer' if answered else None))
    return {'how': how, 'fact': str(a.get('headline') or '').rpartition(': ')[2],
            'result': [{'title': 'The answer', 'answer': {k: a[k] for k in ('question', 'headline', 'summary', 'rule') if isinstance(a.get(k), str)}}]
            if answered else []}


def dashboard(f, s):
    layout = f.file('layout.json')
    tiles = layout.get('tiles') if isinstance(layout.get('tiles'), list) else []
    return {'how': [node('Propose the tiles', 'A tile for each dashboard metric, the other metrics named under them.', ASK,
                         count(len(tiles), 'tile') if tiles else None),
                    node('Build the page', 'Every tile runs on the data, so the page and the chat agree.', ASK,
                         always=True, done=bool(f.interaction.get('dashboard')))],
            'fact': count(len(tiles), 'tile') if tiles else ''}


# Each task-list step: its short name on the page, and what it holds.
BUILDERS = {'intent': ('Use case', intent), 'sources': ('Sources', sources), 'scope': ('Select', scope), 'metrics': ('Metrics', metrics),
            'save': ('Save', save), 'ask': ('Ask', ask), 'dashboard': ('Dashboard', dashboard)}
# Steps a person repeats keep showing their latest; every other step keeps what it showed when it finished.
REPEATS = ('ask', 'dashboard')
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
    return {'moments': [], 'written': max(written, default=0), 'frozen': {}}


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


# The moments the run's own files can show without a data file from the agent, so the run names them when due.
MIDWAY = ('connect', 'sources', 'build', 'context')


def _shows(kind, run, engine_dir):
    import visuals
    if kind == 'connect':
        return True
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
    """The flow's own task list with each step's saved state, as the host's task list shows it."""
    import checklist
    try:
        import experience
        return checklist.build(experience.snapshot(run, engine_dir), flow)['steps']
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        rows = checklist.initial(flow)
        ids = [r['id'] for r in rows]
        last = max((ids.index(FINISHED_BY[k]) for k in kinds if FINISHED_BY.get(k) in ids), default=-1)
        return [dict(r, status='completed' if i <= last else 'in_progress' if i == last + 1 else 'pending') for i, r in enumerate(rows)]


def model(run, state=None, engine_dir=None):
    """The whole view as one document: the flow's steps, their status, what came out and how."""
    run = Path(run)
    state = state or _state(run)
    engine_dir = engine_dir or state.get('engine_dir')
    facts = RunFacts(run, engine_dir)
    kinds = [m.get('kind') for m in state['moments'] if isinstance(m, dict)]
    flow = 'demo' if state.get('demo') else 'build'
    rows = _rows(run, flow, kinds, engine_dir)
    current = next((i for i, r in enumerate(rows) if r['status'] != 'completed'), len(rows))
    frozen = state.setdefault('frozen', {})
    steps = []
    for i, row in enumerate(rows):
        name, build = BUILDERS[row['id']]
        done, now = i < current, i == current
        keep = done and row['id'] not in REPEATS
        section = frozen.get(row['id']) if keep and row['id'] in frozen else build(facts, {'demo': flow == 'demo', 'open': not done})
        if keep:
            frozen.setdefault(row['id'], section)
        nodes = [n for n in section.get('how') or [] if not done or n['n'] or n['always']]
        steps.append({'id': row['id'], 'name': name, 'status': 'done' if done else 'now' if now else 'next',
                      'doing': row['active'] if now else row['content'], 'purpose': row['content'],
                      **({'needs_you': True} if now and row.get('needs_you') else {}),
                      # The step that is running ticks each piece as it finishes; steps ahead only say how they work.
                      'how': [{k: v for k, v in (('t', n['t']), ('d', n['d']), ('by', n['by']), ('n', n['n'] if done or now else None),
                                                 ('s', 'done' if done or now and n['done'] else 'next')) if v}
                              for n in nodes],
                      **{k: section[k] for k in ('fact', 'result') if done and section.get(k)}})
    a = facts.file('answer.json')
    asked = any(s['id'] == 'ask' and s['status'] == 'done' for s in steps)
    return {'title': state.get('title') or 'Analytics setup', 'subject': state.get('subject'), 'demo': flow == 'demo',
            **({'tagline': state.get('tagline') or facts.purpose} if state.get('tagline') or facts.purpose else {}),
            'updated': _now(), 'current': current, 'steps': steps,
            **({'answer': {k: a[k] for k in ('question', 'headline', 'summary', 'rule') if isinstance(a.get(k), str)}}
               if asked and a.get('headline') else {})}


# Recording ------------------------------------------------------------------------------------

def link(run, url):
    """Remember where the side view was published, so every later update names it."""
    if not re.match(r'https://claude\.ai/', str(url)):
        raise ValueError('A side view is a claude.ai artifact address.')
    with _locked(run):
        state = _state(run)
        state['url'] = url
        _write(Path(run) / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))


def record(run, kind, spec=None, engine_dir=None):
    """Log a moment; when the page would show something new, write a new document and refresh the local page.

    Returns the paths to publish or push: `page` and `local` on the first write, `doc` on every write,
    and `url` once the page is published."""
    with _locked(run):
        return _record(Path(run), kind, spec or {}, engine_dir)


def _record(run, kind, spec, engine_dir):
    state = _state(run)
    first = not state['moments']
    facts = RunFacts(run)
    if engine_dir:
        state['engine_dir'] = str(engine_dir)
    if spec.get('firm') and not state.get('subject'):
        state['subject'] = spec['firm']
    if spec.get('tagline') and not state.get('tagline'):
        state['tagline'] = spec['tagline']
    if not state.get('title'):
        root = facts.meta.get('source_root')
        state['title'] = spec.get('title') or ('%s analytics' % Path(root).name if root else 'Analytics setup')
    if spec.get('flow') == 'demo' or facts.interaction.get('flow') == 'demo':
        state['demo'] = True
    state['moments'].append({'kind': kind, 'at': _now()})
    out = {}
    # A moment writes a document only when the page would show something new.
    if first or _ticks(model(run, state)) != state.get('ticks'):
        doc, path = _emit(run, state)
        if first or not (run / 'view' / 'side.html').is_file():
            out['page'] = str(page(run, doc['title']))
            out['local'] = str(run / 'view' / 'progress.html')
        out['doc'] = str(path)
        if state.get('url'):
            out['url'] = state['url']
    _write(run / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
    return out


def _ticks(doc):
    """What makes a new document worth pushing: a step's status or a piece of work's tick. Counts that move
    without either ride along in the next one."""
    return [[s['status'], [n.get('s') for n in s['how']]] for s in doc['steps']]


def _emit(run, state):
    """The view as a new numbered document for the page, and the same data for the local copy."""
    state['written'] = int(state.get('written') or 0) + 1
    doc = dict(model(run, state), n=state['written'])
    state['ticks'] = _ticks(doc)
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
        if engine_dir and not state.get('engine_dir'):
            state['engine_dir'] = str(engine_dir)
        if _ticks(model(run, state)) == state.get('ticks'):
            return {}
        _, path = _emit(run, state)
        _write(run / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
        return {'doc': str(path), **({'url': state['url']} if state.get('url') else {})}


def push(update):
    """The one line a helper result carries, so the agent pushes a newer document without looking anything up."""
    if not update.get('url') or not update.get('doc'):
        return None
    return ('Push it to the side view: `ArtifactData` set, url `%s`, collection `steps`, doc_id `%s`, file_path `%s`.'
            % (update['url'], Path(update['doc']).stem, update['doc']))


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
