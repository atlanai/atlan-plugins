"""Rendering, source binding and the loopback presentation boundary."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import analytics_ux_prototype as fixture
import experience
import experience_view as view


class LiveView(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.output=fixture.ContextState(Path(self.tmp.name).resolve())
        self.run=fixture.seed_run(self.output,'checked')
        fixture.seed_checked(self.run,'checked')
        self.overview={'summary':'Synthetic shipping model, history and sample data.', 'opportunities':['Shipping cost','Shipped units']}

    def tearDown(self): self.tmp.cleanup()

    def test_prepare_is_not_display_and_summary_invalidates(self):
        model=experience.snapshot(self.run)
        before=self.run.read(self.run.path)
        result=view.prepare(self.run,model,self.overview)
        self.assertFalse(result['displayed'])
        self.assertEqual(result['pane']['summary'],self.overview['summary'])
        self.assertEqual(self.run.read(self.run.path),before)
        before['sources'].append({'kind':'data_file','path':'changed.csv'})
        self.run.write(self.run.path,before)
        self.assertEqual(view.overview(self.run),{})

    def test_renderer_escapes_and_keeps_checks(self):
        model=experience.snapshot(self.run)
        page=view.render(model,{'summary':'<script>alert(1)</script>','opportunities':['<img src=x>']})
        self.assertNotIn('<script>alert',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertIn('Inspect SQL, source and checks',page)
        self.assertIn('SQL example checked',page)
        self.assertIn('Copy pause request',page)
        self.assertIn('connect-src',page)

    def test_progress_does_not_mark_review_or_save_as_ready(self):
        model=experience.snapshot(self.run)
        for phase in ('build','check','preview','publish'):
            for status in ('running','paused','local'):
                model['phase'].update(code=phase,status=status)
                self.assertIn('<span aria-current="step">Prepare</span>',view.render(model))
        model['phase']['code']='ready'
        self.assertIn('<span aria-current="step">Use</span>',view.render(model))

    def test_server_only_exposes_view_and_updates_saved_state(self):
        view.prepare(self.run,experience.snapshot(self.run),self.overview)
        before={str(p.relative_to(self.run.root)):p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
        server=view.make_server(self.run.root)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,method='GET',headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request(method,path,headers=headers or {})
            response=conn.getresponse(); data=response.read();status=response.status;conn.close()
            return status,data
        try:
            status,data=request('/snapshot');self.assertEqual(status,200)
            first=json.loads(data);self.assertIn('What we found',first['html'])
            for path in ['/run.json','/../run.json','/snapshot?file=run.json','/.env','/favicon.ico']:
                self.assertEqual(request(path)[0],404)
            self.assertEqual(request('/',headers={'Host':'attacker.example'})[0],403)
            self.assertEqual(request('/',headers={'Origin':'https://attacker.example'})[0],403)
            self.assertEqual(request('/',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(request('/','POST')[0],501)
            after={str(p.relative_to(self.run.root)):p.read_bytes() for p in self.run.root.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.run.journey('domain',answer='Shipping cost')
            second=json.loads(request('/snapshot')[1]);self.assertNotEqual(first['revision'],second['revision'])
            self.assertIn('Shipping cost',second['html'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_symlink_and_invalid_summary_rejected(self):
        link=Path(self.tmp.name).resolve()/'link';link.symlink_to(self.run.root,target_is_directory=True)
        with self.assertRaises(ValueError):view.make_server(link)
        with self.assertRaises(ValueError):view.overview(self.run,{'summary':'x','opportunities':['a']*4})


if __name__=='__main__': unittest.main()
