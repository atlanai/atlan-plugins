#!/usr/bin/env python3
"""Inspect an explicitly supplied Power BI export locally; no network or archive extraction.

Read report layout and model metadata, decoding binary models with PBIXRay.
Connections and DataMashup are excluded from the decoder input. No model code runs.
"""
import argparse
import hashlib
import re
import shutil
import tempfile
import json
import os
from pathlib import Path
import zipfile

LIMIT = 32 * 1024 * 1024

def decode(raw):
    if len(raw) > LIMIT:
        raise ValueError('Metadata member exceeds the 32 MiB limit.')
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-16-le' if raw[:2] in (b'{\x00', b'[\x00') else 'utf-8-sig'
    return json.loads(raw.decode(encoding))

def fields(item, names):
    return {k:item[k] for k in names if k in item}

TYPES = {2:'string',6:'integer',8:'decimal',9:'timestamp',10:'decimal',11:'boolean'}
SECRET = re.compile(r'(?i)(bearer\s+[a-z0-9._-]{10,}|(?:password|pwd|access_token|client_secret|api_key|authorization)\s*[=:]|-----BEGIN .*PRIVATE KEY)')

def sanitize(value):
    if isinstance(value,str) and SECRET.search(value): return '[WITHHELD: credential-shaped content]'
    if isinstance(value,list): return [sanitize(v) for v in value]
    if isinstance(value,dict): return {k:sanitize(v) for k,v in value.items()}
    return value

def records(frame):
    return json.loads(frame.to_json(orient='records',date_format='iso'))

def binary_metadata(path):
    try:
        from pbixray import PBIXRay
        from importlib.metadata import version
        if version('pbixray') != '0.15.5': return None,'runtime_version_mismatch'
    except ImportError:
        return None,'runtime_not_installed'
    # A private model-only copy prevents automatic loading of Connections/DataMashup.
    # Only an archive member stream is copied: never extract arbitrary paths.
    try:
        with tempfile.TemporaryDirectory(prefix='trident-pbix-') as folder:
            model_path=Path(folder)/'model.pbix'
            with zipfile.ZipFile(path) as source, zipfile.ZipFile(model_path,'w') as target:
                with source.open('DataModel') as reader, target.open('DataModel','w',force_zip64=True) as writer:
                    shutil.copyfileobj(reader,writer,length=1024*1024)
            model_path.chmod(0o600)
            with PBIXRay(str(model_path),on_disk=True,temp_dir=folder) as model:
                # Version-pinned internal metadata query: public dax_measures omits IDs/types.
                measures=records(model._metadata.source._db.query(
                    'SELECT m.ID, t.Name AS TableName, m.Name, m.Description, m.DataType, '
                    'm.Expression, m.FormatString, m.LineageTag FROM Measure m '
                    'JOIN [Table] t ON t.ID=m.TableID'))
                tables=records(model.tmschema_tables)
                columns=records(model.tmschema_columns)
                partitions=records(model.tmschema_partitions)
                result={'tables':[], 'relationships':records(model.relationships),
                        'power_query':records(model.power_query),
                        'security':{'roles':records(model._metadata.source._db.query('SELECT ID,Name,Description FROM Role')),
                                    'table_permissions':records(model.rls),
                                    'column_permissions':records(model.ols)},
                        'partitions':[fields(r,('TableName','Name','Mode','Type','RefreshedTime')) for r in partitions]}
                for table in tables:
                    name=table['Name']
                    result['tables'].append({'name':name,
                        'columns':[{'name':c['Name'],'dataType':TYPES.get(c['DataType']),'sourceColumn':c.get('SourceColumn'),
                                    'expression':c.get('Expression'),'source_id':str(c['ID'])} for c in columns if c['TableName']==name],
                        'measures':[{'name':m['Name'],'expression':m['Expression'],'dataType':TYPES.get(m['DataType']),
                                     'description':m.get('Description') or '', 'formatString':m.get('FormatString'),
                                     'source_id':str(m['ID']),'lineageTag':m.get('LineageTag')} for m in measures if m['TableName']==name]})
                return sanitize(result),'decoded'
    except Exception:
        # Parser diagnostics may include source model values; never forward them.
        return None,'binary_decode_failed'

def pbir_layout(archive):
    names=archive.namelist(); prefix='Report/definition/'
    if prefix+'report.json' not in names: return None
    total=0
    def read(name):
        nonlocal total
        info=archive.getinfo(name);total+=info.file_size
        if total>LIMIT: raise ValueError('Report metadata exceeds the size limit.')
        return decode(archive.read(info))
    report=read(prefix+'report.json')
    result={'sections':[],'filters':report.get('filterConfig',{})}
    if prefix+'pages/pages.json' in names:
        result['page_order']=read(prefix+'pages/pages.json')
    for name in names:
        if not (name.startswith(prefix+'pages/') and name.endswith('/page.json')): continue
        data=read(name);page=fields(data,('name','displayName','visibility','pageType'))
        page['filters']=data.get('filterConfig',{});page['visualContainers']=[]
        for visual_name in names:
            if visual_name.startswith(name[:-len('page.json')]+'visuals/') and visual_name.endswith('/visual.json'):
                visual=read(visual_name);v=visual.get('visual',{})
                page['visualContainers'].append({'config':{'singleVisual':{'visualType':v.get('visualType'),
                    'projections':v.get('query',{}),'objects':v.get('objects',{})}},'filters':visual.get('filterConfig',{})})
        result['sections'].append(page)
    return result

def inspect(path):
    path = Path(path).expanduser()
    if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ('.pbix','.pbit','.bim'):
        raise ValueError('Supply an explicit regular .pbix, .pbit or model .bim file.')
    model = layout = None
    binary_status = None
    binary_model = False
    members = []
    if path.suffix.lower() == '.bim':
        if path.stat().st_size > LIMIT: raise ValueError('Model metadata exceeds the size limit.')
        model = decode(path.read_bytes())
    else:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 10000: raise ValueError('Archive has too many members.')
            names = archive.namelist()
            if len(names) != len(set(names)): raise ValueError('Archive has duplicate members.')
            binary_model = 'DataModel' in names
            if binary_model and archive.getinfo('DataModel').file_size > 1024*1024*1024:
                raise ValueError('Binary model exceeds the 1 GiB input limit.')
            for name, kind in [('DataModelSchema','model'),('model.bim','model'),('Report/Layout','layout')]:
                if name not in names: continue
                info = archive.getinfo(name)
                if info.file_size > LIMIT: raise ValueError('Metadata member exceeds the size limit.')
                data = decode(archive.read(info))
                members.append(name)
                if kind == 'model': model = data
                else: layout = data
            if layout is None: layout = pbir_layout(archive)
        if model is None and binary_model:
            model,binary_status = binary_metadata(path)
    out = {'format':'trident-powerbi-file-inventory/1','file':str(path.resolve()),'members_read':members,
           'pages':[], 'tables':[], 'measures':[], 'relationships':[],
           'scope_complete':False,'reference_values_included':False,
           'notes':['Saved report defaults are not proof of the active browser filters, caller security scope or data cutoff.',
                    'This inventory is source metadata, not measured accuracy. Obtain independent source reference answers.',
                    'Connection credentials, DataMashup and row data are not emitted. Power Query is source evidence only; it is never executed.']}
    if isinstance(layout,dict):
        out['report_filters'] = layout.get('filters',[])
        for section in layout.get('sections',[]):
            page = fields(section,('name','displayName','filters','visibility','pageType'))
            page['visuals'] = []
            for visual in section.get('visualContainers',[]):
                config = visual.get('config',{})
                if isinstance(config,str):
                    try: config = json.loads(config)
                    except ValueError: config = {}
                single = config.get('singleVisual',{})
                page['visuals'].append({'type':single.get('visualType'),
                    'projections':single.get('projections',{}),'objects':single.get('objects',{}),'filters':visual.get('filters',[])})
            out['pages'].append(page)
    if isinstance(model,dict):
        model = model.get('model',model)
        for table in model.get('tables',[]):
            out['tables'].append({'name':table.get('name'),'columns':[fields(c,('name','dataType','sourceColumn','expression','source_id')) for c in table.get('columns',[])]})
            for measure in table.get('measures',[]):
                out['measures'].append({'table':table.get('name'),**fields(measure,('name','expression','dataType','description','formatString','lineageTag','source_id'))})
        out['relationships'] = model.get('relationships',[])
        out['power_query'] = model.get('power_query',[])
        out['security'] = model.get('security',{})
        out['partitions'] = model.get('partitions',[])
        out['security_roles_present'] = bool(model.get('roles') or out['security'].get('roles'))
    out['model_metadata_available'] = model is not None
    out['binary_status'] = binary_status
    out['decoder'] = 'pbixray/0.15.5' if binary_status == 'decoded' else 'text_metadata'
    with path.open('rb') as stream:
        sha=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024),b''): sha.update(block)
    out['file_sha256']=sha.hexdigest()
    if model is None:
        out['next_step'] = ('Binary decode is unavailable; check binary_status and the installed PBIXRay runtime. Use a sanctioned export or one bulk metadata query only if decoding cannot be completed.' if binary_model else 'Report-only export: retrieve the linked semantic model metadata once through an authorized connector or model query.')
    else: out['next_step'] = 'Use the extracted model and page inventory; verify only the selected active context and independent reference answers.'
    out['counts'] = {key:len(out[key]) for key in ('pages','tables','measures','relationships')}
    return sanitize(out)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.file)
        # Do not overwrite any existing file or follow an output symlink.
        fd = os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as target: json.dump(result,target,ensure_ascii=False,indent=2)
        print(json.dumps({'inventory_file':str(args.output.resolve()),'counts':result['counts'],
                          'model_metadata_available':result['model_metadata_available'],'next_step':result['next_step']}))
    except (ValueError,OSError,zipfile.BadZipFile,KeyError,TypeError,AttributeError):
        parser.exit(1,'Could not inspect the export. Use a valid supported file and a new output path; nothing was executed or uploaded.\n')
