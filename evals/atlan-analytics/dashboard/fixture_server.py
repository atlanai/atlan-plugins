#!/usr/bin/env python3
"""Offline synthetic dashboard acceptance adapter. Never connects to a real service."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

URL = 'https://app.powerbi.com/groups/11111111-1111-1111-1111-111111111111/reports/22222222-2222-2222-2222-222222222222/Overview'

def setup(root):
    root.mkdir(parents=True, exist_ok=True)
    model = {'model': {'tables': [{'name': 'Accounts', 'columns': [
        {'name': 'Id', 'dataType': 'int64'}, {'name': 'Active', 'dataType': 'boolean'},
        {'name': 'Amount', 'dataType': 'double'}, {'name': 'Source', 'dataType': 'string'}],
        'measures': [
            {'name': 'Active accounts', 'expression': "CALCULATE(COUNTROWS('Accounts'), 'Accounts'[Active] = TRUE())", 'dataType': 'int64', 'lineageTag': 'active-accounts'},
            {'name': 'Total amount', 'expression': "SUM('Accounts'[Amount])", 'dataType': 'double', 'lineageTag': 'total-amount'},
            {'name': 'Incorrect total', 'expression': "SUM('Accounts'[Amount])", 'dataType': 'double', 'lineageTag': 'incorrect-total'},
            {'name': 'Account labels', 'expression': "CONCATENATEX('Accounts', 'Accounts'[Source])", 'dataType': 'string', 'lineageTag': 'account-labels'}]}]}}
    with zipfile.ZipFile(root/'example.pbit', 'w') as archive:
        archive.writestr('DataModelSchema', json.dumps(model))
        archive.writestr('Report/Layout', json.dumps({'sections':[{'name':'Overview','displayName':'Overview'}]}))
    db=sqlite3.connect(root/'warehouse.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS warehouse_accounts(id, active, amount, source)')
    if not db.execute('SELECT COUNT(*) FROM warehouse_accounts').fetchone()[0]:
        db.executemany('INSERT INTO warehouse_accounts VALUES(?,?,?,?)', [(1,1,10,'Direct'),(2,0,30,'Direct')])
    db.commit();db.close()
    if not (root/'registry.json').exists(): (root/'registry.json').write_text('{}')

def obj(props, required=()):
    return {'type':'object','properties':props,'required':list(required),'additionalProperties':False}

STRING={'type':'string'}
TOOLS=[
    {'name':'fixture_source','description':'Read the sanctioned synthetic report export, verified scope and physical mapping. Offline test only.', 'inputSchema':obj({})},
    {'name':'fixture_reference','description':'Observe an independent synthetic report value by measure name. Offline parity reference only.', 'inputSchema':obj({'measure':STRING},['measure'])},
    {'name':'fixture_query','description':'Execute one read-only SQL query with bound parameters against the synthetic SQLite warehouse.', 'inputSchema':obj({'sql':STRING,'parameters':{'type':'object'}},['sql','parameters'])},
    {'name':'fixture_registry','description':'Local Registry test adapter: read schema/inventory, create/read/release synthetic metrics and agents. Never reaches live Registry. Names: registry__get_kind_schema, registry__list_artifacts, registry__get_artifact, registry__create_artifact; fixture_release is test-only.', 'inputSchema':obj({'name':STRING,'arguments':{'type':'object'}},['name','arguments'])},
    {'name':'fixture_advance','description':'Advance only the synthetic warehouse amounts after validation to prove fresh answers. Offline test only.', 'inputSchema':obj({})},
]

def call(root, name, args):
    if name=='fixture_source':
        return {'url':URL,'title':'Example coverage report','path':str(root/'example.pbit'),'model_ref':'example-model',
                'scope_complete':True,'context':{'page':'Overview','filters':[{'table':'Accounts','column':'Source','values':['Direct']}],
                    'data_as_of':'2026-10-01','security_scope':'synthetic test caller'},
                'mapping':{'semantic_table':'Accounts','relation':['warehouse_accounts'],
                    'columns':[{'semantic':s,'physical':p,'datatype':t} for s,p,t in [('Id','id','integer'),('Active','active','boolean'),('Amount','amount','decimal'),('Source','source','string')]],
                    'rowset_equivalent':True,'security_preserved':True,'evidence_ref':'synthetic:model-mapping'}}
    if name=='fixture_reference':
        return {'unavailable':'snapshot_mismatch'} if args['measure']=='Total amount' else {'value':{'Active accounts':1,'Incorrect total':999}[args['measure']], 'evidence_ref':'synthetic:independent-source'}
    if name=='fixture_query':
        db=sqlite3.connect('file:'+str(root/'warehouse.sqlite')+'?mode=ro',uri=True)
        allowed={sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        db.set_authorizer(lambda op,*_: sqlite3.SQLITE_OK if op in allowed else sqlite3.SQLITE_DENY)
        try:
            cursor=db.execute(args['sql'],args['parameters'])
            return {'columns':[d[0] for d in cursor.description], 'rows':[list(row) for row in cursor.fetchmany(100)], 'evidence_ref':'synthetic:executed-sql'}
        finally:db.close()
    if name=='fixture_advance':
        db=sqlite3.connect(root/'warehouse.sqlite');db.execute('UPDATE warehouse_accounts SET amount=amount+5');db.commit();db.close()
        return {'advanced':True}
    if name=='fixture_registry':
        tool=args['name'];a=args['arguments'];path=root/'registry.json';state=json.loads(path.read_text())
        if tool=='registry__get_kind_schema':return {'kind':a['kind'],'status':'active','spec_version':'1.0-draft.4'}
        if tool=='registry__list_artifacts':return {'workspace_id':'workspace_demo','complete':True,'metric_schema':{'kind':'metric','status':'active'},'artifacts':[v for v in state.values() if v['kind']==a.get('kind','metric')]}
        if tool=='registry__create_artifact':
            body=a['metadata'];kind=a['kind']
            if body.get('workspace_id')!='workspace_demo':raise ValueError('wrong synthetic workspace')
            if any(v['name']==body['name'] and v['kind']==kind for v in state.values()):raise ValueError('duplicate synthetic artifact')
            key=kind+'_demo_'+str(len(state)+1);state[key]={'id':key,'kind':kind,'etag':'v1',**body};path.write_text(json.dumps(state));return state[key]
        if tool=='registry__get_artifact':
            item=state[a['artifact_id']]
            return item if a.get('channel')!='latest' or item.get('release_id') else {'not_released':True}
        if tool=='fixture_release':
            item=state[a['artifact_id']];item['release_id']='release_'+item['id'];path.write_text(json.dumps(state));return item
        raise ValueError('unsupported fixture Registry operation')
    raise ValueError('unknown fixture operation')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args()
    root=Path(args.root).resolve();setup(root)
    for line in sys.stdin:
        try:
            req=json.loads(line);method=req.get('method');ident=req.get('id')
            if ident is None:continue
            if method=='initialize':result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'dashboard-fixture','version':'1.0'}}
            elif method=='tools/list':result={'tools':TOOLS}
            elif method=='ping':result={}
            elif method=='tools/call':
                params=req['params'];value=call(root,params['name'],params.get('arguments',{}))
                with (root/'calls.jsonl').open('a') as log:log.write(json.dumps({'name':params['name'],'arguments':params.get('arguments',{}),'result':value})+'\n')
                result={'content':[{'type':'text','text':json.dumps(value)}]}
            else:raise ValueError('unsupported protocol method')
            response={'jsonrpc':'2.0','id':ident,'result':result}
        except Exception:
            response={'jsonrpc':'2.0','id':req.get('id'),'result':{'isError':True,'content':[{'type':'text','text':'Synthetic fixture request failed; inspect the allowed operation and inputs.'}]}}
        print(json.dumps(response),flush=True)

if __name__=='__main__':main()
