"""Failure isolation, public errors and durable restart contracts, using synthetic data only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/atlan-analytics/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).parent / 'dashboard_cases'))
import dashboard
import dashboard_preflight
from dashboard_errors import FlowError, public_error
from dashboard_run import Run
from dashboard_publish import Publisher
from trident.core import Service
from trident.evaluation import answer_plan
from trident.snowflake_cli import CLIError
import test_eval_flow as fixture_flow
from dashboard_fixture import URL, capture
import legacy_snapshot


class AnswerEligibility(unittest.TestCase):
    setUp = fixture_flow.EvalFlow.setUp
    call = fixture_flow.EvalFlow.call
    mutate = fixture_flow.EvalFlow.mutate
    task = fixture_flow.EvalFlow.task
    record = fixture_flow.EvalFlow.record

    def test_failed_direct_answer_is_held_while_other_metric_remains_usable(self):
        bad = self.task()
        self.record(bad, 1, 999)
        good = self.task()
        self.record(good, 40, 40)
        state = self.service.load(self.job)
        self.assertEqual(answer_plan(state, bad['metric_id'], state['context_hash'])['state'], 'requires_validation')
        self.assertEqual(answer_plan(state, good['metric_id'], state['context_hash'])['state'], 'execute_warehouse')
        diagnostic = answer_plan(state, bad['metric_id'], state['context_hash'], 'validation',
                                 bad['case_id'], bad['plan_generation'])
        self.assertEqual(diagnostic['state'], 'execute_warehouse')
        with self.assertRaises(ValueError):
            answer_plan(state, bad['metric_id'], state['context_hash'], 'validation', bad['case_id'], 'stale')
        self.record(bad, 1, 1)
        state = self.service.load(self.job)
        self.assertEqual(answer_plan(state, bad['metric_id'], state['context_hash'])['state'], 'execute_warehouse')

    def test_snapshot_warning_is_answerable_and_pending_metric_is_not(self):
        task = self.task()
        self.mutate('dashboard_record_check', **{k:task[k] for k in
                    ('case_id','plan_generation','capture_hash','definition_hash')},
                    reference={'unavailable':'source_value_unavailable'}, actual={'unavailable':'snapshot_mismatch'})
        state = self.service.load(self.job)
        self.assertEqual(answer_plan(state, task['metric_id'], state['context_hash'])['state'], 'execute_warehouse')
        self.assertEqual(answer_plan(state, self.task()['metric_id'], state['context_hash'])['state'], 'requires_validation')

    @patch('trident.snowflake_cli.execute')
    def test_ordinary_snowflake_execution_never_calls_transport_for_failed_metric(self, execute):
        task = self.task()
        self.record(task, 1, 999)
        state = self.service.load(self.job)
        state['snowflake_connection'] = {'connection':'example','role':'ANALYST','warehouse':'EXAMPLE_WH'}
        self.service.write(self.service.path(self.job,'state.json'), state)
        with self.assertRaisesRegex(ValueError, 'needs correction'):
            self.call('dashboard_snowflake_execute', {'job_id':self.job,'metric_id':task['metric_id'],'context_hash':state['context_hash']})
        execute.assert_not_called()


class DurableRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.run = Run(self.root / 'run')
        self.first = self.run.start(URL)
        self.job = self.first['workflow']['job_id']
        self.service = Service(self.run.jobs)
        self.view = self.first['dashboard']
        self.change('dashboard_capture', capture=capture())
        self.view = legacy_snapshot.restore(self.service, self.job)
        self.change('dashboard_plan_evals')
        task = self.service.dispatch('dashboard_next_check',{'job_id':self.job})['task']
        self.change('dashboard_record_check', **{k:task[k] for k in
                    ('case_id','plan_generation','capture_hash','definition_hash')},
                    reference={'unavailable':'source_value_unavailable'}, actual={'unavailable':'snapshot_mismatch'})
        legacy_snapshot.context(self.service,self.job,self.run.context)

    def change(self, name, **args):
        self.view = self.service.dispatch(name, {'job_id':self.job,'expected_revision':self.view['revision'],**args})

    def test_restart_keeps_same_job_checks_and_questions(self):
        restarted = Run(self.run.root).start(URL)
        self.assertEqual(restarted['workflow']['job_id'],self.job)
        self.assertEqual(restarted['dashboard']['summary']['checked_cases'],1)
        self.assertTrue(restarted['workflow']['questions_available'])
        self.assertEqual(len(list(self.run.jobs.glob('job_*'))),1)
        self.assertLess(len(restarted['workflow']['display_summary'].split()),80)
        with self.assertRaises(FlowError):
            self.run.start(URL.replace('/Overview','/AnotherPage'))

    def test_publication_reconciles_actual_release_and_preserves_uncertain_write(self):
        publisher = Publisher(self.run.context)
        plan = publisher.plan({'workspace_id':'workspace_demo','complete':True,
                              'metric_schema':{'kind':'metric'},'artifacts':[]})
        write = publisher.next(plan['plan_id'])
        resumed = Run(self.run.root).status()['workflow']
        self.assertEqual(resumed['publication']['state'],'pending_readback')
        self.assertTrue(resumed['questions_available'])
        self.assertEqual(publisher.next(plan['plan_id'])['status'],'BLOCKED')
        body = json.loads((self.run.context/'metrics.json').read_text())[0]
        artifact = {**body,'kind':'metric','id':'metric_demo','workspace_id':'workspace_demo','etag':'v1'}
        publisher.ack({'operation_id':write['operation_id'],'read_succeeded':True,'artifact':artifact},recover=True)
        for phase in ('head','release'):
            op=publisher.next(plan['plan_id'])
            if phase=='release':
                with self.assertRaisesRegex(ValueError, 'selectors disagree'):
                    publisher.ack({'operation_id':op['operation_id'],'read_succeeded':True,
                                   'artifact':{**artifact,'release_id':'release_demo'},'release_id':'another_release'})
            publisher.ack({'operation_id':op['operation_id'],'read_succeeded':True,'artifact':artifact,
                           **({'release_id':'release_demo'} if phase=='release' else {})})
        status = Run(self.run.root).status()
        self.assertEqual(status['workflow']['publication']['state'],'released')
        self.assertEqual(status['dashboard']['registry_status'],'released')
        self.assertEqual(status['workflow']['label'],'Local metrics ready to try')
        # Changed captures cannot inherit an old release status.
        new=capture();new['context']['data_as_of']='2026-10-02'
        self.change('dashboard_capture',capture=new)
        self.assertEqual(self.run.status()['workflow']['publication']['state'],'needs_reconciliation')

    def test_agent_pending_readback_prevents_duplicate_registration(self):
        request={'workspace_id':'workspace_demo','name':'example-agent','description':'Example agent',
                 'instructions':'Answer from the approved metrics.'}
        op=self.run.begin_stage('agent',request)
        repeated=Run(self.run.root).begin_stage('agent',request)
        self.assertEqual(repeated['code'],'pending_operation')
        self.assertEqual(op['operation_id'],repeated['operation_id'])
        receipt={'operation_id':op['operation_id'],'read_succeeded':True,
                 'artifact':{'id':'agent_demo','kind':'agent',**request}}
        wrong=copy.deepcopy(receipt);wrong['artifact']['workspace_id']='workspace_other'
        with self.assertRaises(FlowError):self.run.acknowledge('agent',wrong)
        self.run.acknowledge('agent',receipt)
        self.assertEqual(Run(self.run.root).begin_stage('agent',request)['status'],'already_complete')
        self.assertEqual(self.run.status()['workflow']['stages']['agent']['id'],'agent_demo')

    def test_eval_result_is_bound_to_frozen_inputs_and_invalidated_on_context_change(self):
        request={'agent_id':'agent_demo','runner':'claude-question-only','dataset_hash':'example-dataset',
                 'context_snapshot_hash':'example-context'}
        op=self.run.begin_stage('heldout_eval',request)
        receipt={'operation_id':op['operation_id'],'read_succeeded':True,**request,
                 'passed':3,'scored':4,'unmeasured':2,'evidence_ref':'private:example-report'}
        bad={**receipt,'passed':5}
        with self.assertRaises(FlowError):self.run.acknowledge('heldout_eval',bad)
        self.run.acknowledge('heldout_eval',receipt)
        self.assertEqual(self.run.status()['dashboard']['summary']['heldout_accuracy_percent'],75)
        new=capture();new['context']['data_as_of']='2026-10-02'
        self.change('dashboard_capture',capture=new)
        status=self.run.status()
        self.assertEqual(status['workflow']['stages']['heldout_eval']['state'],'stale')
        self.assertIsNone(status['dashboard']['summary']['heldout_accuracy_percent'])

    def test_cli_error_is_actionable_and_persisted_without_private_input(self):
        request=self.root/'request.json'
        request.write_text(json.dumps({'capture':capture(),'expected_revision':0}))
        result=subprocess.run([sys.executable,str(SCRIPTS/'dashboard.py'),'--run-dir',str(self.run.root),
                               'call','dashboard_capture','--request',str(request)],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        message=json.loads(result.stdout)
        self.assertEqual(message['code'],'revision_conflict')
        self.assertEqual(self.run.status()['workflow']['last_issue']['code'],'revision_conflict')
        self.assertTrue(self.run.status()['workflow']['questions_available'])


    def test_resolved_error_is_archived_only_after_the_same_operation_succeeds(self):
        issue={**public_error(ValueError('Record the release selector returned by the latest read.')),
               'operation':'registry-ack:'}
        self.run.issue(issue)
        self.run.resolve_issue('registry-next:')
        self.assertEqual(self.run.manifest()['last_issue'],issue)
        self.run.resolve_issue('registry-ack:')
        self.assertIsNone(self.run.status()['workflow']['last_issue'])
        self.assertEqual(self.run.manifest()['resolved_issues'],[issue])


class PreflightAndErrors(unittest.TestCase):
    def test_public_errors_never_echo_exception_payloads(self):
        for exception in (ValueError('private source data'),OSError('private path'),CLIError('private diagnostic')):
            self.assertNotIn(str(exception),json.dumps(public_error(exception)))
        sql=CLIError('Snowflake rejected the SQL. Check the translation and object permissions; do not reconnect or request another login for this query error.')
        self.assertEqual(public_error(sql)['code'],'warehouse_sql_rejected')
        self.assertEqual(public_error(sql)['retry'],'after_correction')

    @patch('dashboard_preflight.probe')
    @patch('dashboard_preflight.shutil.which')
    def test_preflight_resolves_python_without_using_default_or_authenticating(self,which,probe):
        which.side_effect=lambda name: '/approved/'+name
        probe.side_effect=lambda cmd: '[3,9,6]' if cmd[0]==sys.executable else '[3,12,1]' if 'python3.' in cmd[0] else None
        result=dashboard_preflight.preflight()
        self.assertEqual(result['checks']['python']['version'],'3.12.1')
        self.assertFalse(result['network_auth_performed'])
        self.assertEqual(result['checks']['registry']['status'],'unknown')
        self.assertFalse(any('login' in c.args[0] for c in probe.call_args_list))


if __name__ == '__main__':
    unittest.main()
