import unittest

import numpy as np

from voice_tools.audio.io import Audio
from voice_tools.tools.recording_qa.detector import Config, analyze
from voice_tools.tools.recording_qa.scenarios import EXPECTED, fixture


class DetectorTests(unittest.TestCase):
    def test_scenario_regression_at_two_sample_rates(self):
        for rate in (8000, 16000):
            for case, expected in EXPECTED.items():
                with self.subTest(rate=rate, case=case):
                    data, events = fixture(case, rate)
                    config = Config(system_channel=(events or {}).get("system_channel", 1))
                    result = analyze(Audio(data, rate), events, config)
                    self.assertEqual([op["status"] for op in result["opportunities"]], expected)
                    self.assertTrue(all(not op["fault_confirmed"] for op in result["opportunities"]))

    def test_missing_metadata_downgrades_evidence(self):
        data, events = fixture("missing")
        aligned = analyze(Audio(data, 8000), events)
        inferred = analyze(Audio(data, 8000))
        self.assertEqual(aligned["opportunities"][0]["evidence_level"], "event_aligned")
        self.assertEqual(inferred["opportunities"][0]["evidence_level"], "acoustic_only")
        self.assertEqual(inferred["opportunities"][0]["status"], "NO_OUTPUT_CANDIDATE")

    def test_noise_is_never_called_a_successful_response(self):
        data, events = fixture("noise")
        result = analyze(Audio(data, 8000), events)
        self.assertEqual(result["opportunities"][0]["status"], "OUTPUT_NEEDS_REVIEW")
        self.assertTrue(any("噪声" in warning for warning in result["warnings"]))

    def test_deadline_exact_boundary(self):
        data, events = fixture("missing")
        rate = 8000
        data[11 * rate:13 * rate, 1] = 0.2 * np.sin(2 * np.pi * 440 * np.arange(2 * rate) / rate)
        result = analyze(Audio(data, rate), events)
        self.assertEqual(result["opportunities"][0]["status"], "LATE_OUTPUT_CANDIDATE")
        self.assertEqual(result["opportunities"][0]["latency_s"], 5.0)

    def test_new_turn_does_not_borrow_later_answer(self):
        data, events = fixture("late")
        events["opportunities"].append({"id": "turn-2", "at_s": 9})
        result = analyze(Audio(data, 8000), events)
        self.assertEqual(result["opportunities"][0]["status"], "CENSORED")
        self.assertEqual(result["opportunities"][1]["latency_s"], 3.0)

    def test_exclusion_beginning_censors_window(self):
        data, events = fixture("missing")
        events["exclusions"] = [{"start_s": 8, "end_s": 15}]
        result = analyze(Audio(data, 8000), events)
        self.assertEqual(result["opportunities"][0]["status"], "CENSORED")

    def test_invalid_events_fail_loudly(self):
        data, events = fixture("missing")
        cases = [[], {"schema_version": "2"}, {"channel_verified": "true"},
                 {"ai_start_s": float("nan")}, {"ai_end_s": 99}, {"user_speech": {}},
                 {"user_speech": [{"start_s": 4, "end_s": 3}]}, {"opportunities": None},
                 {"opportunities": [{"id": "x"}]}, {"opportunities": [{"id": "x", "at_s": True}]},
                 {"opportunities": [{"id": "x", "at_s": 6, "expects_response": "yes"}]},
                 {"opportunities": [{"id": "x", "at_s": 6}, {"id": "x", "at_s": 9}]},
                 {"system_channel": True}]
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                analyze(Audio(data, 8000), invalid)

    def test_invalid_config_and_samples(self):
        data, events = fixture("missing")
        for config in (Config(timeout_s=0), Config(threshold_db=float("inf")), Config(system_channel=True), Config(join_gap_s=-1)):
            with self.assertRaises(ValueError):
                analyze(Audio(data, 8000), events, config)
        data[0, 0] = np.nan
        with self.assertRaises(ValueError):
            analyze(Audio(data, 8000), events)

    def test_no_event_means_inference_but_explicit_empty_means_no_opportunity(self):
        data, events = fixture("missing")
        events["opportunities"] = []
        self.assertEqual(analyze(Audio(data, 8000), events)["opportunities"], [])

    def test_swapped_channel_requires_matching_configuration(self):
        data, events = fixture("swapped")
        with self.assertRaisesRegex(ValueError, "不一致"):
            analyze(Audio(data, 8000), events)
