import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tests.homer.test_client import FakeHomer, SECRET, message
from voice_tools.core.files import write_json
from voice_tools.tools.sessions.homer import correlate, search_remote
from voice_tools.tools.sessions.store import build, search
from voice_tools.tools.report.service import build as report
from .fixtures import CALL_A, T0


class HomerLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeHomer()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.server.reset()
        self.server.expected_call_ids = [CALL_A]
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = {k:v for k,v in os.environ.items() if not k.startswith('HOMER_')}
        env.update(HOMER_URL=f'http://127.0.0.1:{self.server.server_port}/homer', HOMER_TOKEN=SECRET,
                   HOMER_CONFIG=str(self.root/'config.json'), NO_PROXY='127.0.0.1,localhost')
        self.patch = patch.dict(os.environ, env, clear=True); self.patch.start(); self.addCleanup(self.patch.stop)
        write_json(self.root/'local.json', {'schema_version':'1.0','rows':[
            {'sid':CALL_A,'from_user':'1001','to_user':'1002',
             '_ref':{'call_id':CALL_A,'timestamp_us':T0*1000000,'id':1,'dbnode':'local','profile':'1_call'}}]})
        build(self.root/'index', homer_json=[self.root/'local.json'])

    def tearDown(self):
        self.assertEqual(self.server.failures, [])
        for p in self.root.rglob('*.json'):
            self.assertNotIn(SECRET, p.read_text(), str(p))

    def test_real_existing_cli_search_and_trace_by_local_call(self):
        result = correlate(self.root/'index',CALL_A,self.root/'linked')
        self.assertFalse(result['partial'], result)
        self.assertEqual(result['search']['exit_code'], 0)
        self.assertEqual(result['trace']['messages'], 6)
        methods = [path for _,path,_,_ in self.server.requests]
        self.assertIn('/homer/api/v3/search/call/data', methods)
        self.assertIn('/homer/api/v3/call/transaction', methods)
        trace=json.loads((self.root/'linked/homer-trace.json').read_text())
        self.assertEqual(trace['messages'][0]['_ref']['call_id'], CALL_A)
        result=report(self.root/'report',correlations=[self.root/'linked'])
        self.assertEqual(result['errors'],0)
        self.assertIn('HOMER 关联',(self.root/'report/report.html').read_text())

    def test_homer_first_number_search_can_be_indexed(self):
        result=search_remote(self.root/'remote',T0,T0+20,caller='1001')
        self.assertFalse(result['partial'],result)
        imported = build(self.root/'imported',homer_json=[self.root/'remote/homer-search.json'])
        self.assertEqual(imported['errors'], 0, imported)
        self.assertEqual(search(self.root/'imported',number='1001')['total'],1)

    def test_homer_partial_exit_six_is_preserved_and_trace_still_runs(self):
        self.server.rows=[message(i+1,100) for i in range(240)]
        result=correlate(self.root/'index',CALL_A,self.root/'partial',max_requests=1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['search']['exit_code'],6)
        self.assertEqual(result['search']['status'],'partial')
        self.assertEqual(result['trace']['status'],'ok')
        result=build(self.root/'partial-index',homer_json=[self.root/'partial/homer-search.json'])
        self.assertEqual(result['errors'], 0, result)
        self.assertTrue(result['partial'])
        self.assertEqual(result['sessions'],1)

    def test_homer_failure_keeps_local_correlation_receipt(self):
        self.server.forced=(503,{'message':'not available'})
        result=correlate(self.root/'index',CALL_A,self.root/'failure')
        self.assertTrue(result['partial'])
        self.assertEqual(result['trace']['status'],'failed')
        self.assertEqual(result['local_observations'],1)

    def test_fs_bridge_peer_call_id_is_included_in_homer_trace(self):
        from .fixtures import CALL_B
        a,b='11111111-2222-3333-4444-555555555555','aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        rows=[{'schema_version':'1.0','call_id':cid,'uuid':uuid,'peer_uuid':peer,
               'observed_at':'2026-09-15T08:00:00Z'} for cid,uuid,peer in ((CALL_A,a,b),(CALL_B,b,a))]
        (self.root/'fs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        build(self.root/'bridged',snapshots=[self.root/'fs.jsonl'])
        self.server.expected_call_ids = [CALL_A,CALL_B]
        result=correlate(self.root/'bridged',CALL_A,self.root/'bridge-linked')
        self.assertEqual(result['trace_requested_call_ids'],[CALL_A,CALL_B])
        self.assertEqual(result['trace']['status'],'ok')
        self.assertEqual(result['local_related_legs'][0]['basis'],'fs_bridge_snapshot')
