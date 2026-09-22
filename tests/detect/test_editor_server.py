import http.client
import json
from pathlib import Path
import tempfile
import threading
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
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
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
        try:self.assertEqual(self.post('/api/run',{'definitions':['level@1'],'batch_id':'busy'})[0],400)
        finally:self.server.application.run_lock.release()
        code,result,_=self.post('/api/run',{'definitions':['level@1'],'batch_id':'partial'})
        self.assertEqual(code,200);self.assertEqual(result['summary']['status'],'partial');self.assertEqual(result['summary']['errors'],1)
        self.assertEqual(self.request(result['report_url'])[0],200)
