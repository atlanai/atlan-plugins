import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('file_inspector',Path(__file__).resolve().parents[3]/'plugins/atlanai-analytics/skills/analytics-build/scripts/dashboard_runtime/scripts/inspect_powerbi.py')
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
