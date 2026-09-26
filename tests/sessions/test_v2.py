import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.tools.sessions.store import build, show
from voice_tools.tools.sessions.export import export
from voice_tools.tools.sessions.workflow import investigate
from tests.sessions.fixtures import pcap, packet, sip, T0, CALL_A
from tests.report.fixtures import rtp


class SessionV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_ip_fragmented_sip_reassembles_across_files(self):
        raw=packet(sip(CALL_A));udp=raw[34:];pieces=[]
        for offset,payload in ((0x2000,udp[:160]),(20,udp[160:])):
            ip=bytearray(raw[14:34]);ip[2:4]=struct.pack('!H',20+len(payload));ip[6:8]=struct.pack('!H',offset)
            pieces.append(raw[:14]+ip+payload)
        a,b=self.root/'a.pcap',self.root/'b.pcap';pcap(a,[(0,pieces[0])]);pcap(b,[(.01,pieces[1])])
        result=build(self.root/'index',pcap_groups=[('sensor',[a,b])])
        self.assertEqual(result['sessions'],1,result)
        call=show(self.root/'index',CALL_A)
        self.assertEqual(call['observations'][0]['data']['media'][0]['codecs']['0']['name'],'PCMU')
        self.assertEqual(len(call['observations'][0]['metadata']['originals']),2)
        exported = export(self.root/'index', CALL_A, self.root/'exported')
        rebuilt = build(self.root/'rebuilt', [self.root/'exported'/exported['files'][0]['file']])
        self.assertEqual(rebuilt['sessions'], 1, 'Export must preserve IP reassembly dependencies')
        a.write_bytes(a.read_bytes()+b'changed')
        rejected=export(self.root/'index',CALL_A,self.root/'changed')
        self.assertTrue(rejected['partial']);self.assertEqual(len(rejected['errors']),1)

    def test_tcp_sip_reassembles_across_files(self):
        raw=sip(CALL_A);cut=180;frames=[]
        for payload,seq in ((raw[:cut],1),(raw[cut:],1+cut)):
            frame=bytearray(packet(payload,tcp=True));frame[38:42]=struct.pack('!I',seq);frames.append(frame)
        a,b=self.root/'a.pcap',self.root/'b.pcap';pcap(a,[(0,frames[0])]);pcap(b,[(.01,frames[1])])
        result=build(self.root/'index',pcap_groups=[('sensor',[a,b])])
        self.assertEqual(result['sessions'],1,result)
        exported = export(self.root/'index', CALL_A, self.root/'exported')
        rebuilt = build(self.root/'rebuilt', [self.root/'exported'/exported['files'][0]['file']])
        self.assertEqual(rebuilt['sessions'], 1, 'Export must preserve TCP reassembly dependencies')

    def test_events_preserve_short_calls_and_media_changes(self):
        folder=self.root/'sensor';folder.mkdir();capture=folder/'calls.pcap'
        pcap(capture,[(.05,packet(rtp(1),16000,24000)),(.2,packet(rtp(2),16000,24000)),
                      (.2,packet(rtp(3),16002,24002))])
        uid='11111111-2222-3333-4444-555555555555'
        flow=lambda p:dict(local_ip='192.0.2.1',local_port=p,remote_ip='192.0.2.2',remote_port=p+8000)
        def event(when,kind,f):
            return {'schema_version':'1.0','evidence':'fs_event','observed_at':when,'uuid':uid,'call_id':CALL_A,'event':kind,'flow':f}
        events=folder/'events.jsonl';events.write_text('\n'.join(json.dumps(r) for r in [
            event('2026-09-15T08:00:00Z','CHANNEL_ANSWER',flow(16000)),
            event('2026-09-15T08:00:00.1Z','CODEC',flow(16002)),
            event('2026-09-15T08:00:00.5Z','CHANNEL_HANGUP_COMPLETE',None)])+'\n')
        result=build(self.root/'index',[capture],events=[events])
        self.assertFalse(result['partial'],result)
        call=show(self.root/'index',CALL_A)
        self.assertEqual(len(call['media_timeline']),2)
        self.assertAlmostEqual(call['media_timeline'][0]['until_epoch'],T0+.1)
        result=export(self.root/'index',CALL_A,self.root/'export',include_media=True,padding=0)
        self.assertEqual(result['files'][0]['packets'],2,result)

    def test_esl_gap_and_explicit_cross_host_correlation(self):
        inputs=[]
        for n in (1,2):
            folder=self.root/('host'+str(n));folder.mkdir();path=folder/'events.jsonl';inputs.append(path)
            row={'schema_version':'1.0','evidence':'fs_event','observed_at':'2026-09-15T08:00:00Z',
                 'uuid':f'{n:08d}-2222-3333-4444-555555555555','call_id':'call'+str(n),'correlation_id':'business-A'}
            path.write_text(json.dumps(row)+'\n')
        with inputs[0].open('a') as stream:stream.write(json.dumps({'schema_version':'1.0','evidence':'fs_event_gap','observed_at':'2026-09-15T08:00:00Z','reason':'disconnect'})+'\n')
        result=build(self.root/'index',events=inputs)
        self.assertTrue(result['partial'])
        call=show(self.root/'index','call1')
        self.assertEqual(call['related_legs'][0]['call_id'],'call2')
        self.assertEqual(call['related_legs'][0]['basis'],'explicit_correlation_id')

    def test_workflow_plan_performs_no_external_actions(self):
        with patch('subprocess.run',side_effect=AssertionError('no subprocess in dry-run')):
            result=investigate(self.root/'job','2026-09-15T08:00:00Z','2026-09-15T08:05:00Z',CALL_A,self.root/'plan',dry_run=True)
        self.assertEqual(result['status'],'planned')
        self.assertEqual([s['name'] for s in result['steps']],['capture','homer-search','index','export','correlation','report'])
        self.assertTrue(all(s['status']=='planned' for s in result['steps']))

    def test_workflow_runs_real_index_export_report_and_isolates_homer_failure(self):
        from voice_tools.core.files import sha256
        from voice_tools.tools.sessions.workflow import run_command
        def transport(command, out, err, timeout):
            if 'ring-fetch' in command:
                root = Path(command[command.index('--out') + 1])
                host = root/'fs-a'
                host.mkdir(parents=True)
                path = host/'part-000001.pcap'
                pcap(path)
                (host/'host.json').write_text(json.dumps({'schema_version': '1.0', 'tool': 'capture-batch-host',
                    'name': 'fs-a', 'status': 'complete', 'files': [{'file': path.name, 'sha256': sha256(path)}]}))
                (root/'batch.json').write_text(json.dumps({'schema_version': '1.0', 'tool': 'capture-batch',
                    'status': 'complete', 'hosts': [{'name': 'fs-a', 'manifest': 'fs-a/host.json'}]}))
                return 0
            if 'homer-search' in command or 'correlate' in command:
                return 2
            return run_command(command, out, err, timeout)
        with patch('voice_tools.tools.sessions.workflow.run_command', side_effect=transport):
            result = investigate(self.root/'job', '2026-09-15T08:00:00Z', '2026-09-15T08:05:00Z',
                                 CALL_A, self.root/'investigation', decode_rtp=True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([s['status'] for s in result['steps']],
                         ['complete', 'failed', 'complete', 'complete', 'failed', 'complete'], result)
        self.assertTrue((self.root/'investigation/report/report.html').is_file())

    def test_media_tags_and_bye_keep_other_fork_open(self):
        from voice_tools.tools.sessions.media import timeline
        def row(when, port, tag, method=None):
            return {'host': 'fs-a', 'epoch': when, 'data': {'src': '192.0.2.1', 'from_tag': 'a',
                    'to_tag': tag, 'method': method, 'media': [{'ip': '192.0.2.1', 'port': port}]}}
        stages = timeline([row(1, 16000, ''), row(2, 16002, 'b'), row(3, 16004, 'c'), row(4, 0, 'b', 'BYE')])
        self.assertEqual([s['until_epoch'] for s in stages], [2, 4, None])
