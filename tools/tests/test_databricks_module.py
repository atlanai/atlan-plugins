"""Databricks plug regression checks using synthetic data and a constrained runtime."""
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/analytics-metrics/modules/metrics/engine'
RUNTIME = os.environ.get('ANALYTICS_METRICS_RUNTIME')
PRELUDE = '''
import os,sys,socket
from pathlib import Path
from unittest import mock
from datetime import datetime,timezone
sys.path.insert(0,str(ENGINE/'src'))
sys.path.insert(0,str(ENGINE/'packages/metrics-agent-databricks/src'))
import duckdb,sqlglot
from metrics_agent import sockets as S
from metrics_agent.socket_gates import ReadOnlyRunner,validated
from metrics_agent_databricks import auth,history
from metrics_agent_databricks.plug import DatabricksDirect,access_blocker

def no_network(*a,**kw):raise AssertionError('Offline test attempted network')
socket.socket.connect=no_network

class Runner:
    dialect='databricks'
    kind='databricks'
    def __init__(self):
        self.db=duckdb.connect()
        self.db.execute("ATTACH ':memory:' AS history_test")
        self.db.execute('CREATE SCHEMA history_test.query')
        self.db.execute('CREATE TABLE history_test.query.history(workspace_id VARCHAR,statement_text VARCHAR,executed_by_user_id VARCHAR,statement_id VARCHAR,start_time TIMESTAMP,execution_status VARCHAR,statement_type VARCHAR)')
        self.sent=[];self.closed=False
    def add(self,text,workspace='123',user='user_1',ident='statement_1',started='2026-01-10',status='FINISHED',kind='SELECT'):
        self.db.execute('INSERT INTO history_test.query.history VALUES (?,?,?,?,?,?,?)',[workspace,text,user,ident,started,status,kind])
    def connect(self):pass
    def close(self):self.closed=True
    def fetch(self,sql,limit):
        self.sent.append(sql)
        result=self.db.execute(sqlglot.transpile(sql.replace('system.query.history','history_test.query.history'),read='databricks',write='duckdb')[0])
        return [v[0] for v in result.description],result.fetchmany(limit)

TABLES=['DEMO.MAIN.ORDERS']
WINDOW=S.Window('2026-01-01','2026-02-01')
SETTINGS={'workspace_id':'123'}
'''


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME for Databricks contracts.')
class DatabricksModuleTests(unittest.TestCase):
    def check(self,code):
        prefix='from pathlib import Path\nENGINE=Path('+repr(str(ENGINE))+')\n'
        proc=subprocess.run([str(Path(RUNTIME)/'venv/bin/python'),'-c',prefix+PRELUDE+textwrap.dedent(code)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)

    def test_workspace_window_counts_privacy_and_verbatim_sql(self):
        self.check('''
            r=Runner();sql='SELECT\\n SUM(amount) AS total FROM demo.main.orders'
            r.add(sql);r.add(sql,user='user_2',ident='statement_2')
            r.add(sql,workspace='999');r.add(sql,status='FAILED');r.add(sql,kind='UPDATE')
            r.add(sql,started='2025-12-31');r.add(sql,started='2026-02-01')
            rows=list(validated(history.records(r,TABLES,WINDOW,SETTINGS),'query_record.schema.json',plug='databricks-direct'))
            assert len(rows)==1 and rows[0].query_text==sql
            assert rows[0].exec_count==2 and rows[0].user_count==2
            assert rows[0].user_name is None and rows[0].catalog is None and rows[0].schema is None
            assert len(r.sent)==2 and all("workspace_id = '123'" in query for query in r.sent)
            assert r.closed
        ''')

    def test_masked_history_is_not_reported_as_empty(self):
        self.check('''
            for hidden in ['<REDACTED>',None,'']:
                r=Runner();r.add(hidden)
                try:list(history.records(r,TABLES,WINDOW,SETTINGS))
                except S.AccessBlocked as exc:assert exc.blockers[0]['code']=='no_select_grant'
                else:raise AssertionError('Masked history admitted')
                assert len(r.sent)==1 and r.closed
        ''')

    def test_namespace_context_and_overflow_fail_before_yield(self):
        self.check('''
            assert history.qualified('WITH c AS (SELECT * FROM demo.main.orders) SELECT COUNT(*) FROM c')
            assert not history.qualified('SELECT COUNT(*) FROM orders')
            assert not history.qualified('SELECT 1; SELECT COUNT(*) FROM demo.main.orders')
            r=Runner();r.add('SELECT COUNT(*) FROM orders')
            try:next(history.records(r,TABLES,WINDOW,SETTINGS))
            except ValueError as exc:assert 'context' in str(exc)
            else:raise AssertionError('Unknown session context guessed')
            r=Runner();r.add('SELECT COUNT(*) FROM demo.main.orders');r.add('SELECT SUM(amount) FROM demo.main.orders')
            values={'default_days':90,'max_days':365,'max_shapes':1}
            with mock.patch.object(history.policy,'load',return_value=mock.Mock(get=lambda k:values[k.split('.')[-1]])):
                try:next(history.records(r,TABLES,WINDOW,SETTINGS))
                except ValueError as exc:assert 'group limit' in str(exc)
                else:raise AssertionError('Partial history yielded')
        ''')

    def test_invalid_inputs_never_reach_warehouse(self):
        self.check('''
            for tables,window,settings in [([],WINDOW,SETTINGS),(['DEMO.MAIN.X;DROP TABLE t'],WINDOW,SETTINGS),(TABLES,S.Window('bad','2026-02-01'),SETTINGS),(TABLES,S.Window('2026-02-01','2026-01-01'),SETTINGS),(TABLES,WINDOW,{}),(TABLES,WINDOW,{'workspace_id':"1' OR TRUE"})]:
                r=Runner()
                try:list(history.records(r,tables,window,settings))
                except ValueError:pass
                else:raise AssertionError('Invalid input accepted')
                assert not r.sent
            start,end,limit=history.bounds(S.Window(),now=datetime(2026,2,1,tzinfo=timezone.utc))
            assert (end-start).days==90 and limit==5000
        ''')

    def test_browser_token_and_legacy_auth_routes(self):
        self.check('''
            settings={'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example'}
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(auth,'at_terminal',return_value=True):
                options=auth.connection_options(settings)
                assert callable(options['credentials_provider']) and 'access_token' not in options
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(auth,'at_terminal',return_value=False):
                try:auth.connection_options(settings)
                except ValueError as exc:assert 'real terminal' in str(exc)
                else:raise AssertionError('Unattended OAuth started')
            with mock.patch.dict(os.environ,{'DATABRICKS_TOKEN':'synthetic-token'},clear=True):
                options=auth.connection_options(settings)
                assert options['access_token']=='synthetic-token' and 'auth_type' not in options
            dsn='server_hostname=example.cloud.databricks.com;http_path=/sql/1.0/warehouses/example;access_token=synthetic-token'
            with mock.patch.dict(os.environ,{'METRICS_AGENT_WAREHOUSE_DSN':dsn},clear=True):
                assert auth.connection_options({})['access_token']=='synthetic-token'
                try:auth.connection_options(dict(settings,server_hostname='other.cloud.databricks.com'))
                except ValueError:pass
                else:raise AssertionError('Token forwarded to another workspace')
            for change in [{'server_hostname':'https://example.cloud.databricks.com'},{'http_path':'/wrong'},{'server_hostname':'attacker.example'}]:
                with mock.patch.dict(os.environ,{'DATABRICKS_TOKEN':'synthetic-token'},clear=True):
                    try:auth.connection_options(dict(settings,**change))
                    except ValueError:pass
                    else:raise AssertionError('Invalid connection metadata accepted')
        ''')

    def test_errors_do_not_expose_credentials_and_writes_stay_blocked(self):
        self.check('''
            settings={'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example'}
            with mock.patch.dict(os.environ,{'DATABRICKS_TOKEN':'synthetic-token'},clear=True),mock.patch.object(DatabricksDirect,'sql_runner',side_effect=ValueError('synthetic-private-value')):
                result=DatabricksDirect(settings).check(settings)
                assert not result.ok and 'synthetic-private-value' not in str(result)
            error=Exception('INSUFFICIENT_PERMISSIONS synthetic-private-value')
            blocker=access_blocker(error)
            assert blocker['code']=='no_select_grant' and 'synthetic-private-value' not in str(blocker)
            uc=access_blocker(Exception('[UC_NOT_ENABLED] Unity Catalog is not enabled'))
            assert uc['code']=='database_not_visible' and 'metastore' in uc['ask']['what']
            r=Runner()
            try:ReadOnlyRunner(r,'databricks').fetch('DELETE FROM orders',1)
            except Exception as exc:assert type(exc).__name__=='ReadOnlyViolation'
            else:raise AssertionError('Write allowed')
            assert not r.sent
        ''')

    def test_sdk_profiles_cloud_and_federated_routes_keep_refresh_and_host_scope(self):
        self.check('''
            import databricks.sdk.core as sdk
            import databricks.sdk.credentials_provider as providers
            from types import SimpleNamespace
            settings={'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example','sign_in':'unified','profile':'example-profile'}
            seen=[];refreshes=[]
            class Delegate:
                def auth_type(self):return 'synthetic'
                def __call__(self,cfg):
                    seen.append(cfg.auth_type)
                    return lambda: refreshes.append(1) or {'Authorization':'Bearer synthetic-token'}
            def configuration(**kwargs):
                assert kwargs['profile']=='example-profile'
                cfg=SimpleNamespace(host='https://example.cloud.databricks.com/',username=None,password=None,auth_type=kwargs.get('auth_type'))
                headers=kwargs['credentials_strategy'](cfg)
                return SimpleNamespace(authenticate=headers)
            methods=['pat','oauth-m2m','databricks-cli','azure-cli','azure-client-secret','google-credentials','google-id','env-oidc','file-oidc','github-oidc','github-oidc-azure','azure-devops-oidc','runtime','runtime-oauth','metadata-service','model-serving']
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(sdk,'Config',side_effect=configuration),mock.patch.object(providers,'DefaultCredentials',Delegate):
                for method in methods:
                    options=auth.connection_options(dict(settings,sdk_auth_type=method))
                    assert 'access_token' not in options and 'auth_type' not in options
                    headers=options['credentials_provider']()
                    assert headers()['Authorization']=='Bearer synthetic-token'
                    headers()
                assert seen==methods and len(refreshes)==2*len(methods)
            def wrong_host(**kwargs):
                cfg=SimpleNamespace(host='https://other.cloud.databricks.com',username=None,password=None,auth_type='pat')
                return kwargs['credentials_strategy'](cfg)
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(sdk,'Config',side_effect=wrong_host),mock.patch.object(providers,'DefaultCredentials') as delegate:
                try:auth.connection_options(settings)['credentials_provider']()
                except auth.AuthConfigurationError:pass
                else:raise AssertionError('Profile token forwarded to selected workspace')
                delegate.return_value.assert_not_called()
            for method in ['basic','external-browser']:
                def unsafe(**kwargs):
                    cfg=SimpleNamespace(host='https://example.cloud.databricks.com',username=None,password=None,auth_type=method)
                    return kwargs['credentials_strategy'](cfg)
                with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(sdk,'Config',side_effect=unsafe),mock.patch.object(providers,'DefaultCredentials') as delegate,mock.patch.object(auth,'at_terminal',return_value=False):
                    try:auth.connection_options(settings)['credentials_provider']()
                    except auth.AuthConfigurationError:pass
                    else:raise AssertionError('Unsafe auth started')
                    delegate.return_value.assert_not_called()
        ''')

    def test_service_principal_selection_sdk_failures_and_azure_browser(self):
        self.check('''
            import databricks.sdk.core as sdk
            from types import SimpleNamespace
            settings={'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example'}
            env={'DATABRICKS_CLIENT_ID':'synthetic-client','DATABRICKS_CLIENT_SECRET':'synthetic-secret'}
            with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(sdk,'Config',return_value=SimpleNamespace(authenticate=lambda:{'Authorization':'Bearer synthetic-token'})) as config:
                options=auth.connection_options(settings)
                assert callable(options['credentials_provider'])
                options['credentials_provider']()()
                args=config.call_args.kwargs
                assert args['auth_type']=='oauth-m2m' and args['config_file']==os.devnull
                assert args['client_id']=='synthetic-client' and args['client_secret']=='synthetic-secret'
                assert args['host']=='https://example.cloud.databricks.com'
            with mock.patch.dict(os.environ,{},clear=True):
                try:auth.connection_options(dict(settings,sign_in='oauth-m2m'))
                except auth.AuthConfigurationError:pass
                else:raise AssertionError('Missing M2M credentials accepted')
            for error in [ValueError('synthetic-private-value'),RuntimeError('synthetic-private-value')]:
                with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(sdk,'Config',side_effect=error):
                    try:auth.connection_options(dict(settings,sign_in='unified'))['credentials_provider']()
                    except auth.AuthConfigurationError as exc:assert 'synthetic-private-value' not in str(exc)
                    else:raise AssertionError('SDK failure swallowed')
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(sdk,'Config',return_value=SimpleNamespace(authenticate=mock.Mock(side_effect=ValueError('synthetic-private-value')))):
                headers=auth.connection_options(dict(settings,sign_in='unified'))['credentials_provider']()
                try:headers()
                except auth.AuthConfigurationError as exc:assert 'synthetic-private-value' not in str(exc)
                else:raise AssertionError('Refresh failure swallowed')
            with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(auth,'at_terminal',return_value=True):
                options=auth.connection_options(dict(settings,sign_in='azure-browser',identity_federation_client_id='synthetic-client'))
                assert options['auth_type']=='azure-oauth' and options['identity_federation_client_id']=='synthetic-client'
            for method in ['browser','azure-browser','token','oauth-m2m','unified','sdk']:
                assert DatabricksDirect.validate_settings('sql_check',{'sign_in':method}) is None
        ''')

    def test_actual_sdk_pat_configuration_and_workspace_guard(self):
        self.check('''
            import databricks.sdk.core as sdk
            settings={'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example','sign_in':'unified'}
            env={'DATABRICKS_HOST':'https://example.cloud.databricks.com','DATABRICKS_TOKEN':'synthetic-token','DATABRICKS_AUTH_TYPE':'pat','DATABRICKS_CONFIG_FILE':os.devnull}
            with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(sdk.Config,'_resolve_host_metadata'):
                headers=auth.connection_options(settings)['credentials_provider']()
                assert headers()['Authorization']=='Bearer synthetic-token'
            with mock.patch.dict(os.environ,dict(env,DATABRICKS_HOST='https://other.cloud.databricks.com'),clear=True):
                try:auth.connection_options(settings)
                except auth.AuthConfigurationError:pass
                else:raise AssertionError('SDK host mismatch accepted')
        ''')

    def test_metadata_scope_is_validated_and_context_defaults_do_not_rewrite_history(self):
        self.check('''
            class MetadataRunner:
                def connect(self):pass
                def close(self):pass
                def scan(self,sql,batch):
                    assert 'demo.information_schema.columns' in sql
                    assert "table_schema = 'main'" in sql and "table_name = 'orders'" in sql
                    yield [('main','orders','id','INT','x'*100)]
            plug=DatabricksDirect()
            with mock.patch.object(plug,'sql_runner',return_value=MetadataRunner()):
                result=plug.table_schema(['demo.main.orders'],max_description_chars=10)
                assert 'DEMO.MAIN.ORDERS' in result.columns
            with mock.patch.object(plug,'sql_runner') as connect:
                try:plug.table_schema(['demo.main.orders;DROP'],max_description_chars=10)
                except ValueError:pass
                else:raise AssertionError('Invalid table accepted')
                connect.assert_not_called()
            from metrics_agent import run_config
            doc={'version':1,'scope':'example-domain','dialect':'databricks','mode':'standalone','tables':TABLES,'inputs':{'history':['databricks-direct'],'schema':[],'bi':[],'sql_check':None},'settings':{'databricks-direct':SETTINGS},'witness_mode':'parse','publish':['files'],'observers':[]}
            run_config.validate(doc)
        ''')

    def test_shared_socket_conformance(self):
        self.check('''
            import pytest
            assert pytest.main([str(ENGINE.parents[4]/'tools/tests/databricks_conformance.py'),'-q'])==0
        ''')
