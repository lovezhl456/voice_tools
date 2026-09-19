import hashlib
import json
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from voice_tools.tools.capture.service import SSH, bpf_for, capture_argv, discover, fetch, flow, start


UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PCAP = struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 2048, 1) + struct.pack("<IIII", 1, 0, 1, 1) + b"x"


class FakeSSH:
    host, port = "test-fs", 22

    def __init__(self, rc=124):
        self.calls = []
        self.rc = rc
        self.fail_copy = False
        self.corrupt = False
        self.data = PCAP
        self.dumps = {
            UUID_A: f"variable_local_media_ip: 10.0.0.1\nvariable_local_media_port: 16000\nvariable_remote_media_ip: 10.0.0.2\nvariable_remote_media_port: 24000\nvariable_bridge_uuid: {UUID_B}\nvariable_sip_auth_password: do-not-save\n",
            UUID_B: "variable_local_media_ip: 10.0.0.1\nvariable_local_media_port: 16002\nvariable_remote_media_ip: 10.0.0.3\nvariable_remote_media_port: 24002\n"}

    def checked(self, argv, timeout=30):
        self.calls.append(argv)
        if argv[0] == "fs_cli":
            return self.dumps[argv[2].split()[1]]
        if argv[0] == "sha256sum":
            return hashlib.sha256(self.data).hexdigest() + "  session.pcap"
        if argv[:2] == ["sh", "-c"]:
            return "/tmp/voice-tools-abcdefghijkl\noperator"
        raise AssertionError(argv)

    def run(self, argv, timeout=30):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, "", "3 packets captured\n0 packets dropped by kernel\n")

    def copy(self, remote, local):
        if self.fail_copy:
            raise ValueError("transfer failed")
        Path(local).write_bytes(self.data + (b"bad" if self.corrupt else b""))


class CaptureTests(unittest.TestCase):
    def test_uuid_and_peer_whitelist(self):
        ssh = FakeSSH()
        flows, warnings = discover(ssh, UUID_A)
        self.assertEqual(len(flows), 2)
        self.assertEqual(flows[1]['remote_port'], 24002)
        self.assertEqual(warnings, [])
        self.assertNotIn('do-not-save', json.dumps(flows))
        self.assertEqual(len(discover(ssh, UUID_A, include_peer=False)[0]), 1)

    def test_no_call_or_no_primary_media_refuses_capture(self):
        ssh = FakeSSH()
        for dump in ('-ERR No such channel!', 'variable_local_media_port: 0'):
            ssh.dumps[UUID_A] = dump
            with self.assertRaises(ValueError):
                discover(ssh, UUID_A)

    def test_peer_failure_explicit_warning(self):
        ssh = FakeSSH()
        ssh.dumps[UUID_B] = '-ERR No such channel'
        flows, warnings = discover(ssh, UUID_A)
        self.assertEqual(len(flows), 1)
        self.assertIn('桥接腿查询失败', warnings[0])

    def test_invalid_endpoints_and_ssh_injection(self):
        for value in ('x;id', '-oProxyCommand=whoami', 'host:/tmp/x', 'a b'):
            with self.assertRaises(ValueError):
                SSH(value)
        for args in [('0.0.0.0', 1, '10.0.0.1', 2), ('::1', 1, '10.0.0.1', 2),
                     ('1.1.1.1', '2;id', '2.2.2.2', 3), ('1.1.1.1', 1, '2.2.2.2', 65536)]:
            with self.assertRaises(ValueError):
                flow(*args)
        with self.assertRaises(ValueError):
            bpf_for([])

    def test_host_checking_and_remote_shell_quoting(self):
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            SSH('operator@fs.example').run(['echo', '$(touch /tmp/bad); hello'])
            argv = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertIn('BatchMode=yes', argv)
            self.assertEqual(shlex.split(argv[-1]), ['echo', '$(touch /tmp/bad); hello'])

    def test_manual_dry_run_is_offline_and_size_bound(self):
        ssh = FakeSSH()
        with tempfile.TemporaryDirectory() as temp:
            result = start(ssh, Path(temp) / 'plan', manual_flows=[flow('10.0.0.1', 1, '10.0.0.2', 2)], dry_run=True)
            self.assertEqual(ssh.calls, [])
            self.assertEqual(result['status'], 'planned')
            self.assertLessEqual(24 + result['packet_limit'] * (result['snaplen'] + 16), 64 * 1024 * 1024)
            self.assertIn('dst host 10.0.0.1', result['bpf'])

    def test_capture_transfer_and_timeout_success(self):
        for rc, status in ((124, 'complete'), (0, 'partial'), (1, 'partial')):
            with self.subTest(rc=rc), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'out'
                result = start(FakeSSH(rc), output, uuid=UUID_A, sudo=True)
                self.assertEqual(result['status'], status)
                self.assertEqual((output / 'session.pcap').read_bytes(), PCAP)
                self.assertEqual(result['pcap']['sha256'], hashlib.sha256(PCAP).hexdigest())
                self.assertEqual(result['tcpdump_stats']['dropped_by_kernel'], 0)
                self.assertNotIn('do-not-save', (output / 'capture.json').read_text())
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)

    def test_failure_keeps_recovery_manifest_and_corruption_rejected(self):
        for failure in ('fail_copy', 'corrupt'):
            with tempfile.TemporaryDirectory() as temp:
                ssh = FakeSSH()
                setattr(ssh, failure, True)
                output = Path(temp) / 'out'
                with self.assertRaises(ValueError):
                    start(ssh, output, uuid=UUID_A)
                manifest = json.loads((output / 'capture.json').read_text())
                self.assertEqual(manifest['status'], 'failed')
                self.assertEqual(manifest['remote_dir'], '/tmp/voice-tools-abcdefghijkl')
                self.assertFalse((output / 'session.pcap').exists())

    def test_empty_capture_and_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            ssh = FakeSSH()
            ssh.data = PCAP[:24]
            result = start(ssh, Path(temp) / 'empty', uuid=UUID_A)
            self.assertEqual(result['status'], 'no_packets')
            ssh.data = PCAP
            result = fetch(ssh, '/tmp/voice-tools-abcdefghijkl', Path(temp) / 'recovered')
            self.assertEqual(result['status'], 'recovered')
            with self.assertRaises(ValueError):
                fetch(ssh, '/etc', Path(temp) / 'bad')

    @unittest.skipUnless(shutil.which('tcpdump'), 'tcpdump unavailable')
    def test_generated_ipv4_ipv6_bpf_compiles_without_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'empty.pcap'
            source.write_bytes(PCAP[:24])
            for addresses in [('10.0.0.1', '10.0.0.2'), ('2001:db8::1', '2001:db8::2')]:
                expression = bpf_for([flow(addresses[0], 16000, addresses[1], 24000)])
                result = subprocess.run(['tcpdump', '-r', str(source), '-ddd', expression], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_bounded_command(self):
        argv = capture_argv('/tmp/voice-tools-abcdefghijkl', 'operator', 'udp', 'any', 60, 10, 2048, True)
        self.assertEqual(argv[:6], ['sudo', '-n', 'timeout', '--signal=INT', '--kill-after=5s', '60s'])
        self.assertIn('-c', argv)
        self.assertEqual(argv[-1], 'udp')
