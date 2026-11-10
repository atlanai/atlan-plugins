"""Actual template reader, using approved runtime dependencies and current owned source."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = os.environ.get('ANALYTICS_METRICS_RUNTIME')


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME for template reader acceptance.')
class PowerBITemplate(unittest.TestCase):
    def test_model_report_and_refusals(self):
        program = r'''
import json, tempfile, zipfile, warnings
from pathlib import Path
from unittest.mock import patch
from metrics_agent.plugs.readers import pbit
from metrics_agent.plugs.readers.powerbi import PowerBIReader
from metrics_agent.sockets import ReaderRefused

model={'model':{'name':'Shipping','tables':[{'name':'Orders','columns':[{'name':'amount','dataType':'decimal'}],
       'measures':[{'name':'Revenue','expression':'SUM(Orders[amount])'}]}]}}
layout={'sections':[{'name':'overview','displayName':'Shipping overview','visualContainers':[
    {'config':json.dumps({'name':'revenue-card','singleVisual':{'visualType':'card','title':'Revenue',
      'projections':{'Values':[{'queryRef':'Orders.Revenue'}]},
      'prototypeQuery':{'From':[{'Name':'o','Entity':'Orders'}],
      'Select':[{'Measure':{'Expression':{'SourceRef':{'Source':'o'}},'Property':'Revenue'},'Name':'Orders.Revenue'}]}}})}]}]}

def archive(path, schema, report=True, extra=None):
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('DataModelSchema',schema)
        if report:z.writestr('Report/Layout',json.dumps(layout).encode('utf-16-le'))
        if extra:z.writestr(*extra)

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); reader=PowerBIReader()
    for encoding in ('utf-8-sig','utf-16-le','utf-16'):
        path=root/(encoding+'.pbit'); archive(path,json.dumps(model).encode(encoding))
        assert reader.detect(path) and reader.form(path)=='pbit'
        facts=reader.parse(path,dialect='duckdb')
        assert len(facts['measures'])==1 and facts['measures'][0]['name']=='Revenue',facts
        assert facts['source_file']==str(path)
        assert any(u.get('dashboards') and 'Revenue' in u.get('measures',[]) for u in facts['usage']),facts['usage']
    path=root/'model-only.pbit'; archive(path,json.dumps(model),report=False)
    facts=reader.parse(path,dialect='duckdb')
    assert not any(u.get('dashboards') for u in facts['usage'])
    assert 'curation only' in facts['_reader_notes'][-1]
    for schema in ('not json','[]','{"model": []}','{"model": {}}'):
        path=root/'malformed.pbit';archive(path,schema)
        try:reader.parse(path)
        except ReaderRefused:pass
        else:raise AssertionError('Malformed template accepted')
    path=root/'duplicate.pbit'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        archive(path,json.dumps(model),extra=('DataModelSchema',json.dumps(model)))
    try:reader.parse(path)
    except ReaderRefused:pass
    else:raise AssertionError('Duplicate entry accepted')
    path=root/'oversized.pbit';archive(path,json.dumps(model))
    with patch.object(pbit,'MAX_SCHEMA_BYTES',5):
        try:reader.parse(path)
        except ReaderRefused:pass
        else:raise AssertionError('Oversized schema accepted')
    path=root/'traversal.pbit';archive(path,json.dumps(model),extra=('Report/definition/../../outside.json','{}'))
    try:reader.parse(path)
    except ValueError:pass
    else:raise AssertionError('Report path traversal accepted')
    assert not (root/'outside.json').exists()
print('Template model, visual placement, encodings, curation and archive refusals passed')
'''
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'plugins/atlanai-analytics/skills/analytics-metrics/modules/metrics/engine/src')}
        result = subprocess.run([str(Path(RUNTIME) / 'venv/bin/python'), '-c', program],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
