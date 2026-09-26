"""Assertion semantics: receive-only evidence, negative cases and corrupt artifacts."""
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from voice_tools.core.files import write_json
from voice_tools.tools.sip.assertions import apply_assertions, evaluate, validate_assertions
from voice_tools.tools.sip.scenario import load_scenario, template
from tests.sip.test_runner import Clock, FakeBackend
from voice_tools.tools.sip.runner import execute


class AssertionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.result = {'status': 'completed', 'call': {'assertion_evidence': {
            'invite_final_code': 200, 'rx_rtp_packets_lower_bound': 20,
            'rtp_final_sample': True, 'received_dtmf': '11#'}}}

    def wav(self, frequencies=(440, 480), seconds=.8, noise=False, dc=False):
        t = np.arange(int(seconds * 8000)) / 8000
        signal = (np.random.default_rng(4).normal(0, .2, len(t)) if noise else
                  np.full(len(t), .2) if dc else
                  sum((.15 * np.sin(2 * np.pi * f * t) for f in frequencies), np.zeros(len(t))))
        with wave.open(str(self.root / 'rx.wav'), 'wb') as w:
            w.setparams((1, 2, 8000, 0, 'NONE', '')); w.writeframes((signal * 32767).astype('<i2').tobytes())

    def check(self, kind, **fields):
        specs = validate_assertions([{'type': kind, **fields}])
        return evaluate(specs, self.result, self.root)['items'][0]

    def test_response_uses_invite_not_last_bye_code(self):
        self.result['call']['last_sip_code'] = 486
        self.assertEqual(self.check('response_code', codes=[200])['status'], 'passed')
        self.assertEqual(self.check('response_code', codes=[486])['status'], 'failed')


    def test_rtp_missing_zero_positive_and_incomplete(self):
        evidence = self.result['call']['assertion_evidence']
        for packets, final, expected in [(None, True, 'insufficient_evidence'), (0, True, 'failed'),
                                         (20, True, 'passed'), (0, False, 'insufficient_evidence')]:
            evidence.update(rx_rtp_packets_lower_bound=packets, rtp_final_sample=final)
            self.assertEqual(self.check('received_rtp')['status'], expected)


    def test_effective_audio_rejects_silence_and_dc(self):
        for dc in (False, True):
            self.wav((), dc=dc)
            self.assertEqual(self.check('effective_audio', min_duration_s=.1)['status'], 'failed')
        self.wav()
        self.assertEqual(self.check('effective_audio', min_duration_s=.4)['status'], 'passed')
        self.assertEqual(self.check('effective_audio', min_duration_s=1)['status'], 'failed')

    def test_tone_simultaneous_and_duration(self):
        self.wav()
        self.assertEqual(self.check('tone', frequencies_hz=[440, 480], min_duration_s=.4)['status'], 'passed')
        self.assertEqual(self.check('tone', frequencies_hz=[440, 620], min_duration_s=.4)['status'], 'failed')
        self.assertEqual(self.check('tone', frequencies_hz=[440, 480], min_duration_s=1)['status'], 'failed')

    def test_tone_rejects_noise_dc_and_missing_frequency(self):
        for kwargs in ({'noise': True}, {'dc': True}, {'frequencies': (440,)}, {'frequencies': ()}):
            self.wav(**kwargs)
            self.assertEqual(self.check('tone', frequencies_hz=[440, 480], min_duration_s=.08)['status'], 'failed')

    def test_missing_and_truncated_wav_are_unknown(self):
        self.assertEqual(self.check('effective_audio', min_duration_s=.1)['status'], 'insufficient_evidence')
        self.wav(); p = self.root / 'rx.wav'; p.write_bytes(p.read_bytes()[:-20])
        self.assertEqual(self.check('effective_audio', min_duration_s=.1)['status'], 'insufficient_evidence')

    def test_dtmf_exact_contains_and_repeated_digits(self):
        self.assertEqual(self.check('dtmf', digits='11#')['status'], 'passed')
        self.assertEqual(self.check('dtmf', digits='1#')['status'], 'failed')
        self.assertEqual(self.check('dtmf', digits='1#', match='contains')['status'], 'passed')
        self.result['call']['assertion_evidence']['received_dtmf'] = ''
        (self.root / 'events.jsonl').write_text('{"event":"dtmf_sent","digit":"1"}\n')
        self.assertEqual(self.check('dtmf', digits='1')['status'], 'failed')

    def test_partial_observation_is_not_negative_proof(self):
        self.result['status'] = 'interrupted'
        self.assertEqual(self.check('dtmf', digits='11#')['status'], 'insufficient_evidence')
        self.assertEqual(self.check('dtmf', digits='11#', match='contains')['status'], 'passed')
        self.wav(())
        self.assertEqual(self.check('effective_audio', min_duration_s=.1)['status'], 'insufficient_evidence')

    def test_aggregate_failure_unknown_and_preserve_execution_error(self):
        for actual in ({}, {'received_dtmf': ''}):
            result = {'status': 'completed', 'call': {'assertion_evidence': actual}}
            apply_assertions({'assertions': validate_assertions([{'type': 'dtmf', 'digits': '1'}])}, result, self.root)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['execution_status'], 'completed')
        result = {'status': 'interrupted', 'error': {'code': 'INTERRUPTED'}}
        apply_assertions({'assertions': []}, result, self.root)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['error']['code'], 'INTERRUPTED')

    def test_strict_validation(self):
        cases = [None, {}, [{'type': []}], [{'type': 'response_code', 'codes': [True]}],
                 [{'type': 'response_code', 'codes': [180]}], [{'type': 'dtmf', 'digits': 'x'}],
                 [{'type': 'dtmf', 'digits': '1', 'match': []}],
                 [{'type': 'received_rtp', 'min_packets': 0}],
                 [{'type': 'effective_audio', 'min_duration_s': float('nan')}],
                 [{'type': 'tone', 'frequencies_hz': [440, 440], 'min_duration_s': .1}],
                 [{'type': 'tone', 'frequencies_hz': [4000], 'min_duration_s': .1}],
                 [{'type': 'received_rtp', 'unexpected': 1}],
                 [{'id': 'dup', 'type': 'received_rtp'}] * 2]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError): validate_assertions(case)


    def test_auth_challenge_followed_by_local_timeout_is_not_expected_rejection(self):
        spec = template(); spec['assertions'] = [{'type': 'response_code', 'codes': [401]}]
        write_json(self.root / 'scenario.json', spec)
        class TimedOut(FakeBackend):
            def dial(self):
                self.disconnected = True; self.ever_connected = False
                self.invite_final_code = 401; self.last_code = 408
        result = execute(load_scenario(self.root / 'scenario.json'), self.root, TimedOut, Clock())
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('expected_rejection', result)


class ServiceAssertionTests(unittest.TestCase):
    def test_missing_worker_evidence_and_artifact_contract(self):
        from unittest.mock import patch
        from voice_tools.tools.sip import service
        from tests.sip.fixtures import tone
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec = template(); spec['target_uri'] = 'sip:peer@127.0.0.1:5090'
            spec['assertions'] = [{'type': 'received_rtp'}]
            write_json(root / 'scenario.json', spec)
            class Worker:
                def __init__(self, *args, **kwargs):
                    tone(root / 'run/rx.wav')
                    write_json(root / 'run/result.json', {'status': 'completed'})
                def wait(self, timeout=None): return 0
            with patch.object(service.importlib.util, 'find_spec', return_value=object()), patch.object(service.subprocess, 'Popen', Worker):
                result = service.run(root / 'scenario.json', root / 'run')
            import json
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['execution_status'], 'completed')
            self.assertEqual(result['assertions']['status'], 'insufficient_evidence')
            self.assertEqual(json.loads((root / 'run/assertions.json').read_text()), result['assertions'])

    def test_dry_run_does_not_claim_passed(self):
        from voice_tools.tools.sip.service import run
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); spec = template()
            spec['assertions'] = [{'type': 'dtmf', 'digits': '11'}]
            write_json(root / 'scenario.json', spec)
            result = run(root / 'scenario.json', root / 'out', dry_run=True)
            self.assertEqual(result['assertions']['status'], 'not_evaluated')
            self.assertFalse(result['network_accessed'])


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        import sys
        import types
        from unittest.mock import patch
        from voice_tools.tools.sip import runner
        self.ns = types.SimpleNamespace
        pj = self.ns(Call=object, Account=object, AudioMediaPlayer=object, AudioMediaPort=object, PJMEDIA_TYPE_AUDIO=1,
                     PJSUA_CALL_MEDIA_ACTIVE=1, PJSIP_EVENT_TSX_STATE=2, PJSIP_EVENT_RX_MSG=3, Error=RuntimeError)
        spec = importlib.util.spec_from_file_location('voice_tools.tools.sip._assertion_adapter', Path(runner.__file__).with_name('pjsua.py'))
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'pjsua2': pj}): spec.loader.exec_module(module)
        self.backend = module.Backend.__new__(module.Backend)
        self.backend.ever_connected = self.backend.closed = self.backend.disconnected = False
        self.backend.invite_final_code = self.backend.rx_rtp_packets = None
        self.events = []; self.backend.emit = lambda event, **kw: self.events.append((event, kw))

    def response(self, method='INVITE', code=200, source=3):
        return self.ns(type=2, body=self.ns(tsxState=self.ns(type=source,
            src=self.ns(rdata=self.ns(wholeMsg=f'SIP/2.0 {code} Status\r\nCSeq: 1 {method}\r\n\r\n')))))

    def test_only_received_invite_then_freeze(self):
        b = self.backend
        for method in ('REGISTER', 'INFO', 'BYE'):
            b.observe_response(self.response(method))
        b.observe_response(self.response(code=408, source=4))
        self.assertIsNone(b.invite_final_code)
        b.observe_response(self.response(code=401)); b.observe_response(self.response())
        self.assertEqual(b.invite_final_code, 200)
        b.ever_connected = True; b.observe_response(self.response(code=488))
        self.assertEqual(b.invite_final_code, 200)

    def test_rtp_uses_receive_counts_and_keeps_lower_bound(self):
        b = self.backend; count = [20]
        b.call = self.ns(getInfo=lambda: self.ns(media=[self.ns(type=1, status=1, index=0)]),
            getStreamStat=lambda index: self.ns(rtcp=self.ns(rxStat=self.ns(pkt=count[0]), txStat=self.ns(pkt=999))))
        self.assertTrue(b.sample_rtp()); self.assertEqual(b.rx_rtp_packets, 20)
        count[0] = 2; self.assertTrue(b.sample_rtp()); self.assertEqual(b.rx_rtp_packets, 20)
        def unavailable(index): raise RuntimeError('stream destroyed')
        b.call.getStreamStat = unavailable
        self.assertFalse(b.sample_rtp()); self.assertEqual(b.rx_rtp_packets, 20)
