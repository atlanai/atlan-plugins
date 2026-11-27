#!/usr/bin/env python3
"""Readiness for Atlan Analytics: what this machine has, checked, and written up as a report.

    readiness.py [--workspace DIR] probe [--fresh]
    readiness.py [--workspace DIR] check snowflake --connection LABEL [--timeout SECONDS]
    readiness.py [--workspace DIR] check databricks --profile LABEL [--timeout SECONDS]
    readiness.py [--workspace DIR] check bigquery --configuration LABEL [--project ID] [--timeout SECONDS]
    readiness.py [--workspace DIR] check redshift --profile LABEL --database DB (--workgroup NAME | --cluster NAME)
    readiness.py [--workspace DIR] record CHECK --status STATUS [--set KEY=VALUE ...]
    readiness.py [--workspace DIR] report

Everything it runs is read-only: version commands, each tool's own listing of its connection,
profile or MCP server names, and one check statement for a route the person said yes to. It never
opens a CLI's or an agent's configuration file, never installs, signs in or changes anything, and
keeps names only: a name that looks like an account identifier becomes a label such as
`snowflake connection 2`, and error text loses hosts, addresses and token-like strings. What the
person has to do (install a CLI, sign in, add a connection) becomes a suggestion in the report,
with the command for them to run.

State and the report live in <workspace>/.atlan-analytics/readiness/. Standard library only, and
written for any Python 3.7+, because it runs before the Python Atlan Analytics needs (3.11 or later)
is in place, and says when it is missing.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCHEMA = 2
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
ROOT = '.atlan-analytics'
FOLDER = 'readiness'
PLUGIN_PYTHON = (3, 11)  # what Atlan Analytics runs on
READ_LIMIT = 50 * 1024 * 1024  # the most read from one dashboard file or archive entry

KINDS = {'snowflake': 'Snowflake', 'databricks': 'Databricks', 'bigquery': 'BigQuery', 'redshift': 'Redshift',
         'postgres': 'Postgres', 'clickhouse': 'ClickHouse', 'fabric': 'Microsoft Fabric', 'trino': 'Trino',
         'duckdb': 'DuckDB', 'sqlite': 'SQLite', 'other': 'Your warehouse'}
# In the Atlan Store's MCP catalog: an Atlan admin adds them once, then each person signs in.
STORE_KINDS = ('snowflake', 'databricks', 'clickhouse')
ADMIN_ROLES = ('ACCOUNTADMIN', 'SECURITYADMIN', 'SYSADMIN', 'ORGADMIN', 'USERADMIN')

AGENTS = (  # name, binaries, macOS apps
    ('Claude Code', ('claude',), ()),
    ('Claude desktop', (), ('Claude.app',)),
    ('Codex', ('codex',), ('Codex.app',)),
    ('Cursor', ('cursor-agent', 'cursor'), ('Cursor.app',)),
    ('Cortex Code', ('cortex',), ()),
    ('Windsurf', ('windsurf',), ('Windsurf.app',)),
    ('Gemini CLI', ('gemini',), ()),
    ('GitHub Copilot CLI', ('copilot',), ()),
    ('VS Code', ('code',), ('Visual Studio Code.app',)),
    ('OpenCode', ('opencode',), ()),
    ('Amp', ('amp',), ()),
    ('Kiro', ('kiro',), ('Kiro.app',)),
    ('Aider', ('aider',), ()),
)
NO_VERSION = ('kiro',)  # no documented version flag
# The agents whose own command lists their MCP servers. The others keep them in files that can hold
# tokens, which this check never opens.
MCP_LIST = {'Claude Code': ('claude', 'mcp', 'list'), 'Codex': ('codex', 'mcp', 'list', '--json'),
            'Gemini CLI': ('gemini', 'mcp', 'list')}
SERVER_CLASSES = (  # a server's class from its name: a hint, not proof
    ('snowflake', ('snowflake', 'snowflakecomputing', 'cortex')),
    ('databricks', ('databricks', 'azuredatabricks', 'genie', 'unity')),
    ('bigquery', ('bigquery', 'gcp', 'google-cloud')),
    ('redshift', ('redshift',)),
    ('other warehouse', ('postgres', 'pg', 'supabase', 'neon', 'clickhouse', 'fabric', 'mssql', 'trino', 'starburst',
                         'duckdb', 'motherduck')),
    ('BI', ('tableau', 'powerbi', 'power-bi', 'pbi', 'looker', 'metabase', 'sigma', 'thoughtspot', 'superset')),
    ('Atlan', ('atlan',)),
)

WAREHOUSE_CLIS = (  # kind, binary
    ('snowflake', 'snow'), ('snowflake', 'cortex'), ('databricks', 'databricks'), ('bigquery', 'gcloud'),
    ('bigquery', 'bq'), ('redshift', 'aws'), ('postgres', 'psql'), ('clickhouse', 'clickhouse'),
    ('clickhouse', 'clickhouse-client'), ('fabric', 'sqlcmd'), ('fabric', 'az'), ('trino', 'trino'),
    ('duckdb', 'duckdb'),
)
BI_FILES = {'.pbix': 'Power BI', '.pbit': 'Power BI', '.pbip': 'Power BI', '.bim': 'Power BI',
            '.twb': 'Tableau', '.twbx': 'Tableau', '.tds': 'Tableau', '.tdsx': 'Tableau', '.hyper': 'Tableau',
            '.lkml': 'Looker', '.qvf': 'Qlik'}
BI_APPS = re.compile(r'tableau|power ?bi|qlik|dbeaver|metabase|looker|excel', re.I)
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', ROOT, 'dist', 'build', '.tox', '.idea', '.vscode'}

STATUSES = ('verified', 'failed', 'present', 'none', 'not_checked', 'answered', 'unanswered')
ENUMS = {
    'via': ('atlan', 'host', 'cli', 'none'), 'kind': tuple(KINDS), 'admin': ('yes', 'no'), 'task_list': ('yes', 'no'),
    'inside_file': ('yes', 'no'), 'tables_known': ('yes', 'roughly', 'no'), 'scope': ('everyone', 'your own'),
}
NUMBERS = ('workspaces', 'pages', 'measures')
CHECKS = {
    'use-case': (('answered', 'unanswered'), ('text',)),
    'harness': (('verified',), ('name', 'version', 'task_list')),
    'atlan': (('verified', 'failed', 'none', 'not_checked'), ('workspaces', 'error')),
    'warehouse': (('verified', 'failed', 'present', 'none', 'not_checked'),
                  ('kind', 'via', 'server', 'connection', 'user', 'role', 'warehouse', 'admin', 'error', 'reason')),
    'bi': (('answered', 'unanswered', 'verified', 'failed', 'not_checked'),
           ('tool', 'dashboard', 'file', 'pages', 'measures', 'inside_file', 'tables_known', 'tables', 'error')),
    'history': (('verified', 'failed', 'not_checked'), ('kind', 'scope', 'error', 'reason')),
    'participant': (('answered', 'unanswered'), ('name', 'email', 'source')),
    'note': (('answered',), ('text',)),
}
MASKED = ('server', 'connection')
SCRUBBED = ('error', 'reason')
LONG = ('text', 'tables')


class ReadinessError(Exception):
    pass


# ---------------------------------------------------------------- names, text and commands

def looks_like_identifier(name):
    """People name connections after the account (`ab12345`, `Snowflake - ab12345`, `adb-1234567890`,
    `<org>-<account>`). A token mixing letters and digits, a long run of digits, a run of five or more
    letters with no vowel (a generated organization name), or a dot, slash, colon or `@` counts. It is a
    heuristic: the skill also never writes a name that reads as an account, workspace or organization id."""
    if re.search(r'[./@:\\]', name):
        return True
    for token in re.split(r'[\s_-]+', name):
        if re.search(r'[A-Za-z]', token) and re.search(r'\d', token):
            return True
        if re.fullmatch(r'\d{5,}', token):
            return True
        if len(token) >= 5 and token.isalpha() and not re.search(r'[aeiouyAEIOUY]', token):
            return True
    return False


def label(kind, noun, index, name):
    return '%s %s %d' % (kind, noun, index) if looks_like_identifier(name) else name


def scrub(text, limit=200):
    """One line of error text without URLs, hosts, addresses, emails or token-like strings."""
    line = next((l.strip() for l in str(text or '').splitlines() if l.strip()), '')
    line = re.sub(r'\b[A-Za-z][A-Za-z0-9+.-]*://\S+', '<url>', line)
    line = re.sub(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', '<email>', line)
    line = re.sub(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', '<address>', line)
    line = re.sub(r'\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b', '<host>', line)
    line = re.sub(r'\b[A-Za-z0-9_\-+/=]{24,}\b', '<redacted>', line)
    line = re.sub(r'\b\d{12}\b', '<account>', line)
    return line[:limit]


def one_line(value, limit):
    return re.sub(r'\s+', ' ', str(value)).strip()[:limit]


def run(args, timeout=10, env=None):
    """Run a read-only command: (returncode, stdout, stderr), or (None, '', reason) when it could not finish."""
    try:
        done = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout, encoding='utf-8', errors='replace', env=env)
    except subprocess.TimeoutExpired:
        return None, '', 'timeout'
    except OSError as error:
        return None, '', error.__class__.__name__
    return done.returncode, done.stdout or '', done.stderr or ''


def version_of(binary, timeout=10):
    code, out, err = run([binary, '--version'], timeout)
    text = (out or err).strip()
    if code is None or not text:
        return None
    match = re.search(r'\d+\.\d+(?:\.\d+)?', text)
    return match.group(0) if match else one_line(text.splitlines()[0], 40)


def find_snow():
    candidates = [shutil.which('snow'), str(Path.home() / '.local' / 'bin' / 'snow')]
    candidates += [str(p) for p in sorted((Path.home() / '.cache' / 'uv' / 'archive-v0').glob('*/bin/snow'))]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def json_of(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def installed(name):
    """On PATH, or in the usual install folders an agent's short PATH can leave out."""
    folders = (Path('/opt/homebrew/bin'), Path('/usr/local/bin'), Path.home() / '.local' / 'bin',
               Path.home() / '.cargo' / 'bin')
    return bool(shutil.which(name)) or any((f / name).is_file() for f in folders)


# ---------------------------------------------------------------- state

def folder(workspace):
    return Path(workspace).expanduser().resolve() / ROOT / FOLDER


def empty():
    return {'schema': SCHEMA, 'checks': {}, 'warehouse': [], 'notes': []}


def load(workspace):
    path = folder(workspace) / 'state.json'
    if not path.exists():
        return empty()
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        raise ReadinessError('The saved readiness state is not readable JSON. Run `probe --fresh` to start again.')
    if not isinstance(state, dict) or state.get('schema') != SCHEMA:
        raise ReadinessError('The saved readiness state is from another version. Run `probe --fresh` to start again.')
    for key, value in empty().items():
        state.setdefault(key, value)
    return state


def save(workspace, state):
    where = folder(workspace)
    where.mkdir(parents=True, exist_ok=True, mode=0o700)
    write(where / 'state.json', json.dumps(state, indent=1, ensure_ascii=False))


def write(path, text):
    temp = path.with_name('.pending-' + path.name)
    temp.write_text(text, encoding='utf-8')
    try:
        os.chmod(str(temp), 0o600)
    except OSError:
        pass
    temp.replace(path)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


# ---------------------------------------------------------------- probe: the coding agent and the machine

def harness_hint():
    env = os.environ
    if env.get('CLAUDECODE'):
        return 'Claude Code'
    if env.get('CODEX_THREAD_ID') or env.get('CODEX_SANDBOX') or env.get('CODEX_SANDBOX_NETWORK_DISABLED'):
        return 'Codex'
    if env.get('CURSOR_AGENT'):
        return 'Cursor'
    return None


def plugin_python():
    """The command for a Python Atlan Analytics runs on (3.11 or later), and its version. An agent's shell
    can have a short PATH, so the usual install folders are looked in too; one found only there is named by
    its full path, with the home folder written as ~."""
    if sys.version_info[:2] >= PLUGIN_PYTHON:
        return ('py -3' if os.name == 'nt' else 'python3'), '%d.%d' % sys.version_info[:2]
    names = ['python3.13', 'python3.12', 'python3.11', 'python3.14', 'python3.15', 'python3', 'python']
    folders = [Path('/opt/homebrew/bin'), Path('/usr/local/bin'), Path.home() / '.local' / 'bin',
               Path.home() / '.pyenv' / 'shims'] + sorted(Path('/Library/Frameworks/Python.framework/Versions').glob('3.*/bin'))
    candidates = [[name] for name in names if shutil.which(name)] + ([['py', '-3']] if os.name == 'nt' and shutil.which('py') else [])
    candidates += [[str(f / name)] for f in folders for name in names if (f / name).is_file()]
    for command in candidates:
        code, out, _ = run(command + ['-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], 10)
        found = out.strip()
        if code == 0 and re.fullmatch(r'\d+\.\d+', found) and tuple(int(x) for x in found.split('.')) >= PLUGIN_PYTHON:
            shown = ' '.join(command)
            home = str(Path.home())
            return ('~' + shown[len(home):] if shown.startswith(home) else shown), found
    return None, None


def machine():
    system = platform.system()
    if system == 'Darwin':
        name = 'macOS ' + (platform.mac_ver()[0] or '')
    elif system == 'Windows':
        name = 'Windows ' + platform.release()
    else:
        name = system + ' ' + platform.release()
    command, version = plugin_python()
    return {'os': name.strip(), 'arch': platform.machine(), 'python': '%d.%d' % sys.version_info[:2],
            'plugin_python': command, 'plugin_python_version': version, 'windows': os.name == 'nt',
            'uv': installed('uv'), 'brew': installed('brew'), 'winget': installed('winget')}


def applications():
    if platform.system() != 'Darwin':
        return set()
    names = set()
    for where in (Path('/Applications'), Path.home() / 'Applications'):
        try:
            names.update(p.name for p in where.iterdir())
        except OSError:
            continue
    return names


WHOLE_WORDS = ('pg', 'pbi', 'gcp', 'neon', 'unity', 'genie', 'sigma')  # matched as words: "community" is not Unity


def server_class(name):
    tokens = set(re.split(r'[^a-z0-9]+', name.lower()))
    lowered = name.lower()
    for cls, words in SERVER_CLASSES:
        for word in words:
            if (word in tokens) if word in WHOLE_WORDS else (word in lowered):
                return cls
    return 'other'


def server_status(text):
    text = text.lower()
    if re.search(r'needs auth|authenticat|log.?in|sign.?in', text):
        return 'needs sign-in'
    if 'connected' in text and 'not connected' not in text:
        return 'connected'
    if 'fail' in text or 'error' in text or 'not connected' in text:
        return 'failed'
    return 'configured'


def parse_mcp(text):
    """`name: what - status` lines (Claude Code, Gemini CLI), or Codex's JSON list. Names and status only:
    the URL or command beside each name is dropped here, unread."""
    data = json_of(text)
    if isinstance(data, list):
        return [(str(s.get('name')), 'enabled' if s.get('enabled', True) else 'disabled')
                for s in data if isinstance(s, dict) and s.get('name')]
    servers = []
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r'^(?:[✓✗●○•*-]\s*)?(.+?):\s+(.*)$', line)
        if not match or re.match(r'(?i)checking|no mcp servers', line):
            continue
        rest = match.group(2)
        servers.append((match.group(1).strip(), server_status(rest.rsplit(' - ', 1)[-1] if ' - ' in rest else rest)))
    return servers


def mcp_servers(found_agents):
    """Each agent's MCP servers by name: warehouse, BI and Atlan servers named, the rest counted."""
    def one(name):
        command = MCP_LIST[name]
        code, out, _ = run(list(command), 90)
        if code != 0 and command[-1] == '--json':
            code, out, _ = run(list(command[:-1]), 90)
            rows = [(l.split()[0], 'configured') for l in out.splitlines()[1:] if l.strip()] if code == 0 else None
        else:
            rows = parse_mcp(out) if code == 0 else None
        if rows is None:
            return {'agent': name, 'note': 'could not list its servers'}
        named, others, counts = [], 0, {}
        for server, status in rows:
            cls = server_class(server)
            if cls == 'other':
                others += 1
                continue
            counts[cls] = counts.get(cls, 0) + 1
            named.append({'label': label(cls.split()[0].lower(), 'server', counts[cls], server), 'class': cls,
                          'status': status})
        return {'agent': name, 'servers': named, 'others': others}

    names = [a['name'] for a in found_agents if a['name'] in MCP_LIST and a['via'] == 'cli']
    with ThreadPoolExecutor(max_workers=3) as pool:
        listed = list(pool.map(one, names))
    unlisted = [a['name'] for a in found_agents if a['name'] not in MCP_LIST]
    return listed + [{'agent': n, 'note': 'no list command; its servers were not read'} for n in unlisted]


def agents(apps):
    found, versions = [], {}
    present = [(name, [b for b in binaries if shutil.which(b)], [a for a in bundles if a in apps])
               for name, binaries, bundles in AGENTS]
    present = [(name, bins) for name, bins, bundles in present if bins or bundles]
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {name: pool.submit(version_of, bins[0]) for name, bins in present if bins and bins[0] not in NO_VERSION}
        for name, job in jobs.items():
            versions[name] = job.result()
    for name, bins in present:
        found.append({'name': name, 'version': versions.get(name), 'via': 'cli' if bins else 'app'})
    return found


def identity():
    """Where the person's name and work email can come from, after the warehouse check's own user."""
    found = {}
    if shutil.which('git'):
        for key in ('name', 'email'):
            code, out, _ = run(['git', 'config', '--global', 'user.' + key], 10)
            if code == 0 and out.strip():
                found['git_' + key] = one_line(out, 200)
    if platform.system() == 'Darwin':
        code, out, _ = run(['id', '-F'], 10)
        if code == 0 and out.strip():
            found['os_name'] = one_line(out, 200)
    elif os.name == 'nt':
        code, out, _ = run(['whoami', '/upn'], 10)
        if code == 0 and '@' in out:
            found['os_email'] = one_line(out, 200)
    if shutil.which('gh'):
        code, out, _ = run(['gh', 'api', 'user', '--jq', '.name'], 15)
        if code == 0 and out.strip() and out.strip() != 'null':
            found['github_name'] = one_line(out, 200)
    return found


# ---------------------------------------------------------------- probe: warehouse CLIs

def snow_names(snow):
    code, out, err = run([snow, 'connection', 'list', '--format', 'JSON'], 30)
    if code != 0:
        blocked = "configure handler 'file'" in err or 'Operation not permitted' in err
        return None, 'blocked by the sandbox' if blocked else 'could not list connections'
    rows = json_of(out)
    if not isinstance(rows, list):
        return None, 'could not list connections'
    names = [(r.get('connection_name', r.get('name')), r.get('is_default') is True) for r in rows if isinstance(r, dict)]
    # Everything else the CLI printed (account, user, host) is dropped here, unread.
    return [(n, d) for n, d in names if isinstance(n, str) and n], None


def databricks_names(binary):
    code, out, _ = run([binary, 'auth', 'profiles', '--skip-validate', '--output', 'json'], 30)
    data = json_of(out) if code == 0 else None
    rows = data.get('profiles') if isinstance(data, dict) else data
    names = [(r.get('name'), False) for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
    names = [(n, d) for n, d in names if isinstance(n, str) and n]
    if os.environ.get('DATABRICKS_HOST') and os.environ.get('DATABRICKS_TOKEN'):
        names.append(('environment', False))  # set in the environment: named, its values never read
    if not isinstance(rows, list) and not names:
        return None, 'could not list profiles'
    return names, None


def gcloud_names(binary):
    code, out, _ = run([binary, 'config', 'configurations', 'list', '--format=json'], 30)
    rows = json_of(out) if code == 0 else None
    if not isinstance(rows, list):
        return None, 'could not list configurations'
    return [(r.get('name'), r.get('is_active') is True) for r in rows
            if isinstance(r, dict) and isinstance(r.get('name'), str)], None


def aws_names(binary):
    code, out, _ = run([binary, 'configure', 'list-profiles'], 30)
    if code != 0:
        return None, 'could not list profiles'
    return [(l.strip(), False) for l in out.splitlines() if l.strip()], None


LISTERS = {  # binary: (lister, kind word, noun)
    'snow': (snow_names, 'snowflake', 'connection'), 'databricks': (databricks_names, 'databricks', 'profile'),
    'gcloud': (gcloud_names, 'bigquery', 'configuration'), 'aws': (aws_names, 'aws', 'profile'),
}


def listed(binary, path):
    lister, kind, noun = LISTERS[binary]
    names, problem = lister(path)
    if names is None:
        return None, problem
    return [{'label': label(kind, noun, i, n), 'default': d} for i, (n, d) in enumerate(names, 1)], None


def clis():
    found = []
    for kind, binary in WAREHOUSE_CLIS:
        path = find_snow() if binary == 'snow' else shutil.which(binary)
        if path:
            found.append((kind, binary, path))

    def one(item):
        kind, binary, path = item
        entry = {'kind': kind, 'cli': binary, 'version': version_of(path, 20)}
        if binary in LISTERS:
            names, problem = listed(binary, path)
            entry['connections'] = names
            if problem:
                entry['note'] = problem
        return entry

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(one, found))


# ---------------------------------------------------------------- probe: dashboards

WAREHOUSE_SOURCES = (  # what a dashboard reads, from Tableau's connection classes or Power BI's M
    ('snowflake', re.compile(r"class=['\"]snowflake['\"]|Snowflake\.Databases", re.I)),
    ('databricks', re.compile(r"class=['\"]databricks['\"]|Databricks\.(?:Catalogs|Query|Contents)|DatabricksMultiCloud", re.I)),
    ('bigquery', re.compile(r"class=['\"]bigquery['\"]|GoogleBigQuery\.Database", re.I)),
    ('redshift', re.compile(r"class=['\"]redshift['\"]|AmazonRedshift\.Database", re.I)),
    ('postgres', re.compile(r"class=['\"]postgres['\"]|PostgreSQL\.Database", re.I)),
    ('other', re.compile(r"class=['\"](?:sqlserver|azure_sqldb|azure_sql_dw)['\"]|Sql\.Databases?\(", re.I)),
)
FILE_SOURCES = re.compile(r"class=['\"](?:hyper|excel-direct|textscan|dataengine)['\"]|Excel\.Workbook|Csv\.Document|"
                          r"#table\(|Table\.FromRows|Binary\.Decompress", re.I)
PUBLISHED = re.compile(r"class=['\"]sqlproxy['\"]|pbiazure://|AnalysisServices\.Database", re.I)


def decode(data):
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', errors='replace')
    if len(data) > 1 and data[1:2] == b'\x00':
        return data.decode('utf-16-le', errors='replace')
    return data.decode('utf-8-sig', errors='replace')


def entry(archive, name):
    """One archive entry's text, or None when it is missing or larger than READ_LIMIT."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > READ_LIMIT:
        return None
    with archive.open(info) as handle:
        return decode(handle.read(READ_LIMIT))


def inspect_dashboard(path):
    """Pages, measures, the warehouse it reads and whether its data is inside the file: read-only, size-bounded,
    by pattern rather than by parsing, so nothing in the file is expanded or run."""
    found = {'pages': None, 'measures': None, 'reads': None, 'inside_file': None}
    suffix, text, packed = path.suffix.lower(), None, False
    try:
        if path.stat().st_size > 2 * 1024 * 1024 * 1024:
            return dict(found, error='too large to open here')
        if suffix in ('.twb', '.tds', '.bim'):
            if path.stat().st_size <= READ_LIMIT:
                text = decode(path.read_bytes())
        elif suffix in ('.twbx', '.tdsx', '.pbit', '.pbix'):
            with zipfile.ZipFile(str(path)) as archive:
                names = archive.namelist()
                packed = any(n.lower().endswith('.hyper') for n in names)
                if suffix in ('.twbx', '.tdsx'):
                    inner = next((n for n in names if n.lower().endswith(('.twb', '.tds'))), None)
                    text = entry(archive, inner) if inner else None
                else:
                    layout = entry(archive, 'Report/Layout')
                    sections = (json_of(layout) or {}).get('sections') if layout else None
                    if isinstance(sections, list):
                        found['pages'] = len(sections)
                    text = entry(archive, 'DataModelSchema') or entry(archive, 'Connections') or ''
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return dict(found, error='could not be opened')
    if text is None:
        return found
    if suffix in ('.twb', '.twbx', '.tds', '.tdsx'):
        found['pages'] = len(re.findall(r'<worksheet\s+name=', text)) + len(re.findall(r'<dashboard\s+name=', text))
        found['measures'] = len(re.findall(r"<calculation\s+class=['\"]tableau['\"]\s+formula=", text))
    else:
        model = json_of(text)
        tables = ((model or {}).get('model') or {}).get('tables') if isinstance(model, dict) else None
        if isinstance(tables, list):
            found['measures'] = sum(len(t.get('measures') or []) for t in tables if isinstance(t, dict))
    reads = next((kind for kind, pattern in WAREHOUSE_SOURCES if pattern.search(text)), None)
    if reads:
        found['reads'], found['inside_file'] = reads, False
    elif PUBLISHED.search(text):
        found['reads'] = 'published'
    elif packed or FILE_SOURCES.search(text):
        found['inside_file'] = True
    return found


def scan(top, depth, limit=20000):
    """Files under `top`, at most `depth` folders down, skipping tool and dependency folders."""
    top = Path(top)
    seen = 0
    for here, dirs, files in os.walk(str(top)):
        level = len(Path(here).relative_to(top).parts)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')] if level < depth else []
        for name in files:
            seen += 1
            if seen > limit:
                return
            yield Path(here) / name


def dashboards_here(workspace):
    found = []
    for path in scan(workspace, 3):
        suffix = path.suffix.lower()
        if suffix in BI_FILES and len(found) < 50:
            item = {'file': str(path.relative_to(workspace)), 'tool': BI_FILES[suffix]}
            if len(found) < 10 and suffix in ('.twb', '.twbx', '.tds', '.tdsx', '.pbit', '.pbix', '.bim'):
                item.update({k: v for k, v in inspect_dashboard(path).items() if v is not None})
            found.append(item)
    return found


def dashboards_elsewhere():
    """Counts by file type in the three usual folders. Never names: they can carry customer names."""
    counts = {}
    for name in ('Desktop', 'Documents', 'Downloads'):
        where = Path.home() / name
        if not where.is_dir():
            continue
        try:
            for path in scan(where, 3):
                suffix = path.suffix.lower()
                if suffix in BI_FILES:
                    counts[suffix] = counts.get(suffix, 0) + 1
        except OSError:
            continue
    return counts


def probe(workspace):
    apps = applications()
    found_agents = agents(apps)
    with ThreadPoolExecutor(max_workers=4) as pool:
        servers = pool.submit(mcp_servers, found_agents)
        warehouse_clis = pool.submit(clis)
        here = pool.submit(dashboards_here, workspace)
        elsewhere = pool.submit(dashboards_elsewhere)
        who = pool.submit(identity)
        found = {
            'when': now(), 'harness_hint': harness_hint(),
            'sandbox': {'network_blocked': bool(os.environ.get('CODEX_SANDBOX_NETWORK_DISABLED'))},
            'machine': machine(), 'agents': found_agents, 'mcp': servers.result(), 'clis': warehouse_clis.result(),
            'bi': {'apps': sorted(a[:-4] if a.endswith('.app') else a for a in apps if BI_APPS.search(a)),
                   'here': here.result(), 'elsewhere': elsewhere.result()},
            'identity': who.result(),
        }
    return found


# ---------------------------------------------------------------- checks through a CLI

def resolve(names, kind, noun, wanted):
    """The real name behind a label, from a fresh listing. Real names are never saved or printed."""
    for i, (name, _) in enumerate(names or [], 1):
        if wanted in (name, label(kind, noun, i, name)):
            return name, label(kind, noun, i, name)
    raise ReadinessError('Nothing is labelled %r. Run `probe` again to see the labels.' % wanted)


def classify(stderr, timed_out=False):
    """status, reason: never the raw output, which can carry hosts, account numbers and login links."""
    text = stderr or ''
    if timed_out:
        return 'present', 'the sign-in did not finish in time'
    if os.environ.get('CODEX_SANDBOX_NETWORK_DISABLED') or re.search(
            r"configure handler 'file'|Operation not permitted|nodename nor servname|Name or service not known", text):
        return 'present', 'blocked by the sandbox'
    if re.search(r'\b(?:390303|390318|250001)\b|token (?:expired|invalid)|authentication failed|'
                 r'Incorrect username or password|auth login|invalid access token|\b401\b|unauthori[sz]ed|'
                 r'SSO session .* expired|Unable to locate credentials|do not currently have an active account|'
                 r'Reauthentication required|invalid_grant', text, re.I):
        return 'failed', 'sign-in needed or expired'
    if re.search(r'network policy|is not allowed to access', text, re.I):
        return 'failed', "blocked by the account's network policy"
    if re.search(r'does not exist or not authorized|insufficient privileges|\b403\b|permission|AccessDenied|'
                 r'Access Denied', text, re.I):
        return 'failed', 'not authorised: an admin grants access'
    return 'failed', scrub(text) or 'the check did not pass'


def failure(record, stderr, timed_out=False):
    status, reason = classify(stderr, timed_out)
    record['reason' if status == 'present' else 'error'] = reason
    return status, record


def first_row(data):
    if isinstance(data, list):
        for item in data:
            row = first_row(item)
            if row is not None:
                return row
    if isinstance(data, dict):
        return {str(k).lower(): v for k, v in data.items()}
    return None


def check_snowflake(args):
    snow = find_snow()
    if not snow:
        raise ReadinessError('The Snowflake CLI is not installed: record the warehouse as `none` with its kind.')
    names, _ = snow_names(snow)
    name, shown = resolve(names, 'snowflake', 'connection', args.connection or '')
    record = {'kind': 'snowflake', 'via': 'cli', 'connection': shown}
    sql = 'select current_user() as user_name, current_role() as role_name, current_warehouse() as warehouse_name'
    code, out, err = run([snow, 'sql', '--connection', name, '--format', 'JSON', '-q', sql], args.timeout)
    row = first_row(json_of(out)) if code == 0 else None
    if row is None:
        return failure(record, err, code is None and err == 'timeout')
    role = str(row.get('role_name') or '')
    record.update({'user': str(row.get('user_name') or ''), 'role': role,
                   'warehouse': str(row.get('warehouse_name') or ''),
                   'admin': 'yes' if role.upper() in ADMIN_ROLES else 'no'})
    record['_history'] = snowflake_history(snow, name, args.timeout)
    return 'verified', record


def check_databricks(args):
    binary = shutil.which('databricks')
    if not binary:
        raise ReadinessError('The Databricks CLI is not installed: record the warehouse as `none` with its kind.')
    names, _ = databricks_names(binary)
    name, shown = resolve(names, 'databricks', 'profile', args.profile or '')
    profile = [] if name == 'environment' else ['--profile', name]
    record = {'kind': 'databricks', 'via': 'cli', 'connection': shown}
    code, out, err = run([binary, 'current-user', 'me', '--output', 'json'] + profile, args.timeout)
    me = json_of(out) if code == 0 else None
    if not isinstance(me, dict):
        return failure(record, err, code is None and err == 'timeout')
    groups = [g.get('display') for g in me.get('groups') or [] if isinstance(g, dict)]
    record.update({'user': str(me.get('userName') or ''), 'admin': 'yes' if 'admins' in groups else 'no'})
    sql = 'select current_user() as user_name, current_catalog(), current_schema()'
    code, out, err = run([binary, 'experimental', 'aitools', 'tools', 'query', sql] + profile, 120)
    if code == 0 and out.strip():
        record['_history'] = databricks_history(binary, profile, None)
        return 'verified', record
    code, out, _ = run([binary, 'warehouses', 'list', '--output', 'json'] + profile, 60)
    rows = [w for w in (json_of(out) if code == 0 else None) or [] if isinstance(w, dict) and w.get('id')]
    if not rows:
        record['reason'] = 'signed in, no SQL warehouse: an admin grants one'
        return 'present', record
    chosen = next((w for w in rows if w.get('state') == 'RUNNING'), rows[0])
    body = json.dumps({'warehouse_id': chosen['id'], 'statement': 'select current_user()', 'wait_timeout': '30s'})
    code, out, err = run([binary, 'api', 'post', '/api/2.0/sql/statements', '--json', body] + profile, 90)
    state = (((json_of(out) or {}) if code == 0 else {}).get('status') or {}).get('state')
    if state not in ('SUCCEEDED', 'PENDING', 'RUNNING'):
        return failure(record, err or 'the check statement did not finish')
    record['warehouse'] = one_line(chosen.get('name') or '', 80)
    if state != 'SUCCEEDED':
        record['reason'] = 'access works; the SQL warehouse was starting'
        record['_history'] = ('not_checked', {'kind': 'databricks', 'reason': 'the SQL warehouse was starting'})
    else:
        record['_history'] = databricks_history(binary, profile, chosen['id'])
    return 'verified', record


def check_bigquery(args):
    gcloud = shutil.which('gcloud')
    if not gcloud:
        raise ReadinessError('The Google Cloud CLI is not installed: record the warehouse as `none` with its kind.')
    names, _ = gcloud_names(gcloud)
    name, shown = resolve(names, 'bigquery', 'configuration', args.configuration or '')
    env = dict(os.environ, CLOUDSDK_ACTIVE_CONFIG_NAME=name)
    record = {'kind': 'bigquery', 'via': 'cli', 'connection': shown}
    code, out, err = run([gcloud, 'auth', 'list', '--format=json'], 30, env)
    accounts = [a for a in (json_of(out) if code == 0 else None) or [] if isinstance(a, dict)]
    if not any(a.get('status') == 'ACTIVE' for a in accounts):
        record['error'] = 'sign-in needed or expired'
        return 'failed', record
    project = args.project
    if not project:
        code, out, _ = run([gcloud, 'config', 'get-value', 'project'], 30, env)
        project = out.strip() if code == 0 and out.strip() not in ('', '(unset)') else None
    if not project:
        record['reason'] = 'no project set'
        return 'present', record
    bq = shutil.which('bq')
    if not bq:
        record['reason'] = 'bq is not installed'
        return 'present', record
    code, out, err = run([bq, 'query', '--use_legacy_sql=false', '--format=json', '--project_id=' + project,
                          'select session_user() as user'], args.timeout, env)
    row = first_row(json_of(out)) if code == 0 else None
    if row is None:
        return failure(record, err or out, code is None and err == 'timeout')
    record['user'] = str(row.get('user') or '')
    record['_history'] = bigquery_history(bq, project, env, args.timeout)
    return 'verified', record


def check_redshift(args):
    aws = shutil.which('aws')
    if not aws:
        raise ReadinessError('The AWS CLI is not installed: record the warehouse as `none` with its kind.')
    names, _ = aws_names(aws)
    name, shown = resolve(names, 'aws', 'profile', args.profile or '')
    record = {'kind': 'redshift', 'via': 'cli', 'connection': shown}
    base = ['--profile', name]
    code, out, err = run([aws, 'sts', 'get-caller-identity', '--query', 'Arn', '--output', 'text'] + base, 60)
    if code != 0:
        return failure(record, err, code is None)
    arn = out.strip()
    # The role name only: the account number in the ARN is never kept.
    role = arn.split(':assumed-role/', 1)[1].split('/')[0] if ':assumed-role/' in arn else arn.rsplit('/', 1)[-1]
    record.update({'role': role, 'admin': 'yes' if 'admin' in role.lower() else 'no'})
    if not args.database or not (args.workgroup or args.cluster):
        record['reason'] = 'signed in; the workgroup or cluster and the database are needed to run the check'
        return 'present', record
    where = ['--workgroup-name', args.workgroup] if args.workgroup else ['--cluster-identifier', args.cluster]
    status, out, err = redshift_sql(aws, base + where, args.database, 'select current_user', args.timeout)
    if status is None:
        return failure(record, err)
    if status != 'FINISHED':
        record['error'] = 'the check statement failed' if status in ('FAILED', 'ABORTED') else 'the check statement did not finish'
        return 'failed', record
    record['user'] = one_line(out, 120)
    status, _, err = redshift_sql(aws, base + where, args.database, HISTORY_SQL['redshift'][0][1], args.timeout)
    record['_history'] = ('verified', {'kind': 'redshift', 'scope': 'your own'}) if status == 'FINISHED' else \
        ('failed', {'kind': 'redshift', 'error': classify(err)[1] if err else 'the statement was refused'})
    return 'verified', record


def redshift_sql(aws, options, database, sql, timeout):
    """One statement through the Redshift Data API: (status, first record as text, error), status None when it
    could not be sent."""
    code, out, err = run([aws, 'redshift-data', 'execute-statement', '--database', database, '--sql', sql,
                          '--query', 'Id', '--output', 'text'] + options, 60)
    if code != 0:
        return None, '', err
    statement, status, deadline = out.strip(), '', time.time() + min(timeout, 120)
    base = [o for o in options[:2]]  # --profile <name>
    while time.time() < deadline:
        code, out, err = run([aws, 'redshift-data', 'describe-statement', '--id', statement, '--query', 'Status',
                              '--output', 'text'] + base, 30)
        status = out.strip()
        if status in ('FINISHED', 'FAILED', 'ABORTED'):
            break
        time.sleep(2)
    if status != 'FINISHED':
        return status, '', err
    code, out, err = run([aws, 'redshift-data', 'get-statement-result', '--id', statement, '--query', 'Records[0]',
                          '--output', 'text'] + base, 30)
    return status, out if code == 0 else '', err


# Whether past queries can be read: everyone's first, then the person's own. Each returns a count from at
# most one row, never a statement's text or any data.
HISTORY_SQL = {
    'snowflake': (('everyone', 'select count(*) as n from (select 1 from snowflake.account_usage.query_history limit 1)'),
                  ('your own', 'select count(*) as n from (select 1 from table(information_schema.query_history_by_user('
                               'result_limit => 1)))')),
    'databricks': (('everyone', 'select count(*) as n from (select 1 from system.query.history limit 1)'),),
    'redshift': (('your own', 'select count(*) from (select 1 from sys_query_history limit 1)'),),
}


def snowflake_history(snow, name, timeout):
    error = ''
    for scope, sql in HISTORY_SQL['snowflake']:
        code, out, err = run([snow, 'sql', '--connection', name, '--format', 'JSON', '-q', sql], timeout)
        if code == 0 and first_row(json_of(out)) is not None:
            return 'verified', {'kind': 'snowflake', 'scope': scope}
        error = error or err
    return 'failed', {'kind': 'snowflake', 'error': classify(error)[1]}


def databricks_history(binary, profile, warehouse_id):
    sql = HISTORY_SQL['databricks'][0][1]
    if warehouse_id is None:
        code, out, err = run([binary, 'experimental', 'aitools', 'tools', 'query', sql] + profile, 120)
        ok, error = code == 0 and bool(out.strip()), err
    else:
        body = json.dumps({'warehouse_id': warehouse_id, 'statement': sql, 'wait_timeout': '30s'})
        code, out, err = run([binary, 'api', 'post', '/api/2.0/sql/statements', '--json', body] + profile, 90)
        status = ((json_of(out) or {}) if code == 0 else {}).get('status') or {}
        if status.get('state') in ('PENDING', 'RUNNING'):
            return 'not_checked', {'kind': 'databricks', 'reason': 'the SQL warehouse was still starting'}
        ok, error = status.get('state') == 'SUCCEEDED', (status.get('error') or {}).get('message') or err
    if ok:
        return 'verified', {'kind': 'databricks', 'scope': 'everyone'}
    return 'failed', {'kind': 'databricks', 'error': classify(error)[1]}


def bigquery_history(bq, project, env, timeout):
    """Jobs listed for everyone in the project (`-a`), else the person's own: metadata only, one job at most."""
    error = ''
    for scope, everyone in (('everyone', ['-a']), ('your own', [])):
        code, _, err = run([bq, 'ls', '-j'] + everyone + ['-n', '1', '--format=json', '--project_id=' + project],
                           timeout, env)
        if code == 0:
            return 'verified', {'kind': 'bigquery', 'scope': scope}
        error = error or err
    return 'failed', {'kind': 'bigquery', 'error': classify(error)[1]}


CHECKERS = {'snowflake': check_snowflake, 'databricks': check_databricks, 'bigquery': check_bigquery,
            'redshift': check_redshift}


# ---------------------------------------------------------------- records

def parse_sets(pairs):
    values = {}
    for pair in pairs or []:
        if '=' not in pair:
            raise ReadinessError('Each --set is KEY=VALUE; %r has no "=".' % pair)
        key, value = pair.split('=', 1)
        values[key.strip()] = value.strip()
    return values


def alias(state, kind, noun, name):
    """A stable label for an identifier-like name: the same server gets the same label on every record.
    Only a salted digest of the name is kept, never the name."""
    if not looks_like_identifier(name):
        return name
    salt = state.setdefault('salt', os.urandom(8).hex())
    digest = hashlib.sha256((salt + name).encode('utf-8')).hexdigest()[:16]
    aliases = state.setdefault('aliases', {})
    if digest not in aliases:
        prefix = '%s %s ' % (kind, noun)
        aliases[digest] = prefix + str(1 + sum(1 for v in aliases.values() if v.startswith(prefix)))
    return aliases[digest]


def clean(check, status, values, state):
    if check not in CHECKS:
        raise ReadinessError('Record one of: %s.' % ', '.join(CHECKS))
    statuses, keys = CHECKS[check]
    if status not in statuses:
        raise ReadinessError('%s takes the status %s.' % (check, ' or '.join(statuses)))
    if check == 'warehouse' and not values.get('kind'):
        raise ReadinessError('A warehouse record needs --set kind=<%s>.' % '|'.join(KINDS))
    fields = {}
    for key, value in values.items():
        if key not in keys:
            raise ReadinessError('%s takes the fields %s; not %r.' % (check, ', '.join(keys), key))
        if key in ENUMS:
            value = value.lower()
            if value not in ENUMS[key]:
                raise ReadinessError('%s is one of %s.' % (key, ', '.join(ENUMS[key])))
        elif key in NUMBERS:
            if not re.fullmatch(r'\d{1,6}', value):
                raise ReadinessError('%s is a whole number.' % key)
            value = int(value)
        elif key in MASKED:
            value = alias(state, values.get('kind', 'other').lower(), 'server' if key == 'server' else 'connection',
                          one_line(value, 128))
        elif key in SCRUBBED:
            value = scrub(value)
        else:
            value = one_line(value, 1000 if key in LONG else 300)
        fields[key] = value
    if check == 'warehouse' and str(fields.get('role') or '').upper() in ADMIN_ROLES:
        fields['admin'] = 'yes'
    return fields


def route_key(fields):
    return fields.get('kind'), fields.get('via'), fields.get('server') or fields.get('connection')


def remember(state, check, status, fields):
    """A later record of the same check (or the same warehouse route) adds to the earlier one."""
    if check == 'note':
        state['notes'].append(fields.get('text', ''))
        return
    if check == 'warehouse':
        earlier = next((w for w in state['warehouse'] if route_key(w['fields']) == route_key(fields)), None)
        others = [w for w in state['warehouse'] if w is not earlier]
        merged = dict((earlier or {}).get('fields') or {}, **fields)
        if status == 'verified':
            for stale in ('error',):
                merged.pop(stale, None)
            if 'reason' not in fields:
                merged.pop('reason', None)
        state['warehouse'] = others + [{'status': status, 'fields': merged, 'when': now()}]
        return
    earlier = state['checks'].get(check) or {}
    state['checks'][check] = {'status': status, 'fields': dict(earlier.get('fields') or {}, **fields), 'when': now()}


# ---------------------------------------------------------------- report

def field(state, check, key, default=None):
    return ((state['checks'].get(check) or {}).get('fields') or {}).get(key, default)


def status_of(state, check):
    return (state['checks'].get(check) or {}).get('status')


def harness_name(state):
    return field(state, 'harness', 'name') or (state.get('probe') or {}).get('harness_hint') or ''


def route_words(fields):
    via = fields.get('via')
    server = fields.get('server')
    if server and server.lower() == KINDS.get(fields.get('kind') or 'other', '').lower():
        server = None  # "Databricks through Atlan (Databricks)" says nothing twice
    if via == 'atlan':
        return 'through Atlan' + (' (%s)' % server if server else '')
    if via == 'host':
        return 'through this agent' + (' (%s)' % server if server else '')
    if via == 'cli':
        return 'through the CLI' + (' (%s)' % fields['connection'] if fields.get('connection') else '')
    return 'with no route yet'


def kinds_in_play(state):
    kinds = []
    for w in state['warehouse']:
        kind = w['fields'].get('kind')
        if kind and kind not in kinds:
            kinds.append(kind)
    if not kinds:
        for item in (state.get('probe') or {}).get('clis', []):
            if item.get('connections') and item['kind'] not in kinds:
                kinds.append(item['kind'])
    return kinds


def cli_entry(state, binary):
    return next((c for c in (state.get('probe') or {}).get('clis', []) if c.get('cli') == binary), None)


def install_command(tool, m):
    windows, brew, uv = m.get('windows'), m.get('brew'), m.get('uv')
    if tool == 'snow':
        return 'uv tool install snowflake-cli' if uv else 'brew install --cask snowflake-cli' if brew else 'pip install snowflake-cli'
    if tool == 'databricks':
        return ('winget install Databricks.DatabricksCLI' if windows else 'brew tap databricks/tap && brew install databricks'
                if brew else 'Follow https://docs.databricks.com/en/dev-tools/cli/install.html')
    if tool == 'gcloud':
        return ('Run the installer from https://cloud.google.com/sdk' if windows else 'brew install --cask google-cloud-sdk'
                if brew else 'Install the Google Cloud CLI package for your system: https://cloud.google.com/sdk')
    if tool == 'aws':
        return 'winget install Amazon.AWSCLI' if windows else 'brew install awscli' if brew else \
            'Follow https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html'
    return None


def sign_in_steps(harness, server):
    h = harness.lower()
    if 'claude' in h:
        return '/mcp, then %s, then Authenticate' % server
    if 'codex' in h:
        return 'codex mcp login %s' % server
    if 'cursor' in h:
        return 'Cursor Settings, then MCP, then %s, then Sign in' % server
    return None


def suggest(state):
    """What the person (or an admin) can do, most useful first. Priority 1 unblocks the build, 2 speeds it up,
    3 is worth doing. Nothing here is run by the check itself."""
    probe = state.get('probe') or {}
    m = probe.get('machine') or {}
    harness = harness_name(state)
    out = []

    def add(priority, who, do, why, command=None, key=None):
        if any(s['key'] == (key or do) for s in out):
            return
        out.append({'priority': priority, 'who': who, 'do': do, 'why': why, 'command': command, 'key': key or do})

    if probe and not m.get('plugin_python'):
        add(1, 'You', 'Install Python 3.12',
            'Atlan Analytics needs Python 3.11 or later, and this machine has %s.' % (m.get('python') or 'an older one'),
            'uv python install 3.12' if m.get('uv') else 'winget install Python.Python.3.12' if m.get('windows')
            else 'brew install python@3.12' if m.get('brew') else None)

    atlan = status_of(state, 'atlan')
    if atlan == 'none':
        add(2, 'You', 'Add the Atlan Trident plugin (`atlanai`) to %s' % (harness or 'this agent'),
            'It is the Atlan connection: saving metrics, sharing your agent and reaching the warehouses your '
            'organization registered in Atlan.')
    elif atlan == 'failed':
        add(2, 'You', 'Sign in to Atlan', 'Saving metrics and sharing your agent with your team need it.',
            sign_in_steps(harness, 'atlan'))

    routes = state['warehouse']
    verified = [w for w in routes if w['status'] == 'verified']
    verified_kinds = {w['fields'].get('kind') for w in verified}
    for w in verified:
        f = w['fields']
        if f.get('admin') == 'yes':
            add(1, 'You, or your warehouse admin', 'Use a read-only role for the accelerator',
                '%s can change data, and the agent only needs to read it.' % (f.get('role') or 'An administrator role'),
                key='admin')
        if f.get('kind') == 'snowflake' and f.get('via') == 'cli' and not f.get('warehouse'):
            add(2, 'You', 'Set a default warehouse on the Snowflake CLI connection',
                'Statements that read tables need one, and without it each of them fails.',
                'snow connection add --no-interactive --connection-name <name> --account <org-account> '
                '--user <you> --authenticator externalbrowser --role <read-only role> --warehouse <warehouse> --default')

    for item in probe.get('mcp', []):
        for server in item.get('servers', []):
            if server['class'] not in ('BI', 'Atlan') and server['status'] in ('needs sign-in', 'failed'):
                add(1, 'You', 'Sign in to %s in %s' % (server['label'], item['agent']),
                    'It is set up there but not connected, so the agent cannot run SQL through it.',
                    sign_in_steps(item['agent'], server['label']))

    for kind in kinds_in_play(state) or ([None] if probe else []):
        if kind in verified_kinds:
            continue
        name = KINDS.get(kind or 'other')
        mine = [w for w in routes if w['fields'].get('kind') == kind]
        cli_failed = any(w['fields'].get('via') == 'cli' and w['status'] == 'failed' for w in mine)
        reasons = ' '.join(str(w['fields'].get('reason') or '') for w in mine)
        through_atlan = [w for w in mine if w['fields'].get('via') == 'atlan']
        if any(w['status'] == 'present' for w in through_atlan):
            add(1, 'You', 'Sign in to %s in Atlan Trident' % name,
                'It is already registered in Atlan for your organization; each person signs in once with their own account.')
        elif any(w['status'] == 'failed' for w in through_atlan):
            failed = next(w for w in through_atlan if w['status'] == 'failed')['fields']
            add(1, 'You, or your warehouse admin', 'Fix your access to %s through Atlan' % name,
                'The check statement was refused%s.' % (': ' + failed['error'] if failed.get('error') else ''))
        elif atlan == 'verified' and kind in STORE_KINDS:
            add(1, 'Your Atlan admin', 'Add %s from the Atlan Store' % name,
                'Then everyone reaches it through Atlan, signed in as themselves, and the agent you build uses the same route.')
        if 'sandbox' in reasons:
            add(1, 'You', 'Allow the warehouse check to run outside the sandbox',
                'The sandbox blocks the network sign-in. Approve that one command when asked, or run it in your own terminal.')
        if kind == 'snowflake':
            snow = cli_entry(state, 'snow')
            if not snow:
                add(1, 'You', 'Install the Snowflake CLI',
                    'It is the quickest route from this machine: it signs in through your browser and runs read-only checks.',
                    install_command('snow', m), key='snow-install')
            if not snow or not snow.get('connections'):
                add(1, 'You', 'Add a Snowflake CLI connection that signs in through your browser',
                    'No password is stored; the browser sign-in is used each time.',
                    'snow connection add --no-interactive --connection-name <name> --account <org-account> --user <you> '
                    '--authenticator externalbrowser --role <read-only role> --warehouse <warehouse> --default')
            elif cli_failed:
                add(1, 'You', 'Sign in again through the Snowflake CLI',
                    'The last check found the sign-in missing or expired.', 'snow connection test -c <your connection>')
            else:
                add(1, 'You', 'Test your Snowflake CLI connection',
                    'The CLI is set up here, but no check has passed through it yet.', 'snow connection test -c <your connection>')
        elif kind == 'databricks':
            dbx = cli_entry(state, 'databricks')
            if not dbx:
                add(1, 'You', 'Install the Databricks CLI', 'It is the quickest route from this machine: it signs in '
                    'through your browser.', install_command('databricks', m), key='databricks-install')
            if not dbx or not dbx.get('connections') or cli_failed:
                add(1, 'You', 'Sign in with the Databricks CLI', 'It saves a profile that signs in through your browser.',
                    'databricks auth login --host <workspace URL>')
            elif 'no SQL warehouse' in reasons:
                add(1, 'Your Databricks admin', 'Grant you access to a SQL warehouse',
                    'You are signed in, but no SQL warehouse is visible to you, so no statement can run.')
            else:
                add(1, 'You', 'Test your Databricks CLI profile', 'The CLI is set up here, but no check has passed through it yet.',
                    'databricks current-user me --profile <your profile>')
        elif kind == 'bigquery':
            if not cli_entry(state, 'gcloud'):
                add(1, 'You', 'Install the Google Cloud CLI', 'It signs in through your browser and brings `bq`, which runs '
                    'read-only queries.', install_command('gcloud', m), key='gcloud-install')
            elif cli_failed:
                add(1, 'You', 'Sign in with the Google Cloud CLI', 'The last check found no signed-in account.',
                    'gcloud auth login --update-adc')
            elif 'no project' in reasons:
                add(1, 'You', 'Set the project that holds your data', '`bq` needs one to run a query.',
                    'gcloud config set project <project-id>')
            elif 'bq is not installed' in reasons:
                add(1, 'You', 'Install `bq`', 'It comes with the Google Cloud CLI as a component.', 'gcloud components install bq')
            else:
                add(1, 'You', 'Test your BigQuery sign-in', 'No check has passed through the CLI yet.',
                    "bq query --use_legacy_sql=false 'select session_user()'")
        elif kind == 'redshift':
            aws = cli_entry(state, 'aws')
            if not aws:
                add(1, 'You', 'Install the AWS CLI', 'It runs read-only statements through the Redshift Data API with your '
                    'AWS sign-in, so no database password is needed.', install_command('aws', m), key='aws-install')
            if not aws or not aws.get('connections'):
                add(1, 'You', 'Create an AWS sign-in profile', 'It asks for your SSO start URL and region, so run it in your '
                    'own terminal.', 'aws configure sso --profile <name>')
            elif cli_failed:
                add(1, 'You', 'Sign in again through the AWS CLI', 'The last check found the sign-in expired.',
                    'aws sso login --profile <your profile>')
            elif 'workgroup' in reasons:
                add(2, 'You', 'Have your Redshift workgroup or cluster name and database ready',
                    'You are signed in; the check needs them to run one statement.')
            else:
                add(1, 'You', 'Test your AWS sign-in', 'No check has passed through the CLI yet.',
                    'aws sts get-caller-identity --profile <your profile>')
        if kind is None:
            add(1, 'You', 'Connect your warehouse',
                'Nothing on this machine reaches a warehouse yet. Say which one your team uses and the check names the route.')
        elif not any(s['priority'] == 1 and name in s['do'] for s in out):
            add(1, 'You', 'Connect %s' % name,
                'No route reaches it yet. Ask your Atlan admin to register it in Atlan Trident, or add its MCP '
                'server to this agent, then run the readiness check again.')

    history, scope = status_of(state, 'history'), field(state, 'history', 'scope')
    if history == 'failed' or (history == 'verified' and scope == 'your own'):
        view = admin_sql(field(state, 'history', 'kind'))
        add(2, 'Your warehouse admin', "Create one view of your team's past queries for this work" if view
            else "Give you read access to your team's query history",
            ("Only your own past queries can be read. " if history == 'verified' else "Past queries can't be read yet. ")
            + "Your team's show which tables and numbers it really uses." + (
                ' The view lets the build read them without user names; the script is at the end of this report.'
                if view else ''),
            key='history-view')

    # The build runs without a CLI, but past queries come back much faster through one.
    for kind, binary in (('snowflake', 'snow'), ('databricks', 'databricks')):
        if kind in verified_kinds and not any(w['fields'].get('via') == 'cli' for w in verified
                                              if w['fields'].get('kind') == kind) and not cli_entry(state, binary):
            add(2, 'You', 'Install the %s CLI' % KINDS[kind],
                "Your team's past queries (often thousands of rows) then go straight to a file instead of through the "
                'chat, so that step of the build is faster, and you keep a second route when the other is down.',
                install_command(binary, m), key=binary + '-install')

    bi = status_of(state, 'bi')
    if field(state, 'bi', 'inside_file') == 'yes':
        add(1, 'You', 'Name the warehouse table behind your dashboard',
            "Its data is inside the file, so the build can't run its numbers on your warehouse.")
    if not field(state, 'bi', 'tool') or not field(state, 'bi', 'dashboard'):
        add(1, 'You', 'Pick your BI tool and the one dashboard to start from',
            'Week one starts from it: its tiles become the first metrics and its numbers the first checks.')
    elif bi in ('answered', 'not_checked') and not (probe.get('bi') or {}).get('here'):
        elsewhere = sum(((probe.get('bi') or {}).get('elsewhere') or {}).values())
        add(2, 'You', "Put the dashboard's file in this folder, or have its link ready",
            'The build reads its measures and filters directly.' + (
                ' This machine has %d dashboard file%s in Desktop, Documents or Downloads.' % (
                    elsewhere, '' if elsewhere == 1 else 's') if elsewhere else ''))
    elif bi == 'failed':
        add(2, 'You', 'Check you can open the dashboard in %s' % (field(state, 'bi', 'tool') or 'its BI tool'),
            'The build reads it with your own access.')

    if status_of(state, 'use-case') in (None, 'unanswered'):
        add(3, 'You', 'Write down the one question your team asks most often', 'The build starts from it.')
    if 'claude' in harness.lower() and field(state, 'harness', 'task_list') == 'no':
        add(3, 'You', 'Turn on the task list in Claude Code',
            'The build shows its progress there. Start a new session after the change.',
            'Add "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1" under "env" in ~/.claude/settings.json')
    out.sort(key=lambda s: s['priority'])
    return out


def verdict(state):
    """The accelerator's verdict: a coding agent, a verified warehouse (an administrator role is still ready,
    with the read-only step), and a BI tool and starting dashboard named. Atlan Analytics also needs its
    Python, and a starting dashboard whose data is not packed inside the file."""
    probe = state.get('probe') or {}
    reasons = []
    if not (harness_name(state) or probe.get('agents')):
        reasons.append('no coding agent')
    if not any(w['status'] == 'verified' for w in state['warehouse']):
        reasons.append('no warehouse route works yet')
    if not field(state, 'bi', 'tool') or not field(state, 'bi', 'dashboard'):
        reasons.append('no BI tool and starting dashboard named')
    if probe and not (probe.get('machine') or {}).get('plugin_python'):
        reasons.append('Python 3.11 or later is missing')
    if field(state, 'bi', 'inside_file') == 'yes':
        reasons.append("the dashboard's data is inside the file")
    return ('ready' if not reasons else 'needs_attention'), reasons


WORDS = {'ready': 'Ready', 'needs_attention': 'Needs attention'}


def cell(value):
    return one_line(value, 200).replace('|', '\\|') if value not in (None, '') else ''


def capital(text):
    return text[:1].upper() + text[1:]


def plural(count, noun):
    return '%s %s%s' % (count, noun, '' if count == 1 else 's')


def dashboard_words(item):
    """`reports/pipeline.pbix (Power BI: 6 pages, 40 measures, reads Snowflake)`."""
    parts = [item['tool']]
    if item.get('pages') is not None:
        parts.append(plural(item['pages'], 'sheet' if item['tool'] == 'Tableau' else 'page'))
    if item.get('measures') is not None:
        parts.append(plural(item['measures'], 'calculation' if item['tool'] == 'Tableau' else 'measure'))
    if item.get('reads') == 'published':
        parts.append('reads a published data source')
    elif item.get('reads'):
        parts.append('reads ' + KINDS.get(item['reads'], item['reads']))
    elif item.get('inside_file'):
        parts.append('data inside the file')
    if item.get('error'):
        parts.append(item['error'])
    return '%s (%s)' % (item['file'], parts[0] + (': ' + ', '.join(parts[1:]) if parts[1:] else ''))


def servers_words(item):
    if item.get('note'):
        return '%s: %s' % (item['agent'], item['note'])
    named = ', '.join('%s (%s, %s)' % (s['label'], s['class'], s['status']) for s in item.get('servers', []))
    others = item.get('others', 0)
    tail = ('and %d other%s' % (others, '' if others == 1 else 's')) if others else ''
    return '%s: %s' % (item['agent'], ', '.join(x for x in (named, tail) if x) or 'none')


def start_prompt(state):
    dashboard, tool = field(state, 'bi', 'dashboard'), field(state, 'bi', 'tool')
    if dashboard:
        return ('Build an agent from my %s dashboard "%s"' % (tool, dashboard)) if tool else \
            'Build an agent from my dashboard "%s"' % dashboard
    kinds = [w['fields'].get('kind') for w in state['warehouse'] if w['status'] == 'verified']
    where = KINDS.get(kinds[0]) if kinds else 'my'
    use = field(state, 'use-case', 'text')
    return 'Set up Atlan Analytics on %s data%s' % (where, (' for: ' + one_line(use, 120)) if use else '')


def render(state, plugin_version):
    probe = state.get('probe') or {}
    m = probe.get('machine') or {}
    value, reasons = verdict(state)
    tips = suggest(state)
    harness = harness_name(state)
    version = field(state, 'harness', 'version')
    running = (harness + (' ' + version if version else '')) or 'unknown'
    lines = ['# Atlan Analytics readiness', '',
             '%s · %s · atlanai-analytics %s' % (now(), running, plugin_version or 'unknown'), '',
             '**%s.**%s' % (WORDS[value], (' ' + capital('; '.join(reasons)) + '.') if reasons else ''), '']
    if tips:
        lines += ['Next: %s.' % tips[0]['do'], '']

    order = ['verified', 'failed', 'present', 'none', 'not_checked']
    routes = sorted(state['warehouse'], key=lambda w: order.index(w['status']))
    if routes:
        f = routes[0]['fields']
        detail = '%s %s' % (KINDS.get(f.get('kind') or 'other'), route_words(f))
        if f.get('role'):
            detail += ', role %s' % f['role']
        if f.get('error') or f.get('reason'):
            detail += ': ' + (f.get('error') or f.get('reason'))
        warehouse = ('verified, admin role' if routes[0]['status'] == 'verified' and f.get('admin') == 'yes'
                     else routes[0]['status'], detail)
    else:
        warehouse = ('none', 'no route found')
    others = [a['name'] for a in probe.get('agents', []) if a['name'] != harness]
    bi_detail = ', '.join(x for x in (field(state, 'bi', 'tool'), field(state, 'bi', 'dashboard') and
                                      '"%s"' % field(state, 'bi', 'dashboard')) if x) or 'not named'
    if field(state, 'bi', 'pages') is not None:
        bi_detail += ', %s' % plural(field(state, 'bi', 'pages'), 'page')
    if field(state, 'bi', 'measures') is not None:
        bi_detail += ', %s' % plural(field(state, 'bi', 'measures'), 'measure')
    bi_status = 'file opened' if status_of(state, 'bi') == 'verified' else 'access not checked'
    rows = [
        ('Use case', status_of(state, 'use-case') or 'unanswered', field(state, 'use-case', 'text') or ''),
        ('Coding agent', 'ok' if harness or others else 'none', running + ('; also installed: ' + ', '.join(others) if others else '')),
        ('Atlan Trident', status_of(state, 'atlan') or 'not_checked',
         plural(field(state, 'atlan', 'workspaces'), 'workspace') if field(state, 'atlan', 'workspaces') is not None
         else field(state, 'atlan', 'error') or ''),
        ('Warehouse', warehouse[0], warehouse[1]),
        ('Past queries', status_of(state, 'history') or 'not_checked', history_words(state)),
        ('BI', bi_status, bi_detail),
        ('Python', 'ok' if m.get('plugin_python') else 'missing' if probe else 'not_checked',
         ('%s (`%s`)' % (m.get('plugin_python_version') or '3.11+', m['plugin_python'])) if m.get('plugin_python')
         else 'Atlan Analytics needs 3.11 or later'),
    ]
    lines += ['## Summary', '', '| Area | Status | Detail |', '|---|---|---|']
    lines += ['| %s | %s | %s |' % (a, s.replace('_', ' '), cell(d)) for a, s, d in rows]

    lines += ['', '## To speed up your build', '']
    if tips:
        for i, tip in enumerate(tips, 1):
            lines.append('%d. **%s** (%s). %s' % (i, tip['do'], tip['who'], tip['why']))
            if tip['command']:
                lines += ['', '   ```', '   ' + tip['command'], '   ```', '']
    else:
        lines.append('Nothing: everything the build needs is in place.')

    lines += ['', '## Participant', '']
    lines.append('- Name: %s' % (field(state, 'participant', 'name') or 'unanswered'))
    lines.append('- Work email: %s' % (field(state, 'participant', 'email') or 'unanswered'))
    lines.append('- Identified from: %s' % (field(state, 'participant', 'source') or 'unanswered'))
    lines += ['', '## Use case', '', field(state, 'use-case', 'text') or 'Not sure yet.']
    lines += ['', '## Coding agents', '', '- Running in: %s' % running,
              '- Also installed: %s' % (', '.join(('%s %s' % (a['name'], a['version'] or '')).strip()
                                                  for a in probe.get('agents', []) if a['name'] != harness) or 'none')]
    servers = probe.get('mcp', [])
    if servers:
        lines.append('- MCP servers by agent: ' + '; '.join(servers_words(item) for item in servers))
    lines += ['', '## Warehouse', '']
    for w in routes:
        f = w['fields']
        extra = ', '.join(x for x in (f.get('user') and 'as ' + f['user'], f.get('role') and 'role ' + f['role'],
                                      f.get('warehouse') and 'warehouse ' + f['warehouse'],
                                      f.get('admin') == 'yes' and 'administrator', f.get('error'), f.get('reason')) if x)
        lines.append('- %s %s: %s%s' % (KINDS.get(f.get('kind') or 'other'), route_words(f), w['status'].replace('_', ' '),
                                        ' (' + extra + ')' if extra else ''))
    for item in probe.get('clis', []):
        names = item.get('connections')
        shown = ', '.join(n['label'] + (' (default)' if n.get('default') else '') for n in names) if names else \
            item.get('note') or ('present' if 'connections' not in item else 'none')
        lines.append('- %s CLI `%s` %s: %s' % (KINDS.get(item['kind'], item['kind']), item['cli'], item.get('version') or '', shown))
    if not routes and not probe.get('clis'):
        lines.append('- No route found.')
    if status_of(state, 'history'):
        lines.append('- Past queries: %s' % (status_of(state, 'history').replace('_', ' ') +
                                            (', ' + history_words(state) if history_words(state) else '')))
    bi = probe.get('bi') or {}
    lines += ['', '## BI', '',
              '- Team\'s BI tool: %s' % (field(state, 'bi', 'tool') or 'unanswered'),
              '- Starting dashboard: %s' % (field(state, 'bi', 'dashboard') or 'unanswered'),
              '- Tables for the use case: %s' % (field(state, 'bi', 'tables') or {'yes': 'named', 'roughly': 'a rough area',
                                                                                   'no': 'not sure'}.get(field(state, 'bi', 'tables_known'), 'unanswered')),
              '- Dashboard files in this folder: %s' % (', '.join(dashboard_words(b) for b in bi.get('here', [])[:10]) or 'none'),
              '- Dashboard files in Desktop, Documents and Downloads: %s' % (
                  ', '.join('%d %s' % (n, ext) for ext, n in sorted((bi.get('elsewhere') or {}).items())) or 'none'),
              '- BI apps: %s' % (', '.join(bi.get('apps', [])) or 'none'),
              '- BI access: %s' % ('the dashboard file was opened here' if status_of(state, 'bi') == 'verified' else 'not checked')]
    lines += ['', '## Machine', '', '- OS: %s, %s' % (m.get('os') or 'unknown', m.get('arch') or ''),
              '- Python: %s here; Atlan Analytics will use %s' % (
                  m.get('python') or '?', ('%s (%s)' % (m['plugin_python'], m.get('plugin_python_version')))
                  if m.get('plugin_python') else 'none found')]
    lines += ['', '## Notes', ''] + (['- ' + n for n in state['notes']] or ['None.'])
    view = admin_sql(field(state, 'history', 'kind')) if any(t['key'] == 'history-view' for t in tips) else None
    if view:
        lines += ['', '## For your warehouse admin', '',
                  'To let the build read your team\'s past queries without user names, create this view once, '
                  'narrowing the `where` to the databases the team works in. Review it before running it.', '',
                  '```sql', view, '```']
    lines += ['', '## Start the build', '', 'Paste this into %s:' % (harness or 'your coding agent'), '',
              '```text', start_prompt(state), '```', '',
              '---', 'Names only: no passwords, tokens, hostnames, account identifiers or data rows. Connection '
              'names that look like account identifiers are replaced with labels.', '']
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)), value, reasons, tips


def history_words(state):
    scope = field(state, 'history', 'scope')
    if scope:
        return {'everyone': "everyone's queries", 'your own': 'your own queries only'}[scope]
    return field(state, 'history', 'error') or field(state, 'history', 'reason') or ''


def admin_sql(kind):
    """The view an admin can create once, read from this skill's query-history.md so the report and
    the reference never drift."""
    heading = {'snowflake': '**Snowflake**', 'databricks': '**Databricks**'}.get(kind)
    try:
        section = (SKILL / 'references' / 'warehouses' / 'query-history.md').read_text(encoding='utf-8').split(
            '## One-time setup an admin can run', 1)
    except OSError:
        return None
    if not heading or len(section) < 2 or heading not in section[1]:
        return None
    match = re.search(r'```sql\n(.*?)```', section[1].split(heading, 1)[1], re.S)
    return match.group(1).strip() if match else None


def plugin_version():
    for path in (SKILL.parents[1] / '.claude-plugin' / 'plugin.json', SKILL.parents[1] / 'plugin.json'):
        data = json_of(path.read_text(encoding='utf-8')) if path.is_file() else None
        if isinstance(data, dict) and data.get('version'):
            return str(data['version'])
    return None


# ---------------------------------------------------------------- command line

def summary_of(state):
    probe = state.get('probe') or {}
    return {'agents': [a['name'] for a in probe.get('agents', [])],
            'harness_hint': probe.get('harness_hint'),
            'mcp': probe.get('mcp', []),
            'clis': [{'kind': c['kind'], 'cli': c['cli'], 'connections': [n['label'] for n in c.get('connections') or []],
                      **({'note': c['note']} if c.get('note') else {})} for c in probe.get('clis', [])],
            'dashboards_here': (probe.get('bi') or {}).get('here', [])[:10],
            'dashboards_elsewhere': (probe.get('bi') or {}).get('elsewhere', {}),
            'bi_apps': (probe.get('bi') or {}).get('apps', []),
            'identity': probe.get('identity', {}),
            'plugin_python': (probe.get('machine') or {}).get('plugin_python'),
            'network_blocked': (probe.get('sandbox') or {}).get('network_blocked'),
            'recorded': sorted(state['checks']) + (['warehouse x%d' % len(state['warehouse'])] if state['warehouse'] else [])}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--workspace', default='.', help='The project folder; state and the report go in its .atlan-analytics/readiness/')
    sub = p.add_subparsers(dest='command')
    pr = sub.add_parser('probe', help='What this machine has. Read-only, names only')
    pr.add_argument('--fresh', action='store_true', help='Start a new assessment: drop what was recorded before')
    ch = sub.add_parser('check', help='Run one read-only check through a CLI connection or profile, after a yes')
    ch.add_argument('warehouse', choices=tuple(CHECKERS))
    ch.add_argument('--connection', help='Snowflake: the connection label from probe')
    ch.add_argument('--profile', help='Databricks or Redshift (AWS): the profile label from probe')
    ch.add_argument('--configuration', help='BigQuery: the gcloud configuration label from probe')
    ch.add_argument('--project', help='BigQuery: the project id, when the configuration sets none')
    ch.add_argument('--database', help='Redshift: the database')
    ch.add_argument('--workgroup', help='Redshift Serverless: the workgroup name')
    ch.add_argument('--cluster', help='Redshift provisioned: the cluster name')
    ch.add_argument('--timeout', type=int, default=300, help='Seconds, enough for a browser sign-in')
    rec = sub.add_parser('record', help='Save what a check through a tool, or an answer, found')
    rec.add_argument('check', choices=tuple(CHECKS))
    rec.add_argument('--status', required=True, choices=STATUSES)
    rec.add_argument('--set', action='append', default=[], metavar='KEY=VALUE')
    sub.add_parser('report', help='Write the report and print the verdict and suggestions')
    args = p.parse_args(argv)
    if not args.command:
        p.print_help()
        return 2
    try:
        workspace = Path(args.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise ReadinessError('%s is not a folder.' % args.workspace)
        if args.command == 'probe':
            state = empty() if args.fresh else load(workspace)
            state['probe'] = probe(workspace)
            save(workspace, state)
            print(json.dumps({'status': 'ok', **summary_of(state)}, ensure_ascii=False))
            return 0
        state = load(workspace)
        if args.command == 'check':
            args.timeout = max(10, min(args.timeout, 600))
            status, fields = CHECKERS[args.warehouse](args)
            history = fields.pop('_history', None)
            fields = clean('warehouse', status, {k: str(v) for k, v in fields.items() if v not in (None, '')}, state)
            remember(state, 'warehouse', status, fields)
            shown = {'status': status, **fields}
            if history:
                past = clean('history', history[0], {k: str(v) for k, v in history[1].items() if v}, state)
                remember(state, 'history', history[0], past)
                shown['past_queries'] = dict(past, status=history[0])
            save(workspace, state)
            print(json.dumps(shown, ensure_ascii=False))
            return 0
        if args.command == 'record':
            fields = clean(args.check, args.status, parse_sets(args.set), state)
            remember(state, args.check, args.status, fields)
            save(workspace, state)
            print(json.dumps({'status': 'ok', 'recorded': args.check, 'as': args.status, 'fields': fields},
                             ensure_ascii=False))
            return 0
        text, value, reasons, tips = render(state, plugin_version())
        path = folder(workspace) / 'report.md'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write(path, text)
        print(json.dumps({'status': 'ok', 'verdict': value, 'words': WORDS[value], 'why': reasons, 'report': str(path),
                          'next': tips[0] if tips else None, 'suggestions': len(tips),
                          'start_prompt': start_prompt(state)}, ensure_ascii=False))
        return 0
    except ReadinessError as error:
        print(json.dumps({'status': 'blocked', 'message': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
