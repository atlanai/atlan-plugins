"""Snowflake CLI transport. Credentials stay within the installed CLI.

Values are encoded separately and bound with Snowflake Scripting USING; never -D.
No shell, installation, config/credential reads, persistent database objects or DML.
"""
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path


class CLIError(ValueError):
    pass


class ConnectionPaused(CLIError):
    """Stop the check loop until the caller explicitly reconnects."""


def discover():
    candidates = [shutil.which('snow'), str(Path.home()/'.local/bin/snow')]
    candidates += [str(p) for p in sorted((Path.home()/'.cache/uv/archive-v0').glob('*/bin/snow'))]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise CLIError('Snowflake CLI is not installed or discoverable. Supply an approved installation; no automatic install was attempted.')


TOKEN = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|:[p][0-9]+\b|[A-Za-z_][A-Za-z_0-9$]*|\s+|.", re.S)
FORBIDDEN = set('INSERT UPDATE DELETE MERGE DROP CREATE ALTER GRANT REVOKE CALL COPY PUT GET REMOVE TRUNCATE USE EXECUTE INTO SYSTEM$SEND_EMAIL'.split())
FUNCTIONS = set('TO_JSON OBJECT_CONSTRUCT_KEEP_NULL COUNT SUM AVG MIN MAX COALESCE NULLIF UPPER LOWER POSITION INSTR CAST TRY_CAST TO_DATE TO_TIMESTAMP DATEADD DATEDIFF DATE_TRUNC CURRENT_DATE CURRENT_ROLE CURRENT_WAREHOUSE CURRENT_DATABASE CURRENT_SCHEMA CURRENT_SECONDARY_ROLES ROW_NUMBER RANK DENSE_RANK ROUND ABS LEAST GREATEST LEAST_IGNORE_NULLS GREATEST_IGNORE_NULLS IFF CONCAT TRIM LTRIM RTRIM SUBSTRING SUBSTR LENGTH REPLACE REGEXP_REPLACE REGEXP_LIKE TRY_TO_NUMBER TO_NUMBER TO_VARCHAR TO_CHAR NUMBER VARCHAR DECIMAL FLOAT BOOLEAN DATE TIMESTAMP_NTZ SPLIT_PART BOOLOR_AGG BOOLAND_AGG'.split())
CLAUSES = set('AS IN OVER PARTITION SELECT FROM WHERE AND OR NOT VALUES WHEN THEN ELSE CASE HAVING ON QUALIFY'.split())


def bound_query(sql, parameters):
    if not isinstance(sql,str) or len(sql)>500000 or not isinstance(parameters,dict):
        raise CLIError('Unsupported query size or bindings.')
    tokens=TOKEN.findall(sql.strip().rstrip(';'))
    meaningful=[t for t in tokens if not t.isspace() and not t.startswith(('--','/*'))]
    if not meaningful or meaningful[0].upper() not in ('SELECT','WITH'):
        raise CLIError('Only a bounded SELECT query is allowed.')
    for i,t in enumerate(meaningful):
        if t in (';','$','!','@','\\') or t.upper() in FORBIDDEN:
            raise CLIError('Only a single read-only SELECT query is allowed.')
        if i+1<len(meaningful) and meaningful[i+1]=='(' and (t.startswith('"') or re.fullmatch(r'[A-Za-z_][A-Za-z_0-9$]*',t)):
            if t.upper() not in FUNCTIONS | CLAUSES:
                raise CLIError('Unsupported SQL function or construct: '+(t.upper() if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9$]*',t) else 'quoted identifier')+'.')
    ordered=[];output=[]
    for token in tokens:
        if re.fullmatch(r':p[0-9]+',token):
            key=token[1:]
            if key not in parameters:raise CLIError('Missing bound parameter.')
            value=parameters[key]
            kind=('BOOLEAN' if type(value) is bool else 'NUMBER(38,0)' if type(value) is int
                  else 'FLOAT' if type(value) is float else 'VARCHAR')
            ordered.append(value);output.append('CAST(? AS '+kind+')')
        else:output.append(token)
    used={t[1:] for t in tokens if re.fullmatch(r':p[0-9]+',t)}
    if used!=set(parameters):raise CLIError('Bindings do not match the SQL parameters.')
    if len(ordered)>5000:raise CLIError('Too many parameter occurrences.')
    return ''.join(output),ordered


def hex_string(value):
    return "HEX_DECODE_STRING('"+value.encode('utf-8').hex()+"')"


def scripting(sql, parameters):
    query,values=bound_query(sql,parameters)
    declarations=['query_text VARCHAR DEFAULT '+hex_string(query)+';', 'rows_result RESULTSET;']
    for i,value in enumerate(values):
        if value is None:kind,literal='VARCHAR','NULL'
        elif type(value) is bool:kind,literal='BOOLEAN','TRUE' if value else 'FALSE'
        elif type(value) is int:
            if len(str(abs(value)))>38:raise CLIError('Integer exceeds Snowflake precision.')
            kind,literal='NUMBER(38,0)',str(value)
        elif type(value) is float and math.isfinite(value):kind,literal='FLOAT',repr(value)
        elif isinstance(value,str) and len(value)<=50000:kind,literal='VARCHAR',hex_string(value)
        else:raise CLIError('Unsupported bound parameter type or size.')
        declarations.append('b'+str(i)+' '+kind+' DEFAULT '+literal+';')
    using=' USING ('+', '.join('b'+str(i) for i in range(len(values)))+')' if values else ''
    return 'EXECUTE IMMEDIATE $$\nDECLARE\n'+'\n'.join(declarations)+'\nBEGIN\nrows_result := (EXECUTE IMMEDIATE :query_text'+using+');\nRETURN TABLE(rows_result);\nEND;\n$$;'


def command(config):
    for key in ('role','warehouse'):
        if not isinstance(config.get(key),str) or not re.fullmatch(r'[A-Za-z0-9_.$-]{1,256}',config[key]):
            raise CLIError('An explicit approved role and warehouse are required.')
    if config['role'].upper() in ('ACCOUNTADMIN','SECURITYADMIN','ORGADMIN'):
        raise CLIError('Use the report’s approved query role, not an administrator role.')
    args=[discover(),'sql','--stdin','--role',config['role'],'--warehouse',config['warehouse'],
          '--secondary-roles','NONE','--enable-templating','NONE','--local-only','--format','JSON','--silent',
          '--enhanced-exit-codes']
    if config.get('connection'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',config['connection']):raise CLIError('Invalid connection name.')
        args+=['--connection',config['connection']]
    else:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}',config.get('account','')):raise CLIError('Supply the source account or a named connection.')
        auth=config.get('authenticator','OAUTH_AUTHORIZATION_CODE')
        if auth not in ('OAUTH_AUTHORIZATION_CODE','externalbrowser'):raise CLIError('Use browser-based authentication.')
        if not config.get('user'):
            raise CLIError('Supply the non-secret Snowflake login name once as user. Browser authentication without a stable user cannot reuse the CLI credential cache; do not start per-query sign-ins.')
        args+=['--temporary-connection','--account',config['account'],'--authenticator',auth]
        # Let the official CLI own secure storage. Never read or serialize tokens here.
        args+=['--client-store-temporary-credential']
        if auth == 'OAUTH_AUTHORIZATION_CODE':
            args+=['--oauth-enable-refresh-tokens']
    if config.get('user'):
        if not re.fullmatch(r'[A-Za-z0-9_.@+\-]{1,256}',config['user']):raise CLIError('Invalid Snowflake login name.')
        args+=['--user',config['user']]
    for key in ('database','schema'):
        if config.get(key):
            if not re.fullmatch(r'[A-Za-z0-9_.$-]{1,256}',config[key]):raise CLIError('Invalid database or schema name.')
            args+=['--'+key,config[key]]
    return args


def execute(config, sql, parameters):
    bound_query(sql,parameters)
    # Preserve server-side scalar types; the CLI may stringify NUMBER/BOOLEAN columns.
    wrapped='SELECT TO_JSON(OBJECT_CONSTRUCT_KEEP_NULL(*)) AS "__trident_row" FROM ('+sql.strip().rstrip(';')+')'
    script=scripting(wrapped,parameters)
    try:
        run=subprocess.run(command(config), input=script, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise ConnectionPaused('Snowflake operation timed out; the warehouse checks are paused. A timeout does not prove the login expired. Resolve the connection or query timeout, then explicitly reconnect once.') from None
    except OSError:
        raise CLIError('Snowflake CLI could not start.') from None
    if run.returncode:
        # Never emit raw auth diagnostics, login URLs, query values or credential material.
        if run.returncode == 5:
            raise CLIError('Snowflake rejected the SQL. Check the translation and object permissions; do not reconnect or request another login for this query error.')
        if run.returncode == 2:
            raise CLIError('Snowflake CLI options are unsupported or invalid. Check CLI compatibility; do not retry with credential reuse disabled.')
        if re.search(r'\b(?:390303|390318)\b|(?:oauth|access|refresh) token (?:expired|invalid)|authentication failed', run.stderr or '', re.I):
            raise ConnectionPaused('Snowflake authentication needs attention. Warehouse checks are paused; reconnect once after sign-in is resolved, then resume the pending cases.')
        raise ConnectionPaused('Snowflake CLI could not complete the operation. Warehouse checks are paused; inspect connection availability and scope before an explicit reconnect. No authentication failure was confirmed.')
    try:
        rows=json.loads(run.stdout)
        if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],list):rows=rows[0]
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError()
        return [json.loads(row['__trident_row']) for row in rows]
    except (ValueError,TypeError,KeyError):
        raise CLIError('Snowflake CLI did not return a supported JSON result.') from None


def probe(config):
    rows=execute(config,'SELECT CURRENT_ROLE() AS ROLE, CURRENT_WAREHOUSE() AS WAREHOUSE',{})
    if len(rows)!=1 or rows[0].get('ROLE')!=config['role'] or rows[0].get('WAREHOUSE')!=config['warehouse']:
        raise CLIError('Connected role or warehouse does not match the selected scope.')
    return {'connected':True,'transport':'snowflake_cli','role':config['role'],'warehouse':config['warehouse']}
