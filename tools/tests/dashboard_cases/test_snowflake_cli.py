import copy
import json
import subprocess
import unittest
from unittest.mock import patch
from trident.snowflake_cli import bound_query, scripting, command, execute, CLIError, ConnectionPaused
from trident.dax_sql import translate, TranslationGap
import test_eval_flow as flow
from test_dax_sql import mapping

CONFIG={'account':'example-account','user':'example_analyst','role':'ANALYST','warehouse':'EXAMPLE_WH'}

class SnowflakeCLI(unittest.TestCase):
    def test_bindings_skip_quoted_text_and_preserve_repetition(self):
        sql,values=bound_query('SELECT :p0, :p0, \'literal :p1\', "column:p1"',{'p0':17})
        self.assertEqual(values,[17,17]);self.assertIn("'literal :p1'",sql)
        self.assertEqual(sql.count('?'),2)

    def test_scripting_keeps_literals_out_of_query_and_uses_server_bindings(self):
        value="x'); DROP TABLE example; $$ --"
        script=scripting('SELECT :p0 AS "value", :p1, :p2',{'p0':value,'p1':False,'p2':None})
        self.assertNotIn(value,script);self.assertIn(value.encode().hex(),script)
        self.assertIn('USING (b0, b1, b2)',script)
        self.assertIn('BOOLEAN DEFAULT FALSE',script)
        self.assertIn('VARCHAR DEFAULT NULL',script)

    def test_mutations_and_external_functions_refused(self):
        for sql in ['DROP TABLE example','SELECT 1; DROP TABLE example','SELECT * INTO example FROM source',
                    'SELECT external_fn(1)','SELECT "external_fn"(1)','SELECT * FROM @stage']:
            with self.assertRaises(CLIError):scripting(sql,{})
        for val in [float('nan'),[],10**39]:
            with self.assertRaises(CLIError):scripting('SELECT :p0',{'p0':val})

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    def test_temporary_oauth_needs_no_saved_connection(self,_):
        args=command(CONFIG)
        self.assertIn('--temporary-connection',args);self.assertIn('OAUTH_AUTHORIZATION_CODE',args)
        self.assertIn('--client-store-temporary-credential',args)
        self.assertIn('--oauth-enable-refresh-tokens',args)
        self.assertNotIn('-D',args);self.assertIn('NONE',args)
        with self.assertRaises(CLIError):command({**CONFIG,'role':'ACCOUNTADMIN'})
        named=command({'connection':'example','role':'ANALYST','warehouse':'EXAMPLE_WH'})
        self.assertIn('--connection',named);self.assertNotIn('--temporary-connection',named)
        self.assertNotIn('--client-store-temporary-credential',named)
        self.assertNotIn('--oauth-enable-refresh-tokens',named)

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    @patch('trident.snowflake_cli.subprocess.run')
    def test_cli_preserves_scalar_types_and_hides_raw_errors(self,run,_):
        run.return_value=subprocess.CompletedProcess([],0,stdout=json.dumps([{'__trident_row':json.dumps({'value':17,'flag':True,'blank':None})}]),stderr='')
        self.assertEqual(execute(CONFIG,'SELECT :p0',{'p0':17}),[{'value':17,'flag':True,'blank':None}])
        self.assertNotIn('shell',run.call_args.kwargs)
        run.return_value=subprocess.CompletedProcess([],1,stdout='',stderr='sensitive auth diagnostic')
        with self.assertRaises(CLIError) as exc:execute(CONFIG,'SELECT 1',{})
        self.assertNotIn('sensitive auth diagnostic',str(exc.exception))

    def test_native_source_query_needs_no_created_view(self):
        m=mapping();m['rowset_sql']='SELECT id, active, amount, tier, change, source FROM example_accounts'
        query=translate("SUM('Accounts'[Amount])",m,{'filters':[]},'snowflake')
        self.assertIn('FROM (SELECT id',query['sql'])
        self.assertEqual(query['source_sql'],m['rowset_sql'])
        m['rowset_sql']='SELECT 1; DELETE FROM example_accounts'
        with self.assertRaises(TranslationGap):translate("COUNTROWS('Accounts')",m,{'filters':[]},'snowflake')

class CLIWorkflow(unittest.TestCase):
    setUp=flow.EvalFlow.setUp
    call=flow.EvalFlow.call
    mutate=flow.EvalFlow.mutate
    task=flow.EvalFlow.task
    @patch('trident.snowflake_cli.probe',return_value={'connected':True})
    @patch('trident.snowflake_cli.execute',return_value=[{'value':17}])
    def test_real_transport_handoff_and_source_sql_contract(self,run,probe):
        p=self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.assertTrue(p['warehouse_connection']['connected'])
        m=mapping();m['rowset_sql']='SELECT id, active, amount, source FROM example_accounts'
        p=self.mutate('dashboard_translate',dialect='snowflake',mappings=[m])
        self.assertEqual(p['summary']['sql_candidates'],2)
        d=self.call('dashboard_read',{'job_id':self.job,'include_definitions':True})['definitions'][0]['definition']
        self.assertIn('sql',d['source']);self.assertNotIn('table',d['source'])
        self.mutate('dashboard_plan_evals')
        task=self.task()
        result=self.call('dashboard_snowflake_execute',{'job_id':self.job,'metric_id':p['metrics'][0]['id'],'context_hash':p['context_hash'],
                         'purpose':'validation','case_id':task['case_id'],'plan_generation':task['plan_generation']})
        self.assertEqual(result['observation']['value'],17)
        self.assertEqual(result['observation']['query_hash'],p['metrics'][0]['translation']['query_hash'])
        self.assertFalse(result['data_cutoff_verified'])
        with self.assertRaises(ValueError):
            self.call('dashboard_snowflake_execute',{'job_id':self.job,'metric_id':p['metrics'][0]['id'],'context_hash':'wrong'})

class Regression051(unittest.TestCase):
    def test_generated_case_expressions_and_known_native_functions(self):
        for sql in ["SELECT CASE WHEN (COUNT(*)=0) THEN (NULL) ELSE (COUNT(DISTINCT id)) END FROM example",
                    "SELECT SPLIT_PART(label, '-', 1), BOOLOR_AGG(active), BOOLAND_AGG(active) FROM example"]:
            self.assertTrue(bound_query(sql,{})[0])
        with self.assertRaises(CLIError): bound_query('SELECT vendor.external_fn(1)',{})

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    def test_external_browser_requires_and_passes_login_name(self,_):
        with self.assertRaisesRegex(CLIError,'dashboard_snowflake_connect'):
            command({k:v for k,v in {**CONFIG,'authenticator':'externalbrowser'}.items() if k!='user'})
        cmd=command({**CONFIG,'authenticator':'externalbrowser','user':'analyst@example.test'})
        self.assertEqual(cmd[cmd.index('--user')+1],'analyst@example.test')
        self.assertIn('--client-store-temporary-credential',cmd)
        self.assertNotIn('--oauth-enable-refresh-tokens',cmd)

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    def test_oauth_without_user_refused_before_login(self,_):
        with self.assertRaisesRegex(CLIError,'dashboard_snowflake_connect'):
            command({k:v for k,v in CONFIG.items() if k!='user'})

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    @patch('trident.snowflake_cli.subprocess.run')
    def test_sql_failure_does_not_ask_for_authentication(self,run,_):
        run.return_value=subprocess.CompletedProcess([],5,stdout='',stderr='SQL compilation error: private query text')
        with self.assertRaises(CLIError) as exc:
            execute(CONFIG,'SELECT 1',{})
        self.assertNotIsInstance(exc.exception,ConnectionPaused)
        self.assertIn('do not reconnect',str(exc.exception))
        self.assertNotIn('private query text',str(exc.exception))

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    @patch('trident.snowflake_cli.subprocess.run')
    def test_auth_failure_and_timeout_pause_without_leaking_diagnostics(self,run,_):
        run.return_value=subprocess.CompletedProcess([],3,stdout='',stderr='390318 OAuth access token expired: private diagnostic')
        with self.assertRaises(ConnectionPaused) as exc:
            execute(CONFIG,'SELECT 1',{})
        self.assertNotIn('private diagnostic',str(exc.exception))
        run.side_effect=subprocess.TimeoutExpired([],120)
        with self.assertRaisesRegex(ConnectionPaused,'does not prove'):
            execute(CONFIG,'SELECT 1',{})

class Reconnect051(unittest.TestCase):
    setUp=flow.EvalFlow.setUp
    call=flow.EvalFlow.call
    mutate=flow.EvalFlow.mutate
    task=flow.EvalFlow.task
    record=flow.EvalFlow.record
    @patch('trident.snowflake_cli.probe',return_value={'connected':True})
    def test_reconnect_retains_generation_questions_and_results(self,probe):
        self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.assertEqual(probe.call_count,1)
        task=self.task();self.record(task,17,17)
        generation=self.p['eval_plan']['generation'];before=copy.deepcopy(self.p['evals'])
        self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.assertEqual(probe.call_count,1)
        self.assertEqual(self.p['eval_plan']['generation'],generation)
        self.assertEqual(self.p['evals'],before)
        self.mutate('dashboard_snowflake_connect',connection={**CONFIG,'role':'OTHER_ANALYST'})
        self.assertEqual(self.p['eval_plan']['generation'],generation)
        self.assertEqual(self.p['summary']['checked_cases'],0)
        self.assertEqual(self.p['summary']['warehouse_portable'],0)

    @patch('trident.snowflake_cli.probe',return_value={'connected':True})
    @patch('trident.snowflake_cli.execute')
    def test_auth_failure_blocks_remaining_metrics_until_explicit_reconnect(self,execute,probe):
        self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.mutate('dashboard_translate',dialect='snowflake',mappings=[mapping()])
        execute.side_effect=ConnectionPaused('Authentication needs attention')
        self.mutate('dashboard_plan_evals')
        task=self.task()
        args={'job_id':self.job,'metric_id':self.p['metrics'][0]['id'],'context_hash':self.p['context_hash'],
              'purpose':'validation','case_id':task['case_id'],'plan_generation':task['plan_generation']}
        with self.assertRaises(ConnectionPaused):
            self.call('dashboard_snowflake_execute',args)
        self.service=type(self.service)(self.service.root)
        for _ in range(30):
            with self.assertRaisesRegex(ValueError,'paused'):
                self.call('dashboard_snowflake_execute',args)
        self.assertEqual(execute.call_count,1)
        self.p=self.call('dashboard_read',{'job_id':self.job})
        self.assertFalse(self.p['warehouse_connection']['connected'])
        self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.assertEqual(probe.call_count,2)
        execute.side_effect=None;execute.return_value=[{'value':17}]
        self.call('dashboard_snowflake_execute',args)
        self.assertEqual(execute.call_count,2)

    @patch('trident.snowflake_cli.probe',return_value={'connected':True})
    @patch('trident.snowflake_cli.execute',side_effect=CLIError('SQL rejected; do not reconnect'))
    def test_sql_error_does_not_invalidate_connection(self,execute,probe):
        self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.mutate('dashboard_translate',dialect='snowflake',mappings=[mapping()])
        self.mutate('dashboard_plan_evals')
        task=self.task()
        with self.assertRaises(CLIError):
            self.call('dashboard_snowflake_execute',{'job_id':self.job,'metric_id':self.p['metrics'][0]['id'],'context_hash':self.p['context_hash'],
                      'purpose':'validation','case_id':task['case_id'],'plan_generation':task['plan_generation']})
        state=self.call('dashboard_read',{'job_id':self.job})
        self.assertTrue(state['warehouse_connection']['connected'])
        self.mutate('dashboard_snowflake_connect',connection=CONFIG)
        self.assertEqual(probe.call_count,1)

    def test_default_view_does_not_repeat_sql_and_definitions_are_paged(self):
        state=self.service.load(self.job)
        for metric in state['metrics']:
            metric['translation']['sql']='SELECT 1 /*'+('x'*100000)+'*/'
        self.assertLess(len(json.dumps(self.service.view(state))),15000)
        self.assertNotIn('sql',self.service.view(state)['metrics'][0]['translation'])
        first=self.call('dashboard_read',{'job_id':self.job,'include_definitions':True})
        second=self.call('dashboard_read',{'job_id':self.job,'include_definitions':True,'definition_offset':1})
        self.assertEqual(first['definition_page']['next_offset'],1)
        self.assertNotEqual(first['definitions'][0]['id'],second['definitions'][0]['id'])

class ConnectionDiscovery(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from trident import snowflake_discovery as discovery
        self.discovery=discovery
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'scoped'
        patcher=patch.object(discovery,'profile_home',return_value=self.home)
        patcher.start();self.addCleanup(patcher.stop)
        self.scope={k:v for k,v in CONFIG.items() if k!='user'}
        patcher=patch('trident.snowflake_cli.discover',side_effect=AssertionError('validation must not discover an executable'))
        patcher.start();self.addCleanup(patcher.stop)

    def row(self,name='example',**values):
        return {'connection':name,'account':CONFIG['account'],'role':CONFIG['role'],
                'warehouse':CONFIG['warehouse'],'user':'example_identity',**values}

    @patch('trident.snowflake_discovery.connections')
    def test_unambiguous_profile_reused_without_login_or_user_prompt(self,listing):
        listing.return_value=[self.row()]
        result=self.discovery.resolve(self.scope,before_login=lambda:self.fail('unexpected login'))
        self.assertEqual(result['connection'],'example')
        self.assertNotIn('user',result)
        self.assertNotIn('_managed_profile',result)

    @patch('trident.snowflake_discovery.connections')
    def test_scope_matches_exact_account_role_and_warehouse(self,listing):
        listing.return_value=[self.row('wrong_account',account='another-account'),
                              self.row('wrong_role',role='OTHER_ROLE'),
                              self.row('wrong_warehouse',warehouse='OTHER_WH'),self.row('right')]
        self.assertEqual(self.discovery.resolve(self.scope)['connection'],'right')

    @patch('trident.snowflake_discovery.connections')
    def test_default_is_preferred_but_ambiguity_never_tries_each_identity(self,listing):
        listing.return_value=[self.row('first'),self.row('chosen',is_default=True)]
        self.assertEqual(self.discovery.resolve(self.scope)['connection'],'chosen')
        listing.return_value=[self.row('first'),self.row('second')]
        with self.assertRaisesRegex(CLIError,'Multiple matching'):
            self.discovery.resolve(self.scope,before_login=lambda:self.fail('unexpected login'))

    @patch('trident.snowflake_discovery.connections',side_effect=AssertionError('no new discovery'))
    def test_current_job_is_reused_without_listing(self,_):
        previous={**self.scope,'connection':'known','_managed_profile':True}
        self.assertEqual(self.discovery.resolve(self.scope,previous),previous)

    @patch('trident.snowflake_discovery.run_cli')
    def test_nested_cli_metadata_is_allowlisted(self,run):
        row={'connection_name':'example','parameters':{'account':'example-account','user':'example_identity',
             'password':'synthetic-private-value','private_key':'synthetic-key','new_secret_field':'withheld'},'is_default':True}
        run.return_value=json.dumps([row])
        result=self.discovery.connections()
        self.assertEqual(result,[{'connection':'example','is_default':True,'account':'example-account','user':'example_identity'}])
        self.assertIn('--all',run.call_args.args[0])

    @patch.dict('os.environ',{'SNOWFLAKE_ACCOUNT':'example-account','SNOWFLAKE_USER':'environment_identity'},clear=True)
    @patch('trident.snowflake_discovery.connections',return_value=[])
    def test_matching_environment_identity_is_used_without_guessing(self,_):
        self.assertEqual(self.discovery.resolve(self.scope)['user'],'environment_identity')

    @patch.dict('os.environ',{'SNOWFLAKE_ACCOUNT':'other-account','SNOWFLAKE_USER':'wrong_identity'},clear=True)
    @patch('trident.snowflake_discovery.run_cli',return_value='')
    @patch('trident.snowflake_discovery.connections')
    def test_username_free_browser_login_stays_in_private_cli_home(self,listing,run):
        listing.side_effect=[[],[self.row('signed_in')]]
        attempted=[]
        result=self.discovery.resolve(self.scope,before_login=lambda:attempted.append(True))
        self.assertEqual(attempted,[True])
        self.assertEqual(result['connection'],'signed_in')
        self.assertTrue(result['_managed_profile'])
        args=run.call_args.args[0]
        self.assertEqual(args[:3],['auth','login','example-account'])
        self.assertNotIn('--user',args)
        self.assertEqual(run.call_args.args[1],self.home)
        self.assertEqual((self.home/'config.toml').stat().st_mode & 0o777,0o600)
        env=self.discovery.environment(self.home)
        self.assertEqual(env['SNOWFLAKE_HOME'],str(self.home))
        self.assertNotIn('SNOWFLAKE_USER',env)

    @patch('trident.snowflake_discovery.run_cli')
    @patch('trident.snowflake_discovery.connections')
    def test_prior_managed_profile_reused_across_jobs(self,listing,run):
        self.home.mkdir()
        listing.side_effect=[[],[self.row('already_signed_in')]]
        result=self.discovery.resolve(self.scope)
        self.assertEqual(result['connection'],'already_signed_in')
        run.assert_not_called()

    @patch('trident.snowflake_discovery.run_cli',side_effect=CLIError('Unsupported CLI'))
    @patch('trident.snowflake_discovery.connections',return_value=[])
    @patch.dict('os.environ',{},clear=True)
    def test_old_cli_does_not_attempt_login_or_fall_back_to_username(self,listing,run):
        with self.assertRaisesRegex(CLIError,'Unsupported CLI'):
            self.discovery.resolve(self.scope,before_login=lambda:self.fail('unexpected login'))
        self.assertFalse(self.home.exists())

    @patch('trident.snowflake_discovery.discover',return_value='/approved/bin/snow')
    def test_managed_queries_explicitly_select_the_isolated_cli_config(self,_):
        args=command({**self.scope,'connection':'signed_in','_managed_profile':True})
        self.assertEqual(args[1:3],['--config-file',str(self.home/'config.toml')])
        self.assertEqual(args[args.index('--connection')+1],'signed_in')


class BrowserLoginWorkflow(unittest.TestCase):
    setUp=flow.EvalFlow.setUp
    call=flow.EvalFlow.call
    mutate=flow.EvalFlow.mutate

    @patch('trident.snowflake_cli.probe',return_value={'connected':True})
    @patch('trident.snowflake_discovery.resolve')
    def test_failed_login_is_not_repeated_without_explicit_retry(self,resolve,probe):
        def failed(config,previous,before_login):
            before_login()
            raise CLIError('Sign-in cancelled')
        resolve.side_effect=failed
        scope={k:v for k,v in CONFIG.items() if k!='user'}
        with self.assertRaisesRegex(CLIError,'cancelled'):
            self.mutate('dashboard_snowflake_connect',connection=scope)
        self.p=self.call('dashboard_read',{'job_id':self.job})
        with self.assertRaisesRegex(ValueError,'already attempted'):
            self.mutate('dashboard_snowflake_connect',connection=scope)
        probe.assert_not_called()
        with self.assertRaisesRegex(CLIError,'cancelled'):
            self.mutate('dashboard_snowflake_connect',connection=scope,retry_login=True)

class DiscoveredAccountProbe(unittest.TestCase):
    @patch('trident.snowflake_cli.execute')
    def test_probe_refuses_a_different_account_even_when_role_matches(self,execute):
        from trident.snowflake_cli import probe
        execute.return_value=[{'ROLE':'ANALYST','WAREHOUSE':'EXAMPLE_WH','SESSION_USER':'USER_OBJECT_NAME',
                               'ACCOUNT_LOCATOR':'XY12345','ORGANIZATION':'example','ACCOUNT_NAME':'account'}]
        self.assertTrue(probe(CONFIG)['connected'])
        with self.assertRaisesRegex(CLIError,'account does not match'):
            probe({**CONFIG,'account':'another-account'})
        self.assertNotIn('user',probe(CONFIG))

class DiscoveryEnvironmentSafety(unittest.TestCase):
    @patch.dict('os.environ',{'SNOWFLAKE_USER':'wrong_scope','SNOWFLAKE_PASSWORD':'synthetic_secret',
                            'SNOWFLAKE_CONNECTIONS_EXAMPLE_PASSWORD':'synthetic_secret',
                            'SNOWFLAKE_CLI_ENFORCE_STRICT_CONFIG_PERMISSIONS':'true'},clear=True)
    def test_scoped_login_keeps_security_settings_but_removes_connection_overrides(self):
        from pathlib import Path
        from trident.snowflake_discovery import environment
        env=environment(Path('/private/example'))
        self.assertNotIn('SNOWFLAKE_PASSWORD',env)
        self.assertNotIn('SNOWFLAKE_CONNECTIONS_EXAMPLE_PASSWORD',env)
        self.assertEqual(env['SNOWFLAKE_CLI_ENFORCE_STRICT_CONFIG_PERMISSIONS'],'true')
