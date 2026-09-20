"""Optional native SIP calls, restricted to the existing localhost fixture."""
import importlib.util
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path

from tests.sip import test_loopback


@unittest.skipUnless(os.environ.get("VOICE_TOOLS_SIP_LOOPBACK") == "1" and importlib.util.find_spec("pjsua2"), "optional localhost media observation")
class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def call(self, **kwargs):
        kwargs.setdefault("benchmark", {"case_id": "loopback", "detector": {"backend": "energy"}})
        return test_loopback.LoopbackTests.call(self, **kwargs)

    def test_bidirectional_frames_and_audio_trigger(self):
        proc, result, peer = self.call(steps=[
            {"action": "wait_audio", "state": "active", "timeout_s": 2},
            {"action": "play", "file": "audio.wav"},
            {"action": "wait", "seconds": .3}, {"action": "hangup"}])
        native_log = (self.root / "run/native.log").read_text()
        self.assertEqual(proc.returncode, 0, (result, native_log))
        observation = json.loads((self.root / "run/media-observation.json").read_text())
        self.assertTrue(observation["complete"])
        self.assertEqual(observation["dropped_frames"], 0)
        frames = [json.loads(line) for line in (self.root / "run/media-frames.jsonl").read_text().splitlines()]
        for direction in ("rx", "tx"):
            selected = [f for f in frames if f["direction"] == direction]
            self.assertGreater(len(selected), 5)
            self.assertTrue(all(f["at_s"] >= 0 and f["samples"] > 0 for f in selected))
            with wave.open(str(self.root / f"run/bridge_{direction}.wav")) as wav:
                self.assertEqual(wav.getnframes(), sum(f["samples"] for f in selected))
        self.assertGreater(peer["non_silent_packets"], 0)

    def test_interrupt_preserves_manifest(self):
        proc, result, _ = self.call(interrupt=True, steps=[{"action": "wait", "seconds": 4}])
        self.assertNotEqual(proc.returncode, 0)
        manifest = json.loads((self.root / "run/media-observation.json").read_text())
        self.assertTrue(manifest["complete"])
        self.assertEqual(result["status"], "interrupted")

    def timing_call(self, mode):
        return self.call(mode=mode, steps=[
            {"action": "wait_audio", "state": "active", "timeout_s": 2},
            {"action": "wait", "seconds": .3}, {"action": "play", "file": "audio.wav"},
            {"action": "wait", "seconds": 1.8}, {"action": "hangup"}])

    def test_stopping_then_reply_is_measured_separately(self):
        proc, result, _ = self.timing_call("timing_reply")
        self.assertEqual(proc.returncode, 0, result)
        report = json.loads((self.root / "run/benchmark.json").read_text())
        self.assertEqual(report["issues"], [])
        stop = next(m for m in report["metrics"] if m["kind"] == "barge_stop")
        response = next(m for m in report["metrics"] if m["kind"] == "response")
        self.assertEqual(stop["status"], "measured", report)
        self.assertGreater(stop["value_ms"], 60)
        self.assertLess(stop["value_ms"], 600)
        self.assertGreater(response["at_s"], stop["end_s"] + .2)

    def test_ignoring_interruption_is_not_a_success(self):
        proc, result, _ = self.timing_call("answer")
        self.assertEqual(proc.returncode, 0, result)
        report = json.loads((self.root / "run/benchmark.json").read_text())
        stop = next(m for m in report["metrics"] if m["kind"] == "barge_stop")
        self.assertEqual(stop["status"], "insufficient_evidence")
        self.assertGreater(stop["lower_bound_ms"], 1000)

    def test_pcm_gap_is_detected_over_real_rtp(self):
        proc, _, _ = self.timing_call("timing_gap")
        self.assertEqual(proc.returncode, 0)
        report = json.loads((self.root / "run/benchmark.json").read_text())
        gap = next(m for m in report["metrics"] if m["kind"] == "longest_silence")
        self.assertGreater(gap["value_ms"], 650)
        self.assertLess(gap["value_ms"], 950)

    def test_audio_trigger_timeout_preserves_evidence(self):
        proc, result, _ = self.call(mode="silence", steps=[
            {"action": "wait_audio", "state": "active", "timeout_s": .4}, {"action": "hangup"}])
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(result["error"]["code"], "WAIT_TIMEOUT")
        report = json.loads((self.root / "run/benchmark.json").read_text())
        self.assertIsNone(report["metrics"][0]["value_ms"])

    def test_early_hangup_preserves_partial_measurement(self):
        proc, result, _ = self.call(mode="hangup", steps=[{"action": "wait", "seconds": 2}])
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(result["error"]["code"], "REMOTE_HANGUP")
        self.assertTrue((self.root / "run/benchmark.json").exists())

    def test_media_recreation_does_not_claim_precision(self):
        proc, result, peer = self.call(mode="reinvite", audio_seconds=1.4, steps=[
            {"action": "play", "file": "audio.wav"}, {"action": "wait", "seconds": .3}, {"action": "hangup"}])
        self.assertEqual(proc.returncode, 0, result)
        self.assertEqual(peer["reinvite_code"], 200)
        report = json.loads((self.root / "run/benchmark.json").read_text())
        self.assertIn("media_recreated", report["issues"])
        self.assertEqual(report["metrics"], [])
