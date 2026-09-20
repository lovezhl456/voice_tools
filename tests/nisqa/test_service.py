import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np

from voice_tools.tools.nisqa.backend import SCORE_NAMES
from voice_tools.tools.nisqa.service import analyze, collect_inputs


@unittest.skipUnless(importlib.util.find_spec("soundfile"), "NISQA soundfile extra is not installed")
class ServiceTests(unittest.TestCase):
    def setUp(self):
        import soundfile as sf
        self.sf = sf
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.calls = []

        def score(samples, rate):
            self.calls.append((samples.copy(), rate))
            return dict(zip(SCORE_NAMES, [3.0, 2.0, 4.0, 3.5, 4.5]))

        self.factory = Mock(return_value=score)

    def write(self, name, samples, rate=16000):
        path = self.inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.sf.write(path, samples, rate, subtype="FLOAT" if path.suffix == ".wav" else "PCM_16")
        return path

    def run_analysis(self, inputs=None, **kwargs):
        output = self.root / "out"
        summary, code = analyze(inputs or [self.inputs], output, scorer_factory=self.factory, **kwargs)
        rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
        return summary, code, rows

    def test_stereo_separate_samples_timestamps_and_short_tail(self):
        a = np.full((40000, 2), [0.05, 0.1], dtype=np.float32)
        self.write("call.wav", a)
        summary, code, rows = self.run_analysis(channel="both", segment_seconds=1)
        self.assertEqual(code, 1)
        self.assertEqual((summary["scored"], summary["insufficient_evidence"]), (4, 2))
        self.assertEqual([r["channel"] for r in rows], ["left", "right"] * 3)
        self.assertEqual([r["start_seconds"] for r in rows], [0, 0, 1, 1, 2, 2])
        self.assertEqual(rows[-1]["end_seconds"], 2.5)
        self.assertIsNone(rows[-1]["scores"])
        np.testing.assert_array_equal(self.calls[0][0], a[:16000, 0])
        np.testing.assert_array_equal(self.calls[1][0], a[:16000, 1])
        with (self.root / "out/results.csv").open() as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(csv_rows[0]["discontinuity"], "4.0")
        self.assertEqual(csv_rows[0]["coloration"], "3.5")
        self.assertEqual(csv_rows[-1]["mos"], "")

    def test_stereo_requires_explicit_channel_but_mono_still_completes(self):
        self.write("stereo.wav", np.full((16000, 2), 0.03))
        self.write("mono.wav", np.full(16000, 0.03))
        summary, code, rows = self.run_analysis()
        self.assertEqual((code, summary["errors"], summary["scored"]), (3, 1, 1))
        self.assertIn("--channel", next(r for r in rows if r["status"] == "error")["reason"])

    def test_mono_is_not_duplicated_for_both(self):
        self.write("mono.wav", np.full(16000, 0.03))
        summary, code, rows = self.run_analysis(channel="both")
        self.assertEqual((code, summary["errors"]), (3, 1))
        self.assertEqual(self.calls, [])

    def test_silent_quiet_short_empty_no_inference(self):
        self.write("silence.wav", np.zeros(16000))
        self.write("quiet.wav", np.full(16000, 1e-5))
        self.write("short.wav", np.full(100, 0.1))
        self.write("empty.wav", np.zeros(0))
        summary, code, rows = self.run_analysis()
        self.assertEqual(code, 1)
        self.assertEqual(summary["insufficient_evidence"], 4)
        self.assertEqual(self.calls, [])
        self.assertTrue(all(r["scores"] is None for r in rows))

    def test_corrupt_file_preserves_other_results(self):
        (self.inputs / "broken.wav").write_bytes(b"not audio")
        self.write("valid.wav", np.full(16000, 0.03))
        summary, code, rows = self.run_analysis()
        self.assertEqual((code, summary["errors"], summary["scored"]), (3, 1, 1))
        self.assertEqual(len(rows), 2)

    def test_nonfinite_audio_or_predictions_are_errors(self):
        self.write("nan.wav", np.full(16000, np.nan))
        self.write("valid.wav", np.full(16000, 0.03))
        self.factory.return_value = lambda *args: dict.fromkeys(SCORE_NAMES, float("nan"))
        summary, code, rows = self.run_analysis()
        self.assertEqual((code, summary["errors"]), (3, 2))
        self.assertTrue(all(r["scores"] is None for r in rows))

    def test_runtime_error_is_recorded_and_next_segment_continues(self):
        self.write("valid.wav", np.full(32000, 0.03))
        self.factory.return_value = Mock(side_effect=[RuntimeError("test failure"), dict.fromkeys(SCORE_NAMES, 3.0)])
        summary, code, rows = self.run_analysis(segment_seconds=1)
        self.assertEqual((code, summary["errors"], summary["scored"]), (3, 1, 1))
        self.assertEqual(rows[0]["reason"], "test failure")

    def test_unsupported_sample_rate_is_rejected_before_scoring(self):
        self.write("rate.wav", np.full(4000, 0.03), rate=4000)
        summary, code, rows = self.run_analysis()
        self.assertEqual((code, summary["errors"]), (3, 1))
        self.assertIn("96 kHz", rows[0]["reason"])
        self.assertEqual(self.calls, [])

    def test_recursive_inputs_deduplicate_and_support_flac(self):
        path = self.write("sub/call.flac", np.full(16000, 0.03))
        self.assertEqual(collect_inputs([self.inputs, path]), [path.resolve()])
        summary, code, rows = self.run_analysis([self.inputs, path])
        self.assertEqual((code, summary["files"]), (0, 1))
        self.assertEqual(rows[0]["sample_rate"], 16000)

    def test_options_and_existing_output_fail_before_model_load(self):
        self.write("valid.wav", np.full(16000, 0.03))
        for kwargs in ({"segment_seconds": float("nan")}, {"segment_seconds": 21}, {"min_seconds": 0},
                       {"min_rms_dbfs": float("inf")}, {"threads": 0}, {"channel": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_analysis(**kwargs)
        self.factory.assert_not_called()
        (self.root / "out").mkdir()
        (self.root / "out/preserve").write_text("untouched")
        with self.assertRaises(ValueError):
            self.run_analysis()
        self.assertEqual((self.root / "out/preserve").read_text(), "untouched")
        self.factory.assert_not_called()

    def test_setup_failure_does_not_create_output(self):
        self.write("valid.wav", np.full(16000, 0.03))
        self.factory.side_effect = ValueError("missing model")
        with self.assertRaisesRegex(ValueError, "missing model"):
            self.run_analysis()
        self.assertFalse((self.root / "out").exists())
