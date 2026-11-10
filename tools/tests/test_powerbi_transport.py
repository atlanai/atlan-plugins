"""The Power BI client sends its bearer token only to the Fabric and Power BI APIs."""
import http.server
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest import mock
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'plugins/atlanai-analytics/modules/metrics/engine/packages/metrics-agent-powerbi'
          / 'src/metrics_agent_powerbi/pbi_definition_extract.py')
spec = importlib.util.spec_from_file_location('pbi_definition_extract_under_test', SOURCE)
pbi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pbi)


class _Response:
    status = 200
    headers = {}

    def __init__(self, body):
        self._body = body

    def read(self, *_):
        body, self._body = self._body, b''
        return body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _serve(handler):
    server = http.server.HTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class PowerBITransportTest(unittest.TestCase):
    def test_continuation_on_another_host_is_refused_before_sending(self):
        sent = []
        pages = [b'{"value": [{"id": "a"}], "continuationUri": "https://collector.example/next"}']

        def fake_open(request, timeout):
            sent.append(request.full_url)
            return _Response(pages.pop(0))

        client = pbi.FabricClient('token-under-test')
        with mock.patch.object(pbi, '_open', fake_open):
            items = client.get_paged('/workspaces')
            self.assertEqual(next(items), {'id': 'a'})
            with self.assertRaises(pbi.ApiError) as raised:
                next(items)
        self.assertEqual(raised.exception.code, 'UntrustedHost')
        self.assertEqual(sent, [pbi.FABRIC_API + '/workspaces'])

    def test_download_from_another_host_is_refused_before_sending(self):
        client = pbi.FabricClient('token-under-test')
        with mock.patch.object(pbi, '_open', side_effect=AssertionError('sent')):
            with self.assertRaises(pbi.ApiError) as raised:
                client.download('https://collector.example/file', '/nonexistent/target')
        self.assertEqual(raised.exception.code, 'UntrustedHost')

    def test_redirect_to_another_host_drops_the_token(self):
        seen = {}

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen['authorization'] = self.headers.get('Authorization')
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'PK')

            def log_message(self, *_):
                pass

        target = _serve(Target)

        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', f'http://localhost:{target.server_port}/file')
                self.end_headers()

            def log_message(self, *_):
                pass

        origin = _serve(Origin)
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{origin.server_port}/export',
                                             headers={'Authorization': 'Bearer token-under-test'})
            with pbi._open(request, timeout=5) as response:
                self.assertEqual(response.read(), b'PK')
        finally:
            origin.shutdown()
            target.shutdown()
            origin.server_close()
            target.server_close()
        self.assertIn('authorization', seen)
        self.assertIsNone(seen['authorization'])


if __name__ == '__main__':
    unittest.main()
