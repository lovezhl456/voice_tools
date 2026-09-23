import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from voice_tools.tools.detect import store
from voice_tools.tools.detect.server import create_server, EditorApplication
from .fixtures import config,wav


class EditorServer(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.audio=wav(self.root/'sample.wav')
        self.db=self.root/'library.sqlite3';self.out=self.root/'service'
        self.server=create_server(self.db,[self.audio],self.out)
        self.thread=threading.Thread(target=self.server.serve_forever,
                                     kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)

    def request(self,path,body=None,headers=None,method=None,raw=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        given={'X-Voice-Tools-Session':self.server.application.token}
        if body is not None or raw is not None:given['Content-Type']='application/json'
        given.update(headers or {})
        content=raw if raw is not None else json.dumps(body).encode() if body is not None else None
        connection.request(method or ('POST' if content is not None else 'GET'),path,body=content,headers=given)
        response=connection.getresponse();payload=response.read();status=response.status;response_headers=dict(response.getheaders());connection.close()
        return status,payload,response_headers

    def post(self,path,body,**kwargs):
        code,body,headers=self.request(path,body,**kwargs)
        return code,json.loads(body),headers

    def test_root_and_library_are_local_and_never_expose_database(self):
        code,html,headers=self.request('/')
        self.assertEqual(code,200);self.assertIn('定义指标与标签'.encode(),html)
        self.assertEqual(headers['X-Frame-Options'],'DENY')
        self.assertEqual(self.request('/api/library')[0],200)
        for path in ('/library.sqlite3','/.library.json','/reports/../.library.json','/reports/%2e%2e/.library.json','/reports/%00'):
            with self.subTest(path=path):self.assertEqual(self.request(path)[0],404)

    def test_host_origin_token_and_content_type_guards(self):
        for headers in ({'Host':'attacker.test'},{'Origin':'https://attacker.test'},{'X-Voice-Tools-Session':'wrong'},
                        {'X-Voice-Tools-Session':''},{'X-Voice-Tools-Session':'é'}):
            self.assertEqual(self.post('/api/definitions',{'config':config()},headers=headers)[0],403)
        self.assertEqual(self.post('/api/definitions',{'config':config()},headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('/api/definitions',method='OPTIONS')[0],403)
        with store.connect(self.db) as db:self.assertEqual(store.definitions(db),[])

    def test_validate_save_immutable_versions_and_restart_persistence(self):
        value=config();value['rules'][0]['when']['value']=-30.0
        self.assertEqual(self.post('/api/validate',{'config':value})[0],200)
        first=self.post('/api/definitions',{'config':value})[1]
        value['rules'][0]['when']['value']=-30
        second=self.post('/api/definitions',{'config':value})[1]
        self.assertEqual(first['hash'],second['hash'])
        value['rules'][0]['label']='changed'
        self.assertEqual(self.post('/api/definitions',{'config':value})[0],400)
        value['version']='2';self.assertEqual(self.post('/api/definitions',{'config':value})[0],200)
        restored=EditorApplication(self.db,[self.audio],self.out)
        self.assertEqual(len(restored.library()['definitions']),2)
        with self.assertRaisesRegex(ValueError,'其他检测库'):
            EditorApplication(self.root/'other.sqlite3',[self.audio],self.out)
        self.assertFalse((self.root/'other.sqlite3').exists())

    def test_run_uses_saved_versions_and_scoped_inputs_and_serves_range_audio(self):
        self.post('/api/definitions',{'config':config()})
        code,result,_=self.post('/api/run',{'definitions':['level@1'],'batch_id':'browser-1'})
        self.assertEqual(code,200);self.assertEqual(result['summary']['findings'],1)
        self.assertEqual(self.request(result['report_url'])[0],200)

        html=self.request(result['report_url'])[1].decode();self.assertIn('href="/"',html)
        source=next((self.out/'reports/browser-1/audio').glob('*.wav'))
        code,body,headers=self.request('/reports/browser-1/audio/'+source.name,headers={'Range':'bytes=0-127'})
        self.assertEqual((code,len(body)),(206,128));self.assertTrue(headers['Content-Range'].startswith('bytes 0-127/'))
        self.assertEqual(self.request('/reports/browser-1/audio/'+source.name,headers={'Range':'bytes=9999999-'})[0],416)
        self.assertEqual(self.post('/api/run',{'definitions':['level@1'],'batch_id':'browser-1'})[0],400)
        self.assertEqual(self.post('/api/run',{'definitions':['level@1'],'batch_id':'x','inputs':['/not-authorized.wav']})[0],400)

    def test_invalid_json_missing_reference_and_report_failure_do_not_create_batch(self):
        duplicate=b'{"config":{},"config":{}}'
        self.assertEqual(self.request('/api/definitions',raw=duplicate)[0],400)
        self.assertEqual(self.post('/api/run',{'definitions':['missing@1'],'batch_id':'absent'})[0],400)
        self.post('/api/definitions',{'config':config()})
        with patch('voice_tools.tools.detect.report.render',side_effect=OSError('write failed')):
            self.assertEqual(self.post('/api/run',{'definitions':['level@1'],'batch_id':'failed'})[0],400)
        with store.connect(self.db) as db:self.assertEqual(store.batch_details(db),[])
        self.assertFalse((self.out/'reports/failed').exists())

    def test_partial_result_is_visible_and_concurrent_run_is_refused(self):
        value=config();value['metrics']['level']['channel']=1
        mono=wav(self.root/'mono.wav',channels=1)
        self.server.application.inputs=[mono]
        self.post('/api/definitions',{'config':value})
        self.server.application.run_lock.acquire()
        try:self.assertEqual(self.post('/api/run',{'definitions':['level@1'],'batch_id':'busy'})[0],409)
        finally:self.server.application.run_lock.release()
        code,result,_=self.post('/api/run',{'definitions':['level@1'],'batch_id':'partial'})
        self.assertEqual(code,200);self.assertEqual(result['summary']['status'],'partial');self.assertEqual(result['summary']['errors'],1)
        self.assertEqual(self.request(result['report_url'])[0],200)

    def test_workspace_routes_require_session_and_reject_malformed_reviews(self):
        self.assertEqual(self.request('/workspace')[0], 200)
        for route in ('/api/workspace', '/api/review-state', '/api/comparisons/missing'):
            self.assertEqual(self.request(route, headers={'X-Voice-Tools-Session':'wrong'})[0], 403)
        self.assertEqual(self.post('/api/workspace/reviews', {'reviews':['bad']})[0], 400)
        self.assertEqual(self.post('/api/workspace/run', {'definitions':['level@1'],'mode':'all','inputs':['/outside']})[0], 400)

    def test_live_report_session_is_injected_only_when_served(self):
        self.post('/api/definitions', {'config':config()})
        _, result, _ = self.post('/api/run', {'definitions':['level@1'],'batch_id':'live'})
        disk = (self.out/'reports/live/index.html').read_text()
        self.assertIn('id="liveSession" type="application/json">null', disk)
        page = self.request(result['report_url'])[1].decode()
        self.assertIn(self.server.application.token, page)
        self.assertNotIn(self.server.application.token, disk)

    def test_online_review_api_updates_state_and_preserves_offline_export(self):
        self.post('/api/definitions', {'config':config()})
        self.post('/api/run', {'definitions':['level@1'],'batch_id':'review'})
        with store.connect(self.db) as db:
            finding = store.query(db)[0]
            packet = {'schema_version':'1.0','library_id':store.library_id(db),'manual':[], 'reviews':[{
                'finding_id':finding['id'],'expected_revision':0,'status':'false_positive','reviewer':'test','comment':'试听正常'}]}
        self.assertEqual(self.post('/api/workspace/reviews', packet)[0], 200)
        latest = json.loads(self.request('/api/review-state')[1])
        self.assertEqual(latest['findings'][0]['status'], 'false_positive')
        self.assertEqual(latest['standards'][0]['verdict'], 'normal')
        self.assertEqual(self.post('/api/workspace/reviews', packet)[0], 400)

    def test_real_batch_write_lock_rejects_all_online_writes_without_waiting(self):
        from voice_tools.tools.detect import service
        self.post('/api/definitions', {'config':config()})
        self.post('/api/run', {'definitions':['level@1'],'batch_id':'seed'})
        with store.connect(self.db) as db:
            finding = store.query(db)[0]
            packet = {'schema_version':'1.0','library_id':store.library_id(db),'manual':[], 'reviews':[{
                'finding_id':finding['id'],'expected_revision':0,'status':'confirmed','reviewer':'test','comment':'已试听'}]}
        self.assertEqual(self.post('/api/workspace/reviews', packet)[0], 200)
        state = json.loads(self.request('/api/workspace')[1])
        packet['reviews'][0]['expected_revision'] = 1
        saved = state['standards'][0]
        setting = {'name':'daily','definitions':['level@1'],'expected_revision':state['settings']['revision']}
        requests = [
            ('/api/definitions', {'config':config(version='2')}),
            ('/api/workspace/settings', setting),
            ('/api/workspace/reviews', packet),
            ('/api/workspace/standard', {'id':saved['id'],'expected_revision':saved['revision'],
              **{key:saved[key] for key in ('recording_id','label','start_s','end_s','verdict','reviewer','comment')},'checked':True}),
            ('/api/workspace/freeze', {'name':'fixed','ids':[saved['id']]}),
        ]
        entered, release = threading.Event(), threading.Event()
        real_analyze = service.analyze
        def held_analysis(*args, **kwargs):
            # service.run has inserted the batch: SQLite really holds a write transaction here.
            entered.set()
            if not release.wait(15):
                raise RuntimeError('test failed to release batch')
            return real_analyze(*args, **kwargs)
        with patch.object(service, 'analyze', side_effect=held_analysis):
            code, job, _ = self.post('/api/workspace/run', {'definitions':['level@1'],'mode':'all'})
            self.assertEqual(code, 200)
            try:
                self.assertTrue(entered.wait(3))
                for route, body in requests:
                    with self.subTest(route=route):
                        started = time.monotonic()
                        status, result, _ = self.post(route, body)
                        self.assertEqual(status, 409)
                        self.assertEqual(result['code'], 'workspace_busy')
                        self.assertIn('未保存内容仍保留', result['error'])
                        self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(self.request('/api/workspace')[0], 200)
                self.assertEqual(self.post('/api/validate', {'config':config()})[0], 200)
            finally:
                release.set()
                deadline = time.monotonic() + 5
                while self.server.application.run_lock.locked() and time.monotonic() < deadline:
                    time.sleep(.01)
        self.assertFalse(self.server.application.run_lock.locked())
        self.assertEqual(self.post('/api/workspace/settings', setting)[0], 200)
        with store.connect(self.db) as db:
            self.assertEqual(store.current_finding(db, finding['id'])['revision'], 1)


class EditorWorkspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.audio = wav(self.inputs / 'original.wav')
        self.db = self.root / 'library.sqlite3'

    def test_output_within_inputs_is_rejected_before_writes_even_after_restart(self):
        output = self.inputs / 'service'
        with self.assertRaisesRegex(ValueError, '输入目录重叠'):
            EditorApplication(self.db, [self.inputs], output)
        self.assertFalse(self.db.exists())
        self.assertFalse(output.exists())
        # Simulate reports left by an older service; a restart must not ingest them.
        output.mkdir()
        generated = wav(output / 'generated.wav', channels=1)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, '输入目录重叠'):
                EditorApplication(self.db, [self.inputs], output)
        self.assertFalse(self.db.exists())
        self.assertTrue(generated.exists())

    def test_resolved_symlinks_and_equal_or_containing_paths_are_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.inputs, target_is_directory=True)
        output_alias = self.root / 'output-alias'
        output_alias.symlink_to(self.inputs / 'service', target_is_directory=True)
        for inputs, output in (([alias], self.inputs / 'service'),
                               ([self.inputs], output_alias),
                               ([self.inputs], self.inputs),
                               ([self.audio], self.inputs),
                               ([self.inputs], self.root)):
            with self.subTest(inputs=inputs, output=output):
                with self.assertRaisesRegex(ValueError, '输入'):
                    EditorApplication(self.db, inputs, output)
                self.assertFalse(self.db.exists())

    def test_sibling_output_restart_keeps_only_original_recording(self):
        output = self.root / 'service'
        first = EditorApplication(self.db, [self.inputs], output)
        first.save(config())
        first.run({'definitions': ['level@1'], 'batch_id': 'first'})
        restarted = EditorApplication(self.db, [self.inputs], output)
        self.assertEqual(restarted.inputs, [self.audio])
        result = restarted.run({'definitions': ['level@1'], 'batch_id': 'second'})
        self.assertEqual(result['summary']['recordings'], 1)
        self.assertEqual(result['summary']['errors'], 0)

    def test_database_inside_empty_workspace_can_initialize_and_restart(self):
        for relative, exists in (('library.sqlite3', False), ('state/library.sqlite3', True)):
            with self.subTest(database=relative):
                output = self.root / ('existing' if exists else 'new')
                if exists:
                    output.mkdir()
                database = output / relative
                first = EditorApplication(database, [self.inputs], output)
                first.save(config())
                first.run({'definitions': ['level@1'], 'batch_id': 'first'})
                restarted = EditorApplication(database, [self.inputs], output)
                self.assertEqual(restarted.library_id, first.library_id)
                self.assertEqual(restarted.library()['definitions'], [config()])
                result = restarted.run({'definitions': ['level@1'], 'batch_id': 'second'})
                self.assertEqual(result['summary']['errors'], 0)

    def test_nonempty_or_file_output_rejects_without_creating_database(self):
        output = self.root / 'occupied'
        output.mkdir()
        keep = output / 'keep.txt'
        keep.write_text('preserve me')
        for target in (output, keep):
            with self.subTest(output=target):
                with self.assertRaisesRegex(ValueError, '服务输出'):
                    EditorApplication(self.db, [self.inputs], target)
                self.assertFalse(self.db.exists())
        self.assertEqual(keep.read_text(), 'preserve me')

    def test_database_cannot_use_workspace_marker_or_public_report_paths(self):
        output = self.root / 'service'
        for database in (output, output / '.library.json', output / 'reports',
                         output / 'reports/private/library.sqlite3'):
            with self.subTest(database=database):
                with self.assertRaisesRegex(ValueError, '检测库不能'):
                    EditorApplication(database, [self.inputs], output)
                self.assertFalse(output.exists())
