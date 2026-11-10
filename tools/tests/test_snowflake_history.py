"""Native Snowflake history contracts, using only synthetic rows and an offline runner."""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/analytics-metrics/modules/metrics/engine'
RUNTIME = os.environ.get('ANALYTICS_METRICS_RUNTIME')

PRELUDE = '''
import sys, socket
from pathlib import Path
from unittest import mock
from datetime import datetime, timezone
sys.path.insert(0, str(ENGINE / 'src'))
sys.path.insert(0, str(ENGINE / 'packages/metrics-agent-snowflake/src'))
import duckdb, sqlglot
from metrics_agent import sockets as S
from metrics_agent.socket_gates import ReadOnlyRunner, validated
from metrics_agent_snowflake import history as H
from metrics_agent_snowflake.plug import SnowflakeDirect

def refuse_network(*args, **kwargs):
    raise AssertionError('Offline history tests must never contact a warehouse')
socket.socket.connect = refuse_network

class Runner:
    def __init__(self):
        self.db = duckdb.connect()
        self.db.execute("ATTACH ':memory:' AS SNOWFLAKE")
        self.db.execute('CREATE SCHEMA SNOWFLAKE.ACCOUNT_USAGE')
        self.db.execute('CREATE TABLE SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY (query_text VARCHAR, user_name VARCHAR, query_id VARCHAR, database_name VARCHAR, schema_name VARCHAR, start_time TIMESTAMPTZ, execution_status VARCHAR, query_type VARCHAR)')
        self.sent = []
        self.closed = False
    def add(self, text, user='user_1', query_id='query_1', database='DEMO', schema='MAIN', started='2026-01-10T12:00:00Z', status='success', kind='SELECT'):
        self.db.execute('INSERT INTO SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY VALUES (?,?,?,?,?,?,?,?)', [text,user,query_id,database,schema,started,status,kind])
    def connect(self):
        pass
    def close(self):
        self.closed = True
    def fetch(self, sql, limit):
        self.sent.append(sql)
        result=self.db.execute(sqlglot.transpile(sql,read='snowflake',write='duckdb')[0])
        return [col[0] for col in result.description], result.fetchmany(limit)
    def scan(self, sql, batch):
        _, rows=self.fetch(sql, batch)
        yield rows

WINDOW = S.Window('2026-01-01', '2026-02-01')
TABLES = ['DEMO.MAIN.ORDERS', 'DEMO.OTHER.ORDERS']
'''


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME to run native Snowflake history tests.')
class SnowflakeHistoryTests(unittest.TestCase):
    def check(self, source):
        prefix = 'from pathlib import Path\nENGINE = Path(' + repr(str(ENGINE)) + ')\n'
        proc = subprocess.run([str(Path(RUNTIME) / 'venv/bin/python'), '-c',
                               prefix + PRELUDE + textwrap.dedent(source)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_native_aggregation_preserves_sql_context_counts_and_privacy(self):
        self.check('''
            r=Runner()
            text='SELECT\\n SUM(amount) AS total\\nFROM orders'
            r.add(text, user='user_1', query_id='query_1')
            r.add(text, user='user_2', query_id='query_2')
            r.add(text, schema='OTHER', query_id='query_3')
            r.add(text, database='OTHER_DB', query_id='query_4')
            r.add(text, status='fail')
            r.add(text, kind='INSERT')
            r.add(text, started='2025-12-31T23:59:59Z')
            r.add(text, started='2026-02-01T00:00:00Z')
            rows=list(validated(H.records(r,TABLES,WINDOW),'query_record.schema.json',plug='snowflake-direct'))
            assert len(rows)==3
            by_context={(v.catalog,v.schema):v for v in rows}
            assert by_context['DEMO','MAIN'].exec_count==2
            assert by_context['DEMO','MAIN'].user_count==2
            assert all(v.query_text==text and v.user_name is None and v.start_time is None for v in rows)
            assert by_context['DEMO','OTHER'].exec_count==1
            assert r.closed
            assert 'COUNT(DISTINCT user_name)' in r.sent[0]
        ''')

    def test_default_window_is_bounded_and_explicit_windows_are_preserved(self):
        self.check('''
            now=datetime(2026,2,1,tzinfo=timezone.utc)
            start,end,limit=H.bounds(S.Window(),now=now)
            assert end==now and (end-start).days==90 and limit==5000
            start,end,_=H.bounds(S.Window('2026-01-01T01:00:00+01:00','2026-01-02T00:00:00Z'))
            assert start.isoformat()=='2026-01-01T00:00:00+00:00'
            assert end.isoformat()=='2026-01-02T00:00:00+00:00'
        ''')

    def test_window_scope_and_policy_validation_precede_any_read(self):
        self.check('''
            for window in [S.Window('2026-02-01','2026-01-01'), S.Window('2026-01-01','2026-01-01'), S.Window('2020-01-01','2026-01-01'), S.Window("2026-01-01';DELETE FROM x;--",'2026-02-01')]:
                r=Runner()
                try: list(H.records(r,TABLES,window))
                except ValueError: pass
                else: raise AssertionError('Invalid window accepted')
                assert not r.sent
            for tables in [[],['DEMO.MAIN.ORDERS;DROP TABLE x'],['ORDERS'],['DEMO.MAIN.%']]:
                r=Runner()
                try: list(H.records(r,tables,WINDOW))
                except ValueError: pass
                else: raise AssertionError('Invalid scope accepted')
                assert not r.sent
            with mock.patch.object(H.policy,'load',return_value=mock.Mock(get=lambda key:True)):
                try: H.bounds(WINDOW)
                except ValueError: pass
                else: raise AssertionError('Invalid policy accepted')
        ''')

    def test_overflow_blocks_without_yielding_partial_history(self):
        self.check('''
            r=Runner();r.add('SELECT SUM(amount) FROM orders');r.add('SELECT COUNT(*) FROM orders')
            values={'default_days':90,'max_days':365,'max_shapes':1}
            config=mock.Mock(get=lambda key:values[key.split('.')[-1]])
            with mock.patch.object(H.policy,'load',return_value=config):
                stream=H.records(r,TABLES,WINDOW)
                try: next(stream)
                except ValueError as exc: assert 'No partial history' in str(exc)
                else: raise AssertionError('Partial history admitted')
            assert 'LIMIT 2' in r.sent[0] and r.closed
        ''')

    def test_shape_table_route_and_native_default_dispatch(self):
        self.check('''
            from metrics_agent import run_config
            document={'version':1,'scope':'example-domain','dialect':'snowflake','mode':'standalone','inputs':{'history':['snowflake-direct'],'schema':[],'bi':[],'sql_check':None},'settings':{'snowflake-direct':{'history_source':'account_usage'}},'tables':['DEMO.MAIN.ORDERS'],'window':{'start':'2026-01-01','end':'2026-02-01'},'witness_mode':'parse','publish':['files'],'observers':[]}
            run_config.validate(document)
            assert H.source({})=='account_usage'
            assert H.source({'shape_table':'MAIN.SHAPES'})=='shape_table'
            for settings in [{'history_source':'other'},{'history_source':'shape_table'},{'history_source':'account_usage','shape_table':'MAIN.SHAPES'},{'history_source':'account_usage','columns':['sql']}]:
                assert SnowflakeDirect.validate_settings('history',settings)
            assert 'history_source: account_usage' in SnowflakeDirect.validate_settings('history',{'shape_table':H.VIEW})
            r=Runner();r.db.execute('CREATE TABLE shapes(sql VARCHAR,exec INT,users INT)')
            r.db.execute("INSERT INTO shapes VALUES ('SELECT COUNT(*) FROM orders',12,3)")
            plug=SnowflakeDirect({'shape_table':'shapes'},dialect='duckdb')
            with mock.patch.object(plug,'sql_runner',return_value=r):
                rows=list(plug.query_history([],S.Window()))
            assert len(rows)==1 and rows[0].exec_count==12 and rows[0].user_count==3
            r=Runner();r.add('SELECT COUNT(*) FROM orders')
            plug=SnowflakeDirect()
            with mock.patch.object(plug,'sql_runner',return_value=r):
                assert len(list(plug.query_history(TABLES,WINDOW)))==1
        ''')

    def test_native_input_passes_shared_socket_conformance(self):
        self.check('''
            import pytest
            result=pytest.main([str(ENGINE.parents[4]/'tools/tests/snowflake_conformance.py'),'-q'])
            assert result==0
        ''')

    def test_h0_keeps_equal_sql_from_different_session_schemas_separate(self):
        self.check('''
            import tempfile, json, subprocess, os
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve()
                history=root/'history.jsonl'
                text='SELECT SUM(amount) AS revenue FROM orders'
                history.write_text(''.join(json.dumps({'query_text':text,'catalog':'DEMO','schema':schema,'exec':count,'users':3})+'\\n' for schema,count in [('MAIN',12),('OTHER',8)]))
                config={'version':1,'scope':'example-domain','dialect':'snowflake','mode':'standalone','inputs':{'history':['local-files'],'schema':[],'bi':[],'sql_check':None},'settings':{'local-files':{'history':str(history)}},'tables':['DEMO.MAIN.ORDERS','DEMO.OTHER.ORDERS'],'witness_mode':'parse','publish':['files'],'observers':[]}
                path=root/'config.json';path.write_text(json.dumps(config))
                proc=subprocess.run([sys.executable,'-m','metrics_agent.cli','run','--run',str(root/'run'),'--config',str(path)],capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=str(ENGINE/'src')))
                assert proc.returncode in (0,3),proc.stderr
                rows=[json.loads(line) for line in (root/'run/query_corpus.jsonl').read_text().splitlines()]
                assert len(rows)==2, rows
                assert sorted(row['exec'] for row in rows)==[8,12],rows
        ''')

    def test_access_refusal_retains_structured_blockers_and_read_only_guard(self):
        self.check('''
            from metrics_agent.lib import access, artifacts
            b=access.blocker('no_select_grant',source='snowflake-direct',stage='sql_check',not_read='SQL',ask='grant read access')
            r=Runner()
            with mock.patch.object(r,'fetch',side_effect=S.AccessBlocked([b])):
                try: list(H.records(r,TABLES,WINDOW))
                except S.AccessBlocked as exc:
                    blocker=exc.blockers[0]
                    assert blocker['stage']=='h0' and H.VIEW in blocker['ask']['what']
                    artifacts.validate({'contract_version':'1.0','blockers':[blocker],'summary':{'n_blockers':1,'by_code':{'no_select_grant':1},'by_who':{blocker['ask']['who']:1},'statements_unverified':0,'widgets_unverified':0,'metrics_unverified':0}},'access_report.schema.json')
                else: raise AssertionError('Access refusal hidden')
            r=Runner()
            try: ReadOnlyRunner(r,'snowflake').fetch('DELETE FROM orders',1)
            except Exception as exc: assert type(exc).__name__=='ReadOnlyViolation'
            else: raise AssertionError('Write allowed')
            assert not r.sent
        ''')


if __name__ == '__main__':
    unittest.main()
