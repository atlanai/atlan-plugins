"""Local, offline file importer; parser runs in its explicitly installed runtime."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

VERSION='0.15.5'
RUNTIME=Path.home()/'.trident/runtimes'/('pbixray-'+VERSION)/'bin/python'

def inspect_file(path):
    source=Path(path).expanduser()
    if not source.is_absolute() or source.is_symlink() or not source.is_file() or source.suffix.lower() not in ('.pbix','.pbit','.bim'):
        raise ValueError('Supply the absolute path to the downloaded PBIX, PBIT or BIM file.')
    script=Path(__file__).resolve().parents[1]/'scripts/inspect_powerbi.py'
    with tempfile.TemporaryDirectory(prefix='trident-inventory-') as folder:
        target=Path(folder)/'inventory.json'
        try:
            run=subprocess.run([str(RUNTIME) if RUNTIME.is_file() else sys.executable,'-B',str(script),str(source),'--output',str(target)],
                               capture_output=True,text=True,timeout=120)
        except (OSError,subprocess.TimeoutExpired):
            raise ValueError('Local PBIX extraction could not complete within two minutes. Existing job state is unchanged.') from None
        if run.returncode or not target.is_file():
            raise ValueError('Local PBIX extraction failed. Check the file and installed parser; existing job state is unchanged.')
        if target.stat().st_size>4_000_000:
            raise ValueError('Extracted metadata exceeds the local bundle limit; narrow the selected source.')
        inventory=json.loads(target.read_text())
    if not inventory.get('model_metadata_available'):
        reason=inventory.get('binary_status')
        if reason == 'runtime_not_installed':
            raise ValueError('Binary Power BI metadata needs the approved PBIXRay runtime. Text PBIT and BIM metadata need no extra dependency.')
        if reason:
            raise ValueError('Local PBIX extraction failed. Check the file and installed parser; existing job state is unchanged.')
        raise ValueError('This file has no embedded readable model. Retrieve the linked semantic model through the existing authorized connection.')
    return inventory


def measure_type(measure, inventory):
    """Infer only the supported scalar AST from declared column types, never formatting."""
    from .dax_sql import Parser, TranslationGap
    types = {'int64':'integer', 'integer':'integer', 'double':'decimal', 'decimal':'decimal',
             'datetime':'timestamp', 'timestamp':'timestamp', 'date':'date',
             'string':'string', 'boolean':'boolean'}
    declared = types.get(str(measure.get('dataType', '')).lower())
    if declared:
        return declared
    columns = {(t['name'], c['name']): types.get(str(c.get('dataType', '')).lower())
               for t in inventory.get('tables', []) for c in t.get('columns', [])}
    def infer(node):
        if node[0] == 'var':
            return infer(node[2])
        if node[0] == 'column':
            return columns.get((node[1], node[2]))
        if node[0] == 'call':
            fn, args = node[1:]
            if fn in ('COUNTROWS', 'DISTINCTCOUNT') and len(args) == 1:
                return 'integer'
            if fn == 'CALCULATE' and args:
                return infer(args[0])
            if fn in ('SUM', 'AVERAGE', 'MIN', 'MAX', 'SELECTEDVALUE') and len(args) == 1:
                kind = infer(args[0])
                if fn in ('SUM', 'AVERAGE') and kind not in ('integer', 'decimal'):
                    return None
                return 'decimal' if fn == 'AVERAGE' and kind else kind
        if node[0] == 'literal' and type(node[1]) in (int, float):
            return 'integer' if type(node[1]) is int else 'decimal'
        if node[0] == 'op' and node[1] in ('+', '-'):
            kinds = (infer(node[2]), infer(node[3]))
            if all(k in ('integer', 'decimal') for k in kinds):
                return 'decimal' if 'decimal' in kinds else 'integer'
        return None
    try:
        return infer(Parser(measure.get('expression') or '').parse())
    except (TranslationGap, RecursionError, TypeError):
        return None

def capture_from_inventory(inventory,state,args):
    measures=[]
    types={'int64':'integer','double':'decimal','decimal':'decimal','datetime':'timestamp','boolean':'boolean',
           'string':'string','integer':'integer','timestamp':'timestamp','date':'date'}
    evidence='powerbi-file:sha256:'+inventory['file_sha256']
    for m in inventory['measures']:
        datatype=measure_type(m, inventory)
        if not datatype:
            raise ValueError('A file measure has no supported source datatype. Retrieve its declared model datatype before importing; no measures were silently dropped.')
        if not isinstance(m.get('expression'),str) or '[WITHHELD:' in m['expression']:
            raise ValueError('A measure expression is absent or withheld. Import stopped without creating incomplete definitions.')
        upstream=m.get('source_id') or m.get('lineageTag')
        measures.append({'source_id':str(upstream) if upstream else m['table']+'/'+m['name'],
                         'identity_kind':'upstream_id' if upstream else 'name_fallback',
                         'table':m['table'],'name':m['name'],'expression':m['expression'],'datatype':datatype,
                         'description':m.get('description') or '', 'evidence_ref':evidence})
    if not measures: raise ValueError('No measure definitions were found; an agent cannot be created from this inventory.')
    return {'source_url':state['source']['url'],'model_ref':args['model_ref'],'title':state['title'],
            'mode':'export','scope_complete':args.get('scope_complete',False),'context':args['context'],'measures':measures}
