"""Explicit local model integration; never download weights from tests."""
import os
from pathlib import Path
import unittest

import numpy as np

from voice_tools.audio.io import Audio,read_wav
from voice_tools.tools.recording_qa.speech_model import SpeechModel


@unittest.skipUnless(os.environ.get('VOICE_TOOLS_TEST_QA_MODEL_DIR'),'set VOICE_TOOLS_TEST_QA_MODEL_DIR for real CPU model tests')
class RealModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model=SpeechModel(os.environ['VOICE_TOOLS_TEST_QA_MODEL_DIR'])

    def test_silence_and_state_isolation_at_supported_rates(self):
        for rate in (8000,16000,22050,48000):
            with self.subTest(rate=rate):
                audio=Audio(np.zeros((rate,2),dtype=np.float32),rate)
                result=self.model.predict(audio)
                self.assertEqual(result['speech'],[[],[]])
                self.assertEqual(result,self.model.predict(audio))
                self.assertEqual(result['coverage_s'],1)

    @unittest.skipUnless(os.environ.get('VOICE_TOOLS_TEST_QA_AUDIO'),'set VOICE_TOOLS_TEST_QA_AUDIO to verify a local speech recording')
    def test_explicit_local_speech_has_bounded_model_evidence(self):
        audio=read_wav(Path(os.environ['VOICE_TOOLS_TEST_QA_AUDIO']))
        result=self.model.predict(audio)
        self.assertTrue(any(result['speech']))
        self.assertEqual(len(result['speech']),audio.samples.shape[1])
        for spans in result['speech']:
            for start,end in spans:self.assertTrue(0<=start<end<=audio.duration_s+1e-5)
