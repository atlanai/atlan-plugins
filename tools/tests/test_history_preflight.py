"""Local filtering and engine admission use one identifying-literal policy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'plugins/atlanai-analytics/skills/analytics-build/scripts'
sys.path.insert(0, str(SCRIPTS))
import analytics_eval

RUNTIME = os.environ.get('ANALYTICS_METRICS_RUNTIME')


class HistoryPreflight(unittest.TestCase):
    def test_local_filter_drops_before_shapes_and_reports_only_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / 'input.jsonl', Path(tmp) / 'clean.jsonl'
            src.write_text('\n'.join(json.dumps({'query_text': sql}) for sql in [
                'SELECT SUM(amount) FROM orders',
                "SELECT SUM(amount) FROM orders WHERE owner = 'Synthetic Person'",
                "SELECT SUM(amount) FROM orders WHERE status = 'Closed Won'"]))
            result = analytics_eval.filter_querylog(str(src), str(dst))
            self.assertEqual((result['kept'], result['dropped']), (2, 1))
            self.assertNotIn('Synthetic Person', json.dumps(result) + dst.read_text())

    @unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME for engine preflight acceptance.')
    def test_engine_filters_before_any_authoring_gate(self):
        program = r'''
import json,sys,tempfile,subprocess
from pathlib import Path
import duckdb
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); database=root/'demo.duckdb'; history=root/'history.jsonl'; run=root/'run'
    db=duckdb.connect(str(database));db.execute('CREATE TABLE orders(amount INTEGER, owner VARCHAR)');db.execute("INSERT INTO orders VALUES (10, 'team')");db.close()
    rows=[{'query_text':sql,'exec_count':30,'user_count':3} for sql in [
        'SELECT SUM(amount) FROM demo.main.orders',
        "SELECT SUM(amount) FROM demo.main.orders WHERE owner = 'Synthetic Person'"]]
    history.write_text(chr(10).join(json.dumps(row) for row in rows)+chr(10))
    config={'version':1,'scope':'synthetic-preflight','dialect':'duckdb','mode':'standalone',
      'inputs':{'history':['local-files'],'schema':['local-files'],'bi':[],'sql_check':['local-files']},
      'settings':{'local-files':{'history':str(history),'database':str(database),'witness_mode':'execute'}},
      'tables':['demo.main.orders'],'publish':['files'],'observers':[]}
    path=root/'input.json';path.write_text(json.dumps(config))
    result=subprocess.run([sys.executable,'-m','metrics_agent.cli','run','--run',str(run),'--config',str(path)],capture_output=True,text=True,timeout=40)
    assert result.returncode==3,(result.stdout,result.stderr)
    corpus=(run/'query_corpus.jsonl').read_text()
    assert len(corpus.strip().splitlines())==1,corpus
    assert 'Synthetic Person' not in corpus
    for prompt in run.glob('*_prompt.json'):
        assert 'Synthetic Person' not in prompt.read_text(),prompt.name
    manifest=(run/'manifest.json').read_text()
    assert 'dropped_privacy_person_name' in manifest,manifest
print('History privacy refusal precedes the first authoring gate')
'''
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'plugins/atlanai-analytics/modules/metrics/engine/src')}
        result = subprocess.run([str(Path(RUNTIME) / 'venv/bin/python'), '-c', program],
                                env=env, capture_output=True, text=True, timeout=50)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
