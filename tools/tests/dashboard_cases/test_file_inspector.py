import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('file_inspector',Path(__file__).resolve().parents[3]/'plugins/atlanai-analytics/skills/atlan-analytics/scripts/dashboard_runtime/scripts/inspect_powerbi.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class FileInventory(unittest.TestCase):
    def test_template_reads_all_pages_and_measures_without_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.pbit'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('Report/Layout',json.dumps({'sections':[{'name':'p1','displayName':'Overview'},{'name':'p2','displayName':'Details'}]}).encode('utf-16-le'))
                archive.writestr('DataModelSchema',json.dumps({'model':{'tables':[{'name':'Accounts','measures':[{'name':'Accounts','expression':'COUNTROWS(Accounts)'}]}],'dataSources':[{'connectionString':'excluded-fixture'}]}}))
                archive.writestr('Connections','excluded-fixture')
                archive.writestr('../outside','never extracted')
            result=module.inspect(path)
            self.assertEqual(result['counts']['pages'],2)
            self.assertEqual(result['counts']['measures'],1)
            self.assertNotIn('excluded-fixture',json.dumps(result))
            self.assertFalse(result['scope_complete'])
            self.assertFalse((Path(folder)/'outside').exists())

    def test_binary_pbix_reports_gap_not_invented_measures(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.pbix'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('DataModel',b'opaque')
                archive.writestr('Report/Layout','{"sections":[]}')
            result=module.inspect(path)
            self.assertFalse(result['model_metadata_available'])
            self.assertEqual(result['counts']['measures'],0)
            self.assertIn('bulk',result['next_step'])

    def test_pbir_pages_and_filters_are_read_without_external_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.pbix'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('DataModelSchema',json.dumps({'model':{'tables':[]}}))
                archive.writestr('Report/definition/report.json','{}')
                archive.writestr('Report/definition/pages/pages.json','{"pageOrder":["overview"]}')
                archive.writestr('Report/definition/pages/overview/page.json','{"name":"overview","displayName":"Overview"}')
                archive.writestr('Report/definition/pages/overview/visuals/metric/visual.json',json.dumps({'visual':{'visualType':'card','query':{'queryState':{'Values':{}}}},'filterConfig':{'filters':[]}}))
            result=module.inspect(path)
            self.assertEqual(result['counts']['pages'],1)
            self.assertEqual(result['pages'][0]['visuals'][0]['type'],'card')
            self.assertEqual(result['pages'][0]['visuals'][0]['filters'],{'filters':[]})

    def test_credential_shaped_expressions_are_withheld(self):
        result=module.sanitize({'power_query':[{'Expression':'Password="synthetic-fixture"'}]})
        self.assertNotIn('synthetic-fixture',json.dumps(result))
        self.assertIn('WITHHELD',json.dumps(result))

class ImportedWorkflow(unittest.TestCase):
    def setUp(self):
        from trident.core import Service
        import dashboard_fixture as fixture
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=Service(Path(self.tmp.name).resolve())
        self.state=self.service.dispatch('dashboard_begin',{'url':fixture.URL})
        self.state=self.service.dispatch('dashboard_identify',{'job_id':self.state['job_id'],'expected_revision':0,'source_url':fixture.URL,'title':'Example report','evidence_ref':'synthetic:file-download'})
        self.args={'job_id':self.state['job_id'],'expected_revision':self.state['revision'],'path':'/example.pbix','model_ref':'example-model','context':fixture.capture()['context']}
        self.inventory={'decoder':'pbixray/0.15.5','file_sha256':'a'*64,'counts':{'pages':2,'tables':1,'measures':1,'relationships':0},
                        'measures':[{'source_id':'1','table':'Accounts','name':'Accounts','expression':"COUNTROWS('Accounts')",'dataType':'integer'}]}

    def test_import_is_native_and_reaches_sql_and_eval_plan(self):
        from unittest.mock import patch
        from test_dax_sql import mapping
        with patch('trident.powerbi_file.inspect_file',return_value=self.inventory):
            p=self.service.dispatch('dashboard_import_file',self.args)
        self.assertEqual(p['summary']['measures'],1)
        self.assertFalse(p['scope_complete'])
        self.assertEqual(p['file_import']['counts']['pages'],2)
        with self.assertRaisesRegex(ValueError,'Complete the selected scope'):
            self.service.dispatch('dashboard_translate',{'job_id':p['job_id'],'expected_revision':p['revision'],'dialect':'sqlite','mappings':[mapping()]})
        with patch('trident.powerbi_file.inspect_file',return_value=self.inventory):
            p=self.service.dispatch('dashboard_import_file',{**self.args,'expected_revision':p['revision'],'scope_complete':True})
        p=self.service.dispatch('dashboard_translate',{'job_id':p['job_id'],'expected_revision':p['revision'],'dialect':'sqlite','mappings':[mapping()]})
        self.assertEqual(p['definitions'][0]['definition']['spec_version'],'1.0-draft.4')
        p=self.service.dispatch('dashboard_plan_evals',{'job_id':p['job_id'],'expected_revision':p['revision']})
        self.assertEqual(p['summary']['planned_cases'],1)
        self.assertEqual(p['evals'][0]['verdict'],'pending')
        self.assertEqual(p['agent_readiness']['state'],'not_ready')
        import sqlite3
        task=self.service.dispatch('dashboard_next_check',{'job_id':p['job_id']})['task']
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE warehouse_accounts(id,active,amount,source)')
            db.executemany('INSERT INTO warehouse_accounts VALUES(?,?,?,?)',[(1,1,10,'Direct'),(2,1,20,None),(3,1,30,'Partner')])
            actual=db.execute(task['warehouse_request']['sql'],task['warehouse_request']['parameters']).fetchone()[0]
        p=self.service.dispatch('dashboard_record_check',{'job_id':p['job_id'],'expected_revision':p['revision'],
            **{k:task[k] for k in ('case_id','plan_generation','capture_hash','definition_hash')},
            'reference':{'observation':{'value':2,'executor':'powerbi','context_hash':task['context_hash'],'evidence_ref':'synthetic:independent-baseline'}},
            'actual':{'observation':{'value':actual,'executor':'warehouse','context_hash':task['context_hash'],
                                   'query_hash':task['warehouse_request']['query_hash'],'evidence_ref':'synthetic:sqlite-execution'}}})
        self.assertEqual(p['agent_readiness']['state'],'ready')
        self.assertEqual(p['summary']['passed_cases'],1)
        self.assertTrue(p['answerable_questions'])

    def test_missing_datatype_preserves_original_job(self):
        from unittest.mock import patch
        self.inventory['measures'][0]['dataType']=None
        before=self.service.load(self.state['job_id'])
        with patch('trident.powerbi_file.inspect_file',return_value=self.inventory):
            with self.assertRaisesRegex(ValueError,'datatype'):
                self.service.dispatch('dashboard_import_file',self.args)
        self.assertEqual(before,self.service.load(self.state['job_id']))
