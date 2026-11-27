"""The analytics-readiness skill: names only, read-only, suggestions for what the person does, one report."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'plugins/atlanai-analytics/skills/analytics-readiness/scripts/readiness.py'
sys.path.insert(0, str(SCRIPT.parent))
import readiness  # noqa: E402

# Each tool's listing carries an account, a user, a host or a URL beside each name; none of it may come out.
SNOW = r'''#!/bin/sh
case "$1" in
  --version) echo "Snowflake CLI version: 3.9.0" ;;
  connection)
    echo '[{"connection_name": "ab12345", "is_default": true, "parameters": {"account": "ab12345.us-east-1", "user": "JANE@EXAMPLE.COM", "host": "ab12345.snowflakecomputing.com"}},
           {"connection_name": "work", "is_default": false, "parameters": {"account": "orgsecret-acct", "password": "****"}}]' ;;
  sql)
    case "$*" in
      *query_history*)
        if [ "$FAKE_SNOW" = nohistory ]; then echo "SQL access control error: Insufficient privileges on https://ab12345.snowflakecomputing.com" 1>&2; exit 1; fi
        echo '[{"N": 1}]'; exit 0 ;;
    esac
    case "$FAKE_SNOW" in
      admin) echo '[{"USER_NAME": "JANE", "ROLE_NAME": "SYSADMIN", "WAREHOUSE_NAME": "WH_XS"}]' ;;
      expired) echo "250001 (08001): Failed to connect to DB https://ab12345.snowflakecomputing.com: OAuth token expired" 1>&2; exit 1 ;;
      *) echo '[{"USER_NAME": "JANE", "ROLE_NAME": "ANALYST", "WAREHOUSE_NAME": ""}]' ;;
    esac ;;
esac
'''
DATABRICKS = r'''#!/bin/sh
case "$1 $2" in
  "--version ") echo "Databricks CLI v0.240.0" ;;
  "auth profiles") echo '{"profiles": [{"name": "adb-1234567890", "host": "https://adb-1234567890.azuredatabricks.net", "valid": true}]}' ;;
  "current-user me") echo '{"userName": "jane@example.com", "groups": [{"display": "users"}]}' ;;
  "experimental aitools") exit 1 ;;
  "warehouses list") [ -n "$FAKE_NO_WAREHOUSE" ] && echo '[]' || echo '[{"id": "abc", "name": "Starter", "state": "STOPPED"}]' ;;
  "api post") echo '{"statement_id": "x", "status": {"state": "PENDING"}}' ;;
esac
'''
GCLOUD = r'''#!/bin/sh
case "$1 $2" in
  "--version ") echo "Google Cloud SDK 500.0.0" ;;
  "config configurations") echo '[{"name": "default", "is_active": true, "properties": {"core": {"account": "jane@example.com", "project": "acme-prod-123"}}}]' ;;
  "auth list") echo '[{"account": "jane@example.com", "status": "ACTIVE"}]' ;;
  "config get-value") echo "acme-prod-123" ;;
esac
'''
BQ = r'''#!/bin/sh
echo '[{"user": "jane@example.com"}]'
'''
AWS = r'''#!/bin/sh
case "$1 $2" in
  "--version ") echo "aws-cli/2.17.0" ;;
  "configure list-profiles") echo "analytics" ;;
  "sts get-caller-identity") echo "arn:aws:sts::123456789012:assumed-role/DataAdminRole/jane" ;;
esac
'''
CLAUDE = r'''#!/bin/sh
case "$1 $2" in
  "--version ") echo "2.1.332 (Claude Code)" ;;
  "mcp list") printf '%s\n' "Checking MCP server health..." "" \
     "plugin:atlanai:atlan: https://api.atlan.com/mcp (HTTP) - \342\234\223 Connected" \
     "snowflake-reader: https://ab12345.snowflakecomputing.com/api/v2/mcp (HTTP) - \342\232\240 Needs authentication" \
     "tableau: npx tableau-mcp (stdio) - \342\234\227 Failed to connect" \
     "github: npx github-mcp --token sk-secret (stdio) - \342\234\223 Connected" ;;
esac
'''


class Sandbox(unittest.TestCase):
    """A fake HOME and PATH, so no real CLI, connection, server or app on this machine is read."""

    def setUp(self):
        if os.name == 'nt':
            self.skipTest('the fake CLIs are shell scripts')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.workspace, self.bin, home = base / 'project', base / 'bin', base / 'home'
        for d in (self.workspace, self.bin, home):
            d.mkdir()
        env = mock.patch.dict(os.environ, {'HOME': str(home), 'PATH': '%s:/usr/bin:/bin' % self.bin})
        env.start()
        self.addCleanup(env.stop)
        for key in ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED', 'CURSOR_AGENT',
                    'DATABRICKS_HOST', 'DATABRICKS_TOKEN'):
            os.environ.pop(key, None)
        for patch in (mock.patch.object(readiness, 'plugin_python', return_value=('python3.12', '3.12')),
                      mock.patch.object(readiness, 'applications', return_value=set()),
                      mock.patch.object(readiness, 'identity', return_value={'git_name': 'Jane Doe'})):
            patch.start()
            self.addCleanup(patch.stop)

    def tool(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = readiness.main(['--workspace', str(self.workspace)] + list(argv))
        return code, json.loads(out.getvalue())

    def saved(self):
        return (self.workspace / '.atlan-analytics/readiness/state.json').read_text()

    def report(self):
        _, out = self.cli('report')
        return out, (self.workspace / '.atlan-analytics/readiness/report.md').read_text()


class Names(unittest.TestCase):
    def test_identifier_like_names_become_labels(self):
        for name in ('ab12345', 'Snowflake - ab12345', 'adb-1234567890', 'org.account', 'jane@example.com',
                     'zqxwvbn-reporting', 'acct:prod'):
            self.assertTrue(readiness.looks_like_identifier(name), name)
        for name in ('work', 'prod-reader', 'genie', 'default', 'analytics team', 'snowflake connection 2'):
            self.assertFalse(readiness.looks_like_identifier(name), name)
        self.assertEqual(readiness.label('snowflake', 'connection', 2, 'ab12345'), 'snowflake connection 2')
        self.assertEqual(readiness.label('snowflake', 'connection', 2, 'work'), 'work')

    def test_error_text_loses_hosts_addresses_accounts_and_tokens(self):
        line = readiness.scrub('Failed for jane@example.com at https://ab12345.snowflakecomputing.com/x via 10.1.2.3 '
                               'account 123456789012 token ' + 'A' * 30 + ' on db.example.io\nsecond line')
        for leaked in ('jane@', 'snowflakecomputing', '10.1.2.3', '123456789012', 'A' * 30, 'example.io', 'second line'):
            self.assertNotIn(leaked, line)

    def test_mcp_servers_are_classed_by_name(self):
        self.assertEqual(readiness.server_class('plugin:atlanai:atlan'), 'Atlan')
        self.assertEqual(readiness.server_class('Snowflake - Cortex'), 'snowflake')
        self.assertEqual(readiness.server_class('pg-prod'), 'other warehouse')
        self.assertEqual(readiness.server_class('community-tools'), 'other')
        self.assertEqual(readiness.parse_mcp('x: https://login.example.com/oauth (HTTP) - \u2713 Connected'),
                         [('x', 'connected')])


class Records(unittest.TestCase):
    def setUp(self):
        self.state = readiness.empty()

    def record(self, check, status, **values):
        fields = readiness.clean(check, status, values, self.state)
        readiness.remember(self.state, check, status, fields)
        return fields

    def test_values_are_checked(self):
        for check, status, values in (('atlan', 'answered', {}), ('warehouse', 'verified', {'via': 'cli'}),
                                      ('warehouse', 'verified', {'kind': 'snowflake', 'via': 'ftp'}),
                                      ('bi', 'answered', {'pages': 'four'}), ('bi', 'answered', {'colour': 'x'}),
                                      ('history', 'verified', {'scope': 'the team'})):
            with self.assertRaises(readiness.ReadinessError):
                readiness.clean(check, status, values, self.state)

    def test_an_admin_role_is_flagged_and_server_names_keep_one_label(self):
        first = self.record('warehouse', 'verified', kind='snowflake', via='atlan', server='Snowflake - ab12345',
                            role='accountadmin')
        self.assertEqual((first['server'], first['admin']), ('snowflake server 1', 'yes'))
        again = self.record('warehouse', 'verified', kind='snowflake', via='atlan', server='Snowflake - ab12345')
        other = self.record('warehouse', 'failed', kind='snowflake', via='atlan', server='Snowflake - cd67890')
        self.assertEqual((again['server'], other['server']), ('snowflake server 1', 'snowflake server 2'))
        self.assertNotIn('ab12345', json.dumps(self.state))
        self.assertEqual(len(self.state['warehouse']), 2)

    def test_a_later_record_adds_to_the_route(self):
        self.record('warehouse', 'verified', kind='snowflake', via='cli', connection='work', user='JANE', role='ANALYST')
        self.record('warehouse', 'verified', kind='snowflake', via='cli', connection='work', warehouse='WH')
        fields = self.state['warehouse'][0]['fields']
        self.assertEqual((fields['role'], fields['warehouse']), ('ANALYST', 'WH'))


class Probe(Sandbox):
    def test_names_only_from_every_listing(self):
        self.tool('snow', SNOW)
        self.tool('databricks', DATABRICKS)
        self.tool('gcloud', GCLOUD)
        self.tool('aws', AWS)
        self.tool('claude', CLAUDE)
        (self.workspace / 'reports').mkdir()
        (self.workspace / 'reports/pipeline.pbix').write_bytes(b'')
        code, out = self.cli('probe', '--fresh')
        self.assertEqual(code, 0)
        clis = {c['cli']: c['connections'] for c in out['clis']}
        self.assertEqual(clis['snow'], ['snowflake connection 1', 'work'])
        self.assertEqual(clis['databricks'], ['databricks profile 1'])
        self.assertEqual((clis['gcloud'], clis['aws']), (['default'], ['analytics']))
        self.assertEqual([d['file'] for d in out['dashboards_here']], ['reports/pipeline.pbix'])
        claude = next(m for m in out['mcp'] if m['agent'] == 'Claude Code')
        self.assertEqual([(s['label'], s['class'], s['status']) for s in claude['servers']],
                         [('atlan server 1', 'Atlan', 'connected'),
                          ('snowflake-reader', 'snowflake', 'needs sign-in'), ('tableau', 'BI', 'failed')])
        self.assertEqual(claude['others'], 1)
        for leaked in ('ab12345', 'JANE@', 'orgsecret', 'snowflakecomputing', 'adb-1234567890', 'azuredatabricks',
                       'acme-prod-123', 'sk-secret', 'api.atlan.com'):
            self.assertNotIn(leaked, json.dumps(out) + self.saved())
        _, text = self.report()
        self.assertIn('Sign in to snowflake-reader in Claude Code', text)

    def test_a_snowflake_check_records_the_role_and_flags_an_administrator(self):
        self.tool('snow', SNOW)
        self.cli('probe', '--fresh')
        with mock.patch.dict(os.environ, {'FAKE_SNOW': 'admin'}):
            code, out = self.cli('check', 'snowflake', '--connection', 'snowflake connection 1')
        self.assertEqual((code, out['status'], out['role'], out['admin']), (0, 'verified', 'SYSADMIN', 'yes'))
        self.assertNotIn('ab12345', json.dumps(out) + self.saved())
        report, text = self.report()
        self.assertEqual(report['next']['key'], 'admin')
        self.assertIn('| Warehouse | verified, admin role |', text)

    def test_the_same_yes_checks_past_queries(self):
        self.tool('snow', SNOW)
        self.cli('probe', '--fresh')
        _, out = self.cli('check', 'snowflake', '--connection', 'work')
        self.assertEqual(out['past_queries'], {'kind': 'snowflake', 'scope': 'everyone', 'status': 'verified'})
        report, text = self.report()
        self.assertIn("| Past queries | verified | everyone's queries |", text)
        self.assertNotIn('## For your warehouse admin', text)

    def test_unreadable_past_queries_hand_the_admin_a_view(self):
        self.tool('snow', SNOW)
        self.cli('probe', '--fresh')
        with mock.patch.dict(os.environ, {'FAKE_SNOW': 'nohistory'}):
            _, out = self.cli('check', 'snowflake', '--connection', 'work')
        self.assertEqual((out['status'], out['past_queries']['status']), ('verified', 'failed'))
        self.assertEqual(out['past_queries']['error'], 'not authorised: an admin grants access')
        _, text = self.report()
        self.assertIn("Create one view of your team's past queries for this work", text)
        self.assertIn('## For your warehouse admin', text)
        self.assertIn('create or replace secure view <db>.atlan.query_shapes', text)
        self.assertNotIn('snowflakecomputing', text + self.saved())

    def test_only_their_own_past_queries_is_a_step_for_the_admin(self):
        self.cli('probe', '--fresh')
        self.cli('record', 'warehouse', '--status', 'verified', '--set', 'kind=databricks', '--set', 'via=atlan')
        self.cli('record', 'history', '--status', 'verified', '--set', 'kind=databricks', '--set', 'scope=your own')
        tips = readiness.suggest(readiness.load(self.workspace))
        tip = next(t for t in tips if t['key'] == 'history-view')
        self.assertEqual((tip['priority'], tip['who']), (2, 'Your warehouse admin'))
        self.assertIn('Only your own past queries can be read', tip['why'])

    def test_an_expired_sign_in_fails_without_the_raw_error(self):
        self.tool('snow', SNOW)
        self.cli('probe', '--fresh')
        with mock.patch.dict(os.environ, {'FAKE_SNOW': 'expired'}):
            _, out = self.cli('check', 'snowflake', '--connection', 'snowflake connection 1')
        self.assertEqual((out['status'], out['error']), ('failed', 'sign-in needed or expired'))
        self.assertNotIn('snowflakecomputing', json.dumps(out) + self.saved())

    def test_databricks_counts_a_starting_warehouse_and_names_a_missing_one(self):
        self.tool('databricks', DATABRICKS)
        self.cli('probe', '--fresh')
        _, out = self.cli('check', 'databricks', '--profile', 'databricks profile 1')
        self.assertEqual(out['status'], 'verified')
        self.assertIn('starting', out['reason'])
        self.assertEqual(out['past_queries']['status'], 'not_checked')
        with mock.patch.dict(os.environ, {'FAKE_NO_WAREHOUSE': '1'}):
            _, out = self.cli('check', 'databricks', '--profile', 'databricks profile 1')
        self.assertEqual(out['status'], 'present')
        self.assertIn('no SQL warehouse', out['reason'])

    def test_bigquery_runs_one_query_and_keeps_no_project_id(self):
        self.tool('gcloud', GCLOUD)
        self.tool('bq', BQ)
        self.cli('probe', '--fresh')
        _, out = self.cli('check', 'bigquery', '--configuration', 'default')
        self.assertEqual((out['status'], out['user']), ('verified', 'jane@example.com'))
        self.assertEqual(out['past_queries']['scope'], 'everyone')
        self.assertNotIn('acme-prod-123', json.dumps(out) + self.saved())

    def test_redshift_keeps_the_role_name_and_never_the_account_number(self):
        self.tool('aws', AWS)
        self.cli('probe', '--fresh')
        _, out = self.cli('check', 'redshift', '--profile', 'analytics')
        self.assertEqual((out['status'], out['role'], out['admin']), ('present', 'DataAdminRole', 'yes'))
        self.assertIn('workgroup', out['reason'])
        self.assertNotIn('123456789012', json.dumps(out) + self.saved())

    def test_an_unknown_label_is_refused(self):
        self.tool('snow', SNOW)
        code, out = self.cli('check', 'snowflake', '--connection', 'ab12345-nope')
        self.assertEqual((code, out['status']), (2, 'blocked'))


class Report(Sandbox):
    def test_no_route_needs_attention_and_suggests_the_cli_and_a_browser_connection(self):
        self.cli('probe', '--fresh')
        self.cli('record', 'warehouse', '--status', 'none', '--set', 'kind=snowflake')
        self.cli('record', 'atlan', '--status', 'failed', '--set', 'error=needs sign-in')
        out, text = self.report()
        self.assertEqual(out['verdict'], 'needs_attention')
        self.assertIn('no warehouse route works yet', out['why'])
        self.assertIn('Install the Snowflake CLI', text)
        self.assertIn('--authenticator externalbrowser', text)
        self.assertIn('Sign in to Atlan', text)

    def test_an_unchecked_cli_connection_is_the_next_step(self):
        self.tool('snow', SNOW)
        self.cli('probe', '--fresh')
        self.cli('record', 'warehouse', '--status', 'none', '--set', 'kind=snowflake')
        out, _ = self.report()
        self.assertEqual(out['next']['do'], 'Test your Snowflake CLI connection')

    def test_every_unreached_warehouse_gets_a_step(self):
        self.cli('probe', '--fresh')
        self.cli('record', 'warehouse', '--status', 'none', '--set', 'kind=postgres')
        out, _ = self.report()
        self.assertEqual(out['next']['do'], 'Connect Postgres')

    def test_ready_is_an_agent_a_warehouse_and_a_named_dashboard(self):
        self.cli('probe', '--fresh')
        for argv in (('harness', '--status', 'verified', '--set', 'name=Claude Code'),
                     ('atlan', '--status', 'verified', '--set', 'workspaces=2'),
                     ('warehouse', '--status', 'verified', '--set', 'kind=snowflake', '--set', 'via=atlan',
                      '--set', 'server=Snowflake', '--set', 'role=ANALYST'),
                     ('bi', '--status', 'answered', '--set', 'tool=Power BI', '--set', 'dashboard=Customer Health',
                      '--set', 'tables_known=roughly', '--set', 'tables=ANALYTICS.CS')):
            self.cli('record', *argv)
        out, text = self.report()
        self.assertEqual(out['verdict'], 'ready')
        tip = next(t for t in readiness.suggest(readiness.load(self.workspace)) if t['key'] == 'snow-install')
        self.assertEqual(tip['priority'], 2)
        self.assertIn('past queries', tip['why'])
        for heading in ('## Summary', '## To speed up your build', '## Participant', '## Use case', '## Coding agents',
                        '## Warehouse', '## BI', '## Machine', '## Notes', '## Start the build'):
            self.assertIn(heading, text)
        self.assertIn('- BI access: not checked', text)
        self.assertIn('- Tables for the use case: ANALYTICS.CS', text)
        self.assertIn('Build an agent from my Power BI dashboard "Customer Health"', text)
        self.assertNotIn('\u2014', text)

    def test_a_dashboard_with_its_data_inside_needs_attention(self):
        self.cli('probe', '--fresh')
        self.cli('record', 'warehouse', '--status', 'verified', '--set', 'kind=databricks', '--set', 'via=host')
        self.cli('record', 'bi', '--status', 'verified', '--set', 'tool=Tableau', '--set', 'dashboard=Sales',
                 '--set', 'inside_file=yes')
        out, _ = self.report()
        self.assertEqual(out['verdict'], 'needs_attention')
        self.assertEqual(out['next']['do'], 'Name the warehouse table behind your dashboard')

    def test_an_old_python_is_the_first_thing_to_fix(self):
        with mock.patch.object(readiness, 'plugin_python', return_value=(None, None)):
            self.cli('probe', '--fresh')
        out, _ = self.report()
        self.assertEqual(out['next']['do'], 'Install Python 3.12')
        self.assertIn('Python 3.11 or later is missing', out['why'])


class Dashboards(unittest.TestCase):
    """Dashboard files are opened read-only, by pattern: pages, measures, the warehouse they read."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_a_tableau_workbook_on_snowflake(self):
        path = self.dir / 'pipeline.twb'
        path.write_text("<workbook><datasources><datasource><connection class='federated'><named-connections>"
                        "<named-connection><connection class='snowflake' server='x' /></named-connection>"
                        "</named-connections></connection>"
                        "<column caption='Win rate'><calculation class='tableau' formula='SUM(1)' /></column>"
                        "</datasource></datasources><worksheets><worksheet name='Trend' /><worksheet name='By rep' />"
                        "</worksheets><dashboards><dashboard name='Overview' /></dashboards></workbook>")
        self.assertEqual(readiness.inspect_dashboard(path),
                         {'pages': 3, 'measures': 1, 'reads': 'snowflake', 'inside_file': False})

    def test_a_packaged_workbook_with_its_data_inside(self):
        path = self.dir / 'sales.twbx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('sales.twb', "<workbook><connection class='hyper' /><worksheet name='Sheet 1' /></workbook>")
            archive.writestr('Data/Extracts/sales.hyper', b'\x00')
        found = readiness.inspect_dashboard(path)
        self.assertEqual((found['pages'], found['inside_file'], found['reads']), (1, True, None))

    def test_a_power_bi_template_reads_its_pages_measures_and_warehouse(self):
        path = self.dir / 'health.pbit'
        model = {'model': {'tables': [{'name': 'Accounts', 'measures': [{'name': 'ARR'}, {'name': 'Churn'}],
                                       'partitions': [{'source': {'expression': 'let Source = Snowflake.Databases("x") in Source'}}]},
                                      {'name': 'Dates', 'measures': [{'name': 'Days'}]}]}}
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('DataModelSchema', json.dumps(model).encode('utf-16-le'))
            archive.writestr('Report/Layout', b'\xff\xfe' + json.dumps({'sections': [{}, {}, {}, {}]}).encode('utf-16-le'))
        self.assertEqual(readiness.inspect_dashboard(path),
                         {'pages': 4, 'measures': 3, 'reads': 'snowflake', 'inside_file': False})

    def test_a_power_bi_file_reports_what_it_can_and_a_broken_file_says_so(self):
        path = self.dir / 'pipeline.pbix'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Report/Layout', json.dumps({'sections': [{}, {}]}).encode('utf-16-le'))
            archive.writestr('DataModel', b'\x00compressed')
        self.assertEqual(readiness.inspect_dashboard(path)['pages'], 2)
        broken = self.dir / 'broken.pbix'
        broken.write_bytes(b'not a zip')
        self.assertEqual(readiness.inspect_dashboard(broken)['error'], 'could not be opened')


class SelfContained(unittest.TestCase):
    def test_the_readiness_check_needs_no_build_helper(self):
        """It runs before the build's Python is in place, so its files use nothing outside the skill."""
        folder = SCRIPT.parents[1]
        for path in (folder / 'references' / 'readiness.md', folder / 'references' / 'warehouse-checks.md', SCRIPT):
            text = path.read_text()
            for outside in ('PROJECT', 'project.py', 'analytics-build', 'atlan-share', '../'):
                self.assertNotIn(outside, text, '%s names %s' % (path.relative_to(folder), outside))

    def test_nothing_names_the_skill_it_replaced(self):
        """source-survey's flows live in this skill now: a leftover name would load nothing."""
        places = [ROOT / 'plugin.toml', ROOT / 'tools/tests/fixtures/journeys.json']
        for top in (ROOT / 'plugins/atlanai-analytics', ROOT / 'evals/atlan-analytics/cases'):
            places += [p for p in top.rglob('*') if p.suffix in ('.md', '.py', '.json', '.yaml', '.toml', '.html')]
        for path in places:
            self.assertNotIn('source-survey', path.read_text(errors='ignore'), str(path.relative_to(ROOT)))


class Portable(unittest.TestCase):
    def test_parses_as_python_3_7(self):
        """It runs before the Python Atlan Analytics needs is in place, on a Mac's own python3."""
        ast.parse(SCRIPT.read_text(), feature_version=(3, 7))

    def test_runs_nothing_that_writes_or_signs_in(self):
        text = SCRIPT.read_text()
        for verb in ("'install'", "'add'", "'login'", "'configure', 'set", "'sso'", 'shell=True'):
            self.assertNotIn(verb, text)


if __name__ == '__main__':
    unittest.main()
