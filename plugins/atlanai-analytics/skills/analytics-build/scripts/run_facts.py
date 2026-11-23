"""What a setup run holds, read from its own files, and the words a person reads for it.

The chat's chips and cards (visuals.py) and the side view (side_view.py) both read a run through
`RunFacts`, so they always agree. Every reader tolerates a missing, partial or malformed file and returns
nothing for it: a run that has not reached a step yet, or skipped a source, simply has less to show.
"""
from functools import cached_property
import json
import math
from pathlib import Path
import re

import context_contracts as contracts

CURRENCY = {'GBP': '£', 'USD': '$', 'EUR': '€'}
# Survey statuses of a source the run can read; a Tableau workbook surveys as needing the model's review.
USABLE = ('available', 'needs_model_review')
# The dashboard kinds source_inventory.source_kind reports, and the tool each one is.
BI_TOOLS = {'powerbi': 'Power BI', 'powerbi_project': 'Power BI', 'tableau': 'Tableau'}
# Every kind of source the survey records, as a person names it, in the order the found chip lists them.
FOUND = ((('powerbi',), 'Power BI dashboard'), (('powerbi_project',), 'Power BI project'), (('tableau',), 'Tableau dashboard'),
         (('dashboard_html',), 'HTML dashboard'), (('model_candidate',), 'model file'), (('data_file',), 'data file'),
         (('sqlite', 'duckdb'), 'database'), (('rowsets',), 'saved row set'), (('saved_sql',), 'saved SQL file'),
         (('query_history',), 'query history'))
# A found label as the person reads it, where it differs: the label itself stays, since visuals.FOUND_MARKS matches it.
FOUND_SAID = {'query history': ('past queries', 'files of past queries')}
# Warehouses as a person names them; any other kind reads as its own name.
WAREHOUSES = {'snowflake': 'Snowflake', 'databricks': 'Databricks', 'bigquery': 'BigQuery', 'redshift': 'Redshift',
              'postgres': 'Postgres', 'postgresql': 'Postgres', 'duckdb': 'DuckDB', 'mysql': 'MySQL', 'tsql': 'SQL Server',
              'mssql': 'SQL Server', 'sqlserver': 'SQL Server', 'oracle': 'Oracle', 'trino': 'Trino', 'presto': 'Presto',
              'athena': 'Athena', 'synapse': 'Synapse', 'teradata': 'Teradata', 'clickhouse': 'ClickHouse'}


DEMOS = Path(__file__).resolve().parents[2] / 'analytics-demo' / 'references'


def demo_facts(run):
    """A demo's facts file (its firm, title, agent and tour), found from the demo's own working folder, so a call that
    left out `--data` still names the demo. Only a run the journey records as a demo has one."""
    run = Path(run)
    if read_json(run / 'interaction.json', dict, {}).get('flow') != 'demo':
        return {}
    root = read_json(run / 'run.json', dict, {}).get('source_root')
    demo = Path(str(root)).name if root else ''
    if not re.fullmatch(r'[a-z][a-z-]{1,30}', demo):
        return {}
    return read_json(DEMOS / (demo + '.json'), dict, {})


def engine_folder(run):
    """For a flow that never named its engine run: the run's newest engine folder that has made metrics, else the
    newest with a manifest. A rebuild started in its own folder (a changed rule) does not hide the last build's
    metrics until it has its own."""
    def newest(pattern):
        found = []
        for m in Path(run).glob(pattern):
            try:
                found.append((m.stat().st_mtime, m))
            except OSError:  # removed while the run was read
                continue
        return max(found)[1].parent.name if found else None
    return newest('engine*/merged_metrics.json') or newest('engine*/manifest.json')


# Words ----------------------------------------------------------------------------------------

def _half_up(value, places=0):
    scale = 10 ** places
    return (1 if value >= 0 else -1) * int(abs(value) * scale + 0.5) / scale


def number(value, unit=None):
    """A checked value in the words a person reads: currency, a share, or a plain count."""
    if value is None or isinstance(value, bool):
        return 'blank'
    if isinstance(value, str):
        return value
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return 'blank'
    unit = (unit or '').strip()
    low = unit.lower()
    # A plain ratio past 1 is a multiple, such as a markup; within 1 it reads as a share.
    if 'fraction' in low or low in ('share', 'percent', 'percentage', '%') or low == 'ratio' and abs(value) <= 1:
        pct = value if low in ('percent', 'percentage', '%') else value * 100
        return ('%.1f' % _half_up(pct, 1)).rstrip('0').rstrip('.') + '%'
    if low.startswith(('ratio', 'times', 'multiple')):
        return '%.1f×' % _half_up(value, 1)
    # A currency per unit, such as "USD per ticket", keeps its sign.
    symbol = CURRENCY.get(unit.upper(), '') or CURRENCY.get(unit.split(' ')[0].upper(), '') if unit else ''
    # Large amounts read in whole units; small ones keep their pence or cents, and a value under 1 its first two figures.
    places = 0 if abs(value) >= 100 else 2 if symbol and not float(value).is_integer() else 0 if float(value).is_integer() else 1
    tiny = not symbol and 0 < abs(value) < 1
    if tiny:
        # At most six places: past that it is a float's rounding noise, such as a total that is zero.
        places = min(1 - math.floor(math.log10(abs(value))), 6)
    rounded = _half_up(value, places)
    text = '{:,.{p}f}'.format(abs(rounded), p=places)
    if tiny:
        text = text.rstrip('0').rstrip('.')
    return ('-' if rounded < 0 else '') + symbol + text


def first_question(text):
    """A description's first question: the engine's words can hold several in one string."""
    text = str(text or '').strip()
    found = re.match(r'(.+?\?)(?=\s|$)', text)
    return found.group(1) if found else text


# Names of months and days keep their capital wherever they fall in a name.
CALENDAR = {'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november',
            'december', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'}


def sentence(name):
    """'On-Time Rate' reads as 'On-time rate'; words in capitals, such as SQL, and months and days stay; 'margin-by-job'
    reads as words."""
    name = str(name).strip()
    if re.fullmatch(r'[a-z0-9]+(?:[-_][a-z0-9]+)+', name):
        name = re.sub(r'[-_]', ' ', name).capitalize()
    words = re.split(r'(\s+|-)', name)
    out, first = [], True
    for w in words:
        if w and not w.isspace() and w != '-':
            if not first and w[:1].isupper() and w[1:].islower() and re.sub(r'\W', '', w).lower() not in CALENDAR:
                w = w.lower()
            first = False
        out.append(w)
    return ''.join(out)


def clip(text, width):
    """Text on one line, cut to a width at a word with "…", never inside one ("delivery f…" read as broken in 0.9.74)."""
    text = ' '.join(str(text or '').split())
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    # A cut that ends where a word ends keeps that word.
    space = width - 1 if text[width - 1] == ' ' else cut.rfind(' ')
    return (cut[:space] if space > width // 2 else cut).rstrip(' ,;:·-') + '…'


def short_name(text, width=60):
    """A use case as a name: a long answer's own short name, the text before its first ":" or ".", else the answer cut
    at a word. "AE pod targets: what each pod's S1 target is. Table: GTM_OPE…" reads as "AE pod targets"."""
    text = ' '.join(str(text or '').split())
    if len(text) > width:
        head = re.split(r':|\.(?=\s|$)', text, maxsplit=1)[0].strip()
        text = head if head else text
    return clip(text, width)


def table_name(name):
    """A table as a person says it: 'site.work_logs' reads as 'work logs'."""
    return str(name).split('.')[-1].replace('_', ' ')


def count(n, word, many=None):
    return '%s %s' % ('{:,}'.format(n), word if n == 1 else many or word + 's')


def their(subject):
    return subject + "'s" if subject else 'your'


def agent_name(facts, demo=None):
    """Your agent's name, for "Your <name> is ready": a demo's own ("job profit agent"), else "agent for" the dashboard
    or the use case ("agent for Sales pipeline"), never the raw title with "agent" after it, else "agent". `demo` is a
    demo's ready facts when the caller holds them; otherwise they are read from the demo's own folder."""
    demo = demo if demo is not None else demo_facts(facts.dir).get('ready') or {}
    name = ' '.join(str(demo.get('agent') or '').split()) if isinstance(demo, dict) else ''
    if not name:
        # A long use case reads as a paragraph, not a name: its own short name stands for it.
        base = facts.report or short_name(facts.use_case)
        name = 'agent for ' + base if base and len(base) <= 60 else ''
    return name or 'agent'


# Files ----------------------------------------------------------------------------------------

def read_json(path, kind=None, default=None):
    """A JSON file, or `default` when it is missing, unreadable or not of the expected kind (dict or list)."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default
    return value if kind is None or isinstance(value, kind) else default


def read_lines(path):
    """The JSON lines of a file, skipping any line that does not parse."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line) if line.strip() else None
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def whole(value, default=0):
    """A count from a file, or `default` when it is not a number."""
    try:
        number_ = float(value)
    except (TypeError, ValueError):
        return default
    return int(number_) if math.isfinite(number_) else default


def from_dashboard(source_id):
    """The engine names a proposal drawn from a dashboard with a `bi-` prefix, and one from past queries with `h-`."""
    return str(source_id).startswith('bi-')


def _verdict(entry):
    """Whether an item's check has given its verdict. The structural pass marks every item `schema_checked` before
    anything runs."""
    return isinstance(entry, dict) and entry.get('status') != 'schema_checked'


def _passed(entry):
    if not isinstance(entry, dict):
        return False
    comp = entry.get('composition') if isinstance(entry.get('composition'), dict) else {}
    return (not entry.get('errors') and entry.get('status') in ('executed', 'example_executed')
            and comp.get('status', 'executed') == 'executed')


def _share_unit(unit, formula):
    """A percent metric whose formula never multiplies by 100 holds a share from 0 to 1."""
    if unit.lower() in ('percent', 'percentage', '%') and not re.search(r'\*\s*100(\.0+)?\b|\b100(\.0+)?\s*\*', str(formula)):
        return 'fraction'
    return unit


class RunFacts:
    """One run's files, each read at most once."""

    def __init__(self, run, engine_dir=None):
        self.dir = Path(run)
        self.engine_dir = engine_dir

    def file(self, *parts, kind=dict):
        return self._read(self.dir.joinpath(*parts), kind)

    def _read(self, path, kind):
        cache = self.__dict__.setdefault('_files', {})
        if (path, kind) not in cache:
            cache[(path, kind)] = read_json(path, kind, kind())
        return cache[(path, kind)]

    @cached_property
    def meta(self):
        return self.file('run.json')

    @cached_property
    def interaction(self):
        return self.file('interaction.json')

    @property
    def local(self):
        return self.interaction.get('mode') == 'local'

    @property
    def purpose(self):
        intent = self.interaction.get('intent')
        return intent.get('purpose') if isinstance(intent, dict) else None

    @property
    def use_case(self):
        intent = self.interaction.get('intent')
        return ' '.join(str(intent.get('domain') or '').split()) if isinstance(intent, dict) else ''

    @property
    def report(self):
        """The dashboard the person named for a dashboard run ("Sales pipeline: Overview, Stages" names Sales pipeline),
        or ''."""
        return ' '.join(str(self.interaction.get('report') or '').split(':')[0].split())

    @property
    def folder(self):
        """The working folder a chat opens in to find this run (`<folder>/.atlan-metrics/run_…`, or a dashboard project's
        `<folder>/.atlan-analytics/<slug>`), home as ~, or None for a run kept anywhere else. A dashboard run in
        `.atlan-analytics` read "this folder" in every message (0.9.76's live run)."""
        if self.dir.parent.name not in ('.atlan-metrics', '.atlan-analytics'):
            return None
        folder = self.dir.absolute().parent.parent
        try:
            return '~/' + str(folder.relative_to(Path.home())) if folder != Path.home() else '~'
        except ValueError:
            return str(folder)

    @cached_property
    def held(self):
        """How many metrics and saved queries the save held back, from its record of the holds."""
        holds = self.file('context', 'publication-holds.json')
        return {k: sum(1 for h in holds.get(k) or [] if isinstance(h, dict)) if isinstance(holds.get(k), list) else 0
                for k in ('metrics', 'queries')}

    @cached_property
    def warehouse(self):
        """The warehouse the run reads, as the connection recorded it in the run's state, or None for local data."""
        recorded = self.file('state.json').get('warehouse')
        kind = recorded.get('kind') if isinstance(recorded, dict) else None
        if not kind:
            return None
        # "other" is how the record says "not one we name": the person reads its dialect instead, which recording an
        # other warehouse requires ("other warehouse" in the review's run).
        dialect = str(recorded.get('dialect') or '').lower()
        if str(kind).lower() == 'other' and dialect and dialect != 'other':
            return WAREHOUSES.get(dialect, dialect.title())
        return WAREHOUSES.get(str(kind).lower(), str(kind))

    @cached_property
    def config(self):
        # A warehouse run keeps it in private/ (metrics-engine.md): its tables read "from your data" without this.
        return self.file('engine-config.json') or self.file('private', 'engine-config.json')

    @cached_property
    def inputs(self):
        """What the person chose to build from, once the build is configured: {'bi': bool, 'history': bool}; None before."""
        given = self.config.get('inputs')
        if not isinstance(given, dict):
            # A host can keep the agreed configuration elsewhere; the engine writes its own copy as JSON.
            given = read_json(self.engine / 'run.yml', dict, {}).get('inputs')
        if not isinstance(given, dict):
            return None
        return {k: bool(given.get(k)) for k in ('bi', 'history')}

    @cached_property
    def scoped_tables(self):
        """How many warehouse tables the build reads: the chosen scope, else the columns the run read."""
        chosen = self.config.get('tables')
        if isinstance(chosen, list) and chosen:
            return len(chosen)
        return len(self.file('context', 'table-schema.json'))

    @cached_property
    def sources(self):
        survey = self.file('survey-cache.json').get('sources') or self.meta.get('sources') or []
        return [s for s in survey if isinstance(s, dict)]

    def usable(self, *kinds):
        return [s for s in self.sources if s.get('kind') in kinds and s.get('status') in USABLE]

    @cached_property
    def found(self):
        """What the survey and the warehouse connection found, as (how many, the words for them): only what can be named
        is counted, so the count and the words always agree. Then the same labels as the person reads them."""
        items = [(1, '%s warehouse' % self.warehouse, '%s warehouse' % self.warehouse)] if self.warehouse else []
        # A report fetched from its link is in the engine's fetch record, not the folder's survey: the live run's chip read
        # "1 source · Snowflake warehouse" for a Power BI build (2026-11-23). It leads, as the source the build is from.
        fetched = self.engine_file('bi_fetch.json')
        plug = str(fetched.get('plug') or '') if fetched else ''
        # Not a fetch the access check blocked whole, which keeps its record with nothing read: the review's run still
        # counted that report as a source.
        per_plug = fetched.get('fetched') if fetched and isinstance(fetched.get('fetched'), dict) else {}
        blocked = bool(per_plug) and not fetched.get('bi') and all(
            isinstance(v, dict) and v.get('blocked') and not v.get('bi') for v in per_plug.values())
        if plug and not blocked and not self.usable(*BI_TOOLS):
            reports = ((read_json(Path(str(fetched.get('root') or '')) / 'manifest.json', dict, {}).get('reports') or {})
                       .get('fetched') or 1) if fetched.get('root') else 1
            word = 'Power BI dashboard' if plug.startswith('powerbi') else 'Tableau dashboard' if plug.startswith('tableau') \
                else 'dashboard'
            said = 'Power BI report' if plug.startswith('powerbi') else word
            items.insert(0, (reports, word, said if reports == 1 else '%d %ss' % (reports, said)))
        for kinds, word in FOUND:
            n = sum(1 for s in self.sources if s.get('kind') in kinds)
            if not n:
                continue
            # A Power BI project is one report kept as many files; a dashboard or a history reads alone when it is one.
            label = ('%s (%s)' % (word, count(n, 'file')) if n > 1 else word) if kinds == ('powerbi_project',) else \
                word if n == 1 and kinds[0] in ('powerbi', 'tableau', 'dashboard_html', 'query_history') else count(n, word)
            said = FOUND_SAID.get(word)
            items.append((n, label, label if not said else said[0] if n == 1 else '%d %s' % (n, said[1])))
        return sum(n for n, _, _ in items), [label for _, label, _ in items], [said for _, _, said in items]

    @cached_property
    def dashboard(self):
        """The dashboard the survey read: its tool, and how many formulas its file holds."""
        tools = [BI_TOOLS[s['kind']] for s in self.usable(*BI_TOOLS)]
        counts = [read_json(p, dict, {}).get('counts') for p in sorted((self.dir / 'context').glob('src_*.json'))]
        formulas = max((c.get('measures') or 0 for c in counts if isinstance(c, dict)), default=0)
        return {k: v for k, v in (('tool', tools[0] if tools else None), ('formulas', formulas)) if v}

    @cached_property
    def tables(self):
        """The tables the checks run on, largest first, as (name, rows): the ones prepared from files, or else the ones an
        existing database was bound with, whose rows were never counted (None)."""
        tables = self.file('context', 'data-preparation.json').get('tables') or []
        rows = [(str(t.get('table')).split('.')[-1], int(t.get('rows') or 0)) for t in tables if isinstance(t, dict) and t.get('table')]
        if not rows:
            bound = (self.meta.get('execution') or {}).get('tables') if isinstance(self.meta.get('execution'), dict) else None
            rows = [(str(name).split('.')[-1], None) for name in bound or [] if name]
        # Once the person has chosen, only the tables they chose: a bound database can hold more.
        chosen = {str(t).split('.')[-1].lower() for t in self.config.get('tables') or [] if t}
        kept = [r for r in rows if r[0].lower() in chosen]
        rows = kept if chosen and kept else rows
        return sorted(rows, key=lambda t: -(t[1] or 0))

    @cached_property
    def history(self):
        """The query history the survey found: distinct statements and how often they ran, read the way the survey reads
        it (JSON lines, a JSON array or CSV, any column case). Query logs come either one row per statement with a run
        count, or one row per run."""
        root = self.meta.get('source_root')
        source = next((s for s in self.meta.get('sources') or [] if isinstance(s, dict) and s.get('kind') == 'query_history'
                       and s.get('status') in USABLE and s.get('path')), None)
        if root and source:
            try:
                rows = [r for r in contracts.read_querylog_source(Path(root) / source['path']) if isinstance(r, dict)]
            except (OSError, ValueError, UnicodeDecodeError):
                return None
        else:
            # A warehouse's history arrives as the engine's own input file, pulled through the query tool.
            rows = read_lines(self.dir / 'context' / 'querylog.jsonl')
        picked = [(contracts._pick(r, contracts._SQL_COLS), contracts._pick(r, contracts._EXEC_COLS)) for r in rows]
        picked = [(re.sub(r'\s+', ' ', str(sql)).strip().lower(), whole(runs, 1)) for sql, runs in picked if sql]
        if not picked:
            return None
        return {'statements': len({sql for sql, _ in picked}), 'runs': sum(runs for _, runs in picked)}

    @cached_property
    def engine(self):
        """The engine folder: one named on the command, else the run's current engine run. A run folder that moved
        keeps finding its engine by name inside it."""
        if self.engine_dir or self.meta.get('engine_run'):
            given = Path(self.engine_dir or self.meta['engine_run'])
        else:
            given = Path(engine_folder(self.dir) or 'engine')
        if not given.is_absolute():
            return self.dir / given
        return given if given.is_dir() or not (self.dir / given.name).is_dir() else self.dir / given.name

    def engine_file(self, name, kind=dict):
        return self._read(self.engine / name, kind)

    @cached_property
    def descriptions(self):
        """The engine's plain words for each verified query, by id: an object, as its schema has it."""
        said = self.engine_file('sql_descriptions.json').get('descriptions')
        return {str(k): v for k, v in said.items() if isinstance(v, dict)} if isinstance(said, dict) else {}

    @cached_property
    def stages(self):
        """The engine's stages by name, such as h0_corpus, each with the counts of its latest pass."""
        stages = self.engine_file('manifest.json').get('stages') or []
        latest = {s.get('stage') or s.get('name'): s for s in stages if isinstance(s, dict)}
        # A stage the engine refused records its entry too, as blocked: it has not finished.
        return {name: s.get('counts') if isinstance(s.get('counts'), dict) else {}
                for name, s in latest.items() if s.get('verdict') not in ('blocked', 'failed')}

    def stage(self, name, key):
        """A stage's count, or None when the stage did not run or did not record it."""
        value = (self.stages.get(name) or {}).get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @cached_property
    def query_log(self):
        """How many past queries the run took in, before the privacy filter."""
        return len(read_lines(self.dir / 'context' / 'querylog.jsonl'))

    @cached_property
    def merged(self):
        """Each metric the engine made, with how many dashboard tiles and past queries it came from."""
        out = []
        for m in self.engine_file('merged_metrics.json').get('metrics') or []:
            if not isinstance(m, dict) or not m.get('name'):
                continue
            ids = [str((s.get('proposal_id') if isinstance(s, dict) else s) or '') for s in m.get('sources') or []]
            tiles = sum(1 for i in ids if from_dashboard(i))
            out.append({'name': sentence(m['name']), 'tiles': tiles, 'queries': len(ids) - tiles})
        return sorted(out, key=lambda m: (not m['tiles'], -(m['tiles'] + m['queries'])))

    @cached_property
    def funnel(self):
        """Past queries in, questions written up (tiles and past questions), metrics out."""
        merged = self.engine_file('merged_metrics.json')
        if not merged:
            return {}
        tiles = len(self.engine_file('verified_queries.bi.json').get('objects') or [])
        asked = len(self.engine_file('verified_queries.history.json').get('objects') or [])
        metrics = merged.get('n_metrics_out') if isinstance(merged.get('n_metrics_out'), int) else len(self.merged)
        # Past queries read, the number the sources chip shows; the side page shows how many of them ran. None when the
        # person built from the dashboard alone.
        chose = self.inputs is None or self.inputs['history']
        read = ((self.history or {}).get('statements') or self.query_log) if chose else None
        return {k: v for k, v in (('queries', read), ('tiles', tiles), ('questions', tiles + asked), ('metrics', metrics)) if v}

    @cached_property
    def tile_figures(self):
        """What each dashboard tile's own query returned when it ran on the data, by its id, as (tile name, number):
        only a tile whose query returned one number."""
        out = {}
        for o in self.engine_file('verified_queries.bi.json').get('objects') or []:
            w = o.get('witness') if isinstance(o, dict) and isinstance(o.get('witness'), dict) else {}
            sample = w.get('sample')
            if w.get('result') != 'pass' or not (isinstance(sample, list) and len(sample) == 1 and isinstance(sample[0], list)
                                                 and len(sample[0]) == 1):
                continue
            try:
                value = float(sample[0][0])
            except (TypeError, ValueError):
                continue
            prov = o.get('provenance') if isinstance(o.get('provenance'), dict) else {}
            bi = prov.get('bi') if isinstance(prov.get('bi'), dict) else {}
            if bi.get('visual'):
                out[o.get('vq_id')] = (sentence(str(bi['visual'])), value)
        return out

    @cached_property
    def context(self):
        """The context as checked: each metric's value, and how many items ran cleanly. Empty before the checks."""
        metrics, queries = self.file('context', 'metrics.json', kind=list), self.file('context', 'queries.json', kind=list)
        checks = self.file('context', 'candidate-checks.json')
        if not checks:
            return {}
        metrics = [m for m in metrics if isinstance(m, dict) and m.get('name')]
        queries = [q for q in queries if isinstance(q, dict) and q.get('name')]
        rows = []
        for m in metrics:
            entry = checks.get('metric:' + m['name'])
            entry = entry if isinstance(entry, dict) else {}
            d = m.get('definition') if isinstance(m.get('definition'), dict) else {}
            comp = entry.get('composition') if isinstance(entry.get('composition'), dict) else {}
            values = comp.get('rows') if isinstance(comp.get('rows'), list) and comp.get('rows') else [[None]]
            values = [v if isinstance(v, list) else [v] for v in values]
            unit = self.unit_of(m)
            ok = bool(entry) and _passed(entry)
            derived = (d.get('provenance') or {}).get('derived_from') if isinstance(d.get('provenance'), dict) else []
            rows.append({'name': sentence(m.get('display_name') or m['name']), 'ok': ok,
                         'origin': 'tile' if any(from_dashboard(x) for x in derived or []) else 'queries',
                         'value': 'not run' if not ok else number(values[0][0] if values and values[0] else None, unit)
                         if len(values) == 1 else count(len(values), 'row')})
            # Beside the dashboard's own figure, when the metric came from a tile whose query returned one number.
            tile = next((self.tile_figures[x] for x in derived or [] if x in self.tile_figures), None)
            if ok and tile and len(values) == 1:
                shown = number(tile[1], unit)
                rows[-1]['tile'] = {'name': tile[0], 'value': shown, 'same': shown == rows[-1]['value']}
        rows.sort(key=lambda r: r['origin'] != 'tile')
        metrics_ran = sum(r['ok'] for r in rows)
        queries_ran = sum(_passed(checks.get('sql_query:' + q['name']) or {}) for q in queries)
        metrics_checked = sum(_verdict(checks.get('metric:' + m['name'])) for m in metrics)
        queries_checked = sum(_verdict(checks.get('sql_query:' + q['name'])) for q in queries)
        # One check fact for every place that counts: what passed, and what needs a look (did not pass, for any reason),
        # the save question's own rule. While checks still run, `failed` is what ran and did not pass.
        total, passed = len(rows) + len(queries), metrics_ran + queries_ran
        return {'metrics': rows, 'queries': len(queries), 'metrics_ran': metrics_ran, 'queries_ran': queries_ran,
                'metrics_checked': metrics_checked, 'queries_checked': queries_checked, 'total': total, 'passed': passed,
                'need_look': total - passed, 'failed': metrics_checked + queries_checked - passed}

    @property
    def checks_over(self):
        """Whether the checks are over: every item has its verdict, or, on local data, the flow has moved on to the
        preview or later (an item left without one then needs a look). Never while they still run: the structural check
        records the check stage complete before any sample has run. A warehouse run's verdicts reach its files only as
        each tool result is recorded, so one left without a verdict is not counted as needing a look."""
        c = self.context
        if not c:
            return False
        if c['metrics_checked'] + c['queries_checked'] == c['total']:
            return True
        return (self.checks_here and not self.checks_skipped
                and self.file('progress.json').get('stage') in ('preview', 'publish', 'ready'))

    @property
    def checks_skipped(self):
        """Whether the person chose to skip the checks (`journey checks --answer skip`) and some item has no verdict:
        an item left without one is untested, never needing a look."""
        c = self.context
        return (self.interaction.get('checks') == 'skip'
                and not (c and c['metrics_checked'] + c['queries_checked'] == c['total']))

    @property
    def checks_here(self):
        """Whether the sample checks run through this helper, which records each verdict in the run's files: local data.
        A warehouse's checks run with its own read-only tool."""
        return not self.warehouse

    @staticmethod
    def unit_of(metric):
        """A metric's unit as its values read: a percent whose formula never multiplies by 100 holds a share from 0 to 1."""
        d = metric.get('definition') if isinstance(metric.get('definition'), dict) else {}
        return _share_unit(str((d.get('output') or {}).get('unit') or ''), d.get('formula', ''))

    def unit_for(self, name):
        """The unit of the context's metric with this name, or None when the context has no such metric."""
        wanted = sentence(name or '').lower()
        if not wanted:
            return None
        for m in self.file('context', 'metrics.json', kind=list):
            if isinstance(m, dict) and wanted in (sentence(m.get('display_name') or '').lower(), sentence(m.get('name') or '').lower()):
                return self.unit_of(m) or None
        return None

    @cached_property
    def publication(self):
        """What the publish receipt proves: the items read back as live, and where."""
        items = [a for a in (self.meta.get('publication') or {}).get('artifacts') or [] if isinstance(a, dict) and a.get('name')]
        if not items:
            return {}
        plan = self.file('context', 'plan.json')
        counts = plan.get('counts') if isinstance(plan.get('counts'), dict) else {}
        links = counts.get('links') if isinstance(counts.get('links'), int) else len(plan.get('links') or [])
        return {'metrics': [sentence(a['name']) for a in items if a.get('kind') == 'metric'],
                'queries': sum(1 for a in items if a.get('kind') == 'sql_query'), 'items': len(items), 'links': links,
                'planned': plan.get('counts') or {}, 'workspace': self.file('saved.json').get('workspace')}
