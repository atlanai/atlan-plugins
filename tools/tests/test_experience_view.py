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
            first_pane=json.loads(request('/pane')[1])
            self.assertEqual(first_pane['summary'],self.overview['summary'])
            self.assertEqual(first_pane['interaction_mode'],'active')
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
            self.assertEqual(json.loads(request('/pane')[1])['domain'],'Shipping cost')
            self.run.journey('purpose',answer='Prepare team metrics')
            self.assertEqual(json.loads(request('/pane')[1])['purpose'],'Prepare team metrics')
            self.run.journey('pause')
            self.assertEqual(json.loads(request('/pane')[1])['interaction_mode'],'paused')
            self.run.journey('resume')
            self.assertEqual(json.loads(request('/pane')[1])['interaction_mode'],'active')
        finally:server.shutdown();server.server_close();thread.join()

    def test_symlink_and_invalid_summary_rejected(self):
        link=Path(self.tmp.name).resolve()/'link';link.symlink_to(self.run.root,target_is_directory=True)
        with self.assertRaises(ValueError):view.make_server(link)
        with self.assertRaises(ValueError):view.overview(self.run,{'summary':'x','opportunities':['a']*4})

    def test_engine_wait_is_not_a_user_pause(self):
        model=experience.snapshot(self.run)
        model['phase']['status']='paused'
        self.assertEqual(view.pane(model,{})['status'],'working')
        self.assertEqual(view.pane(model,{})['interaction_mode'],'active')
        model['interaction']['mode']='paused'
        self.assertEqual(view.pane(model,{})['status'],'paused')

    def test_live_detail_is_counted_bounded_and_served_apart(self):
        state=self.run.read(self.run.path)
        state['sources'][0]['counts']={'measures':23,'tables':6,'hidden':0}
        state['sources'][1]['records']=412
        state['sources'][2]['status']='pending_inspection'
        self.run.write(self.run.path,state)
        self.run.write(self.run.root/'progress.json',{'stage':'inspect','status':'running','completed_items':2,'total_items':3})
        live=view.pane_v2(experience.snapshot(self.run),{})
        self.assertEqual(live['now'],'Inspecting source 3 of 3 · shipments.csv')
        self.assertEqual(live['findings'],['shipping.bim · 23 measures, 6 tables','query_history.jsonl · 412 query records','shipments.csv · waiting to be read'])
        self.assertEqual(live['questions'],[])
        self.run.write(self.run.root/'progress.json',{'stage':'inspect','status':'complete','completed_items':3,'total_items':3})
        self.assertEqual(view.pane_v2(experience.snapshot(self.run),{})['now'],'Inspected 3 sources')
        self.run.write(self.run.root/'progress.json',{'stage':'check','status':'running','completed_items':4,'total_items':9})
        self.assertEqual(view.pane_v2(experience.snapshot(self.run),{})['now'],'Checking definition 5 of 9')
        # A count the run did not record is never invented.
        self.run.write(self.run.root/'progress.json',{'stage':'check','status':'running','completed_items':9,'total_items':4})
        self.assertEqual(view.pane_v2(experience.snapshot(self.run),{})['now'],'Checking the definitions')
        self.run.write(self.run.root/'progress.json',{'stage':'preview','status':'running'})
        model=experience.snapshot(self.run)
        waiting=view.pane_v2(model,{})
        self.assertEqual((waiting['action'],waiting['needs']),('destination','Choose where to save'))
        self.assertNotIn('now',view.pane(model,{}))
        # Questions are offered only where the run can answer them, never from held work.
        self.run.journey('keep-local')
        local=experience.snapshot(self.run)
        offered=view.pane_v2(local,{})
        self.assertEqual(offered['action'],'explore-local')
        self.assertTrue(0<len(offered['questions'])<=3)
        self.assertTrue(all('\n' not in q and len(q)<=160 for q in offered['questions']))
        for item in local['items']: item['status']='held'
        self.assertEqual(view.pane_v2(local,{})['questions'],[])
        server=view.make_server(self.run.root)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def read(path):
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                conn.request('GET',path);response=conn.getresponse();data=response.read();conn.close()
                return response.status,data
            self.assertNotIn('now',json.loads(read('/pane')[1]))
            self.assertIn('now',json.loads(read('/pane/v2')[1]))
            self.assertEqual(read('/pane/v3')[0],404)
        finally:server.shutdown();server.server_close();thread.join()

    def test_native_feed_clears_access_error_after_current_host_read(self):
        import auth_recovery
        self.run.write(self.run.root/'auth-recovery.json', {
            'host':'claude_code','server':'plugin:atlanai:atlan','status':'login_required','started_at':1000})
        server=view.make_server(self.run.root,host='claude_code',server='plugin:atlanai:atlan')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def read():
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            conn.request('GET','/pane');response=conn.getresponse()
            value=json.loads(response.read());conn.close();return value
        try:
            self.assertEqual(read()['status'],'access_needed')
            auth_recovery.advance(self.run,{'operation':'record_read','host':'claude_code',
                'server':'plugin:atlanai:atlan','read_tool':'registry__list_workspaces','read_succeeded':True},now=2000)
            self.assertNotEqual(read()['status'],'access_needed')
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__': unittest.main()
