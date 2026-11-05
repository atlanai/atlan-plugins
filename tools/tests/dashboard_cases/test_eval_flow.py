import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from trident.core import Service
from trident.contracts import validate
import dashboard_fixture as fixture
from test_dax_sql import mapping


class EvalFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.service = Service(Path(self.tmp.name).resolve())
        self.p = self.call('dashboard_begin', {'url': fixture.URL}); self.job = self.p['job_id']
        cap = fixture.capture()
        other = copy.deepcopy(cap['measures'][0]); other.update(source_id='second', name='Total amount', expression="SUM('Accounts'[Amount])", datatype='decimal')
        cap['measures'].append(other)
        self.mutate('dashboard_capture', capture=cap)
        self.mutate('dashboard_translate', dialect='sqlite', mappings=[mapping()])
        self.mutate('dashboard_plan_evals')

    def call(self, name, args):
        validate(name, args)
        return self.service.dispatch(name, args)

    def mutate(self, name, **args):
        self.p = self.call(name, {'job_id':self.job, 'expected_revision':self.p['revision'], **args})
        return self.p

    def task(self, case_id=None):
        return self.call('dashboard_next_check', {'job_id':self.job, **({'case_id':case_id} if case_id else {})})['task']

    def record(self, task, expected, actual_value, **overrides):
        args = {'plan_generation':task['plan_generation'], 'case_id':task['case_id'], 'capture_hash':task['capture_hash'], 'definition_hash':task['definition_hash'],
            'reference':{'observation':{'value':expected,'executor':'powerbi','context_hash':task['context_hash'],'evidence_ref':'synthetic:source'}},
            'actual':{'observation':{'value':actual_value,'executor':'warehouse','context_hash':task['context_hash'],
                                   'query_hash':task['warehouse_request']['query_hash'],'evidence_ref':'synthetic:warehouse'}}}
        args.update(overrides)
        return self.mutate('dashboard_record_check', **args)

    def test_full_flow_executes_sql_and_routes_fresh_answers(self):
        self.assertEqual(self.p['summary']['planned_cases'],2)
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        db.execute('CREATE TABLE warehouse_accounts(id,active,amount,source)')
        db.executemany('INSERT INTO warehouse_accounts VALUES(?,?,?,?)', [(1,1,10,'Direct'),(2,0,30,'Direct')])
        for expected in (1,40):
            task = self.task(); req=task['warehouse_request']
            value=db.execute(req['sql'],req['parameters']).fetchone()[0]
            self.record(task,expected,value)
        self.assertEqual(self.p['status'],'evaluated')
        self.assertEqual(self.p['summary']['passed_cases'],2)
        self.assertEqual(len(self.p['suggested_questions']),2)
        self.assertEqual(self.task()['state'],'no_pending_checks')
        q=self.p['suggested_questions'][1]
        answer=self.call('dashboard_answer_plan',{'job_id':self.job,'metric_id':q['metric_id'],'context_hash':self.p['context_hash']})['answer_plan']
        self.assertEqual(answer['validation'],'passed_at_captured_scope')
        db.execute('UPDATE warehouse_accounts SET amount=amount+5')
        query=answer['query']
        self.assertEqual(db.execute(query['sql'],query['parameters']).fetchone()[0],50)
        self.assertNotIn('reference',answer)
        self.assertIsNone(self.p['summary']['heldout_accuracy_percent'])
        self.assertEqual(self.p['agent_readiness']['state'],'ready')

    def test_build_precedes_evals_and_preserves_native_business_fields(self):
        cap=fixture.capture()
        cap['measures'][0]['business_context']={'population':'Accounts selected in the report',
            'grain':'warehouse_accounts.id', 'unit':'accounts', 'exclusions':['Inactive accounts'],
            'evidence_ref':'synthetic:model-doc'}
        self.mutate('dashboard_capture',capture=cap)
        self.assertEqual(self.p['next_action'],'map_and_translate_to_sql')
        with self.assertRaisesRegex(ValueError,'Build warehouse metric'):
            self.mutate('dashboard_plan_evals')
        built=self.mutate('dashboard_translate',dialect='sqlite',mappings=[mapping()])
        self.assertEqual(built['next_action'],'create_eval_plan')
        d=built['definitions'][0]['definition']
        self.assertEqual(d['spec_version'],'1.0-draft.4')
        self.assertEqual(d['output']['unit'],'accounts')
        self.assertEqual(d['grain'],'warehouse_accounts.id')
        self.assertEqual(d['population'],'Accounts selected in the report')
        self.assertNotIn('domain',d)
        self.assertEqual(built['agent_readiness']['state'],'not_ready')
        exported=self.mutate('dashboard_export')
        envelope=json.loads(Path(exported['export']['files']['metric-envelopes.json']).read_text())[0]
        self.assertEqual(envelope['description'],cap['measures'][0]['description'])
        self.assertEqual(envelope['definition'],d)

    def test_authored_question_flows_to_picker_and_old_tasks_are_refused(self):
        old=self.task()
        question='How many active accounts are in this dashboard?'
        self.mutate('dashboard_plan_evals',questions=[{'metric_id':old['metric_id'],
            'question':question,'evidence_ref':'synthetic:visual-title'}])
        with self.assertRaisesRegex(ValueError,'plan changed'):
            self.record(old,17,17)
        task=self.task();self.assertEqual(task['question'],question)
        self.record(task,17,17)
        self.record(self.task(),40,40)
        self.assertEqual(self.p['answerable_questions'][0]['question'],question)
        self.assertEqual(self.p['agent_readiness']['state'],'ready')

    def test_question_plan_refuses_unknown_and_duplicate_metric(self):
        q={'metric_id':self.p['metrics'][0]['id'],'question':'Synthetic question?',
           'evidence_ref':'synthetic:visual'}
        for questions in [[q,q],[{**q,'metric_id':'unknown'}]]:
            with self.assertRaises(ValueError):self.mutate('dashboard_plan_evals',questions=questions)

    def test_picker_includes_every_checked_question_not_only_three(self):
        cap=fixture.capture();base=cap['measures'][0]
        cap['measures']=[{**base,'source_id':f'm-{i}','name':f'Synthetic metric {i}'} for i in range(4)]
        self.mutate('dashboard_capture',capture=cap)
        self.mutate('dashboard_translate',dialect='sqlite',mappings=[mapping()])
        self.mutate('dashboard_plan_evals')
        for _ in range(4):self.record(self.task(),17,17)
        self.assertEqual(len(self.p['suggested_questions']),3)
        self.assertEqual(len(self.p['answerable_questions']),4)
        self.assertEqual(self.p['agent_readiness']['state'],'ready')

    def test_ready_is_not_claimed_for_partial_unavailable_or_old_style_scores(self):
        self.record(self.task(),17,17)
        self.assertEqual(self.p['agent_readiness']['state'],'not_ready')
        self.record(self.task(),40,None,actual={'unavailable':'snapshot_mismatch'})
        self.assertEqual(self.p['agent_readiness']['state'],'ready_with_warnings')
        self.assertEqual(len(self.p['answerable_questions']),2)
        self.mutate('dashboard_translate',dialect='sqlite',mappings=[])
        self.assertEqual(self.p['agent_readiness']['state'],'not_ready')
        self.assertEqual(self.p['answerable_questions'],[])

    def test_snapshot_gaps_continue_to_context_and_fresh_questions_without_a_score(self):
        for _ in range(2):
            self.record(self.task(),17,None,actual={'unavailable':'snapshot_mismatch'})
        self.assertEqual(self.p['next_action'],'save_context_and_ask')
        self.assertEqual(self.p['agent_readiness']['state'],'ready_with_warnings')
        self.assertFalse(self.p['agent_readiness']['validation_complete'])
        self.assertEqual(len(self.p['answerable_questions']),2)
        self.assertEqual(self.p['summary']['passed_cases'],0)
        self.assertEqual(self.p['summary']['warehouse_portable'],0)
        self.assertIsNone(self.p['summary']['parity_percent'])
        import dashboard
        context=Path(self.tmp.name).resolve()/'context'
        dashboard.prepare_context(self.service,self.job,context)
        saved=json.loads((context/'metrics.json').read_text())
        self.assertEqual(len(saved),2)
        validation=saved[0]['definition']['extensions']['atlan_analytics']['dashboard_validation']
        self.assertEqual(validation['parity'],'unmeasured')
        self.assertEqual(validation['reasons'],['snapshot_mismatch'])
        question=self.p['suggested_questions'][0]
        answer=self.call('dashboard_answer_plan',{'job_id':self.job,'metric_id':question['metric_id'],
                         'context_hash':self.p['context_hash']})['answer_plan']
        self.assertEqual(answer['state'],'execute_warehouse')
        self.assertEqual(answer['data_policy'],'fresh_warehouse_result')
        self.assertEqual(answer['validation'],'unverified')
        self.assertNotIn('reference',answer)

    def test_source_gaps_allow_questions_but_execution_and_scope_gaps_do_not(self):
        for reason, count in [('source_value_unavailable',2),('source_connection_required',2),
                              ('warehouse_connection_required',0),('safe_binding_unavailable',0)]:
            self.mutate('dashboard_plan_evals')
            for _ in range(2):
                self.record(self.task(),17,None,actual={'unavailable':reason})
            self.assertEqual(len(self.p['answerable_questions']),count,reason)

    def test_failed_retry_preserves_other_cases_and_denominator(self):
        first=self.task();self.record(first,17,16)
        self.assertEqual(self.p['summary']['pending_cases'],1)
        self.assertEqual(self.p['status'],'evaluating')
        self.record(self.task(),40,40)
        self.assertEqual(self.p['summary']['planned_cases'],2)
        self.assertEqual(self.p['summary']['passed_cases'],1)
        self.assertEqual(len(self.p['suggested_questions']),1)
        self.record(self.task(first['case_id']),17,17)
        self.assertEqual(self.p['summary']['passed_cases'],2)

    def test_unavailable_is_unmeasured_not_passed_or_failed(self):
        task=self.task()
        self.record(task,17,None,actual={'unavailable':'warehouse_connection_required'})
        self.assertEqual(self.p['evals'][0]['verdict'],'warehouse_connection_required')
        self.assertEqual(self.p['summary']['measured_cases'],0)
        self.assertEqual(self.p['summary']['warehouse_portable'],0)
        self.assertEqual(self.p['suggested_questions'],[])

    def test_gold_never_returned_or_exported(self):
        task=self.task();secret_synthetic_value=987654321234
        self.record(task,secret_synthetic_value,secret_synthetic_value)
        exported=self.mutate('dashboard_export')
        for name,path in exported['export']['files'].items():
            self.assertNotIn(str(secret_synthetic_value),Path(path).read_text())
        for packet in [self.p, self.call('dashboard_read',{'job_id':self.job,'include_definitions':True}),self.task()]:
            self.assertNotIn(str(secret_synthetic_value),json.dumps(packet))
        rows=json.loads(Path(exported['export']['files']['eval-record-drafts.json']).read_text())
        self.assertEqual(len(rows),2)

    def test_replan_invalidates_receipts_even_with_same_definitions(self):
        first=self.task();self.record(first,17,17)
        self.mutate('dashboard_plan_evals')
        self.assertEqual(self.p['summary']['passed_cases'],0)
        second=self.p['evals'][1]['case_id']
        self.record(self.task(second),40,40)
        self.assertEqual(self.p['summary']['checked_cases'],1)
        self.assertEqual(self.p['summary']['pending_cases'],1)
        self.assertEqual(self.p['summary']['warehouse_portable'],1)

    def test_translation_invalidates_tasks_and_answer_scope(self):
        task=self.task();self.record(task,17,17)
        self.mutate('dashboard_translate',dialect='postgres',mappings=[mapping()])
        with self.assertRaises(ValueError): self.record(task,17,17)
        answer=self.call('dashboard_answer_plan',{'job_id':self.job,'metric_id':task['metric_id'],'context_hash':'wrong'})['answer_plan']
        self.assertEqual(answer['state'],'needs_new_scope')
        self.assertEqual(self.p['summary']['passed_cases'],0)

    def test_unsupported_measures_stay_in_total(self):
        cap=fixture.capture();extra=copy.deepcopy(cap['measures'][0]);extra.update(source_id='unsupported',expression='[Other Measure]',name='Other')
        cap['measures'].append(extra)
        self.mutate('dashboard_capture',capture=cap)
        self.mutate('dashboard_translate',dialect='sqlite',mappings=[mapping()]);self.mutate('dashboard_plan_evals')
        self.record(self.task(),17,17)
        self.assertEqual(self.p['status'],'evaluated')
        self.assertEqual(self.p['summary']['planned_cases'],2)
        self.assertEqual(self.p['summary']['passed_cases'],1)
        self.assertEqual(self.p['evals'][1]['verdict'],'needs_sql')

    def test_recording_rejects_ambiguous_and_unbound_evidence(self):
        task=self.task()
        with self.assertRaises(ValueError):
            self.record(task,17,17,actual={'observation':{'value':17,'context_hash':task['context_hash'],'evidence_ref':'synthetic'},'unavailable':'snapshot_mismatch'})
        self.record(task,17,17,actual={'observation':{'value':17,'executor':'warehouse','context_hash':task['context_hash'],'query_hash':'wrong','evidence_ref':'synthetic'}})
        self.assertEqual(self.p['evals'][0]['verdict'],'warehouse_query_not_bound')

    def test_no_model_or_gold_needed_for_plan_and_export(self):
        self.mutate('dashboard_plan_evals')
        self.assertTrue(all(c['question'] for c in self.p['evals']) and bool(self.p['context_hash']))
        self.assertEqual(self.p['summary']['measured_cases'],0)
        self.assertEqual(self.p['summary']['planned_cases'],2)
