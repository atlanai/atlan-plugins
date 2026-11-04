import unittest
import tempfile
from pathlib import Path
from trident.core import Service
from trident.contracts import validate
URL = 'https://app.powerbi.com/groups/11111111-1111-1111-1111-111111111111/reports/22222222-2222-2222-2222-222222222222/Overview'


def capture():
    return {'source_url': URL, 'model_ref': 'example-model', 'title': 'Example coverage report', 'mode': 'browser',
        'scope_complete': True, 'context': {'page': 'Overview', 'filters': [{'table': 'Accounts', 'column': 'Source', 'values': ['Direct', None]}],
            'data_as_of': '2026-10-01', 'security_scope': 'current signed-in caller'},
        'measures': [{'source_id': 'measure-1', 'identity_kind': 'upstream_id', 'name': 'Active accounts', 'table': 'Accounts',
            'expression': "CALCULATE(DISTINCTCOUNT('Accounts'[ID]), 'Accounts'[Active] = TRUE())", 'datatype': 'integer',
            'evidence_ref': 'synthetic:model-metadata:1', 'description': 'Synthetic active-account count.'}]}


class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = Service(Path(self.tmp.name).resolve())
        self.begin = self.call('dashboard_begin', {'url': URL})
        self.job = self.begin['job_id']

    def call(self, name, args):
        validate(name, args)
        return self.service.dispatch(name, args)

    def prepared(self, data=None):
        return self.call('dashboard_capture', {'job_id': self.job, 'expected_revision': 0, 'capture': data or capture()})

    def case(self, result, expected=17, actual=17):
        m = result['metrics'][0]
        return {'case_id': 'case-1', 'question': 'How many active accounts?', 'metric_id': m['id'],
            'capture_hash': result['capture_hash'], 'definition_hash': m['definition_hash'],
            'reference': {'value': expected, 'context_hash': result['context_hash'], 'evidence_ref': 'synthetic:card'},
            'actual': {'value': actual, 'context_hash': result['context_hash'], 'evidence_ref': 'synthetic:query-success'}}

    def evaluate(self, result, case):
        return self.call('dashboard_evaluate', {'job_id': self.job, 'expected_revision': result['revision'], 'cases': [case]})

