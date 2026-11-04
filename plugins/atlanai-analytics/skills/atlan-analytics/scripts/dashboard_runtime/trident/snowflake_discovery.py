"""Discover non-secret CLI metadata; the official CLI owns browser login and storage."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .snowflake_cli import CLIError, ConnectionPaused, command, discover


FIELDS = ('account', 'user', 'role', 'warehouse', 'database', 'schema', 'authenticator')


def account_key(value):
    return str(value).lower().removesuffix('.snowflakecomputing.com')


def profile_home(config):
    key = hashlib.sha256(json.dumps([account_key(config['account']), config['role']],
                                   separators=(',', ':')).encode()).hexdigest()
    path = Path.home() / '.atlan' / 'analytics' / 'snowflake' / key
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise CLIError('Use an ordinary private Analytics connection directory.')
    return path


def environment(home=None):
    if home is None:
        return None
    # Isolate the CLI's config, connections file and default. Never inspect values.
    connection_keys = {'SNOWFLAKE_' + key for key in (
        'ACCOUNT','USER','PASSWORD','AUTHENTICATOR','HOST','PORT','PROTOCOL','TOKEN','TOKEN_FILE_PATH',
        'PRIVATE_KEY','PRIVATE_KEY_FILE','PRIVATE_KEY_PASSPHRASE','ROLE','WAREHOUSE','DATABASE','SCHEMA',
        'CONNECTION_NAME','DEFAULT_CONNECTION_NAME','OAUTH_CLIENT_ID','OAUTH_CLIENT_SECRET',
        'OAUTH_AUTHORIZATION_URL','OAUTH_TOKEN_REQUEST_URL','OAUTH_REDIRECT_URI','OAUTH_SCOPE')}
    env = {k: v for k, v in os.environ.items()
           if k not in connection_keys and not k.startswith('SNOWFLAKE_CONNECTIONS_')}
    env['SNOWFLAKE_HOME'] = str(home)
    return env


def prefix(home=None):
    args = [discover()]
    if home is not None:
        args += ['--config-file', str(home / 'config.toml')]
    return args


def run_cli(args, home=None, timeout=30):
    try:
        result = subprocess.run(prefix(home) + args, capture_output=True, text=True,
                                timeout=timeout, env=environment(home), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise ConnectionPaused('Snowflake discovery or browser sign-in did not finish. Pause setup; do not restart login for every metric.') from None
    if result.returncode:
        raise CLIError('Snowflake CLI discovery or sign-in is unavailable. Check CLI compatibility or complete browser sign-in; raw output was withheld.')
    return result.stdout


def connections(home=None):
    try:
        args = ['connection', 'list'] + (['--all'] if home is None else []) + ['--format', 'JSON']
        rows = json.loads(run_cli(args, home))
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError()
        safe = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError()
            name = row.get('connection_name', row.get('name'))
            values = row.get('parameters', row)
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) or not isinstance(values, dict):
                continue
            # CLI output can contain masked secrets or unknown future fields. Drop them all.
            safe.append({'connection': name, 'is_default': row.get('is_default') is True,
                         **{k: v for k in FIELDS if isinstance((v := values.get(k)), str) and 0 < len(v) <= 256}})
        return safe
    except (ValueError, TypeError):
        raise CLIError('Snowflake CLI returned unsupported connection metadata; no raw configuration was exposed.') from None


def matches(candidate, requested):
    return (account_key(candidate.get('account', '')) == account_key(requested.get('account', ''))
            and all(not candidate.get(k) or candidate[k] == requested[k]
                    for k in ('role', 'warehouse') if requested.get(k)))


def choose(rows, config, managed=False):
    candidates = [row for row in rows if matches(row, config)]
    defaults = [row for row in candidates if row.get('is_default')]
    if len(defaults) == 1:
        candidates = defaults
    if len(candidates) > 1:
        names = ', '.join(sorted(row['connection'] for row in candidates)[:8])
        raise CLIError('Multiple matching Snowflake connections: ' + names + '. Choose a connection profile, not a password or token.')
    if not candidates:
        return None
    row = candidates[0]
    result = {**config, 'connection': row['connection']}
    # A named profile supplies its own identity and authentication; do not override them.
    result.pop('user', None)
    result.pop('authenticator', None)
    if managed:
        result['_managed_profile'] = True
    return result


def resolve(config, previous=None, before_login=None):
    config = dict(config)
    if previous and all(previous.get(k) == v for k, v in config.items()):
        return dict(previous)
    if config.get('connection') or config.get('user'):
        command(config)  # Validate scope without opening a connection.
        return config
    # Validate dashboard-derived scope before looking up profiles or invoking OAuth.
    command({**config, 'user': 'discovery_validation'})
    selected = choose(connections(), config)
    if selected:
        return selected
    # Some CLI versions omit environment-only identities from `connection list --all`.
    if account_key(os.environ.get('SNOWFLAKE_ACCOUNT', '')) == account_key(config['account']):
        user = os.environ.get('SNOWFLAKE_USER')
        if user:
            selected = {**config, 'user': user}
            command(selected)
            return selected
    home = profile_home(config)
    if home.exists():
        selected = choose(connections(home), config, managed=True)
        if selected:
            return selected
    # The installed CLI must support username-free OAuth login. No hidden flags enabled.
    run_cli(['auth', 'login', '--help'])
    if before_login is None:
        raise CLIError('Browser sign-in is needed; start it through the dashboard connection workflow.')
    before_login()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    config_path = home / 'config.toml'
    if config_path.is_symlink() or (home / 'connections.toml').is_symlink():
        raise CLIError('Use ordinary files for the private Analytics CLI configuration.')
    if not config_path.exists():
        fd = os.open(config_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    args = ['auth', 'login', config['account'], '--role', config['role'],
            '--warehouse', config['warehouse'], '--format', 'JSON']
    for key in ('database', 'schema'):
        if config.get(key):
            args += ['--' + key, config[key]]
    run_cli(args, home, timeout=180)
    selected = choose(connections(home), config, managed=True)
    if not selected:
        raise CLIError('Browser sign-in did not produce a matching connection. Pause setup; do not guess a username.')
    return selected
