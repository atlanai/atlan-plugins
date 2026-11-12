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
# Warehouses as a person names them; any other kind reads as its own name.
WAREHOUSES = {'snowflake': 'Snowflake', 'databricks': 'Databricks', 'bigquery': 'BigQuery', 'redshift': 'Redshift',
              'postgres': 'Postgres', 'postgresql': 'Postgres', 'duckdb': 'DuckDB'}


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
    if 'fraction' in low or low in ('ratio', 'share', 'percent', 'percentage', '%'):
        pct = value if low in ('percent', 'percentage', '%') else value * 100
        return ('%.1f' % _half_up(pct, 1)).rstrip('0').rstrip('.') + '%'
    if low.startswith('ratio of'):
        return '%.1f×' % _half_up(value, 1)
    symbol = CURRENCY.get(unit.upper(), '')
    # Large amounts read in whole units; small ones keep their pence or cents.
    places = 0 if abs(value) >= 100 else 2 if symbol and not float(value).is_integer() else 0 if float(value).is_integer() else 1
    rounded = _half_up(value, places)
    text = '{:,.{p}f}'.format(abs(rounded), p=places)
    return ('-' if rounded < 0 else '') + symbol + text


def sentence(name):
    """'On-Time Rate' reads as 'On-time rate'; words in capitals, such as SQL, stay; 'margin-by-job' reads as words."""
    name = str(name).strip()
    if re.fullmatch(r'[a-z0-9]+(?:[-_][a-z0-9]+)+', name):
        name = re.sub(r'[-_]', ' ', name).capitalize()
    words = re.split(r'(\s+|-)', name)
    out, first = [], True
    for w in words:
        if w and not w.isspace() and w != '-':
            if not first and w[:1].isupper() and w[1:].islower():
                w = w.lower()
            first = False
        out.append(w)
    return ''.join(out)


def table_name(name):
    """A table as a person says it: 'site.work_logs' reads as 'work logs'."""
    return str(name).split('.')[-1].replace('_', ' ')


def count(n, word, many=None):
    return '%s %s' % ('{:,}'.format(n), word if n == 1 else many or word + 's')


def their(subject):
    return subject + "'s" if subject else 'your'


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
        return read_json(self.dir.joinpath(*parts), kind, kind())

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

    @cached_property
    def warehouse(self):
        """The warehouse the run reads, as the connection recorded it in the run's state, or None for local data."""
        recorded = self.file('state.json').get('warehouse')
        kind = recorded.get('kind') if isinstance(recorded, dict) else None
        return WAREHOUSES.get(str(kind).lower(), str(kind)) if kind else None

    @cached_property
    def config(self):
        return self.file('engine-config.json')

    @cached_property
    def inputs(self):
        """What the person chose to build from, once the build is configured: {'bi': bool, 'history': bool}; None before."""
        given = self.config.get('inputs')
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
    def dashboard(self):
        """The dashboard the survey read: its tool, and how many formulas its file holds."""
        tools = [BI_TOOLS[s['kind']] for s in self.usable(*BI_TOOLS)]
        counts = [read_json(p, dict, {}).get('counts') for p in sorted((self.dir / 'context').glob('src_*.json'))]
        formulas = max((c.get('measures') or 0 for c in counts if isinstance(c, dict)), default=0)
        return {k: v for k, v in (('tool', tools[0] if tools else None), ('formulas', formulas)) if v}

    @cached_property
    def tables(self):
        """The loaded tables, largest first, as (name, rows)."""
        tables = self.file('context', 'data-preparation.json').get('tables') or []
        rows = [(str(t.get('table')).split('.')[-1], int(t.get('rows') or 0)) for t in tables if isinstance(t, dict) and t.get('table')]
        return sorted(rows, key=lambda t: -t[1])

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
        given = Path(self.engine_dir) if self.engine_dir else Path(self.meta['engine_run']) if self.meta.get('engine_run') else Path('engine')
        if not given.is_absolute():
            return self.dir / given
        return given if given.is_dir() or not (self.dir / given.name).is_dir() else self.dir / given.name

    def engine_file(self, name, kind=dict):
        return read_json(self.engine / name, kind, kind())

    @cached_property
    def stages(self):
        """The engine's stages by name, such as h0_corpus, each with the counts of its latest pass."""
        stages = self.engine_file('manifest.json').get('stages') or []
        return {s.get('stage') or s.get('name'): s.get('counts') if isinstance(s.get('counts'), dict) else {}
                for s in stages if isinstance(s, dict)}

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
        return {k: v for k, v in (('queries', self.query_log), ('tiles', tiles), ('questions', tiles + asked), ('metrics', metrics)) if v}

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
        rows.sort(key=lambda r: r['origin'] != 'tile')
        metrics_ran = sum(r['ok'] for r in rows)
        queries_ran = sum(_passed(checks.get('sql_query:' + q['name']) or {}) for q in queries)
        # One check fact for every place that counts: what passed, and what needs a look (did not pass, for any reason),
        # the save question's own rule.
        total = len(rows) + len(queries)
        return {'metrics': rows, 'queries': len(queries), 'metrics_ran': metrics_ran, 'queries_ran': queries_ran,
                'metrics_checked': sum(isinstance(checks.get('metric:' + m['name']), dict) for m in metrics),
                'queries_checked': sum(isinstance(checks.get('sql_query:' + q['name']), dict) for q in queries),
                'total': total, 'passed': metrics_ran + queries_ran, 'need_look': total - metrics_ran - queries_ran}

    @staticmethod
    def unit_of(metric):
        """A metric's unit as its values read: a percent whose formula never multiplies by 100 holds a share from 0 to 1."""
        d = metric.get('definition') if isinstance(metric.get('definition'), dict) else {}
        return _share_unit(str((d.get('output') or {}).get('unit') or ''), d.get('formula', ''))

    def unit_for(self, name):
        """The unit of the context's metric with this name, or None when the context has no such metric."""
        wanted = sentence(name or '').lower()
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
