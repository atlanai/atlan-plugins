"""Current Registry wire contract and bounded schema parsing, without network."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugins/atlanai-analytics/skills/atlan-analytics/scripts'))
from registry_inspection import parse_response, inspect_contract, ReadCLI

TARGET={'workspace_id':'workspace_example','gateway_origin':'https://registry.example.invalid'}

class Inspector(unittest.TestCase):
    def setUp(self):
        nested={'type':'string'}
        for _ in range(16): nested={'properties':{'field':nested}}
        self.kind={'kind':'metric','status':'active','manifest':{'properties':{'definition':nested}}}
        self.workspace={'id':'workspace_example','workspace_status':'active','permissions':{'can_create_content':True}}
        self.calls=[]
    def api(self,target,method,path,fields=()):
        self.assertEqual(target,TARGET);self.assertEqual(method,'get');self.calls.append(path)
        return parse_response(json.dumps(self.kind if path=='/kinds/metric' else self.workspace))
    def test_current_status_and_deep_schema_use_direct_kind_read(self):
        # This realistic schema nesting failed the old user-input depth limit.
        nested={'type':'string'}
        for _ in range(8): nested={'properties':{'field':nested}}
        self.kind['manifest']['properties']['definition']=nested
        result=inspect_contract(self,TARGET)
        self.assertEqual(result['write_permission'],'observed_allowed')
        self.assertEqual(self.calls,['/kinds/metric','/workspaces/workspace_example'])
    def test_permission_is_not_inferred_from_workspace_existence(self):
        self.kind['manifest']['properties']['definition']={}
        for permissions in ({},{'can_create_content':False},{'can_create_content':'true'}):
            self.workspace['permissions']=permissions
            self.assertEqual(inspect_contract(self,TARGET)['write_permission'],'unverified_or_denied')
    def test_wrong_workspace_and_conflicting_status_are_refused(self):
        self.kind['manifest']['properties']['definition']={}
        self.workspace['id']='workspace_other'
        with self.assertRaisesRegex(ValueError,'workspace_contract'):inspect_contract(self,TARGET)
        self.kind['kind_status']='inactive'
        with self.assertRaisesRegex(ValueError,'metric_unsupported'):inspect_contract(self,TARGET)
    def test_bounded_response_and_ambiguous_json_remain_refused(self):
        for raw in ('{"a":1,"a":2}','{"a":NaN}','['*40+'0'+']'*40,'"'+'x'*2_000_000+'"'):
            with self.assertRaises(ValueError):parse_response(raw)
    def test_read_cli_refuses_other_destinations_before_a_call(self):
        client=ReadCLI();client.run=lambda args:self.fail('unexpected CLI call')
        for method,path in [('post','/kinds/metric'),('get','/workspaces/workspace_other'),('get','https://other.invalid')]:
            with self.assertRaises(ValueError):client.api(TARGET,method,path)
