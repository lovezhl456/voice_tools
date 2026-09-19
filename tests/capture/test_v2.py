import base64
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from voice_tools.tools.capture import esl, ring
from voice_tools.tools.capture.batch import scope_bpf
from voice_tools.tools.capture.remote import Agent, bounds, capture_command, capture_statistics
from voice_tools.tools.capture.service import dumpcap_argv
from tests.sessions.fixtures import pcap, packet, T0


class CaptureV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def config(self, **extra):
        return dict(name='fs-a', host='fs-a', sensor_id='fs-a:any', seconds=10, segment_seconds=10,
                    max_mib=8, snaplen=65535, mode='ring', ring_files=4, bpf='udp', snapshot_seconds=0, **extra)

    def test_byte_quota_is_independent_of_packet_count(self):
        config = self.config(); args = capture_command(config, '/tmp/spool')
        self.assertNotIn('-c', args)
        kilobytes = int(next(a.split(':')[1] for a in args if a.startswith('filesize:')))
        self.assertLessEqual((kilobytes*1000 + config['snaplen']+512)*4, 8*1048576)
        self.assertGreater(kilobytes*1000*4, 7*1048576)
        single = dumpcap_argv('/tmp/voice-tools-abcdefghijkl', 'udp', 'any', 60, 64, 65535)
        self.assertNotIn('-c', single)
        self.assertIn('duration:60', single)

    def test_fragment_bpf_keeps_noninitial_fragments(self):
        original = packet(b'x'*80,16000,24000)
        udp = original[34:]
        frames=[]
        for offset, payload in ((0x2000,udp[:24]),(3,udp[24:])):
            ip=bytearray(original[14:34]);ip[2:4]=struct.pack('!H',20+len(payload));ip[6:8]=struct.pack('!H',offset)
            frames.append(original[:14]+ip+payload)
        source=self.root/'fragments.pcap';pcap(source,[(0,frames[0]),(.01,frames[1])])
        cp=subprocess.run(['tcpdump','-nn','-r',str(source),scope_bpf(['192.0.2.1'],[5060],[[16000,24000]])],capture_output=True,text=True)
        self.assertEqual(cp.returncode,0,cp.stderr);self.assertEqual(len(cp.stdout.splitlines()),2)

    def test_esl_frame_fragmentation_and_secret_whitelist(self):
        body=('Event-Name: CHANNEL_BRIDGE\nUnique-ID: 11111111-2222-3333-4444-555555555555\n'
              'variable_sip_call_id: call-a%40example.net\nvariable_sip_auth_password: SECRET\n'
              'Event-Date-Timestamp: 1700000000000000\nvariable_local_media_ip: 192.0.2.1\n'
              'variable_remote_media_ip: 192.0.2.2\nvariable_local_media_port: 16000\nvariable_remote_media_port: 24000\n\n').encode()
        raw=b'Content-Type: text/event-plain\nContent-Length: '+str(len(body)).encode()+b'\n\n'+body
        class Socket:
            def __init__(self):self.parts=[raw[i:i+3] for i in range(0,len(raw),3)]
            def recv(self,n):return self.parts.pop(0) if self.parts else b''
        head,payload=esl.Connection(Socket()).frame()
        result=esl.record(esl.headers(payload.decode()))
        self.assertEqual(result['call_id'],'call-a@example.net')
        self.assertEqual(result['flow']['remote_port'],24000)
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(result['observed_at'],'2023-11-14T22:13:20+00:00')
        for conf in ({'password':'SECRET'},{'host':'198.51.100.1','password_env':'ESL_PASSWORD'},{}):
            with self.assertRaises(ValueError):esl.validate_config(conf)

    def test_esl_disconnect_marks_gap_without_echoing_secret(self):
        stop=threading.Event();events=[]
        def emit(item):events.append(item);stop.set()
        os.environ['VOICE_TEST_ESL_PASSWORD']='SECRET'
        self.addCleanup(os.environ.pop,'VOICE_TEST_ESL_PASSWORD',None)
        def unavailable(*args,**kwargs):raise OSError('SECRET should not appear')
        esl.listen({'password_env':'VOICE_TEST_ESL_PASSWORD'},stop,emit,unavailable)
        self.assertEqual(events[0]['evidence'],'fs_event_gap')
        self.assertNotIn('SECRET',json.dumps(events))

    def test_agent_freezes_closed_files_and_preserves_source(self):
        agent=Agent(self.root,self.config()); agent.started=T0-2
        source=self.root/'spool/capture_00001.pcap';pcap(source)
        other=self.root/'spool/capture_00002.pcap';pcap(other)
        class Running:
            def poll(self):return None
        agent.process=Running()
        self.assertEqual(len(agent.files()),1)
        class Ended:
            def poll(self):return 0
        agent.process=Ended()
        uid='11111111-2222-3333-4444-555555555555'
        agent.emit({'schema_version':'1.0','evidence':'fs_event','observed_at':'2026-09-15T08:00:00+00:00','uuid':uid,'call_id':'a','event':'CHANNEL_ANSWER'})
        result=agent.freeze({'id':uuid4().hex,'from_epoch':T0,'to_epoch':T0+2})
        self.assertEqual(len(result['manifest']['files']),2)
        frozen=Path(result['remote_export'])
        self.assertEqual((frozen/'part-000001.pcap').read_bytes(),source.read_bytes())
        self.assertTrue(source.exists())
        self.assertIn('events',result['manifest'])
        self.assertEqual(bounds(source)['packets'],12)
        agent.config['frozen_mib']=0
        with self.assertRaises(ValueError):agent.freeze({'id':uuid4().hex,'from_epoch':T0,'to_epoch':T0+2})

    def test_ring_plan_is_offline_and_rejects_secret_value(self):
        config=self.root/'hosts.json';config.write_text(json.dumps({'schema_version':'1.0','hosts':[{
            'name':'fs-a','host':'fs-a','addresses':['192.0.2.1'],'rtp_ranges':[[16000,24000]]}]}))
        def fail(*args):raise AssertionError('network attempted')
        result=ring.start(config,self.root/'plan',dry_run=True,ssh_factory=fail)
        self.assertEqual(result['status'],'planned')
        config.write_text(config.read_text().replace('"addresses":','"esl":{"password":"SECRET"},"addresses":'))
        with self.assertRaises(ValueError):ring.start(config,self.root/'bad',dry_run=True,ssh_factory=fail)

    def test_damaged_truncated_and_dropped_capture_is_partial(self):
        agent = Agent(self.root, self.config())
        agent.started = T0 - 2
        path = self.root / 'spool/broken.pcap'
        path.write_bytes(b'broken')
        source = self.root / 'spool/good.pcap'
        raw = bytearray(pcap(source))
        captured = struct.unpack_from('<I', raw, 32)[0]
        struct.pack_into('<I', raw, 36, captured + 100)
        source.write_bytes(raw)
        self.assertEqual(bounds(source)['truncated_packets'], 1)
        class Ended:
            def poll(self):
                return 0
        agent.process = Ended()
        result = agent.freeze({'id': uuid4().hex, 'from_epoch': T0, 'to_epoch': T0 + 2})
        self.assertEqual(result['status'], 'partial')
        self.assertIn('broken.pcap', result['manifest']['invalid_files'])
        stats = capture_statistics("Packets received/dropped on interface 'eth0': 123/7 (pcap:5/dumpcap:2/flushed:0/ps_ifdrop:3) (5.0%)")
        self.assertEqual(stats['kernel_dropped_packets'], 7)
        self.assertEqual(stats['interface_dropped_packets'], 3)
        self.assertIsNone(capture_statistics('no stats')['kernel_dropped_packets'])

    def test_esl_auth_subscription_and_eof_are_observable(self):
        stop, records = threading.Event(), []
        body = ('Event-Name: CHANNEL_ANSWER\nUnique-ID: 11111111-2222-3333-4444-555555555555\n'
                'variable_sip_call_id: short-call\n\n').encode()
        class Socket:
            def __init__(self):
                self.sent = []
                self.raw = (b'Content-Type: auth/request\n\n'
                            b'Content-Type: command/reply\nReply-Text: +OK accepted\n\n'
                            b'Content-Type: command/reply\nReply-Text: +OK subscribed\n\n'
                            b'Content-Type: text/event-plain\nContent-Length: ' + str(len(body)).encode() + b'\n\n' + body)
            def recv(self, n):
                chunk, self.raw = self.raw[:11], self.raw[11:]
                return chunk
            def sendall(self, value):
                self.sent.append(value)
            def settimeout(self, timeout):
                pass
            def close(self):
                pass
        sock = Socket()
        os.environ['VOICE_TEST_ESL_PASSWORD'] = 'SECRET'
        self.addCleanup(os.environ.pop, 'VOICE_TEST_ESL_PASSWORD', None)
        def emit(item):
            records.append(item)
            if item['evidence'] == 'fs_event_gap':
                stop.set()
        esl.listen({'password_env': 'VOICE_TEST_ESL_PASSWORD'}, stop, emit, lambda *a, **k: sock)
        self.assertEqual([r['evidence'] for r in records], ['fs_event_status', 'fs_event', 'fs_event_gap'])
        self.assertEqual(records[1]['call_id'], 'short-call')
        self.assertTrue(sock.sent[1].startswith(b'event plain CHANNEL_CREATE'))
        self.assertNotIn('SECRET', json.dumps(records))

    def test_remote_agent_lifecycle_with_local_transport_and_fake_capture(self):
        # Execute the real deployed Python agent. Only SSH transport and packet
        # acquisition are simulated; status, freeze, hashing and release are real.
        remote=Path('/tmp')/('voice-tools-'+uuid4().hex[:12]);remote.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree,remote,True)
        binary=self.root/'bin';binary.mkdir()
        raw=pcap(self.root/'seed.pcap')
        program='''#!/usr/bin/env python3
import base64,pathlib,struct,sys,time
args=sys.argv[1:];target=pathlib.Path(args[args.index('-w')+1])
data=bytearray(base64.b64decode(DATA));offset=24;now=int(time.time())
while offset<len(data):
 struct.pack_into('<I',data,offset,now);size=struct.unpack_from('<I',data,offset+8)[0];offset+=16+size
for i in (1,2):
 (target.parent/('capture_%05d.pcap'%i)).write_bytes(data);time.sleep(.2)
time.sleep(1.6)
'''.replace('DATA',repr(base64.b64encode(raw).decode()))
        fake=binary/'dumpcap';fake.write_text(program);fake.chmod(0o700)
        env={**os.environ,'PATH':str(binary)+os.pathsep+os.environ['PATH']}
        class LocalSSH:
            def __init__(self,*args):pass
            def checked(self,args,timeout=30):
                if args[:2]==['sh','-c'] and 'mktemp' in args[2]:
                    return f'{remote}\n{os.getuid()}\n{os.getgid()}'
                result=subprocess.run(args,capture_output=True,text=True,timeout=timeout,env=env)
                if result.returncode:raise ValueError(result.stderr[-1000:])
                return result.stdout.strip()
            def copy(self,source,target):shutil.copyfile(source,target)
        config=self.root/'hosts.json';config.write_text(json.dumps({'schema_version':'1.0','hosts':[{
            'name':'fs-a','host':'fs-a','addresses':['192.0.2.1'],'rtp_ranges':[[16000,24000]]}]}))
        result=ring.start(config,self.root/'job',seconds=2,segment_seconds=10,max_mib=8,snapshot_seconds=0,ssh_factory=LocalSSH)
        self.assertEqual(result['hosts'][0]['status'],'running',result)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=ring.status(self.root/'job',LocalSSH)['hosts'][0]
            if state['status']!='running':break
            time.sleep(.2)
        self.assertEqual(state['status'],'complete',state)
        # PCAP seconds are deliberately integer timestamps, allow the adjacent fragment.
        frozen=ring.fetch(self.root/'job',self.root/'fetched',int(state['started_epoch']),state['updated_epoch'],LocalSSH)
        meta=json.loads((self.root/'fetched/fs-a/host.json').read_text())
        self.assertEqual(len(meta['files']),2,meta)
        self.assertTrue((self.root/'fetched/fs-a/events.jsonl').exists())
        self.assertEqual(ring.release(self.root/'job','fs-a',meta['freeze_id'],LocalSSH)['status'],'released')
        self.assertFalse((remote/'frozen'/meta['freeze_id']).exists())
