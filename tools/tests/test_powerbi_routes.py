"""Power BI input routes found broken in a live test of every sign-in and capture route.

1. A supplied token (PBI_ACCESS_TOKEN) failed the whole fetch for anyone not a Power BI admin:
   the admin probe's 401 was taken for an expired token.
2. A report without its semantic model (a live-connection .pbix, a report-only extract)
   stopped the run instead of being left out while the run read its query history.
3. A .pbix model read in a runtime set up without the `pbix` feature failed mid-run with a
   missing-module error.
4. A run-configuration error said only its type, never what to change.
5. A semantic model fetched with --with-model, named by its report only by id, was recorded in
   the manifest with no display name.
"""
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics'
ENGINE = METRICS / 'engine'
sys.path.insert(0, str(ENGINE / 'packages/metrics-agent-powerbi/src'))
from metrics_agent_powerbi import pbi_definition_extract as x  # noqa: E402
from metrics_agent_powerbi.credentials import TokenProvider  # noqa: E402

DEPENDENCIES = all(importlib.util.find_spec(n) for n in ('sqlglot', 'yaml', 'jsonschema'))


def _unauthorized():
    return urllib.error.HTTPError('https://api.powerbi.com/v1.0/myorg/admin/workspaces/modified',
                                  401, 'Unauthorized', {}, io.BytesIO(b'{}'))


class SuppliedToken(unittest.TestCase):
    def provider(self):
        args = mock.Mock(auth='token', scope=x.DEFAULT_SCOPE, tenant=None)
        with mock.patch.object(x, 'acquire_token', return_value='synthetic-token'), \
                mock.patch.object(x, 'token_claims', return_value={}):
            provider = TokenProvider(args, x)
            provider()
        return provider

    def test_admin_401_is_the_apis_answer_not_an_expired_token(self):
        client = x.FabricClient('synthetic-token', token_provider=self.provider())
        client.max_attempts = 1
        with mock.patch.object(x, '_open', side_effect=_unauthorized()):
            with self.assertRaises(x.ApiError) as caught:
                client._request('GET', 'https://api.powerbi.com/v1.0/myorg/admin/workspaces/modified')
        self.assertEqual(caught.exception.status, 401)

    def test_download_401_is_the_apis_answer_too(self):
        client = x.FabricClient('synthetic-token', token_provider=self.provider())
        client.max_attempts = 1
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(x, '_open', side_effect=_unauthorized()):
            with self.assertRaises(x.ApiError):
                client.download('https://api.powerbi.com/v1.0/myorg/synthetic', str(Path(d) / 'f'))

    def test_the_provider_says_refused_not_expired(self):
        with self.assertRaises(x.AuthError) as caught:
            self.provider()(force=True)
        self.assertIn('refused', str(caught.exception))
        self.assertIn('PBI_ACCESS_TOKEN', str(caught.exception))


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class ReportWithoutModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ENGINE / 'src'))
        from metrics_agent import readers
        from metrics_agent.plugs.readers.powerbi import PowerBIReader
        # Readers register when the engine is installed; a checkout asks the reader directly.
        cls.patch = mock.patch.object(readers, 'all_readers', return_value=[PowerBIReader()])
        cls.patch.start()
        cls.readers = readers

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()

    def test_live_connection_pbix_is_a_named_gap(self):
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / 'report.pbix'
            with zipfile.ZipFile(live, 'w') as z:
                z.writestr('Report/definition/report.json', '{}')
                z.writestr('Connections', '{}')
            self.assertIn('live-connection', self.readers.model_gap(live))
            full = Path(d) / 'full.pbix'
            with zipfile.ZipFile(full, 'w') as z:
                z.writestr('Report/definition/report.json', '{}')
                z.writestr('DataModel', b'\0')
            self.assertIsNone(self.readers.model_gap(full))

    def test_report_extract_without_tmdl_is_a_named_gap(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / 'reports/ws/r'
            report.mkdir(parents=True)
            (report / 'definition.pbir').write_text('{}')
            self.assertIn('without its semantic model', self.readers.model_gap(Path(d)))
            model = Path(d) / 'semanticModels/ws/m/definition'
            model.mkdir(parents=True)
            (model / 'model.tmdl').write_text('model Model')
            self.assertIsNone(self.readers.model_gap(Path(d)))

    def test_the_access_code_is_declared_in_both_lists(self):
        from metrics_agent.lib import access
        b = access.blocker('model_not_included', source='local-files', stage='s0_inputs',
                           item={'kind': 'bi_input', 'name': 'report.pbix'},
                           not_read='a report', ask='its model')
        self.assertEqual(b['effect'], 'not_read')
        schema = (ENGINE / 'src/metrics_agent/data/schemas/access_report.schema.json').read_text()
        self.assertIn('"model_not_included"', schema)


class BoundModelName(unittest.TestCase):
    def client(self):
        client = x.FabricClient('synthetic-token')
        client.max_attempts = 1
        return client

    def test_a_bound_model_is_named_from_its_item(self):
        with mock.patch.object(x.FabricClient, '_request',
                               return_value=(200, {}, {'displayName': 'Sales Model'})) as call:
            self.assertEqual(x.item_name(self.client(), 'ws-1', 'model-1'), 'Sales Model')
        self.assertEqual(call.call_args.args[1], f'{x.FABRIC_API}/workspaces/ws-1/items/model-1')

    def test_a_refused_lookup_leaves_the_name_unknown(self):
        with mock.patch.object(x.FabricClient, '_request',
                               side_effect=x.ApiError(404, 'ItemNotFound', 'not visible')):
            self.assertIsNone(x.item_name(self.client(), 'ws-1', 'model-1'))


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class PrepareAndErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ENGINE / 'src'))
        sys.path.insert(0, str(METRICS))
        import worker
        from metrics_agent import readers
        from metrics_agent.plugs.readers.powerbi import PowerBIReader
        cls.patch = mock.patch.object(readers, 'all_readers', return_value=[PowerBIReader()])
        cls.patch.start()
        cls.worker = worker

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()

    def doc(self, path):
        return {'settings': {'local-files': {'bi': str(path), 'bi_vendor': 'powerbi'}}}

    def test_a_pbix_model_without_the_feature_is_named_at_prepare(self):
        with tempfile.TemporaryDirectory() as d:
            full = Path(d) / 'full.pbix'
            with zipfile.ZipFile(full, 'w') as z:
                z.writestr('Report/definition/report.json', '{}')
                z.writestr('DataModel', b'\0')
            with mock.patch('importlib.util.find_spec', return_value=None):
                self.assertIn('`pbix` feature', self.worker.optional_readers_missing(self.doc(full)))

    def test_a_live_connection_pbix_needs_no_reader(self):
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / 'report.pbix'
            with zipfile.ZipFile(live, 'w') as z:
                z.writestr('Report/definition/report.json', '{}')
            with mock.patch('importlib.util.find_spec', return_value=None):
                self.assertEqual(self.worker.optional_readers_missing(self.doc(live)), '')

    def test_engine_errors_say_what_to_change_and_others_do_not(self):
        from metrics_agent.run_config import ConfigError
        self.assertEqual(self.worker.own_message(ConfigError('settings.x must be a mapping')),
                         'settings.x must be a mapping')
        self.assertEqual(self.worker.own_message(ValueError('plain')), 'plain')
        Foreign = type('ProgrammingError', (ValueError,), {'__module__': 'snowflake.connector'})
        self.assertIsNone(self.worker.own_message(Foreign('dsn=secret')))
        self.assertIsNone(self.worker.own_message(OSError('path')))


if __name__ == '__main__':
    unittest.main()
