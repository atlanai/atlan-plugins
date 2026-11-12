"""The run's side view: every step, what came out of it and how, read from the run's own files.

`record` logs a moment. When the moment finishes a step it writes one self-contained document,
`view/steps/sNN.json`, for the claude.ai artifact to show, and refreshes `view/progress.html`, the same
page as a local file for hosts without artifacts. Every number comes from a file the run wrote. A step's
"how we got there" lists only the work this run did and names the part of the plugin that did it; once a
step is done, what it showed is kept, so a later change does not rewrite what an earlier step found.
"""
from collections import namedtuple
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

from run_facts import BI_TOOLS, RunFacts, count, read_json, table_name, their
import visual_kit

PAGE = Path(__file__).with_name('side_view.html')
FONT = re.compile(r'@import url\([^)]*\);\n?')

# Who did a piece of work, in the words the person reads. `ai` marks the judgements Claude makes; every
# other part follows fixed rules. The page's key explains each name.
YOU, DEMO, SETUP, SURVEY, ENGINE, CLAUDE, ATLAN, ASK = (
    'You', 'Demo guide', 'Setup helper', 'Source survey', 'Metrics engine', 'Claude', 'Atlan', 'Ask guide')
PARTS = {CLAUDE: 'judgement calls, such as which numbers share a rule', ENGINE: 'fixed rules that turn queries into metrics',
         SETUP: 'runs each step on this machine', SURVEY: 'reads dashboard files', DEMO: 'sets up the made-up firm',
         ASK: 'answers from the saved rules', ATLAN: 'stores what you save, where agents find it'}


def node(title, text, by, n=None, ai=False, always=False, done=None):
    """One piece of work. It ticks as soon as the run has finished it: by default, once the run left its count.
    A finished step shows it only when the run left its count, or when it has none to leave."""
    return {'t': title, 'd': text, 'by': by, 'n': n, 'ai': ai, 'always': always, 'done': n is not None if done is None else done}


def ratio(a, b, form='%d of %d'):
    return form % (a, b) if isinstance(a, int) and isinstance(b, int) else None


# Each step: what it shows while it runs, and what came out of it --------------------------------

def connect(f, s):
    files = len(f.sources)
    how = ([node('You pick a demo', 'A made-up firm with its own dashboard, past queries and data.', YOU, always=True),
            node('Copy its files into a working folder', 'Nothing leaves this machine until you choose to save.', DEMO, always=True)]
           if s['demo'] else
           [node('You point at your sources', 'A folder, a dashboard export or query history.', YOU, always=True)])
    how += [node('Set up the metrics engine', 'A private copy on this machine; later runs reuse it.', SETUP, always=True),
            node('Open this view', 'Each step lands here as it finishes; the chat stays short.', SETUP, always=True)]
    kinds = [(f.dashboard.get('tool') and '%s dashboard' % f.dashboard['tool'] if f.usable(*BI_TOOLS) else ''),
             count(len(f.usable('data_file')), 'data file') if f.usable('data_file') else '',
             'query history' if f.usable('query_history') else '']
    found = [k for k in kinds if k]
    return {'how': how, 'fact': ', '.join(found), 'result': [{'title': 'Found', 'list': [[k, ''] for k in found]}] if found else []}


def read(f, s):
    history, tables, dash = f.history, f.tables, f.dashboard
    rows = sum(t[1] for t in tables)
    how = [node('Find every source', 'Each file is listed and fingerprinted, so a later change is noticed.', SETUP,
                count(len(f.sources), 'source') if f.sources else None)]
    if dash or f.usable(*BI_TOOLS):
        how.append(node('Read the dashboard', 'Its tiles and the formula behind each one.', SURVEY,
                        count(dash['formulas'], 'formula') if dash.get('formulas') else None))
    how.append(node('Load the tables on this machine', 'Into a small read-only database, so every number can be tested '
                    'without touching anything real.', SETUP, '%s · %s rows' % (count(len(tables), 'table'), '{:,}'.format(rows)) if tables else None))
    if history or f.usable('query_history'):
        how.append(node('Count the past queries', 'What analysts actually ran is the best evidence of what they measure.', SETUP,
                        '%s · run %s times' % ('{:,}'.format(history['statements']), '{:,}'.format(history['runs'])) if history else None))
    fact = ', '.join(x for x in (count(history['statements'], 'query', 'queries') if history else '',
                                 count(len(tables), 'table') if tables else '') if x)
    return {'how': how, 'fact': fact,
            'result': [{'title': 'Tables loaded', 'list': [[table_name(t[0]), count(t[1], 'row')] for t in tables]}] if tables else []}


def build(f, s):
    st = f.stage
    how = []
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
                node('Review each translation', 'A second read of every translated formula, before it is used.', CLAUDE,
                     count(st('a3b_emit', 'prompted'), 'formula') if st('a3b_emit', 'prompted') and 'a3b_ingest' in f.stages else None, ai=True)]
    described, none, proposals, metrics = st('m2_ingest', 'described'), st('x1_ingest', 'no_metric'), st('x1_ingest', 'proposals'), st('x2_ingest', 'metrics_out')
    measuring = described - none if isinstance(described, int) and isinstance(none, int) else None
    how += [
        node('Describe every question in plain words', 'The dashboard tiles and the questions from past queries, each said '
             'so a person, or another agent, can tell what it answers.', CLAUDE, count(described, 'question') if described else None, ai=True),
        node('Keep the ones that measure a number', 'Lists and row counts stay as saved queries, not metrics.', CLAUDE,
             ratio(measuring, described), ai=True),
        node('Merge numbers that share a rule', ('Those questions hold %s; numbers worked out the same way become one metric.'
                                                 % count(proposals, 'number')) if proposals else
             'Numbers worked out the same way become one metric.', CLAUDE, ratio(proposals, metrics, '%d → %d'), ai=True),
        node("Write each metric's full rule", 'What it counts, what it leaves out and how it is worked out.', CLAUDE,
             count(metrics, 'rule') if metrics and 'x3_ingest' in f.stages else None, ai=True),
        node('Link related metrics', 'Such as a margin that is built from two costs.', CLAUDE,
             count(st('x4_ingest', 'edges'), 'link') if st('x4_ingest', 'edges') is not None else None, ai=True)]
    made = f.merged

    def source(m):
        return ', '.join(x for x in ('the dashboard' if m['tiles'] == 1 else count(m['tiles'], 'dashboard question') if m['tiles'] else '',
                                     count(m['queries'], 'past query', 'past queries') if m['queries'] else '') if x)
    return {'how': how, 'fact': count(len(made), 'metric') if made else '',
            'result': [{'title': 'Metrics, and what each came from', 'list': [[m['name'], source(m)] for m in made]}] if made else []}


def check(f, s):
    c = f.context
    done = bool(c)
    metrics, queries = (len(c['metrics']), c['queries']) if done else (None, None)
    how = [node('Check each item is complete', 'Every metric and query needs all its parts before it runs.', SETUP,
                '%s + %s' % (count(metrics, 'metric'), count(queries, 'query', 'queries')) if done else None),
           node('Run each metric on the data', 'Its examples, its full calculation, and a test that its filter leaves '
                'nothing in when nothing should count.', SETUP, ratio(c['metrics_ran'], metrics) if done and metrics else None,
                done=done and c['metrics_checked'] == metrics),
           node('Run each query', 'Each one runs as written on the loaded tables.', SETUP,
                ratio(c['queries_ran'], queries) if done and queries else None, done=done and c['queries_checked'] == queries)]
    if not done or c['review']:
        how.append(node('Flag what needs a person', 'Queries the engine could not fully confirm, such as a filter value it '
                        'could not match or a query that came back empty, wait for a person to review.', ENGINE,
                        count(c['review'], 'query', 'queries') if done else None, done=done and c['queries_checked'] == queries))
    if not done:
        return {'how': how}
    ran, total = c['metrics_ran'] + c['queries_ran'], metrics + queries
    unmeasured = sum(1 for m in c['metrics'] if m['unmeasured'])
    fact = ('%d ran' % ran if ran == total else '%d of %d ran' % (ran, total)) + (', %d to confirm' % c['review'] if c['review'] else '')
    return {'how': how, 'fact': fact,
            'result': [{'title': 'Each metric, run on the data', 'list': [[m['name'], m['value']] + ([] if m['ok'] else ['warn']) for m in c['metrics']]}],
            'note': ('Running cleanly is not the same as matching the dashboard: %s not been compared with %s yet.'
                     % ('the tile number has' if unmeasured == 1 else 'the %d tile numbers have' % unmeasured, f.dashboard.get('tool') or 'it'))
            if unmeasured else None}


def publish(f, s):
    if f.local:
        return {'how': [node('You keep the context on this machine', 'Nothing is saved to Atlan; answers use the local copy.', YOU, always=True)],
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
    how = [node('You pick a workspace', 'Where the metrics live in Atlan, for your team and its agents.', YOU, p.get('workspace'),
                done=bool(picked or p)),
           node('Plan the save', 'What is new, what changed, and what each item links to.', SETUP,
                '%s · %s' % (count(made('metrics'), 'metric'), count(made('queries') or 0, 'query', 'queries')) if made('metrics') is not None else None),
           node('Save each metric and query', 'Each one becomes an item in the workspace.', ATLAN,
                count(p['items'], 'item') if p else '%d saved so far' % saved if saved else None, done=bool(p)),
           node('Link each metric to its sources', 'Queries to the metrics they feed, and metrics to the ones they build on.', ATLAN,
                count(p['links'], 'link') if p and p.get('links') else None),
           node('Read every item back', 'Proof that each one is live, which is what lets agents find it.', ATLAN,
                '%d live' % p['items'] if p else None)]
    if not p:
        # A later step can finish without the save's read-back; the view then says so rather than showing it done.
        return {'how': how, 'fact': 'Not confirmed live',
                'note': 'Nothing was read back from Atlan as live, so this run cannot show the save as done.'}
    return {'how': how, 'fact': '%d live in Atlan' % p['items'],
            'result': [{'title': 'Saved to Atlan Trident', 'kv': [['Workspace', p.get('workspace') or 'Atlan'], ['Metrics', str(len(p['metrics']))],
                                                                 ['Queries', str(p['queries'])]]}]}


def answer(f, s):
    a = f.file('answer.json') if 'answer' in s['seen'] else {}
    retrieved = f.file('retrieved-result.json').get('artifacts') or []
    breakdown = a.get('breakdown') if isinstance(a.get('breakdown'), dict) else {}
    how = [node('Fetch the saved rules', 'Answers use the rules you saved, not a fresh guess at the tables.', ASK,
                count(len(retrieved), 'item') if a and retrieved else None),
           node('Run the query', "On the loaded data, with the metric's rule.", ASK,
                count(len(a['rows']), 'row') if isinstance(a.get('rows'), list) and a['rows'] else None)]
    if breakdown or not a:
        how.append(node('Break the answer down', 'Into the parts that add up to it.', ASK,
                        count(len(breakdown.get('steps') or []), 'part') if breakdown.get('steps') else None))
    how.append(node('Write the answer', 'In plain words, with the rule behind it.', CLAUDE, '1 answer' if a.get('headline') else None, ai=True))
    return {'how': how, 'fact': str(a.get('headline') or '').rpartition(': ')[2],
            'result': [{'title': 'The answer', 'answer': {k: a[k] for k in ('question', 'headline', 'summary', 'rule') if isinstance(a.get(k), str)}}]
            if a.get('headline') else []}


Step = namedtuple('Step', 'name moments doing purpose build')
STEPS = (
    Step('Connect', ('connect',), 'Getting {their} sources ready', 'Open the folder and set up the engine', connect),
    Step('Read', ('sources',), 'Reading {their} dashboard and past queries', 'Read the dashboard, the past queries and the data', read),
    Step('Build', ('build',), 'Turning {their} questions into metrics', 'Turn questions into metrics, each with its rule', build),
    Step('Check', ('context',), 'Testing each metric on {their} data', 'Run every metric and query on the data', check),
    Step('Publish', ('saved',), 'Saving the metrics to Atlan', 'Save to Atlan, or keep it here', publish),
    Step('Answer', ('answer', 'layout', 'edit'), 'Ready for a question in the chat', 'Ask a question', answer),
)
STEP_OF = {kind: i for i, step in enumerate(STEPS) for kind in step.moments}
# The moment that finishes each step, and so writes a document: at most one write per step.
FINISHES = tuple(step.moments[0] for step in STEPS)


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


# The moments in the middle of a run: no question or turn end waits for them, so the run names them when due.
MIDWAY = ('connect', 'sources', 'build')


def due(run, engine_dir=None):
    """The mid-run moments the run's files can show but the side view has not recorded, in order."""
    import visuals
    run = Path(run)
    if not (run / 'run.json').is_file():
        return []
    done = set(seen(run))
    return [kind for kind in MIDWAY if kind not in done
            and (kind == 'connect' or visuals.render(kind, visuals.build_spec(kind, run, engine_dir=engine_dir)))]


def model(run, state=None, engine_dir=None):
    """The whole view as one document: the steps, their status, what came out and how."""
    run = Path(run)
    state = state or _state(run)
    facts = RunFacts(run, engine_dir or state.get('engine_dir'))
    kinds = [m.get('kind') for m in state['moments'] if isinstance(m, dict)]
    last = max((STEP_OF[k] for k in kinds if k in STEP_OF), default=-1)
    frozen = state.setdefault('frozen', {})
    ctx = {'demo': bool(state.get('demo')), 'seen': kinds}
    steps = []
    for i, step in enumerate(STEPS):
        done = i <= last
        status = 'now' if i == last + 1 else 'next'
        if done:
            status = 'done' if step.name != 'Publish' else 'local' if facts.local else 'done' if facts.publication else 'unconfirmed'
        section = frozen.get(step.name) if done and step.name in frozen else step.build(facts, dict(ctx, open=not done))
        if done and status != 'unconfirmed':
            frozen.setdefault(step.name, section)
        nodes = [n for n in section.get('how') or [] if not done or n['n'] or n['always']]
        steps.append({'name': step.name, 'status': status, 'doing': step.doing.format(their=their(state.get('subject'))),
                      'purpose': step.purpose,
                      # The step that is running ticks each piece as it finishes; steps ahead only say how they work.
                      'how': [{k: v for k, v in (('t', n['t']), ('d', n['d']), ('by', n['by']), ('n', n['n'] if done or status == 'now' else None),
                                                 ('ai', n['ai'] or None), ('s', 'done' if done or status == 'now' and n.get('done') else 'next')) if v}
                              for n in nodes],
                      **{k: section[k] for k in ('fact', 'result', 'note') if done and section.get(k)}})
    current = next((i for i, s in enumerate(steps) if s['status'] == 'now'), len(STEPS))
    a = facts.file('answer.json') if 'answer' in kinds else {}
    return {'title': state.get('title') or 'Analytics setup', 'subject': state.get('subject'), 'demo': bool(state.get('demo')),
            **({'tagline': state.get('tagline') or facts.purpose} if state.get('tagline') or facts.purpose else {}),
            'updated': _now(), 'current': current, 'steps': steps,
            'parts': {by: PARTS[by] for by in dict.fromkeys(n['by'] for s in steps for n in s['how']) if by in PARTS},
            **({'answer': {**{k: a[k] for k in ('question', 'headline', 'summary', 'rule') if isinstance(a.get(k), str)},
                           **({'proof': [str(x.get('name')) for x in a['proof'] if isinstance(x, dict)][:3]} if isinstance(a.get('proof'), list) else {})}}
               if a.get('headline') else {})}


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
    """Log a moment; when it finishes a step, write that step's document and refresh the local page.

    Returns the paths to publish or push: `page` and `local` on the first write, `doc` on every write,
    and `url` once the page is published."""
    with _locked(run):
        return _record(Path(run), kind, spec or {}, engine_dir)


def _record(run, kind, spec, engine_dir):
    state = _state(run)
    before = [m.get('kind') for m in state['moments'] if isinstance(m, dict)]
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
    if kind in FINISHES and kind not in before:
        doc, path = _emit(run, state)
        if not before or not (run / 'view' / 'side.html').is_file():
            out['page'] = str(page(run, doc['title']))
            out['local'] = str(run / 'view' / 'progress.html')
        out['doc'] = str(path)
        if state.get('url'):
            out['url'] = state['url']
    _write(run / 'view' / 'state.json', json.dumps(state, ensure_ascii=False, indent=1))
    return out


def _ticks(doc):
    """What the page shows changing: each step's status and each piece of work's tick and count."""
    return [[s['status'], [[n.get('s'), n.get('n')] for n in s['how']]] for s in doc['steps']]


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
