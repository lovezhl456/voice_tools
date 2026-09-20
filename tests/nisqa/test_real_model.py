"""Opt-in actual checkpoint test, never downloads or distributes audio/weights."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get("VOICE_TOOLS_NISQA_MODEL_DIR") and os.environ.get("VOICE_TOOLS_NISQA_AUDIO"),
                     "set model directory and speech WAV explicitly for real-model validation")
class RealModelTests(unittest.TestCase):
    def test_offline_real_model_matches_torchmetrics_function(self):
        import numpy as np
        import soundfile as sf
        import torch
        from torchmetrics.functional.audio import nisqa
        from voice_tools.tools.nisqa.backend import Scorer, SCORE_NAMES
        from voice_tools.tools.nisqa.service import analyze

        directory = os.environ["VOICE_TOOLS_NISQA_MODEL_DIR"]
        path = Path(os.environ["VOICE_TOOLS_NISQA_AUDIO"])
        samples, rate = sf.read(path, dtype="float32", always_2d=True)
        samples = samples[:rate * 10, 0].copy()
        self.assertGreaterEqual(len(samples), rate)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")), \
             patch.object(nisqa, "NISQA_DIR", directory), \
             patch.object(nisqa, "_download_weights", side_effect=AssertionError("network forbidden")):
            scorer = Scorer(directory, threads=2)
            actual = scorer(samples, rate)
            nisqa._load_nisqa_model.cache_clear()
            try:
                expected = nisqa.non_intrusive_speech_quality_assessment(torch.from_numpy(samples), rate).tolist()
            finally:
                nisqa._load_nisqa_model.cache_clear()
            np.testing.assert_allclose([actual[k] for k in SCORE_NAMES], expected, rtol=1e-6, atol=1e-6)
            with tempfile.TemporaryDirectory() as output:
                summary, code = analyze([path], output, model_dir=directory)
            self.assertIn(code, (0, 1))
            self.assertGreater(summary["scored"], 0)
            self.assertEqual(summary["errors"], 0)
