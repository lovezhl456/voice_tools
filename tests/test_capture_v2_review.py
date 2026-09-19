"""Adversarial regressions found in the second capture 2.0 review."""
import json
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from voice_tools.audio.rtp import parse_rtp, reconstruct, segments
from voice_tools.core.files import sha256
from voice_tools.tools.capture.remote import Agent
from voice_tools.tools.capture import ring
from voice_tools.tools.capture.esl import iso
from voice_tools.tools.sessions.media import timeline
from voice_tools.tools.sessions.store import build as index
from voice_tools.tools.sessions.export import export
from voice_tools.tools.report.media import codec_for
from voice_tools.tools.report.service import build as report
from tests.sessions.fixtures import CALL_A, T0, packet, pcap, sip
from tests.report.test_v2 import rtp


class CaptureReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def agent(self):
        config = dict(name='fs-a', host='fs-a', sensor_id='fs-a:any', seconds=10,
                      segment_seconds=10, max_mib=8, snaplen=65535, mode='ring',
                      ring_files=4, bpf='udp', snapshot_seconds=0)
        agent = Agent(self.root, config)
        agent.started = T0 - 10
        return agent

    def test_freeze_preserves_last_media_mapping_before_nonmedia_event(self):
        agent = self.agent()
        pcap(self.root/'spool/capture.pcap', [(5, packet(rtp(1), 16000, 24000))])
        row = dict(schema_version='1.0', evidence='fs_event', uuid=str(uuid4()),
                   call_id=CALL_A, observed_at=iso(T0), event='CHANNEL_ANSWER',
                   flow=dict(local_ip='192.0.2.1', local_port=16000, remote_ip='192.0.2.2', remote_port=24000))
        agent.emit(row)
        agent.emit(dict(row, observed_at=iso(T0+1), event='CHANNEL_CALLSTATE', flow=None))
        result = agent.freeze(dict(id=uuid4().hex, from_epoch=T0+4, to_epoch=T0+6))
        events = [json.loads(line) for line in (Path(result['remote_export'])/'events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('flow') == row['flow'] for e in events), events)

    def test_stop_request_does_not_publish_immutable_status_before_exit(self):
        agent = self.agent()
        class Running:
            def poll(self):
                return None
        agent.process = Running()
        agent.running = True
        agent.forced_reason = 'requested_stop'
        self.assertEqual(agent.status()['status'], 'running')

    def test_equal_mtime_never_makes_an_open_file_freezable(self):
        agent = self.agent()
        # Deliberately create the active file first and give both files one mtime.
        active = self.root/'spool/capture_00002.pcap'
        closed = self.root/'spool/capture_00001.pcap'
        pcap(active); pcap(closed)
        os.utime(active, (T0, T0)); os.utime(closed, (T0, T0))
        class Running:
            def poll(self):
                return None
        agent.process = Running()
        # No close notification: an apparently stable, complete file can be open.
        self.assertEqual(agent.files(), [])

    def test_srtp_offer_protects_both_directions(self):
        mappings = [{'from_epoch': T0, 'media': {'ip': '192.0.2.1', 'port': 16000,
                     'protocol': 'RTP/SAVP', 'codecs': {}}}]
        _, encrypted = codec_for({'pt': 0}, '192.0.2.1', 16000, '192.0.2.2', 24000, T0+1, mappings)
        self.assertTrue(encrypted, 'One-sided SAVP signaling must not decode the other direction as plaintext')

    def test_timestamp_regression_after_first_packet_is_rejected(self):
        packets = []
        for seq, stamp in ((1, 1000), (2, 1320), (3, 1160)):
            value = parse_rtp(rtp(seq, stamp))
            value['codec'] = {'name': 'PCMU', 'clock_rate': 8000}
            packets.append(value)
        result, used = reconstruct(segments(packets)[0], self.root, 'backward', 1048576)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(used, 0)

    def test_reverse_tag_reinvite_replaces_same_sender_media(self):
        def observation(when, port, tags):
            return {'host': 'fs-a', 'epoch': when, 'data': {'src': '192.0.2.1',
                    'from_tag': tags[0], 'to_tag': tags[1], 'media': [{'ip': '192.0.2.1', 'port': port}]}}
        result = timeline([observation(1, 16000, ('a', 'b')), observation(2, 16002, ('b', 'a'))])
        self.assertEqual(result[0]['until_epoch'], 2)

    def test_late_hangup_does_not_extend_expired_snapshot(self):
        flow = dict(local_ip='192.0.2.1', local_port=16000, remote_ip='192.0.2.2', remote_port=24000)
        result = timeline([{'host': 'fs-a', 'epoch': 10, 'data': {'uuid': 'u', 'flow': flow,
                    'evidence': 'fs_snapshot', 'window_seconds': 5}},
                   {'host': 'fs-a', 'epoch': 100, 'data': {'uuid': 'u', 'event': 'CHANNEL_HANGUP_COMPLETE'}}])
        self.assertEqual(result[0]['until_epoch'], 15)

    def test_bye_for_one_fork_does_not_truncate_open_fork_export(self):
        source = self.root/'forks.pcap'
        second = sip(CALL_A, '200', 24002, '192.0.2.2').replace(b'tag=b', b'tag=c')
        pcap(source, [(0, packet(sip(CALL_A, '200', 24000, '192.0.2.2'), src='192.0.2.2', dst='192.0.2.1')),
                      (.1, packet(second, src='192.0.2.2', dst='192.0.2.1')),
                      (1, packet(sip(CALL_A, 'BYE', 0))), (2, packet(rtp(1), 16002, 24002))])
        index(self.root/'index', [source])
        result = export(self.root/'index', CALL_A, self.root/'export', include_media=True, padding=0)
        self.assertEqual(result['files'][0]['packets'], 4)

    def test_report_keeps_known_calls_separate_when_ssrc_and_ports_reused(self):
        inputs = []
        for n in (1, 2):
            folder = self.root/str(n); folder.mkdir(); inputs.append(folder)
            source = folder/'call.pcap'
            pcap(source, [(n, packet(rtp(1), 16000, 24000)), (n+.02, packet(rtp(2), 16000, 24000))])
            (folder/'session.json').write_text(json.dumps({'schema_version': '1.0', 'tool': 'sessions-export',
                'call_id': 'call'+str(n), 'files': [{'file': source.name, 'sha256': sha256(source),
                'host': 'fs-a', 'sensor_id': 'fs-a:any', 'rtp_ports': [16000, 24000]}]}))
        data = report(self.root/'report', session_exports=inputs, decode_rtp=True)
        self.assertEqual(len(data['pcaps']), 2)
        self.assertTrue(all(p['analysis']['streams'][0]['duplicate_candidates'] == 0 for p in data['pcaps']))

    def test_size_rotation_freezes_without_waiting_for_time_rotation(self):
        agent = self.agent()
        path = self.root/'spool/capture_00001.pcap'
        pcap(path)
        class Running:
            def poll(self):
                return None
        agent.process = Running()
        agent.mark_closed(str(path))
        token = uuid4().hex
        (self.root/'requests'/f'{token}.json').write_text(json.dumps(dict(
            id=token, action='freeze', from_epoch=T0, to_epoch=T0+.1)))
        with patch('voice_tools.tools.capture.remote.time.time', return_value=T0+2):
            agent.requests()
        self.assertTrue((self.root/'replies'/f'{token}.json').is_file())

    def test_damaged_event_keeps_capture_and_marks_gap(self):
        agent = self.agent()
        pcap(self.root/'spool/capture.pcap')
        (self.root/'events.jsonl').write_text('{"truncated":\n')
        result = agent.freeze(dict(id=uuid4().hex, from_epoch=T0, to_epoch=T0+2))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['manifest']['files']), 1)
        rows = (Path(result['remote_export'])/'events.jsonl').read_text()
        self.assertIn('fs_event_gap', rows)

    def test_rotation_during_directory_scan_does_not_abort_capture(self):
        agent = self.agent()
        path = self.root/'spool/gone.pcap'
        pcap(path)
        actual = Path.lstat
        def disappear(p):
            if p == path:
                raise FileNotFoundError()
            return actual(p)
        with patch.object(Path, 'lstat', disappear):
            self.assertEqual(agent.files(), [])
        self.assertTrue(agent.evicted)

    def test_capture_log_is_bounded_under_frequent_rotation(self):
        agent = self.agent()
        class Fake:
            stderr = io.BytesIO((b'File: '+b'x'*200+b'\n')*20000)
        agent.process = Fake()
        agent.observe_capture_log()
        self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob('capture*.log')), 2*1048576)
        self.assertFalse(agent.stop.is_set())

    def test_status_heartbeat_uses_remote_clock(self):
        job = self.root/'job.json'
        job.write_text(json.dumps({'schema_version': '1.0', 'tool': 'capture-ring-job',
            'hosts': [{'name': 'fs-a', 'host': 'fs-a', 'remote_dir': '/tmp/voice-tools-abcdefghijkl'}]}))
        with patch.object(ring, 'remote_json', return_value={
                'status': 'running', 'updated_epoch': 100, '_remote_read_epoch': 101}):
            self.assertEqual(ring.status(job)['hosts'][0]['status'], 'running')

    def test_interrupted_startup_retains_path_before_remote_mutation(self):
        inventory = self.root/'hosts.json'
        inventory.write_text(json.dumps({'schema_version': '1.0', 'hosts': [
            {'name': 'fs-a', 'host': 'fs-a', 'addresses': ['192.0.2.1'], 'rtp_ranges': [[16000, 24000]]}]}))
        test = self
        class InterruptedSSH:
            def __init__(self, *args):
                pass
            def checked(self, args, **kwargs):
                job = json.loads((test.root/'job/job.json').read_text())
                test.assertEqual(len(job['hosts']), 1)
                test.assertTrue(job['hosts'][0]['remote_dir'].startswith('/tmp/voice-tools-'))
                if args[0] == 'sh':
                    test.assertIn(job['hosts'][0]['remote_dir'], args[2])
                    return '1000\n1000'
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            ring.start(inventory, self.root/'job', ssh_factory=InterruptedSSH)
        job = ring.load_job(self.root/'job')
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual(job['hosts'][0]['status'], 'preparing')

    def test_snapshot_without_call_id_is_retained_as_incomplete_evidence(self):
        agent = self.agent()
        uid = str(uuid4())
        response = subprocess.CompletedProcess([], 0, 'Channel-State: CS_EXECUTE\n', '')
        with patch('voice_tools.tools.capture.remote.subprocess.run', return_value=response):
            agent.snapshot([uid])
        path = self.root/'events.jsonl'
        self.assertTrue(path.exists())
        result = index(self.root/'index', events=[path])
        self.assertTrue(result['partial'])

    def test_direct_pcap_index_propagates_capture_truncation(self):
        source = self.root/'truncated.pcap'
        raw = bytearray(pcap(source))
        captured = struct.unpack_from('<I', raw, 32)[0]
        struct.pack_into('<I', raw, 36, captured+100)
        source.write_bytes(raw)
        result = index(self.root/'index', [source])
        self.assertTrue(result['partial'])
        self.assertEqual(result['sources'][0]['details']['truncated_packets'], 1)

    def test_corrupt_event_log_cannot_amplify_beyond_freeze_quota(self):
        agent = self.agent()
        agent.config['frozen_mib'] = 1
        pcap(self.root/'spool/capture.pcap')
        (self.root/'events.jsonl').write_text('!\n'*16000)
        result = agent.freeze(dict(id=uuid4().hex, from_epoch=T0, to_epoch=T0+2))
        self.assertEqual(result['status'], 'partial')
        frozen = Path(result['remote_export'])
        self.assertLessEqual(sum(p.stat().st_size for p in frozen.iterdir()), 1048576)
        rows = [json.loads(line) for line in (frozen/'events.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['invalid_records'], 16000)

    def test_snapshot_preceding_window_can_select_an_earlier_pcap(self):
        source = self.root/'before-snapshot.pcap'
        pcap(source, [(5, packet(rtp(1), 16000, 24000))])
        events = self.root/'events.jsonl'
        events.write_text(json.dumps({'schema_version': '1.0', 'evidence': 'fs_snapshot', 'uuid': str(uuid4()),
            'call_id': CALL_A, 'observed_at': iso(T0+10), 'window_seconds': 10,
            'flow': dict(local_ip='192.0.2.1', local_port=16000, remote_ip='192.0.2.2', remote_port=24000)})+'\n')
        index(self.root/'index', [source], events=[events])
        result = export(self.root/'index', CALL_A, self.root/'export', include_media=True, padding=0)
        self.assertEqual(result['files'][0]['packets'], 1)

    @unittest.skipUnless(shutil.which('dumpcap'), 'requires native dumpcap')
    def test_native_dumpcap_agent_rotation_via_fifo(self):
        # Replay synthetic frames through a FIFO, never capture a real interface.
        agent = self.agent()
        agent.started = time.time()
        fifo = self.root/'input.pcap'
        os.mkfifo(fifo)
        agent.config.update(interface=str(fifo), seconds=3, max_mib=1, ring_files=2)
        payload = pcap(self.root/'seed.pcap', [(i*.02, packet(rtp(i+1), 16000, 24000)) for i in range(5000)])
        def feed():
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError:
                    time.sleep(.02)
            else:
                return
            os.set_blocking(fd, True)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
            except BrokenPipeError:
                pass
        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        agent.run()
        writer.join(timeout=6)
        self.assertGreater(agent.closed_notifications, 2)
        self.assertEqual(len(agent.files()), 2)
        self.assertLessEqual(sum(p.stat().st_size for p in (self.root/'spool').glob('*.pcap')), 1048576)
        self.assertEqual(agent.capture_health['capture_dropped_packets'], 0)
        self.assertTrue(agent.evicted)
