"""Guard acceptance against false audio passes; no network/native dependencies."""
import tempfile
import unittest
import wave
from pathlib import Path
import numpy as np
from tests.sip.interop import marker, audio_metrics


class InteropMetricsTests(unittest.TestCase):
    def test_shifted_gain_and_small_clock_adjustment_match(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);x=marker(p/'ref.wav')
            y=np.r_[np.zeros(2000),x[:6000]*.6,x[6080:]*.6,np.zeros(2000)].astype('<i2')
            with wave.open(str(p/'rx.wav'),'wb') as w:
                w.setparams((1,2,8000,0,'NONE',''));w.writeframes(y.tobytes())
            self.assertTrue(audio_metrics(p/'rx.wav',p/'ref.wav')['matched'])

    def test_silence_wrong_signal_and_truncated_audio_do_not_match(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);marker(p/'ref.wav');marker(p/'wrong.wav',1)
            self.assertFalse(audio_metrics(p/'wrong.wav',p/'ref.wav')['matched'])
            for name,x in [('silence',np.zeros(16000)),('short',np.ones(400))]:
                with wave.open(str(p/(name+'.wav')),'wb') as w:
                    w.setparams((1,2,8000,0,'NONE',''));w.writeframes(x.astype('<i2').tobytes())
                self.assertFalse(audio_metrics(p/(name+'.wav'),p/'ref.wav')['matched'])
