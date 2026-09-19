"""Independent failure cases for the number-capture review."""
import hashlib
import json
import shutil
import sys
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from voice_tools.core.files import read_json, sha256, write_json
from voice_tools.core.capture_contract import selectors
from voice_tools.tools.capture import numbers, number_remote
from voice_tools.tools.capture.service import SSH
from voice_tools.tools.sessions import number_bundle
from tests.capture import test_numbers as fixtures
from tests.sessions.fixtures import CALL_A, CALL_B, packet
from tests.report.test_v2 import rtp


class NumberReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NumberCaptureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def bundle(self):
        root, config = self.fixture.seed()
        number_bundle.process(root, config)
        return root, config, read_json(root / 'number-status.json')

    def rewrite(self, root, mutate):
        with zipfile.ZipFile(root / 'sessions.zip') as archive:
            bodies = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(bodies['manifest.json'])
        manifest = mutate(manifest, bodies)
        bodies['manifest.json'] = json.dumps(manifest).encode()
        path = self.root / 'changed.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, body in bodies.items():
                archive.writestr(name, body)
        return path, {'file': 'sessions.zip', 'bytes': path.stat().st_size, 'sha256': sha256(path)}

    def test_bad_fs_row_keeps_other_mapping_rows(self):
        root, config = self.fixture.seed([(0, packet(rtp(1), 16000, 24000))])
        good = dict(schema_version='1.0', call_id=CALL_A, caller='1001', callee='1002',
                    uuid='11111111-2222-3333-4444-555555555555', evidence='fs_snapshot',
                    observed_at='2026-09-15T08:00:00+00:00', window_seconds=10,
                    flow=dict(local_ip='192.0.2.1', local_port=16000, remote_ip='192.0.2.2', remote_port=24000))
        good['media'] = ['invalid cross-protocol field']
        (root / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in
            (good, dict(good, observed_at='broken'), {'schema_version': 'wrong'})) + '\n')
        number_bundle.process(root, config)
        state = read_json(root / 'number-status.json')
        self.assertEqual((state['status'], state['exported_sessions']), ('partial', 1), state)

    def test_declared_session_count_must_match_actual_members(self):
        root, _, _ = self.bundle()
        def mutate(manifest, bodies):
            manifest['exported_sessions'] = 99
            return manifest
        path, info = self.rewrite(root, mutate)
        with self.assertRaises(ValueError): numbers.verify_archive(path, info, 8 * 1048576)

    def test_each_session_requires_pcap_and_inner_manifest(self):
        root, _, _ = self.bundle()
        def mutate(manifest, bodies):
            for name in list(bodies):
                if name != 'manifest.json': del bodies[name]
            manifest['sessions'][0]['files'] = []
            return manifest
        path, info = self.rewrite(root, mutate)
        with self.assertRaises(ValueError): numbers.verify_archive(path, info, 8 * 1048576)

    def test_inner_session_identity_must_agree_with_bundle(self):
        root, _, _ = self.bundle()
        def mutate(manifest, bodies):
            entry = next(f for f in manifest['sessions'][0]['files'] if f['file'].endswith('session.json'))
            value = json.loads(bodies[entry['file']]); value['call_id'] = CALL_B
            bodies[entry['file']] = json.dumps(value).encode()
            entry.update(bytes=len(bodies[entry['file']]), sha256=hashlib.sha256(bodies[entry['file']]).hexdigest())
            return manifest
        path, info = self.rewrite(root, mutate)
        with self.assertRaises(ValueError): numbers.verify_archive(path, info, 8 * 1048576)

    def test_download_must_match_requested_selection(self):
        root, _, state = self.bundle()
        row = dict(name='fs-a', host='fs-a', remote_dir='/tmp/voice-tools-' + 'a'*32,
                   seconds=1, processing_seconds=30, bundle_mib=8, selection=selectors(['9999'], []))
        class SSH:
            def __init__(self, *args): pass
            def copy(self, remote, local, stop=None): shutil.copyfile(root / 'sessions.zip', local)
        with patch.object(numbers, 'remote_state', return_value=state):
            result = numbers.collect_host(row, self.root, threading.Event(), False, SSH)
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse((self.root / 'fs-a/sessions.zip').exists())

    def test_invalid_manifest_type_does_not_abort_other_hosts(self):
        root, _, state = self.bundle()
        path, info = self.rewrite(root, lambda m, b: [])
        rows = [dict(name=name, host=name, remote_dir='/tmp/voice-tools-' + 'a'*32,
                     seconds=1, processing_seconds=30, bundle_mib=8) for name in ('fs-a', 'fs-b')]
        class SSH:
            def __init__(self, host, *args): self.host = host
            def copy(self, remote, local, stop=None): shutil.copyfile(root / 'sessions.zip' if self.host == 'fs-a' else path, local)
        def status(ssh, directory):
            return state if ssh.host == 'fs-a' else {**state, 'name': 'fs-b', 'archive': info}
        with patch.object(numbers, 'remote_state', side_effect=status):
            result = numbers.collect({'selection': selectors(['1001'], []), 'hosts': rows}, self.root, False, SSH)
        self.assertEqual({h['name']: h['status'] for h in result['hosts']}, {'fs-a': 'complete', 'fs-b': 'failed'})

    def test_producer_file_limit_keeps_previously_finished_sessions(self):
        root, config = self.fixture.seed([(0, packet(fixtures.invite(CALL_A))),
                                         (.01, packet(fixtures.invite(CALL_B, port=16002)))])
        with patch.object(number_bundle, 'MAX_BUNDLE_FILES', 3, create=True):
            number_bundle.process(root, config)
        state = read_json(root / 'number-status.json')
        self.assertEqual((state['status'], state['exported_sessions']), ('partial', 1))
        numbers.verify_archive(root / 'sessions.zip', state['archive'], 8 * 1048576)

    def test_manifest_budget_keeps_a_valid_empty_partial_bundle(self):
        root, config = self.fixture.seed()
        with patch.object(number_bundle, 'MAX_BUNDLE_MANIFEST_BYTES', 4500):
            number_bundle.process(root, config)
        state = read_json(root / 'number-status.json')
        self.assertEqual((state['status'], state['exported_sessions']), ('partial', 0))
        numbers.verify_archive(root / 'sessions.zip', state['archive'], 8 * 1048576)

    def test_invalid_utf8_event_does_not_abort_packet_export(self):
        root, config = self.fixture.seed()
        (root / 'events.jsonl').write_bytes(b'\xff\n')
        number_bundle.process(root, config)
        state = read_json(root / 'number-status.json')
        self.assertEqual((state['status'], state['exported_sessions']), ('partial', 1))

    def test_scp_cancellation_terminates_local_transfer(self):
        stop = threading.Event()
        timer = threading.Timer(.15, stop.set); timer.start(); self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, '取消'):
            SSH.copy_cancellable([sys.executable, '-c', 'import time; time.sleep(30)'], stop)
        self.assertLess(time.monotonic() - started, 3)

    def test_scp_cancel_also_reaps_helper_after_transfer_parent_exits(self):
        stop = threading.Event()
        timer = threading.Timer(.4, stop.set); timer.start(); self.addCleanup(timer.cancel)
        script = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])"
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, '取消'):
            SSH.copy_cancellable([sys.executable, '-c', script], stop)
        self.assertLess(time.monotonic() - started, 3)

    def test_many_export_errors_have_a_bounded_manifest(self):
        root, config = self.fixture.seed()
        rows = [dict(call_id='通'*1000+str(i),caller='1001',callee='1002',basis='fs_roles',uuid=None) for i in range(100)]
        with patch.object(number_bundle, 'select_calls', return_value=(rows, 100, 100)), \
             patch('voice_tools.tools.sessions.export.export', side_effect=OSError('错'*500)):
            number_bundle.process(root, config)
        state = read_json(root / 'number-status.json')
        manifest = numbers.verify_archive(root/'sessions.zip', state['archive'], 8*1048576)
        self.assertEqual((manifest['status'],manifest['error_count'],len(manifest['errors'])),('partial',100,64))

    def test_recovery_rejects_unbounded_or_malformed_duration(self):
        job = self.root / 'job'; job.mkdir()
        value = dict(schema_version='1.0', tool='capture-ring-job', purpose='capture-by-number',
                     selection=selectors(['1001'], []), hosts=[dict(name='fs-a',host='fs-a',
                     remote_dir='/tmp/voice-tools-'+'a'*32, seconds=10**30, bundle_mib=8, processing_seconds=30)])
        write_json(job / 'job.json', value)
        with self.assertRaises(ValueError): numbers.fetch(job, self.root / 'recovery')
        self.assertFalse((self.root / 'recovery').exists())

    def test_success_exit_without_final_receipt_is_failure(self):
        root, config = self.fixture.seed(); write_json(root / 'config.json', config)
        with patch.object(sys, 'argv', ['worker', 'run', str(root)]), patch.object(number_remote, 'check'), \
             patch.object(number_remote, 'Agent'), patch.object(number_remote.subprocess, 'Popen') as spawn:
            spawn.return_value.wait.return_value = 0
            number_remote.main()
        self.assertEqual(read_json(root / 'number-status.json')['status'], 'failed')


if __name__ == '__main__': unittest.main()
