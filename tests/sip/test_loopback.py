"""Opt in with VOICE_TOOLS_SIP_LOOPBACK=1. All sockets and calls use 127.0.0.1."""
import importlib.util
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

from voice_tools.core.files import write_json
from voice_tools.tools.sip.scenario import template
from tests.sip.fixtures import tone


@unittest.skipUnless(os.environ.get("VOICE_TOOLS_SIP_LOOPBACK") == "1" and importlib.util.find_spec("pjsua2"), "optional PJSUA2 localhost integration")
class LoopbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def call(self, mode="answer", early=False, steps=None, interrupt=False, interrupt_signal=signal.SIGINT, audio_seconds=.4, assertions=None):
        peer_dir = self.root / "peer"
        log = (self.root / "peer.log").open("w"); self.addCleanup(log.close)
        peer = subprocess.Popen([sys.executable, "-m", "tests.sip.loopback_peer", "--out", str(peer_dir), "--mode", mode, "--duration", "8"], stdout=log, stderr=log)
        def cleanup():
            if peer.poll() is None: peer.terminate(); peer.wait(timeout=5)
        self.addCleanup(cleanup)
        until = time.monotonic() + 4
        while not (peer_dir / "ready.json").exists() and time.monotonic() < until and peer.poll() is None:
            time.sleep(.02)
        self.assertTrue((peer_dir / "ready.json").exists(), (self.root / "peer.log").read_text())
        info = json.loads((peer_dir / "ready.json").read_text())
        spec = template(); spec["target_uri"] = f"sip:peer@127.0.0.1:{info['sip_port']}"
        for attempt in range(100):
            free_port = random.randrange(20000, 45000, 2)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as a, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as b:
                try:
                    a.bind(("127.0.0.1", free_port)); b.bind(("127.0.0.1", free_port + 1))
                    break
                except OSError:
                    continue
        else:
            self.fail("no free localhost RTP pair")
        spec["network"] = {"bind_address": "127.0.0.1", "sip_port": 0, "rtp_port": free_port}
        spec["connect_timeout_s"] = 2; spec["max_call_s"] = 5; spec["record_early"] = early
        spec["steps"] = steps or [{"action": "play", "file": "audio.wav"}, {"action": "dtmf", "digits": "1#"},
                                  {"action": "dtmf", "digits": "2", "method": "sip_info"}, {"action": "wait", "seconds": .2}, {"action": "hangup"}]
        if assertions is not None: spec["assertions"] = assertions
        env = dict(os.environ)
        if mode in ("auth", "register_auth"):
            spec["account"]["auth"] = {"username": "tester", "realm": "local-test", "password_env": "VOICE_TOOLS_TEST_SECRET"}
            env["VOICE_TOOLS_TEST_SECRET"] = "fixture-secret"
        if mode in ("register", "register_auth"):
            spec["account"]["registrar_uri"] = f"sip:127.0.0.1:{info['sip_port']}"
        tone(self.root / "audio.wav", audio_seconds); write_json(self.root / "scenario.json", spec)
        command = [sys.executable, "-m", "voice_tools", "--json", "sip", "run", str(self.root / "scenario.json"), "--out", str(self.root / "run")]
        if interrupt:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            try:
                deadline = time.monotonic() + 5
                events = self.root / "run/events.jsonl"
                while child.poll() is None and time.monotonic() < deadline:
                    if events.exists() and '"strategy_start"' in events.read_text(): break
                    time.sleep(.02)
                child.send_signal(interrupt_signal)
                stdout, stderr = child.communicate(timeout=15)
                proc = subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
            finally:
                if child.poll() is None: child.kill(); child.wait()
        else:
            proc = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
        envelope = json.loads(proc.stdout)
        self.assertNotIn("fixture-secret", proc.stdout)
        self.assertTrue((self.root / "run/result.json").exists(), proc.stdout + proc.stderr)
        result = json.loads((self.root / "run/result.json").read_text())
        peer.wait(timeout=10)
        report = json.loads((peer_dir / "report.json").read_text())
        return proc, result, report

    def test_structured_assertions_receive_evidence(self):
        proc, result, report = self.call(assertions=[
            {"type": "response_code", "codes": [200]},
            {"type": "received_rtp", "min_packets": 5},
            {"type": "effective_audio", "min_duration_s": .1},
            {"type": "dtmf", "digits": "1#"}])
        self.assertEqual(proc.returncode, 3, proc.stderr)
        rows = result["assertions"]["items"]
        self.assertEqual([r["status"] for r in rows], ["passed", "passed", "passed", "failed"], result)
        self.assertEqual(rows[3]["actual"]["digits"], "")
        self.assertEqual(result["execution_status"], "completed")

    def test_expected_busy_is_successful_response_test(self):
        proc, result, report = self.call(mode="reject", assertions=[{"type": "response_code", "codes": [486]}])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(result["expected_rejection"])
        self.assertEqual(result["assertions"]["status"], "passed")

    def test_received_dtmf_repeated_digits(self):
        proc, result, report = self.call(mode="echo_dtmf", steps=[
            {"action": "wait", "seconds": .2}, {"action": "dtmf", "digits": "11#"},
            {"action": "wait", "seconds": .3}, {"action": "hangup"}],
            assertions=[{"type": "dtmf", "digits": "11#"}])
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(result["assertions"]["items"][0]["actual"]["digits"], "11#")

    def test_silent_rtp_is_not_effective_audio(self):
        proc, result, report = self.call(mode="silence", assertions=[
            {"type": "received_rtp"}, {"type": "effective_audio", "min_duration_s": .1}])
        self.assertEqual(proc.returncode, 3, result)
        self.assertEqual([r["status"] for r in result["assertions"]["items"]], ["passed", "failed"])

    def test_received_sip_info_dtmf(self):
        proc, result, report = self.call(mode="echo_info", assertions=[{"type": "dtmf", "digits": "2"}])
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(result["assertions"]["status"], "passed")

    def test_received_dual_tone(self):
        proc, result, report = self.call(mode="tone", assertions=[
            {"type": "tone", "frequencies_hz": [440, 480], "min_duration_s": .2}])
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(result["assertions"]["status"], "passed")

    def test_no_rtp_is_failure_even_with_recording(self):
        proc, result, report = self.call(mode="no_rtp", assertions=[{"type": "received_rtp"}])
        self.assertEqual(proc.returncode, 3, result)
        self.assertEqual(result["assertions"]["status"], "failed", result)

    def test_no_response_is_not_expected_408(self):
        proc, result, report = self.call(mode="drop", assertions=[{"type": "response_code", "codes": [408]}])
        self.assertEqual(proc.returncode, 3, result)
        self.assertEqual(result["assertions"]["status"], "insufficient_evidence")

    def test_real_udp_play_dtmf_info_record_and_bye(self):
        proc, result, peer = self.call()
        self.assertEqual(proc.returncode, 0, (result, (self.root / "run/native.log").read_text()))
        self.assertEqual(peer["dtmf"], ["1", "#"])
        self.assertEqual(peer["sip_info"], ["2"])
        self.assertGreater(peer["non_silent_packets"], 5)
        self.assertTrue(peer["bye_received"])
        with wave.open(str(self.root / "run/rx.wav")) as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertGreater(wav.getnframes(), 4000)
            self.assertNotEqual(set(wav.readframes(100000)), {0})

    def test_digest_auth_does_not_leak_secret(self):
        proc, result, peer = self.call("auth")
        self.assertEqual(proc.returncode, 0, result)
        self.assertTrue(peer["auth_verified"])
        self.assertNotIn("fixture-secret", (self.root / "run/native.log").read_text())

    def test_optional_registration_before_call(self):
        proc, result, peer = self.call("register")
        self.assertEqual(proc.returncode, 0, result)
        self.assertGreaterEqual(peer["registers"], 1)

    def test_registration_digest_challenge_before_call(self):
        proc, result, peer = self.call("register_auth")
        self.assertEqual(proc.returncode, 0, result)
        self.assertGreaterEqual(peer["registers"], 2)
        self.assertTrue(peer["auth_verified"])

    def test_operator_interrupt_hangs_up_and_preserves_result(self):
        proc, result, peer = self.call(steps=[{"action": "wait", "seconds": 4}, {"action": "hangup"}], interrupt=True)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(peer["bye_received"])

    def test_sigterm_hangs_up_and_preserves_result(self):
        proc, result, peer = self.call(steps=[{"action": "wait", "seconds": 4}, {"action": "hangup"}],
                                       interrupt=True, interrupt_signal=signal.SIGTERM)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(peer["bye_received"])

    def test_reinvite_media_port_switch_keeps_both_directions(self):
        proc, result, peer = self.call("reinvite", audio_seconds=2,
                                       steps=[{"action": "play", "file": "audio.wav"}, {"action": "wait", "seconds": .2}, {"action": "hangup"}])
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(peer["reinvite_code"], 200)
        self.assertGreater(peer["post_switch_non_silent_packets"], 20)
        with wave.open(str(self.root / "run/rx.wav")) as wav:
            import numpy as np
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
        self.assertGreater(len(pcm), 16000)
        self.assertGreater(float(np.sqrt(np.mean(pcm[-4000:].astype(float) ** 2))), 100)
        events = [json.loads(line) for line in (self.root / "run/events.jsonl").read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'recording_start' for e in events), 1)

    def test_early_media_is_recorded_before_strategy(self):
        proc, result, peer = self.call("early", early=True)
        self.assertEqual(proc.returncode, 0, result)
        events = [json.loads(line) for line in (self.root / "run/events.jsonl").read_text().splitlines()]
        recording = next(e for e in events if e["event"] == "recording_start")
        strategy = next(e for e in events if e["event"] == "strategy_start")
        self.assertTrue(recording["early"])
        self.assertGreater(strategy["at_s"] - recording["at_s"], .25)

    def test_busy_is_failure_not_success(self):
        proc, result, peer = self.call("reject")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(result["call"]["last_sip_code"], 486)
        self.assertEqual(result["status"], "failed")

    def test_remote_hangup_during_play_keeps_artifacts(self):
        proc, result, peer = self.call("hangup")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(result["error"]["code"], "REMOTE_HANGUP")
        self.assertTrue((self.root / "run/rx.wav").exists())

    @unittest.skipUnless(__import__("shutil").which("tshark"), "optional tshark")
    def test_imported_pcap_media_and_dtmf_timeline(self):
        from tests.sip.fixtures import capture
        from voice_tools.tools.sip.pcap import read_capture, import_capture
        data = read_capture(capture(self.root / "source.pcap"), [4000, 5000])
        stream = next(s["id"] for s in data["streams"].values() if s["src_port"] == 4000)
        import_capture(data, stream, self.root / "bundle", dtmf_pt=101)
        proc, result, peer = self.call(steps=[{"action": "play_media", "file": str(self.root / "bundle/media.json")},
                                             {"action": "wait", "seconds": .2}, {"action": "hangup"}])
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(peer["dtmf"], ["1"])
