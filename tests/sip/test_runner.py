import tempfile
import unittest
from pathlib import Path

from voice_tools.core.files import write_json
from voice_tools.tools.sip.runner import execute
from voice_tools.tools.sip.scenario import load_scenario, template
from tests.sip.fixtures import tone


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class FakeBackend:
    def __init__(self, plan, output, emit, clock):
        self.clock, self.emit = clock, emit
        self.failure = None
        self.connected = self.disconnected = self.media_ready = False
        self.record_started = None
        self.closed = False
        self.actions = []
        self.end = None
        self.hangup_at = None
        self.reject = False

    def dial(self):
        self.actions.append("dial")
        self.connected = self.media_ready = not self.reject
        if not self.reject: self.record_started = self.clock()

    def poll(self, ms):
        self.clock.now += ms / 1000
        if self.hangup_at and self.clock() >= self.hangup_at: self.disconnected = True

    def play(self, filename): self.actions.append("play"); self.end = self.clock() + .4
    def playback_done(self): return self.clock() >= self.end
    def stop_playback(self): self.actions.append("stop_play")
    def dtmf(self, digit, duration, method): self.actions.append("dtmf:" + digit)
    def hangup(self): self.actions.append("hangup"); self.disconnected = True
    def details(self): return {"fake": True}
    def close(self): self.closed = True; self.hangup()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); tone(self.root / "audio.wav")
        self.spec = template(); self.spec["target_uri"] = "sip:peer@127.0.0.1:5090"
        self.spec["steps"] = [{"action": "play", "file": "audio.wav"}, {"action": "dtmf", "digits": "1#"}, {"action": "hangup"}]
        self.clock = Clock()
        self.backend = None

    def execute(self, configure=None):
        write_json(self.root / "scenario.json", self.spec)
        plan = load_scenario(self.root / "scenario.json")
        def factory(*args):
            self.backend = FakeBackend(*args)
            if configure: configure(self.backend)
            return self.backend
        return execute(plan, self.root, factory, self.clock)


    def test_native_media_failure_is_not_success(self):
        result = self.execute(lambda b: setattr(b, "failure", "no compatible codec"))
        self.assertEqual(result["error"]["code"], "MEDIA_ERROR")
        self.assertTrue(self.backend.closed)

    def test_recording_postprocess_error_still_closes_native_resources(self):
        from unittest.mock import patch
        with patch("voice_tools.tools.sip.runner.source_recording", side_effect=OSError("disk full")):
            result = self.execute()
        self.assertTrue(self.backend.closed)
        self.assertEqual(result["error"]["code"], "RECORDING_ERROR")
        self.assertEqual(result["status"], "failed")
