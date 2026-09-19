import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from voice_tools.core.files import write_json, sha256
from voice_tools.tools.sessions.store import build, search, show
from voice_tools.tools.sessions.export import export
from voice_tools.tools.report.service import build as report
from .fixtures import pcap, sip, packet, T0, CALL_A, CALL_B


@unittest.skipUnless(shutil.which('tshark'), 'tshark unavailable')
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for host in ('fs-a', 'fs-b'):
            (self.root/host).mkdir()
            pcap(self.root/host/'calls.pcap')

    def index(self):
        index = self.root/'index'
        result = build(index, [self.root/'fs-a/calls.pcap', self.root/'fs-b/calls.pcap'])
        self.assertEqual(result['errors'], 0, result)
        return index, result

    def test_real_sip_sdp_index_and_search_many_hosts_same_number(self):
        index, result = self.index()
        self.assertEqual(result['sessions'], 2)
        self.assertEqual(result['observations'], 12)
        self.assertEqual(search(index, number='1001')['total'], 2)
        self.assertEqual(search(index, start='2026-09-15T08:00:00.5Z', end='2026-09-15T08:00:00.6Z')['total'], 2)
        match = search(index, call_id=CALL_A, host='fs-a')['sessions'][0]
        self.assertEqual(match['observations'], 3)
        self.assertEqual(search(index, call_id="' OR 1=1 --")['total'], 0)
        data = show(index, CALL_A)['observations']
        endpoints = [ep for r in data for ep in r['data'].get('endpoints', [])]
        self.assertIn({'ip': '192.0.2.1', 'port': 16000, 'basis': 'sdp_advertised'}, endpoints)

    def test_session_export_excludes_other_overlapping_call_and_reports(self):
        index, _ = self.index()
        result = export(index, CALL_A, self.root/'export', include_media=True)
        self.assertFalse(result['partial'], result)
        self.assertEqual(len(result['files']), 2)
        for f in result['files']:
            p = self.root/'export'/f['file']
            cp = subprocess.run(['tshark','-n','-r',str(p),'-T','fields','-e','sip.Call-ID','-e','udp.srcport'], capture_output=True,text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertNotIn(CALL_B, cp.stdout)
            self.assertNotIn('16002', cp.stdout)
            self.assertEqual(len(cp.stdout.splitlines()), 6)
        data = report(self.root/'report', session_exports=[self.root/'export'])
        self.assertEqual(data['errors'], 0, data)
        self.assertEqual(data['pcaps'][0]['analysis']['streams'][0]['sequence_gap_candidates'], 1)
        self.assertIn('媒体', (self.root/'report/report.html').read_text())

    def test_no_media_export_and_changed_source_refused(self):
        index, _ = self.index()
        result = export(index, CALL_A, self.root/'sip-only')
        self.assertEqual(result['files'][0]['media_evidence'], 'signaling_only')
        source = self.root/'fs-a/calls.pcap'
        source.write_bytes(source.read_bytes()+b'bad')
        result = export(index, CALL_A, self.root/'changed')
        self.assertTrue(result['partial'])
        self.assertEqual(len(result['errors']), 1)

    def test_fs_uuid_and_homer_refs_join_exact_call_id(self):
        snapshot = {'schema_version':'1.0','call_id':CALL_A,'uuid':'11111111-2222-3333-4444-555555555555',
                    'observed_at':'2026-09-15T08:00:00+00:00','caller':'1001','callee':'1002'}
        (self.root/'fs.jsonl').write_text(json.dumps(snapshot)+'\n')
        write_json(self.root/'homer.json', {'schema_version':'1.0','completeness':{'status':'partial'},'rows':[
            {'sid':CALL_A,'from_user':'1001','to_user':'1002','_ref':{'id':42,'dbnode':'db-a','profile':'1_call','timestamp_us':T0*1000000,'call_id':CALL_A}}]})
        result = build(self.root/'index', [self.root/'fs-a/calls.pcap'], homer_json=[self.root/'homer.json'], snapshots=[self.root/'fs.jsonl'])
        self.assertTrue(result['partial'])
        self.assertEqual(search(self.root/'index',uuid=snapshot['uuid'])['sessions'][0]['call_id'], CALL_A)
        observations = show(self.root/'index',CALL_A)['observations']
        self.assertEqual([r['data']['homer_ref']['id'] for r in observations if r['kind']=='homer'], [42])

    def test_corrupt_input_rolls_back_only_that_source_and_limit_is_explicit(self):
        bad = self.root/'bad.pcap'; bad.write_bytes(b'bad')
        result = build(self.root/'index',[self.root/'fs-a/calls.pcap',bad],max_packets=2)
        self.assertEqual(result['errors'], 1)
        self.assertTrue(result['partial'])
        self.assertEqual(result['observations'], 2)

    def test_multiple_sip_messages_in_one_tcp_frame(self):
        path = self.root/'tcp.pcap'
        pcap(path, [(0,packet(sip(CALL_A,port=0)+sip(CALL_B,port=0),tcp=True))])
        result = build(self.root/'tcp-index',[path])
        self.assertEqual(result['errors'],0,result)
        self.assertEqual(result['sessions'],2,result)

    def test_invalid_index_and_unknown_call(self):
        with self.assertRaises(ValueError): search(self.root/'missing')
        index,_=self.index()
        with self.assertRaises(ValueError): show(index,'absent')
        with self.assertRaises(ValueError): search(index,start='2026-09-15T08:00:00')
