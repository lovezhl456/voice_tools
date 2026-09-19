import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voice_tools.core.files import write_json
from voice_tools.tools.sip.pcap import g711_sample, import_capture, inspection, media_from_stream, read_capture
from voice_tools.tools.sip.scenario import load_scenario, media_bundle, template
from voice_tools.tools.sip.service import run
from voice_tools.tools.sip.sipp import load_package, prepare, run_package
from tests.sip.fixtures import capture, tone


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "scenario.json"
        self.scenario = template()

    def load(self):
        write_json(self.path, self.scenario)
        return load_scenario(self.path)

    def test_valid_defaults_and_offline_plan(self):
        self.load()
        result = run(self.path, self.root / "planned", dry_run=True)
        self.assertFalse(result["network_accessed"])
        with self.assertRaisesRegex(ValueError, "输出目录"):
            run(self.path, self.root / "planned", dry_run=True)

    def test_reject_unknown_fields_nonfinite_bool_and_plaintext_password(self):
        for update in ({"max_call_s": True}, {"connect_timeout_s": float("inf")}, {"oops": 1},
                       {"account": {"password": "do-not-save"}}, {"target_uri": "sip:test@localhost\r\nInjected: x"},
                       {"target_uri": "sip:test@localhost;transport=tcp"}, {"network": {"rtp_port": 4001}}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.scenario = {**template(), **update}; self.load()

    def test_validate_relative_wav_and_final_hangup(self):
        tone(self.root / "voice.wav")
        self.scenario["steps"] = [{"action": "play", "file": "voice.wav"}, {"action": "hangup"}]
        result = self.load()
        self.assertEqual(result["steps"][0]["audio"]["duration_s"], .4)
        self.scenario["steps"].reverse()
        with self.assertRaisesRegex(ValueError, "最后"):
            self.load()

    def test_reject_unsupported_wav_and_budget(self):
        with wave.open(str(self.root / "stereo.wav"), "wb") as wav:
            wav.setparams((2, 2, 8000, 0, "NONE", "not compressed")); wav.writeframes(bytes(3200))
        self.scenario["steps"] = [{"action": "play", "file": "stereo.wav"}]
        with self.assertRaisesRegex(ValueError, "单声道"):
            self.load()
        self.scenario = template(); self.scenario["max_call_s"] = 1
        with self.assertRaisesRegex(ValueError, "总时长"):
            self.load()

    def test_machine_entrypoints_and_unknown_option(self):
        self.load()
        for args in (["schema", "--tool", "sip"], ["--json", "sip", "validate", str(self.path)]):
            proc = subprocess.run([sys.executable, "-m", "voice_tools", *args], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr); self.assertIsInstance(json.loads(proc.stdout), dict)
        proc = subprocess.run([sys.executable, "-m", "voice_tools", "--json", "sip", "run", "--bad"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2); self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_run_snapshots_source_before_starting_worker(self):
        self.scenario["target_uri"] = "sip:peer@127.0.0.1:5090"
        tone(self.root / "voice.wav")
        self.scenario["steps"] = [{"action": "play", "file": "voice.wav"}]
        original_hash = self.load()["steps"][0]["audio"]["sha256"]
        output = self.root / "run"
        original = self.root / "voice.wav"
        class FakeWorker:
            def __init__(self, command, **kwargs):
                original.unlink()
                plan = json.loads((output / "plan.json").read_text())
                self.audio = plan["steps"][0]["audio"]
                write_json(output / "result.json", {"status": "completed", "recording": {}})
                tone(output / "rx.wav")
            def wait(self, timeout): return 0
        with patch("voice_tools.tools.sip.service.importlib.util.find_spec", return_value=object()), \
             patch("voice_tools.tools.sip.service.subprocess.Popen", FakeWorker):
            result = run(self.path, output)
        plan = json.loads((output / "plan.json").read_text())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(plan["steps"][0]["audio"]["sha256"], original_hash)
        self.assertTrue(Path(plan["steps"][0]["audio"]["file"]).is_file())
        self.assertFalse(original.exists())


class DecoderTests(unittest.TestCase):
    def test_known_g711_values(self):
        self.assertEqual([g711_sample(v, "PCMU") for v in (0xff, 0x7f, 0, 0x80)], [0, 0, -32124, 32124])
        self.assertEqual([g711_sample(v, "PCMA") for v in (0xd5, 0x55, 0xaa, 0x2a)], [8, -8, 32256, -32256])

    def packet(self, seq, stamp, payload=b"\xd5" * 160, pt=8):
        return {"seq": seq, "timestamp": stamp, "payload": payload, "pt": pt}

    def test_wrap_reorder_gap_and_duplicate(self):
        p1 = self.packet(65535, 0xffffff60)
        p2 = self.packet(0, 0)
        p3 = self.packet(2, 320)
        pcm, details = media_from_stream({"packets": [p2, p1, p1, p3]})
        self.assertEqual(len(pcm), 640 * 2)
        self.assertEqual(details["duplicates_removed"], 1)
        self.assertEqual(details["silence_fill_samples"], 160)

    def test_reject_unknown_payload_conflict_and_clock_reset(self):
        cases = [([self.packet(1, 0), self.packet(2, 160, b"abc", 110)], "未映射"),
                 ([self.packet(1, 0), self.packet(1, 0, b"\x55" * 160)], "冲突"),
                 ([self.packet(1, 0), self.packet(2, 8000 * 901)], "900")]
        for packets, text in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, text):
                media_from_stream({"packets": packets})


@unittest.skipUnless(shutil.which("tshark"), "optional tshark not installed")
class CaptureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = capture(self.root / "sample.pcap")
        self.data = read_capture(self.path, [4000, 5000])
        self.stream = next(s["id"] for s in self.data["streams"].values() if s["src_port"] == 4000)

    def test_actual_tshark_import_deduplicates_dtmf_and_separates_direction(self):
        self.assertEqual(len(inspection(self.data)["streams"]), 2)
        result = import_capture(self.data, self.stream, self.root / "bundle", dtmf_pt=101)
        self.assertEqual(result["dtmf"], [{"at_s": .2, "digit": "1", "duration_ms": 160, "end_observed": True}])
        self.assertAlmostEqual(result["duration_s"], .56)
        self.assertEqual(result["silence_fill_samples"], 1440)
        bundle = media_bundle(self.root / "bundle/media.json")
        self.assertEqual(len(bundle["dtmf"]), 1)
        self.assertEqual(load_scenario(self.root / "bundle/scenario.json")["codec"], "PCMA")
        (self.root / "bundle/audio.wav").write_bytes(b"broken")
        with self.assertRaises(ValueError):
            media_bundle(self.root / "bundle/media.json")

    def test_explicit_mapping_and_selection_required(self):
        with self.assertRaisesRegex(ValueError, "未映射"):
            import_capture(self.data, self.stream, self.root / "bad")
        with self.assertRaisesRegex(ValueError, "stream"):
            import_capture(self.data, "wrong", self.root / "wrong")

    def test_sipp_export_contains_one_stream_and_no_arbitrary_command(self):
        package = self.root / "package"
        prepare(self.data, self.stream, package, "sip:peer@127.0.0.1:5090", "127.0.0.1", dtmf_pt=101)
        selected = read_capture(package / "selected.pcap", [4000, 5000])
        self.assertEqual(len(selected["streams"]), 1)
        result = run_package(package, self.root / "dry", dry_run=True)
        self.assertEqual(result["status"], "planned")
        self.assertIn("-m", result["command"])
        xml = package / "scenario.xml"
        xml.write_text(xml.read_text().replace('play_pcap_audio="selected.pcap"', 'command="touch /tmp/unwanted"'))
        from voice_tools.core.files import sha256
        plan = json.loads((package / "plan.json").read_text()); plan["files"]["scenario.xml"] = sha256(xml)
        write_json(package / "plan.json", plan)
        with self.assertRaisesRegex(ValueError, "exec"):
            load_package(package)

    def test_raw_socket_permission_fails_before_subprocess(self):
        from voice_tools.tools.sip.sipp import check_raw_socket_permission
        with patch("voice_tools.tools.sip.sipp.sys.platform", "darwin"), \
             patch("voice_tools.tools.sip.sipp.socket.socket", side_effect=PermissionError("not permitted")):
            with self.assertRaisesRegex(ValueError, "尚未拨号"):
                check_raw_socket_permission(sys.executable)
