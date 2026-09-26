"""Boundary and failure-path regressions found during the post-merge audit."""
import importlib.util
import json
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from voice_tools.core.files import write_json
from voice_tools.tools.sip import service
from voice_tools.tools.sip.pcap import media_from_stream
from voice_tools.tools.sip.scenario import load_scenario, template
from tests.sip.fixtures import tone
from tests.sip import test_runner


class InputBoundaries(unittest.TestCase):
    def test_malformed_json_values_stay_in_cli_error_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scenario.json'
            for update in ({'steps': [{'action': []}]}, {'steps': [{'action': {}}]},
                           {'network': {'bind_address': True}}, {'max_call_s': 10 ** 400},
                           {'assertions': {}}, {'assertions': [{'type': 'received_rtp', 'min_packets': True}]},
                           {'assertions': [{'type': 'dtmf', 'digits': 'X'}]}):
                with self.subTest(update=update):
                    write_json(path, {**template(), **update})
                    p = subprocess.run([sys.executable, '-m', 'voice_tools', '--json', 'sip', 'validate', str(path)], capture_output=True, text=True)
                    self.assertEqual(p.returncode, 2, p.stderr)
                    self.assertFalse(json.loads(p.stdout)['ok'])
                    self.assertNotIn('Traceback', p.stderr)

    def test_import_rejects_dtmf_that_cannot_be_loaded(self):
        audio = {'seq': 0, 'timestamp': 0, 'pt': 8, 'payload': b'\xd5' * 160}
        cases = [[audio, {'seq': 1, 'timestamp': 160, 'pt': 101, 'payload': struct.pack('!BBH', 1, 0x80, 65535)}],
                 [audio] + [{'seq': n + 1, 'timestamp': 160 + n * 800, 'pt': 101, 'payload': struct.pack('!BBH', 1, 0x80, 320)} for n in range(257)]]
        for packets in cases:
            with self.subTest(packets=len(packets)), self.assertRaises(ValueError):
                media_from_stream({'packets': packets}, dtmf_pt=101)


class ServiceFailures(unittest.TestCase):
    def run_worker(self, root, worker):
        spec = template(); spec['target_uri'] = 'sip:peer@127.0.0.1:5090'
        write_json(root / 'scenario.json', spec)
        with patch.object(service.importlib.util, 'find_spec', return_value=object()), patch.object(service.subprocess, 'Popen', worker):
            return service.run(root / 'scenario.json', root / 'run')

    def test_worker_timeout_is_not_operator_interrupt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            class Worker:
                returncode = None
                def __init__(self, *a, **k): pass
                def poll(self): return self.returncode
                def wait(self, timeout=None):
                    if self.returncode is None: raise subprocess.TimeoutExpired('worker', timeout)
                    return self.returncode
                def terminate(self): self.returncode = -signal.SIGTERM
            result = self.run_worker(root, Worker)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'WORKER_TIMEOUT')

    def test_truncated_nonempty_recording_cannot_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            class Worker:
                def __init__(self, *a, **k):
                    out = root / 'run'; tone(out / 'rx.wav')
                    path = out / 'rx.wav'; path.write_bytes(path.read_bytes()[:-100])
                    write_json(out / 'result.json', {'status': 'completed'})
                def wait(self, timeout=None): return 0
            result = self.run_worker(root, Worker)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'RECORDING_INCOMPLETE')

    def test_corrupt_worker_receipt_is_execution_failure_and_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            class Worker:
                def __init__(self, *a, **k):
                    (root / 'run/result.json').write_text('{"status":')
                def wait(self, timeout=None): return 0
            result = self.run_worker(root, Worker)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'WORKER_RESULT_INVALID')
            self.assertEqual((root / 'run/worker-result.invalid.json').read_text(), '{"status":')


class FinalizationFailure(unittest.TestCase):
    def test_details_failure_does_not_skip_native_cleanup(self):
        fixture = test_runner.RunnerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def configure(backend):
            def broken(): raise RuntimeError('details unavailable')
            backend.details = broken
        result = fixture.execute(configure)
        self.assertTrue(fixture.backend.closed)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue((fixture.root / 'result.json').exists())

    def test_cleanup_failure_keeps_original_call_failure(self):
        fixture = test_runner.RunnerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        def configure(backend):
            backend.failure = 'media transport failed'
            def broken(): backend.closed = True; raise RuntimeError('cleanup failed')
            backend.close = broken
        result = fixture.execute(configure)
        self.assertEqual(result['error']['code'], 'MEDIA_ERROR')
        self.assertEqual(result['secondary_errors'][0]['code'], 'CLEANUP_ERROR')
        self.assertTrue(fixture.backend.closed)

    def test_final_journal_failure_still_saves_result(self):
        from voice_tools.tools.sip.runner import Journal
        fixture = test_runner.RunnerTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original = Journal.emit
        def emit(journal, event, **fields):
            if event == 'run_end': raise OSError('journal unavailable')
            return original(journal, event, **fields)
        with patch.object(Journal, 'emit', emit): result = fixture.execute()
        self.assertTrue(fixture.backend.closed)
        self.assertEqual(result['error']['code'], 'JOURNAL_ERROR')
        self.assertTrue((fixture.root / 'result.json').exists())


class NativeAdapterLifecycle(unittest.TestCase):
    def setUp(self):
        # Import the adapter against a tiny SDK boundary, without requiring PJSUA2.
        from voice_tools.tools.sip import runner
        fake = types.SimpleNamespace(Call=object, Account=object, AudioMediaPlayer=object, AudioMediaPort=object,
                                     PJMEDIA_TYPE_AUDIO=1, PJSUA_CALL_MEDIA_ACTIVE=1, Error=RuntimeError)
        path = Path(runner.__file__).with_name('pjsua.py')
        spec = importlib.util.spec_from_file_location('voice_tools.tools.sip._review_adapter', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'pjsua2': fake}): spec.loader.exec_module(module)
        self.module = module
        self.backend = module.Backend.__new__(module.Backend)
        b = self.backend
        b.connected = True; b.disconnected = False; b.media_ready = True
        b.recorder = object(); b.player = None; b.record_started = 1.0
        b.observation = b.rx_tap = None
        b.plan = {'record_early': True}; b.clock = lambda: 2.0; b.emit = lambda *a, **k: None


    def test_inactive_media_clears_readiness(self):
        b = self.backend; b.audio = None
        b.call = types.SimpleNamespace(getInfo=lambda: types.SimpleNamespace(media=[]))
        b.attach_media()
        self.assertFalse(b.media_ready)

    def test_shutdown_exception_still_destroys_endpoint(self):
        b = self.backend; calls = []
        b.closed = False; b.call = b.audio = b.recorder = None; b.rejected_calls = []
        b.hangup = lambda: None; b.stop_playback = lambda: None
        def failed(): raise RuntimeError('account shutdown failed')
        b.account = types.SimpleNamespace(shutdown=failed)
        b.ep = types.SimpleNamespace(libDestroy=lambda: calls.append('destroy'))
        with self.assertRaises(RuntimeError): b.close()
        self.assertEqual(calls, ['destroy'])
