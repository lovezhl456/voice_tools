import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import unittest

from voice_tools.core.files import write_json
from voice_tools.tools.capture.batch import run_batch, scope_bpf, batch_command, fetch_batch
from voice_tools.tools.capture.snapshots import snapshot
from tests.sessions.fixtures import pcap


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = pcap(self.root/'fixture.pcap')
        self.hosts = [{'name': name, 'host': name, 'addresses': ['192.0.2.1'], 'sip_ports': [5060],
                       'rtp_ranges': [[16000, 32000]]} for name in ('fs-a', 'fs-b')]
        self.config = self.root/'hosts.json'
        write_json(self.config, {'schema_version': '1.0', 'hosts': self.hosts})

    def factory(self, barrier=None, broken=None):
        data = self.data
        class FakeSSH:
            def __init__(self, host, *args): self.host = host
            def checked(self, args, timeout=30):
                if args[0] == 'sh': return '/tmp/voice-tools-abcdefghijkl\noperator'
                if args[0] == 'find': return '/tmp/voice-tools-abcdefghijkl/part-20260919T120000.pcap'
                if args[0] == 'sha256sum': return hashlib.sha256(data).hexdigest() + ' file'
                raise AssertionError(args)
            def run(self, args, timeout=30):
                if barrier: barrier.wait(timeout=5)
                if self.host == broken: raise ValueError('simulated host failure')
                return subprocess.CompletedProcess(args, 124, '', '12 packets captured\n0 packets dropped by kernel\n')
            def copy(self, remote, local): Path(local).write_bytes(data)
        return FakeSSH

    def test_dry_run_never_constructs_ssh_connection(self):
        def fail(*args): raise AssertionError('connection attempted')
        result = run_batch(self.config, self.root/'plan', dry_run=True, ssh_factory=fail)
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(len(result['plans']), 2)
        self.assertIn('tcp', result['plans'][0]['bpf'])

    def test_hosts_capture_concurrently_and_transfer_fragments(self):
        result = run_batch(self.config, self.root/'out', seconds=1, snapshot_seconds=0,
                           ssh_factory=self.factory(threading.Barrier(2)))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['hosts']), 2)
        for host in result['hosts']:
            meta = json.loads((self.root/'out'/host['manifest']).read_text())
            self.assertEqual(meta['files'][0]['sha256'], hashlib.sha256(self.data).hexdigest())
            self.assertEqual(meta['kernel_drops'], 0)

    def test_failed_host_does_not_discard_other_host(self):
        result = run_batch(self.config, self.root/'out', seconds=1, snapshot_seconds=0,
                           ssh_factory=self.factory(broken='fs-b'))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual({h['status'] for h in result['hosts']}, {'failed', 'complete'})

    def test_scope_validation_and_rotation_bound(self):
        for args in [([], [5060], []), (['0.0.0.0/0'], [5060], []), (['192.0.2.1'], [], []),
                     (['192.0.2.1'], [0], []), (['192.0.2.1'], [], [[200, 100]])]:
            with self.assertRaises(ValueError): scope_bpf(*args)
        conf = {**self.hosts[0], 'bpf': 'udp', 'sudo': True}
        command = batch_command(conf, '/tmp/voice-tools-abcdefghijkl', 'operator', 300, 60, 100, 2048)
        inner = shlex.split(command[-1].split('exec ', 1)[1])
        self.assertEqual(inner[:2], ['sudo', '-n'])
        self.assertEqual(inner[inner.index('-W')+1], '6')
        self.assertNotIn('-C', inner)

    @unittest.skipUnless(shutil.which('tcpdump'), 'tcpdump unavailable')
    def test_batch_bpf_compiles_and_selects_scoped_packets(self):
        expression = scope_bpf(['192.0.2.1'], [5060], [[16000, 16000]])
        result = subprocess.run(['tcpdump', '-n', '-r', str(self.root/'fixture.pcap'), expression], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 9)  # six SIP messages + three selected RTP

    def test_recover_batch_fragments(self):
        result = fetch_batch(self.factory()('fs-a'), '/tmp/voice-tools-abcdefghijkl', self.root/'recover')
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(len(result['files']), 1)

    def test_snapshots_whitelist_fields_and_preserve_media_mapping(self):
        uuid = '11111111-2222-3333-4444-555555555555'
        class FS:
            def checked(self, args, timeout=30): return json.dumps({'rows': [{'uuid': uuid}]})
            def run(self, args, timeout=30):
                body = f'\n__VOICE_UUID__:{uuid}\nvariable_sip_call_id: call-a@example.net\nvariable_sip_auth_password: SECRET\n'
                body += 'variable_local_media_ip: 192.0.2.1\nvariable_remote_media_ip: 192.0.2.2\nvariable_local_media_port: 16000\nvariable_remote_media_port: 24000\n'
                return subprocess.CompletedProcess(args, 0, body, '')
        rows, warnings = snapshot(FS())
        self.assertEqual(warnings, [])
        self.assertEqual(rows[0]['flow']['remote_port'], 24000)
        self.assertNotIn('SECRET', json.dumps(rows))
