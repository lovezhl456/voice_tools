import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from voice_tools.core.files import read_json, write_json
from voice_tools.tools.benchmark.analysis import analyze, metrics, attach_assertions
from voice_tools.tools.benchmark.config import configuration
from voice_tools.tools.benchmark.observation import Observation
from voice_tools.tools.benchmark.summary import distribution
from voice_tools.tools.benchmark.templates import initialize
from voice_tools.tools.sip.scenario import load_scenario, template
from tests.sip.fixtures import tone


def span(start, end):
    return {"start_s": start, "end_s": end}


class TimingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.config = configuration({"case_id": "中文测试", "detector": {"backend": "energy"}})

    def evidence(self, voiced=((0, .1), (.9, 1)), duration=1, frame_ms=20, tx=False):
        observation = Observation(self.root, lambda: 0, self.config["detector"])
        count = round(frame_ms * 8)
        signal = (np.sin(np.arange(count) * 2 * np.pi * 440 / 8000) * 10000).astype("<i2").tobytes()
        for i in range(round(duration * 8000 / count)):
            at = i * count / 8000
            payload = signal if any(a <= at < b - 1e-9 for a, b in voiced) else bytes(count * 2)
            observation.submit("rx", payload, at=at)
            if tx:
                observation.submit("tx", signal, at=at, source={"step_index": 0, "sample_start": i * count, "valid_samples": count})
            observation.drain()
        observation.close()
        write_json(self.root / "plan.json", {"benchmark": self.config, "steps": [{"action": "play"}]})
        write_json(self.root / "result.json", {"status": "completed", "execution_status": "completed"})
        (self.root / "events.jsonl").write_text('{"event":"strategy_start","at_s":0}\n')

    def test_continuous_silent_pcm_is_an_800ms_gap(self):
        self.evidence()
        report = analyze(self.root)
        self.assertEqual(report["issues"], [])
        gap = next(m for m in report["metrics"] if m["kind"] == "longest_silence")
        self.assertAlmostEqual(gap["value_ms"], 800)
        self.assertAlmostEqual(gap["total_silence_ms"], 800)

    def test_payload_duration_comes_from_samples(self):
        self.evidence(voiced=((0, .4),), duration=.4, frame_ms=40)
        report = analyze(self.root)
        self.assertEqual(report["issues"], [])
        self.assertAlmostEqual(report["activity"]["rx"][0]["end_s"], .4)

    def test_already_quiet_is_invalid_not_zero_ms(self):
        self.config["expectations"]["expect_interrupt"] = True
        rows = metrics([span(0, .2)], [span(1, 1.4)], 0, 0, 3, self.config)
        stop = next(m for m in rows if m["kind"] == "barge_stop")
        self.assertEqual(stop["status"], "invalid")
        self.assertIsNone(stop["value_ms"])

    def test_short_pause_does_not_end_old_segment(self):
        rows = metrics([span(0, 1.1), span(1.2, 1.5)], [span(1, 1.4)], 0, 0, 3, self.config)
        self.assertAlmostEqual(next(m["value_ms"] for m in rows if m["kind"] == "barge_stop"), 500)

    def test_missing_first_audio_stays_unknown(self):
        self.evidence(voiced=())
        report = analyze(self.root)
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertIsNone(report["metrics"][0]["value_ms"])

    def test_low_volume_is_below_configured_floor(self):
        import wave
        self.evidence(voiced=((0, 1),))
        path = self.root / "bridge_rx.wav"
        with wave.open(str(path), "rb") as stream:
            params, pcm = stream.getparams(), stream.readframes(stream.getnframes())
        quiet = (np.frombuffer(pcm, dtype="<i2") / 1000).astype("<i2").tobytes()
        with wave.open(str(path), "wb") as stream:
            stream.setparams(params)
            stream.writeframes(quiet)
        report = analyze(self.root)
        self.assertEqual(report["activity"]["rx"], [])
        self.assertEqual(report["metrics"][0]["status"], "insufficient_evidence")

    def test_missing_pcm_preserves_unknown_and_reason(self):
        self.evidence()
        (self.root / "bridge_rx.wav").unlink()
        report = analyze(self.root)
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertTrue(report["issues"])

    def test_manifest_loss_disables_precise_metrics(self):
        self.evidence()
        manifest = read_json(self.root / "media-observation.json")
        manifest["dropped_frames"] = 1
        write_json(self.root / "media-observation.json", manifest)
        report = analyze(self.root)
        self.assertEqual(report["metrics"], [])
        self.assertEqual(report["status"], "insufficient_evidence")

    def test_truncated_pcm_is_not_silence(self):
        self.evidence()
        path = self.root / "bridge_rx.wav"
        path.write_bytes(path.read_bytes()[:-100])
        self.assertEqual(analyze(self.root)["metrics"], [])

    def test_missing_frame_is_detected_independently_of_manifest(self):
        self.evidence()
        path = self.root / "media-frames.jsonl"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[:3] + lines[4:]) + "\n")
        self.assertEqual(analyze(self.root)["metrics"], [])

    def test_early_media_is_separate_from_opening(self):
        self.evidence(voiced=((0, .2), (.6, 1)))
        (self.root / "events.jsonl").write_text('{"event":"strategy_start","at_s":0.5}\n')
        report = analyze(self.root)
        self.assertEqual(len(report["early_media"]), 1)
        self.assertAlmostEqual(report["metrics"][0]["value_ms"], 100)

    def test_human_source_annotations_override_vad_with_provenance(self):
        self.evidence(voiced=((0, 1),), tx=True)
        plan = read_json(self.root / "plan.json")
        plan["steps"][0]["speech"] = [{"start_s": .2, "end_s": .5}]
        write_json(self.root / "plan.json", plan)
        activity = analyze(self.root)["activity"]["tx"]
        self.assertEqual(len(activity), 1)
        self.assertAlmostEqual(activity[0]["start_s"], .2)
        self.assertAlmostEqual(activity[0]["end_s"], .5)
        self.assertEqual(activity[0]["annotation"], "human")

    def test_queue_overflow_is_recorded(self):
        observation = Observation(self.root, lambda: 0, self.config["detector"], queue_size=1)
        observation.submit("rx", bytes(320))
        observation.submit("rx", bytes(320))
        observation.close()
        self.assertEqual(read_json(self.root / "media-observation.json")["dropped_frames"], 1)

    def test_aggregate_keeps_failures_and_invalid_calls(self):
        reports = [{"status": "completed", "execution_status": "completed", "metrics": [{"kind": "first_audio", "status": "measured", "value_ms": 20}]},
                   {"status": "completed", "execution_status": "failed", "metrics": []},
                   {"status": "completed", "execution_status": "completed", "metrics": [{"status": "invalid", "value_ms": None}]},
                   {"status": "insufficient_evidence", "execution_status": "completed", "metrics": []}]
        summary = distribution(reports)
        self.assertEqual(summary["counts"], {"valid": 1, "failed": 1, "invalid": 1, "insufficient_evidence": 1})

    def test_empty_measurement_is_not_valid(self):
        self.assertEqual(distribution([{"execution_status": "completed", "status": "completed", "metrics": []}])["counts"]["valid"], 0)

    def test_clock_gap_and_epoch_are_verified_without_manifest(self):
        for field, value in (("at_s", .6), ("epoch", 2)):
            self.evidence()
            path = self.root / "media-frames.jsonl"
            frames = [json.loads(line) for line in path.read_text().splitlines()]
            frames[10][field] = value
            path.write_text("".join(json.dumps(f) + "\n" for f in frames))
            self.assertEqual(analyze(self.root)["metrics"], [])

    def test_partial_final_silence_does_not_extend_observed_stop_lower_bound(self):
        rows = metrics([span(0, 1.1)], [span(1, 1.05)], 0, 0, 1.2, self.config)
        stop = next(m for m in rows if m["kind"] == "barge_stop")
        self.assertEqual(stop["status"], "insufficient_evidence")
        self.assertAlmostEqual(stop["lower_bound_ms"], 100)

    def test_hangup_silence_cannot_count_as_interrupt_success(self):
        self.evidence(voiced=((0, .6),), tx=True)
        plan = read_json(self.root / "plan.json")
        plan["steps"][0]["speech"] = [{"start_s": .2, "end_s": .4}]
        write_json(self.root / "plan.json", plan)
        with (self.root / "events.jsonl").open("a") as stream:
            stream.write('{"event":"step_start","action":"hangup","at_s":0.6}\n')
        stop = next(m for m in analyze(self.root)["metrics"] if m["kind"] == "barge_stop")
        self.assertEqual(stop["status"], "insufficient_evidence")
        self.assertEqual(stop["lower_bound_ms"], 400)

    def test_missing_tx_cannot_skip_configured_response_assertion(self):
        self.evidence()
        plan = read_json(self.root / "plan.json")
        plan["benchmark"]["expectations"]["response_max_ms"] = 1000
        write_json(self.root / "plan.json", plan)
        report = analyze(self.root)
        result = {"status": "completed", "assertions": {"items": []}}
        attach_assertions(report, result)
        self.assertEqual(result["assertions"]["status"], "insufficient_evidence")

    def test_failed_drain_leaves_incomplete_manifest_and_closes_wav(self):
        from unittest.mock import patch
        observation = Observation(self.root, lambda: 0, self.config["detector"])
        with patch.object(observation, "drain", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                observation.close()
        self.assertFalse(read_json(self.root / "media-observation.json")["complete"])

    def test_existing_error_is_not_overwritten_by_timing_assertions(self):
        result = {"status": "interrupted", "error": {"code": "INTERRUPTED"}, "assertions": {"items": []}}
        report = {"configuration": {"expectations": {"first_audio_max_ms": 10}}, "metrics": [],
                  "issues": ["missing"], "status": "insufficient_evidence"}
        attach_assertions(report, result)
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["error"]["code"], "INTERRUPTED")

    def test_version_gate_annotations_and_original_scenario(self):
        tone(self.root / "source.wav")
        spec = template()
        spec["steps"] = [{"action": "play", "file": "source.wav"}]
        write_json(self.root / "scenario.json", spec)
        self.assertEqual(load_scenario(self.root / "scenario.json")["schema_version"], "1.0")
        spec["benchmark"] = self.config
        write_json(self.root / "scenario.json", spec)
        with self.assertRaises(ValueError): load_scenario(self.root / "scenario.json")
        spec["schema_version"] = "1.1"
        spec["steps"][0]["speech"] = [{"start_s": .1, "end_s": .3}]
        write_json(self.root / "scenario.json", spec)
        self.assertEqual(load_scenario(self.root / "scenario.json")["steps"][0]["speech"], spec["steps"][0]["speech"])

    def test_templates_do_not_invent_chinese_recordings(self):
        output = self.root / "templates"
        initialize(output)
        self.assertEqual(len(list(output.glob("*.wav"))), 0)
        self.assertEqual(len(read_json(output / "queue.json")["jobs"]), 5)
        self.assertEqual(load_scenario(output / "greeting.json")["schema_version"], "1.1")
        with self.assertRaises((ValueError, OSError)):
            load_scenario(output / "interrupt.json")
