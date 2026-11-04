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
        result=self.call('dashboard_snowflake_execute',{'job_id':self.job,'metric_id':p['metrics'][0]['id'],'context_hash':p['context_hash']})
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
        with self.assertRaisesRegex(CLIError,'login name'):
            command({k:v for k,v in {**CONFIG,'authenticator':'externalbrowser'}.items() if k!='user'})
        cmd=command({**CONFIG,'authenticator':'externalbrowser','user':'analyst@example.test'})
        self.assertEqual(cmd[cmd.index('--user')+1],'analyst@example.test')
        self.assertIn('--client-store-temporary-credential',cmd)
        self.assertNotIn('--oauth-enable-refresh-tokens',cmd)

    @patch('trident.snowflake_cli.discover',return_value='/approved/bin/snow')
    def test_oauth_without_user_refused_before_login(self,_):
        with self.assertRaisesRegex(CLIError,'login name once'):
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
        args={'job_id':self.job,'metric_id':self.p['metrics'][0]['id'],'context_hash':self.p['context_hash']}
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
        with self.assertRaises(CLIError):
            self.call('dashboard_snowflake_execute',{'job_id':self.job,'metric_id':self.p['metrics'][0]['id'],'context_hash':self.p['context_hash']})
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
